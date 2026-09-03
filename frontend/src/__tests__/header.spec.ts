import { get, writable, type Writable } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  OutgoingMessage,
  OutgoingStats,
  RestartReason,
  Stats,
} from '../lib/protocol'
import type { Host } from '../lib/settings'
import type {
  ConnectionState,
  Diagnostics,
  StatsSnapshot,
  UnauthorizedReason,
  WsClient,
} from '../lib/ws'

// Written from SPEC.md 4.4.1 (the `unitName` row), 4.5.1.2 ("What the
// header holds", issues #30 and #200) and docs/schemas/common.json, and
// the fixed public surface handed to the test author for issue #30/#200.
// Deliberately not read from lib/stores.ts or App.svelte, which are
// authored in parallel.
//
// The precedence SPEC 4.5.1.2 states is: the active host's label (as
// sanitizeLabel already renders it), then stats.name once a frame has
// arrived and it is not null, then the active host's address, which is
// never blank while a host is active. Before any host is active, the
// header shows the app's own name, "companion" -- but that substitution
// is the DOM's job (App.svelte), not the store's: unitName itself is
// `null` with no active host, which the second describe block below pins
// directly against docs/schemas/common.json's `name: string | null`.
//
// Rule 2 (stats.name) and rule 3 (address) can only be observed once rule
// 1 (the label) is out of the way, and lib/settings.ts's own sanitizeLabel
// guarantees a persisted Host.label is *never* empty -- an empty label
// submitted anywhere in the real app (addHost, updateHost, a malformed
// persisted blob) is repaired to the address before unitName ever sees it.
// So the two tests that need an empty label to reach rules 2 and 3 replace
// lib/settings's `activeHost` store with a local double via `vi.doMock`,
// which lets a Host literal with `label: ''` reach unitName the way no
// real, sanctioned path through lib/settings.ts can produce one. This
// assumes lib/stores.ts imports `activeHost` from lib/settings.ts, which
// SPEC 4.4.1's own row names as the derivation's second input; if it is
// wired differently the two tests fail on the mock/import mismatch itself,
// not silently pass.

const T = 1700000000

function gpsReading() {
  return {
    enabled: true,
    source: 'gpsd' as const,
    piFix: true,
    fix: true,
    lat: 10.1,
    lon: -30.2,
    altitude: 5,
    accuracy: 8,
    updated: T,
  }
}

function statsData(overrides: Partial<Stats> = {}): Stats {
  return {
    uptime: 120,
    name: null,
    mode: 'AUTO',
    channel: 6,
    battery: { percent: 80, charging: false },
    temperature: 45.2,
    handshakes: 3,
    handshakesTotal: 4,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: gpsReading(),
    sessionAge: 5,
    capabilities: {
      pasv: true,
      pisugar: false,
      gpsSource: 'gpsd',
      pluginVersion: '0.1.0',
    },
    ...overrides,
  }
}

function statsEnvelope(overrides: Partial<Stats> = {}): OutgoingStats {
  return { type: 'stats', timestamp: T, data: statsData(overrides) }
}

function host(overrides: Partial<Host> = {}): Host {
  return {
    id: 'h1',
    label: 'Unit A',
    address: '172.20.10.9',
    wsPort: 8082,
    httpPort: 8443,
    token: null,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// A minimal double for WsClient, trimmed to what connectStores needs to
// deliver a `stats` frame and report a state. Not read from lib/ws.ts;
// shaped from the WsClient surface stores.spec.ts (issue #111) already
// exercises, since that is the fixed contract connectStores is specified
// against (SPEC 4.4).
// ---------------------------------------------------------------------------

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'offline'
  reasonValue: UnauthorizedReason | null = null
  private restartReasonValue: RestartReason | null = null
  private lastStatsValue: StatsSnapshot | null = null
  private diagnosticsValue: Diagnostics = {
    lastError: null,
    latencyMs: null,
    droppedFrames: 0,
  }
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly diagnosticsHandlers = new Set<(diagnostics: Diagnostics) => void>()

  connect(): void {}
  close(): void {}

  state(): ConnectionState {
    return this.currentState
  }

  unauthorizedReason(): UnauthorizedReason | null {
    return this.reasonValue
  }

  restartReason(): RestartReason | null {
    return this.currentState === 'restarting' ? this.restartReasonValue : null
  }

  lastStats(): StatsSnapshot | null {
    return this.lastStatsValue
  }

  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    return () => this.stateHandlers.delete(handler)
  }

  onMessage(handler: (message: OutgoingMessage) => void): () => void {
    this.messageHandlers.add(handler)
    return () => this.messageHandlers.delete(handler)
  }

  diagnostics(): Diagnostics {
    return this.diagnosticsValue
  }

  onDiagnostics(handler: (diagnostics: Diagnostics) => void): () => void {
    this.diagnosticsHandlers.add(handler)
    return () => this.diagnosticsHandlers.delete(handler)
  }

  request(type: string, data?: unknown): Promise<OutgoingMessage> {
    return new Promise(() => {
      // Never settled: nothing here exercises a reply.
      void type
      void data
    })
  }

  command(): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error('the store layer must never issue a command of its own'),
    )
  }

  sendGps(): void {}

  emitMessage(message: OutgoingMessage): void {
    if (message.type === 'stats') {
      this.lastStatsValue = {
        stats: message.data,
        timestamp: message.timestamp,
      }
    }
    for (const handler of [...this.messageHandlers]) handler(message)
  }
}

// ---------------------------------------------------------------------------
// A fake localStorage, exactly the shape settings.spec.ts and
// navigation.spec.ts already use: real storage is never touched.
// ---------------------------------------------------------------------------

class FakeStorage implements Storage {
  private readonly data = new Map<string, string>()

  get length(): number {
    return this.data.size
  }

  clear(): void {
    this.data.clear()
  }

  getItem(key: string): string | null {
    return this.data.has(key) ? (this.data.get(key) as string) : null
  }

  key(index: number): string | null {
    return Array.from(this.data.keys())[index] ?? null
  }

  removeItem(key: string): void {
    this.data.delete(key)
  }

  setItem(key: string, value: string): void {
    this.data.set(key, String(value))
  }
}

let fakeStorage: FakeStorage

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('../lib/settings')
  vi.unstubAllGlobals()
})

/** Seeds companion.settings with a raw blob, the same shape lib/settings.ts persists, before that module is ever imported this test. */
function seedSettings(blob: unknown): void {
  fakeStorage.setItem('companion.settings', JSON.stringify(blob))
}

describe('unitName: precedence reachable through the real settings module', () => {
  it('the label wins over stats.name and over the address', async () => {
    seedSettings({
      hosts: [host({ label: 'Unit A', address: '172.20.10.9' })],
      activeHostId: 'h1',
    })
    const settingsModule = await import('../lib/settings')
    const storesModule = await import('../lib/stores')
    settingsModule.loadSettings()

    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: 'pwnagotchi-042' }))

    expect(get(storesModule.unitName)).toBe('Unit A')
    teardown()
  })

  it('a label the owner typed keeps winning while a stats frame with a null name is live', async () => {
    // "Unset by the owner" here means the default hosts' own fixed label
    // ('Bluetooth'): this exercises rule 1 still winning over rule 3 while
    // a stats frame is live, as the counterpart to the empty-label tests
    // below that need the mock to reach past rule 1 at all.
    seedSettings({
      hosts: [host({ id: 'h1', label: 'Bluetooth', address: '172.20.10.2' })],
      activeHostId: 'h1',
    })
    const settingsModule = await import('../lib/settings')
    const storesModule = await import('../lib/stores')
    settingsModule.loadSettings()

    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: null }))

    expect(get(storesModule.unitName)).toBe('Bluetooth')
    teardown()
  })

  it('a label equal to the address is the sanitizer fallback, not the owner, and the unit name wins', async () => {
    // sanitizeLabel fills a blank label with the address, so this is what a
    // host the owner never named looks like once stored (SPEC 4.5.1.2 rule 1).
    seedSettings({
      hosts: [host({ id: 'h1', label: '172.20.10.9', address: '172.20.10.9' })],
      activeHostId: 'h1',
    })
    const settingsModule = await import('../lib/settings')
    const storesModule = await import('../lib/stores')
    settingsModule.loadSettings()

    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    expect(get(storesModule.unitName)).toBe('172.20.10.9')
    fake.emitMessage(statsEnvelope({ name: 'pwnagotchi-042' }))

    expect(get(storesModule.unitName)).toBe('pwnagotchi-042')
    fake.emitMessage(statsEnvelope({ name: null }))
    expect(get(storesModule.unitName)).toBe('172.20.10.9')
    teardown()
  })

  it('is null with no active host, before any stats frame and after one', async () => {
    seedSettings({ hosts: [], activeHostId: null })
    const settingsModule = await import('../lib/settings')
    const storesModule = await import('../lib/stores')
    settingsModule.loadSettings()

    expect(get(storesModule.unitName)).toBeNull()
  })

  it('follows a host switch by construction, without a new stats frame', async () => {
    seedSettings({
      hosts: [
        host({ id: 'h1', label: 'Unit A', address: '172.20.10.9' }),
        host({ id: 'h2', label: 'Unit B', address: '172.20.10.10' }),
      ],
      activeHostId: 'h1',
    })
    const settingsModule = await import('../lib/settings')
    const storesModule = await import('../lib/stores')
    settingsModule.loadSettings()

    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: 'pwnagotchi-042' }))
    expect(get(storesModule.unitName)).toBe('Unit A')

    // No further message is emitted: only the active host changes.
    settingsModule.activateHost('h2')
    expect(get(storesModule.unitName)).toBe('Unit B')
    teardown()
  })
})

describe('unitName: rules 2 and 3, only reachable past an empty label', () => {
  // lib/settings.ts's sanitizeLabel guarantees a real, persisted Host.label
  // is never empty (SPEC 4.5.1.2's own citation of it), so these two cases
  // are reached here by replacing lib/settings's `activeHost` export with a
  // local store holding a Host literal built directly in this file, which
  // TypeScript's structural typing does not forbid.
  async function loadStoresWithActiveHost(initial: Host | null) {
    const activeHostStore: Writable<Host | null> = writable(initial)
    vi.doMock('../lib/settings', async () => {
      const actual =
        await vi.importActual<typeof import('../lib/settings')>(
          '../lib/settings',
        )
      return {
        ...actual,
        activeHost: { subscribe: activeHostStore.subscribe },
      }
    })
    const storesModule = await import('../lib/stores')
    return storesModule
  }

  it('an empty label falls through to stats.name', async () => {
    const storesModule = await loadStoresWithActiveHost(
      host({ label: '', address: '172.20.10.5' }),
    )
    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: 'pwnagotchi-099' }))

    expect(get(storesModule.unitName)).toBe('pwnagotchi-099')
    teardown()
  })

  it('a null stats.name falls through to the address, with an empty label', async () => {
    const storesModule = await loadStoresWithActiveHost(
      host({ label: '', address: '172.20.10.6' }),
    )
    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: null }))

    expect(get(storesModule.unitName)).toBe('172.20.10.6')
    teardown()
  })

  it('an empty-string stats.name falls through to the address, with an empty label', async () => {
    // docs/schemas/common.json documents that the plugin "never" sends an
    // empty string for name -- only null or a real hostname -- but the
    // schema's own type is `string | null` with no minLength, so nothing on
    // the wire stops a misbehaving or compromised unit from sending one
    // anyway (SPEC 4.5.3: a remote string is attacker-chosen). This is the
    // frontend's own defence for that case, independent of the plugin's
    // guarantee.
    const storesModule = await loadStoresWithActiveHost(
      host({ label: '', address: '172.20.10.8' }),
    )
    const fake = new FakeWsClient()
    const teardown = storesModule.connectStores(fake)
    fake.emitMessage(statsEnvelope({ name: '' }))

    expect(get(storesModule.unitName)).toBe('172.20.10.8')
    teardown()
  })
})

// ---------------------------------------------------------------------------
// The rendered header, SPEC 4.5.1.2's DOM-hooks paragraph: inside
// [data-region="header"], exactly two text-bearing fields,
// [data-field="unitName"] and [data-field="connectionState"], and no other
// text. App.svelte "starts the real session" (navigation.spec.ts's own
// comment on the same file), so this reuses that file's pattern -- a fresh
// module graph per render, a fake localStorage, a matchMedia double so the
// shell can mount -- rather than driving the header through the stores
// directly, which the two describe blocks above already cover.
// ---------------------------------------------------------------------------

describe('the rendered header (SPEC 4.5.1.2)', () => {
  interface ChangeLike {
    matches: boolean
    media: string
  }

  class FakeMediaQueryList {
    media: string
    matches = false
    onchange: ((event: ChangeLike) => void) | null = null

    constructor(media: string) {
      this.media = media
    }

    addEventListener(): void {}
    removeEventListener(): void {}
    addListener(): void {}
    removeListener(): void {}
  }

  function installMatchMedia(): void {
    window.matchMedia = ((query: string) =>
      new FakeMediaQueryList(query)) as unknown as typeof window.matchMedia
  }

  type SvelteApi = typeof import('svelte')
  let svelte: SvelteApi | null = null
  let container: HTMLElement | null = null
  let instance: Record<string, unknown> | null = null

  async function renderShell(): Promise<HTMLElement> {
    svelte = await import('svelte')
    const module = (await import('../App.svelte')) as unknown as {
      default: Parameters<SvelteApi['mount']>[0]
    }
    container = document.createElement('div')
    document.body.appendChild(container)
    instance = svelte.mount(module.default, { target: container }) as Record<
      string,
      unknown
    >
    svelte.flushSync()
    await svelte.tick()
    return container
  }

  afterEach(() => {
    if (instance && svelte) {
      svelte.unmount(instance)
    }
    instance = null
    svelte = null
    container?.remove()
    container = null
  })

  function headerRegion(root: HTMLElement): HTMLElement {
    const region = root.querySelector('[data-region="header"]')
    expect(
      region,
      'SPEC 4.5.1.2: a [data-region="header"] element',
    ).not.toBeNull()
    return region as HTMLElement
  }

  function textFieldsOf(header: HTMLElement): Record<string, string> {
    const fields = Array.from(header.querySelectorAll('[data-field]'))
    const byName: Record<string, string> = {}
    for (const field of fields) {
      const name = field.getAttribute('data-field') as string
      byName[name] = (field.textContent ?? '').replace(/\s+/g, ' ').trim()
    }
    return byName
  }

  // SPEC 4.5.1.2 "no other text": whatever the header renders outside its
  // data-field elements is a regression, so the header's whole text must be
  // the fields' text and nothing more. Whitespace stripped on both sides,
  // because the markup between the fields is not text.
  function expectNoOtherText(header: HTMLElement): void {
    const fieldsText = Array.from(header.querySelectorAll('[data-field]'))
      .map((field) => (field.textContent ?? '').replace(/\s+/g, ''))
      .join('')
    expect((header.textContent ?? '').replace(/\s+/g, '')).toBe(fieldsText)
  }

  it('shows "companion" and no connection state with no host active', async () => {
    fakeStorage.setItem(
      'companion.settings',
      JSON.stringify({ hosts: [], activeHostId: null }),
    )
    installMatchMedia()
    const root = await renderShell()
    const header = headerRegion(root)

    // SPEC 4.5.1.2: with no host active the state element is absent, not
    // empty. Asserted as absence, so a guard that hid the element instead
    // of omitting it would fail here rather than pass on an empty string.
    const present = Array.from(header.querySelectorAll('[data-field]')).map(
      (field) => field.getAttribute('data-field'),
    )
    expect(present).toEqual(['unitName'])
    expect(
      header.querySelectorAll('[data-field="connectionState"]').length,
    ).toBe(0)

    const fields = textFieldsOf(header)
    expect(fields.unitName).toBe('companion')
    expectNoOtherText(header)
  }, 10_000)

  it('shows the unit name and the formatted connection state with a host active', async () => {
    fakeStorage.setItem(
      'companion.settings',
      JSON.stringify({
        hosts: [host({ id: 'h1', label: 'Unit A', address: '172.20.10.9' })],
        activeHostId: 'h1',
      }),
    )
    installMatchMedia()
    const root = await renderShell()
    const header = headerRegion(root)

    expect(header.querySelectorAll('[data-field]').length).toBe(2)

    // Ground truth for the connection sentence is lib/stores's own
    // `connection` store and lib/format's `formatConnectionState`, read
    // from the same module graph App.svelte was mounted from -- the
    // contract under test is that the header mirrors that mapping, not a
    // specific state a real, never-resolving WebSocket happens to be in
    // inside jsdom.
    const storesModule = await import('../lib/stores')
    const formatModule = await import('../lib/format')
    const expectedState = formatModule.formatConnectionState(
      get(storesModule.connection).state,
    )

    const fields = textFieldsOf(header)
    expect(fields.unitName).toBe('Unit A')
    expect(fields.connectionState).toBe(expectedState)
    expectNoOtherText(header)
  }, 10_000)
})
