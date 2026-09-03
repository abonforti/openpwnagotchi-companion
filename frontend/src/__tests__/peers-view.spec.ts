import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import getPeersSchema from '../../../docs/schemas/incoming/get_peers.json'

import type { OutgoingMessage, OutgoingPeersList, Peer } from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  UnauthorizedReason,
  WsClient,
  WsClientOptions,
} from '../lib/ws'
// DASH and EMPTY_LABEL are the pre-existing SPEC 4.5.1.1 surface. formatUnitTime
// is also pre-existing (named, with its return shape, in 4.5.1.1's own function
// table) and is reused here only to pin the wiring of the lastSeen field, the
// same way wifi-view.spec.ts pins Captured's time field against it rather than
// re-deriving the formatting rule locally.
import { DASH, EMPTY_LABEL, formatUnitTime } from '../lib/format'

import { assertRemoteStringIsolated } from './helpers/remoteText'

// Written from SPEC.md 4.5.2.4 ("Peers, and the refresh rule stops being the
// Wi-Fi view's", issue #190), which claims most of its behaviour from 4.5.2.3
// (the Wi-Fi view) rather than restating it: the three refresh occasions, why
// there is no timer, why being connected is not a precondition, and the
// empty-versus-never-fetched rule all live there. Also read for this file:
// 4.5 (routes, views stay mounted), 4.5.1.1 (the dash/data-empty/DOM-hook
// conventions and the function table this view reuses), 4.5.2 (what the
// screen holds), 4.5.3 (remote strings are attacker-chosen - the first screen
// where every text field is one), 4.4.1 (the peers store: list replaces, a
// push upserts on fingerprint and never on the default '???'), 4.4.2 (what
// survives a reconnect / a host switch), 2.4 (peers_list is not part of
// initial_burst(), unlike access_points), 2.9 and 10.7.
//
// Deliberately not read: views/Peers.svelte (a placeholder today), nor
// whatever shared lib/ surface the refresh is being factored into (4.5.2.4
// says it must be shared with Wi-Fi's, not copied), nor the parts of
// lib/format.ts this change might add.
//
// Every element is reached through the DOM hooks 4.5.2.4 declares
// (data-view, data-row, data-row-key, data-field, data-action,
// data-empty-message) and nothing else. Every copy assertion is a literal
// transcribed from 4.5.2.4's own table, never a string imported from
// lib/format.ts. The harness (FakeWsClient, mount-via-startSession,
// settle()/flushSync()+tick()) is wifi-view.spec.ts's, reused wholesale per
// this task's own instruction, since it is exactly what a lib/-driven refresh
// against a client double requires.

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

const T = 1_700_000_000

function peer(overrides: Partial<Peer> = {}): Peer {
  return {
    name: 'unit-alpha',
    fingerprint: 'fingerprint-aaaa1111',
    fullName: 'unit-alpha@fingerprint-aaaa1111',
    rssi: -60,
    channel: 6,
    firstSeen: T,
    prevSeen: T,
    firstMet: T,
    lastSeen: T + 0.5,
    encounters: 3,
    pwndRun: 1,
    pwndTotal: 12,
    version: '1.2.3',
    uptime: 3600,
    face: '(^_^)',
    ...overrides,
  }
}

function peersListEnvelope(entries: Peer[]): OutgoingPeersList {
  return { type: 'peers_list', timestamp: T, data: { entries } }
}

// ---------------------------------------------------------------------------
// A fake localStorage, the same shape every other frontend spec file uses.
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

// ---------------------------------------------------------------------------
// A hand-driven WsClient double, wifi-view.spec.ts's shape: request() calls
// are recorded by type and settled/rejected individually. Only get_peers is
// ever outstanding at once here (one list, not two), but the same addressable
// shape is kept so a host-switch test can settle client A's late reply
// without touching client B's.
// ---------------------------------------------------------------------------

interface PendingRequest {
  type: string
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly requestCalls: string[] = []
  private readonly pending: PendingRequest[] = []

  constructor(readonly options?: WsClientOptions) {}

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
  lastStats(): null {
    return null
  }
  onState(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler)
    return () => this.stateHandlers.delete(handler)
  }
  onMessage(handler: (message: OutgoingMessage) => void): () => void {
    this.messageHandlers.add(handler)
    return () => this.messageHandlers.delete(handler)
  }
  request(type: string, ..._rest: unknown[]): Promise<OutgoingMessage> {
    this.requestCalls.push(type)
    return new Promise((resolve, reject) => {
      this.pending.push({ type, resolve, reject })
    })
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error('the peers view must never send a command, only a read'),
    )
  }
  sendGps(): void {}
  diagnostics(): Diagnostics {
    return { lastError: null, latencyMs: null, droppedFrames: 0 }
  }
  onDiagnostics(): () => void {
    return () => {}
  }

  emitState(
    state: ConnectionState,
    reason: UnauthorizedReason | null = null,
  ): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  emitMessage(message: OutgoingMessage): void {
    for (const handler of [...this.messageHandlers]) handler(message)
  }

  /** Settles the oldest still-pending request() call of the given type. */
  settle(type: string, reply: OutgoingMessage): void {
    const index = this.pending.findIndex((entry) => entry.type === type)
    expect(index, `expected a pending "${type}" request`).not.toBe(-1)
    const entry = this.pending.splice(index, 1)[0] as PendingRequest
    this.emitMessage(reply)
    entry.resolve(reply)
  }

  /** Rejects the oldest still-pending request() call of the given type. */
  rejectPending(type: string, error: Error): void {
    const index = this.pending.findIndex((entry) => entry.type === type)
    expect(index, `expected a pending "${type}" request`).not.toBe(-1)
    const entry = this.pending.splice(index, 1)[0] as PendingRequest
    entry.reject(error)
  }

  countRequestCalls(type: string): number {
    return this.requestCalls.filter((entry) => entry === type).length
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

// ---------------------------------------------------------------------------
// Mounting: Peers.svelte via a real session (startSession), wifi-view.spec.ts's
// pattern exactly. lib/settings.ts, lib/session.ts, lib/ws.ts and the view are
// imported together, after vi.resetModules(), so the module singletons never
// diverge - every runtime value below is taken from the same dynamic import
// mountPeers() itself performs, never from a statically-imported class.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsModule = typeof import('../lib/settings')
type SessionModule = typeof import('../lib/session')
type RouterModule = typeof import('../lib/router')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  // SPEC 4.5.2.4/4.5.2.3: the refresh trigger is the route becoming /peers,
  // which lib/router.ts reads off window.location. jsdom's location survives
  // across tests (unlike the module registry, which vi.resetModules() below
  // gives a fresh copy of), so a stray pushState from a previous test's
  // navigation would otherwise leak into this one.
  window.history.pushState({}, '', '/')
  vi.resetModules()
})

afterEach(async () => {
  if (instance && svelte) svelte.unmount(instance)
  instance = null
  container?.remove()
  container = null
  if (stopSession) {
    stopSession()
    stopSession = null
  }
  svelte = null
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

/**
 * `route`, like wifi-view.spec.ts's mountWifi(), is the refresh trigger
 * itself (SPEC 4.5.2.4 claims 4.5.2.3's "the route becoming the view's own"),
 * not merely a starting condition - views stay mounted across navigation
 * (SPEC 4.5), so a mount-only trigger would fire once per app launch and
 * never again. The default '/peers' pre-navigates before the component ever
 * subscribes. Passing `null` mounts the view while the route is whatever it
 * already was (the default '/' from this file's own beforeEach) and leaves
 * navigating to '/peers' to the test.
 */
async function mountPeers(
  initialState: ConnectionState = 'connected',
  route: string | null = '/peers',
  target: HTMLElement | null = null,
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{
  client: FakeWsClient
  settings: SettingsModule
  session: SessionModule
  router: RouterModule
  target: HTMLElement
}> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  const router = await import('../lib/router')
  settings.loadSettings(() => 'not-an-ip-address')

  let client!: FakeWsClient
  stopSession = session.startSession({
    createClient: (options: WsClientOptions) => {
      client = new FakeWsClient(options)
      client.currentState = initialState
      onClientCreated?.(client)
      return asClient(client)
    },
  })

  if (route !== null) router.navigate(route)

  const module = (await import('../views/Peers.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  const mountTarget = target ?? document.createElement('div')
  if (!target) document.body.appendChild(mountTarget)
  container = mountTarget
  instance = svelte.mount(module.default, { target: mountTarget }) as Record<
    string,
    unknown
  >
  await settle()
  return { client, settings, session, router, target: mountTarget }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true }),
  )
  await settle()
}

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC 4.5.2.4's "The DOM
// hooks", closed list).
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('Peers view is not mounted')
  const viewRoot = container.querySelector('[data-view="peers"]')
  expect(
    viewRoot,
    'expected [data-view="peers"] inside the mount container',
  ).not.toBeNull()
  return viewRoot as HTMLElement
}

function rows(): HTMLElement[] {
  return Array.from(root().querySelectorAll('[data-row="peer"]'))
}

function rowKeys(): string[] {
  return rows().map((row) => row.getAttribute('data-row-key') ?? '')
}

function fieldIn(row: Element, field: string): HTMLElement {
  const found = row.querySelector(`[data-field="${field}"]`)
  expect(found, `expected [data-field="${field}"] inside row`).not.toBeNull()
  return found as HTMLElement
}

function fieldText(row: Element, field: string): string {
  return (fieldIn(row, field).textContent ?? '').trim()
}

// dashboard.spec.ts's own helper, reused via wifi-view.spec.ts's copy: the
// value slot may legitimately carry the accessible EMPTY_LABEL text alongside
// the dash in the same element (SPEC 4.5.1.1), which textContent flattens
// into one string. This strips EMPTY_LABEL back out so a DASH-equality
// assertion is about the visible dash, not about whether a label happens to
// sit beside it in the DOM.
function visibleFieldText(row: Element, field: string): string {
  return fieldText(row, field).split(EMPTY_LABEL).join('').trim()
}

function emptyMessage(): HTMLElement | null {
  return root().querySelector('[data-empty-message]')
}

function refreshControl(): HTMLElement {
  const found = root().querySelector('[data-action="refresh"]')
  expect(found, 'expected [data-action="refresh"]').not.toBeNull()
  return found as HTMLElement
}

// =============================================================================
// 1. The refresh: three occasions and no timer, claimed from SPEC 4.5.2.3 by
//    4.5.2.4 rather than restated, and pinned here against this view's own
//    single get_peers request.
// =============================================================================

describe('the refresh: route becoming /peers, the control, and a host switch in place - no timer', () => {
  it('mounting the view issues exactly one get_peers request', async () => {
    const { client } = await mountPeers()
    expect(client.countRequestCalls('get_peers')).toBe(1)
  })

  it('the request type matches docs/schemas\' own "type" const, not a hand-typed literal that could drift from it', async () => {
    const { client } = await mountPeers()
    const requestType = (
      getPeersSchema as { properties: { type: { const: string } } }
    ).properties.type.const
    expect(client.requestCalls).toContain(requestType)
  })

  it('mounting while the route is not /peers issues nothing; navigating there asks', async () => {
    const { client, router } = await mountPeers('connected', null)
    expect(client.countRequestCalls('get_peers')).toBe(0)
    router.navigate('/peers')
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(1)
  })

  it('leaving /peers does not itself ask for anything', async () => {
    const { client, router } = await mountPeers()
    expect(client.countRequestCalls('get_peers')).toBe(1)
    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(1)
  })

  it('returning to /peers on the same still-mounted view asks again - becoming current is not a once-per-launch event', async () => {
    const { client, router } = await mountPeers()
    expect(client.countRequestCalls('get_peers')).toBe(1)
    client.settle('get_peers', peersListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))
    router.navigate('/log')
    await settle()
    router.navigate('/peers')
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(2)
  })

  it('the refresh control issues another request once the mount-time one has settled', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await click(refreshControl())
    expect(client.countRequestCalls('get_peers')).toBe(2)
  })

  it('tapping refresh while the mount-time request is still in flight coalesces into it, rather than adding a second one', async () => {
    const { client } = await mountPeers()
    expect(client.countRequestCalls('get_peers')).toBe(1)
    await click(refreshControl())
    expect(client.countRequestCalls('get_peers')).toBe(1)
  })

  it('a host switch in place - the view still mounted, the route still /peers - asks the new unit for its own list', async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountPeers(
      'connected',
      '/peers',
      null,
      (c) => created.push(c),
    )
    expect(created).toHaveLength(1)
    expect(clientA.countRequestCalls('get_peers')).toBe(1)

    // clientA's own request is left deliberately unsettled - its reply can
    // never reach a store once A is detached (SPEC 4.4.1's refreshHandshakes
    // reasoning, claimed for this refresh by 4.5.2.3 via 4.5.2.4).
    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.9',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()

    expect(created).toHaveLength(2)
    const clientB = created[1] as FakeWsClient
    expect(clientB.countRequestCalls('get_peers')).toBe(1)

    clientB.settle(
      'get_peers',
      peersListEnvelope([peer({ fingerprint: 'fp-unit-b' })]),
    )
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(rowKeys()).toEqual(['fp-unit-b'])
  })

  it("A's own late-settling reply, after B has already taken over, does not release an owner that is now B's", async () => {
    const first = await mountPeers()
    expect(first.client.countRequestCalls('get_peers')).toBe(1)
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    const second = await mountPeers()
    expect(second.client.countRequestCalls('get_peers')).toBe(1)

    first.client.settle('get_peers', peersListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))

    await click(refreshControl())
    expect(second.client.countRequestCalls('get_peers')).toBe(1)
  })

  it('a client that is no longer attached does not block a new one: the new client sends its own request', async () => {
    const first = await mountPeers()
    expect(first.client.countRequestCalls('get_peers')).toBe(1)
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    const second = await mountPeers()
    expect(second.client.countRequestCalls('get_peers')).toBe(1)
  })

  it('no timer: waiting does not by itself produce another request', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountPeers()
      expect(client.countRequestCalls('get_peers')).toBe(1)
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
      expect(client.countRequestCalls('get_peers')).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('becoming current while offline still asks: being connected is not a precondition (SPEC 4.5.2.3 via 4.5.2.4, and 4.3.3)', async () => {
    const { client, router } = await mountPeers('offline', null)
    router.navigate('/peers')
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(1)
  })

  it('the socket staying down past the queue timeout rejects the read, which the refresh swallows: the panel still reads not-connected', async () => {
    const { client, router } = await mountPeers('offline', null)
    router.navigate('/peers')
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(1)
    client.rejectPending('get_peers', new Error('simulated queue timeout'))
    await settle()
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it("tapping refresh with no active host does nothing: no request, no throw, no new client (SPEC 4.5.2.3's trigger has nothing to call request() on)", async () => {
    const created: FakeWsClient[] = []
    const { client, settings } = await mountPeers(
      'connected',
      '/peers',
      null,
      (c) => created.push(c),
    )
    expect(created).toHaveLength(1)
    client.settle('get_peers', peersListEnvelope([]))
    await settle()
    expect(client.countRequestCalls('get_peers')).toBe(1)

    // §4.7 makes an empty active host a reachable state; removing the
    // active host through the settings seam (session.ts's activeHost
    // subscriber rebuilds against `null` and tears the client down -
    // SPEC 4.4.2) is what settings.spec.ts itself uses to drive it, and it
    // is reachable here without ever touching Peers.svelte or the shared
    // refresh surface. 'bluetooth' is the literal, stable id
    // lib/settings.ts's own defaultHosts() gives the entry
    // loadSettings(() => 'not-an-ip-address') activates in this file's
    // mountPeers().
    settings.removeHost('bluetooth')
    await settle()

    // The tap must be observably inert: no throw escaping the click, no
    // further request against the now-detached client (there is nothing
    // for the trigger to call request() on), and no second client built -
    // a throw or a request against a null client would both be silent in a
    // way this behaviour is not supposed to be.
    await expect(click(refreshControl())).resolves.toBeUndefined()
    expect(client.countRequestCalls('get_peers')).toBe(1)
    expect(created).toHaveLength(1)
  })
})

// =============================================================================
// 2. Empty vs. not-yet-fetched, decided by connection state alone. Peers
//    is not in initial_burst() at all (SPEC 2.4), so a fresh connect leaves
//    the list empty until the trigger above asks - there is no one-frame
//    race window here the way there is for access_points/stats.
// =============================================================================

describe('empty vs. not-yet-fetched, decided by connection state', () => {
  it('empty while connected: "The unit has met no peers."', async () => {
    const { client } = await mountPeers('connected')
    client.settle('get_peers', peersListEnvelope([]))
    await settle()
    const message = emptyMessage()
    expect(message).not.toBeNull()
    expect(message?.textContent).toBe('The unit has met no peers.')
    expect(message?.getAttribute('role')).toBe('status')
  })

  it('empty while degraded (still counted as fetched): the same connected sentence', async () => {
    const { client } = await mountPeers('degraded')
    client.settle('get_peers', peersListEnvelope([]))
    await settle()
    expect(emptyMessage()?.textContent).toBe('The unit has met no peers.')
  })

  it("empty while not connected: the shared not-connected sentence, identical to the Wi-Fi view's own", async () => {
    await mountPeers('connecting')
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('empty while offline: the same not-connected sentence', async () => {
    await mountPeers('offline')
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('the empty message is absent once the list has rows', async () => {
    const { client } = await mountPeers('connected')
    client.settle('get_peers', peersListEnvelope([peer()]))
    await settle()
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 3. The list is not keyed on anything a peer chooses: several peers sharing
//    the documented default '???' fingerprint all render as separate rows.
//    This is the crash issue #187 fixed for AccessPoint.bssid, except the
//    duplicate here is the *default*, produced by any unit that has not
//    advertised an identity, not an edge case requiring a hostile unit.
// =============================================================================

describe("several peers sharing the default '???' fingerprint all render as separate rows (SPEC 4.5.2.4)", () => {
  it('three peers that have not advertised an identity render as three rows, not one', async () => {
    const one = peer({
      fingerprint: '???',
      name: '???',
      fullName: '???@???',
      rssi: -40,
    })
    const two = peer({
      fingerprint: '???',
      name: '???',
      fullName: '???@???',
      rssi: -55,
    })
    const three = peer({
      fingerprint: '???',
      name: '???',
      fullName: '???@???',
      rssi: -70,
    })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([one, two, three]))
    await settle()
    expect(rows()).toHaveLength(3)
  })

  it("data-row-key still carries the fingerprint verbatim ('???' included) - it is rendered as a hook, never used as the #each key", async () => {
    const one = peer({ fingerprint: '???', rssi: -40 })
    const two = peer({ fingerprint: '???', rssi: -55 })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([one, two]))
    await settle()
    expect(rowKeys()).toEqual(['???', '???'])
  })

  it("one peer with a real fingerprint and two sharing '???' are three rows together, not two", async () => {
    const identified = peer({
      fingerprint: 'fp-real-0001',
      name: 'known-unit',
      rssi: -30,
    })
    const anonA = peer({ fingerprint: '???', rssi: -50 })
    const anonB = peer({ fingerprint: '???', rssi: -60 })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([identified, anonA, anonB]))
    await settle()
    expect(rows()).toHaveLength(3)
  })
})

// =============================================================================
// 4. '???' is rendered, never turned into a dash and never marked empty - the
//    dash says the app does not know, '???' says the peer did not say.
// =============================================================================

describe("'???' renders verbatim, never as a dash, and carries no data-empty (SPEC 4.5.2.4)", () => {
  it("name and fingerprint both reading '???' render the literal string, with no data-empty", async () => {
    const { client } = await mountPeers()
    client.settle(
      'get_peers',
      peersListEnvelope([peer({ name: '???', fingerprint: '???' })]),
    )
    await settle()
    const row = rows()[0] as HTMLElement
    const nameField = fieldIn(row, 'name')
    const fingerprintField = fieldIn(row, 'fingerprint')
    expect(nameField.textContent).toBe('???')
    expect(nameField.getAttribute('data-empty')).not.toBe('true')
    expect(fingerprintField.textContent).toBe('???')
    expect(fingerprintField.getAttribute('data-empty')).not.toBe('true')
  })

  it('an advertised name and fingerprint render verbatim too, distinct from the sentinel', async () => {
    const { client } = await mountPeers()
    client.settle(
      'get_peers',
      peersListEnvelope([
        peer({ name: 'known-unit', fingerprint: 'fp-real-0001' }),
      ]),
    )
    await settle()
    const row = rows()[0] as HTMLElement
    expect(fieldIn(row, 'name').textContent).toBe('known-unit')
    expect(fieldIn(row, 'fingerprint').textContent).toBe('fp-real-0001')
  })
})

// SPEC 4.5.1.1, applied to this screen by 4.5.2.4's own hook list (name,
// fullName and fingerprint all carry data-empty): "a remote string that is
// empty is empty." '???' is a value the peer sent and stays verbatim with no
// data-empty (pinned above); '' is nothing the peer said, which is the same
// "not known" the dash exists for everywhere else on this row. Written
// together with the '???' tests above because the two are easy to collapse
// into each other, and collapsing them is exactly the invisible-failure shape
// 4.5.2.4 exists to remove: a blank column, no dash, no data-empty, nothing
// read aloud, for a peer that did say something the plugin dropped in
// transit. Expected red until the fix lands (review, 2026-08-29): the
// implementation currently renders name/fullName/fingerprint raw regardless
// of whether the string is empty.
describe("an empty string ('') dashes and is marked empty, distinct from '???' (SPEC 4.5.1.1 via 4.5.2.4)", () => {
  it('an empty name dashes and is marked empty', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ name: '' })]))
    await settle()
    const row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'name')).toBe(DASH)
    expect(fieldIn(row, 'name').getAttribute('data-empty')).toBe('true')
  })

  it('an empty fullName dashes and is marked empty', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ fullName: '' })]))
    await settle()
    const row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'fullName')).toBe(DASH)
    expect(fieldIn(row, 'fullName').getAttribute('data-empty')).toBe('true')
  })

  it('an empty fingerprint dashes and is marked empty', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ fingerprint: '' })]))
    await settle()
    const row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'fingerprint')).toBe(DASH)
    expect(fieldIn(row, 'fingerprint').getAttribute('data-empty')).toBe('true')
  })

  it("an empty fingerprint is not the '???' default and does not collapse rows the way several '???' peers deliberately do", async () => {
    // Guards against a fix that conflates the two: an empty fingerprint is
    // still a distinct wire value from '???' and from every other empty
    // fingerprint, so two peers each reporting '' are two rows, the same
    // as two peers each reporting '???' are two rows (SPEC 4.5.2.4's own
    // "a key must be positional" rule is about the render key, not about
    // treating '' as another sentinel).
    const first = peer({
      fingerprint: '',
      name: 'unit-with-blank-fingerprint-a',
      rssi: -40,
    })
    const second = peer({
      fingerprint: '',
      name: 'unit-with-blank-fingerprint-b',
      rssi: -55,
    })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([first, second]))
    await settle()
    expect(rows()).toHaveLength(2)
  })
})

// =============================================================================
// 5. Every string field renders as text with no element created (SPEC 4.5.3):
//    the first screen where every text field - name, fullName, fingerprint,
//    face, version - was chosen by a stranger's unit over pwngrid.
// =============================================================================

const HOSTILE_STRINGS = [
  '<script>window.__peersViewPwned = true</script>',
  '"><img src=x onerror="window.__peersViewPwned = true">',
  '\'"><svg/onload=alert(1)>',
]

describe('every remote string renders as text (SPEC 4.5.3)', () => {
  beforeEach(() => {
    ;(window as unknown as { __peersViewPwned?: boolean }).__peersViewPwned =
      undefined
  })

  const fields: Array<keyof Peer> = [
    'name',
    'fullName',
    'fingerprint',
    'face',
    'version',
  ]

  for (const field of fields) {
    for (const hostile of HOSTILE_STRINGS) {
      // Every one of these five fields is isolated inside a <bdi> (5a below
      // pins it directly for name/fullName/fingerprint/face/version), so
      // "no element created" is no longer literally true - the field's own
      // wrapper is deliberate. assertRemoteStringIsolated is the exact
      // property this test is actually named for: the string's own
      // characters present as text, inside that one wrapper, and no script,
      // img or other element anywhere in the field, on top of it - a check
      // that "the field has no child elements" stopped being able to make
      // once the template started creating one on purpose (SPEC 4.5.3).
      it(`${field} "${hostile}" renders as text, isolated inside a <bdi>, with no other element created`, async () => {
        const { client } = await mountPeers()
        client.settle(
          'get_peers',
          peersListEnvelope([peer({ [field]: hostile } as Partial<Peer>)]),
        )
        await settle()
        const row = rows()[0] as HTMLElement
        const el = fieldIn(row, field)
        expect(el.textContent).toBe(hostile)
        expect(root().querySelector('script')).toBeNull()
        expect(root().querySelector('[onerror]')).toBeNull()
        expect(
          (window as unknown as { __peersViewPwned?: boolean })
            .__peersViewPwned,
        ).toBeUndefined()
        assertRemoteStringIsolated(el, hostile)
      })
    }
  }

  it('a name of exactly "-" is a real reading, distinct from the dash the app draws for an unknown value', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ name: '-' })]))
    await settle()
    const field = fieldIn(rows()[0] as HTMLElement, 'name')
    expect(field.textContent).toBe('-')
    expect(field.getAttribute('data-empty')).not.toBe('true')
  })
})

// =============================================================================
// 5a. Bidi isolation (SPEC 4.5.3, issue #219): `Peer.fullName`, `Peer.face`
//     and `Peer.identity` arrive over pwngrid from another person's unit, so
//     a peer name is exactly the "reorders its own sentence" case SPEC 4.5.3
//     names. See wifi-view.spec.ts's own bidi-isolation block for why the
//     wrapping shape is checked either way rather than one guessed reading.
// =============================================================================

const BIDI_OVERRIDE = '‮'
const HOSTILE_BIDI_NAME = `unit-${BIDI_OVERRIDE}atled.exe`
const RTL_NAME_ARABIC = 'وحدة_اختبار'
const RTL_NAME_HEBREW = 'יחידת_בדיקה'

describe('bidi isolation for remote strings (SPEC 4.5.3, issue #219)', () => {
  // Every text field on a peer row (§4.5.2.4's own DOM-hook list), not only
  // name and fullName - fingerprint, face and version arrive over pwngrid
  // exactly the same way, and each was left untested here until review found
  // the gap: a wrapper deleted from any of the three left this suite green.
  for (const field of [
    'name',
    'fullName',
    'fingerprint',
    'face',
    'version',
  ] as const) {
    it(`${field} carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped`, async () => {
      const { client } = await mountPeers()
      client.settle(
        'get_peers',
        peersListEnvelope([
          peer({ [field]: HOSTILE_BIDI_NAME } as Partial<Peer>),
        ]),
      )
      await settle()
      const el = fieldIn(rows()[0] as HTMLElement, field)
      expect(el.textContent).toBe(HOSTILE_BIDI_NAME)
      expect(el.textContent).toContain(BIDI_OVERRIDE)
      assertRemoteStringIsolated(el, HOSTILE_BIDI_NAME)
    })

    for (const rtlName of [RTL_NAME_ARABIC, RTL_NAME_HEBREW]) {
      it(`a legitimate right-to-left ${field} "${rtlName}" renders unmangled, not stripped or reordered`, async () => {
        const { client } = await mountPeers()
        client.settle(
          'get_peers',
          peersListEnvelope([peer({ [field]: rtlName } as Partial<Peer>)]),
        )
        await settle()
        const el = fieldIn(rows()[0] as HTMLElement, field)
        expect(el.textContent).toBe(rtlName)
      })
    }
  }
})

// =============================================================================
// 6. Nullable fields dash with the accessible label when null, and render
//    verbatim, unmarked, when present: rssi, channel, lastSeen, encounters,
//    pwndRun, pwndTotal (SPEC 4.5.2.4's own list of the six).
// =============================================================================

describe('the six wire-nullable fields dash with data-empty on null, and render verbatim otherwise', () => {
  const nullableFields: Array<{ field: keyof Peer; sample: number }> = [
    { field: 'rssi', sample: -47 },
    { field: 'channel', sample: 11 },
    { field: 'encounters', sample: 5 },
    { field: 'pwndRun', sample: 2 },
    { field: 'pwndTotal', sample: 9 },
  ]

  for (const { field, sample } of nullableFields) {
    it(`${field}: null dashes with data-empty, a known value renders unmarked`, async () => {
      const { client } = await mountPeers()
      client.settle(
        'get_peers',
        peersListEnvelope([peer({ [field]: null } as Partial<Peer>)]),
      )
      await settle()
      let row = rows()[0] as HTMLElement
      expect(visibleFieldText(row, field)).toBe(DASH)
      expect(fieldIn(row, field).getAttribute('data-empty')).toBe('true')

      client.emitMessage(
        peersListEnvelope([peer({ [field]: sample } as Partial<Peer>)]),
      )
      await settle()
      row = rows()[0] as HTMLElement
      expect(fieldIn(row, field).textContent).toContain(String(sample))
      expect(fieldIn(row, field).getAttribute('data-empty')).not.toBe('true')
    })
  }

  it('rssi, channel, encounters, pwndRun and pwndTotal at zero still render their value, not a dash for a falsy number', async () => {
    const { client } = await mountPeers()
    client.settle(
      'get_peers',
      peersListEnvelope([
        peer({ rssi: 0, channel: 0, encounters: 0, pwndRun: 0, pwndTotal: 0 }),
      ]),
    )
    await settle()
    const row = rows()[0] as HTMLElement
    for (const field of [
      'rssi',
      'channel',
      'encounters',
      'pwndRun',
      'pwndTotal',
    ]) {
      expect(fieldIn(row, field).getAttribute('data-empty'), field).not.toBe(
        'true',
      )
    }
  })

  it('lastSeen: null dashes with data-empty, a known epoch renders through formatUnitTime', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ lastSeen: null })]))
    await settle()
    let row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'lastSeen')).toBe(DASH)
    expect(fieldIn(row, 'lastSeen').getAttribute('data-empty')).toBe('true')

    client.emitMessage(peersListEnvelope([peer({ lastSeen: T })]))
    await settle()
    row = rows()[0] as HTMLElement
    expect(fieldIn(row, 'lastSeen').textContent).toBe(formatUnitTime(T))
    expect(fieldIn(row, 'lastSeen').getAttribute('data-empty')).not.toBe('true')
  })
})

// SPEC 4.5.2.4: "face and version dash when null, and that is not inconsistent
// with" name/fingerprint rendering '???' verbatim. pwngrid's own library
// supplies the '???' sentinel for name/identity and nothing at all for face
// or version, so on those two the app cannot tell "the peer did not say" from
// "we could not read it" - the wire gives one value, null, for both - and
// SPEC 4.5.1.1's answer for a value the app does not know is the dash. This is
// therefore the opposite rule from the '???' tests above, on fields that sit
// in the same "identity" grouping, and is worth pinning on its own for
// exactly that reason: the two rules read as contradictory until the wire
// asymmetry behind them is known, and a test asserting only one of them would
// not catch the two being swapped.
describe('face and version dash on null, unlike name/fingerprint - the sentinel pwngrid never gives them', () => {
  it('face: null dashes with data-empty, an advertised face renders verbatim and unmarked', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ face: null })]))
    await settle()
    let row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'face')).toBe(DASH)
    expect(fieldIn(row, 'face').getAttribute('data-empty')).toBe('true')

    client.emitMessage(peersListEnvelope([peer({ face: '(^_^)' })]))
    await settle()
    row = rows()[0] as HTMLElement
    expect(fieldIn(row, 'face').textContent).toBe('(^_^)')
    expect(fieldIn(row, 'face').getAttribute('data-empty')).not.toBe('true')
  })

  it('version: null dashes with data-empty, an advertised version renders verbatim and unmarked', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ version: null })]))
    await settle()
    let row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'version')).toBe(DASH)
    expect(fieldIn(row, 'version').getAttribute('data-empty')).toBe('true')

    // The base peer() fixture's own version ('1.2.3', obviously synthetic -
    // SPEC 11 permits a version number here only as a dated observation or to
    // describe what changed between two tags, and a fixture is neither),
    // reused rather than a second literal so the assertion below depends on
    // the fixture constant and not on a string that could be mistaken for a
    // real upstream release.
    const advertisedVersion = peer().version
    client.emitMessage(
      peersListEnvelope([peer({ version: advertisedVersion })]),
    )
    await settle()
    row = rows()[0] as HTMLElement
    expect(fieldIn(row, 'version').textContent).toBe(advertisedVersion)
    expect(fieldIn(row, 'version').getAttribute('data-empty')).not.toBe('true')
  })

  // face and version are nullable, so '' is a third shape on top of null and
  // a real reading - SPEC 4.5.1.1 treats null and '' the same ("a remote
  // string that is empty is empty"), so both must dash and both must carry
  // data-empty, not only the null arm. Expected red until the fix lands
  // (review, 2026-08-29), same as the name/fullName/fingerprint block above.
  it('face: an empty string dashes with data-empty, the same as null', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ face: '' })]))
    await settle()
    const row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'face')).toBe(DASH)
    expect(fieldIn(row, 'face').getAttribute('data-empty')).toBe('true')
  })

  it('version: an empty string dashes with data-empty, the same as null', async () => {
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([peer({ version: '' })]))
    await settle()
    const row = rows()[0] as HTMLElement
    expect(visibleFieldText(row, 'version')).toBe(DASH)
    expect(fieldIn(row, 'version').getAttribute('data-empty')).toBe('true')
  })
})

// =============================================================================
// 7. Order: last seen first, most recent first; signal (strongest first) the
//    tie-break; the fingerprint after that, so the order is total. Nulls
//    placed as SPEC 4.5.2.4 says: a null lastSeen sorts last, and a null rssi
//    sorts after a known one within its lastSeen group.
// =============================================================================

describe('order: most recent lastSeen first, rssi (strongest first) the tie-break, total and stable', () => {
  it('sorts by lastSeen, most recent first', async () => {
    const { client } = await mountPeers()
    const older = peer({ fingerprint: 'fp-older', lastSeen: T - 100 })
    const newest = peer({ fingerprint: 'fp-newest', lastSeen: T + 100 })
    const middle = peer({ fingerprint: 'fp-middle', lastSeen: T })
    client.settle('get_peers', peersListEnvelope([older, newest, middle]))
    await settle()
    expect(rowKeys()).toEqual(['fp-newest', 'fp-middle', 'fp-older'])
  })

  it('a null lastSeen sorts last, after every peer with a known one', async () => {
    const { client } = await mountPeers()
    const unknown = peer({ fingerprint: 'fp-unknown-seen', lastSeen: null })
    const known = peer({
      fingerprint: 'fp-known-seen',
      lastSeen: T - 1_000_000,
    }) // far older, still known
    client.settle('get_peers', peersListEnvelope([unknown, known]))
    await settle()
    expect(rowKeys()).toEqual(['fp-known-seen', 'fp-unknown-seen'])
  })

  // The test above arrives the unknown peer first in the payload
  // (`[unknown, known]`); Array.prototype.sort's comparator is not
  // guaranteed to see the operands in array order, so on its own that
  // fixture does not pin what happens when the comparator is asked to place
  // an unknown lastSeen ahead of a known one instead of behind it - a
  // comparator can answer one of those two questions correctly and the
  // other wrongly, and still be "wrong" only sometimes, in a way that
  // depends on array length and the engine's own sort strategy rather than
  // on anything a fixture with one arrival order would ever show. This
  // reverses the arrival order (known first, unknown second) so the
  // comparator is exercised from the other side, and pins the same final
  // answer: unknown still sorts last regardless of which side of the
  // comparison it lands on.
  it('a null lastSeen still sorts last when it arrives after the known peers in the payload, not only before them', async () => {
    const { client } = await mountPeers()
    const known = peer({
      fingerprint: 'fp-known-seen-2',
      lastSeen: T - 1_000_000,
    })
    const unknown = peer({ fingerprint: 'fp-unknown-seen-2', lastSeen: null })
    client.settle('get_peers', peersListEnvelope([known, unknown]))
    await settle()
    expect(rowKeys()).toEqual(['fp-known-seen-2', 'fp-unknown-seen-2'])
  })

  it('two peers tied on lastSeen: rssi breaks the tie, strongest first', async () => {
    const { client } = await mountPeers()
    const weak = peer({ fingerprint: 'fp-weak', lastSeen: T, rssi: -85 })
    const strong = peer({ fingerprint: 'fp-strong', lastSeen: T, rssi: -30 })
    client.settle('get_peers', peersListEnvelope([weak, strong]))
    await settle()
    expect(rowKeys()).toEqual(['fp-strong', 'fp-weak'])
  })

  it('within a tied lastSeen group, a null rssi sorts after a known one', async () => {
    const { client } = await mountPeers()
    const unknownRssi = peer({
      fingerprint: 'fp-unknown-rssi',
      lastSeen: T,
      rssi: null,
    })
    const knownRssi = peer({
      fingerprint: 'fp-known-rssi',
      lastSeen: T,
      rssi: -90,
    }) // very weak, still known
    client.settle('get_peers', peersListEnvelope([unknownRssi, knownRssi]))
    await settle()
    expect(rowKeys()).toEqual(['fp-known-rssi', 'fp-unknown-rssi'])
  })

  // Same asymmetry as the lastSeen test above, one key down: the fixture
  // above always arrives the null rssi first (`[unknownRssi, knownRssi]`),
  // which does not by itself pin what happens when the comparator meets an
  // unknown rssi from the other side. Reversed arrival order, same tied
  // lastSeen group, same expected final answer.
  it('within a tied lastSeen group, a null rssi still sorts after a known one when it arrives second in the payload, not only first', async () => {
    const { client } = await mountPeers()
    const knownRssi = peer({
      fingerprint: 'fp-known-rssi-2',
      lastSeen: T,
      rssi: -90,
    })
    const unknownRssi = peer({
      fingerprint: 'fp-unknown-rssi-2',
      lastSeen: T,
      rssi: null,
    })
    client.settle('get_peers', peersListEnvelope([knownRssi, unknownRssi]))
    await settle()
    expect(rowKeys()).toEqual(['fp-known-rssi-2', 'fp-unknown-rssi-2'])
  })

  it('a null rssi in one lastSeen group does not leak into a more recent group: lastSeen still leads', async () => {
    const { client } = await mountPeers()
    const olderKnownRssi = peer({
      fingerprint: 'fp-older-known',
      lastSeen: T - 100,
      rssi: -20,
    })
    const newerNullRssi = peer({
      fingerprint: 'fp-newer-null',
      lastSeen: T,
      rssi: null,
    })
    client.settle(
      'get_peers',
      peersListEnvelope([olderKnownRssi, newerNullRssi]),
    )
    await settle()
    expect(rowKeys()).toEqual(['fp-newer-null', 'fp-older-known'])
  })

  it("two peers tied on both lastSeen and rssi break the tie on fingerprint, ascending (SPEC 4.5.2.4, matching 4.5.2.3's BSSID rule)", async () => {
    const lower = peer({ fingerprint: 'fp-tied-aaaa', lastSeen: T, rssi: -50 })
    const higher = peer({ fingerprint: 'fp-tied-bbbb', lastSeen: T, rssi: -50 })
    const { client } = await mountPeers()
    // Arrive in descending fingerprint order, so an unsorted or
    // arrival-order render would read backwards from what is asserted below.
    client.settle('get_peers', peersListEnvelope([higher, lower]))
    await settle()
    expect(rowKeys()).toEqual(['fp-tied-aaaa', 'fp-tied-bbbb'])
  })

  it('a later frame with the same lastSeen/rssi ties keeps a total order: no swap between two peers tied on both, across a permuted arrival', async () => {
    const { client } = await mountPeers()
    const tiedA = peer({ fingerprint: 'fp-tied-aaaa', lastSeen: T, rssi: -50 })
    const tiedB = peer({ fingerprint: 'fp-tied-bbbb', lastSeen: T, rssi: -50 })
    client.settle('get_peers', peersListEnvelope([tiedA, tiedB]))
    await settle()
    const firstOrder = rowKeys()
    expect(new Set(firstOrder)).toEqual(
      new Set([tiedA.fingerprint, tiedB.fingerprint]),
    )

    // SPEC 4.5.2.4 names the fingerprint tie-break as ascending, matching
    // 4.5.2.3's BSSID rule - pinned by the direction test above. This test is
    // the separate claim: the same pair, tied on both keys, arriving in the
    // opposite array order, must not swap relative places between frames.
    // Direction and stability are different claims, and on the Wi-Fi view an
    // earlier fixture made a direction rule pass by coincidence and only a
    // rewrite caught it - so both are kept here rather than one standing in
    // for the other.
    client.emitMessage(peersListEnvelope([tiedB, tiedA]))
    await settle()
    expect(rowKeys()).toEqual(firstOrder)
  })

  it('the same tied pair mounted fresh from the opposite arrival order settles into a consistent order across separate mounts', async () => {
    const tiedA = peer({ fingerprint: 'fp-tied-cccc', lastSeen: T, rssi: -50 })
    const tiedB = peer({ fingerprint: 'fp-tied-dddd', lastSeen: T, rssi: -50 })

    const first = await mountPeers()
    first.client.settle('get_peers', peersListEnvelope([tiedA, tiedB]))
    await settle()
    const orderOne = rowKeys()

    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    const second = await mountPeers()
    second.client.settle('get_peers', peersListEnvelope([tiedB, tiedA]))
    await settle()
    const orderTwo = rowKeys()

    expect(orderOne).toEqual(orderTwo)
  })

  // Two peers unknown on the same key, together: the claim is not merely
  // that both rows exist, it is that neither is treated as greater than the
  // other, so the comparison falls through to the next key exactly as if
  // the two had been equal there - the same "total order" property SPEC
  // 4.5.2.4 asks the fingerprint tie-break for, one key up. Both arrival
  // orders are asserted to the same final answer, since a comparator that
  // breaks this only for one array arrangement is the asymmetry the two
  // reversed-arrival tests above exist to catch.
  it('two peers both reporting a null lastSeen: neither wins the tie, so rssi still decides between them', async () => {
    const strongerNullSeen = peer({
      fingerprint: 'fp-nullseen-zzzz',
      lastSeen: null,
      rssi: -30,
    })
    const weakerNullSeen = peer({
      fingerprint: 'fp-nullseen-aaaa',
      lastSeen: null,
      rssi: -70,
    })
    const { client } = await mountPeers()
    client.settle(
      'get_peers',
      peersListEnvelope([strongerNullSeen, weakerNullSeen]),
    )
    await settle()
    expect(rowKeys()).toEqual(['fp-nullseen-zzzz', 'fp-nullseen-aaaa'])

    client.emitMessage(peersListEnvelope([weakerNullSeen, strongerNullSeen]))
    await settle()
    expect(rowKeys()).toEqual(['fp-nullseen-zzzz', 'fp-nullseen-aaaa'])
  })

  it('two peers both reporting a null lastSeen and a null rssi: the tie falls all the way through to fingerprint, ascending', async () => {
    const lower = peer({
      fingerprint: 'fp-nullboth-aaaa',
      lastSeen: null,
      rssi: null,
    })
    const higher = peer({
      fingerprint: 'fp-nullboth-bbbb',
      lastSeen: null,
      rssi: null,
    })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([higher, lower]))
    await settle()
    expect(rowKeys()).toEqual(['fp-nullboth-aaaa', 'fp-nullboth-bbbb'])

    client.emitMessage(peersListEnvelope([lower, higher]))
    await settle()
    expect(rowKeys()).toEqual(['fp-nullboth-aaaa', 'fp-nullboth-bbbb'])
  })

  it('two peers tied on a known lastSeen, both reporting a null rssi: the tie falls through to fingerprint, ascending', async () => {
    const lower = peer({
      fingerprint: 'fp-tiedseen-nullrssi-aaaa',
      lastSeen: T,
      rssi: null,
    })
    const higher = peer({
      fingerprint: 'fp-tiedseen-nullrssi-bbbb',
      lastSeen: T,
      rssi: null,
    })
    const { client } = await mountPeers()
    client.settle('get_peers', peersListEnvelope([higher, lower]))
    await settle()
    expect(rowKeys()).toEqual([
      'fp-tiedseen-nullrssi-aaaa',
      'fp-tiedseen-nullrssi-bbbb',
    ])

    client.emitMessage(peersListEnvelope([lower, higher]))
    await settle()
    expect(rowKeys()).toEqual([
      'fp-tiedseen-nullrssi-aaaa',
      'fp-tiedseen-nullrssi-bbbb',
    ])
  })
})

// =============================================================================
// 8. The refresh control's copy.
// =============================================================================

describe("control copy, pinned by equality against SPEC 4.5.2.4's table", () => {
  it('the refresh control reads "Refresh"', async () => {
    await mountPeers()
    expect(refreshControl().textContent).toBe('Refresh')
  })
})

// =============================================================================
// 9. data-view="peers" on the view root, as SPEC 4.5 requires of every view.
// =============================================================================

describe('the view root', () => {
  it('carries data-view="peers"', async () => {
    await mountPeers()
    expect(root().getAttribute('data-view')).toBe('peers')
  })
})
