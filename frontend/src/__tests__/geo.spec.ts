import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import gpsDataSchema from '../../../docs/schemas/incoming/gps_data.json'

import { formatGeoStateMessage, formatGeoToggleLabel } from '../lib/format'
import type { GeoState } from '../lib/geo'
import type {
  Capabilities,
  Gps,
  OutgoingMessage,
  OutgoingStats,
} from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  UnauthorizedReason,
  WsClient,
  WsClientOptions,
} from '../lib/ws'

// Written from SPEC.md 4.6 and 4.6.2 ("Acquiring the position: the tap, the
// third timer, and what backgrounding takes away", issue #175), which is
// the section that decides everything this file pins: the tap-only
// acquisition rule (G1); that navigator.permissions is never called at all
// -- amended after this file's first pass, which had wrongly pinned an
// earlier draft's "read after the watch has started" sentence, since G2
// alone already rules it out as a source of the denied state and the copy
// table has no sentence that turns on its answer; that watchPosition is
// torn down on hide and rebuilt on show, unconditionally, so a
// visibilitychange firing without an actual transition cannot leave two
// watches live (G3); that which property says the document is hidden is
// deliberately not a rule SPEC 4.6.2 states -- `document.hidden` is
// `visibilityState !== 'visible'` by definition, so the two cannot
// disagree in a browser, and that is exactly why this file's own
// setVisibility() fixture sets both rather than the section naming one (an
// earlier version of this file set only `visibilityState`, which is how
// the implementation reading `document.hidden` went unnoticed until a
// mutant exposed it); that GeolocationPositionError.code === 1 and only 1
// is row 3 of 4.6.1, and reaching it tears the watch and the timer down
// rather than leaving them running behind a state that says the browser
// refused (G4); the five client-local states and their sentences (4.6.2's
// own table); the 5 s cadence, derived in 4.6 from incoming/gps_data.json's
// 10 s TTL and repeated in 4.6.2 as "not a third exception"; the piFix gate
// read from the `gps` store at push time, not latched at the tap; and
// 4.3.3's push-is-discarded-not-replayed rule as it applies to sendGps.
//
// Also read: 4.3 (WsClient's onState/onMessage/state/lastStats/sendGps
// surface), 4.3.3 (gps_data discarded rather than queued -- lib/ws.ts's
// sendGps already does this; this file is not re-proving ws.spec.ts's own
// coverage of that rule, only that lib/geo.ts does not duplicate the gate
// itself), 4.4.1 (the `gps` store: written by gps_update and by the `gps`
// field of `stats`, whichever arrived later -- so this file drives piFix
// through a `stats` push exactly like every other store-reading view's
// tests do), 4.7/4.8 (Settings, lib/session.ts's currentClient() -- the
// "current one" 4.6.2 says this module asks for at each push, already
// exported before this issue), and 10.7. Not 4.5.1: an earlier version of
// this comment cited it for "a control is labelled by its state", which
// SPEC 4.6.2 itself now says is a precedent in this codebase (the Log's
// follow control, the Mirror's auto-refresh control) rather than a rule
// 4.5.1 states -- the same fabricated citation the specification caught
// itself making twice, corrected here rather than repeated a third time.
//
// Deliberately not read: lib/geo.ts, or the geolocation parts of
// views/Settings.svelte. Only the DOM hooks 4.6.2 declares are used
// (data-geo-toggle on the control, data-geo-state on its section, carrying
// the state name), plus navigator.geolocation/navigator.permissions, which
// this module is declared to be the only caller of in this app.
//
// A genuine scope question, resolved against the issue text rather than
// guessed: the issue that opened #175 originally described lib/geo.ts as
// holding "the four states 4.6.1 decides", but 4.6.2 -- written for this
// change -- explicitly narrows that: "The states this module holds are
// client-local, and they are not 4.6.1's four... The two meet at one point
// only -- row 3, permission denied... and this module is where that fact
// comes from." The issue itself also states plainly that "anything about
// how a browser position is drawn differently from a Pi fix... is #34's
// presentation half" and is out of scope here. So this file does not
// import or guess the name of a combined four-row resolver: it pins the
// fact 4.6.2 says this module supplies (denied is a fact about the
// browser, independent of piFix) rather than a composition function whose
// home SPEC does not name and the task's own reading restriction forbids
// discovering by inspection.

// ---------------------------------------------------------------------------
// Schema conformance, ajv-free (matching every other spec file's own
// precedent): the schema JSON imported directly, checked for
// additionalProperties, required and the literal "type".
// ---------------------------------------------------------------------------

interface JsonSchemaLike {
  title: string
  properties: Record<
    string,
    { const?: string; enum?: unknown[]; type?: string | string[] }
  >
  required: string[]
}

const GPS_DATA_SCHEMA = gpsDataSchema as JsonSchemaLike

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
// A hand-driven WsClient double, log-view.spec.ts's shape, extended with
// sendGps recording -- the one method that file never needed to inspect.
// sendGps here never gates on connection state itself: real lib/ws.ts's
// sendGps is what discards a push while the socket is not usable (SPEC
// 4.3.3), already exercised in ws.spec.ts, and this fake recording every
// call unconditionally is what lets a test here tell "lib/geo.ts skipped
// the push itself" apart from "lib/geo.ts called sendGps and ws.ts
// discarded it" -- the delegation 4.6.2 states explicitly.
// ---------------------------------------------------------------------------

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'connected'
  reasonValue: UnauthorizedReason | null = null
  readonly stateHandlers = new Set<(state: ConnectionState) => void>()
  readonly messageHandlers = new Set<(message: OutgoingMessage) => void>()
  readonly sendGpsCalls: Array<Record<string, unknown>> = []

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
  request(): Promise<OutgoingMessage> {
    return new Promise(() => {})
  }
  command(): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error('the geolocation module must never send a command'),
    )
  }
  sendGps(data: Record<string, unknown>): void {
    this.sendGpsCalls.push(data)
  }
  diagnostics(): Diagnostics {
    return { lastError: null, latencyMs: null }
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
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

// ---------------------------------------------------------------------------
// stats/gps fixtures. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

function gpsReading(overrides: Partial<Gps> = {}): Gps {
  return {
    enabled: false,
    source: null,
    piFix: false,
    fix: false,
    lat: null,
    lon: null,
    altitude: null,
    accuracy: null,
    updated: null,
    ...overrides,
  }
}

function capabilitiesData(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    pasv: true,
    pisugar: false,
    gpsSource: 'auto',
    pluginVersion: '0.1.0',
    ...overrides,
  }
}

function statsEnvelope(
  gps: Gps,
  capabilities: Capabilities = capabilitiesData(),
): OutgoingStats {
  return {
    type: 'stats',
    timestamp: 1_700_000_000,
    data: {
      uptime: 1,
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
      gps,
      sessionAge: 1,
      capabilities,
    },
  }
}

// A unit whose on-Pi source has a fix, so a browser push while this stands
// would be exactly the case 4.6 forbids.
function piFixStats(): OutgoingStats {
  return statsEnvelope(
    gpsReading({
      enabled: true,
      source: 'gpsd',
      piFix: true,
      fix: true,
      lat: 10.1,
      lon: -30.2,
      updated: 1_700_000_000,
    }),
  )
}

// The ordinary "nothing on the Pi" case that makes the phone the source
// worth having (SPEC 4.6.1's own framing).
function noPiFixStats(): OutgoingStats {
  return statsEnvelope(
    gpsReading({ enabled: false, source: null, piFix: false, fix: false }),
  )
}

// ---------------------------------------------------------------------------
// A fake browser Geolocation API. watchPosition/clearWatch only: 4.6.2 is
// built entirely around watchPosition (G1's "watchPosition() called
// without one fails silently"), so getCurrentPosition is not stubbed. This
// file therefore makes no claim about getCurrentPosition either way -- not
// that it is unused (SPEC 4.6.2 never says so, and asserting an absence
// SPEC does not state would be inventing a rule) and not that it is used
// (nothing here calls it). If a future change reaches for it, a fixture
// that throws on the first call, rather than one that silently no-ops,
// is what turns that into a red test instead of a silent pass.
// ---------------------------------------------------------------------------

interface FakeWatcher {
  id: number
  success: PositionCallback
  error: PositionErrorCallback | null | undefined
  options: PositionOptions | undefined
}

class FakeGeolocation {
  readonly watchCalls: FakeWatcher[] = []
  readonly clearedIds: number[] = []
  private nextId = 1

  watchPosition(
    success: PositionCallback,
    error?: PositionErrorCallback | null,
    options?: PositionOptions,
  ): number {
    const id = this.nextId++
    const watcher: FakeWatcher = { id, success, error, options }
    this.watchCalls.push(watcher)
    return id
  }

  clearWatch(id: number): void {
    this.clearedIds.push(id)
  }

  getCurrentPosition(): void {
    throw new Error('unexpected getCurrentPosition call in this fixture')
  }

  /** The most recent watch that has not since been cleared, or null. */
  activeWatch(): FakeWatcher | null {
    for (let i = this.watchCalls.length - 1; i >= 0; i--) {
      const watcher = this.watchCalls[i] as FakeWatcher
      if (!this.clearedIds.includes(watcher.id)) return watcher
    }
    return null
  }

  // Coordinates passed to this method, throughout this file, are the two
  // this repository already uses -- open Atlantic (10.1, -30.2), the
  // convention dashboard.spec.ts, dashboard-controls.spec.ts, format.spec.ts,
  // stores.spec.ts and wifi-view.spec.ts all pin GPS fixtures to, varying a
  // trailing digit when a second, distinct position is needed to tell two
  // pushes apart; and the digit-sequence placeholder (12.34, 56.78), which
  // ws.spec.ts uses, for a value with non-trivial decimals. Both lists came
  // out of `grep` after the first version of this comment named the wrong
  // files for the second pair. Now recorded in SPEC 10.7 as well, so this
  // comment is the second place it lives rather than the only one. The
  // convention exists because a plausible-looking coordinate, even one
  // this coarse, narrows a real person to a real place the moment it lands
  // in a public repository -- this file pushes a position through the
  // whole module on every call, which is exactly the shape of test that
  // makes reaching for a real-looking place tempting. Reach for one of the
  // two above instead; do not invent a third "clearly fictional" one, since
  // the point of a convention is that the next reader does not have to
  // work out whether this one is.
  emitPosition(lat: number, lon: number, accuracy: number | null = 5): void {
    const watcher = this.activeWatch()
    expect(watcher, 'expected an active watchPosition() watcher').not.toBeNull()
    const position = {
      coords: {
        latitude: lat,
        longitude: lon,
        accuracy,
        altitude: null,
        altitudeAccuracy: null,
        heading: null,
        speed: null,
      },
      timestamp: Date.now(),
    } as unknown as GeolocationPosition
    watcher!.success(position)
  }

  emitError(code: 1 | 2 | 3): void {
    const watcher = this.activeWatch()
    expect(watcher, 'expected an active watchPosition() watcher').not.toBeNull()
    expect(
      watcher!.error,
      'expected an error callback to have been supplied',
    ).toBeTypeOf('function')
    const error = {
      code,
      message: 'synthetic geolocation error',
      PERMISSION_DENIED: 1,
      POSITION_UNAVAILABLE: 2,
      TIMEOUT: 3,
    } as unknown as GeolocationPositionError
    watcher!.error!(error)
  }
}

// ---------------------------------------------------------------------------
// A fake navigator.permissions.query(). SPEC G2: on iOS this can report
// "prompt" for a permission the user actually denied in system settings,
// which is exactly why 4.6.2 says its answer may never be the reason this
// app says "denied" -- only a real GeolocationPositionError.code === 1 can.
// ---------------------------------------------------------------------------

// No deferred-resolution machinery: SPEC 4.6.2's amendment means
// navigator.permissions is never called at all, so this fixture's only job
// is to let a test assert query() was not reached, and a query() that
// still had to model "never settles until told to" would be unexercised
// machinery in the one file whose own SPEC 10.7 entry argues against it.
class FakePermissions {
  queryCalls = 0
  state: PermissionState = 'prompt'

  query(): Promise<PermissionStatus> {
    this.queryCalls++
    const status = {
      state: this.state,
      onchange: null,
    } as unknown as PermissionStatus
    return Promise.resolve(status)
  }
}

// ---------------------------------------------------------------------------
// Installing/removing navigator.geolocation and navigator.permissions.
// Direct property definition, the same technique navigation.spec.ts uses
// for window.matchMedia, rather than vi.stubGlobal('navigator', ...): the
// latter would replace jsdom's whole Navigator and break everything else
// that reads it.
// ---------------------------------------------------------------------------

let geolocation: FakeGeolocation | null = null

function installGeolocation(): FakeGeolocation {
  const fake = new FakeGeolocation()
  Object.defineProperty(window.navigator, 'geolocation', {
    value: fake,
    configurable: true,
  })
  geolocation = fake
  return fake
}

function removeGeolocation(): void {
  Object.defineProperty(window.navigator, 'geolocation', {
    value: undefined,
    configurable: true,
  })
  geolocation = null
}

function installPermissions(
  state: PermissionState = 'prompt',
): FakePermissions {
  const fake = new FakePermissions()
  fake.state = state
  Object.defineProperty(window.navigator, 'permissions', {
    value: fake,
    configurable: true,
  })
  return fake
}

function removePermissions(): void {
  Object.defineProperty(window.navigator, 'permissions', {
    value: undefined,
    configurable: true,
  })
}

// ---------------------------------------------------------------------------
// document.visibilityState, read-only in jsdom by default: redefined the
// same way as the two navigator properties above.
// ---------------------------------------------------------------------------

// SPEC 4.6.2 (amended): "document.hidden is defined as visibilityState !==
// 'visible', so the two cannot disagree in a browser... A fixture that sets
// only one of them is an incomplete fixture" -- the §10.7 inventory now
// carries this as a fixture rule rather than the section naming a property.
// An earlier version of this file set only visibilityState, which is
// exactly how the implementation reading document.hidden went unnoticed
// until a mutant exposed it: both are set here so a fake can never test
// "whichever property the implementation happens to read".
function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, 'visibilityState', {
    value: state,
    configurable: true,
  })
  Object.defineProperty(document, 'hidden', {
    value: state !== 'visible',
    configurable: true,
  })
  document.dispatchEvent(new Event('visibilitychange'))
}

// ---------------------------------------------------------------------------
// Mounting: Settings.svelte via a real session (startSession), the same
// pattern log-view.spec.ts and settings-view.spec.ts each use half of.
// startSession is needed (rather than settings-view.spec.ts's direct
// connectStores) because lib/geo.ts's own push path is declared to ask
// lib/session.ts's currentClient() for "the current one" at each push, and
// that function only answers once a session is actually running.
// ---------------------------------------------------------------------------

type SvelteApi = typeof import('svelte')
type SettingsModule = typeof import('../lib/settings')
type SessionModule = typeof import('../lib/session')

let svelte: SvelteApi | null = null
let container: HTMLElement | null = null
let instance: Record<string, unknown> | null = null
let stopSession: (() => void) | null = null

// SPEC 4.6.2, G4: `GeolocationPositionError` is a real, standard global in
// every browser this app targets (the constructor `onError` reads
// `.PERMISSION_DENIED` etc. off of), and jsdom does not implement the
// Geolocation API at all, this constant included. Stubbing it is filling a
// platform gap in the fixture, the same reason navigator.geolocation and
// navigator.permissions are stubbed below -- not a concession to the
// implementation, since a real browser needs no such stub.
class FakeGeolocationPositionErrorCtor {
  static readonly PERMISSION_DENIED = 1
  static readonly POSITION_UNAVAILABLE = 2
  static readonly TIMEOUT = 3
}

beforeEach(() => {
  fakeStorage = new FakeStorage()
  vi.stubGlobal('localStorage', fakeStorage)
  vi.stubGlobal('GeolocationPositionError', FakeGeolocationPositionErrorCtor)
  installGeolocation()
  removePermissions()
  setVisibility('visible')
  vi.resetModules()
})

// lib/geo.ts is a module singleton with a document-level side effect
// (the visibilitychange listener attached while sharing is "on") that
// outlives vi.resetModules(): resetting the module registry discards the
// in-memory state of a test's module instance, but does nothing to a
// listener that instance already registered on the one document this
// whole file shares. A test that leaves sharing on without turning it off
// -- most of them do, since that is not what they are testing -- leaks
// that listener into every later test's setVisibility() calls, each one
// silently starting another watch on the shared FakeGeolocation. Relative
// assertions (toBeGreaterThan a captured baseline) never notice; an
// absolute one (toHaveLength(0), toBeNull()) does, and unpredictably, since
// the count depends on how many earlier tests in the file happened to leak
// one. There is no exported reset for this module (SPEC 4.6.2's own
// argument against an unexercised seam applies here too), so the control
// itself -- the one thing this module is driven through -- is what turns
// it off before the next test's module is even loaded.
async function turnSharingOffIfOn(): Promise<void> {
  if (!container || !svelte) return
  const toggle = container.querySelector('[data-geo-toggle]')
  if (!toggle || toggle.getAttribute('aria-pressed') !== 'true') return
  toggle.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true }),
  )
  svelte.flushSync()
  await svelte.tick()
}

afterEach(async () => {
  await turnSharingOffIfOn()
  if (instance && svelte) svelte.unmount(instance)
  instance = null
  container?.remove()
  container = null
  if (stopSession) {
    stopSession()
    stopSession = null
  }
  svelte = null
  removeGeolocation()
  removePermissions()
  setVisibility('visible')
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function settle(): Promise<void> {
  svelte?.flushSync()
  await svelte?.tick()
}

async function mountSettingsGeo(
  onClientCreated?: (client: FakeWsClient) => void,
): Promise<{
  client: FakeWsClient
  settings: SettingsModule
  session: SessionModule
  target: HTMLElement
}> {
  svelte = await import('svelte')
  const settings = await import('../lib/settings')
  const session = await import('../lib/session')
  settings.loadSettings(() => 'not-an-ip-address')

  let client!: FakeWsClient
  stopSession = session.startSession({
    createClient: (options: WsClientOptions) => {
      client = new FakeWsClient(options)
      onClientCreated?.(client)
      return asClient(client)
    },
  })

  const module = (await import('../views/Settings.svelte')) as unknown as {
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
  return { client, settings, session, target: mountTarget }
}

async function click(element: Element): Promise<void> {
  element.dispatchEvent(
    new MouseEvent('click', { bubbles: true, cancelable: true }),
  )
  await settle()
}

// ---------------------------------------------------------------------------
// Queries, on 4.6.2's own declared hooks only: data-geo-toggle on the
// control, data-geo-state on its section.
// ---------------------------------------------------------------------------

function root(): HTMLElement {
  if (!container) throw new Error('Settings is not mounted')
  const viewRoot = container.querySelector('[data-view="settings"]')
  expect(
    viewRoot,
    'expected [data-view="settings"] inside the mount container',
  ).not.toBeNull()
  return viewRoot as HTMLElement
}

function geoSection(): HTMLElement {
  const found = root().querySelector('[data-geo-state]')
  expect(found, 'expected a [data-geo-state] section').not.toBeNull()
  return found as HTMLElement
}

function geoState(): string | null {
  return geoSection().getAttribute('data-geo-state')
}

function geoToggle(): HTMLElement {
  const found = geoSection().querySelector('[data-geo-toggle]')
  expect(
    found,
    'expected [data-geo-toggle] inside the geolocation section',
  ).not.toBeNull()
  return found as HTMLElement
}

// SPEC 4.6.2: data-geo-message is on "the element holding the sentence...
// so that a test asserting the copy reads the one element that is the
// copy, rather than the section's flattened text, which also contains the
// control's own label and would pass a sentence that had gone missing as
// long as the label still matched." Every copy assertion in this file goes
// through this, never through geoSection().textContent.
function geoMessage(): HTMLElement {
  const found = geoSection().querySelector('[data-geo-message]')
  expect(
    found,
    'expected [data-geo-message] inside the geolocation section',
  ).not.toBeNull()
  return found as HTMLElement
}

// =============================================================================
// 1. Off at launch, and not persisted (SPEC 4.6.2: "Sharing is off at
//    launch, and one tap turns it on for the life of the app" / "Not
//    persisted").
// =============================================================================

describe('off at launch, and not persisted across a reload', () => {
  it('the section starts in the "off" state', async () => {
    await mountSettingsGeo()
    expect(geoState()).toBe('off')
  })

  it('the toggle starts unpressed', async () => {
    await mountSettingsGeo()
    expect(geoToggle().getAttribute('aria-pressed')).toBe('false')
  })

  it('mounting the view does not itself call watchPosition: the tap is the only caller', async () => {
    await mountSettingsGeo()
    expect(geolocation!.watchCalls).toHaveLength(0)
  })

  it('turning sharing on, then simulating a reload (a fresh module graph over the same storage), comes back off', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).not.toBe('off')

    // "Reload": tear the mount down, reset the module registry (a fresh
    // lib/geo.ts, a fresh lib/session.ts), but keep the same fakeStorage --
    // a stored preference would survive this and come back on; SPEC 4.6.2
    // says a stored true "cannot produce the gesture the next launch
    // needs", so this module must not have stored one in the first place.
    //
    // Turned off first, deliberately: this is a manual mount/unmount cycle
    // mid-test, so the afterEach cleanup below only ever sees the *second*
    // mount -- without this, the first module instance's document-level
    // visibilitychange listener would outlive the "reload" (document
    // itself is not part of what a reload discards in this fixture) and
    // leak a phantom watchPosition() call into whatever test runs next.
    await turnSharingOffIfOn()
    if (instance && svelte) svelte.unmount(instance)
    instance = null
    container?.remove()
    container = null
    stopSession?.()
    stopSession = null
    installGeolocation() // a fresh watcher registry for the "reloaded" page
    vi.resetModules()

    await mountSettingsGeo()
    expect(geoState()).toBe('off')
    expect(geolocation!.watchCalls).toHaveLength(0)
  })
})

// =============================================================================
// 2. The tap: watchPosition inside the tap's own call stack, nothing
//    awaited first (SPEC 4.6.2, G1), and navigator.permissions never
//    called at all (SPEC 4.6.2's own amendment, G2).
// =============================================================================

describe("the tap: watchPosition inside the tap's own call stack, nothing awaited first (SPEC 4.6.2, G1)", () => {
  it('watchPosition is called synchronously by the click, before any microtask runs', async () => {
    await mountSettingsGeo()
    expect(geolocation!.watchCalls).toHaveLength(0)

    // Deliberately not awaited: the assertion below has to observe the
    // state immediately after the synchronous part of the click handler
    // runs, before Svelte's own microtask flush or this file's settle().
    geoToggle().dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true }),
    )
    expect(
      geolocation!.watchCalls,
      "expected watchPosition to have been called inside the tap's own call stack, before any await",
    ).toHaveLength(1)

    await settle()
  })

  // SPEC 4.6.2: "enableHighAccuracy: true. The default is false, and this
  // app exists to say where a capture was taken -- a position good to the
  // nearest cell tower locates the neighbourhood, not the capture."
  it('the watch asks for high accuracy: watchPosition is called with { enableHighAccuracy: true }', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watcher = geolocation!.activeWatch()
    expect(watcher).not.toBeNull()
    expect(watcher!.options).toEqual({ enableHighAccuracy: true })
  })

  // SPEC 4.6.2 (amended): "navigator.permissions is not called at all... A
  // call whose result nothing renders is a branch nothing can reach." G2 is
  // *why* that decision is safe -- it already ruled permissions.query() out
  // as a source of the denied state, since iOS reports "prompt" for a
  // permission the owner actually denied in system settings -- but the
  // module's own behaviour is simpler than G2 alone: it must never call
  // query() at all, denied answer or not. Both facts are asserted below:
  // the fake is never invoked, and (the residual form of the old G2 test,
  // now that there is nothing left to race) a "denied" query answer still
  // does not put the section in the denied state, which would only be a
  // live question again if a later change started calling query() despite
  // this paragraph.
  it('navigator.permissions.query() is never called: the copy table has no sentence that turns on its answer', async () => {
    const perms = installPermissions('prompt')
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await click(geoToggle()) // off
    await click(geoToggle()) // on again, a second attempt to ask
    expect(perms.queryCalls).toBe(0)
  })

  it('a permissions API reporting "denied", with no GeolocationPositionError ever fired, does not put the section in the denied state (G2) -- and, per the point above, is never even asked', async () => {
    const perms = installPermissions('denied')
    await mountSettingsGeo()
    await click(geoToggle())
    expect(geoState()).not.toBe('denied')
    expect(perms.queryCalls).toBe(0)
  })
})

// =============================================================================
// 3. The five client-local states (SPEC 4.6.2's own table).
// =============================================================================

describe('the five client-local states (SPEC 4.6.2)', () => {
  it('"waiting": the control on, the watch running, no position and no error yet', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    expect(geoState()).toBe('waiting')
    expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
  })

  it('"sharing": reached once a position has been received', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')
    expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
  })

  it('"denied": reached from GeolocationPositionError.code === 1 (G4)', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoState()).toBe('denied')
  })

  // lib/geo.ts's own comment on codes 2 and 3: "not a reason to change
  // state or tear anything down: the watch is still live". `not.toBe`
  // against a fixed set of five states passes unchanged if onError instead
  // tore everything down for every code -- the state would land on
  // 'waiting', which also is not 'denied', and this assertion alone would
  // not notice. What actually distinguishes "ignored" from "torn down
  // anyway": the watch is still the very watcher that reported the error,
  // not a replacement, and a position delivered afterwards still reaches
  // 'sharing' rather than restarting from 'waiting'.
  it('is NOT reached from code 2 (POSITION_UNAVAILABLE), and the watch survives it', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watcherBefore = geolocation!.activeWatch()
    expect(watcherBefore).not.toBeNull()

    geolocation!.emitError(2)
    await settle()
    expect(geoState()).not.toBe('denied')

    const watcherAfter = geolocation!.activeWatch()
    expect(watcherAfter).not.toBeNull()
    expect(watcherAfter!.id).toBe(watcherBefore!.id)

    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')
  })

  it('is NOT reached from code 3 (TIMEOUT), and the watch survives it', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watcherBefore = geolocation!.activeWatch()
    expect(watcherBefore).not.toBeNull()

    geolocation!.emitError(3)
    await settle()
    expect(geoState()).not.toBe('denied')

    const watcherAfter = geolocation!.activeWatch()
    expect(watcherAfter).not.toBeNull()
    expect(watcherAfter!.id).toBe(watcherBefore!.id)

    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')
  })

  // SPEC 4.6.2: "unsupported is recomputed from the same guard every
  // time the state is published, and in a browser the answer never
  // changes... What is deliberately not added is a publish of its own at
  // the point the control is tapped and refuses: that would be a branch
  // reachable only from a test." The section is explicit that this is not
  // the same claim as "decided once and never revisited" -- an earlier
  // draft said that and the code contradicts it, since the guard is read
  // fresh at every publish -- but a browser never actually removes
  // navigator.geolocation from a running document, so nothing here can
  // observe the guard changing its answer mid-session; this is the
  // mount-time case, asserted in full: the state, the disabled control,
  // and a tap that does nothing.
  it('a browser with no geolocation shows "unsupported" at mount, with the control disabled and its tap a no-op', async () => {
    removeGeolocation()
    await mountSettingsGeo()
    expect(geoState()).toBe('unsupported')
    expect(geoToggle().hasAttribute('disabled')).toBe(true)

    // A throw during the click handler would fail this test on its own; no
    // special matcher is needed to observe that.
    await click(geoToggle())
    expect(geoState()).toBe('unsupported')
  })

  // The guard itself, kept separate from the state question above.
  // SPEC 4.6.2 says "unsupported is recomputed from the same guard every
  // time the state is published" -- the same capability read that decides
  // the state is what a tap consults before it would call watchPosition,
  // so this pins the guard's other job: nothing reaches the browser API
  // when there is nothing to reach. This does not need the state to
  // move -- only that the tap itself does not.
  it('the guard runs at the point of use: with no geolocation, nothing ever reaches watchPosition, tap included', async () => {
    const fake = installGeolocation()
    removeGeolocation()
    await mountSettingsGeo()
    await click(geoToggle())
    expect(fake.watchCalls).toHaveLength(0)
  })
})

// =============================================================================
// 3b. The toggle's own label and aria-pressed, over all five states (SPEC
//     4.6.2's "DOM hooks and copy" paragraph): "Share this phone's
//     position", except at denied, where it carries "Try again"; aria-pressed
//     true for waiting and sharing and neither of the other three. Both
//     driven from lib/format.ts's formatGeoToggleLabel rather than a
//     literal in this file, the same reason the copy meta-assertions are:
//     formatGeoToggleLabel runs on every Settings mount regardless, so its
//     branches report covered whether or not any assertion ever looks at
//     what they produced.
// =============================================================================

describe("the toggle's own label and aria-pressed, over all five states (SPEC 4.6.2)", () => {
  it('"off": "Share this phone\'s position", not pressed', async () => {
    await mountSettingsGeo()
    expect(geoState()).toBe('off')
    expect(geoToggle().textContent).toBe(formatGeoToggleLabel('off'))
    expect(geoToggle().getAttribute('aria-pressed')).toBe('false')
  })

  it('"waiting": "Share this phone\'s position", pressed', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    expect(geoState()).toBe('waiting')
    expect(geoToggle().textContent).toBe(formatGeoToggleLabel('waiting'))
    expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
  })

  it('"sharing": "Share this phone\'s position", pressed', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')
    expect(geoToggle().textContent).toBe(formatGeoToggleLabel('sharing'))
    expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
  })

  // "that is not a state the control can be pressed back out of, and the
  // only thing left to offer is another attempt" -- the label switches to
  // "Try again" specifically because tapping again does not turn anything
  // off, and aria-pressed says so too: the watch is not running.
  it('"denied": "Try again", not pressed', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoState()).toBe('denied')
    expect(geoToggle().textContent).toBe(formatGeoToggleLabel('denied'))
    expect(geoToggle().textContent).toBe('Try again')
    expect(geoToggle().getAttribute('aria-pressed')).toBe('false')
  })

  it('"unsupported": "Share this phone\'s position", not pressed, disabled', async () => {
    removeGeolocation()
    await mountSettingsGeo()
    expect(geoState()).toBe('unsupported')
    expect(geoToggle().textContent).toBe(formatGeoToggleLabel('unsupported'))
    expect(geoToggle().getAttribute('aria-pressed')).toBe('false')
    expect(geoToggle().hasAttribute('disabled')).toBe(true)
  })
})

// =============================================================================
// 3c. Turning the control off tears the watch and the timer down (SPEC
//     4.6.2: "it exists only while the control is on, it stops the moment
//     the control is off"). The mirror of the "denied" teardown test below,
//     for the ordinary path every other test's afterEach already walks
//     through without ever asserting anything about it.
// =============================================================================

describe('turning the control off tears the watch and the timer down (SPEC 4.6.2)', () => {
  it('no active watcher, the right id cleared, and no further pushes well past the interval', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      const watchIdBeforeOff = geolocation!.activeWatch()!.id
      const callsBeforeOff = client.sendGpsCalls.length

      await click(geoToggle())
      expect(geoState()).toBe('off')
      expect(geolocation!.activeWatch()).toBeNull()
      expect(geolocation!.clearedIds).toContain(watchIdBeforeOff)

      await vi.advanceTimersByTimeAsync(20_000)
      expect(client.sendGpsCalls.length).toBe(callsBeforeOff)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 4. "denied" is sticky, and only "try again" -- returning to "waiting" --
//    gets out of it (SPEC 4.6.2).
// =============================================================================

describe('"denied" is sticky, and trying again returns to "waiting" (SPEC 4.6.2)', () => {
  // SPEC 4.6.2 (amended): "Reaching denied tears the watch and the timer
  // down... a watch that has reported PERMISSION_DENIED will not deliver a
  // position afterwards, so keeping it is the module still appearing to
  // try at something it has been told it may not do." This replaces an
  // earlier version of this test that fired a second emitError(1) into the
  // same watch -- unreachable once the watch is actually torn down on the
  // first one, since the fake's own activeWatch() would have nothing live
  // to deliver it to. What is observable and load-bearing instead: no
  // watcher is left active, the cleared id is the one that reported the
  // denial (not merely superseded by a newer one), and the push timer -
  // proven with a position it would otherwise still be sending - produces
  // nothing further.
  it('reaching "denied" tears the watch and the timer down: no active watcher, and no further pushes', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      const watchIdBeforeDenial = geolocation!.activeWatch()!.id
      const callsBeforeDenial = client.sendGpsCalls.length

      geolocation!.emitError(1)
      await settle()
      expect(geoState()).toBe('denied')
      expect(geolocation!.activeWatch()).toBeNull()
      expect(geolocation!.clearedIds).toContain(watchIdBeforeDenial)

      await vi.advanceTimersByTimeAsync(20_000)
      expect(client.sendGpsCalls.length).toBe(callsBeforeDenial)
    } finally {
      vi.useRealTimers()
    }
  })

  it('tapping the control again from "denied" attempts a fresh watch and returns to "waiting"', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoState()).toBe('denied')
    const watchesBefore = geolocation!.watchCalls.length

    await click(geoToggle())
    expect(geoState()).toBe('waiting')
    expect(geolocation!.watchCalls.length).toBeGreaterThan(watchesBefore)
  })
})

// =============================================================================
// 5. The push cadence: at most 5 s, and a stationary phone still pushes on
//    the interval (SPEC 4.6, 4.6.2's "third timer" section).
// =============================================================================

describe('the push cadence is at most 5 s, and a stationary phone (no further watchPosition callbacks) still pushes on it', () => {
  // SPEC 4.6.2: "Pushing on significant change as well -- every
  // watchPosition callback -- is the optimisation SPEC 4.6 already allows
  // on top of the floor." This is the one test in the file that observes it
  // directly: a push recorded before any clock advance at all, so deleting
  // the pushPosition() call from onPosition fails this test rather than
  // surviving it the way a comparison against the interval's own effect
  // would. Every other cadence test in this describe advances the clock
  // past the interval before asserting, which cannot tell the optimisation
  // apart from the floor timer alone -- this is deliberately the only one
  // that does not.
  it("a position received while sharing pushes immediately, before the timer's own interval elapses", async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      expect(client.sendGpsCalls).toHaveLength(0)

      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      expect(
        client.sendGpsCalls.length,
        'expected the watchPosition callback itself to push, with no clock advance at all',
      ).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a held position with no further callbacks is still pushed again at the 5 s mark, not sooner and not much later', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      // A baseline, not an assertion: the push-on-callback optimisation
      // above may already have sent this position once. What this test
      // pins is the floor underneath it -- the next test below covers that
      // optimisation directly instead of a comparison that cannot fail.
      const callsAfterFirstPosition = client.sendGpsCalls.length

      await vi.advanceTimersByTimeAsync(4_999)
      expect(client.sendGpsCalls.length).toBe(callsAfterFirstPosition)

      await vi.advanceTimersByTimeAsync(1)
      expect(client.sendGpsCalls.length).toBeGreaterThan(
        callsAfterFirstPosition,
      )
    } finally {
      vi.useRealTimers()
    }
  })

  it('the interval keeps producing a push every 5 s with no callback in between, for more than one cycle', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      await vi.advanceTimersByTimeAsync(5_000)
      const afterFirstInterval = client.sendGpsCalls.length
      expect(afterFirstInterval).toBeGreaterThan(0)

      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.sendGpsCalls.length).toBeGreaterThan(afterFirstInterval)
    } finally {
      vi.useRealTimers()
    }
  })

  it('while "waiting" (no position ever received), the interval elapsing produces no push: there is nothing to send', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      await vi.advanceTimersByTimeAsync(20_000)
      expect(client.sendGpsCalls).toHaveLength(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 6. Nothing is pushed while stats.gps.piFix is true, evaluated at push
//    time from the gps store, and the timer keeps running underneath the
//    transition rather than being stopped and restarted (SPEC 4.6, 4.6.2).
// =============================================================================

describe('nothing is pushed while stats.gps.piFix is true, read from the gps store at push time (SPEC 4.6/4.6.2)', () => {
  it('a unit that already has a Pi fix when sharing is turned on receives no pushes', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(piFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      await vi.advanceTimersByTimeAsync(20_000)
      expect(client.sendGpsCalls).toHaveLength(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a unit that acquires a Pi fix while the app is open stops receiving pushes, without the control being touched', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      await vi.advanceTimersByTimeAsync(5_000)
      const callsBeforeFix = client.sendGpsCalls.length
      expect(callsBeforeFix).toBeGreaterThan(0)

      client.emitMessage(piFixStats())
      await settle()
      // The control is untouched: still "on".
      expect(geoToggle().getAttribute('aria-pressed')).toBe('true')

      await vi.advanceTimersByTimeAsync(20_000)
      expect(client.sendGpsCalls.length).toBe(callsBeforeFix)
    } finally {
      vi.useRealTimers()
    }
  })

  it('a unit whose Pi fix drops while the app is open resumes receiving pushes, without the control being touched', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(piFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      await vi.advanceTimersByTimeAsync(10_000)
      expect(client.sendGpsCalls).toHaveLength(0)

      client.emitMessage(noPiFixStats())
      await settle()
      expect(geoToggle().getAttribute('aria-pressed')).toBe('true')

      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.sendGpsCalls.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('the timer keeps running across the transition rather than being stopped and restarted: the 5 s boundary does not reset when piFix flips mid-interval', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(piFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      // Blocked from t=0. At t=2000 (mid-interval) piFix drops.
      await vi.advanceTimersByTimeAsync(2_000)
      client.emitMessage(noPiFixStats())
      await settle()
      expect(client.sendGpsCalls).toHaveLength(0)

      // A timer restarted from the moment piFix cleared would fire at
      // t=2000+5000=7000. A timer that kept running underneath fires at
      // its own next boundary, t=5000. Advancing to just before t=5000
      // (2999 more ms, total 4999) must still show nothing; the single
      // remaining ms crossing t=5000 is what proves which one happened.
      await vi.advanceTimersByTimeAsync(2_999)
      expect(client.sendGpsCalls).toHaveLength(0)

      await vi.advanceTimersByTimeAsync(1)
      expect(
        client.sendGpsCalls.length,
        "expected a push at the timer's own t=5000 boundary, not a fresh window started when piFix cleared at t=2000",
      ).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 7. No usable socket: nothing thrown with no active host, and the loop
//    picks up a client that becomes available later without the control
//    being touched (SPEC 4.6.2: "asks for the current one at each push").
// =============================================================================

describe('no usable socket: nothing thrown with no client, and a later client is used without re-arming the control', () => {
  it('with every host removed, turning sharing on and letting the timer run throws nothing', async () => {
    vi.useFakeTimers()
    try {
      const { settings } = await mountSettingsGeo()
      for (const host of get(settings.settings).hosts)
        settings.removeHost(host.id)
      await settle()

      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      // An unhandled throw from inside the push loop with no client to
      // push to fails this test on its own (log-view.spec.ts's own
      // no-active-client pattern for its follow timer).
      await vi.advanceTimersByTimeAsync(20_000)
      // The control itself is not reset by losing the client (the same
      // rule the Log's follow control is held to in log-view.spec.ts).
      expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
    } finally {
      vi.useRealTimers()
    }
  })

  it('activating a host afterward gives the running loop a client to push to, on the next tick, with no tap needed', async () => {
    vi.useFakeTimers()
    try {
      const created: FakeWsClient[] = []
      const { settings } = await mountSettingsGeo((c) => created.push(c))
      for (const host of get(settings.settings).hosts)
        settings.removeHost(host.id)
      await settle()

      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      await vi.advanceTimersByTimeAsync(20_000)

      const newHost = settings.addHost({
        label: 'Unit B',
        address: '172.20.10.9',
        wsPort: 8082,
        httpPort: 8443,
        token: null,
      })
      settings.activateHost(newHost.id)
      await settle()
      // `created` already holds the initial mount's client for the
      // default (bluetooth) host, from before every host was removed
      // above; this is the second one, built for the newly activated host.
      expect(created).toHaveLength(2)
      const client = created[1] as FakeWsClient
      client.emitMessage(noPiFixStats())
      await settle()

      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.sendGpsCalls.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('lib/geo.ts does not duplicate the offline gate: it still calls sendGps while the client itself reports "offline" (SPEC 4.3.3 is ws.ts\'s rule, not this module\'s)', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      client.emitState('offline')
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      await vi.advanceTimersByTimeAsync(5_000)
      // This fake's sendGps records unconditionally (see the class
      // comment); the real lib/ws.ts is what discards while offline
      // (already covered by ws.spec.ts). A geo.ts that itself special-cased
      // "offline" and skipped the call would fail this assertion.
      expect(client.sendGpsCalls.length).toBeGreaterThan(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

// =============================================================================
// 8. Backgrounding: hidden clears the watch and stops the timer; visible
//    restarts both, with the control unchanged (SPEC 4.6.2, G3).
// =============================================================================

describe('backgrounding: hidden clears the watch and stops the timer, visible restarts both (SPEC 4.6.2, G3)', () => {
  // SPEC 4.6.2: "which property says the document is hidden is not a rule
  // this section makes... A fixture that sets only one of them is an
  // incomplete fixture." setVisibility() below sets both
  // document.visibilityState and document.hidden, so a module reading
  // either one sees the same fixture -- there is no mutant left in this
  // file that swaps one property for the other, and there should not be
  // one: the two cannot disagree in a real browser.
  //
  // The mutant that does remain is inverting the branch itself (treating
  // hidden as visible and visible as hidden). Measured directly against
  // this describe block -- not reasoned about -- it is killed by 6 of the
  // 9 tests here: "going hidden clears the active watchPosition() watch,
  // and starts no new one", "going hidden drops a held position
  // immediately", "going hidden stops the push timer", "coming back
  // visible resumes pushing once the new watch delivers a fresh position",
  // "a stale watch surviving the trip... must actually be a new one", and
  // "a visibilitychange to visible while already visible does not leave
  // two watches live". The second assertion in the first of those is
  // load-bearing on its own, and was found by mutation rather than written
  // up front: the inverted branch also clears the old watch before
  // starting a fresh one, so asserting only that the watch id was cleared
  // passes under the inversion too. Asserting no new watch was started is
  // what tells the two apart.
  it('going hidden clears the active watchPosition() watch, and starts no new one', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watchId = geolocation!.activeWatch()!.id
    const watchesBeforeHide = geolocation!.watchCalls.length

    setVisibility('hidden')
    await settle()
    expect(geolocation!.clearedIds).toContain(watchId)
    expect(
      geolocation!.watchCalls.length,
      'expected no new watchPosition() call while genuinely hidden',
    ).toBe(watchesBeforeHide)
    expect(geolocation!.activeWatch()).toBeNull()
  })

  // SPEC 4.6.2 ("Backgrounding"): "the last reading is dropped there
  // rather than on the way back. That placement is the one that makes the
  // state true throughout: sharing is defined below as a position having
  // been received and being what the timer sends, and while hidden
  // nothing is sent, so a state left at sharing for the duration is the
  // app describing something it is not doing. It is not invisible either
  // -- iOS renders the hidden document in the app switcher." This is the
  // claim the resume tests below cannot make on their own: they observe
  // the state *after* coming back, which the rejected alternative
  // (dropping on the way back instead) would also have satisfied. What is
  // only true under the placement this section settles on is that the
  // state is already waiting while still hidden, immediately, with no
  // resume involved at all.
  it('going hidden drops a held position immediately: the state is "waiting", not "sharing", for the whole time the document stays hidden', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')

    setVisibility('hidden')
    await settle()
    expect(geoState()).toBe('waiting')
  })

  it('going hidden stops the push timer: advancing the clock produces no more pushes', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      await vi.advanceTimersByTimeAsync(5_000)
      const callsBeforeHide = client.sendGpsCalls.length
      expect(callsBeforeHide).toBeGreaterThan(0)

      setVisibility('hidden')
      await settle()

      await vi.advanceTimersByTimeAsync(30_000)
      expect(client.sendGpsCalls.length).toBe(callsBeforeHide)
    } finally {
      vi.useRealTimers()
    }
  })

  it('coming back visible, with the control still on, starts a fresh watch', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watchesBeforeHide = geolocation!.watchCalls.length

    setVisibility('hidden')
    await settle()
    setVisibility('visible')
    await settle()

    expect(geolocation!.watchCalls.length).toBeGreaterThan(watchesBeforeHide)
    expect(geoToggle().getAttribute('aria-pressed')).toBe('true')
  })

  // SPEC 4.6.2 ("Coming back is a fresh start, not a resumption."): "the
  // timer re-armed on the way back would fire up to a full interval
  // before the new watch has delivered anything, and what it would send
  // is the coordinate taken before the phone was pocketed... Dropping the
  // reading with the watch is what closes it." (The reading is now
  // dropped on hide rather than here, per the paragraph above this one --
  // this test's own assertion holds under either placement, since it
  // observes the outcome rather than where the drop happens.) The
  // previous version of this test emitted a fresh position immediately
  // after setVisibility('visible'), on the assumption that a fresh watch
  // needs a fresh position -- an assumption about the implementation, not
  // a fact this file is entitled to. Because it emitted before advancing
  // the clock, it passed identically whether or not the pre-hide
  // coordinate survived, so the whole file could not see review's
  // finding: the timer re-armed on resume fires up to a full interval
  // before the new watch delivers, pushing the coordinate taken before
  // the phone was pocketed. This is the test that actually observes
  // that: resume, do not emit, advance past the interval, and nothing is
  // sent.
  it('coming back visible does not push the position taken before the phone was pocketed', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()
      expect(geoState()).toBe('sharing')

      setVisibility('hidden')
      await settle()
      setVisibility('visible')
      await settle()

      // SPEC 4.6.2: "the state on resume is waiting until the new watch
      // produces something -- the same thing the control's own tap does."
      expect(geoState()).toBe('waiting')
      const callsAtResume = client.sendGpsCalls.length

      // Deliberately no emitPosition() here.
      await vi.advanceTimersByTimeAsync(5_000)
      expect(client.sendGpsCalls.length).toBe(callsAtResume)
      expect(geoState()).toBe('waiting')
    } finally {
      vi.useRealTimers()
    }
  })

  it('coming back visible resumes pushing once the new watch delivers a fresh position', async () => {
    vi.useFakeTimers()
    try {
      const { client } = await mountSettingsGeo()
      client.emitMessage(noPiFixStats())
      await click(geoToggle())
      geolocation!.emitPosition(10.1, -30.2)
      await settle()

      setVisibility('hidden')
      await settle()
      setVisibility('visible')
      await settle()
      const callsAtResume = client.sendGpsCalls.length

      geolocation!.emitPosition(10.1, -30.3)
      await settle()

      expect(client.sendGpsCalls.length).toBeGreaterThan(callsAtResume)
      expect(geoState()).toBe('sharing')
    } finally {
      vi.useRealTimers()
    }
  })

  it("a stale watch surviving the trip would push the last position it saw -- G3's own reason -- so the watch must actually be a new one, not the same id reused", async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const before = geolocation!.activeWatch()!.id

    setVisibility('hidden')
    await settle()
    setVisibility('visible')
    await settle()

    const after = geolocation!.activeWatch()
    expect(after).not.toBeNull()
    expect(after!.id).not.toBe(before)
  })

  // SPEC 4.6.2 (amended): "starting them clears whatever is already
  // running first, unconditionally, so that an event that fires without
  // the visibility having actually changed cannot leave two watches live
  // with only the second one reachable to clear." Fired directly, without
  // going through `hidden` first: `document.visibilityState` is already
  // `'visible'`, so this is exactly the spurious-event case the sentence
  // names, not a real hidden->visible transition. The defect this guards
  // against is a `watchPosition` call added on top of an existing one
  // rather than replacing it -- silently leaving an orphaned watcher that
  // nothing will ever clear, since only the most recent id is retained
  // anywhere for a later hide to clear.
  it('a visibilitychange to visible while already visible does not leave two watches live: starting clears whatever is running first, unconditionally', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const first = geolocation!.activeWatch()!.id

    setVisibility('visible')
    await settle()

    const liveWatches = geolocation!.watchCalls.filter(
      (w) => !geolocation!.clearedIds.includes(w.id),
    )
    expect(
      liveWatches,
      'expected exactly one live watch after a spurious visible event',
    ).toHaveLength(1)
    expect(liveWatches[0]!.id).not.toBe(first)
  })

  it('backgrounding while sharing is off does nothing harmful: no watch to clear, nothing thrown', async () => {
    await mountSettingsGeo()
    expect(geoState()).toBe('off')
    setVisibility('hidden')
    await settle()
    setVisibility('visible')
    await settle()
    expect(geoState()).toBe('off')
    expect(geolocation!.watchCalls).toHaveLength(0)
  })
})

// =============================================================================
// 9. The pushed frame: schema conformance, and no forbidden `source`
//    (SPEC 4.6.1: "a client that adds source to its push gets
//    bad_request").
// =============================================================================

describe('the pushed frame validates against incoming/gps_data.json (SPEC 4.6.1/4.6.2)', () => {
  it('conforms to the schema: no extra property, the required ones present', async () => {
    const { client } = await mountSettingsGeo()
    client.emitMessage(noPiFixStats())
    await click(geoToggle())
    geolocation!.emitPosition(12.34, 56.78, 8)
    await settle()

    expect(client.sendGpsCalls.length).toBeGreaterThan(0)
    const data = client.sendGpsCalls[0] as Record<string, unknown>
    const frame = { type: 'gps_data', ...data }
    assertConformsToSchema(frame, GPS_DATA_SCHEMA)
  })

  it('never carries "source": the wire shape the client pushes has no such property, unlike stats.gps', async () => {
    const { client } = await mountSettingsGeo()
    client.emitMessage(noPiFixStats())
    await click(geoToggle())
    geolocation!.emitPosition(12.34, 56.78, 8)
    await settle()

    for (const call of client.sendGpsCalls) {
      expect(Object.prototype.hasOwnProperty.call(call, 'source')).toBe(false)
    }
  })

  it('carries the latitude and longitude the browser reported', async () => {
    const { client } = await mountSettingsGeo()
    client.emitMessage(noPiFixStats())
    await click(geoToggle())
    geolocation!.emitPosition(12.34, 56.78, 8)
    await settle()

    const data = client.sendGpsCalls[0] as Record<string, unknown>
    expect(data.latitude).toBe(12.34)
    expect(data.longitude).toBe(56.78)
  })
})

// =============================================================================
// 10. Copy: the five sentences, exact, and the negative claim that none of
//     them says anything about what the unit has (SPEC 4.6.2's own table).
// =============================================================================

describe("copy: the five sentences, pinned by equality against 4.6.2's own table", () => {
  // Literal, hand-transcribed from SPEC 4.6.2's copy table -- the DOM
  // assertions below check the section against these, which is what
  // catches the section's own wording drifting from the table. The two
  // meta-assertions further down deliberately do NOT use this constant:
  // they are driven from lib/format.ts's formatGeoStateMessage instead, so
  // that a state added to production with a bad sentence fails there even
  // though it could never fail here, where the five keys are fixed by this
  // file's own hand.
  const SENTENCES: Record<string, string> = {
    off: "Not sharing this phone's position.",
    waiting: 'Waiting for a position.',
    sharing: "Sharing this phone's position.",
    denied:
      "This browser refused location access. Allow it in the browser's settings, then try again.",
    unsupported: 'This browser cannot supply a position.',
  }

  // SPEC 4.6.2: data-geo-message is "the element holding the sentence...
  // so that a test asserting the copy reads the one element that is the
  // copy, rather than the section's flattened text, which also contains
  // the control's own label and would pass a sentence that had gone
  // missing as long as the label still matched." Every assertion below
  // reads that element, and by equality (toBe), not by substring
  // (toContain) against the flattened section.

  it('"off"', async () => {
    await mountSettingsGeo()
    expect(geoMessage().textContent).toBe(SENTENCES.off)
  })

  it('"waiting"', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    expect(geoMessage().textContent).toBe(SENTENCES.waiting)
  })

  it('"sharing"', async () => {
    const { client } = await mountSettingsGeo()
    client.emitMessage(noPiFixStats())
    await click(geoToggle())
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoMessage().textContent).toBe(SENTENCES.sharing)
  })

  it('"denied"', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoMessage().textContent).toBe(SENTENCES.denied)
  })

  it('"unsupported"', async () => {
    removeGeolocation()
    await mountSettingsGeo()
    expect(geoMessage().textContent).toBe(SENTENCES.unsupported)
  })

  // Driven from lib/format.ts's own formatGeoStateMessage, over every
  // GeoState this file knows the name of -- not from the SENTENCES literal
  // above, which is this file's own constant and would make either claim
  // true by construction: a sixth state added to production with the word
  // "unit" in it, or two states given the same sentence, would not fail
  // against a copy of itself.
  const ALL_STATES: GeoState[] = [
    'off',
    'waiting',
    'sharing',
    'denied',
    'unsupported',
  ]

  it('none of the five sentences says anything about what the unit has: the word "unit" does not appear in any of them', () => {
    for (const state of ALL_STATES) {
      const sentence = formatGeoStateMessage(state)
      expect(
        sentence.toLowerCase(),
        `the ${state} sentence must not mention the unit`,
      ).not.toContain('unit')
    }
  })

  it('the five sentences are pairwise distinct: no two states share a sentence', () => {
    const values = ALL_STATES.map((state) => formatGeoStateMessage(state))
    expect(new Set(values).size).toBe(values.length)
  })
})

// =============================================================================
// 10b. "denied" is independent of piFix in both directions (SPEC 4.6.2's
//      own division of labour: "this module supplies the fact and does not
//      compose the four rows"). Written at the coordinator's request after
//      review found the claim recorded in the SPEC 10.7 inventory with no
//      test behind it -- neither direction was previously exercised: no
//      test emitted a piFix stats payload before an emitError.
// =============================================================================

describe('"denied" is independent of piFix in both directions (SPEC 4.6.2)', () => {
  it('a Pi fix appearing after denial does not clear it: this module never reads piFix at all', async () => {
    const { client } = await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoState()).toBe('denied')

    client.emitMessage(piFixStats())
    await settle()
    expect(geoState()).toBe('denied')
  })

  it('reaching denied does not affect what the unit reports: the gps store keeps updating from ordinary stats pushes', async () => {
    const stores = await import('../lib/stores')
    const { client } = await mountSettingsGeo()
    await click(geoToggle())
    geolocation!.emitError(1)
    await settle()
    expect(geoState()).toBe('denied')

    const reading = gpsReading({
      enabled: true,
      source: 'gpsd',
      piFix: false,
      fix: true,
      lat: 10.3,
      lon: -30.2,
      updated: 1_700_000_500,
    })
    client.emitMessage(statsEnvelope(reading))
    await settle()

    expect(get(stores.gps)).toEqual(reading)
  })
})

// =============================================================================
// 10c. A callback from a watch this module no longer owns (SPEC 4.6.2):
//      clearWatch() does not promise an already-queued platform callback
//      is cancelled, so a PERMISSION_DENIED from a torn-down watch can
//      still land afterwards. This reverses an earlier decision, and the
//      history is worth keeping because the first answer was defensible
//      and still wrong: this file's own fake was what answered the
//      question -- it drove the race, and running it (not guessing at it)
//      showed the state became denied even for a control already switched
//      off. That looked free because the explanation ("denied is a fact
//      about the browser") was not lost by keeping it. What it missed is
//      the second consequence of the same race, and SPEC 4.6.2 now states
//      it: "off, then Try again, then the previous watch's queued refusal
//      arrives and re-denies the fresh attempt and tears down the watch
//      that was just started. The retry -- the one recovery this state
//      exists to offer -- becomes defeatable by a callback belonging to a
//      watch nobody is waiting on." So the callback is now ignored
//      entirely, by a per-watch token minted in beginWatch and checked in
//      onPosition/onError before either does anything: a mismatch means
//      the watch that produced the callback has since been torn down, and
//      SPEC 4.6.2 says the explanation is not lost by dropping it either,
//      since geolocation permission is per-origin rather than per-watch --
//      the browser refuses the new watch too, and the denial arrives again
//      from the watch that is actually current.
// =============================================================================

describe('a callback from a watch this module no longer owns is ignored, whatever it says (SPEC 4.6.2)', () => {
  // The narrower claim first: a stale callback arriving in the window
  // between a teardown and the next beginWatch -- turned off, nothing
  // started again yet -- does nothing at all, not merely "nothing bad".
  it('a late PERMISSION_DENIED from a torn-down watch, with nothing new started since, changes neither the state nor its sentence', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watcher = geolocation!.activeWatch()
    expect(watcher).not.toBeNull()

    await click(geoToggle()) // turnOff(): tears the watch down
    expect(geoState()).toBe('off')
    const messageBeforeLateCallback = geoMessage().textContent

    // The platform queued this before clearWatch() took effect. Modelled
    // by holding the original watcher's error callback directly --
    // bypassing activeWatch(), which correctly reports nothing live -- and
    // invoking it after teardown rather than before.
    watcher!.error!({ code: 1 } as GeolocationPositionError)
    await settle()

    expect(geoState()).toBe('off')
    expect(geoMessage().textContent).toBe(messageBeforeLateCallback)
  })

  // The wider claim, and the one the reversal exists for: the same stale
  // callback must not be able to reach through a retry and kill the fresh
  // attempt it has nothing to do with.
  it('after a denial and a retry, a stale refusal from the old watch does not re-deny or tear down the new one', async () => {
    await mountSettingsGeo()
    await click(geoToggle())
    const watcherA = geolocation!.activeWatch()
    expect(watcherA).not.toBeNull()

    watcherA!.error!({ code: 1 } as GeolocationPositionError)
    await settle()
    expect(geoState()).toBe('denied')

    await click(geoToggle()) // "Try again"
    const watcherB = geolocation!.activeWatch()
    expect(watcherB).not.toBeNull()
    expect(watcherB!.id).not.toBe(watcherA!.id)
    expect(geoState()).toBe('waiting')

    // The old watch's queued refusal, arriving again now that the retry
    // has already started a new watch. Before the token, this would have
    // re-denied the fresh attempt and torn watcherB down.
    watcherA!.error!({ code: 1 } as GeolocationPositionError)
    await settle()

    expect(geoState()).toBe('waiting')
    const stillActive = geolocation!.activeWatch()
    expect(stillActive).not.toBeNull()
    expect(stillActive!.id).toBe(watcherB!.id)

    // And the new watch is still genuinely live, not merely un-cleared:
    // a position through it still reaches the screen.
    geolocation!.emitPosition(10.1, -30.2)
    await settle()
    expect(geoState()).toBe('sharing')
  })
})

// =============================================================================
// 11. The DOM hooks themselves (SPEC 4.6.2).
// =============================================================================

describe('the DOM hooks (SPEC 4.6.2)', () => {
  it('data-geo-state carries the state name, updated live', async () => {
    await mountSettingsGeo()
    expect(geoState()).toBe('off')
    await click(geoToggle())
    expect(geoState()).toBe('waiting')
  })

  it('the control is inside the section its state is reported on', async () => {
    await mountSettingsGeo()
    expect(geoSection().contains(geoToggle())).toBe(true)
  })

  it('the toggle is inside the Settings view root', async () => {
    await mountSettingsGeo()
    expect(root().contains(geoToggle())).toBe(true)
  })
})
