import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import getScreenSchema from '../../../docs/schemas/incoming/get_screen.json'
import screenImageSchema from '../../../docs/schemas/outgoing/screen_image.json'

import { DASH, EMPTY_LABEL, formatUnitTime } from '../lib/format'
import type { OutgoingMessage, OutgoingScreenImage } from '../lib/protocol'
import type { ConnectionState, Diagnostics, UnauthorizedReason, WsClient, WsClientOptions } from '../lib/ws'

// Written from SPEC.md 4.5.2.6 ("The Mirror, the second timer, and a string
// that becomes a URL", issue #196), which explicitly adopts 4.5.2.5's
// follow-timer terms (the second timer this client is allowed, granted on
// identical terms: opt-in, off on arrival, asking immediately on the tap and
// then on the interval, stopping the moment the view is not current,
// surviving the trip away and back) and 4.5.2.3's three refresh occasions
// (the route becoming this view's own, the refresh control, the active host
// changing under a view that is already current). Also read for this file:
// 4.5 (routes, views stay mounted -- load-bearing for the auto-survives-
// navigation tests), 4.5.1.1 (the DOM-hook/copy-lives-nowhere-but-SPEC
// conventions this view reuses, and specifically the "a value that is not
// known renders as a dash, and its row stays" rule this file reads onto
// data-field="frameTime"), 4.5.2 (what the screen holds: full e-ink PNG via
// get_screen, manual refresh, auto ~5s toggle), 4.5.3 (remote strings are
// attacker-chosen -- the one place this section names as the place somebody
// would build a URL out of one), 4.4.1 (the `screen` store: on demand only,
// never pushed, cleared by resetStores() with everything else -- "one
// unit's display left on another's Mirror... the harder [defect] to
// notice, because a picture of a face carries no address to give it
// away"), 4.3.5 (no_frame: "in the Mirror view: not an error, the unit has
// not drawn yet"), 2.10 (the wire contract: base64 PNG, no prefix, on
// demand only, no_frame on a missing frame file) and 10.7.
//
// Deliberately not read: views/Mirror.svelte, nor whatever lib/ surface the
// auto timer and the base64 shape guard are built on (a dedicated
// lib/mirror.ts, an extension of lib/viewRefresh.ts, or something inline in
// the view -- this file does not care which, and imports none of them).
//
// Every element is reached through the DOM hooks 4.5.2.6 declares
// (data-view, data-screen-frame, data-field="frameTime", data-action,
// data-empty-message) and nothing else. Every copy assertion is a literal
// transcribed from 4.5.2.6's own tables, never a string pulled from
// lib/format.ts (formatUnitTime is the one exception: it is 4.5.1.1's own
// function under test as a fact about the field's *value*, not about the
// view's copy, and format.spec.ts already owns formatUnitTime's own
// behaviour). The harness (FakeWsClient, mount-via-startSession,
// settle()/flushSync()+tick(), the dynamic-import-only rule for anything
// whose identity matters) is log-view.spec.ts's, reused wholesale per this
// task's own instruction. formatUnitTime and DASH are imported statically:
// both are pure functions with no module-scoped session state, the same
// reason dashboard.spec.ts imports lib/format.ts statically rather than
// through the per-test dynamic import.

// ---------------------------------------------------------------------------
// Schema conformance, without ajv (not a frontend dependency, matching
// dashboard-controls.spec.ts's and log-view.spec.ts's own precedent): the
// schema JSON itself, imported directly, checked for additionalProperties,
// required and the "type" const.
// ---------------------------------------------------------------------------

interface JsonSchemaLike {
  title: string
  properties: Record<string, { const?: string; enum?: unknown[]; type?: string }>
  required: string[]
}

const GET_SCREEN_SCHEMA = getScreenSchema as JsonSchemaLike
const SCREEN_IMAGE_SCHEMA = screenImageSchema as JsonSchemaLike

function assertConformsToSchema(frame: Record<string, unknown>, schema: JsonSchemaLike): void {
  const allowedKeys = Object.keys(schema.properties)
  for (const key of Object.keys(frame)) {
    expect(allowedKeys, `"${key}" is not a property of ${schema.title}`).toContain(key)
  }
  for (const key of schema.required) {
    expect(Object.prototype.hasOwnProperty.call(frame, key), `missing required "${key}"`).toBe(true)
  }
  expect(frame.type).toBe(schema.properties.type?.const)
}

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

const T = 1_700_000_000
const GET_SCREEN_TYPE = (getScreenSchema as { properties: { type: { const: string } } }).properties
  .type.const

// `Buffer.from('synthetic-pwnagotchi-frame-fixture-bytes').toString('base64')`,
// computed once and pasted here so the fixture stays deterministic without a
// runtime dependency on Buffer inside the view's own code path. This is what
// 4.5.2.6 calls a shape that passes the base64-alphabet guard: it is not
// claimed to decode to a real PNG, only to be a string the guard accepts.
const VALID_PNG_B64 = 'c3ludGhldGljLXB3bmFnb3RjaGktZnJhbWUtZml4dHVyZS1ieXRlcw=='

function screenImageEnvelope(png: string, mtime: number, timestamp = T): OutgoingScreenImage {
  return { type: 'screen_image', timestamp, data: { png, mtime } }
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
// A hand-driven WsClient double, log-view.spec.ts's shape.
// ---------------------------------------------------------------------------

interface RecordedRequest {
  type: string
  data: unknown
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient {
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
    return Promise.reject(new Error('the mirror view must never send a command, only a read'))
  }
  sendGps(): void {}
  diagnostics(): Diagnostics {
    return { lastError: null, latencyMs: null }
  }
  onDiagnostics(): () => void {
    return () => {}
  }

  emitState(state: ConnectionState, reason: UnauthorizedReason | null = null): void {
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

  lastRequestCall(type: string): RecordedRequest {
    const calls = this.requestCalls.filter((entry) => entry.type === type)
    const last = calls[calls.length - 1]
    expect(last, `expected at least one "${type}" request`).toBeDefined()
    return last as RecordedRequest
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake as unknown as WsClient
}

// ---------------------------------------------------------------------------
// Mounting: Mirror.svelte via a real session (startSession), log-view.spec.ts's
// pattern exactly. lib/settings.ts, lib/session.ts, lib/ws.ts, lib/router.ts
// and the view are imported together, after vi.resetModules(), so the module
// singletons never diverge -- every runtime value below (including
// ws.RemoteError, used for the no_frame tests) is taken from the same dynamic
// import mountMirror() itself performs, never from a statically imported
// symbol.
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
 * `route`, like log-view.spec.ts's mountLog(), is the refresh trigger itself,
 * not merely a starting condition -- views stay mounted across navigation
 * (SPEC 4.5), so a mount-only trigger would fire once per app launch and
 * never again. The default '/mirror' pre-navigates before the component ever
 * subscribes. Passing `null` mounts the view while the route is whatever it
 * already was and leaves navigating to '/mirror' to the test.
 */
async function mountMirror(
  initialState: ConnectionState = 'connected',
  route: string | null = '/mirror',
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

  const module = (await import('../views/Mirror.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  const mountTarget = document.createElement('div')
  document.body.appendChild(mountTarget)
  container = mountTarget
  instance = svelte.mount(module.default, { target: mountTarget }) as Record<string, unknown>
  await settle()
  return { client, settings, session, router, ws, target: mountTarget }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }))
  await settle()
}

// ---------------------------------------------------------------------------
// Queries, on the declared data-* hooks only (SPEC 4.5.2.6's "The DOM
// hooks", closed list).
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('Mirror view is not mounted')
  const viewRoot = container.querySelector('[data-view="mirror"]')
  expect(viewRoot, 'expected [data-view="mirror"] inside the mount container').not.toBeNull()
  return viewRoot as HTMLElement
}

function frameImage(): HTMLImageElement | null {
  return root().querySelector('img[data-screen-frame]')
}

function anyImage(): HTMLImageElement | null {
  return root().querySelector('img')
}

function frameTimeField(): HTMLElement {
  const found = root().querySelector('[data-field="frameTime"]')
  expect(found, 'expected [data-field="frameTime"]').not.toBeNull()
  return found as HTMLElement
}

function frameTimeText(): string {
  return (frameTimeField().textContent ?? '').trim()
}

// SPEC 4.5.1.1 puts the accessible EMPTY_LABEL inside the same element as
// the value, deliberately -- a dash on its own is not readable aloud -- and
// textContent flattens the two into one string. dashboard.spec.ts's own
// visibleFieldText() solves this by splitting EMPTY_LABEL back out so a
// DASH-equality assertion is about the visible dash, not about whether a
// label happens to sit beside it; wifi-view.spec.ts and peers-view.spec.ts
// both reuse the same shape rather than re-deriving it, and this file does
// the same rather than a third time. Deliberately indifferent to whether a
// separator sits between the dash and the label (issue #188: whether
// assistive technology announces a boundary at the element edge has never
// been measured), which is why this splits and joins rather than asserting
// on a fixed separator string.
function visibleFrameTimeText(): string {
  return frameTimeText().split(EMPTY_LABEL).join('').trim()
}

function emptyMessage(): HTMLElement | null {
  return root().querySelector('[data-empty-message]')
}

function refreshControl(): HTMLElement {
  const found = root().querySelector('[data-action="refresh"]')
  expect(found, 'expected [data-action="refresh"]').not.toBeNull()
  return found as HTMLElement
}

function autoControl(): HTMLElement {
  const found = root().querySelector('[data-action="auto"]')
  expect(found, 'expected [data-action="auto"]').not.toBeNull()
  return found as HTMLElement
}

// Standard accessible-name resolution order (explicit aria-label first, then
// aria-labelledby, then any wrapping/associated <label>), log-view.spec.ts's
// own helper, reused here for the image's accessible name.
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

  // An <img>'s alt text is also part of its accessible name computation.
  const alt = el.getAttribute('alt')
  if (alt) return alt.trim()

  return ''
}

// =============================================================================
// 1. The refresh occasions (SPEC 4.5.2.3, adopted by 4.5.2.6): mounting on
//    /mirror, the refresh control, and a host switch in place.
// =============================================================================

describe('the refresh: route becoming /mirror, the refresh control, and a host switch, per 4.5.2.6\'s adoption of 4.5.2.3', () => {
  it('mounting the view issues exactly one get_screen request', async () => {
    const { client } = await mountMirror()
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
  })

  it('the request frame conforms to incoming/get_screen.json', async () => {
    const { client } = await mountMirror()
    const call = client.lastRequestCall(GET_SCREEN_TYPE)
    const frame = { type: call.type, ...(call.data as Record<string, unknown> | undefined) }
    assertConformsToSchema(frame, GET_SCREEN_SCHEMA)
  })

  it('mounting while the route is not /mirror issues nothing; navigating there asks', async () => {
    const { client, router } = await mountMirror('connected', null)
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(0)
    router.navigate('/mirror')
    await settle()
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
  })

  it('the refresh control issues another request once the mount-time one has settled', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    await click(refreshControl())
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(2)
  })

  it('becoming current while offline still asks: being connected is not a precondition (SPEC 4.3.3, via 4.5.2.3)', async () => {
    const { client, router } = await mountMirror('offline', null)
    router.navigate('/mirror')
    await settle()
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
  })

  // SPEC 4.4.1 puts `screen` under the same store architecture as `log`,
  // whose own coalescing owner (createCoalescedRefresh) is pinned in full by
  // log-view.spec.ts; that mechanism is not duplicated here. This is the one
  // observable consequence that would tell an implementer diverged: a
  // second tap of the refresh control while the mount-time request is still
  // outstanding must not add a second request, only coalesce into the one
  // already in flight.
  it('a second tap of the refresh control while a request is still in flight coalesces into it: no second request', async () => {
    const { client } = await mountMirror()
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
    await click(refreshControl())
    expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
  })
})

// =============================================================================
// 2. The frame is held and rendered, and a host switch clears it -- the
//    harder-to-notice defect SPEC 4.5.2.6 names explicitly: "a host switch
//    leaving unit A's display on B's Mirror... a picture of a face carries
//    no address to give it away."
// =============================================================================

describe('the frame is held and rendered, and cleared on a host switch (SPEC 4.5.2.6, 4.4.1)', () => {
  it('a valid frame renders an img carrying data-screen-frame', async () => {
    const { client } = await mountMirror()
    expect(frameImage()).toBeNull()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    expect(frameImage()).not.toBeNull()
    expect(emptyMessage()).toBeNull()
  })

  it("a host switch clears unit A's frame before unit B's own reply ever arrives", async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountMirror('connected', '/mirror', (c) =>
      created.push(c),
    )
    clientA.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    expect(frameImage()).not.toBeNull()

    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.9',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()

    // The host switch itself already fired a request for B, but B has not
    // answered it yet: if A's frame were still on screen at this exact
    // moment, that would be the defect this section calls harder to notice
    // than the equivalent one on the Log, because nothing about the picture
    // says whose unit drew it.
    expect(created).toHaveLength(2)
    const clientB = created[1] as FakeWsClient
    expect(clientB.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
    expect(frameImage()).toBeNull()
    expect(emptyMessage()).not.toBeNull()
  })

  it("unit B's own frame renders normally once its request settles", async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountMirror('connected', '/mirror', (c) =>
      created.push(c),
    )
    clientA.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()

    const hostB = settings.addHost({
      label: 'Unit B',
      address: '172.20.10.9',
      wsPort: 8082,
      httpPort: 8443,
      token: null,
    })
    settings.activateHost(hostB.id)
    await settle()
    const clientB = created[1] as FakeWsClient

    clientB.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T + 100))
    await settle()
    expect(frameImage()).not.toBeNull()
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 3. The auto toggle: off on arrival, an immediate ask on the tap and then a
//    5 s interval, stopping the moment the view is not current, surviving
//    the trip away and back, cleared on destroy -- SPEC 4.5.2.6's own terms,
//    "granted... on identical terms" to 4.5.2.5's follow timer.
// =============================================================================

describe('the auto toggle: off on arrival, an immediate ask plus a 5 s interval while on, stops (but does not reset) when not current', () => {
  it('off on arrival: mounting and waiting produces exactly one request, not a repeating one', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountMirror()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('auto off by default: the control reads "Refresh automatically" and is not pressed', async () => {
    await mountMirror()
    const control = autoControl()
    expect(control.textContent).toBe('Refresh automatically')
    expect(control.getAttribute('aria-pressed')).toBe('false')
  })

  it('turning auto on asks immediately, then again at 5 s and at 10 s', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(1)

      // The tap itself asks at once: a control that waited out the first
      // interval before doing anything would read as broken.
      await click(autoControl())
      expect(autoControl().textContent).toBe('Refreshing automatically')
      expect(autoControl().getAttribute('aria-pressed')).toBe('true')
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(2)
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 4))
      await settle()

      // Not again before 5 s from the tap.
      await vi.advanceTimersByTimeAsync(4_999)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(2)

      // At 5 s from the tap.
      await vi.advanceTimersByTimeAsync(1)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 3))
      await settle()

      // And again 5 s after that (10 s from the tap).
      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(4)
    } finally {
      vi.useRealTimers()
    }
  })

  it('turning auto off stops the re-asking', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await click(autoControl())
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await vi.advanceTimersByTimeAsync(5_000)
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)

      await click(autoControl())
      expect(autoControl().textContent).toBe('Refresh automatically')
      expect(autoControl().getAttribute('aria-pressed')).toBe('false')

      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('auto stops asking the moment the view is no longer current: navigating away with auto on produces no further requests', async () => {
    vi.useFakeTimers()
    try {
      const { client, router } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await click(autoControl())
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await vi.advanceTimersByTimeAsync(5_000)
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)

      router.navigate('/wifi')
      await settle()
      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('returning to /mirror resumes both the control\'s own state and the timer: only the asking stopped, not the toggle (SPEC 4.5.2.6, via 4.5\'s "views stay mounted")', async () => {
    vi.useFakeTimers()
    try {
      const { client, router } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await click(autoControl())
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await vi.advanceTimersByTimeAsync(5_000)
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)

      router.navigate('/wifi')
      await settle()
      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(3)

      // Returning fires the ordinary becoming-current ask...
      router.navigate('/mirror')
      await settle()
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(4)

      // ...and the control itself still reads "on": the toggle was never
      // reset, only the asking paused while the view was not current.
      expect(autoControl().textContent).toBe('Refreshing automatically')
      expect(autoControl().getAttribute('aria-pressed')).toBe('true')

      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()

      // And the 5 s interval itself has resumed, not merely the toggle's
      // own display.
      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(5)
    } finally {
      vi.useRealTimers()
    }
  })

  // ask() has nothing to ask when there is no active client: reached by
  // removing the active host through the settings seam (SPEC 4.7), the same
  // seam log-view.spec.ts's own no-active-host follow-timer test uses,
  // rather than by unplugging the transport underneath a live client.
  // Losing the client is not a reason to silently turn auto off -- the
  // control is a statement of intent ("keep showing me this unit's
  // display"), and a control that resets itself on a transient loss would
  // need re-arming after every tether hiccup.
  it('the auto timer with no active client: nothing requested, nothing thrown, and auto stays on', async () => {
    vi.useFakeTimers()
    try {
      const created: FakeWsClient[] = []
      const { client, settings } = await mountMirror('connected', '/mirror', (c) =>
        created.push(c),
      )
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()

      await click(autoControl()) // immediate ask, turning auto on
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      expect(autoControl().getAttribute('aria-pressed')).toBe('true')

      const requestsBeforeRemoval = client.countRequestCalls(GET_SCREEN_TYPE)

      // 'bluetooth' is the stable id lib/settings.ts's own defaultHosts()
      // gives the entry loadSettings(() => 'not-an-ip-address') activates in
      // this file's mountMirror(); removing it tears the client down (SPEC
      // 4.4.2) with no replacement to ask instead.
      settings.removeHost('bluetooth')
      await settle()

      // Advanced plainly: an unhandled throw from inside the timer's own
      // ask with no client to ask fails this test on its own.
      await vi.advanceTimersByTimeAsync(5_000)

      // Nothing was asked of the now-detached client, and no new client was
      // built for the timer to ask instead.
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(requestsBeforeRemoval)
      expect(created).toHaveLength(1)

      // Losing the client is not a reason to silently turn auto off.
      expect(autoControl().getAttribute('aria-pressed')).toBe('true')
      expect(autoControl().textContent).toBe('Refreshing automatically')
    } finally {
      vi.useRealTimers()
    }
  })

  // The property lib/viewRefresh.ts's own subscription-leak fix exists to
  // guarantee (SPEC 4.5.2.5's own equivalent test), pinned here for the
  // auto timer specifically. Deliberately does not call stopSession() here:
  // the client and the session stay alive throughout, so a request
  // appearing after unmount could only come from the timer itself still
  // running, not from a torn-down client refusing to be asked.
  it('unmounting the view with auto armed clears the interval: advancing the clock afterward asks nothing', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      await click(autoControl())
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
      await settle()
      const requestsBeforeUnmount = client.countRequestCalls(GET_SCREEN_TYPE)

      if (instance && svelte) svelte.unmount(instance)
      instance = null
      container?.remove()
      container = null

      await vi.advanceTimersByTimeAsync(60_000)
      expect(client.countRequestCalls(GET_SCREEN_TYPE)).toBe(requestsBeforeUnmount)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 4. A base64 string that is not base64 renders a sentence, not an image
//    (SPEC 4.5.2.6, via 4.5.3 and issue #109). The shape check has two
//    halves for two different reasons, per 4.5.2.6's own text, and this
//    section keeps them apart rather than folding both into one fixture
//    list:
//
//    - the ALPHABET half is the one 4.5.3 cares about, because a quote or
//      an angle bracket in the payload is a string leaving the URL it was
//      supposed to stay inside. Quotes and angle brackets are the
//      interesting characters for exactly that reason.
//    - the LENGTH half (padding only at the end, a length that is a
//      multiple of four) is checked for an unrelated reason: a wrong-length
//      payload is not dangerous, merely unrenderable, and without the
//      check the screen would show a broken image with nothing saying why.
//      It turns a silent failure into the pinned sentence.
//
//    Neither half establishes that the bytes are a PNG -- no amount of
//    shape-checking could -- and the length-only fixture below is written
//    to keep that reading honest: it is alphabet-clean and would pass an
//    alphabet-only check, so it is worth nothing unless the length half is
//    also enforced.
// =============================================================================

const HOSTILE_PNG_PAYLOADS = [
  '"><img src=x onerror=window.__mirrorViewPwned=true>',
  "'''\"\"\"",
  '<script>window.__mirrorViewPwned = true</script>',
  'not base64 at all, has spaces and punctuation!',
]

// Alphabet-clean (letters and digits only, no padding, no stray character)
// but 11 characters long, which is not a multiple of four -- the case the
// alphabet half of the guard alone would let through. Deliberately not one
// of HOSTILE_PNG_PAYLOADS above: this fixture tests the LENGTH half of the
// check, not the alphabet half, and the two must not be conflated into one
// assertion that a future refactor could satisfy by keeping only one of
// them.
const WRONG_LENGTH_PNG_PAYLOAD = 'YWJjZGVmZ2g'

describe('a base64 string that is not base64 renders a sentence, not an image (SPEC 4.5.2.6, 4.5.3, issue #109)', () => {
  beforeEach(() => {
    ;(window as unknown as { __mirrorViewPwned?: boolean }).__mirrorViewPwned = undefined
  })

  for (const payload of HOSTILE_PNG_PAYLOADS) {
    it(`payload ${JSON.stringify(payload)}: no img is rendered and the string appears nowhere in the DOM`, async () => {
      const { client } = await mountMirror()
      client.settle(GET_SCREEN_TYPE, screenImageEnvelope(payload, T - 5))
      await settle()

      // No image element at all -- not merely one missing data-screen-frame.
      expect(anyImage()).toBeNull()
      expect(frameImage()).toBeNull()

      const message = emptyMessage()
      expect(message).not.toBeNull()
      expect(message?.textContent).toBe('The frame could not be read.')

      // The raw payload never appears anywhere in the mounted tree, as text
      // or as markup, and nothing it could have injected executed.
      expect(root().innerHTML).not.toContain(payload)
      expect(root().querySelector('script')).toBeNull()
      expect(root().querySelector('[onerror]')).toBeNull()
      expect(
        (window as unknown as { __mirrorViewPwned?: boolean }).__mirrorViewPwned,
      ).toBeUndefined()
    })
  }

  it('a payload with a valid alphabet but the wrong length (not a multiple of four) also renders the unreadable sentence, not an img', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(WRONG_LENGTH_PNG_PAYLOAD, T - 5))
    await settle()

    expect(anyImage()).toBeNull()
    expect(frameImage()).toBeNull()
    expect(emptyMessage()?.textContent).toBe('The frame could not be read.')
  })

  // The empty string is refused by the same group rule that refuses a
  // wrong length (SPEC 4.5.2.6): a base64 string's final group holds two to
  // four characters, and there is no zero-length alternative for an empty
  // string to match. So this case needs no guard of its own -- it is not,
  // as an earlier draft of this comment claimed, a gap the length rule
  // misses because zero happens to be a multiple of four. What this test
  // pins is only that the empty string keeps being refused, whichever line
  // in the implementation does the refusing, kept separate from
  // WRONG_LENGTH_PNG_PAYLOAD's test so a regression in either shape is
  // named by the failing test rather than merged into one assertion.
  it('an empty png string renders the unreadable sentence and no img at all', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope('', T - 5))
    await settle()

    expect(anyImage()).toBeNull()
    expect(frameImage()).toBeNull()
    expect(emptyMessage()?.textContent).toBe('The frame could not be read.')
  })

  it('a valid-shaped payload does not raise the unreadable sentence', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(frameImage()).not.toBeNull()
  })

  it('the unreadable sentence is distinct from the no-frame-yet sentence', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(HOSTILE_PNG_PAYLOADS[0] as string, T - 5))
    await settle()
    expect(emptyMessage()?.textContent).not.toBe('The unit has not drawn a frame yet.')
  })

  it('a later valid frame recovers from the unreadable sentence', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(HOSTILE_PNG_PAYLOADS[0] as string, T - 5))
    await settle()
    expect(emptyMessage()?.textContent).toBe('The frame could not be read.')

    await click(refreshControl())
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 4))
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(frameImage()).not.toBeNull()
  })
})

// =============================================================================
// 5. A valid frame becomes a data: URL and never a blob: one (SPEC 4.5.2.6,
//    2.15.1's img-src carries data: and deliberately omits blob:).
// =============================================================================

describe('a valid frame becomes a data: URL, never a blob: one (SPEC 4.5.2.6)', () => {
  it('the img src begins with data:image/png;base64, and carries the exact payload', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    const img = frameImage()
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toBe(`data:image/png;base64,${VALID_PNG_B64}`)
  })

  it('the img src never begins with blob:', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    const src = frameImage()?.getAttribute('src') ?? ''
    expect(src.startsWith('blob:')).toBe(false)
  })
})

// =============================================================================
// 6. No inline style attribute anywhere in this view (SPEC 4.5.2.6, 2.15.1's
//    style-src 'self' forbids the attribute and not only the block, and
//    names this view as the place somebody reaches for one when sizing an
//    image).
// =============================================================================

describe('no inline style attribute anywhere in the view (SPEC 4.5.2.6, 2.15.1)', () => {
  it('with no frame held, nothing in the mounted tree carries a style attribute', async () => {
    await mountMirror()
    const styled = root().querySelectorAll('[style]')
    expect(styled.length).toBe(0)
  })

  it('with a frame rendered, nothing in the mounted tree carries a style attribute', async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    const styled = root().querySelectorAll('[style]')
    expect(styled.length).toBe(0)
  })

  it('with the auto toggle on, nothing in the mounted tree carries a style attribute', async () => {
    await mountMirror()
    await click(autoControl())
    const styled = root().querySelectorAll('[style]')
    expect(styled.length).toBe(0)
  })
})

// =============================================================================
// 7. no_frame renders the same sentence as a frame that has never arrived --
//    deliberately, per SPEC 4.5.2.6 ("they ARE the same thing from the
//    owner's side") -- and the raw code never reaches the DOM (4.5.3, and
//    4.3.5's own row: "in the Mirror view: not an error").
// =============================================================================

describe('no_frame renders the same sentence as a frame that has never arrived (SPEC 4.5.2.6, 4.3.5)', () => {
  it('before any reply: "The unit has not drawn a frame yet."', async () => {
    await mountMirror()
    expect(emptyMessage()?.textContent).toBe('The unit has not drawn a frame yet.')
  })

  it('the unit answering no_frame: the identical sentence, not a distinct one', async () => {
    const { client, ws } = await mountMirror()
    client.rejectPending(GET_SCREEN_TYPE, new ws.RemoteError('no frame written yet', 'no_frame'))
    await settle()
    expect(emptyMessage()?.textContent).toBe('The unit has not drawn a frame yet.')
  })

  it('the raw error code string never appears anywhere in the view', async () => {
    const { client, ws } = await mountMirror()
    client.rejectPending(GET_SCREEN_TYPE, new ws.RemoteError('no frame written yet', 'no_frame'))
    await settle()
    expect(root().textContent ?? '').not.toContain('no_frame')
  })

  it('no_frame does not raise the unreadable-frame sentence', async () => {
    const { client, ws } = await mountMirror()
    client.rejectPending(GET_SCREEN_TYPE, new ws.RemoteError('no frame written yet', 'no_frame'))
    await settle()
    expect(emptyMessage()?.textContent).not.toBe('The frame could not be read.')
  })

  it('no_frame followed by a later valid frame clears the sentence', async () => {
    const { client, ws } = await mountMirror()
    client.rejectPending(GET_SCREEN_TYPE, new ws.RemoteError('no frame written yet', 'no_frame'))
    await settle()
    expect(emptyMessage()).not.toBeNull()

    await click(refreshControl())
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 4))
    await settle()
    expect(emptyMessage()).toBeNull()
    expect(frameImage()).not.toBeNull()
  })
})

// =============================================================================
// 8. Empty/not-connected sentences, pinned by equality against 4.5.2.6's
//    table, including the not-connected sentence 4.5.2.3 shares unchanged.
// =============================================================================

describe('the sentences, pinned by equality against 4.5.2.6\'s table', () => {
  it('no frame held, connected: "The unit has not drawn a frame yet."', async () => {
    await mountMirror('connected')
    expect(emptyMessage()?.textContent).toBe('The unit has not drawn a frame yet.')
  })

  it('no frame held, degraded (still counted as connected): the same sentence', async () => {
    await mountMirror('degraded')
    expect(emptyMessage()?.textContent).toBe('The unit has not drawn a frame yet.')
  })

  it('no frame held, not connected (connecting): the shared not-connected sentence', async () => {
    await mountMirror('connecting')
    expect(emptyMessage()?.textContent).toBe('Not connected, so this list has not been read.')
  })

  it('no frame held, offline: the same not-connected sentence', async () => {
    await mountMirror('offline')
    expect(emptyMessage()?.textContent).toBe('Not connected, so this list has not been read.')
  })

  it('the empty message carries role="status"', async () => {
    await mountMirror('connected')
    expect(emptyMessage()?.getAttribute('role')).toBe('status')
  })

  it('the empty message is absent once a frame is rendered', async () => {
    const { client } = await mountMirror('connected')
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 9. The frame's own mtime is shown, through formatUnitTime, and it is the
//    frame's time and not the arrival time (SPEC 4.5.2.6).
// =============================================================================

describe("the frame's own mtime is shown through formatUnitTime, not the arrival time (SPEC 4.5.2.6)", () => {
  // Two separate claims, deliberately asserted separately: data-empty="true"
  // is one fact (SPEC 4.5.1.1's own hook) and the visible dash is another
  // (the value the reader sees), and one can be true without the other --
  // exactly the gap a single combined assertion against the flattened
  // textContent would paper over.
  it('with no frame held, the frameTime field carries data-empty="true"', async () => {
    await mountMirror('connected')
    expect(frameTimeField().getAttribute('data-empty')).toBe('true')
  })

  it('with no frame held, the visible text is the dash alone', async () => {
    await mountMirror('connected')
    expect(visibleFrameTimeText()).toBe(DASH)
  })

  it("the field shows the frame's mtime, formatted through formatUnitTime, and is no longer empty", async () => {
    const { client } = await mountMirror()
    const frameMtime = T - 7_200 // two hours before the envelope's own timestamp
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, frameMtime, T))
    await settle()
    expect(frameTimeText()).toBe(formatUnitTime(frameMtime))
    expect(frameTimeField().getAttribute('data-empty')).not.toBe('true')
  })

  it('a distinct mtime and message timestamp: the field reads the mtime, not the timestamp', async () => {
    const { client } = await mountMirror()
    const frameMtime = 1_000_000
    const messageTimestamp = 2_000_000
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, frameMtime, messageTimestamp))
    await settle()
    expect(frameTimeText()).toBe(formatUnitTime(frameMtime))
    expect(frameTimeText()).not.toBe(formatUnitTime(messageTimestamp))
  })
})

// =============================================================================
// 10. Control copy and the image's accessible name, pinned by equality
//     against SPEC 4.5.2.6's own tables.
// =============================================================================

describe('control copy and the accessible name, pinned by equality against SPEC 4.5.2.6\'s table', () => {
  it('the refresh control reads "Refresh"', async () => {
    await mountMirror()
    expect(refreshControl().textContent).toBe('Refresh')
  })

  it('the auto control reads "Refresh automatically" when off and "Refreshing automatically" when on', async () => {
    await mountMirror()
    expect(autoControl().textContent).toBe('Refresh automatically')
    await click(autoControl())
    expect(autoControl().textContent).toBe('Refreshing automatically')
  })

  it("the image's accessible name is \"The unit's display\", naming the object rather than what it shows", async () => {
    const { client } = await mountMirror()
    client.settle(GET_SCREEN_TYPE, screenImageEnvelope(VALID_PNG_B64, T - 5))
    await settle()
    const img = frameImage()
    expect(img).not.toBeNull()
    expect(accessibleName(img as HTMLImageElement)).toBe("The unit's display")
  })
})

// =============================================================================
// 11. Wire conformance of the fixture builder itself, and the view root.
// =============================================================================

describe('wire conformance and the view root', () => {
  it('the screenImageEnvelope fixture conforms to outgoing/screen_image.json', () => {
    assertConformsToSchema(
      screenImageEnvelope(VALID_PNG_B64, T - 5) as unknown as Record<string, unknown>,
      SCREEN_IMAGE_SCHEMA,
    )
  })

  it('the view root carries data-view="mirror"', async () => {
    await mountMirror()
    expect(root().getAttribute('data-view')).toBe('mirror')
  })
})
