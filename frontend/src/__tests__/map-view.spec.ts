import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import getHandshakesSchema from '../../../docs/schemas/incoming/get_handshakes.json'

import type {
  Gps,
  HandshakeEntry,
  HandshakeGps,
  OutgoingGpsUpdate,
  OutgoingHandshakesList,
  OutgoingMessage,
} from '../lib/protocol'
import type { ConnectionState, Diagnostics, UnauthorizedReason, WsClient, WsClientOptions } from '../lib/ws'

// Written from SPEC 4.5.2.7 ("The Map, the one external origin, and a
// viewport that has to survive", issue #198), plus 4.5.2.3/4.5.2.4 (the
// refresh rule this section claims a third time rather than restating), 4.4.1
// (the handshakes_list and gps stores this view reads), 4.5.1 (the geometry
// rule asserted in tests/e2e/geometry.spec.ts, not here), 2.7/2.8 and 10.7.
//
// Deliberately not read: views/Map.svelte, lib/map.ts, or the parts of
// lib/stores.ts/lib/format.ts this change adds - all four exist in this
// checkout as the implementer's parallel work by the time this file was
// finished, and were left unopened. The two pre-existing lib/format.ts
// strings this file quotes below (the shared not-connected sentence and the
// existing Captured-segment one) were confirmed against 4.5.2.4/4.5.2.3's own
// precedent and the section text itself, not by reading the function that
// selects between them.
//
// Every element is reached through the DOM hooks 4.5.2.7's own "hooks and the
// copy" paragraph declares: data-view, data-map-surface, data-empty-message,
// and nothing narrower - deliberately not Leaflet's own class names
// (.leaflet-marker-icon and friends), which the section itself calls "a
// library's private vocabulary" that a test must not depend on. Every copy
// assertion is a literal transcribed from that paragraph's table and from its
// own caption example, never a string imported from lib/format.ts.
//
// The client is reached through lib/session.ts's startSession(), the same
// seam peers-view.spec.ts and wifi-view.spec.ts use.

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only - coordinates in
// the open ocean, matching wifi-view.spec.ts's own gpsReading() (10.1,
// -30.2) and its Captured-row gps fixture (10.5, -20.25).
// ---------------------------------------------------------------------------

const T = 1_700_000_000

function handshakeGps(overrides: Partial<HandshakeGps> = {}): HandshakeGps {
  return { lat: 10.5, lon: -20.25, accuracy: 12, source: 'gpsd', ...overrides }
}

let bssidCounter = 0
function handshakeEntry(overrides: Partial<HandshakeEntry> = {}): HandshakeEntry {
  bssidCounter += 1
  const bssid = `aabbccddee${String(bssidCounter % 10)}${String(Math.floor(bssidCounter / 10) % 10)}`
  return {
    filename: `TestNet_${bssid}.pcapng`,
    ssid: 'TestNet_001',
    bssid,
    mtime: T,
    size: 4096,
    gps: null,
    ...overrides,
  }
}

function handshakesListEnvelope(
  entries: HandshakeEntry[],
  truncated = false,
  total: number | null = entries.length,
): OutgoingHandshakesList {
  return { type: 'handshakes_list', timestamp: T, data: { entries, truncated, total } }
}

function gpsFix(overrides: Partial<Gps> = {}): Gps {
  return {
    enabled: true,
    source: 'gpsd',
    piFix: true,
    fix: true,
    lat: 10.1,
    lon: -30.2,
    altitude: 5,
    accuracy: 8,
    updated: T,
    ...overrides,
  }
}

function gpsUpdateEnvelope(overrides: Partial<Gps> = {}): OutgoingGpsUpdate {
  return { type: 'gps_update', timestamp: T, data: gpsFix(overrides) }
}

// A hostile SSID, the same shape peers-view.spec.ts's HOSTILE_STRINGS uses for
// a remote string chosen by a stranger's unit. SPEC 4.5.2.7's own "a pin's
// popup is a string from the air" paragraph names HandshakeEntry.ssid
// specifically as the one place in this view a remote string reaches DOM
// through Leaflet rather than through Svelte's own escaping.
const HOSTILE_SSID = '<img src=x onerror="window.__mapViewPwned = true">'

// ---------------------------------------------------------------------------
// Coordinate-extraction helper for the Leaflet call recorders below. A
// LatLng-like argument arrives in one of Leaflet's own accepted shapes -
// [lat, lng], {lat, lng}, or an L.LatLng instance, which also exposes .lat
// and .lng as own properties - so this does not commit to which shape the
// implementation happens to pass.
// ---------------------------------------------------------------------------

interface LatLngLike {
  lat: number
  lng: number
}

function extractLatLng(value: unknown): LatLngLike | null {
  if (Array.isArray(value) && value.length >= 2) {
    return { lat: Number(value[0]), lng: Number(value[1]) }
  }
  if (value !== null && typeof value === 'object' && 'lat' in value && 'lng' in value) {
    const candidate = value as { lat: unknown; lng: unknown }
    return { lat: Number(candidate.lat), lng: Number(candidate.lng) }
  }
  return null
}

function closeTo(actual: number, expected: number, epsilon = 0.001): boolean {
  return Math.abs(actual - expected) <= epsilon
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
// A hand-driven WsClient double, peers-view.spec.ts's and wifi-view.spec.ts's
// shape: request() calls are recorded by type and settled individually.
// ---------------------------------------------------------------------------

interface PendingRequest {
  type: string
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
}

class FakeWsClient {
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
  request(type: string): Promise<OutgoingMessage> {
    this.requestCalls.push(type)
    return new Promise((resolve, reject) => {
      this.pending.push({ type, resolve, reject })
    })
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(new Error('the map view must never send a command, only a read'))
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
    const entry = this.pending.splice(index, 1)[0] as PendingRequest
    this.emitMessage(reply)
    entry.resolve(reply)
  }

  countRequestCalls(type: string): number {
    return this.requestCalls.filter((entry) => entry === type).length
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake as unknown as WsClient
}

// ---------------------------------------------------------------------------
// Mounting: Map.svelte via a real session (startSession), peers-view.spec.ts's
// pattern.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsModule = typeof import('../lib/settings')
type SessionModule = typeof import('../lib/session')
type RouterModule = typeof import('../lib/router')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

// SPEC 4.5.2.7: "the current location marker is the unit's location, not the
// phone's" - lib/geo.ts is documented as the only module in this app that
// calls into navigator.geolocation, so a Map view that reached past the gps
// store and called the browser API itself would be the exact confusion that
// paragraph warns against. Stubbed on every navigator so an unstubbed call
// is observed rather than throwing (real jsdom has no navigator.geolocation
// at all).
let getCurrentPositionSpy: ReturnType<typeof vi.fn>
let watchPositionSpy: ReturnType<typeof vi.fn>
let clearWatchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  window.history.pushState({}, '', '/')
  vi.resetModules()
  bssidCounter = 0

  getCurrentPositionSpy = vi.fn()
  watchPositionSpy = vi.fn()
  clearWatchSpy = vi.fn()
  Object.defineProperty(window.navigator, 'geolocation', {
    value: {
      getCurrentPosition: getCurrentPositionSpy,
      watchPosition: watchPositionSpy,
      clearWatch: clearWatchSpy,
    },
    configurable: true,
  })
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
  // @ts-expect-error - removing the per-test stub, not a real browser property
  delete window.navigator.geolocation
})

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

/**
 * Recorders for the observable behaviour Leaflet itself owns, patched onto
 * the class prototypes rather than the module's exported factory functions:
 * `L.marker(...)`, `new L.Marker(...)` and `L.circleMarker(...)` all end up
 * calling `Layer.prototype.addTo`/`bindPopup`, and `L.Map.prototype.setView`/
 * `invalidateSize` are Leaflet's own stable, documented public methods -
 * unlike a CSS class name, not "a library's private vocabulary" (SPEC
 * 4.5.2.7's own phrase), so pinning a test against them does not commit to
 * which factory function or icon choice the implementation made. Each spy
 * replaces the real implementation (mockImplementation, never calling
 * through) so it also never asks a real Leaflet map to lay itself out inside
 * jsdom, which is the one thing about this file this author could not verify
 * by running it - see the end-of-task report.
 *
 * A coupling worth naming rather than leaving implicit: these four methods
 * are the current *means* the implementation happens to use, not the rule
 * being pinned. The rules are "the map centres on the unit's position when
 * one is available, and on the first located capture otherwise" and "a
 * pin's popup content is an element with its text assigned, never a string
 * handed to Leaflet" - SPEC 4.5.2.7's own two sentences. Confirmed against
 * the implementation (not read by this author, but checked by the
 * coordinator on this file's behalf): centring goes through
 * `instance.setView(latlng, FOCUSED_ZOOM)` for both the unit-position and
 * first-capture paths, and pins go through
 * `L.marker([lat, lon]).bindPopup(content).addTo(layer)`. If a later change
 * moves centring to `flyTo`/`panTo` or popups to `bindTooltip`, the correct
 * fix is to move the spy to the new entry point, not to relax what it
 * asserts - the rule above is what SPEC 4.5.2.7 actually requires, and
 * `setView`/`bindPopup` are only where this test suite currently has to
 * stand to observe it.
 */
async function setupLeafletSpies(): Promise<{
  addToCalls: LatLngLike[]
  bindPopupCalls: unknown[]
  setViewCalls: LatLngLike[]
  invalidateSizeCallCount: () => number
}> {
  const L = await import('leaflet')
  const addToCalls: LatLngLike[] = []
  const bindPopupCalls: unknown[] = []
  const setViewCalls: LatLngLike[] = []
  let invalidateSizeCalls = 0

  // `this: any` throughout: the exact class typing (L.Layer/L.Map) could not
  // be checked against a real @types/leaflet on this host - see the
  // end-of-task report - and an explicit `any` here is a deliberate,
  // narrow opt-out of that check rather than a load-bearing assumption
  // about Leaflet's own types.
  vi.spyOn(L.Layer.prototype, 'addTo').mockImplementation(function (this: any) {
    if (typeof this.getLatLng === 'function') {
      const ll = extractLatLng(this.getLatLng())
      if (ll) addToCalls.push(ll)
    }
    return this
  })

  vi.spyOn(L.Layer.prototype, 'bindPopup').mockImplementation(function (
    this: any,
    content: unknown,
  ) {
    bindPopupCalls.push(content)
    return this
  })

  vi.spyOn(L.Map.prototype, 'setView').mockImplementation(function (this: any, center: unknown) {
    const ll = extractLatLng(center)
    if (ll) setViewCalls.push(ll)
    return this
  })

  vi.spyOn(L.Map.prototype, 'invalidateSize').mockImplementation(function (this: any) {
    invalidateSizeCalls += 1
    return this
  })

  return { addToCalls, bindPopupCalls, setViewCalls, invalidateSizeCallCount: () => invalidateSizeCalls }
}

async function mountMap(
  initialState: ConnectionState = 'connected',
  route: string | null = '/map',
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{
  client: FakeWsClient
  settings: SettingsModule
  session: SessionModule
  router: RouterModule
  leaflet: Awaited<ReturnType<typeof setupLeafletSpies>>
}> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  const router = await import('../lib/router')
  const leaflet = await setupLeafletSpies()
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

  const module = (await import('../views/Map.svelte')) as unknown as {
    default: Parameters<SvelteApi['mount']>[0]
  }
  const mountTarget = document.createElement('div')
  document.body.appendChild(mountTarget)
  container = mountTarget
  instance = svelte.mount(module.default, { target: mountTarget }) as Record<string, unknown>
  await settle()
  return { client, settings, session, router, leaflet }
}

function root(): HTMLElement {
  if (!container) throw new Error('Map view is not mounted')
  const viewRoot = container.querySelector('[data-view="map"]')
  expect(viewRoot, 'expected [data-view="map"] inside the mount container').not.toBeNull()
  return viewRoot as HTMLElement
}

/** SPEC 4.5.2.7's own hooks paragraph: "The three states... appear in a [data-empty-message] element". */
function emptyMessage(): HTMLElement | null {
  return root().querySelector('[data-empty-message]')
}

// The three literal sentences from 4.5.2.7's own table. The first two are
// lib/format.ts's pre-existing strings, "taken unchanged" (the section's own
// words); the third is new to this section. Hardcoded here rather than
// imported from lib/format.ts, matching peers-view.spec.ts's and
// wifi-view.spec.ts's own stated convention: a copy assertion is a literal
// transcribed from the spec's table, never a string shared with the code
// under test.
const NOT_CONNECTED_MESSAGE = 'Not connected, so this list has not been read.'
const NO_CAPTURES_MESSAGE = 'The unit has no captures.'
const NO_POSITION_MESSAGE = "None of the unit's captures has a position."

// =============================================================================
// 1. The refresh: three occasions, no timer - claimed a third time from SPEC
//    4.5.2.3 the way 4.5.2.4 claims it, pinned here against get_handshakes.
// =============================================================================

describe('the refresh: route becoming /map, a host switch in place, and no timer', () => {
  it('mounting the view issues exactly one get_handshakes request', async () => {
    const { client } = await mountMap()
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it("the request type matches docs/schemas' own \"type\" const, not a hand-typed literal", async () => {
    const { client } = await mountMap()
    const requestType = (getHandshakesSchema as { properties: { type: { const: string } } })
      .properties.type.const
    expect(client.requestCalls).toContain(requestType)
  })

  it('mounting while the route is not /map issues nothing; navigating there asks', async () => {
    const { client, router } = await mountMap('connected', null)
    expect(client.countRequestCalls('get_handshakes')).toBe(0)
    router.navigate('/map')
    await settle()
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('leaving /map does not itself ask for anything', async () => {
    const { client, router } = await mountMap()
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
    router.navigate('/log')
    await settle()
    expect(client.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('returning to /map on the same still-mounted view asks again', async () => {
    const { client, router } = await mountMap()
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 0))
    router.navigate('/log')
    await settle()
    router.navigate('/map')
    await settle()
    expect(client.countRequestCalls('get_handshakes')).toBe(2)
  })

  it('a host switch in place - the view still mounted, the route still /map - asks the new unit for its own list', async () => {
    const created: FakeWsClient[] = []
    const { client: clientA, settings } = await mountMap('connected', '/map', (c) => created.push(c))
    expect(created).toHaveLength(1)
    expect(clientA.countRequestCalls('get_handshakes')).toBe(1)

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
    expect(clientB.countRequestCalls('get_handshakes')).toBe(1)
  })

  it('no timer: waiting does not by itself produce another request', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountMap()
      expect(client.countRequestCalls('get_handshakes')).toBe(1)
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
      expect(client.countRequestCalls('get_handshakes')).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 2. Three empty states, not two (SPEC 4.5.2.7), each pinned against the
//    literal sentence its own table gives: no reply yet (decided by
//    connection state, the same hasListDataArrived mechanism 4.5.2.3 already
//    argued out - not by whether a reply has literally arrived), a reply with
//    zero captures, and a reply whose captures carry no position. The third
//    is the one a screen collapsing into "nothing captured" would get wrong.
// =============================================================================

describe('three empty states, not two (SPEC 4.5.2.7)', () => {
  it('not connected: "Not connected, so this list has not been read." - the shared sentence, unchanged', async () => {
    await mountMap('offline')
    expect(emptyMessage()?.textContent).toBe(NOT_CONNECTED_MESSAGE)
  })

  it('connecting (not yet connected) reads the same not-connected sentence', async () => {
    await mountMap('connecting')
    expect(emptyMessage()?.textContent).toBe(NOT_CONNECTED_MESSAGE)
  })

  it('connected, a reply with zero captures: "The unit has no captures."', async () => {
    const { client } = await mountMap('connected')
    client.settle('get_handshakes', handshakesListEnvelope([]))
    await settle()
    expect(emptyMessage()?.textContent).toBe(NO_CAPTURES_MESSAGE)
  })

  it("connected, a reply whose captures all carry gps: null: \"None of the unit's captures has a position.\"", async () => {
    const { client } = await mountMap('connected')
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: null }), handshakeEntry({ gps: null })]),
    )
    await settle()
    expect(emptyMessage()?.textContent).toBe(NO_POSITION_MESSAGE)
  })

  it('the three sentences are pairwise distinct', () => {
    expect(NOT_CONNECTED_MESSAGE).not.toBe(NO_CAPTURES_MESSAGE)
    expect(NO_CAPTURES_MESSAGE).not.toBe(NO_POSITION_MESSAGE)
    expect(NOT_CONNECTED_MESSAGE).not.toBe(NO_POSITION_MESSAGE)
  })

  it('the empty message clears once at least one capture carries a usable position', async () => {
    const { client } = await mountMap('connected')
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: null }), handshakeEntry({ gps: handshakeGps() })]),
    )
    await settle()
    expect(emptyMessage()).toBeNull()
  })
})

// =============================================================================
// 3. A capture is plotted only when gps is non-null and both coordinates are
//    finite (issue #109: lib/stores.ts writes the wire payload with no
//    runtime validation). Observable through the caption SPEC 4.5.2.7 gives
//    an exact example for ("12 captures, 3 with a position."), rather than
//    through Leaflet's own marker DOM - the section is explicit that the
//    caption exists precisely so this rule does not need a test to count
//    Leaflet markers.
// =============================================================================

describe('the caption counts only finite, non-null positions (issue #109)', () => {
  it('the section\'s own worked example: 12 captures, 3 with a position', async () => {
    const { client } = await mountMap('connected')
    const entries = [
      ...Array.from({ length: 3 }, () => handshakeEntry({ gps: handshakeGps() })),
      ...Array.from({ length: 4 }, () => handshakeEntry({ gps: null })),
      handshakeEntry({ gps: handshakeGps({ lat: Number.NaN }) }),
      handshakeEntry({ gps: handshakeGps({ lat: Number.NaN }) }),
      handshakeEntry({ gps: handshakeGps({ lon: Number.POSITIVE_INFINITY }) }),
      handshakeEntry({ gps: handshakeGps({ lon: Number.NEGATIVE_INFINITY }) }),
      // A string that satisfies the schema's "type: number" in name only -
      // the schema cannot see this at runtime once the value is already
      // handed to the client as JS (issue #109).
      handshakeEntry({ gps: handshakeGps({ lat: '10.5' as unknown as number }) }),
    ]
    expect(entries).toHaveLength(12)
    client.settle('get_handshakes', handshakesListEnvelope(entries))
    await settle()
    expect(root().textContent).toContain('12 captures, 3 with a position.')
  })

  it('a caption of zero-with-position still names the total, not just the sentence above it', async () => {
    const { client } = await mountMap('connected')
    const entries = [
      handshakeEntry({ gps: null }),
      handshakeEntry({ gps: handshakeGps({ lat: Number.NaN }) }),
    ]
    client.settle('get_handshakes', handshakesListEnvelope(entries))
    await settle()
    expect(root().textContent).toContain('2 captures, 0 with a position.')
  })
})

// =============================================================================
// 3a. SPEC 4.5.2.7's own paragraph for issue #153: total:null (no directory
//     to enumerate - a manual-mode unit) must render differently from
//     total:0 (a directory that was read and found empty), and the caption's
//     first clause "must not count" on a null. SPEC pins the literal caption
//     for the null case directly ("Capture count unavailable, 0 with a
//     position."), and says why §4.5.1.1's dash convention does not transfer:
//     that section renders an unknown value as a dash in a cell, where the
//     dash sits where a number would, and a caption is a sentence rather than
//     a cell - "- captures, 0 with a position" is not one. The caption reuses
//     the word "unavailable" that §4.5.1.1 already gives the screen reader,
//     rather than inventing a second word for the same idea.
// =============================================================================

describe('the caption tells a null total (no directory) apart from a real zero (issue #153)', () => {
  it('total:null with no entries does not render as "0 captures" - the app was never told a directory, it did not look and find nothing', async () => {
    const { client } = await mountMap('connected')
    client.settle('get_handshakes', handshakesListEnvelope([], false, null))
    await settle()
    expect(root().textContent).not.toContain('0 captures')
  })

  it('total:null and total:0 render different captions for the same (empty) entry list', async () => {
    const { client: nullClient } = await mountMap('connected')
    nullClient.settle('get_handshakes', handshakesListEnvelope([], false, null))
    await settle()
    const nullCaption = root().textContent

    // The second mount needs the first session released, not just its markup
    // removed: startSession refuses a second active session, and clearing the
    // body leaves the first one running. The afterEach hook cannot help here,
    // because both mounts happen inside one test.
    if (stopSession) {
      stopSession()
      stopSession = null
    }
    document.body.innerHTML = ''
    const { client: zeroClient } = await mountMap('connected')
    zeroClient.settle('get_handshakes', handshakesListEnvelope([], false, 0))
    await settle()
    const zeroCaption = root().textContent

    expect(zeroCaption).toContain('0 captures, 0 with a position.')
    expect(nullCaption).not.toBe(zeroCaption)
  })

  it('total:null renders the pinned caption: "Capture count unavailable, 0 with a position."', async () => {
    const { client } = await mountMap('connected')
    client.settle('get_handshakes', handshakesListEnvelope([], false, null))
    await settle()
    expect(root().textContent).toContain('Capture count unavailable, 0 with a position.')
  })

  it('the second clause - how many carry a position - stays a real count even when total is null (SPEC: "that one is true either way")', async () => {
    const { client } = await mountMap('connected')
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: handshakeGps() }), handshakeEntry({ gps: null })], false, null),
    )
    await settle()
    expect(root().textContent).toContain('1 with a position.')
  })
})

// =============================================================================
// 4. The current-location marker is the unit's location (the gps store),
//    never the browser's own (lib/geo.ts). Two separate claims, both pinned:
//    the marker actually exists at the unit's coordinates once the gps store
//    carries them (the half a review of this file found missing - a
//    describe naming this rule with no assertion of a plotted marker stays
//    green even if the whole unit-marker effect is deleted), and the source
//    of that position is never the browser's own navigator.geolocation,
//    which only lib/geo.ts is permitted to call.
// =============================================================================

describe('the current-location marker comes from the gps store, never from the browser directly', () => {
  it('a gps_update with a fix produces a plotted layer at the unit\'s coordinates', async () => {
    const { client, leaflet } = await mountMap()
    client.emitMessage(gpsUpdateEnvelope({ source: 'gpsd', piFix: true, fix: true, lat: 10.1, lon: -30.2 }))
    await settle()
    const plotted = leaflet.addToCalls.some(
      (ll) => closeTo(ll.lat, 10.1) && closeTo(ll.lng, -30.2),
    )
    expect(
      plotted,
      `expected a plotted layer near (10.1, -30.2); recorded addTo() calls: ${JSON.stringify(leaflet.addToCalls)}`,
    ).toBe(true)
  })

  it('a browser-sourced fix relayed through the gps store is plotted the same way', async () => {
    const { client, leaflet } = await mountMap()
    client.emitMessage(gpsUpdateEnvelope({ source: 'browser', piFix: false, fix: true, lat: 10.1, lon: -30.2 }))
    await settle()
    const plotted = leaflet.addToCalls.some((ll) => closeTo(ll.lat, 10.1) && closeTo(ll.lng, -30.2))
    expect(plotted).toBe(true)
  })

  it('mounting the view, and receiving a gps_update, never calls navigator.geolocation', async () => {
    const { client } = await mountMap()
    client.emitMessage(gpsUpdateEnvelope({ source: 'browser', piFix: false, fix: true }))
    await settle()
    expect(getCurrentPositionSpy).not.toHaveBeenCalled()
    expect(watchPositionSpy).not.toHaveBeenCalled()
  })

  it('a gps store with no fix at all does not throw while captures are present', async () => {
    const { client } = await mountMap()
    client.emitMessage(
      gpsUpdateEnvelope({ enabled: false, piFix: false, fix: false, lat: null, lon: null, source: null }),
    )
    client.settle('get_handshakes', handshakesListEnvelope([handshakeEntry({ gps: handshakeGps() })]))
    await expect(settle()).resolves.toBeUndefined()
  })
})

// =============================================================================
// 6. A pin's popup is a string from the air, and Leaflet will treat it as
//    markup (SPEC 4.5.2.7): bindPopup given a string parses it as HTML, and
//    HandshakeEntry.ssid is chosen by whoever named the access point. The
//    popup content must arrive at bindPopup as an element with its text
//    assigned, never as a raw string. This is the §4.5.3 rule reaching the
//    one place in this view Svelte's own escaping is not in the path.
// =============================================================================

describe("a pin's popup content is an element with its text assigned, never a string handed to Leaflet (SPEC 4.5.3 via 4.5.2.7)", () => {
  beforeEach(() => {
    ;(window as unknown as { __mapViewPwned?: boolean }).__mapViewPwned = undefined
  })

  it('a hostile SSID reaches bindPopup as an element whose text is the hostile string, not as a string Leaflet would parse', async () => {
    const { client, leaflet } = await mountMap('connected')
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ ssid: HOSTILE_SSID, gps: handshakeGps() })]),
    )
    await settle()

    expect(leaflet.bindPopupCalls.length, 'expected at least one bindPopup() call').toBeGreaterThan(0)
    const sawEscaped = leaflet.bindPopupCalls.some((content) => {
      if (typeof content === 'string') return false
      const node = content as { nodeType?: number; textContent?: string | null }
      return node.nodeType === 1 && node.textContent === HOSTILE_SSID
    })
    expect(
      sawEscaped,
      `expected one bindPopup() call to receive an element with textContent === the hostile SSID; ` +
        `received: ${JSON.stringify(leaflet.bindPopupCalls.map((c) => (typeof c === 'string' ? c : typeof c)))}`,
    ).toBe(true)

    // Defence in depth, the same pattern peers-view.spec.ts's hostile-string
    // block uses: even though bindPopup was intercepted above and never ran
    // Leaflet's real HTML parsing, nothing else on the page may have turned
    // the string into markup either.
    expect(document.querySelector('script')).toBeNull()
    expect(document.querySelector('[onerror]')).toBeNull()
    expect((window as unknown as { __mapViewPwned?: boolean }).__mapViewPwned).toBeUndefined()
  })
})

// =============================================================================
// 7. Becoming current has to tell Leaflet to measure again (SPEC 4.5.2.7):
//    the map is created once and kept mounted (SPEC 4.5), so it is sized
//    while hidden at least once, where a hidden element measures zero. The
//    section names this as the one failure that is silent and only visible
//    after a navigation - pinned here as a strictly-increasing call count
//    across a navigation onto /map, not an exact count, since nothing in the
//    section commits to how many times invalidateSize runs before that.
// =============================================================================

describe('becoming current calls invalidateSize again (SPEC 4.5.2.7)', () => {
  it('navigating from /log to /map calls invalidateSize at least once more than before the navigation', async () => {
    const { leaflet, router } = await mountMap('connected', '/log')
    const before = leaflet.invalidateSizeCallCount()
    router.navigate('/map')
    await settle()
    expect(leaflet.invalidateSizeCallCount()).toBeGreaterThan(before)
  })
})

// =============================================================================
// 8. Three behaviours SPEC 4.5.2.7 decides beyond the plotting/refresh rules.
// =============================================================================

describe('an empty sentence never appears over a map that is still showing pins (SPEC 4.5.2.7)', () => {
  it('losing the connection after captures were plotted does not bring back an empty message', async () => {
    const { client } = await mountMap('connected')
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: handshakeGps() })]),
    )
    await settle()
    expect(emptyMessage(), 'expected no empty message once a capture is plotted').toBeNull()

    client.emitState('offline')
    await settle()
    expect(
      emptyMessage(),
      'a disconnect must not paint a contradicting "not connected" sentence over pins still on screen',
    ).toBeNull()
  })
})

describe('the caption counts what the unit has, not what arrived (SPEC 4.5.2.7)', () => {
  it('a truncated reply\'s caption uses total, not the length of the returned entries', async () => {
    const { client } = await mountMap('connected')
    const entries = [handshakeEntry({ gps: handshakeGps() }), handshakeEntry({ gps: handshakeGps() })]
    client.settle('get_handshakes', handshakesListEnvelope(entries, true, 500))
    await settle()
    expect(root().textContent).toContain('500 captures,')
    expect(root().textContent).not.toContain('2 captures,')
  })
})

describe('which source centres the map is decided by availability, not by which arrived first (SPEC 4.5.2.7)', () => {
  it("the unit's own position still centres the map even when a located capture arrived first", async () => {
    const { client, leaflet } = await mountMap('connected')
    // handshakes_list beats the first gps_update - the exact race the
    // section names as the failure a naive "first data wins" effect has.
    client.settle(
      'get_handshakes',
      handshakesListEnvelope([handshakeEntry({ gps: handshakeGps({ lat: 5, lon: 5 }) })]),
    )
    await settle()
    client.emitMessage(gpsUpdateEnvelope({ source: 'gpsd', piFix: true, fix: true, lat: 10.1, lon: -30.2 }))
    await settle()

    const centredOnUnit = leaflet.setViewCalls.some(
      (ll) => closeTo(ll.lat, 10.1) && closeTo(ll.lng, -30.2),
    )
    expect(
      centredOnUnit,
      `expected a setView() call centred on the unit's own position (10.1, -30.2) even though the ` +
        `capture arrived first; recorded setView() calls: ${JSON.stringify(leaflet.setViewCalls)}`,
    ).toBe(true)
  })
})

// =============================================================================
// 5. The view root and the map surface hook, SPEC 4.5.2.7's own "hooks and
//    the copy" paragraph: data-view="map" like every other view, and
//    data-map-surface on the map's own element - deliberately not any
//    Leaflet class name, "a library's private vocabulary".
// =============================================================================

describe('the view root and the map surface hook', () => {
  it('carries data-view="map"', async () => {
    await mountMap()
    expect(root().getAttribute('data-view')).toBe('map')
  })

  it('the map surface (data-map-surface) is present once the view is mounted', async () => {
    await mountMap()
    expect(root().querySelector('[data-map-surface]')).not.toBeNull()
  })
})
