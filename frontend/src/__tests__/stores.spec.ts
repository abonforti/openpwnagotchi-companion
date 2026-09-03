import { get } from 'svelte/store'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type {
  AccessPoint,
  Battery,
  Capabilities,
  ErrorCode,
  FaceStatus,
  Gps,
  HandshakeEntry,
  Mode,
  OutgoingAccessPoints,
  OutgoingAcknowledgment,
  OutgoingChannelHop,
  OutgoingError,
  OutgoingFaceStatus,
  OutgoingGpsUpdate,
  OutgoingHandshake,
  OutgoingHandshakesList,
  OutgoingKeepalive,
  OutgoingLogLines,
  OutgoingMessage,
  OutgoingPeerDetected,
  OutgoingPeersList,
  OutgoingPong,
  OutgoingRestarting,
  OutgoingScreenImage,
  OutgoingStats,
  OutgoingStatusChange,
  OutgoingWifiUpdate,
  Peer,
  RestartReason,
  Stats,
} from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  LastError,
  StatsSnapshot,
  UnauthorizedReason,
  WsClient,
} from '../lib/ws'
import {
  accessPoints,
  capabilities,
  channel,
  connectStores,
  connection,
  face,
  gps,
  handshakes,
  log,
  peers,
  resetStores,
  stats,
} from '../lib/stores'

// Written from SPEC.md 4.4 (4.4.1, 4.4.2) and docs/schemas/, and the fixed
// public surface handed to the test author for issue #111. Deliberately not
// read from lib/stores.ts, which is authored in parallel.
//
// The double below fakes WsClient rather than a socket: connectStores is
// specified to subscribe to the client, never to reach into a transport, so
// the suite drives it purely through onState/onMessage/request and never
// touches ws.ts or a socket.

const T = 1700000000

// ---------------------------------------------------------------------------
// Fixture builders. Realistic shapes, synthetic content only.
// ---------------------------------------------------------------------------

function battery(overrides: Partial<Battery> = {}): Battery {
  return { percent: 80, charging: false, ...overrides }
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

function gpsReading(overrides: Partial<Gps> = {}): Gps {
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

function statsData(overrides: Partial<Stats> = {}): Stats {
  return {
    uptime: 120,
    name: null,
    mode: 'AUTO',
    channel: 6,
    battery: battery(),
    temperature: 45.2,
    handshakes: 3,
    handshakesTotal: 4,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: gpsReading(),
    sessionAge: 5,
    capabilities: capabilitiesData(),
    ...overrides,
  }
}

function accessPoint(overrides: Partial<AccessPoint> = {}): AccessPoint {
  return {
    bssid: 'AA:BB:CC:DD:EE:01',
    hostname: 'TestNet_001',
    channel: 6,
    rssi: -55,
    encryption: 'WPA2',
    vendor: 'TestVendor',
    clients: 0,
    ...overrides,
  }
}

function handshakeEntry(
  overrides: Partial<HandshakeEntry> = {},
): HandshakeEntry {
  return {
    filename: 'TestNet_001_aabbccddeeff.pcapng',
    ssid: 'TestNet_001',
    bssid: 'aabbccddeeff',
    mtime: T,
    size: 1024,
    gps: null,
    ...overrides,
  }
}

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

function faceStatusData(overrides: Partial<FaceStatus> = {}): FaceStatus {
  return { face: '(-_-)', status: 'idle', mode: 'AUTO', ...overrides }
}

function statsEnvelope(overrides: Partial<Stats> = {}): OutgoingStats {
  return { type: 'stats', timestamp: T, data: statsData(overrides) }
}

function accessPointsEnvelope(list: AccessPoint[]): OutgoingAccessPoints {
  return { type: 'access_points', timestamp: T, data: list }
}

function wifiUpdateEnvelope(list: AccessPoint[]): OutgoingWifiUpdate {
  return { type: 'wifi_update', timestamp: T, data: list }
}

function handshakesPayload(
  entries: HandshakeEntry[],
  truncated = false,
  total = entries.length,
): OutgoingHandshakesList['data'] {
  return { entries, truncated, total }
}

function handshakesListEnvelope(
  entries: HandshakeEntry[],
  truncated = false,
  total = entries.length,
): OutgoingHandshakesList {
  return {
    type: 'handshakes_list',
    timestamp: T,
    data: handshakesPayload(entries, truncated, total),
  }
}

function handshakePushEnvelope(
  overrides: Partial<OutgoingHandshake['data']> = {},
): OutgoingHandshake {
  return {
    type: 'handshake',
    timestamp: T,
    data: {
      filename: 'TestNet_002_112233445566.pcapng',
      ap: 'AA:BB:CC:DD:EE:02',
      gps: null,
      ...overrides,
    },
  }
}

function peersListEnvelope(entries: Peer[]): OutgoingPeersList {
  return { type: 'peers_list', timestamp: T, data: { entries } }
}

function peerDetectedEnvelope(entry: Peer): OutgoingPeerDetected {
  return { type: 'peer_detected', timestamp: T, data: entry }
}

function logLinesEnvelope(
  lines: string[],
  path = '/tmp/pwnagotchi.log',
): OutgoingLogLines {
  return { type: 'log_lines', timestamp: T, data: { lines, path } }
}

function gpsUpdateEnvelope(reading: Gps): OutgoingGpsUpdate {
  return { type: 'gps_update', timestamp: T, data: reading }
}

function faceStatusEnvelope(entry: FaceStatus): OutgoingFaceStatus {
  return { type: 'face_status', timestamp: T, data: entry }
}

function statusChangeEnvelope(
  status: string,
  mood: 'bored' | 'excited' | 'lonely' | 'sad',
): OutgoingStatusChange {
  return { type: 'status_change', timestamp: T, data: { status, mood } }
}

function channelHopEnvelope(value: number): OutgoingChannelHop {
  return { type: 'channel_hop', timestamp: T, data: { channel: value } }
}

// The six message types the store layer does not act on at all (SPEC 4.4.1
// lists a writer for every other type). All six still reach onMessage in
// production - ws.ts dispatches every frame there regardless of type.

function keepaliveEnvelope(): OutgoingKeepalive {
  return { type: 'keepalive', timestamp: T, data: {} }
}

function pongEnvelope(): OutgoingPong {
  return { type: 'pong', timestamp: T, data: {} }
}

function acknowledgmentEnvelope(acknowledged: string): OutgoingAcknowledgment {
  return { type: 'acknowledgment', timestamp: T, data: { acknowledged } }
}

function errorEnvelope(code: ErrorCode, message = 'an error'): OutgoingError {
  return { type: 'error', timestamp: T, data: { code, message } }
}

function screenImageEnvelope(): OutgoingScreenImage {
  return {
    type: 'screen_image',
    timestamp: T,
    data: { png: 'aGVsbG8=', mtime: T },
  }
}

function restartingEnvelope(
  reason: RestartReason,
  mode: Mode | null,
): OutgoingRestarting {
  return { type: 'restarting', timestamp: T, data: { reason, mode } }
}

// ---------------------------------------------------------------------------
// A hand-driven double for WsClient. Records subscribers so a teardown can be
// proven to remove them, records request() calls so a debounced refresh can
// be proven, and lets a test fire a state or a message by hand - no socket,
// no timers, no wall clock.
// ---------------------------------------------------------------------------

interface RecordedRequest {
  type: string
  data: unknown
}

class FakeWsClient implements WsClient {
  currentState: ConnectionState = 'offline'
  reasonValue: UnauthorizedReason | null = null
  // SPEC 4.4.1 / issue #131: mirrors the real client's own restartReason(),
  // which gates the same way - non-null only while currentState is
  // 'restarting' (ws.ts: "currentState === 'restarting' ? restartReasonValue
  // : null"). The raw value survives leaving restarting (nothing clears it),
  // exactly as the real client's own field does; only the getter's gate does.
  private restartReasonValue: RestartReason | null = null
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
  readonly requestCalls: RecordedRequest[] = []
  private readonly pendingRequests: Array<{
    resolve: (message: OutgoingMessage) => void
    reject: (error: Error) => void
  }> = []

  connect(): void {
    // Not exercised: the store layer only subscribes, per SPEC 4.4.
  }

  close(): void {
    // Not exercised directly by connectStores: SPEC 4.4.2 has the adapter
    // react to the unauthorized state, and treats close() and a dropped
    // tether as indistinguishable, both landing on offline.
  }

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
    this.requestCalls.push({ type, data })
    return new Promise((resolve, reject) => {
      this.pendingRequests.push({ resolve, reject })
    })
  }

  command(_type: string, ..._rest: unknown[]): Promise<OutgoingMessage> {
    return Promise.reject(
      new Error(
        'the store layer must never issue a command of its own (SPEC 4.4)',
      ),
    )
  }

  sendGps(_data: unknown): void {
    // Not exercised: the store layer only subscribes, per SPEC 4.4.
  }

  /** Test-only: sets the value connectStores should seed lastStats from at mount, before connect() was ever called on a real client. Takes the envelope timestamp alongside the payload (SPEC 4.3.10's StatsSnapshot), the same pairing a live stats push carries. */
  primeLastStats(data: Stats, timestamp: number = T): void {
    this.lastStatsValue = { stats: data, timestamp }
  }

  /** Test-only: sets the value connectStores should seed diagnostics from at mount, before connect() was ever called on a real client (SPEC 4.3.10, mirrors primeLastStats). */
  primeDiagnostics(diagnostics: Diagnostics): void {
    this.diagnosticsValue = diagnostics
  }

  /** Test-only: simulates the client entering a new state, exactly as onState would deliver it. */
  emitState(
    state: ConnectionState,
    reason: UnauthorizedReason | null = null,
  ): void {
    this.currentState = state
    this.reasonValue = reason
    for (const handler of [...this.stateHandlers]) handler(state)
  }

  /** Test-only: simulates a frame arriving from the plugin, on the same stream a request's reply would also arrive on. */
  emitMessage(message: OutgoingMessage): void {
    if (message.type === 'stats')
      this.lastStatsValue = {
        stats: message.data,
        timestamp: message.timestamp,
      }
    // Mirrors ws.ts's own handleRestarting: the reason is captured off the
    // wire frame itself, before the state transition that follows it.
    if (message.type === 'restarting')
      this.restartReasonValue = message.data.reason
    for (const handler of [...this.messageHandlers]) handler(message)
  }

  /** Test-only: simulates the client's diagnostics changing, exactly as onDiagnostics would deliver it (SPEC 4.3.10). */
  emitDiagnostics(diagnostics: Diagnostics): void {
    this.diagnosticsValue = diagnostics
    for (const handler of [...this.diagnosticsHandlers]) handler(diagnostics)
  }

  /**
   * Test-only: settles the oldest still-pending request() call. Matches the
   * real client's own ordering (SPEC 4.3, ws.ts handleMessage): the reply is
   * dispatched to onMessage subscribers first, and the request's promise is
   * only resolved after, so a caller that awaits the promise to know when it
   * is safe to issue another request sees that moment strictly after the
   * store write the message caused, not before or in the same microtask.
   */
  settleOldestRequest(reply: OutgoingMessage): void {
    const entry = this.pendingRequests.shift()
    if (!entry)
      throw new Error('settleOldestRequest: no pending request to settle')
    this.emitMessage(reply)
    entry.resolve(reply)
  }

  /** Test-only: rejects the oldest still-pending request() call, as a request timeout or a close() would (SPEC 4.3.8, 4.3). */
  rejectOldestRequest(error: Error): void {
    const entry = this.pendingRequests.shift()
    if (!entry)
      throw new Error('rejectOldestRequest: no pending request to reject')
    entry.reject(error)
  }
}

function asClient(fake: FakeWsClient): WsClient {
  return fake
}

let teardown: (() => void) | null = null

function mount(fake: FakeWsClient): void {
  teardown = connectStores(asClient(fake))
}

beforeEach(() => {
  resetStores()
})

afterEach(() => {
  if (teardown) {
    teardown()
    teardown = null
  }
  resetStores()
})

// ---------------------------------------------------------------------------
// 4.4.1 - one store, one rule, per row of the table.
// ---------------------------------------------------------------------------

describe('connection: a mirror of the client state, not a second opinion', () => {
  it('reflects the client state at mount, before any push', () => {
    const fake = new FakeWsClient()
    fake.currentState = 'degraded'
    mount(fake)
    expect(get(connection)).toEqual({
      state: 'degraded',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })

  it('carries every connection state through unchanged', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const states: ConnectionState[] = [
      'connecting',
      'connected',
      'degraded',
      'offline',
      'restarting',
    ]
    for (const state of states) {
      fake.emitState(state)
      expect(get(connection)).toEqual({
        state,
        unauthorizedReason: null,
        restartReason: null,
        lastError: null,
        latencyMs: null,
        droppedFrames: 0,
      })
    }
  })

  it('carries both unauthorized reasons through unchanged', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitState('unauthorized', 'rejected')
    expect(get(connection)).toEqual({
      state: 'unauthorized',
      unauthorizedReason: 'rejected',
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
    fake.emitState('unauthorized', 'required')
    expect(get(connection)).toEqual({
      state: 'unauthorized',
      unauthorizedReason: 'required',
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })

  it('clears the reason once the client leaves unauthorized, rather than keeping it stale', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitState('unauthorized', 'rejected')
    fake.emitState('connecting')
    expect(get(connection)).toEqual({
      state: 'connecting',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })

  it("reads offline once no client is attached, not the client's last reported state (SPEC 4.4.2)", () => {
    // Not the module-initial value: without the rule under test, deleting
    // the previous client's own report ("connected") would still leave
    // this reading connected, since nothing else ever writes to
    // `connection` between the two calls. That is what makes this
    // assertion prove the teardown rule rather than an accident of
    // whatever `connection` started at.
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitState('connected')
    expect(get(connection)).toEqual({
      state: 'connected',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })

    teardown?.()

    expect(get(connection)).toEqual({
      state: 'offline',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.4.1 (issue #131): "the restarting reason reaches the store ...
// mode_change, reboot and shutdown differ in wording alone [except] shutdown
// [which] is terminal ... So ConnectionView carries the reason beside the
// state, the same way it carries the unauthorized one ... It is null in
// every state but restarting." SPEC now pins the field by this exact name:
// "So `ConnectionView` carries it as `restartReason`, beside the state, the
// same way it carries `unauthorizedReason` and for the same reason." What
// every test below defends against is a view that can see the connection is
// `restarting` but not which of the three reasons it is, which for
// `shutdown` (§4.3.1 makes it terminal, no reconnection is ever attempted)
// is the difference between correct copy and telling someone to wait for a
// screen that is not coming.
//
// WsClient does carry a `restartReason()` accessor (mirrored by this file's
// own FakeWsClient below, gated the same way the real one is), but these
// tests still drive it the way the wire actually delivers it rather than by
// priming that accessor directly: the `restarting` message's own
// `data.reason`, pushed through `emitMessage` exactly as a real `restarting`
// frame would arrive, immediately followed by the state transition
// `emitState('restarting')` reports for it. That leaves the accessor itself
// - and specifically whether connectStores reads it correctly rather than
// re-deriving the gate - to be covered against the real client, not this
// double, in ws.spec.ts (issue #131 follow-up).
// ---------------------------------------------------------------------------

describe('connection: the restarting reason reaches the store, and only there (SPEC 4.4.1, issue #131)', () => {
  it('is null before anything has happened, and in the ordinary connected state', () => {
    const fake = new FakeWsClient()
    mount(fake)
    expect(get(connection).restartReason).toBeNull()
    fake.emitState('connected')
    expect(get(connection).restartReason).toBeNull()
  })

  it('is null in offline, connecting, degraded and unauthorized - restarting is the only state that carries one', () => {
    const fake = new FakeWsClient()
    mount(fake)
    for (const state of ['offline', 'connecting', 'degraded'] as const) {
      fake.emitState(state)
      expect(get(connection).restartReason).toBeNull()
    }
    fake.emitState('unauthorized', 'required')
    expect(get(connection).restartReason).toBeNull()
  })

  it('carries mode_change through to the store', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(restartingEnvelope('mode_change', 'MANUAL'))
    fake.emitState('restarting')
    expect(get(connection)).toMatchObject({
      state: 'restarting',
      restartReason: 'mode_change',
    })
  })

  it('carries reboot through to the store', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(restartingEnvelope('reboot', null))
    fake.emitState('restarting')
    expect(get(connection)).toMatchObject({
      state: 'restarting',
      restartReason: 'reboot',
    })
  })

  it('carries shutdown through, distinct from reboot - the value a view needs to tell the terminal case apart', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(restartingEnvelope('shutdown', null))
    fake.emitState('restarting')
    expect(get(connection).restartReason).toBe('shutdown')
    expect(get(connection).restartReason).not.toBe('reboot')
  })

  it('clears once the client leaves restarting, the same way unauthorizedReason clears on leaving unauthorized', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(restartingEnvelope('reboot', null))
    fake.emitState('restarting')
    expect(get(connection).restartReason).toBe('reboot')

    fake.emitState('connecting')
    expect(get(connection).restartReason).toBeNull()
  })

  it('reads null once no client is attached, not the last reason a torn-down client reported (SPEC 4.4.2)', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(restartingEnvelope('shutdown', null))
    fake.emitState('restarting')
    expect(get(connection).restartReason).toBe('shutdown')

    teardown?.()

    expect(get(connection).restartReason).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.3.10 / 4.4.1: ConnectionView gains lastError and latencyMs, mirrored
// from WsClient.diagnostics()/onDiagnostics() exactly as state/unauthorizedReason
// already are. Written from the same public surface handed to the ws.ts test
// author for issue #176 (Diagnostics/LastError), not from stores.ts.
// ---------------------------------------------------------------------------

// Overrides are typed against the 'frame'/'close' pair, not the full
// `LastError` union: both carry `code: string`, and every call site below
// only ever swaps `source` between the two or changes `code`/`message`/`at`.
// A `Partial<LastError>` would still typecheck a `source: 'local'` override
// paired with an arbitrary `code` string, a combination the client can never
// produce, so this fixture must not be able to build it either.
type WireLastError = Extract<LastError, { source: 'frame' | 'close' }>

function sampleLastError(overrides: Partial<WireLastError> = {}): LastError {
  return {
    source: 'frame',
    code: 'internal_error',
    message: 'a sample failure',
    at: 1700000123,
    ...overrides,
  }
}

describe('connection: the diagnostics pair mirrors the client, not a second opinion (SPEC 4.3.10)', () => {
  it('seeds lastError and latencyMs from the client at mount, before any onDiagnostics push', () => {
    const fake = new FakeWsClient()
    const seededError = sampleLastError()
    fake.primeDiagnostics({
      lastError: seededError,
      latencyMs: 42,
      droppedFrames: 0,
    })
    mount(fake)
    expect(get(connection)).toEqual({
      state: 'offline',
      unauthorizedReason: null,
      restartReason: null,
      lastError: seededError,
      latencyMs: 42,
      droppedFrames: 0,
    })
  })

  it('shows null lastError and null latencyMs at mount when the client has neither yet, rather than a placeholder shape', () => {
    const fake = new FakeWsClient()
    mount(fake)
    expect(get(connection).lastError).toBeNull()
    expect(get(connection).latencyMs).toBeNull()
  })

  it('carries a diagnostics update through unchanged, without disturbing state or unauthorizedReason', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitState('connected')
    const error = sampleLastError({
      source: 'close',
      code: '1006',
      message: '',
    })
    fake.emitDiagnostics({ lastError: error, latencyMs: 88, droppedFrames: 0 })
    expect(get(connection)).toEqual({
      state: 'connected',
      unauthorizedReason: null,
      restartReason: null,
      lastError: error,
      latencyMs: 88,
      droppedFrames: 0,
    })
  })

  // SPEC 4.3.11 (issue #109): droppedFrames is mirrored the same way
  // lastError and latencyMs are, but every fixture above happens to carry
  // 0 for it, so none of those tests can tell a real mirror from a field
  // hardcoded to zero. This drives a non-zero count through the live-push
  // mirroring site specifically, the one exercised on every ordinary
  // reconnect-free run of the app.
  it('mirrors a non-zero droppedFrames count through to the connection store, not a hardcoded zero', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitDiagnostics({ lastError: null, latencyMs: null, droppedFrames: 5 })
    expect(get(connection).droppedFrames).toBe(5)
  })

  // The same gap, at the seed-at-mount site rather than the live-push one:
  // "seeds lastError and latencyMs from the client at mount" above pins the
  // seeding mechanism but carries droppedFrames: 0 like every other fixture
  // in this file did before the two tests above, so it cannot tell a real
  // seed from a field the seeding code hardcodes to zero.
  it('seeds a non-zero droppedFrames count from the client at mount, not a hardcoded zero', () => {
    const fake = new FakeWsClient()
    fake.primeDiagnostics({
      lastError: null,
      latencyMs: null,
      droppedFrames: 3,
    })
    mount(fake)
    expect(get(connection).droppedFrames).toBe(3)
  })

  // A latency of exactly 0ms is a measurement, not the absence of one - a
  // mirror that treats a falsy number the same as null would silently turn
  // a very fast link into "no data", which is the reading SPEC 4.3.10 rules
  // out ("both are absent rather than zero when [there is nothing to report]").
  it('distinguishes a measured latencyMs of 0 from an absent one', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitDiagnostics({ lastError: null, latencyMs: 0, droppedFrames: 0 })
    expect(get(connection).latencyMs).toBe(0)
    expect(get(connection).latencyMs).not.toBeNull()
  })

  it("reads a null lastError and null latencyMs once no client is attached, not the client's last reported diagnostics (SPEC 4.4.2)", () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitDiagnostics({
      lastError: sampleLastError(),
      latencyMs: 15,
      droppedFrames: 0,
    })
    expect(get(connection).lastError).not.toBeNull()
    expect(get(connection).latencyMs).toBe(15)

    teardown?.()

    expect(get(connection).lastError).toBeNull()
    expect(get(connection).latencyMs).toBeNull()
  })

  // AMBIGUITY, see report: SPEC 4.3.10 pins that latency changes go through
  // onDiagnostics rather than onState ("a client that fired onState to
  // announce a round trip would be telling every subscriber the state moved
  // when it did not"), but it does not say whether the *reverse* also
  // happens - whether the state-change handler re-reads client.diagnostics()
  // on every transition, or reuses whatever the store already cached from
  // the last onDiagnostics push/seed. The ordinary FakeWsClient double above
  // cannot distinguish the two designs at all: its diagnostics() never
  // changes as a side effect of a state emission, so both designs read the
  // same value either way, which is exactly why nothing in this file caught
  // it. This double is built specifically so the two disagree: diagnostics()
  // returns a fixed reading for 'connected' and a different one otherwise,
  // and the test drives only emitState() - never emitDiagnostics() - so the
  // store's own cached copy (seeded null/null at mount) and a fresh read of
  // diagnostics() at the moment of the transition are forced apart.
  //
  // The direction asserted below (the transition re-reads diagnostics())
  // is not read off §4.3.10 directly - it follows this file's own stated
  // principle for every other field in this describe block ("a mirror of
  // the client state, not a second opinion", the same reasoning behind
  // "reads offline once no client is attached, not the client's last
  // reported state" above): a mirror that composes a stale, separately
  // cached copy of lastError/latencyMs into the object it hands out on a
  // state transition is a second opinion about what the client's
  // diagnostics currently are, not a mirror of them. That is an inference
  // from the file's own design language, not a literal §4.3.10 citation, so
  // it is named as such here rather than presented as a direct quote.
  it('a state transition composes the connection object from a fresh read of diagnostics(), not a stale cached copy (inferred from the "mirror, not a second opinion" principle - see report)', () => {
    class StateLinkedDiagnosticsClient extends FakeWsClient {
      diagnostics(): Diagnostics {
        return this.currentState === 'connected'
          ? { lastError: null, latencyMs: 999, droppedFrames: 0 }
          : { lastError: null, latencyMs: null, droppedFrames: 0 }
      }
    }
    const fake = new StateLinkedDiagnosticsClient()
    mount(fake)
    expect(get(connection).latencyMs).toBeNull()

    fake.emitState('connected') // onState only - emitDiagnostics() is never called
    expect(get(connection).latencyMs).toBe(999)
  })

  // The same fresh-read requirement, for droppedFrames rather than
  // latencyMs: the state-transition handler could carry droppedFrames
  // forward from whatever the store already held instead of reading it off
  // client.diagnostics() the same way it reads lastError and latencyMs, and
  // every fixture elsewhere in this file happens to agree on 0 for it, so
  // nothing above would notice. Same double, same drive-only-emitState()
  // shape as the test above, with droppedFrames forced apart between states
  // instead of latencyMs.
  it('a state transition also composes droppedFrames from a fresh read of diagnostics(), not a stale cached copy', () => {
    class StateLinkedDroppedFramesClient extends FakeWsClient {
      diagnostics(): Diagnostics {
        return this.currentState === 'connected'
          ? { lastError: null, latencyMs: null, droppedFrames: 9 }
          : { lastError: null, latencyMs: null, droppedFrames: 0 }
      }
    }
    const fake = new StateLinkedDroppedFramesClient()
    mount(fake)
    expect(get(connection).droppedFrames).toBe(0)

    fake.emitState('connected') // onState only - emitDiagnostics() is never called
    expect(get(connection).droppedFrames).toBe(9)
  })
})

describe("seeding at mount: the adapter reads the client's current stats, not only future pushes", () => {
  it('shows the last stats already known to the client immediately on connectStores, with no push at all', () => {
    const fake = new FakeWsClient()
    const seeded = statsData({ uptime: 555, channel: 9 })
    fake.primeLastStats(seeded)
    mount(fake)
    expect(get(stats)).toEqual(seeded)
    expect(get(channel)).toBe(9)
    expect(get(capabilities)).toEqual(seeded.capabilities)
  })

  it('shows null stats at mount when the client has none yet, rather than a placeholder shape', () => {
    const fake = new FakeWsClient()
    mount(fake)
    expect(get(stats)).toBeNull()
  })

  // SPEC 4.3.10/4.4.1 (issue #162's hole): the seeded channel carries the
  // envelope stamp lastStats() now hands over, so it is gated exactly as a
  // live push would be, from the moment a view mounts rather than only from
  // the next broadcast. A double that seeded the channel unguarded (or a
  // lastStats() that dropped the timestamp again) would let the older push
  // below win and this test would catch it.
  it('a channel seeded at mount is gated like a live push: a stats push timestamped before the seed does not move it backward', () => {
    const fake = new FakeWsClient()
    fake.primeLastStats(statsData({ channel: 9 }), T + 100)
    mount(fake)
    expect(get(channel)).toBe(9)
    fake.emitMessage(statsEnvelopeAt(T + 50, 20)) // older than the seed's own timestamp
    expect(get(channel)).toBe(9) // must not move backward
  })
})

describe('stats: the last stats message, verbatim', () => {
  it('holds exactly the payload of the last stats message, sessionAge included', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const envelope = statsEnvelope({ sessionAge: 42, uptime: 999 })
    fake.emitMessage(envelope)
    expect(get(stats)).toEqual(envelope.data)
  })

  it('is replaced whole by the next stats message, not merged with the previous one', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statsEnvelope({ uptime: 1, temperature: 10 }))
    const second = statsEnvelope({ uptime: 2, temperature: null })
    fake.emitMessage(second)
    expect(get(stats)).toEqual(second.data)
  })
})

describe('capabilities: derived from stats, no writer of its own', () => {
  it('is null before any stats has arrived', () => {
    const fake = new FakeWsClient()
    mount(fake)
    expect(get(capabilities)).toBeNull()
  })

  it('mirrors the capabilities object carried inside the last stats message', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const caps = capabilitiesData({
      pasv: false,
      gpsSource: 'none',
      pluginVersion: '0.2.0',
    })
    fake.emitMessage(statsEnvelope({ capabilities: caps }))
    expect(get(capabilities)).toEqual(caps)
  })
})

describe('channel: derived, and stats is never patched to hold it', () => {
  it('hop then stats: the later stats message wins over an earlier hop, and stats stays untouched', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statsEnvelope({ channel: 6 }))
    fake.emitMessage(channelHopEnvelope(11))
    expect(get(channel)).toBe(11)
    const secondStats = statsEnvelope({ channel: 20, sessionAge: 7 })
    fake.emitMessage(secondStats)
    expect(get(channel)).toBe(20)
    expect(get(stats)).toEqual(secondStats.data)
  })

  it('stats then hop: the hop is the most recent event and wins, while the stats payload is untouched byte for byte', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const baseline = statsEnvelope({ channel: 6, sessionAge: 3 })
    fake.emitMessage(baseline)
    fake.emitMessage(channelHopEnvelope(9))
    expect(get(channel)).toBe(9)
    expect(get(stats)).toEqual(baseline.data)
    expect(get(stats)?.channel).toBe(6)
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.4.1 (amended, issue #162): "'Whichever came later' is a timestamp,
// not an arrival order ... the store therefore keeps the timestamp it last
// wrote and ignores a message carrying an older one." Every test in the
// describe block above shares one hardcoded timestamp (T) across every
// envelope, so arrival order and timestamp order are the same sequence
// there and neither the regression nor its fix can be told apart by it -
// that is exactly the gap issue #162 names ("a test that only sends
// messages in timestamp order passes on the broken implementation"). These
// four tests give hop and stats independent timestamps for that reason.
// ---------------------------------------------------------------------------

function statsEnvelopeAt(
  timestamp: number,
  channelValue: number,
): OutgoingStats {
  return {
    type: 'stats',
    timestamp,
    data: statsData({ channel: channelValue }),
  }
}

function channelHopEnvelopeAt(
  timestamp: number,
  value: number,
): OutgoingChannelHop {
  return { type: 'channel_hop', timestamp, data: { channel: value } }
}

describe('channel: "whichever came later" is the timestamp, not the arrival order (issue #162)', () => {
  it('a stats produced before a hop, but delivered after it, does not overwrite the newer channel - the regression itself', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(channelHopEnvelopeAt(T + 100, 11)) // the hop, timestamped later
    fake.emitMessage(statsEnvelopeAt(T + 50, 6)) // a broadcast the unit produced earlier, arriving after the hop
    expect(get(channel)).toBe(11) // a plain "last write wins" store would read 6 here
  })

  it('the ordinary direction still works: a stats timestamped after the hop does overwrite it', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(channelHopEnvelopeAt(T, 9))
    fake.emitMessage(statsEnvelopeAt(T + 50, 20))
    expect(get(channel)).toBe(20)
  })

  it('an equal timestamp is accepted, hop then stats: the message that arrived second is the newer of the two', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(channelHopEnvelopeAt(T, 9))
    fake.emitMessage(statsEnvelopeAt(T, 20))
    expect(get(channel)).toBe(20)
  })

  it('an equal timestamp is accepted, stats then hop: reversing arrival order reverses which one wins', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statsEnvelopeAt(T, 20))
    fake.emitMessage(channelHopEnvelopeAt(T, 9))
    expect(get(channel)).toBe(9)
  })
})

describe('accessPoints: replaced whole, never merged', () => {
  it('replaces the list on wifi_update, dropping an access point absent from the new one', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const first = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:01',
      hostname: 'TestNet_001',
    })
    const second = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:02',
      hostname: 'TestNet_002',
    })
    fake.emitMessage(wifiUpdateEnvelope([first, second]))
    expect(get(accessPoints)).toEqual([first, second])
    const third = accessPoint({
      bssid: 'AA:BB:CC:DD:EE:03',
      hostname: 'TestNet_003',
    })
    fake.emitMessage(wifiUpdateEnvelope([third]))
    expect(get(accessPoints)).toEqual([third])
  })

  it('replaces the list on the access_points reply too, the same rule as the push', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const first = accessPoint({ bssid: 'AA:BB:CC:DD:EE:01' })
    fake.emitMessage(accessPointsEnvelope([first]))
    expect(get(accessPoints)).toEqual([first])
    fake.emitMessage(accessPointsEnvelope([]))
    expect(get(accessPoints)).toEqual([])
  })
})

describe('handshakes: written by the list reply only, holding the whole payload', () => {
  it('replaces the store with the whole handshakes_list payload, not the entries alone', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const first = handshakeEntry({
      filename: 'TestNet_001_aabbccddeeff.pcapng',
    })
    const second = handshakeEntry({
      filename: 'TestNet_004_aabbccdd0004.pcapng',
    })
    fake.emitMessage(handshakesListEnvelope([first, second], true, 750))
    expect(get(handshakes)).toEqual(
      handshakesPayload([first, second], true, 750),
    )
    const third = handshakeEntry({
      filename: 'TestNet_005_aabbccdd0005.pcapng',
    })
    fake.emitMessage(handshakesListEnvelope([third]))
    expect(get(handshakes)).toEqual(handshakesPayload([third]))
  })

  it('keeps truncated and total distinct from entries.length - the fields a truncation notice and a 500+ badge read', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const entries = [
      handshakeEntry({ filename: 'TestNet_001_aabbccddeeff.pcapng' }),
    ]
    fake.emitMessage(handshakesListEnvelope(entries, true, 812))
    const stored = get(handshakes)
    expect(stored.entries).toHaveLength(1)
    expect(stored.truncated).toBe(true)
    expect(stored.total).toBe(812)
  })

  it('a handshake push does not itself carry data into the store - it is a filename-and-macs shape, not a HandshakeEntry', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(
      handshakesListEnvelope([handshakeEntry({ filename: 'existing.pcapng' })]),
    )
    fake.emitMessage(handshakePushEnvelope({ filename: 'brand_new.pcapng' }))
    // Only the get_handshakes round trip below is allowed to change the
    // store; the push by itself must not insert a fabricated entry.
    expect(get(handshakes)).toEqual(
      handshakesPayload([handshakeEntry({ filename: 'existing.pcapng' })]),
    )
    // Release the coalescing flag this push started: SPEC 4.4.1 says a
    // refresh belongs to the client that asked for it, and this fake stays
    // attached for the rest of the test, so leaving it in flight here would
    // leak into the next test.
    fake.settleOldestRequest(
      handshakesListEnvelope([handshakeEntry({ filename: 'existing.pcapng' })]),
    )
  })

  it('a handshake push causes exactly one get_handshakes request', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(handshakePushEnvelope())
    expect(fake.requestCalls).toHaveLength(1)
    expect(fake.requestCalls[0]?.type).toBe('get_handshakes')
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it('handling a handshake push does not depend on a station field (SPEC 2.13, issue #33)', () => {
    // The wire no longer carries `station` at all, but SPEC 4.3.11 accepts
    // unknown keys on any incoming shape, so a message built by an older
    // unit that still sends one must be handled identically to the current
    // `{filename, ap, gps}` shape: one refresh request either way, nothing
    // read out of the field itself. The cast is required because the
    // generated type no longer has a `station` property to assign.
    const withStation = {
      ...handshakePushEnvelope({ filename: 'legacy_shape.pcapng' }),
      data: {
        ...handshakePushEnvelope({ filename: 'legacy_shape.pcapng' }).data,
        station: 'AA:BB:CC:DD:EE:03',
      },
    } as unknown as ReturnType<typeof handshakePushEnvelope>

    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(withStation)
    expect(fake.requestCalls).toHaveLength(1)
    expect(fake.requestCalls[0]?.type).toBe('get_handshakes')
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it('several pushes while a refresh is in flight cause one request, not one per push', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(handshakePushEnvelope({ filename: 'burst_one.pcapng' }))
    fake.emitMessage(handshakePushEnvelope({ filename: 'burst_two.pcapng' }))
    fake.emitMessage(handshakePushEnvelope({ filename: 'burst_three.pcapng' }))
    expect(fake.requestCalls).toHaveLength(1)
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it('the reply to the refresh replaces the store, exactly like an ordinary handshakes_list', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(
      handshakesListEnvelope([handshakeEntry({ filename: 'before.pcapng' })]),
    )
    fake.emitMessage(handshakePushEnvelope({ filename: 'after.pcapng' }))
    const refreshed = [handshakeEntry({ filename: 'after.pcapng' })]
    fake.settleOldestRequest(handshakesListEnvelope(refreshed))
    expect(get(handshakes)).toEqual(handshakesPayload(refreshed))
  })

  it('settling writes the store before the coalescing flag clears: a push in the same tick still coalesces, a later tick does not', async () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(handshakePushEnvelope())
    expect(fake.requestCalls).toHaveLength(1)
    const refreshed = [handshakeEntry({ filename: 'after.pcapng' })]
    fake.settleOldestRequest(handshakesListEnvelope(refreshed))
    // The store write happens synchronously, inside settleOldestRequest's
    // dispatch to onMessage subscribers.
    expect(get(handshakes)).toEqual(handshakesPayload(refreshed))
    // The coalescing flag has not cleared yet in this same tick: a push
    // issued right now still coalesces into the settled request rather than
    // starting a second one.
    fake.emitMessage(handshakePushEnvelope({ filename: 'same_tick.pcapng' }))
    expect(fake.requestCalls).toHaveLength(1)
    // The flag clears a hop or two after resolve(), not necessarily within
    // a single microtask - flushing past every microtask (rather than
    // pinning an exact count) is what the coalescing contract actually
    // promises.
    await new Promise((resolve) => setTimeout(resolve, 0))
    fake.emitMessage(handshakePushEnvelope({ filename: 'next_tick.pcapng' }))
    expect(fake.requestCalls).toHaveLength(2)
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it('a rejected refresh still releases the coalescing flag, so a following push starts a new one', async () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(handshakePushEnvelope())
    expect(fake.requestCalls).toHaveLength(1)
    fake.rejectOldestRequest(new Error('simulated request timeout'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    fake.emitMessage(
      handshakePushEnvelope({ filename: 'after_failure.pcapng' }),
    )
    expect(fake.requestCalls).toHaveLength(2)
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it('the in-flight flag survives a remount on the same client - it is not reset on attach', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(handshakePushEnvelope())
    expect(fake.requestCalls).toHaveLength(1)
    const stop = teardown as () => void
    stop()
    teardown = null
    mount(fake) // same client, re-attached without the first request ever settling
    fake.emitMessage(
      handshakePushEnvelope({ filename: 'after_remount.pcapng' }),
    )
    expect(fake.requestCalls).toHaveLength(1) // still coalesced, not reset by the remount
    // Release the flag so it does not leak into the next test: SPEC 4.4.1
    // says a refresh belongs to the client that asked for it, and this
    // fake stays attached for the rest of the test.
    fake.settleOldestRequest(handshakesListEnvelope([]))
  })

  it("a host switch lets the new host issue its own refresh, rather than waiting on the previous host's abandoned one", () => {
    const clientA = new FakeWsClient()
    mount(clientA)
    clientA.emitMessage(handshakePushEnvelope({ filename: 'from_a.pcapng' }))
    expect(clientA.requestCalls).toHaveLength(1) // A's refresh, deliberately left pending
    const stopA = teardown as () => void
    stopA()
    teardown = null
    resetStores()
    const clientB = new FakeWsClient()
    mount(clientB)
    clientB.emitMessage(handshakePushEnvelope({ filename: 'from_b.pcapng' }))
    // A's refresh is owned by a client that is no longer attached, so it
    // does not count as in flight for B: B issues its own request rather
    // than losing this push because A's reply can never reach a store.
    expect(clientB.requestCalls).toHaveLength(1)
    clientB.settleOldestRequest(
      handshakesListEnvelope([handshakeEntry({ filename: 'from_b.pcapng' })]),
    )
  })

  it("A's own refresh settling late, after B has already taken over, does not release B's in-flight owner", async () => {
    const clientA = new FakeWsClient()
    mount(clientA)
    clientA.emitMessage(handshakePushEnvelope({ filename: 'from_a.pcapng' }))
    expect(clientA.requestCalls).toHaveLength(1) // A's refresh, left pending on purpose
    const stopA = teardown as () => void
    stopA()
    teardown = null
    resetStores()
    const clientB = new FakeWsClient()
    mount(clientB)
    clientB.emitMessage(handshakePushEnvelope({ filename: 'from_b.pcapng' }))
    expect(clientB.requestCalls).toHaveLength(1) // B's own refresh, owner is now B
    // A's request finally settles, long after A detached. Its owner is A,
    // not B, so this must not clear B's in-flight flag.
    clientA.rejectOldestRequest(new Error('timeout'))
    await new Promise((resolve) => setTimeout(resolve, 0))
    clientB.emitMessage(handshakePushEnvelope({ filename: 'second.pcapng' }))
    // Still coalesced into B's own outstanding refresh - A's late settle
    // must not have cleared an owner that was never A's to clear.
    expect(clientB.requestCalls).toHaveLength(1)
    clientB.settleOldestRequest(
      handshakesListEnvelope([handshakeEntry({ filename: 'from_b.pcapng' })]),
    )
  })
})

describe("peers: list replaces, a push upserts on fingerprint, never on name or the default '???'", () => {
  it('replaces the list on the peers_list reply', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const p1 = peer({ fingerprint: 'fp-0001', name: 'unit-one' })
    fake.emitMessage(peersListEnvelope([p1]))
    expect(get(peers)).toEqual([p1])
    const p2 = peer({ fingerprint: 'fp-0002', name: 'unit-two' })
    fake.emitMessage(peersListEnvelope([p2]))
    expect(get(peers)).toEqual([p2])
  })

  it('upserts an existing fingerprint in place, rather than appending a duplicate', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const p1 = peer({ fingerprint: 'fp-0001', name: 'unit-one', rssi: -60 })
    const p2 = peer({ fingerprint: 'fp-0002', name: 'unit-two', rssi: -70 })
    fake.emitMessage(peersListEnvelope([p1, p2]))
    const updatedP1 = peer({
      fingerprint: 'fp-0001',
      name: 'unit-one',
      rssi: -40,
    })
    fake.emitMessage(peerDetectedEnvelope(updatedP1))
    const result = get(peers)
    expect(result).toHaveLength(2)
    const found = result.find((entry) => entry.fingerprint === 'fp-0001')
    expect(found).toEqual(updatedP1)
  })

  it('inserts a peer whose fingerprint was never seen before', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const p1 = peer({ fingerprint: 'fp-0001', name: 'unit-one' })
    fake.emitMessage(peersListEnvelope([p1]))
    const p3 = peer({ fingerprint: 'fp-0003', name: 'unit-three' })
    fake.emitMessage(peerDetectedEnvelope(p3))
    const result = get(peers)
    expect(result).toHaveLength(2)
    expect(result.some((entry) => entry.fingerprint === 'fp-0003')).toBe(true)
  })

  it('keeps two peers that share a name but not a fingerprint as two distinct entries', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const shared1 = peer({ fingerprint: 'fp-aaaa', name: 'shared-name' })
    const shared2 = peer({ fingerprint: 'fp-bbbb', name: 'shared-name' })
    fake.emitMessage(peerDetectedEnvelope(shared1))
    fake.emitMessage(peerDetectedEnvelope(shared2))
    const result = get(peers)
    expect(result).toHaveLength(2)
    // SPEC 4.4.1 leaves list order to the view, so this checks membership,
    // not position.
    expect(result.map((entry) => entry.fingerprint).sort()).toEqual([
      'fp-aaaa',
      'fp-bbbb',
    ])
  })

  it("never matches on the default identity '???': two peers that both report it are two entries, not one collapsed row", () => {
    const fake = new FakeWsClient()
    mount(fake)
    const anonymousOne = peer({
      fingerprint: '???',
      name: 'unit-one',
      rssi: -50,
    })
    const anonymousTwo = peer({
      fingerprint: '???',
      name: 'unit-two',
      rssi: -80,
    })
    fake.emitMessage(peerDetectedEnvelope(anonymousOne))
    fake.emitMessage(peerDetectedEnvelope(anonymousTwo))
    const result = get(peers)
    expect(result).toHaveLength(2)
    expect(result).toContainEqual(anonymousOne)
    expect(result).toContainEqual(anonymousTwo)
  })
})

describe('log: replaces, never appends', () => {
  it('holds only the lines of the second log_lines reply, not the union of both', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(logLinesEnvelope(['line one', 'line two']))
    expect(get(log)).toEqual(['line one', 'line two'])
    fake.emitMessage(logLinesEnvelope(['line three']))
    expect(get(log)).toEqual(['line three'])
  })
})

describe('gps: written by gps_update and by the gps field of stats, whichever arrived later', () => {
  it('takes a later gps_update over an earlier stats reading', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statsEnvelope({ gps: gpsReading({ lat: 1, lon: 1 }) }))
    const later = gpsReading({ lat: 2, lon: 2 })
    fake.emitMessage(gpsUpdateEnvelope(later))
    expect(get(gps)).toEqual(later)
  })

  it('takes a later stats reading over an earlier gps_update', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(gpsUpdateEnvelope(gpsReading({ lat: 1, lon: 1 })))
    const later = gpsReading({ lat: 3, lon: 3 })
    fake.emitMessage(statsEnvelope({ gps: later }))
    expect(get(gps)).toEqual(later)
  })
})

describe('face: face_status writes the whole shape, status_change writes only its status half', () => {
  it('holds the whole face_status payload', () => {
    const fake = new FakeWsClient()
    mount(fake)
    const entry = faceStatusData({
      face: '(o_o)',
      status: 'scanning',
      mode: 'AUTO',
    })
    fake.emitMessage(faceStatusEnvelope(entry))
    expect(get(face)).toEqual(entry)
  })

  it('a status_change updates only the status text, leaving face and mode as they were', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(
      faceStatusEnvelope(
        faceStatusData({ face: '(o_o)', status: 'scanning', mode: 'AUTO' }),
      ),
    )
    fake.emitMessage(statusChangeEnvelope('feeling lonely', 'lonely'))
    expect(get(face)).toEqual({
      face: '(o_o)',
      status: 'feeling lonely',
      mode: 'AUTO',
    })
  })

  it('a status_change with no face_status ever received is dropped, not turned into a partial shape', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statusChangeEnvelope('bored text', 'bored'))
    expect(get(face)).toBeNull()
  })
})

describe('messages with no writer of their own: reach onMessage, touch nothing, throw nothing', () => {
  it('keepalive, pong, acknowledgment, error, screen_image and restarting leave every store untouched', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    const before = snapshotData()
    const inertMessages: OutgoingMessage[] = [
      keepaliveEnvelope(),
      pongEnvelope(),
      acknowledgmentEnvelope('get_stats'),
      errorEnvelope('bad_request'),
      screenImageEnvelope(),
      restartingEnvelope('reboot', null),
    ]
    for (const message of inertMessages) {
      expect(() => fake.emitMessage(message)).not.toThrow()
    }
    expect(snapshotData()).toEqual(before)
  })
})

// ---------------------------------------------------------------------------
// 4.4.2 - what survives a reconnect.
// ---------------------------------------------------------------------------

function populateEverything(fake: FakeWsClient): void {
  fake.emitMessage(wifiUpdateEnvelope([accessPoint()]))
  fake.emitMessage(handshakesListEnvelope([handshakeEntry()]))
  fake.emitMessage(peersListEnvelope([peer()]))
  fake.emitMessage(logLinesEnvelope(['line one']))
  fake.emitMessage(statsEnvelope())
  fake.emitMessage(gpsUpdateEnvelope(gpsReading()))
  fake.emitMessage(faceStatusEnvelope(faceStatusData()))
  fake.emitMessage(channelHopEnvelope(11))
}

function snapshotData(): Record<string, unknown> {
  return {
    accessPoints: get(accessPoints),
    handshakes: get(handshakes),
    peers: get(peers),
    log: get(log),
    stats: get(stats),
    gps: get(gps),
    face: get(face),
    capabilities: get(capabilities),
    channel: get(channel),
  }
}

describe('a drop and a reconnect leave the data stores untouched', () => {
  it('nothing is cleared on offline - the transition most likely to be added by mistake', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    const before = snapshotData()
    fake.emitState('offline')
    expect(snapshotData()).toEqual(before)
  })

  it('a full drop-and-reconnect cycle (offline, connecting, connected) leaves the data as it was', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    const before = snapshotData()
    fake.emitState('offline')
    fake.emitState('connecting')
    fake.emitState('connected')
    expect(snapshotData()).toEqual(before)
  })
})

describe('unauthorized clears every data store', () => {
  it('clears accessPoints, handshakes, peers, log, stats, gps, face, capabilities and channel', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    fake.emitState('unauthorized', 'rejected')
    expect(get(accessPoints)).toEqual([])
    expect(get(handshakes)).toEqual(handshakesPayload([]))
    expect(get(peers)).toEqual([])
    expect(get(log)).toEqual([])
    expect(get(stats)).toBeNull()
    expect(get(gps)).toBeNull()
    expect(get(face)).toBeNull()
    expect(get(capabilities)).toBeNull()
    expect(get(channel)).toBeNull()
  })

  it('the connection store keeps reporting unauthorized itself - clearing is about the data, not the mirror', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    fake.emitState('unauthorized', 'required')
    expect(get(connection)).toEqual({
      state: 'unauthorized',
      unauthorizedReason: 'required',
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })
})

describe("close(): indistinguishable from a dropped tether on the client's own surface", () => {
  it('an offline transition right after data was populated does not clear it, whether or not it came from close()', () => {
    // The adapter cannot tell a close() apart from an ordinary drop; both
    // land on the same offline state. This is the same rule as the
    // drop-and-reconnect block above, restated for close() specifically so
    // a change that starts special-casing "the client asked to close" is
    // caught even if the offline test above is ever narrowed.
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    const before = snapshotData()
    fake.close()
    fake.emitState('offline')
    expect(snapshotData()).toEqual(before)
  })
})

describe('resetStores: the explicit clear a caller reaches for around close()', () => {
  it('clears every data store when called directly, and leaves connection alone', () => {
    const fake = new FakeWsClient()
    mount(fake)
    populateEverything(fake)
    fake.emitState('connected')
    resetStores()
    expect(get(accessPoints)).toEqual([])
    expect(get(handshakes)).toEqual(handshakesPayload([]))
    expect(get(peers)).toEqual([])
    expect(get(log)).toEqual([])
    expect(get(stats)).toBeNull()
    expect(get(gps)).toBeNull()
    expect(get(face)).toBeNull()
    expect(get(capabilities)).toBeNull()
    expect(get(channel)).toBeNull()
    // SPEC 4.4.2: connection is a mirror of the client (4.4.1); resetStores
    // must not report offline while the client is still connected, which
    // would be the second opinion that row forbids.
    expect(get(connection)).toEqual({
      state: 'connected',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })

  it('is safe to call with no client ever connected, and every data store starts from that same cleared shape', () => {
    resetStores()
    expect(get(accessPoints)).toEqual([])
    expect(get(handshakes)).toEqual(handshakesPayload([]))
    expect(get(peers)).toEqual([])
    expect(get(log)).toEqual([])
    expect(get(stats)).toBeNull()
    expect(get(gps)).toBeNull()
    expect(get(face)).toBeNull()
    expect(get(capabilities)).toBeNull()
    expect(get(channel)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// connectStores: at most one client at a time.
// ---------------------------------------------------------------------------

describe('connectStores: refuses a second attachment', () => {
  it('throws when called again before the previous attachment tears down', () => {
    const fake1 = new FakeWsClient()
    mount(fake1)
    const fake2 = new FakeWsClient()
    expect(() => connectStores(asClient(fake2))).toThrow()
    // The first attachment is still the live one: its handlers were never
    // removed by the refused second call.
    expect(fake1.stateHandlers.size).toBeGreaterThan(0)
    expect(fake2.stateHandlers.size).toBe(0)
  })

  it('a stale teardown from a previous client does not disarm the guard for the one now attached', () => {
    const clientA = new FakeWsClient()
    mount(clientA)
    const stopA = teardown as () => void
    stopA() // A tears down normally; the guard is released
    teardown = null
    const clientB = new FakeWsClient()
    mount(clientB) // B attaches; the guard now points to B
    stopA() // stale: A's own teardown, called a second time
    // B is still the one attached: a third attach is still refused, and B's
    // own subscriptions are still live.
    const clientC = new FakeWsClient()
    expect(() => connectStores(asClient(clientC))).toThrow()
    clientB.emitMessage(statsEnvelope({ uptime: 42 }))
    expect(get(stats)?.uptime).toBe(42)
  })

  it('calling the same teardown twice is harmless', () => {
    const clientA = new FakeWsClient()
    mount(clientA)
    const stopA = teardown as () => void
    expect(() => {
      stopA()
      stopA()
    }).not.toThrow()
    teardown = null
    // The guard was released by the first call, so a fresh attach succeeds.
    const clientB = new FakeWsClient()
    expect(() => mount(clientB)).not.toThrow()
  })

  it('teardown, resetStores, then attach is the documented way to switch hosts', () => {
    const fake1 = new FakeWsClient()
    mount(fake1)
    populateEverything(fake1)
    const stop = teardown as () => void
    stop()
    resetStores()
    const fake2 = new FakeWsClient()
    mount(fake2)
    // The new host starts clean: the previous unit's data is not left on
    // screen under the new one (SPEC 4.4.2).
    expect(get(accessPoints)).toEqual([])
    expect(get(peers)).toEqual([])
    expect(get(handshakes)).toEqual(handshakesPayload([]))
    fake2.emitMessage(peersListEnvelope([peer({ fingerprint: 'fp-new-host' })]))
    expect(get(peers)).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// connectStores teardown.
// ---------------------------------------------------------------------------

describe('connectStores: teardown unsubscribes everything', () => {
  it('removes every handler this call registered on the client', () => {
    const fake = new FakeWsClient()
    mount(fake)
    expect(fake.stateHandlers.size).toBeGreaterThan(0)
    expect(fake.messageHandlers.size).toBeGreaterThan(0)
    const stop = teardown as () => void
    stop()
    teardown = null
    expect(fake.stateHandlers.size).toBe(0)
    expect(fake.messageHandlers.size).toBe(0)
  })

  it('a message fired after teardown no longer reaches the stores', () => {
    const fake = new FakeWsClient()
    mount(fake)
    fake.emitMessage(statsEnvelope({ uptime: 1 }))
    expect(get(stats)?.uptime).toBe(1)
    const stop = teardown as () => void
    stop()
    teardown = null
    fake.emitMessage(statsEnvelope({ uptime: 2 }))
    expect(get(stats)?.uptime).toBe(1)
  })
})
