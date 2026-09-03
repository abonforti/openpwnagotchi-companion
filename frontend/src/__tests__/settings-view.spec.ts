import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  Capabilities,
  OutgoingMessage,
  OutgoingStats,
} from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  LastError,
  StatsSnapshot,
  UnauthorizedReason,
  WsClient,
} from '../lib/ws'

import { assertRemoteStringIsolated } from './helpers/remoteText'

// Written wholesale from SPEC.md 4.5.2.1 ("Settings presentation and DOM
// hooks", issue #134), the Settings bullet of 4.5.2, 4.5.1's ports-are-
// diagnostics and IPv4-only rules, 4.7 (lib/settings.ts's own contract), and
// 4.5.3/4.5.1.1's remote-string rule as it applies to the remote strings this
// screen renders (pluginVersion, unauthorizedReason, and now lastError -
// SPEC.md 4.3.10, issue #176).
//
// Deliberately not read from views/Settings.svelte. The previous version of
// this file was written after that file had already been opened, guessed at
// hooks SPEC.md did not yet pin, and is replaced wholesale rather than
// amended: nothing in it carries any authority here, including the fixtures,
// which were rebuilt independently below (dashboard.spec.ts's shapes were
// used as the reference for what a conformant stats/capabilities payload
// looks like, not the old settings-view.spec.ts).
//
// The DOM hooks below are exactly SPEC.md 4.5.2.1's list and nothing else:
// data-view="settings"; data-host-id / data-host-active on a row;
// data-host-field ("label" | "address" | "wsPort" | "httpPort" | "token") on
// a row, distinct from data-add-field ("label" | "address" | "token") on the
// add-host form's own inputs, which the form carries rather than
// data-host-field precisely so a test never has to tell the two apart by
// position; data-action ("activate" | "remove" | "edit" | "save" | "cancel"
// | "add" | "reveal", the last unmasking one token field at a time, in a row
// or in the add form); data-field-error="address"; data-field
// ("connectionState" | "pluginVersion" | "unauthorizedReason" | "lastError" |
// "latency", the last two added by SPEC.md 4.3.10 / issue #176), reusing
// 4.5.1.1's data-empty convention on all but unauthorizedReason, which
// SPEC.md says carries none.

// ---------------------------------------------------------------------------
// A fake localStorage: lib/settings.ts is reached through the real view
// import below, and real storage must never be touched by a test.
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

const TOKEN_KEY = 'companion.token'

let fakeStorage: FakeStorage

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.resetModules()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

// ---------------------------------------------------------------------------
// Mounting, the same way dashboard.spec.ts mounts a view: no props, via
// svelte.mount, torn down between tests. lib/settings.ts and lib/stores.ts
// are imported in the same call as the view so the module singletons
// (settings, activeHost, the store writables) never diverge from what the
// mounted view itself sees.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsApi = typeof import('../lib/settings')
type StoresApi = typeof import('../lib/stores')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let teardownStores: (() => void) | null = null

async function mountSettings(): Promise<{
  root: HTMLElement
  settings: SettingsApi
  stores: StoresApi
}> {
  svelte = await import('svelte')
  const settingsApi = await import('../lib/settings')
  const storesApi = await import('../lib/stores')
  const module = (await import('../views/Settings.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  container = document.createElement('div')
  document.body.appendChild(container)
  instance = svelte.mount(module.default, { target: container }) as Record<
    string,
    unknown
  >
  await settle()
  return { root: container, settings: settingsApi, stores: storesApi }
}

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

afterEach(async () => {
  if (instance && svelte) {
    svelte.unmount(instance)
  }
  instance = null
  container?.remove()
  container = null
  svelte = null
  if (teardownStores) {
    teardownStores()
    teardownStores = null
  }
})

function root(): HTMLElement {
  if (!container) throw new Error('Settings is not mounted')
  return container
}

function hostRow(id: string): HTMLElement {
  const found = root().querySelector(`[data-host-id="${id}"]`)
  expect(found, `expected a [data-host-id="${id}"] row`).not.toBeNull()
  return found as HTMLElement
}

function hostRowOrNull(id: string): HTMLElement | null {
  return root().querySelector(`[data-host-id="${id}"]`)
}

function hostField(id: string, name: string): HTMLElement {
  const row = hostRow(id)
  const found = row.querySelector(`[data-host-field="${name}"]`)
  expect(
    found,
    `expected [data-host-field="${name}"] inside host ${id}`,
  ).not.toBeNull()
  return found as HTMLElement
}

function hostAction(id: string, action: string): HTMLElement {
  const row = hostRow(id)
  const found = row.querySelector(`[data-action="${action}"]`)
  expect(
    found,
    `expected [data-action="${action}"] inside host ${id}`,
  ).not.toBeNull()
  return found as HTMLElement
}

/**
 * An input in the always-visible add-host form (SPEC 4.5.2.1: `data-add-field`,
 * over `label`, `address` and `token` -- distinct from a row's `data-host-field`
 * so a test never has to tell the two apart by position, the defect the
 * previous version of this helper worked around).
 */
function addField(name: string): HTMLInputElement {
  const found = root().querySelector(`[data-add-field="${name}"]`)
  expect(
    found,
    `expected [data-add-field="${name}"] in the add-host form`,
  ).not.toBeNull()
  return found as HTMLInputElement
}

function addFormAddressInput(): HTMLInputElement {
  return addField('address')
}

function addButton(): HTMLElement {
  const found = root().querySelector('[data-action="add"]')
  expect(found, 'expected an [data-action="add"] control').not.toBeNull()
  return found as HTMLElement
}

/** The reveal control belonging to the add-host form's token field, not to any row's. */
function addFormReveal(): HTMLElement {
  const candidates = [
    ...root().querySelectorAll('[data-action="reveal"]'),
  ].filter((el) => el.closest('[data-host-id]') === null)
  expect(
    candidates,
    'expected exactly one reveal control outside any row',
  ).toHaveLength(1)
  return candidates[0] as HTMLElement
}

function click(el: HTMLElement): void {
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

function typeInto(el: HTMLElement, value: string): void {
  const input = el as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

function diagnosticField(name: string): HTMLElement {
  const found = root().querySelector(`[data-field="${name}"]`)
  expect(found, `expected a [data-field="${name}"] diagnostic`).not.toBeNull()
  return found as HTMLElement
}

function diagnosticFieldOrNull(name: string): HTMLElement | null {
  return root().querySelector(`[data-field="${name}"]`)
}

// ---------------------------------------------------------------------------
// A hand-driven WsClient double, the same shape dashboard.spec.ts uses, for
// the diagnostics block.
// ---------------------------------------------------------------------------

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  private lastStatsValue: StatsSnapshot | null = null
  // SPEC 4.3.10: absent, not zero, until something sets it.
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
  // SPEC 4.4.1 (issue #131): not exercised by this file - no test here
  // drives a restarting state, so this always answers null rather than
  // gating on state like stores.spec.ts's own double does.
  restartReason(): null {
    return null
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
  request(): Promise<OutgoingMessage> {
    return new Promise(() => {})
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(new Error('not used in these tests'))
  }
  sendGps(): void {}

  emitState(
    state: ConnectionState,
    reason: UnauthorizedReason | null = null,
  ): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  emitMessage(message: OutgoingMessage): void {
    if (message.type === 'stats')
      this.lastStatsValue = {
        stats: message.data,
        timestamp: message.timestamp,
      }
    for (const handler of [...this.messageHandlers]) handler(message)
  }

  emitDiagnostics(diagnostics: Diagnostics): void {
    this.diagnosticsValue = diagnostics
    for (const handler of [...this.diagnosticsHandlers]) handler(diagnostics)
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

function capabilitiesData(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    pasv: true,
    pisugar: false,
    gpsSource: 'gpsd',
    pluginVersion: '0.1.0',
    ...overrides,
  }
}

function statsEnvelope(capabilities: Capabilities): OutgoingStats {
  return {
    type: 'stats',
    timestamp: 1_700_000_000,
    data: {
      uptime: 1,
      name: null,
      mode: 'AUTO',
      channel: 6,
      battery: { percent: 90, charging: false },
      temperature: 40,
      handshakes: 0,
      handshakesTotal: 0,
      peers: 0,
      accessPoints: 0,
      lastHandshake: null,
      lastPeer: null,
      gps: {
        enabled: false,
        source: null,
        piFix: false,
        fix: false,
        lat: null,
        lon: null,
        altitude: null,
        accuracy: null,
        updated: null,
      },
      sessionAge: 1,
      capabilities,
    },
  }
}

// ---------------------------------------------------------------------------
// data-view, SPEC 4.5.
// ---------------------------------------------------------------------------

describe('the view root', () => {
  it('carries data-view="settings"', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(root().querySelector('[data-view="settings"]')).not.toBeNull()
  })
})

// ---------------------------------------------------------------------------
// "The host list comes first and the add-host form sits under it." (4.5.2.1)
// ---------------------------------------------------------------------------

describe('the host list comes before the add-host form (SPEC 4.5.2.1)', () => {
  it('places the first host row earlier in the document than the add-host form', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const firstRow = hostRow('bluetooth')
    const button = addButton()
    const relative = firstRow.compareDocumentPosition(button)
    expect(
      relative & Node.DOCUMENT_POSITION_FOLLOWING,
      'expected the add-host form (its [data-action="add"] control) to follow the host list in document order',
    ).not.toBe(0)
  })
})

// ---------------------------------------------------------------------------
// "The add-host form is always visible, with no toggle." (SPEC 4.5.2.1)
// ---------------------------------------------------------------------------

describe('the add-host form is always visible, with no toggle (SPEC 4.5.2.1)', () => {
  it('shows the address input and the add control at mount, before any interaction', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const input = addFormAddressInput()
    expect(input.hidden).toBe(false)
    expect(addButton().hasAttribute('hidden')).toBe(false)
  })

  it('stays visible even when the host list is empty, the reachable state SPEC 4.7 names', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    for (const host of get(api.settings).hosts) api.removeHost(host.id)
    await settle()

    expect(get(api.settings).hosts).toHaveLength(0)
    const input = addFormAddressInput()
    expect(input.hidden).toBe(false)
    expect(addButton().hasAttribute('hidden')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Host list: add, edit, remove, activate (SPEC 4.5.2's Settings bullet).
// ---------------------------------------------------------------------------

describe('the host list: add, edit, remove, activate (SPEC 4.5.2)', () => {
  it('shows the two prefilled hosts on a fresh install, Bluetooth active, via data-host-active', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(hostField('bluetooth', 'address').textContent).toContain(
      '172.20.10.2',
    )
    expect(hostField('usb', 'address').textContent).toContain('10.0.0.2')
    expect(hostRow('bluetooth').getAttribute('data-host-active')).toBe('true')
    expect(hostRow('usb').getAttribute('data-host-active')).not.toBe('true')
  })

  it('activates a host from the list, through [data-action="activate"], without touching localStorage by hand', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    click(hostAction('usb', 'activate'))
    await settle()

    expect(get(api.activeHost)?.id).toBe('usb')
    expect(hostRow('usb').getAttribute('data-host-active')).toBe('true')
    expect(hostRow('bluetooth').getAttribute('data-host-active')).not.toBe(
      'true',
    )
  })

  it('removes a host from the list, through [data-action="remove"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    api.addHost({
      label: 'Extra unit',
      address: '172.20.10.5',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    await settle()

    const extra = get(api.settings).hosts.find((h) => h.label === 'Extra unit')
    expect(extra).toBeDefined()
    click(hostAction(extra!.id, 'remove'))
    await settle()

    expect(get(api.settings).hosts.some((h) => h.id === extra!.id)).toBe(false)
    expect(hostRowOrNull(extra!.id)).toBeNull()
  })

  it('adds a host through the form, with a valid IPv4 address, through [data-action="add"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    typeInto(addFormAddressInput(), '172.20.10.40')
    click(addButton())
    await settle()

    const added = get(api.settings).hosts.find(
      (h) => h.address === '172.20.10.40',
    )
    expect(
      added,
      'the new host must appear in the settings store',
    ).toBeDefined()
    expect(hostRowOrNull(added!.id)).not.toBeNull()
  })

  it('edits a host through [data-action="edit"] and [data-action="save"], and persists the change', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    click(hostAction('bluetooth', 'edit'))
    await settle()

    const labelField = hostField('bluetooth', 'label')
    expect(
      labelField.tagName,
      'expected the label field to become an input while editing',
    ).toBe('INPUT')
    typeInto(labelField, 'My Bluetooth Unit')
    click(hostAction('bluetooth', 'save'))
    await settle()

    expect(
      get(api.settings).hosts.find((h) => h.id === 'bluetooth')?.label,
    ).toBe('My Bluetooth Unit')
  })

  it('discards an in-progress edit through [data-action="cancel"]', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const before = get(api.settings).hosts.find(
      (h) => h.id === 'bluetooth',
    )?.label
    click(hostAction('bluetooth', 'edit'))
    await settle()
    typeInto(hostField('bluetooth', 'label'), 'Discarded label')
    click(hostAction('bluetooth', 'cancel'))
    await settle()

    expect(
      get(api.settings).hosts.find((h) => h.id === 'bluetooth')?.label,
    ).toBe(before)
  })

  it('ports are shown but never editable (SPEC 4.5.1: "ports are diagnostics, not settings")', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    for (const [port, expected] of [
      ['wsPort', String(api.DEFAULT_WS_PORT)],
      ['httpPort', String(api.DEFAULT_HTTP_PORT)],
    ] as const) {
      const el = hostField('bluetooth', port)
      if (el.tagName === 'INPUT') {
        const input = el as HTMLInputElement
        expect(
          input.disabled || input.readOnly,
          `${port} input must be disabled or readOnly`,
        ).toBe(true)
      }
      expect(el.textContent ?? (el as HTMLInputElement).value).toContain(
        expected,
      )
    }

    // Also true while a row is in its "edit" state: ports never become
    // editable there either, since editing them means the plugin restarts
    // and drops the socket carrying the confirmation (SPEC 4.5.1). Same
    // shape as the block above and for the same reason: the `if` guard
    // alone only fires on a mutant that turns the span into an input, so
    // there must also be an unconditional assertion that runs against the
    // shipped (non-input) markup, or this loop pins nothing about what
    // actually renders while editing.
    click(hostAction('bluetooth', 'edit'))
    await settle()
    for (const [port, expected] of [
      ['wsPort', String(api.DEFAULT_WS_PORT)],
      ['httpPort', String(api.DEFAULT_HTTP_PORT)],
    ] as const) {
      const el = hostField('bluetooth', port)
      if (el.tagName === 'INPUT') {
        const input = el as HTMLInputElement
        expect(
          input.disabled || input.readOnly,
          `${port} input must stay disabled or readOnly while editing`,
        ).toBe(true)
      }
      expect(
        el.textContent ?? (el as HTMLInputElement).value,
        `${port} must still show the negotiated value while editing`,
      ).toContain(expected)
    }
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1: "Any constant the library owns is imported, never restated
// ... a change to the library's defaults would have left the screen
// quietly creating hosts on the old ports." Driven from lib/settings.ts's
// own exported DEFAULT_WS_PORT/DEFAULT_HTTP_PORT rather than from 8082 and
// 8443 written out again here, so this test cannot become the third copy
// of the number it exists to guard.
// ---------------------------------------------------------------------------

describe('the view carries no port literals of its own (SPEC 4.5.2.1)', () => {
  it("a host added through the form comes out at the library's exported port defaults", async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    typeInto(addFormAddressInput(), '172.20.10.12')
    click(addButton())
    await settle()

    const added = get(api.settings).hosts.find(
      (h) => h.address === '172.20.10.12',
    )
    expect(
      added,
      'the new host must appear in the settings store',
    ).toBeDefined()
    expect(added?.wsPort).toBe(api.DEFAULT_WS_PORT)
    expect(added?.httpPort).toBe(api.DEFAULT_HTTP_PORT)
  })
})

// ---------------------------------------------------------------------------
// "A token is edited in the row, beside the host it belongs to." (4.5.2.1)
// No edit mode gates it: unlike label/address, it is directly editable.
// ---------------------------------------------------------------------------

describe('a token is edited directly in the row, with no edit mode gating it (SPEC 4.5.2.1)', () => {
  it('writes to the per-host record, and to companion.token when that host is active, without clicking edit first', async () => {
    const { settings: api } = await mountSettings()
    const result = api.loadSettings(() => 'not-an-ip-address')
    api.activateHost(result.hosts[0]!.id)
    await settle()

    const activeId = get(api.activeHost)?.id
    expect(activeId).toBeDefined()

    // No [data-action="edit"] click: the token field must already be a
    // usable input at mount, per 4.5.2.1's "no grammar to check ... putting
    // it behind an edit mode separates it from the one fact that gives it
    // meaning".
    const tokenInput = hostField(activeId!, 'token')
    expect(
      tokenInput.tagName,
      'the token field must be directly editable',
    ).toBe('INPUT')
    typeInto(tokenInput, 'a-fresh-token')
    tokenInput.dispatchEvent(new Event('change', { bubbles: true }))
    tokenInput.dispatchEvent(new FocusEvent('blur', { bubbles: true }))
    await settle()

    const record = get(api.settings).hosts.find((h) => h.id === activeId)
    expect(record?.token, 'the per-host record is the source of truth').toBe(
      'a-fresh-token',
    )
    expect(
      fakeStorage.getItem(TOKEN_KEY),
      'companion.token is the copy the client reads',
    ).toBe('a-fresh-token')
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1: "It is masked, with a control to reveal it ... The add-host
// form's token is masked on the same terms ... One field is revealed at a
// time, across the whole screen. Revealing a token re-masks whatever was
// showing before it." Discovered through the DOM this file is written
// against (an <input type="password"> toggled to type="text">), not by
// reading views/Settings.svelte: the masking mechanism itself is not
// pinned by SPEC.md, only its observable effect, so this asserts the input
// is not readable as plain text by default and becomes so only once
// [data-action="reveal"] is used, without assuming *how*.
// ---------------------------------------------------------------------------

function isMasked(input: HTMLInputElement): boolean {
  // The one thing SPEC.md pins is the observable effect: not readable from
  // the DOM as plain text until revealed. type="password" is how a browser
  // (and jsdom) keeps a field's value out of the rendered text while still
  // letting the input carry it, which is the shape this suite found; a
  // mutant that flips this attribute is exactly what "make the reveal
  // control a no-op" targets.
  return input.type === 'password'
}

// A password input's current value is never part of an element's
// serialized markup, in any browser or in jsdom: the "value" IDL property
// (what the input actually holds and what `reveal` toggles the rendering
// of) is distinct from the "value" content attribute (what `innerHTML`
// reads back), and setting the former does not touch the latter -- this is
// standard DOM behaviour and true for a type="text" input exactly as much
// as for type="password". Verified empirically against the mounted view
// rather than assumed. So `[data-action="reveal"]` toggling `type` alone
// already guarantees the token is absent from `container.innerHTML` in
// both states; what this checks is the regression the type toggle alone
// cannot catch, named explicitly by the review: a `title`, `aria-label`,
// `placeholder` or stray text node carrying the token in parallel with a
// correctly-toggling `type`, which would leave every test above green
// while putting the secret back on screen by a different road.
function tokenLeaksIntoMarkup(token: string): boolean {
  return root().innerHTML.includes(token)
}

describe('the token is masked, with a reveal control (SPEC 4.5.2.1)', () => {
  it('is masked in a row until [data-action="reveal"] is used there, then reads as plain text', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    const fixtureToken = 'row-reveal-fixture-value'
    api.updateHost('bluetooth', { token: fixtureToken })
    await settle()

    const tokenInput = hostField('bluetooth', 'token') as HTMLInputElement
    expect(isMasked(tokenInput), 'the token must be masked by default').toBe(
      true,
    )
    expect(
      tokenLeaksIntoMarkup(fixtureToken),
      'the token must not appear in the rendered markup while masked',
    ).toBe(false)
    // The value is still there, carried in the property the browser reads
    // to render the field, just not exposed as text or as a stray
    // attribute -- masking hides it visually, it does not discard it.
    expect(tokenInput.value).toBe(fixtureToken)

    click(hostAction('bluetooth', 'reveal'))
    await settle()

    const revealedInput = hostField('bluetooth', 'token') as HTMLInputElement
    expect(isMasked(revealedInput), 'revealing must unmask it').toBe(false)
    expect(
      revealedInput.value,
      'revealing must not have altered or discarded the value',
    ).toBe(fixtureToken)
    // Still true after revealing: the field being type="text" now is what
    // makes the value visible to a person looking at the screen, but that
    // visibility is the browser rendering the input widget, not the value
    // becoming part of the DOM's serialized markup -- so this assertion,
    // like the masked one above, is the guard against a *different* leak
    // (title/aria-label/placeholder/text node) than the one `isMasked`
    // already covers, not a claim that revealing changes what
    // `innerHTML` contains.
    expect(
      tokenLeaksIntoMarkup(fixtureToken),
      'revealing must not spill the token into markup either',
    ).toBe(false)
  })

  it('is masked in the add-host form on the same terms, until its own reveal control is used', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const fixtureToken = 'add-form-reveal-fixture-value'
    const addToken = addField('token')
    typeInto(addToken, fixtureToken)
    await settle()

    expect(
      isMasked(addField('token')),
      'the add-host form token must be masked by default too',
    ).toBe(true)
    expect(
      tokenLeaksIntoMarkup(fixtureToken),
      'a token typed into the add form must not leak into markup while masked',
    ).toBe(false)

    click(addFormReveal())
    await settle()

    const revealed = addField('token')
    expect(
      isMasked(revealed),
      'revealing the add-host form field must unmask it',
    ).toBe(false)
    expect(revealed.value).toBe(fixtureToken)
    expect(
      tokenLeaksIntoMarkup(fixtureToken),
      'revealing must not spill the token into markup either',
    ).toBe(false)
  })

  // "One field is revealed at a time, across the whole screen. Revealing a
  // token re-masks whatever was showing before it." Both orders are worth
  // an assertion: a shared flag cleared in only one direction would still
  // pass a test that only reveals row-then-form.
  it('revealing a second field re-masks whichever was showing, in both orders', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    // Row, then the add form: the row must re-mask.
    click(hostAction('bluetooth', 'reveal'))
    await settle()
    expect(isMasked(hostField('bluetooth', 'token') as HTMLInputElement)).toBe(
      false,
    )

    click(addFormReveal())
    await settle()
    expect(
      isMasked(addField('token')),
      'the add-host form field must now be revealed',
    ).toBe(false)
    expect(
      isMasked(hostField('bluetooth', 'token') as HTMLInputElement),
      "revealing the add-host form's token must re-mask the row's",
    ).toBe(true)

    // The add form, then a row: the add form must re-mask.
    click(hostAction('usb', 'reveal'))
    await settle()
    expect(
      isMasked(hostField('usb', 'token') as HTMLInputElement),
      'the usb row must now be revealed',
    ).toBe(false)
    expect(
      isMasked(addField('token')),
      "revealing a row's token must re-mask the add-host form's",
    ).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7 / 4.5.2.1: "An address is validated in the form, with the message
// next to the field and role="alert" on it, and the mutator's throw is
// caught as the backstop it is meant to be."
// ---------------------------------------------------------------------------

describe('a bad address typed into the add-host form (SPEC 4.5.2.1, 4.7)', () => {
  it('is refused, with role="alert" on the message beside the field, and never reaches the user as a thrown error', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()
    const before = get(api.settings).hosts.length

    typeInto(addFormAddressInput(), 'not-an-ip-address')

    expect(() => click(addButton())).not.toThrow()
    await settle()

    expect(
      get(api.settings).hosts.length,
      'no host is added for a refused address',
    ).toBe(before)
    const error = root().querySelector('[data-field-error="address"]')
    expect(
      error,
      'expected an error message next to the address field',
    ).not.toBeNull()
    expect((error?.textContent ?? '').length).toBeGreaterThan(0)
    expect(error?.getAttribute('role')).toBe('alert')
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.2.1 (amended): "The address grammar has one expression, and the
// form asks for it ... A test cannot reach a backstop that nothing can
// trip, so what it pins instead is that the form and the mutator refuse the
// same set - driven from one table, so a value added to it is asserted on
// both sides at once." lib/settings.ts now exports isUsableAddress for
// exactly this. Includes the two named-but-well-formed refusals SPEC 4.7
// names by hand (the wildcard and the limited broadcast address) and the
// leading-zero spellings SPEC 4.7 explains at length: an octal-looking
// octet new URL would silently reinterpret, and one whose leading zero
// makes new URL throw instead.
// ---------------------------------------------------------------------------

const ADDRESS_CASES: Array<[label: string, address: string, usable: boolean]> =
  [
    ['an ordinary IPv4 literal', '172.20.10.9', true],
    ['the highest legal octets', '255.255.255.254', true],
    ['the wildcard address, refused by name (SPEC 4.7)', '0.0.0.0', false],
    [
      'the limited broadcast address, refused by name (SPEC 4.7)',
      '255.255.255.255',
      false,
    ],
    [
      'an octal-looking octet new URL would silently reinterpret (SPEC 4.7)',
      '010.0.0.2',
      false,
    ],
    [
      'a leading zero on a digit that is not a valid octal one either, where new URL would throw (SPEC 4.7)',
      '08.0.0.2',
      false,
    ],
    ['a malformed literal', 'not-an-ip-address', false],
    ['an out-of-range octet', '256.0.0.1', false],
    ['an IPv6 literal', '[fe80::1]', false],
  ]

describe('the form and the mutator refuse the same set (SPEC 4.5.2.1, 4.7)', () => {
  it.each(ADDRESS_CASES)(
    '%s (%s): isUsableAddress, addHost and the form agree (usable=%s)',
    async (_label, address, usable) => {
      const { settings: api } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      await settle()

      // 1. The predicate itself, exported from lib/settings.ts.
      expect(api.isUsableAddress(address)).toBe(usable)

      // 2. The mutator: addHost throws iff the predicate refuses the address
      // (SPEC 4.7's "a mutator throws on a bad address").
      const beforeMutator = get(api.settings).hosts.length
      const hostArgs = {
        label: 'probe',
        address,
        wsPort: 8082,
        httpPort: 8443,
        token: null,
      }
      if (usable) {
        expect(() => api.addHost(hostArgs)).not.toThrow()
        expect(get(api.settings).hosts.length).toBe(beforeMutator + 1)
      } else {
        expect(() => api.addHost(hostArgs)).toThrow()
        expect(get(api.settings).hosts.length).toBe(beforeMutator)
      }
      // Undo whatever the mutator did, so the form assertion below starts
      // clean regardless of which branch ran.
      const mutatorAdded = get(api.settings).hosts.find(
        (h) => h.address === address,
      )
      if (mutatorAdded) api.removeHost(mutatorAdded.id)
      await settle()

      // 3. The form: the same address, through the DOM, never a thrown error
      // reaching the user either way (SPEC 4.5.2.1).
      const beforeForm = get(api.settings).hosts.length
      typeInto(addFormAddressInput(), address)
      expect(() => click(addButton())).not.toThrow()
      await settle()

      if (usable) {
        expect(get(api.settings).hosts.some((h) => h.address === address)).toBe(
          true,
        )
        expect(get(api.settings).hosts.length).toBe(beforeForm + 1)
        expect(root().querySelector('[data-field-error="address"]')).toBeNull()
      } else {
        expect(
          get(api.settings).hosts.length,
          `${address} must not be added through the form`,
        ).toBe(beforeForm)
        const error = root().querySelector('[data-field-error="address"]')
        expect(error, `expected an error for ${address}`).not.toBeNull()
        expect(error?.getAttribute('role')).toBe('alert')
      }
    },
  )
})

// ---------------------------------------------------------------------------
// SPEC 4.7: "the note that the USB path is desktop-only ... is rendered by
// the Settings screen and is not part of the default label."
// ---------------------------------------------------------------------------

describe('the USB desktop-only note (SPEC 4.7)', () => {
  it('is rendered on screen, and the USB host\'s persisted label stays exactly "USB"', async () => {
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const usbHost = get(api.settings).hosts.find((h) => h.id === 'usb')
    expect(usbHost?.label).toBe('USB')

    const usbRow = hostRow('usb')
    expect(
      /desktop[- ]only|cannot use|an iphone/i.test(usbRow.textContent ?? ''),
      'expected the desktop-only note somewhere in the USB row',
    ).toBe(true)

    // The persisted label must not be where the warning lives: SPEC 4.7
    // says plainly that a warning in the label is one the owner can delete
    // by renaming the entry.
    expect(hostField('usb', 'label').textContent).not.toMatch(
      /desktop[- ]only|cannot use/i,
    )
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7: "The list is for a client served by the unit it talks to ...
// Settings should say so where the second host is added."
// ---------------------------------------------------------------------------

describe('the CSP note (SPEC 4.7)', () => {
  it('names the Content-Security-Policy restriction somewhere in the screen once a second host exists', async () => {
    // AMBIGUITY, see report: SPEC 4.7 says the note appears "where the
    // second host is added", which could mean conditionally (only once
    // hosts.length > 1) or simply positioned near the add-host form as
    // static copy. The two prefilled hosts already make hosts.length === 2
    // on a fresh install, so this test only asserts the note is present
    // once that ordinary two-host state is reached; it does not assert
    // absence with a single host, since SPEC.md does not pin that either
    // way.
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    expect(get(api.settings).hosts.length).toBeGreaterThan(1)
    expect(
      /content[- ]security[- ]policy|\bcsp\b/i.test(root().textContent ?? ''),
      'expected a note about the Content-Security-Policy restriction somewhere in the screen',
    ).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Diagnostics (SPEC 4.5.2.1): render what exists, name what does not.
// ---------------------------------------------------------------------------

describe('diagnostics (SPEC 4.5.2.1)', () => {
  it('renders connectionState as a real, non-empty value even before any client attaches', async () => {
    // No exact wording is pinned for this diagnostic (unlike the Dashboard
    // banner's table, SPEC 4.5.1.1): the view is free to present the state
    // as friendly copy rather than the raw enum, and the shipped build does
    // ("Offline" rather than "offline"). Assert presence and non-emptiness
    // rather than a specific string.
    const { settings: api } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    await settle()

    const el = diagnosticField('connectionState')
    expect((el.textContent ?? '').trim().length).toBeGreaterThan(0)
    expect(el.getAttribute('data-empty')).not.toBe('true')
  })

  it('dashes pluginVersion, with data-empty, before the first stats frame', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.getAttribute('data-empty')).toBe('true')
  })

  it('renders the negotiated plugin version once a stats frame carries it', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitMessage(
      statsEnvelope(capabilitiesData({ pluginVersion: '1.4.2' })),
    )
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.textContent).toBe('1.4.2')
    expect(el.getAttribute('data-empty')).not.toBe('true')
  })

  it('reflects the live connection state as it changes: the text differs between two distinct states', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitState('connected')
    await settle()
    const connectedText = diagnosticField('connectionState').textContent

    client.emitState('degraded')
    await settle()
    const degradedText = diagnosticField('connectionState').textContent

    expect(degradedText).not.toBe(connectedText)
  })

  // SPEC 4.3.10 / 4.5.2.1: lastError and latency both carry data-empty, "and
  // carry it often: an app that has just started has neither." Each pair
  // below asserts the empty case and the populated one in the same test, so
  // neither reading distinguishes "empty" from "the field does not exist".
  describe('lastError and latency (SPEC 4.3.10, issue #176)', () => {
    // Every call site below overrides at most `code`, `message` or `at`, or
    // swaps `source` between 'frame' and 'close' - both of which carry
    // `code: string`. Typing overrides against that pair (rather than the
    // full `LastError` union) keeps a `source: 'local'` override paired with
    // an arbitrary `code` string from typechecking here: the client can
    // never produce that combination, and a fixture that could still build
    // it would let a test assert behaviour for a case that does not exist.
    // The `source: 'local'` fixtures below are built as literals instead,
    // and already carry one of the two `LocalErrorCode` values.
    type WireLastError = Extract<LastError, { source: 'frame' | 'close' }>

    function sampleLastError(
      overrides: Partial<WireLastError> = {},
    ): LastError {
      return {
        source: 'frame',
        code: 'internal_error',
        message: 'a sample failure',
        at: 1700000000000,
        ...overrides,
      }
    }

    it('renders lastError with data-empty="true" before any error has been reported', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      await settle()

      const el = diagnosticField('lastError')
      expect(el.getAttribute('data-empty')).toBe('true')
    })

    it('renders lastError once the client reports one, without data-empty', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({ message: 'a distinctive sample message' }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      expect(el.textContent ?? '').toContain('a distinctive sample message')
    })

    // "code is matched against known sentences and never printed, which
    // 4.5.2.2 already required of it on the command path." An unrecognised
    // code has no known sentence, so the raw wire value must not leak
    // through as the fallback - a view that reflects it verbatim would pass
    // a "renders something" test while breaking this rule specifically.
    it('never prints the raw error code verbatim, even one the client has no known sentence for', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      const distinctiveUnmappedCode = 'zzz_unmapped_diagnostic_code_xyz'
      client.emitDiagnostics({
        lastError: sampleLastError({
          code: distinctiveUnmappedCode,
          message: 'unit rebooted unexpectedly',
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      expect(root().textContent ?? '').not.toContain(distinctiveUnmappedCode)
    })

    it('renders latency with data-empty="true" before any measurement has been reported', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      await settle()

      const el = diagnosticField('latency')
      expect(el.getAttribute('data-empty')).toBe('true')
    })

    // SPEC 4.3.10: "whole milliseconds with its unit ... `123 ms`", and "not
    // rounded to seconds" - exact text, not a substring, so a mutant that
    // renders "123ms" (no space), "123" (no unit) or "0.1 s" still fails.
    it('renders the latency as exact whole milliseconds with its unit - "123 ms"', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: null,
        latencyMs: 123,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('latency')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      expect((el.textContent ?? '').trim()).toBe('123 ms')
    })

    // A latency of exactly 0ms is a measurement (SPEC 4.3.10), not the
    // absence of one - guards against a falsy check on the store value, and
    // against the pinned rendering collapsing to something that reads as
    // absent (e.g. an empty string) for this one value specifically.
    it('treats a measured latency of 0ms as present, not empty, and renders it as "0 ms"', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: null,
        latencyMs: 0,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('latency')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      expect((el.textContent ?? '').trim()).toBe('0 ms')
    })

    it('renders a hostile lastError message as text, with no element or script created from it (SPEC 4.5.3)', async () => {
      const HOSTILE =
        '<script>window.__pwned_settings_lasterror = true</script>'
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({ message: HOSTILE }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      expect(root().querySelector('script')).toBeNull()
      expect(
        (window as unknown as { __pwned_settings_lasterror?: boolean })
          .__pwned_settings_lasterror,
      ).not.toBe(true)
      delete (window as unknown as { __pwned_settings_lasterror?: boolean })
        .__pwned_settings_lasterror
    })

    // Bidi isolation (SPEC 4.5.3, "A string rendered as text can still lie
    // about its neighbours", issue #219): `message` "came off the wire, so
    // 4.5.3 applies to it in full" (SPEC 4.3.10). The diagnostics line
    // assembles a fixed sentence, the remote message, and a time (SPEC
    // 4.3.10), so the isolation boundary is looked for around the message
    // text specifically, not around the whole assembled line - the sentence
    // and the time are this app's own words and are not remote strings.
    it('a lastError message carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
      const BIDI_OVERRIDE = '‮'
      const HOSTILE_MESSAGE = `unit went ${BIDI_OVERRIDE}gnorw`
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({ message: HOSTILE_MESSAGE }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.textContent ?? '').toContain(HOSTILE_MESSAGE)
      assertRemoteStringIsolated(el, HOSTILE_MESSAGE)
    })

    // The distinguishing assertion: a genuine right-to-left message renders
    // exactly as it arrived - isolation, not stripping (SPEC 4.5.3).
    it('a legitimate right-to-left lastError message renders unmangled, not stripped or reordered', async () => {
      const RTL_MESSAGE = 'الوحدة لم تستجب'
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({ message: RTL_MESSAGE }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.textContent ?? '').toContain(RTL_MESSAGE)
    })

    // SPEC 4.3.10 (amended, issue #176): "A message that is not a string has
    // no message ... Coercing it prints null, undefined or [object Object]
    // on the line, attributed to the unit ... So a non-string is treated as
    // absent - the code's sentence and the time still render, because those
    // are facts this client established itself." This is the composed-line
    // version of the rule format.spec.ts pins at the function level: the
    // View must actually assemble the sentence and the time without the
    // message part, not merely have a formatter that returns the empty
    // string for a non-string input somewhere unused. `message` is cast
    // through `unknown` because the wire carries no runtime validation
    // (issue #109) and TypeScript's own `LastError` type would otherwise
    // refuse to let the test construct this fixture at all.
    it('a non-string message (the audit finding: an array) renders as absent, not coerced to its string form', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))

      client.emitDiagnostics({
        lastError: sampleLastError({
          code: 'internal_error',
          message: ['unexpected', 'array', 'payload'] as unknown as string,
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = diagnosticField('lastError').textContent ?? ''
      expect(text).not.toContain('unexpected,array,payload') // Array.prototype.toString() join
      expect(text).not.toContain('[object')
      expect(text).not.toContain('null')
      expect(text).not.toContain('undefined')
      // "the code's sentence and the time still render, because those are
      // facts this client established itself" - the message is the only
      // part missing, the line is not simply blanked out entirely.
      expect(text.trim().length).toBeGreaterThan(0)
      expect(/\d{1,2}:\d{2}/.test(text)).toBe(true)
    })

    // SPEC 4.3.10: "unauthorized, pasv_requires_auto, pasv_unavailable,
    // log_unavailable and no_frame reuse the sentence they already carry ...
    // every other frame code gets one generic sentence." No literal wording
    // is pinned anywhere reachable without reading the implementation the
    // spec itself is being authored against in parallel, so what is asserted
    // here is the shape of the rule rather than its exact prose: a pinned
    // code's line must differ from the generic line an unmapped code gets
    // (catching "the special case was deleted and it now falls through"),
    // and two different unmapped codes must produce byte-identical text
    // (catching "the generic case quietly starts printing the code again",
    // the mutation SPEC.md names explicitly). `message` is held identical
    // across every comparison so the only thing that can make two lines
    // differ is the sentence, not the message beside it.
    describe('frame code -> sentence mapping (SPEC 4.3.10)', () => {
      it('unauthorized renders a line distinct from the generic line an unmapped code gets', async () => {
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'an_unmapped_diagnostic_code_one',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const generic = diagnosticField('lastError').textContent

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'unauthorized',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const pinned = diagnosticField('lastError').textContent

        expect(pinned).not.toBe(generic)
      })

      it('log_unavailable renders a line distinct from the generic line an unmapped code gets', async () => {
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'an_unmapped_diagnostic_code_two',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const generic = diagnosticField('lastError').textContent

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'log_unavailable',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const pinned = diagnosticField('lastError').textContent

        expect(pinned).not.toBe(generic)
      })

      it('two different codes the client does not recognise render the identical generic line', async () => {
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'totally_unrecognised_code_alpha',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const first = diagnosticField('lastError').textContent

        client.emitDiagnostics({
          lastError: sampleLastError({
            code: 'completely_different_unrecognised_beta',
            message: 'shared payload text',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const second = diagnosticField('lastError').textContent

        expect(second).toBe(first)
      })
    })

    // SPEC 4.3.10: "A close code is shown, and a wire code is not." Two
    // halves of the same rule, both asserted here so an implementation that
    // gets either half backwards (numbers always shown, or never shown)
    // fails on one of the two branches.
    describe('a close code reaches the screen, a frame code never does (SPEC 4.3.10)', () => {
      it('a close (source "close") puts its numeric code on screen, distinguishing 1006 from 1008 from a clean 1000', async () => {
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))

        client.emitDiagnostics({
          lastError: sampleLastError({
            source: 'close',
            code: '1006',
            message: '',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const droppedText = diagnosticField('lastError').textContent ?? ''
        expect(droppedText).toContain('1006')

        client.emitDiagnostics({
          lastError: sampleLastError({
            source: 'close',
            code: '1008',
            message: '',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const refusedText = diagnosticField('lastError').textContent ?? ''
        expect(refusedText).toContain('1008')
        expect(refusedText).not.toBe(droppedText) // three different diagnoses, three different lines

        client.emitDiagnostics({
          lastError: sampleLastError({
            source: 'close',
            code: '1000',
            message: '',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        expect(diagnosticField('lastError').textContent ?? '').toContain('1000')
      })

      it('a frame (source "frame") never puts its code on screen, even one shaped like a close code', async () => {
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))

        // §4.5.2.2's rule already forbade a raw wire code; this is the
        // sibling case where the digits happen to look exactly like one of
        // the close codes asserted above, so a mutant that renders `code`
        // whenever it is numeric-looking (rather than gating on `source`)
        // still fails.
        client.emitDiagnostics({
          lastError: sampleLastError({
            source: 'frame',
            code: '1006',
            message: 'a coincidentally numeric code',
          }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()

        expect(diagnosticField('lastError').textContent ?? '').not.toContain(
          '1006',
        )
      })
    })

    // SPEC 4.3.10 (amended, issue #176): "unauthorized reuses the reason it
    // already carries and not the call to action beside it ... this line is
    // already on the screen it would send them to." format.spec.ts pins the
    // formatter itself; this pins that the View actually composes the line
    // that way rather than gluing the call to action back on somewhere
    // between the formatter and the DOM.
    it('unauthorized carries the reason but not "Fix it in Settings" - that call to action is redundant on this screen', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))

      client.emitDiagnostics({
        lastError: sampleLastError({
          source: 'frame',
          code: 'unauthorized',
          message: '',
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = diagnosticField('lastError').textContent ?? ''
      expect(text).toContain('refused the stored token')
      expect(text).not.toContain('Fix it in Settings')
    })

    // SPEC 4.3.10: "it is one sentence of prose ... it does not read as a
    // sentence with its own full stop glued to a second one." The sibling of
    // the pasv_unavailable case above, now expressible for `unauthorized`
    // too: now that §4.3.10 has settled that `unauthorized` reuses only the
    // single-sentence reason (never the call-to-action clause), its reused
    // sentence is single-sentence exactly like pasv_unavailable's, so the
    // same "exactly one full stop" count applies to it as well.
    it('unauthorized is also one sentence of prose, not two glued together, now that only the reason is reused', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))

      client.emitDiagnostics({
        lastError: sampleLastError({
          source: 'frame',
          code: 'unauthorized',
          message: 'token value did not match',
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = (diagnosticField('lastError').textContent ?? '').trim()
      expect(text).toContain('refused the stored token')
      expect(text).toContain('token value did not match')
      expect(text).not.toContain('Fix it in Settings')
      expect(text).not.toContain('.:')
      expect(text).not.toContain('. :')
      expect(text).not.toContain('..')
      expect(text.match(/\./g)?.length ?? 0).toBe(1)
    })

    // SPEC 4.3.10: "`at` is rendered, as a time of day rather than an age".
    // The testable core of that is that formatting `at` is a pure function
    // of the stamp, not of the clock at the moment of render - the mistake
    // an "age" computation makes. Proven by rendering the same fixed `at`
    // from two completely separate mounts with the system clock ten minutes
    // apart between them: an age would differ, a time of day would not.
    it('at renders as a pure function of the stamp: the same `at` produces the same text however far the clock has since moved (SPEC 4.3.10)', async () => {
      const FIXED_AT = 1_700_000_000_000

      vi.useFakeTimers()
      try {
        vi.setSystemTime(new Date(FIXED_AT))
        const client1 = new FakeWsClient()
        const { settings: api1, stores: stores1 } = await mountSettings()
        api1.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores1.connectStores(asClient(client1))
        client1.emitDiagnostics({
          lastError: sampleLastError({ at: FIXED_AT }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const firstText = diagnosticField('lastError').textContent

        // Tear the first mount down completely before the second, exactly as
        // the file's own afterEach does, so the two mounts are independent.
        if (instance && svelte) svelte.unmount(instance)
        container?.remove()
        if (teardownStores) {
          teardownStores()
          teardownStores = null
        }
        instance = null
        container = null

        vi.setSystemTime(new Date(FIXED_AT + 10 * 60 * 1000)) // ten minutes later, same `at`

        const client2 = new FakeWsClient()
        const { settings: api2, stores: stores2 } = await mountSettings()
        api2.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores2.connectStores(asClient(client2))
        client2.emitDiagnostics({
          lastError: sampleLastError({ at: FIXED_AT }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const laterText = diagnosticField('lastError').textContent

        expect(laterText).toBe(firstText)
      } finally {
        vi.useRealTimers()
      }
    })

    // SPEC 4.5.2.1's field list names five diagnostics, and `at` is folded
    // into the same `data-field="lastError"` text rather than getting a
    // sixth hook of its own.
    it('at has no data-field hook of its own - it lives inside data-field="lastError"', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError(),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      expect(diagnosticFieldOrNull('at')).toBeNull()
    })

    // SPEC 4.3.10: the pinned test that was missing. "at renders as a pure
    // function of the stamp" above compares two renders of the *same*
    // stamp, so an implementation that omits the time from the line
    // entirely still passes it (two identical omissions are still equal).
    // This pins the actual rendered text for one fixed `at`, so a rendering
    // that drops the time - or renders it wrong - fails here even though
    // the pure-function test above cannot see it. The system clock is fixed
    // for the render (not just for the stamp) so the test does not depend
    // on the host's own timezone: SPEC 4.5.1.1's own `formatUnitTime` tests
    // extract an "H:MM" pair rather than pin an exact clock string for
    // exactly this reason, and the same discipline is used here.
    it('renders the time of day for a fixed `at`, not merely something that repeats (SPEC 4.3.10)', async () => {
      const FIXED_AT = new Date(2026, 7, 15, 9, 14, 0).getTime() // local 09:14, a whole minute

      vi.useFakeTimers()
      try {
        vi.setSystemTime(new Date(FIXED_AT)) // "now" is the stamp itself: today, not another day
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))
        client.emitDiagnostics({
          lastError: sampleLastError({ at: FIXED_AT }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()

        const text = diagnosticField('lastError').textContent ?? ''
        const match = /(\d{1,2}):(\d{2})/.exec(text)
        expect(
          match,
          `expected an "H:MM" clock time in ${JSON.stringify(text)}`,
        ).not.toBeNull()
        const [, h, m] = match as RegExpExecArray
        // 09:14 local, allowing a 12h-with-meridiem rendering (hour 9 either way).
        expect(Number(h)).toBe(9)
        expect(m).toBe('14')
      } finally {
        vi.useRealTimers()
      }
    })

    // SPEC 4.3.10 (amended): "A stamp from another day says so ... a bare
    // HH:MM then reads as something that happened today." A last error
    // recorded at 23:59 and read after midnight, with the app never having
    // reconnected in between (so nothing else could have cleared it), must
    // not render as if it happened on the day it is being read.
    it('a stamp from another day renders with a date, not a bare HH:MM that would read as today (SPEC 4.3.10)', async () => {
      const YESTERDAY_2359 = new Date(2026, 7, 14, 23, 59, 0).getTime()
      const TODAY_0001 = new Date(2026, 7, 15, 0, 1, 0).getTime() // just after midnight

      vi.useFakeTimers()
      try {
        vi.setSystemTime(new Date(TODAY_0001))
        const client = new FakeWsClient()
        const { settings: api, stores } = await mountSettings()
        api.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores.connectStores(asClient(client))
        client.emitDiagnostics({
          lastError: sampleLastError({ at: YESTERDAY_2359 }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const crossDayText = diagnosticField('lastError').textContent ?? ''

        if (instance && svelte) svelte.unmount(instance)
        container?.remove()
        if (teardownStores) {
          teardownStores()
          teardownStores = null
        }
        instance = null
        container = null

        // The same 23:59 stamp, but read the same day it was recorded -
        // the control that isolates "the date got added" from "the render
        // just differs for some unrelated reason".
        vi.setSystemTime(new Date(YESTERDAY_2359 + 30_000)) // 30s later, same day
        const client2 = new FakeWsClient()
        const { settings: api2, stores: stores2 } = await mountSettings()
        api2.loadSettings(() => 'not-an-ip-address')
        teardownStores = stores2.connectStores(asClient(client2))
        client2.emitDiagnostics({
          lastError: sampleLastError({ at: YESTERDAY_2359 }),
          latencyMs: null,
          droppedFrames: 0,
        })
        await settle()
        const sameDayText = diagnosticField('lastError').textContent ?? ''

        expect(crossDayText).not.toBe(sameDayText)
      } finally {
        vi.useRealTimers()
      }
    })

    // SPEC 4.3.10 (amended): "it is one sentence of prose ... it does not
    // read as a sentence with its own full stop glued to a second one."
    // The review found `The unit refused the stored token.: token rejected
    // at 14:03` reaching the screen - a sentence's own trailing "." with a
    // second delimiter glued directly onto it. `pasv_unavailable` is used
    // here rather than `unauthorized` because its reused sentence (SPEC's
    // PASV-control table) is a single sentence with no call-to-action
    // clause of its own, so a legitimate render contains exactly one "."
    // in total - unlike `unauthorized`'s reused banner text, which is
    // itself two sentences and would make a period count ambiguous. The
    // message and the rendered time are both chosen with no "." of their
    // own so the sentence's own trailing stop is the only one that may
    // legitimately appear.
    it('is one sentence of prose, not a sentence with a second full stop glued onto it (SPEC 4.3.10)', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({
          source: 'frame',
          code: 'pasv_unavailable',
          message: 'PASV mode requires AUTO',
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = (diagnosticField('lastError').textContent ?? '').trim()
      // The sentence SPEC's own PASV-control table pins for this code, with
      // its trailing full stop taken off by the assembly rule (SPEC 4.3.10:
      // "the sentences arrive with their full stops and the assembly takes
      // them off"), so the substring checked here deliberately omits it -
      // asserting the sentence *with* its stop here would require the join
      // to double it up, which is the defect this whole block exists to
      // catch.
      expect(text).toContain('The unit does not have the PASV plugin')
      expect(text).toContain('PASV mode requires AUTO')
      expect(text).not.toContain('.:')
      expect(text).not.toContain('. :')
      expect(text).not.toContain('..')
      expect(text.match(/\./g)?.length ?? 0).toBe(1)
    })

    // SPEC 4.3.10 (amended): "log_unavailable carries two [sentences] ...
    // Reusing it here means this line carries two as well, which is the
    // price of the rule that one code is worded once." The test above chose
    // pasv_unavailable specifically to keep its period count at exactly one;
    // this is the sibling that pins the two-sentence exception instead of
    // sidestepping it, per the review's instruction not to leave it avoided.
    // Only the reused sentence's own *trailing* stop is taken off by the
    // assembly (the same rule as every other code); the internal stop after
    // "log" belongs to the sentence itself and survives, so a legitimate
    // render carries exactly two "." in total: one internal, one added by
    // the assembly at the very end of the composed line (the same one the
    // pasv_unavailable test above counts as its only period).
    it('log_unavailable renders both of its sentences, not just the first (SPEC 4.3.10)', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: sampleLastError({
          source: 'frame',
          code: 'log_unavailable',
          message: 'no config on disk',
        }),
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = (diagnosticField('lastError').textContent ?? '').trim()
      expect(text).toContain('The unit could not read its log')
      expect(text).toContain(
        'With no agent it has no configuration to find the path in',
      )
      expect(text).toContain('no config on disk')
      expect(text).not.toContain('.:')
      expect(text).not.toContain('. :')
      expect(text).not.toContain('..')
      expect(text.match(/\./g)?.length ?? 0).toBe(2)
    })
  })

  // SPEC 4.3.11 / 4.5.2.1 (issue #109): a measurement rather than a value
  // that can be absent - "it renders, always, including at zero ... it
  // carries no data-empty, because zero is not the absence of a figure, it
  // is the figure." Covered the same way lastError and latency are above:
  // the empty-vs-populated distinction does not apply here, so what is
  // pinned instead is that the hook exists, the value renders, and
  // data-empty is never set on it, at zero or otherwise. No label text is
  // asserted, per the implementer's note that SPEC pins the hook and the
  // no-data-empty rule but not the wording.
  describe('droppedFrames (SPEC 4.3.11, issue #109)', () => {
    it('renders 0 before any traffic at all, with no data-empty attribute', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      await settle()

      const el = diagnosticField('droppedFrames')
      expect(el.getAttribute('data-empty')).toBeNull()
      expect((el.textContent ?? '').trim()).toContain('0')
    })

    it('renders a non-zero count once the client reports one, still with no data-empty attribute', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: null,
        latencyMs: null,
        droppedFrames: 7,
      })
      await settle()

      const el = diagnosticField('droppedFrames')
      expect(el.getAttribute('data-empty')).toBeNull()
      expect((el.textContent ?? '').trim()).toContain('7')
    })
  })

  // SPEC 4.3.10 (resolved, issue #176): "Each of the two gets its own
  // sentence, and the five-code table above does not govern them ... They
  // must not collapse into each other or into the generic sentence."
  // format.spec.ts pins the exact-wording level of this rule (the two
  // sentences differ from each other and from the generic case, and each
  // contains its distinguishing phrase); this file stays at the DOM level
  // it was already at. Before this block, no fixture anywhere in this file
  // built a `source: 'local'` LastError, so an implementation returning the
  // generic frame sentence for both codes, swapping the two, or throwing on
  // the unfamiliar `source` value passed every DOM test in the suite
  // regardless. What is asserted here: both render a real, non-empty line
  // with the time on it and never leak their own raw code.
  describe('source "local" - a drop this client detected itself (SPEC 4.3.10)', () => {
    it('pong_timeout renders a real line with a time on it, and never leaks the raw code', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: {
          source: 'local',
          code: 'pong_timeout',
          message: '',
          at: 1_700_000_000_000,
        },
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      const text = el.textContent ?? ''
      expect(text.trim().length).toBeGreaterThan(0)
      expect(text).not.toContain('pong_timeout')
      expect(/\d{1,2}:\d{2}/.test(text)).toBe(true)
    })

    it('connect_timeout renders a real line with a time on it, and never leaks the raw code', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: {
          source: 'local',
          code: 'connect_timeout',
          message: '',
          at: 1_700_000_000_000,
        },
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      const text = el.textContent ?? ''
      expect(text.trim().length).toBeGreaterThan(0)
      expect(text).not.toContain('connect_timeout')
      expect(/\d{1,2}:\d{2}/.test(text)).toBe(true)
    })

    // SPEC 4.3.11 (issue #109): `bad_frame` is the fourth local code -- a
    // frame arrived and failed the boundary guard. Same DOM-level shape as
    // pong_timeout and connect_timeout above: a real, non-empty line with
    // the time on it, and the raw code never leaks onto the screen.
    it('bad_frame renders a real line with a time on it, and never leaks the raw code', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: {
          source: 'local',
          code: 'bad_frame',
          message: '',
          at: 1_700_000_000_000,
        },
        latencyMs: null,
        droppedFrames: 1,
      })
      await settle()

      const el = diagnosticField('lastError')
      expect(el.getAttribute('data-empty')).not.toBe('true')
      const text = el.textContent ?? ''
      expect(text.trim().length).toBeGreaterThan(0)
      expect(text).not.toContain('bad_frame')
      expect(/\d{1,2}:\d{2}/.test(text)).toBe(true)
    })

    // "A close code is shown, and a wire code is not." source 'local' is
    // neither 'close' nor a numeric code - the sibling of the frame-vs-close
    // digit test above (SPEC 4.3.10, "the rendered sentence comes from the
    // code, as it does for a frame").
    it('neither local code renders any digits on screen - source "local" is not the close case', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitDiagnostics({
        lastError: {
          source: 'local',
          code: 'pong_timeout',
          message: '',
          at: 1_700_000_000_000,
        },
        latencyMs: null,
        droppedFrames: 0,
      })
      await settle()

      const text = diagnosticField('lastError').textContent ?? ''
      expect(/\b1\d{3}\b/.test(text)).toBe(false) // no close-code-shaped digits (1000-1999) anywhere
    })
  })

  describe('unauthorizedReason', () => {
    it('is absent while the state is not unauthorized', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('connected')
      await settle()

      expect(diagnosticFieldOrNull('unauthorizedReason')).toBeNull()
    })

    it('appears only while the state is unauthorized, and disappears again once it changes', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'rejected')
      await settle()

      expect(diagnosticFieldOrNull('unauthorizedReason')).not.toBeNull()

      client.emitState('connected')
      await settle()
      expect(diagnosticFieldOrNull('unauthorizedReason')).toBeNull()
    })

    // "It is also the one field that carries no data-empty: the row exists
    // only when there is a reason, so the two can never both be true, and a
    // branch that cannot be reached is worse than absent."
    it('carries no data-empty attribute at all while it is rendered', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'required')
      await settle()

      const el = diagnosticField('unauthorizedReason')
      expect(el.hasAttribute('data-empty')).toBe(false)
    })

    // "Diagnostics render what exists ... labelling the authentication
    // reason as 'the last error' says nothing happened when a TLS failure
    // or a refused connection is exactly what did." The row must not be
    // presented under that label.
    it('is never labelled "the last error"', async () => {
      const client = new FakeWsClient()
      const { settings: api, stores } = await mountSettings()
      api.loadSettings(() => 'not-an-ip-address')
      teardownStores = stores.connectStores(asClient(client))
      client.emitState('unauthorized', 'rejected')
      await settle()

      const el = diagnosticField('unauthorizedReason')
      const label = `${el.getAttribute('aria-label') ?? ''} ${el.closest('[data-field]')?.previousElementSibling?.textContent ?? ''}`
      expect(/last error/i.test(label)).toBe(false)

      // Not a screen-wide search: the lastError diagnostic legitimately
      // carries that label elsewhere on this screen. Scoped to this field's
      // own row (the wrapper that also holds its label), so an
      // implementation that labels *this* row "Last error" still fails here.
      const row = el.closest('[data-field]')?.parentElement
      expect(
        row,
        'expected the unauthorizedReason field to sit in a labelled row',
      ).not.toBeNull()
      expect(/last error/i.test(row?.textContent ?? '')).toBe(false)
    })
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.5.3, applied to this screen's one remote string: pluginVersion.
// unauthorizedReason is not a remote string (SPEC.md 4.5.2.1) -- lib/ws.ts
// mints it locally from a close code, so §4.5.3 does not apply to it.
// ---------------------------------------------------------------------------

describe('hostile remote strings render as text (SPEC 4.5.3)', () => {
  const HOSTILE_SCRIPT = '<script>window.__pwned_settings = true</script>'

  afterEach(() => {
    delete (window as unknown as { __pwned_settings?: boolean })
      .__pwned_settings
  })

  it('renders a hostile plugin version verbatim, isolated inside a <bdi>, with no other element created', async () => {
    const client = new FakeWsClient()
    const { settings: api, stores } = await mountSettings()
    api.loadSettings(() => 'not-an-ip-address')
    teardownStores = stores.connectStores(asClient(client))
    client.emitMessage(
      statsEnvelope(capabilitiesData({ pluginVersion: HOSTILE_SCRIPT })),
    )
    await settle()

    const el = diagnosticField('pluginVersion')
    expect(el.textContent).toBe(HOSTILE_SCRIPT)
    expect(root().querySelector('script')).toBeNull()
    expect(
      (window as unknown as { __pwned_settings?: boolean }).__pwned_settings,
    ).not.toBe(true)
    assertRemoteStringIsolated(el, HOSTILE_SCRIPT)
  })

  // No equivalent test exists for unauthorizedReason: SPEC.md 4.5.2.1 (the
  // ledger for this screen's own remote strings) is explicit that
  // unauthorizedReason is *not* one -- lib/ws.ts mints it locally from a
  // close code, from a closed set of values this codebase chose, so a
  // hostile value cannot reach this field from the wire. A test that casts
  // a script tag into UnauthorizedReason to exercise this path defends
  // against something that cannot happen, which SPEC.md 4.5.2.1 names as a
  // mistake in its own right ("A defence against something that cannot
  // happen is not free: it reads as evidence that it can").
})
