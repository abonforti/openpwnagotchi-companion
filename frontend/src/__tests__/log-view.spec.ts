import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import getLogSchema from '../../../docs/schemas/incoming/get_log.json'

import type { OutgoingLogLines, OutgoingMessage } from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  UnauthorizedReason,
  WsClient,
  WsClientOptions,
} from '../lib/ws'

import { assertRemoteStringIsolated } from './helpers/remoteText'

// Written from SPEC.md 4.5.2.5 ("The Log, and the one timer this client is
// allowed", issue #193), which now explicitly adopts 4.5.2.3's refresh
// reasoning (the three occasions, no timer anywhere else, being connected is
// not a precondition) the same way 4.5.2.4 adopted it, rather than leaving it
// to be inferred. Also read for this file: 4.5 (routes, views stay mounted --
// load-bearing for the follow-survives-navigation tests below: only the
// asking stops, not the control's own state), 4.5.1.1 (the DOM-hook and
// copy-lives-nowhere-but-SPEC conventions this view reuses), 4.5.2 (what the
// screen holds: live tail, text filter, auto-scroll/follow), 4.5.3 (remote
// strings are attacker-chosen -- a pwnagotchi logs the SSIDs it just saw),
// 4.4.1 (the `log` store: replaces, never appends), 4.4.2 (resetStores()
// runs on an explicit disconnect and on unauthorized, not on an ordinary
// drop -- why log_unavailable needs the not-connected sentence to outrank
// it rather than clearing on its own), 4.3.5 (its own `log_unavailable` row
// now points at 4.5.2.5, so the two sections agree rather than one being
// silently out of date), 2.6.0 (log_unavailable is also what a unit with no
// agent, i.e. MANU, answers), 2.9 (the wire contract: lines defaults/clamps
// server-side, oldest first, opaque strings) and 10.7.
//
// Deliberately not read: views/Log.svelte, nor whatever lib/ surface the
// follow timer and the client-side filter are built on (a dedicated
// lib/log.ts, an extension of lib/viewRefresh.ts or lib/stores.ts -- this
// file does not care which, and imports none of them). The level filter
// SPEC 4.5.1 asks for is explicitly NOT built (4.5.2.5 says why: a log
// line's format is not a pinned fact, SPEC 11.1) and is NOT tested here --
// that is issue #194, and a test asserting any particular line shape would
// be a guess shipped as a passing test.
//
// Every element is reached through the DOM hooks 4.5.2.5 declares
// (data-view, data-font-size, data-log-lines, data-log-line, data-action,
// data-log-filter, data-empty-message) and nothing else. Every copy
// assertion is a literal transcribed from 4.5.2.5's own tables, never a
// string pulled from lib/format.ts. The harness (FakeWsClient,
// mount-via-startSession, settle()/flushSync()+tick(), the
// dynamic-import-only rule for anything whose identity matters) is
// peers-view.spec.ts's, reused wholesale per this task's own instruction.

// ---------------------------------------------------------------------------
// Schema conformance, without ajv (not a frontend dependency, matching
// dashboard-controls.spec.ts's own precedent): the schema JSON itself,
// imported directly, checked for additionalProperties, required and the
// "type" const.
// ---------------------------------------------------------------------------

interface JsonSchemaLike {
  title: string
  properties: Record<
    string,
    { const?: string; enum?: unknown[]; type?: string }
  >
  required: string[]
}

const GET_LOG_SCHEMA = getLogSchema as JsonSchemaLike

function assertConformsToSchema(
  frame: Record<string, unknown>,
  schema: JsonSchemaLike,
): void {
  const allowedKeys = Object.keys(schema.properties)
  for (const key of Object.keys(frame)) {
    expect(
      allowedKeys,
      `"${key}" is not a property of ${schema.title}`,
    ).toContain(key)
  }
  for (const key of schema.required) {
    expect(
      Object.prototype.hasOwnProperty.call(frame, key),
      `missing required "${key}"`,
    ).toBe(true)
  }
  expect(frame.type).toBe(schema.properties.type?.const)
}

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

const T = 1_700_000_000
const LOG_PATH = '/etc/pwnagotchi/log/pwnagotchi.log'

function logLinesEnvelope(lines: string[]): OutgoingLogLines {
  return { type: 'log_lines', timestamp: T, data: { lines, path: LOG_PATH } }
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
// A hand-driven WsClient double, peers-view.spec.ts's shape. request() calls
// are recorded with their type *and* their data payload -- unlike peers,
// this file has to assert on the payload itself (SPEC 4.5.2.5: "each ask
// carries no `lines` value").
// ---------------------------------------------------------------------------

interface RecordedRequest {
  type: string
  data: unknown
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly requestCalls: RecordedRequest[] = []
  private readonly pending: RecordedRequest[] = []

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
  request(type: string, data?: unknown): Promise<OutgoingMessage> {
    return new Promise((resolve, reject) => {
      const entry = { type, data, resolve, reject }
      this.requestCalls.push(entry)
      this.pending.push(entry)
    })
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error('the log view must never send a command, only a read'),
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
    const entry = this.pending.splice(index, 1)[0] as RecordedRequest
    this.emitMessage(reply)
    entry.resolve(reply)
  }

  /** Rejects the oldest still-pending request() call of the given type. */
  rejectPending(type: string, error: Error): void {
    const index = this.pending.findIndex((entry) => entry.type === type)
    expect(index, `expected a pending "${type}" request`).not.toBe(-1)
    const entry = this.pending.splice(index, 1)[0] as RecordedRequest
    entry.reject(error)
  }

  countRequestCalls(type: string): number {
    return this.requestCalls.filter((entry) => entry.type === type).length
  }

  /** The most recently issued request() call of the given type. */
  lastRequestCall(type: string): RecordedRequest {
    const calls = this.requestCalls.filter((entry) => entry.type === type)
    const last = calls[calls.length - 1]
    expect(last, `expected at least one "${type}" request`).toBeDefined()
    return last as RecordedRequest
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

// ---------------------------------------------------------------------------
// Mounting: Log.svelte via a real session (startSession), peers-view.spec.ts's
// pattern exactly. lib/settings.ts, lib/session.ts, lib/ws.ts, lib/router.ts
// and the view are imported together, after vi.resetModules(), so the module
// singletons never diverge -- every runtime value below (including
// ws.RemoteError, used for the log_unavailable tests) is taken from the same
// dynamic import mountLog() itself performs, never from a statically
// imported symbol.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsModule = typeof import('../lib/settings')
type SessionModule = typeof import('../lib/session')
type RouterModule = typeof import('../lib/router')
type WsModule = typeof import('../lib/ws')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  // SPEC 4.5.2.5 via 4.5.2.3: the refresh trigger is the route becoming
  // /log, which lib/router.ts reads off window.location. jsdom's location
  // survives across tests (unlike the module registry, which
  // vi.resetModules() below gives a fresh copy of), so a stray pushState
  // from a previous test's navigation would otherwise leak into this one.
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
 * `route`, like peers-view.spec.ts's mountPeers(), is the refresh trigger
 * itself, not merely a starting condition -- views stay mounted across
 * navigation (SPEC 4.5), so a mount-only trigger would fire once per app
 * launch and never again. The default '/log' pre-navigates before the
 * component ever subscribes. Passing `null` mounts the view while the route
 * is whatever it already was and leaves navigating to '/log' to the test.
 */
async function mountLog(
  initialState: ConnectionState = 'connected',
  route: string | null = '/log',
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{
  client: FakeWsClient
  settings: SettingsModule
  session: SessionModule
  router: RouterModule
  ws: WsModule
  target: HTMLElement
}> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  const router = await import('../lib/router')
  const ws = await import('../lib/ws')
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

  const module = (await import('../views/Log.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  const mountTarget = document.createElement('div')
  document.body.appendChild(mountTarget)
  container = mountTarget
  instance = svelte.mount(module.default, { target: mountTarget }) as Record<
    string,
    unknown
  >
  await settle()
  return { client, settings, session, router, ws, target: mountTarget }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true }),
  )
  await settle()
}

function typeInto(element: Element, value: string): void {
  const input = element as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC 4.5.2.5's "The DOM
// hooks", closed list).
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('Log view is not mounted')
  const viewRoot = container.querySelector('[data-view="log"]')
  expect(
    viewRoot,
    'expected [data-view="log"] inside the mount container',
  ).not.toBeNull()
  return viewRoot as HTMLElement
}

function linesContainer(): HTMLElement {
  const found = root().querySelector('[data-log-lines]')
  expect(found, 'expected [data-log-lines]').not.toBeNull()
  return found as HTMLElement
}

function lineElements(): HTMLElement[] {
  return Array.from(root().querySelectorAll('[data-log-line]'))
}

function emptyMessage(): HTMLElement | null {
  return root().querySelector('[data-empty-message]')
}

function refreshControl(): HTMLElement {
  const found = root().querySelector('[data-action="refresh"]')
  expect(found, 'expected [data-action="refresh"]').not.toBeNull()
  return found as HTMLElement
}

function followControl(): HTMLElement {
  const found = root().querySelector('[data-action="follow"]')
  expect(found, 'expected [data-action="follow"]').not.toBeNull()
  return found as HTMLElement
}

function fontSizeControl(): HTMLElement {
  const found = root().querySelector('[data-action="font-size"]')
  expect(found, 'expected [data-action="font-size"]').not.toBeNull()
  return found as HTMLElement
}

/** SPEC 4.5.2.5: data-font-size lives on the view root, over small/medium/large. */
function fontSize(): string | null {
  return root().getAttribute('data-font-size')
}

function filterInput(): HTMLElement {
  const found = root().querySelector('[data-log-filter]')
  expect(found, 'expected [data-log-filter]').not.toBeNull()
  return found as HTMLElement
}

// SPEC 4.5.2.5: "carries an accessible name of its own rather than a
// placeholder: a placeholder is not a name". dashboard.spec.ts's own
// accessibleText() covers aria-label/aria-describedby/plain text for a
// read-only value field; a form control's name additionally comes from an
// associated <label>, which that helper has no case for since Dashboard
// never labels an <input>. This is the standard accessible-name resolution
// order for a form control: an explicit aria-label wins, then
// aria-labelledby, then an associated <label> (by `for`/id or by wrapping
// the control), in that order -- and, deliberately, no placeholder fallback,
// since SPEC says plainly that a placeholder is not a name.
function accessibleName(el: HTMLElement): string {
  const ariaLabel = el.getAttribute('aria-label')
  if (ariaLabel) return ariaLabel.trim()

  const labelledBy = el.getAttribute('aria-labelledby')
  if (labelledBy) {
    return labelledBy
      .split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' ')
      .trim()
  }

  const id = el.getAttribute('id')
  if (id) {
    const labelFor = document.querySelector(`label[for="${id}"]`)
    if (labelFor) return (labelFor.textContent ?? '').trim()
  }

  const wrappingLabel = el.closest('label')
  if (wrappingLabel) return (wrappingLabel.textContent ?? '').trim()

  return ''
}

const GET_LOG_TYPE = (
  getLogSchema as { properties: { type: { const: string } } }
).properties.type.const

// =============================================================================
// 1. The refresh: route becoming /log, the refresh control, and a host
//    switch in place, all now explicitly adopted by 4.5.2.5 itself from
//    4.5.2.3/4.5.2.4 rather than left to be inferred -- pinned here against
//    this view's own get_log request, and every ask checked against the
//    wire schema and against 4.5.2.5's "no lines value".
// =============================================================================

describe("the refresh: route becoming /log and the refresh control, per 4.5.2.5's own adoption of 4.5.2.3's rule", () => {
  it('mounting the view issues exactly one get_log request', async () => {
    const { client } = await mountLog()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
  })

  it('the request carries no "lines" value: the plugin\'s own default and clamp decide the window (SPEC 2.9)', async () => {
    const { client } = await mountLog()
    const call = client.lastRequestCall(GET_LOG_TYPE)
    if (call.data !== undefined && call.data !== null) {
      expect(Object.prototype.hasOwnProperty.call(call.data, 'lines')).toBe(
        false,
      )
    }
  })

  it('the request frame conforms to incoming/get_log.json', async () => {
    const { client } = await mountLog()
    const call = client.lastRequestCall(GET_LOG_TYPE)
    const frame = {
      type: call.type,
      ...(call.data as Record<string, unknown> | undefined),
    }
    assertConformsToSchema(frame, GET_LOG_SCHEMA)
  })

  it('mounting while the route is not /log issues nothing; navigating there asks', async () => {
    const { client, router } = await mountLog('connected', null)
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(0)
    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
  })

  it('leaving /log does not itself ask for anything', async () => {
    const { client, router } = await mountLog()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
    router.navigate('/wifi')
    await settle()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
  })

  it('returning to /log on the same still-mounted view asks again', async () => {
    const { client, router } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
    await settle()
    router.navigate('/wifi')
    await settle()
    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(2)
  })

  it('the refresh control issues another request once the mount-time one has settled', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
    await settle()
    await click(refreshControl())
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(2)
  })

  it('a host switch in place asks the new unit for its own log', async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountLog(
      'connected',
      '/log',
      (c) => created.push(c),
    )
    expect(created).toHaveLength(1)
    expect(clientA.countRequestCalls(GET_LOG_TYPE)).toBe(1)

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
    expect(clientB.countRequestCalls(GET_LOG_TYPE)).toBe(1)
  })

  it('becoming current while offline still asks: being connected is not a precondition (SPEC 4.3.3, via 4.5.2.3)', async () => {
    const { client, router } = await mountLog('offline', null)
    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
  })

  // The coalescing owner lives in lib/stores.ts, shared with the other list
  // refreshes (SPEC 4.4.1's createCoalescedRefresh), and this is
  // peers-view.spec.ts's own "does not release an owner that is now B's"
  // test, adapted -- deliberately not a copy, because the log refresh does
  // NOT swallow its rejection the way handshakes/access_points/peers do
  // (SPEC 4.5.2.5's own sentence for log_unavailable exists precisely
  // because this refresh's failure is shown, not dropped). So a late
  // rejection from a detached client is a second, log-specific risk on top
  // of the owner-release one: it must not paint the now-current client's
  // screen with a sentence that was never about it.
  it("A's own late-settling get_log -- here a rejection, after B has already taken over -- does not release an owner that is now B's, and does not paint B's screen", async () => {
    const first = await mountLog()
    expect(first.client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null

    const second = await mountLog()
    const clientB = second.client
    expect(clientB.countRequestCalls(GET_LOG_TYPE)).toBe(1)

    // A's leftover get_log settles late, after B has taken over -- as a
    // rejection this time, since that is the path this refresh does not
    // swallow.
    first.client.rejectPending(
      GET_LOG_TYPE,
      new second.ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()

    // The owner was not released by A's late finally: B's own request is
    // still the one in flight, so a tap of the refresh control coalesces
    // into it instead of sending a duplicate.
    await click(refreshControl())
    expect(clientB.countRequestCalls(GET_LOG_TYPE)).toBe(1)

    // And B's screen was not painted by A's failure: no log_unavailable
    // sentence rendered for a rejection that was never about B.
    expect(emptyMessage()?.textContent).not.toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )

    // B's own, still-outstanding request settling normally afterward still
    // reaches the screen -- A's rejection did not leave B's view stuck.
    clientB.settle(
      GET_LOG_TYPE,
      logLinesEnvelope(['line from the unit B is now attached to']),
    )
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line from the unit B is now attached to',
    ])
  })

  // The coordinator's own five-step sequence: mount against A, switch to B
  // (taking ownership), switch back to A while A's first request is still
  // outstanding, settle A's first (stale) request, then settle A's second
  // with log_unavailable. Attempted here through the same settings seam
  // settings.spec.ts and this file's own host-switch tests use.
  //
  // That sequence turned out to describe something the code cannot do, on
  // two counts checked directly rather than assumed. First: a single client
  // can never hold two outstanding get_log requests in the first place --
  // tapping Follow while a request from any other occasion is still in
  // flight coalesces into it (refreshLog's own owner check), it does not
  // add a second one, so there is never a same-client pair for a stale
  // settle to confuse. Second, and what this test pins: "switching back to
  // A" does not hand back A's original client object. lib/session.ts's
  // rebuild() calls createClient() unconditionally on every switch,
  // including a switch back to a host already seen earlier in the run, so
  // A reappearing after a trip through B is a third, freshly built client,
  // never the first one again.
  //
  // Together those two facts are *why* the coalescing owner in
  // lib/stores.ts is allowed to compare client references rather than
  // needing a per-request token: reference comparison is exactly as safe as
  // a token for as long as no client object can ever be asked to answer for
  // a second, concurrent request, and the first fact above says that never
  // happens, and this test's own assertion says no earlier client can be
  // handed back to make it happen a different way. A per-request token was
  // added to guard a race that turns out to need both of those to fail
  // before it could ever fire; removing it removed a branch nothing could
  // reach (SPEC 4.5.2.1: a branch that cannot be reached is worse than
  // absent, because it reads as a case somebody handled). What keeps that
  // removal honest is this test: reference comparison stops being
  // sufficient the moment rebuild() starts reusing a client for a
  // reactivated host, so a future change in that direction breaks this
  // test's own assertion below before it can quietly reopen the race the
  // token used to close.
  it('switching back to a previously active host does not resurrect the original client object', async () => {
    const created: FakeWsClient[] = []
    const {
      client: clientA1,
      settings,
      ws,
    } = await mountLog('connected', '/log', (c) => created.push(c))
    expect(created).toHaveLength(1)

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

    // 'bluetooth' is the default host's stable id (see the no-active-host
    // test elsewhere in this file); switching back to it is switching back
    // to the same host record A was on originally.
    settings.activateHost('bluetooth')
    await settle()
    expect(created).toHaveLength(3)

    const clientA2 = created[2] as FakeWsClient
    expect(
      clientA2,
      'rebuild() is expected to build a fresh client on every switch, this one ' +
        'included -- see the block comment above for why that is load-bearing',
    ).not.toBe(clientA1)

    // Consequence: A1's own request and A2's own request are two different
    // clients' requests, not two outstanding requests on one client -- the
    // cross-client case the test above this one already covers, generalised
    // to a third hop. A1's very stale original reply settling here must
    // still not paint A2's screen, and A2's own reply must still reach it.
    expect(clientA1.countRequestCalls(GET_LOG_TYPE)).toBe(1)
    expect(clientA2.countRequestCalls(GET_LOG_TYPE)).toBe(1)

    clientA1.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(emptyMessage()?.textContent).not.toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )

    clientA2.settle(
      GET_LOG_TYPE,
      logLinesEnvelope(['line from the reattached unit']),
    )
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line from the reattached unit',
    ])
  })
})

// =============================================================================
// 2. The follow timer: off on arrival, an immediate ask on the tap that
//    turns it on and then a 10 s interval, and it stops asking the moment
//    the view is not current -- but survives the round trip itself: §4.5
//    keeps the view mounted, so only the asking stops, and both the
//    control's own state and the timer resume on return. This is the one
//    place fake timers are correct rather than forbidden (SPEC 4.5.2.5).
// =============================================================================

describe('the follow timer: off on arrival, an immediate ask plus a 10 s interval while on, stops (but does not reset) when not current', () => {
  it('off on arrival: mounting and waiting produces exactly one request, not a repeating one', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('follow off by default: the control reads "Follow" and is not pressed', async () => {
    await mountLog()
    const control = followControl()
    expect(control.textContent).toBe('Follow')
    expect(control.getAttribute('aria-pressed')).toBe('false')
  })

  it('turning follow on asks immediately, then again at 10 s and at 20 s', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope(['first line']))
      await settle()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(1)

      // The tap itself asks at once: a control that waited out the first
      // interval before doing anything would read as broken.
      await click(followControl())
      expect(followControl().textContent).toBe('Following')
      expect(followControl().getAttribute('aria-pressed')).toBe('true')
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(2)
      client.settle(GET_LOG_TYPE, logLinesEnvelope(['second line']))
      await settle()

      // Not again before 10 s from the tap.
      await vi.advanceTimersByTimeAsync(9_999)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(2)

      // At 10 s from the tap.
      await vi.advanceTimersByTimeAsync(1)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)
      client.settle(GET_LOG_TYPE, logLinesEnvelope(['third line']))
      await settle()

      // And again 10 s after that (20 s from the tap).
      await vi.advanceTimersByTimeAsync(10_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('the immediate, tap-triggered request carries no "lines" value', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl())
      const call = client.lastRequestCall(GET_LOG_TYPE)
      if (call.data !== undefined && call.data !== null) {
        expect(Object.prototype.hasOwnProperty.call(call.data, 'lines')).toBe(
          false,
        )
      }
    } finally {
      vi.useRealTimers()
    }
  })

  it('the interval-triggered request carries no "lines" value either', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl())
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await vi.advanceTimersByTimeAsync(10_000)
      const call = client.lastRequestCall(GET_LOG_TYPE)
      if (call.data !== undefined && call.data !== null) {
        expect(Object.prototype.hasOwnProperty.call(call.data, 'lines')).toBe(
          false,
        )
      }
    } finally {
      vi.useRealTimers()
    }
  })

  it('turning follow off stops the re-asking', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl())
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await vi.advanceTimersByTimeAsync(10_000)
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)

      await click(followControl())
      expect(followControl().textContent).toBe('Follow')
      expect(followControl().getAttribute('aria-pressed')).toBe('false')

      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  // ask() has nothing to ask when there is no active client: reached by
  // removing the active host through the settings seam (SPEC 4.7), the same
  // seam peers-view.spec.ts's own no-active-host refresh-control test uses,
  // rather than by unplugging the transport underneath a live client. Losing
  // the client is not a reason to silently turn Follow off -- the control
  // is a statement of intent ("I want to watch this"), and a control that
  // resets itself on a transient loss would need re-arming after every
  // tether hiccup.
  it('the follow timer with no active client: nothing requested, nothing thrown, and Follow stays on', async () => {
    vi.useFakeTimers()
    try {
      const created: FakeWsClient[] = []
      const { client, settings } = await mountLog('connected', '/log', (c) =>
        created.push(c),
      )
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()

      await click(followControl()) // immediate ask, turning Follow on
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      expect(followControl().getAttribute('aria-pressed')).toBe('true')

      const requestsBeforeRemoval = client.countRequestCalls(GET_LOG_TYPE)

      // 'bluetooth' is the stable id lib/settings.ts's own defaultHosts()
      // gives the entry loadSettings(() => 'not-an-ip-address') activates in
      // this file's mountLog(); removing it tears the client down (SPEC
      // 4.4.2) with no replacement to ask instead.
      settings.removeHost('bluetooth')
      await settle()

      // Advanced plainly: an unhandled throw from inside ask() with no
      // client to ask fails this test on its own, without needing an
      // assertion on what advanceTimersByTimeAsync itself resolves to (it
      // resolves to the `vi` object, not to `undefined`).
      await vi.advanceTimersByTimeAsync(10_000)

      // Nothing was asked of the now-detached client, and no new client was
      // built for the timer to ask instead.
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(requestsBeforeRemoval)
      expect(created).toHaveLength(1)

      // Losing the client is not a reason to silently turn Follow off.
      expect(followControl().getAttribute('aria-pressed')).toBe('true')
      expect(followControl().textContent).toBe('Following')
    } finally {
      vi.useRealTimers()
    }
  })

  it('follow stops asking the moment the view is no longer current: navigating away with follow on produces no further requests', async () => {
    vi.useFakeTimers()
    try {
      const { client, router } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl()) // immediate ask
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await vi.advanceTimersByTimeAsync(10_000) // interval ask
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)

      router.navigate('/wifi')
      await settle()
      await vi.advanceTimersByTimeAsync(60_000)
      // No new get_log request while /log is not the current route.
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('returning to /log resumes both the control\'s own state and the timer: only the asking stopped, not the toggle (SPEC 4.5.2.5, via 4.5\'s "views stay mounted")', async () => {
    vi.useFakeTimers()
    try {
      const { client, router } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl()) // immediate ask
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await vi.advanceTimersByTimeAsync(10_000) // interval ask
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)

      router.navigate('/wifi')
      await settle()
      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(3)

      // Returning fires the ordinary becoming-current ask...
      router.navigate('/log')
      await settle()
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(4)

      // ...and the control itself still reads "on": the toggle was never
      // reset, only the asking paused while the view was not current.
      expect(followControl().textContent).toBe('Following')
      expect(followControl().getAttribute('aria-pressed')).toBe('true')

      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()

      // And the 10 s interval itself has resumed, not merely the toggle's
      // own display.
      await vi.advanceTimersByTimeAsync(10_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(5)
    } finally {
      vi.useRealTimers()
    }
  })

  // The property the internal onDestroy in lib/viewRefresh.ts's own
  // subscription-leak fix exists to guarantee, pinned here for the follow
  // timer specifically: every prior test in this block stops the interval
  // by navigating away (the route no longer current), never by destroying
  // the component outright. Deliberately does not call stopSession() here:
  // the client and the session stay alive throughout, so a request appearing
  // after unmount could only come from the timer itself still running, not
  // from a torn-down client refusing to be asked.
  it('unmounting the view with Follow armed clears the interval: advancing the clock afterward asks nothing', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      await click(followControl()) // immediate ask
      client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
      await settle()
      const requestsBeforeUnmount = client.countRequestCalls(GET_LOG_TYPE)

      if (instance && svelte) svelte.unmount(instance)
      instance = null
      container?.remove()
      container = null

      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(requestsBeforeUnmount)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 2a. Following and scrolling are one control (SPEC 4.5.2.5): the container
//     scrolls to its end after every buffer change, and only while
//     following -- not on every render, not once on the tap.
//
//     jsdom performs no layout: scrollHeight and clientHeight are both
//     always zero on every element, so an assertion of the shape
//     `scrollTop === scrollHeight - clientHeight` would read `0 === 0`
//     whether or not the implementation contains any scrolling code at all
//     -- exactly the vacuous check this section exists to avoid. What is
//     observable honestly here is whether the property was written to:
//     spyOnScrollTop below shadows the element's native (always-zero)
//     scrollTop with an own-property override that records every
//     assignment, so a test can assert the container's scrollTop was set,
//     or was not, without claiming anything about the numeric value that
//     only real layout could make meaningful.
// =============================================================================

/** See the section comment above for why this, and not a value equality. */
function spyOnScrollTop(el: HTMLElement): { readonly calls: number[] } {
  const calls: number[] = []
  let value = 0
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get(): number {
      return value
    },
    set(v: number) {
      value = v
      calls.push(v)
    },
  })
  return { calls }
}

describe('scrolling to the end after every buffer change, and only while following', () => {
  it("with Follow off, a new buffer leaves the container's scrollTop untouched", async () => {
    const { client } = await mountLog()
    const spy = spyOnScrollTop(linesContainer())
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()
    expect(spy.calls).toHaveLength(0)
  })

  it("with Follow on, a new buffer writes the container's scrollTop (scrolled to its end)", async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one']))
    await settle()

    const spy = spyOnScrollTop(linesContainer())
    await click(followControl()) // immediate ask, Follow now on
    client.settle(
      GET_LOG_TYPE,
      logLinesEnvelope(['line one', 'line two', 'line three']),
    )
    await settle()

    expect(spy.calls.length).toBeGreaterThanOrEqual(1)
  })

  it('turning Follow off does not itself move anything: no scrollTop write on the toggle alone', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one']))
    await settle()
    await click(followControl()) // Follow on, immediate ask
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()

    const spy = spyOnScrollTop(linesContainer())
    await click(followControl()) // Follow off, no ask
    expect(spy.calls).toHaveLength(0)
  })

  it('once Follow is off again, a later buffer change (via the refresh control) no longer writes scrollTop', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one']))
    await settle()
    await click(followControl()) // Follow on
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()
    await click(followControl()) // Follow off again

    const spy = spyOnScrollTop(linesContainer())
    await click(refreshControl())
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line three']))
    await settle()
    expect(spy.calls).toHaveLength(0)
  })
})

// =============================================================================
// 3. log_unavailable gets its own sentence, distinct from an empty log and
//    from not-connected (SPEC 4.5.2.5, 4.3.5, 2.6.0, 2.9); only a genuine
//    log_unavailable raises it, only while no lines are held, and a later
//    success clears it.
// =============================================================================

describe('log_unavailable: its own sentence, only for that code, only while nothing is held, and cleared by a later success', () => {
  it('the unit answering log_unavailable renders the pinned sentence', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    const message = emptyMessage()
    expect(message).not.toBeNull()
    expect(message?.textContent).toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )
    expect(message?.getAttribute('role')).toBe('status')
  })

  it('the raw error code string never appears anywhere in the view (SPEC 4.5.3)', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(root().textContent ?? '').not.toContain('log_unavailable')
  })

  it('log_unavailable is distinct from the empty-log sentence', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(emptyMessage()?.textContent).not.toBe("The unit's log is empty.")
  })

  it('log_unavailable is distinct from the not-connected sentence', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(emptyMessage()?.textContent).not.toBe(
      'Not connected, so this list has not been read.',
    )
  })

  // SPEC 4.5.2.5: "Not connected outranks an unreadable log." Nothing clears
  // the unavailable reading on an ordinary drop (SPEC 4.4.2: resetStores()
  // runs on an explicit disconnect and on unauthorized, not on offline), so
  // a unit that answered log_unavailable and then went out of range must not
  // keep asserting, indefinitely, that it has no agent -- the same argument
  // SPEC 4.3.1 makes against a mirror that keeps showing the last thing it
  // saw.
  it('not connected outranks an unreadable log: log_unavailable followed by a drop shows the not-connected sentence, not the unavailable one', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(emptyMessage()?.textContent).toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )

    client.emitState('offline')
    await settle()
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  // Every rejection above is a RemoteError carrying log_unavailable, which
  // leaves two branches of the settle handling unexercised: a rejection
  // that is not a RemoteError at all, and one carrying a different code.
  // Neither is hypothetical -- lib/ws.ts rejects a closed client's pending
  // reads with a plain Error('the client was closed') and a timed-out read
  // with Error('request timed out'), so a tether dropping mid-request, the
  // ordinary case in the field, arrives as neither of the things the tests
  // above send. The rule: the screen keeps what it had. A plain Error, or a
  // RemoteError with some other code, must not raise the unavailable
  // sentence -- that sentence says something specific and alarming about
  // the unit having no agent, and a timeout is not evidence of it.
  it('a plain Error (a closed or timed-out request, not a RemoteError) leaves the screen where it was', async () => {
    const { client } = await mountLog()
    const before = emptyMessage()?.textContent
    client.rejectPending(GET_LOG_TYPE, new Error('the client was closed'))
    await settle()
    expect(emptyMessage()?.textContent).toBe(before)
    expect(emptyMessage()?.textContent).not.toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )
  })

  it('a plain Error on a later refresh leaves already-held lines on screen, untouched', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line one',
      'line two',
    ])

    await click(refreshControl())
    client.rejectPending(GET_LOG_TYPE, new Error('request timed out'))
    await settle()

    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line one',
      'line two',
    ])
    expect(emptyMessage()).toBeNull()
  })

  it('a RemoteError with a different code leaves the screen where it was', async () => {
    const { client, ws } = await mountLog()
    const before = emptyMessage()?.textContent
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('something else went wrong', 'internal_error'),
    )
    await settle()
    expect(emptyMessage()?.textContent).toBe(before)
    expect(emptyMessage()?.textContent).not.toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )
  })

  it('a RemoteError with a different code on a later refresh leaves already-held lines on screen, untouched', async () => {
    const { client, ws } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one']))
    await settle()

    await click(refreshControl())
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('malformed request', 'bad_request'),
    )
    await settle()

    expect(lineElements().map((el) => el.textContent)).toEqual(['line one'])
    expect(emptyMessage()).toBeNull()
  })

  // The unavailable flag being cleared by a later success is a separate
  // claim from the sentence appearing in the first place, and every settle()
  // in this file up to here executes the clearing line without a test ever
  // observing it: an implementation that set the flag once and never
  // cleared it would still pass everything above.
  it('a later successful refresh clears the unavailable sentence and shows the new lines', async () => {
    const { client, ws } = await mountLog()
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()
    expect(emptyMessage()?.textContent).toBe(
      'The unit could not read its log. With no agent it has no configuration to find the path in.',
    )

    await click(refreshControl())
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line after recovery']))
    await settle()

    expect(emptyMessage()).toBeNull()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line after recovery',
    ])
  })

  // SPEC 4.5.2.5: the copy table's log_unavailable row is qualified -- "no
  // lines held, and the unit answered log_unavailable" -- and "A held
  // buffer wins over a failed refresh" states the precedence explicitly: a
  // unit that delivered lines and then failed a later refresh keeps its
  // lines, because replacing them with a sentence would discard a real
  // reading in order to report a failure to get a newer one. Same fixture
  // as the plain-Error/different-code tests above, seen from the other
  // side: this is what a *genuine* log_unavailable does when lines are
  // already held, not merely what a lesser failure does.
  it('a held buffer wins over a failed refresh: lines already shown stay shown when a later refresh answers log_unavailable', async () => {
    const { client, ws } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line one',
      'line two',
    ])

    await click(refreshControl())
    client.rejectPending(
      GET_LOG_TYPE,
      new ws.RemoteError('log path not configured', 'log_unavailable'),
    )
    await settle()

    expect(lineElements().map((el) => el.textContent)).toEqual([
      'line one',
      'line two',
    ])
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 4. Empty vs. not-yet-fetched vs. unavailable, and the three-way distinction
//    by equality against 4.5.2.5's table.
// =============================================================================

describe("the three sentences, pinned by equality against 4.5.2.5's table", () => {
  it('no lines, connected: "The unit\'s log is empty."', async () => {
    const { client } = await mountLog('connected')
    client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
    await settle()
    expect(emptyMessage()?.textContent).toBe("The unit's log is empty.")
  })

  it('no lines, degraded (still counted as fetched): the same connected sentence', async () => {
    const { client } = await mountLog('degraded')
    client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
    await settle()
    expect(emptyMessage()?.textContent).toBe("The unit's log is empty.")
  })

  it("no lines, not connected: the shared not-connected sentence, identical to the other list views'", async () => {
    await mountLog('connecting')
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('no lines, offline: the same not-connected sentence', async () => {
    await mountLog('offline')
    expect(emptyMessage()?.textContent).toBe(
      'Not connected, so this list has not been read.',
    )
  })

  it('the empty message is absent once lines are rendered', async () => {
    const { client } = await mountLog('connected')
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['a log line']))
    await settle()
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 5. The filter: filters what is on screen, never the request; a
//    case-insensitive substring; a non-matching filter gets its own
//    sentence, distinct from the empty-log one.
// =============================================================================

describe('the filter filters the rendered lines and never the request (SPEC 4.5.2.5)', () => {
  it('typing a filter removes non-matching lines and keeps matching ones, and sends no new request', async () => {
    const { client } = await mountLog()
    client.settle(
      GET_LOG_TYPE,
      logLinesEnvelope([
        'starting monitor mode',
        'associated with TestNet_001',
        'entering AUTO mode',
      ]),
    )
    await settle()
    expect(lineElements()).toHaveLength(3)
    const requestsBefore = client.countRequestCalls(GET_LOG_TYPE)

    typeInto(filterInput(), 'testnet')
    await settle()

    expect(client.countRequestCalls(GET_LOG_TYPE)).toBe(requestsBefore)
    const remaining = lineElements().map((el) => el.textContent)
    expect(remaining).toEqual(['associated with TestNet_001'])
  })

  it('the match is case-insensitive in both directions', async () => {
    const { client } = await mountLog()
    client.settle(
      GET_LOG_TYPE,
      logLinesEnvelope(['UPPER CASE LINE', 'lower case line']),
    )
    await settle()

    typeInto(filterInput(), 'UPPER')
    await settle()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'UPPER CASE LINE',
    ])

    typeInto(filterInput(), 'lower')
    await settle()
    expect(lineElements().map((el) => el.textContent)).toEqual([
      'lower case line',
    ])
  })

  it('a filter matching nothing renders its own sentence, distinct from the empty-log one', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['starting monitor mode']))
    await settle()

    typeInto(filterInput(), 'this substring is not in the buffer')
    await settle()

    const message = emptyMessage()
    expect(message).not.toBeNull()
    expect(message?.textContent).toBe('No lines match the filter.')
    expect(message?.textContent).not.toBe("The unit's log is empty.")
  })

  it('clearing the filter brings every held line back', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line one', 'line two']))
    await settle()

    typeInto(filterInput(), 'one')
    await settle()
    expect(lineElements()).toHaveLength(1)

    typeInto(filterInput(), '')
    await settle()
    expect(lineElements()).toHaveLength(2)
  })

  // A precedence rule, not a fourth condition: with no lines held at all,
  // "No lines match the filter." would blame the filter for an absence that
  // has nothing to do with it and send somebody to clear a field that was
  // never the reason. The empty-log sentence wins whenever the buffer itself
  // is empty, filter text or not.
  it('an empty log wins over an unmatched filter: the empty-log sentence, not the filter one', async () => {
    const { client } = await mountLog('connected')
    client.settle(GET_LOG_TYPE, logLinesEnvelope([]))
    await settle()

    typeInto(filterInput(), 'anything at all')
    await settle()

    const message = emptyMessage()
    expect(message).not.toBeNull()
    expect(message?.textContent).toBe("The unit's log is empty.")
    expect(message?.textContent).not.toBe('No lines match the filter.')
  })
})

// =============================================================================
// 6. A matched substring is not highlighted (SPEC 4.5.2.5, via 4.5.3): a
//    filtered line creates no highlighting element around the match, inside
//    its <bdi> isolation boundary (SPEC 4.5.3).
// =============================================================================

describe('a matched substring is not highlighted: no highlighting element created (SPEC 4.5.2.5/4.5.3)', () => {
  it('an unfiltered line renders as text inside its <bdi>, with no other element created', async () => {
    const { client } = await mountLog()
    const line = 'associated with TestNet_001'
    client.settle(GET_LOG_TYPE, logLinesEnvelope([line]))
    await settle()
    const el = lineElements()[0] as HTMLElement
    expect(el.textContent).toBe(line)
    // assertRemoteStringIsolated subsumes the separate "bdi has no children"
    // and "no other injected element" checks this used to run one after the
    // other: it fails on either an unwrapped line or an extra element
    // anywhere in the field, including one nested inside the <bdi> itself.
    assertRemoteStringIsolated(el, line)
  })

  it('a line with an active filter match still renders as text inside its <bdi>, with no highlighting element created', async () => {
    const { client } = await mountLog()
    const line = 'associated with TestNet_001'
    client.settle(GET_LOG_TYPE, logLinesEnvelope([line]))
    await settle()

    typeInto(filterInput(), 'testnet')
    await settle()

    const el = lineElements()[0] as HTMLElement
    expect(el.textContent).toBe(line)
    assertRemoteStringIsolated(el, line)
  })
})

// =============================================================================
// 7. Every log line is hostile (SPEC 4.5.3): a pwnagotchi logs the SSIDs it
//    just saw, so a line is a stranger's string with a timestamp in front.
//    Rendered as text, both unfiltered and while a filter is matching it.
// =============================================================================

const HOSTILE_LINE =
  '2026-08-21 12:00:00 [INFO] associated with <script>window.__logViewPwned = true</script>"><img src=x onerror="window.__logViewPwned = true">'

describe('every log line renders as text, no element created (SPEC 4.5.3)', () => {
  beforeEach(() => {
    ;(window as unknown as { __logViewPwned?: boolean }).__logViewPwned =
      undefined
  })

  it('a hostile line renders as text, unfiltered', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope([HOSTILE_LINE]))
    await settle()
    const el = lineElements()[0] as HTMLElement
    expect(el.textContent).toBe(HOSTILE_LINE)
    assertRemoteStringIsolated(el, HOSTILE_LINE)
    expect(root().querySelector('script')).toBeNull()
    expect(root().querySelector('[onerror]')).toBeNull()
    expect(
      (window as unknown as { __logViewPwned?: boolean }).__logViewPwned,
    ).toBeUndefined()
  })

  it('a hostile line renders as text while a filter is matching it', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope([HOSTILE_LINE]))
    await settle()

    typeInto(filterInput(), 'associated')
    await settle()

    const el = lineElements()[0] as HTMLElement
    expect(el.textContent).toBe(HOSTILE_LINE)
    assertRemoteStringIsolated(el, HOSTILE_LINE)
    expect(root().querySelector('script')).toBeNull()
    expect(root().querySelector('[onerror]')).toBeNull()
    expect(
      (window as unknown as { __logViewPwned?: boolean }).__logViewPwned,
    ).toBeUndefined()
  })
})

// =============================================================================
// 7a. Bidi isolation (SPEC 4.5.3, "a pwnagotchi logs the SSIDs it just saw",
//     issue #219): a log line is a stranger's string with a timestamp in
//     front, so it is exactly the case the isolation rule exists for.
// =============================================================================

const BIDI_OVERRIDE = '‮'
const HOSTILE_BIDI_LINE = `2026-08-21 12:00:00 [INFO] associated with TestNet_${BIDI_OVERRIDE}lave.exe`
const RTL_LINE_ARABIC = '2026-08-21 12:00:00 [INFO] associated with شبكة_اختبار'
const RTL_LINE_HEBREW = '2026-08-21 12:00:00 [INFO] associated with רשת_בדיקה'

describe('bidi isolation for a log line (SPEC 4.5.3, issue #219)', () => {
  it('a line carrying U+202E is isolated inside a <bdi>, the character preserved rather than stripped', async () => {
    const { client } = await mountLog()
    client.settle(GET_LOG_TYPE, logLinesEnvelope([HOSTILE_BIDI_LINE]))
    await settle()
    const el = lineElements()[0] as HTMLElement
    expect(el.textContent).toBe(HOSTILE_BIDI_LINE)
    expect(el.textContent).toContain(BIDI_OVERRIDE)
    assertRemoteStringIsolated(el, HOSTILE_BIDI_LINE)
  })

  for (const rtlLine of [RTL_LINE_ARABIC, RTL_LINE_HEBREW]) {
    it(`a legitimate right-to-left line "${rtlLine}" renders unmangled, not stripped or reordered`, async () => {
      const { client } = await mountLog()
      client.settle(GET_LOG_TYPE, logLinesEnvelope([rtlLine]))
      await settle()
      const el = lineElements()[0] as HTMLElement
      expect(el.textContent).toBe(rtlLine)
    })
  }
})

// =============================================================================
// 8. The buffer replaces, never appends (SPEC 4.4.1): a second log_lines
//    reply with fewer lines leaves fewer lines on screen.
// =============================================================================

describe('the buffer replaces, never appends (SPEC 4.4.1)', () => {
  it('a shorter second reply leaves fewer lines, not more', async () => {
    const { client } = await mountLog()
    client.settle(
      GET_LOG_TYPE,
      logLinesEnvelope(['line 1', 'line 2', 'line 3', 'line 4', 'line 5']),
    )
    await settle()
    expect(lineElements()).toHaveLength(5)

    await click(refreshControl())
    client.settle(GET_LOG_TYPE, logLinesEnvelope(['line a', 'line b']))
    await settle()

    const remaining = lineElements().map((el) => el.textContent)
    expect(remaining).toEqual(['line a', 'line b'])
  })
})

// =============================================================================
// 9. Control copy, pinned by equality against 4.5.2.5's own table.
// =============================================================================

describe("control copy, pinned by equality against SPEC 4.5.2.5's table", () => {
  it('the refresh control reads "Refresh"', async () => {
    await mountLog()
    expect(refreshControl().textContent).toBe('Refresh')
  })

  it('the font-size control reads "Text size"', async () => {
    await mountLog()
    expect(fontSizeControl().textContent).toBe('Text size')
  })

  it('the filter input carries the accessible name "Filter", not only a placeholder', async () => {
    await mountLog()
    expect(accessibleName(filterInput())).toBe('Filter')
  })
})

// =============================================================================
// 9a. The font-size control: three steps, cycling in a fixed order and
//     wrapping, the current step carried as data-font-size on the view root
//     (SPEC 4.5.2.5) so a test reads the same fact the styling does rather
//     than inferring it from a rendered size.
// =============================================================================

describe('the font-size control cycles small -> medium -> large -> small, carried on the root as data-font-size', () => {
  const ORDER = ['small', 'medium', 'large'] as const

  it('the root starts at one of the three declared steps', async () => {
    await mountLog()
    expect(ORDER).toContain(fontSize())
  })

  it('each tap advances exactly one step in the fixed order, and the fourth tap wraps back to the first', async () => {
    await mountLog()
    const start = fontSize()
    const startIndex = ORDER.indexOf(start as (typeof ORDER)[number])
    expect(
      startIndex,
      `expected a recognised starting step, got "${start}"`,
    ).not.toBe(-1)

    await click(fontSizeControl())
    expect(fontSize()).toBe(ORDER[(startIndex + 1) % 3])

    await click(fontSizeControl())
    expect(fontSize()).toBe(ORDER[(startIndex + 2) % 3])

    await click(fontSizeControl())
    expect(fontSize()).toBe(start)
  })

  it('kept while the app is open, not persisted: it survives navigating away and back, like the Wi-Fi segment choice (SPEC 4.5.2.5/4.5)', async () => {
    const { router } = await mountLog()
    const start = fontSize()
    await click(fontSizeControl())
    const changed = fontSize()
    expect(changed).not.toBe(start)

    router.navigate('/wifi')
    await settle()
    router.navigate('/log')
    await settle()
    expect(fontSize()).toBe(changed)
  })
})

// =============================================================================
// 10. The view root.
// =============================================================================

describe('the view root', () => {
  it('carries data-view="log"', async () => {
    await mountLog()
    expect(root().getAttribute('data-view')).toBe('log')
  })

  it('carries a scrolling lines container, data-log-lines', async () => {
    await mountLog()
    expect(linesContainer()).not.toBeNull()
  })
})
