import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WsClientOptions, WsDeps } from '../lib/ws'
import { createWsClient } from '../lib/ws'

// Written from SPEC.md §4.3.11 (issue #109), plus §4.3.1 (the state machine),
// §4.3.9 (the tokenless queue-unblocking rule) and §4.3.10 (the diagnostics
// surface) for the rules a dropped frame must not have triggered. Also from
// docs/schemas/, never from lib/ws.ts or lib/protocol.ts, which are authored
// in parallel.
//
// Harness borrowed from ws.spec.ts (FakeSocket, makeDeps/setup, nth,
// firstStats/toConnected) rather than imported from it, since that file is
// not a shared module - kept to the pieces this file actually needs.
//
// Left unstated deliberately (§4.3.11 requires it but it is not observable
// from this surface): the guard rejects a JSON Schema construct the
// generator cannot express as a hard build error, not a runtime behaviour of
// WsClient - that is tools/gen-protocol-types.mjs's contract, out of scope
// for a client-behaviour suite exercised through createWsClient.

// ---------------------------------------------------------------------------
// A hand-driven fake socket, copied from ws.spec.ts's own comment on why:
// no real network, no real waiting, every state change caused by an
// explicit call from the test.
// ---------------------------------------------------------------------------

class FakeSocket {
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  onerror: ((error: unknown) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  send(data: string): void {
    if (this.closed) throw new Error('FakeSocket.send() called after close()')
    this.sent.push(data)
  }

  close(): void {
    this.closed = true
  }

  open(): void {
    this.onopen?.()
  }

  /** The server pushed a frame - a plain object, deliberately possibly malformed. */
  push(envelope: unknown): void {
    this.onmessage?.({ data: JSON.stringify(envelope) })
  }

  /** The server pushed bytes that are not a well-formed envelope at all - not even JSON, or JSON that does not parse to an object. */
  pushRaw(data: string): void {
    this.onmessage?.({ data })
  }

  serverClose(code: number, reason = ''): void {
    this.closed = true
    this.onclose?.({ code, reason })
  }

  parsedSent(): Array<Record<string, unknown>> {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>)
  }

  sentOf(type: string): Array<Record<string, unknown>> {
    return this.parsedSent().filter((m) => m['type'] === type)
  }
}

function makeDeps(overrides: Partial<WsDeps> = {}): {
  deps: WsDeps
  sockets: FakeSocket[]
} {
  const sockets: FakeSocket[] = []
  const deps: WsDeps = {
    createSocket: (_url: string) => {
      const socket = new FakeSocket()
      sockets.push(socket)
      return socket
    },
    random: () => 0.5,
    now: () => Date.now(),
    ...overrides,
  }
  return { deps, sockets }
}

function nth<T>(list: readonly T[], index: number): T {
  const item = list[index]
  if (item === undefined)
    throw new Error(
      `expected an element at index ${index}, only ${list.length} present`,
    )
  return item
}

function setup(optionOverrides: Partial<Omit<WsClientOptions, 'deps'>> = {}) {
  const { deps, sockets } = makeDeps()
  const client = createWsClient({
    url: 'wss://192.0.2.1:8082/ws',
    ...optionOverrides,
    deps,
  })
  return { client, sockets, deps }
}

/** Opens the newest socket and delivers a first well-formed `stats` frame. */
function firstStats(sockets: FakeSocket[], sessionAge: number | null): FakeSocket {
  const socket = nth(sockets, sockets.length - 1)
  socket.open()
  socket.push(validStatsEnvelope({ sessionAge }))
  return socket
}

function toConnected(sockets: FakeSocket[]): FakeSocket {
  return firstStats(sockets, 5)
}

const BACKOFF_CAPS_S = [1, 2, 4, 8, 16, 30, 30]

function waitOutBackoff(capSeconds: number): void {
  vi.advanceTimersByTime(capSeconds * 1000)
}

// ---------------------------------------------------------------------------
// Schema-shaped fixture builders, built directly from docs/schemas/ - never
// from the generated protocol.ts guards under test. Each "data" builder
// returns a plain object so a test can corrupt one field at a time and keep
// the rest schema-valid, which is what proves a drop was caused by the field
// under test and not by some other accidental defect in the fixture.
// ---------------------------------------------------------------------------

function validGpsData(): Record<string, unknown> {
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
  }
}

function validBattery(): Record<string, unknown> {
  return { percent: 80, charging: false }
}

function validCapabilities(): Record<string, unknown> {
  return { pasv: true, pisugar: false, gpsSource: 'none', pluginVersion: '0.1.0' }
}

function validStatsData(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    uptime: 120,
    mode: 'AUTO',
    channel: 6,
    battery: validBattery(),
    temperature: 45,
    handshakes: 3,
    handshakesTotal: 4,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: validGpsData(),
    sessionAge: 5,
    capabilities: validCapabilities(),
    ...overrides,
  }
}

function validStatsEnvelope(
  dataOverrides: Record<string, unknown> = {},
  envelopeOverrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: 'stats',
    timestamp: 1700000000,
    data: validStatsData(dataOverrides),
    ...envelopeOverrides,
  }
}

function validRestartingEnvelope(
  reason: string,
  mode: string | null,
): Record<string, unknown> {
  return {
    type: 'restarting',
    timestamp: 1700000000,
    data: { reason, mode },
  }
}

function validAccessPoint(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    bssid: 'AA:BB:CC:DD:EE:FF',
    hostname: 'TestNet_001',
    channel: 6,
    rssi: -50,
    encryption: 'WPA2',
    vendor: 'TestVendor',
    clients: 1,
    ...overrides,
  }
}

function validAccessPointsEnvelope(
  entries: Record<string, unknown>[],
  envelopeOverrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: 'access_points',
    timestamp: 1700000000,
    data: entries,
    ...envelopeOverrides,
  }
}

function ackEnvelope(
  acknowledged: string,
  message_id?: string,
): Record<string, unknown> {
  return {
    type: 'acknowledgment',
    timestamp: 1700000000,
    data: { acknowledged },
    ...(message_id !== undefined ? { message_id } : {}),
  }
}

function withMessageId<T extends Record<string, unknown>>(
  envelope: T,
  message_id: string,
): T {
  return { ...envelope, message_id }
}

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// An unknown `type` is dropped.
// ---------------------------------------------------------------------------

describe('§4.3.11: an unrecognised type is dropped', () => {
  it('a well-formed envelope whose type is not a known outgoing type never reaches a message subscriber, never moves the state, and is counted', () => {
    const { client, sockets } = setup()
    const messages: unknown[] = []
    client.onMessage((m) => messages.push(m))
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    s.push({ type: 'not_a_real_outgoing_type', timestamp: 1700000000, data: {} })

    expect(client.state()).toBe('connecting') // never admitted: this was not a frame
    expect(messages).toHaveLength(0)
    expect(client.diagnostics().droppedFrames).toBe(1)

    // Positive control: a known type, otherwise identical in shape, is not
    // dropped - proving the assertions above test the type check and not a
    // client that drops everything it is given.
    s.push(validStatsEnvelope({ sessionAge: 5 }))
    expect(client.state()).toBe('connected')
    expect(messages).toHaveLength(1)
    expect(client.diagnostics().droppedFrames).toBe(1) // unchanged by the valid frame
  })
})

// ---------------------------------------------------------------------------
// Bytes that are not a well-formed envelope at all - not even JSON, or JSON
// that does not parse to an object - are dropped and counted the same as a
// frame that failed its guard (SPEC 4.3.11, amended for #109: "a frame that
// is not JSON at all, or not an object, is dropped and counted the same").
// ---------------------------------------------------------------------------

describe('§4.3.11: a frame that is not JSON at all, or not an object, is dropped and counted the same as a guard failure', () => {
  it('bytes that do not parse as JSON at all are dropped, counted, and recorded as bad_frame', () => {
    const { client, sockets } = setup()
    const messages: unknown[] = []
    client.onMessage((m) => messages.push(m))
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    expect(() => s.pushRaw('this is not json at all')).not.toThrow()

    expect(client.state()).toBe('connecting')
    expect(messages).toHaveLength(0)
    expect(client.diagnostics().droppedFrames).toBe(1)
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'bad_frame',
    })

    // Positive control.
    s.push(validStatsEnvelope({ sessionAge: 5 }))
    expect(client.state()).toBe('connected')
    expect(messages).toHaveLength(1)
  })

  it('well-formed JSON that does not parse to an object (a bare number, string, array or null) is dropped and counted the same way', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    for (const notAnObject of ['42', '"just a string"', '[1, 2, 3]', 'null']) {
      expect(() => s.pushRaw(notAnObject)).not.toThrow()
    }

    expect(client.state()).toBe('connecting')
    expect(client.diagnostics().droppedFrames).toBe(4)
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'bad_frame',
      message: '',
    })

    // Positive control.
    s.push(validStatsEnvelope({ sessionAge: 5 }))
    expect(client.state()).toBe('connected')
  })
})

// ---------------------------------------------------------------------------
// `isRecord` rejects arrays: `typeof [] === 'object'` used to let an array
// pass every guard whose `data` has no required keys to check, since the
// guard for such a shape had nothing left to fail on once the record check
// itself waved an array through. Picked `pong`, whose `data` schema is
// `{type: object, properties: {}, required: []}` - exactly the shape with
// no other check standing between an array and a false pass.
// ---------------------------------------------------------------------------

describe("§4.3.11: an array where an object is required (isRecord rejects arrays, issue #109) - 'pong', whose data has no required keys to fall back on", () => {
  it('data: [] is dropped rather than passed as an empty object', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const messages: unknown[] = []
    client.onMessage((m) => messages.push(m)) // registered only after admission, so the admitting stats itself is not counted below

    s.push({ type: 'pong', timestamp: 1700000000, data: [] })

    expect(messages).toHaveLength(0)
    expect(client.diagnostics().droppedFrames).toBe(1)

    // Positive control: the same type, with data as an actual (empty)
    // object, is not dropped.
    s.push({ type: 'pong', timestamp: 1700000000, data: {} })
    expect(messages).toHaveLength(1)
    expect(client.diagnostics().droppedFrames).toBe(1) // unchanged by the valid frame
  })
})

// ---------------------------------------------------------------------------
// Schema-invalid payloads are dropped: one test per named failure shape.
// ---------------------------------------------------------------------------

describe('§4.3.11: a schema-invalid payload is dropped', () => {
  it('a missing required key (stats.data.sessionAge absent) is dropped, not read as connected or degraded', () => {
    const { client, sockets } = setup()
    const messages: unknown[] = []
    client.onMessage((m) => messages.push(m))
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    const data = validStatsData()
    delete data['sessionAge']
    s.push({ type: 'stats', timestamp: 1700000000, data })

    expect(client.state()).toBe('connecting')
    expect(messages).toHaveLength(0)
    expect(client.diagnostics().droppedFrames).toBe(1)
  })

  it('a key of the wrong type (stats.data.handshakes as a string) is dropped', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    s.push(validStatsEnvelope({ handshakes: '3' }))

    expect(client.state()).toBe('connecting')
    expect(client.diagnostics().droppedFrames).toBe(1)

    // Positive control.
    s.push(validStatsEnvelope({ sessionAge: 5 }))
    expect(client.state()).toBe('connected')
  })

  it('an enum value outside the enum (restarting.data.reason) is dropped, and the following close is handled by the ordinary close rules', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)

    s.push({
      type: 'restarting',
      timestamp: 1700000000,
      data: { reason: 'unplugged', mode: null },
    })

    expect(client.state()).toBe('connected') // never saw a restarting message at all
    expect(client.diagnostics().droppedFrames).toBe(1)

    // The close that follows is now an ordinary, unannounced close: from
    // `connected` that is `offline` (§4.3.1), not the suppressed-banner
    // `restarting` path a valid frame would have produced.
    s.serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })

  it('a nullable field given a non-null wrong type (stats.data.mode as a number) is dropped', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    s.push(validStatsEnvelope({ mode: 123 }))

    expect(client.state()).toBe('connecting')
    expect(client.diagnostics().droppedFrames).toBe(1)
  })

  it('an array element of the wrong shape (an access_points entry missing bssid) is dropped and does not resolve the pending read', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_access_points')
    const messageId = s.sentOf('get_access_points')[0]?.['message_id'] as string
    expect(messageId).toBeTruthy()

    const badEntry = validAccessPoint()
    delete badEntry['bssid']
    s.push(
      withMessageId(
        validAccessPointsEnvelope([validAccessPoint(), badEntry]),
        messageId,
      ),
    )

    let settled = false
    void pending.then(
      () => {
        settled = true
      },
      () => {
        settled = true
      },
    )
    await Promise.resolve()
    await Promise.resolve()
    expect(settled).toBe(false) // the invalid reply did not resolve, nor reject, the pending request
    expect(client.diagnostics().droppedFrames).toBe(1)

    // Positive control: the same request, correlated the same way, resolves
    // normally once a schema-valid reply for it arrives.
    s.push(
      withMessageId(validAccessPointsEnvelope([validAccessPoint()]), messageId),
    )
    await expect(pending).resolves.toMatchObject({ type: 'access_points' })
  })

  it('a stats with a non-numeric sessionAge is dropped rather than read as degraded (the second case the issue names)', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    s.push(validStatsEnvelope({ sessionAge: 'a lot' }))

    expect(client.state()).toBe('connecting') // not degraded: this was not read as stale data
    expect(client.diagnostics().droppedFrames).toBe(1)

    // The visible consequence §4.3.11 states plainly: a unit emitting only
    // malformed stats leaves `connecting` to expire into `offline` at 55s.
    vi.advanceTimersByTime(55000)
    expect(client.state()).toBe('offline')
  })
})

// ---------------------------------------------------------------------------
// An unknown extra key is forward compatibility, not a defect - accepted.
// ---------------------------------------------------------------------------

describe('§4.3.11: an unknown extra key is accepted, deliberately', () => {
  it('an extra top-level key and an extra key inside data do not cause a drop', () => {
    const { client, sockets } = setup()
    const messages: unknown[] = []
    client.onMessage((m) => messages.push(m))
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    s.push(
      validStatsEnvelope(
        { fieldFromANewerPlugin: 'unrecognised' },
        { anotherNewField: 42 },
      ),
    )

    expect(client.state()).toBe('connected')
    expect(messages).toHaveLength(1)
    expect(client.diagnostics().droppedFrames).toBe(0)
  })
})

// ---------------------------------------------------------------------------
// An invalid frame never counts as the first `stats` of the connection, and
// never opens the tokenless queue-unblocking gate (§4.3.9).
// ---------------------------------------------------------------------------

describe('§4.3.11: an invalid frame does nothing an invalid frame could have done', () => {
  it('a schema-invalid stats does not open the tokenless queue-unblocking gate (§4.3.9), a valid one does', () => {
    const { client, sockets } = setup() // no token: the tokenless path
    client.connect()
    void client.request('get_peers')
    const s = nth(sockets, 0)
    s.open()
    expect(s.sent).toHaveLength(0) // nothing sent yet, socket open but unit silent so far

    s.push(validStatsEnvelope({ sessionAge: 'not a number' })) // schema-invalid: not a frame
    expect(s.sent).toHaveLength(0) // still queued - this did not count as the unit's first word

    s.push(validStatsEnvelope({ sessionAge: 5 })) // positive control: a valid frame does open it
    expect(s.sentOf('get_peers')).toHaveLength(1)
  })

  it('an invalid frame does not count as the first stats of a restarting connection either (§4.3.1 row: restarting -> first stats of a new connection)', () => {
    const { client, sockets } = setup()
    client.connect()
    const s1 = toConnected(sockets)
    s1.push(validRestartingEnvelope('reboot', null))
    expect(client.state()).toBe('restarting')

    s1.serverClose(1006, '') // the restart's own close; state survives it (§4.3.1)
    const s2 = nth(sockets, sockets.length - 1)
    s2.open()

    s2.push(validStatsEnvelope({ handshakes: 'nope' })) // invalid: must not admit
    expect(client.state()).toBe('restarting')
    expect(client.diagnostics().droppedFrames).toBeGreaterThanOrEqual(1)

    s2.push(validStatsEnvelope({ sessionAge: 5 })) // positive control
    expect(client.state()).toBe('connected')
  })
})

// ---------------------------------------------------------------------------
// §4.3.10 - droppedFrames and bad_frame's LastError.
// ---------------------------------------------------------------------------

describe('§4.3.11 + §4.3.10: diagnostics for a dropped frame', () => {
  it('droppedFrames starts at 0 and is monotonic across a reconnection, never reset by it', () => {
    const { client, sockets } = setup()
    client.connect()
    expect(client.diagnostics().droppedFrames).toBe(0) // baseline, before any traffic at all

    const s1 = toConnected(sockets)
    s1.push({ type: 'still_not_a_real_type', timestamp: 1700000000, data: {} })
    s1.push({ type: 'also_not_real', timestamp: 1700000000, data: {} })
    expect(client.diagnostics().droppedFrames).toBe(2)

    s1.serverClose(1006, '')
    expect(client.state()).toBe('offline') // §4.3.1: connected -> close -> offline
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    const s2 = nth(sockets, sockets.length - 1)
    s2.open()
    s2.push(validStatsEnvelope({ sessionAge: 5 })) // re-admitted
    expect(client.state()).toBe('connected')

    expect(client.diagnostics().droppedFrames).toBe(2) // the reconnection did not reset it

    s2.push({ type: 'yet_another_unknown_type', timestamp: 1700000000, data: {} })
    expect(client.diagnostics().droppedFrames).toBe(3) // and it keeps climbing afterwards
  })

  it('records LastError {source: "local", code: "bad_frame"} for a dropped frame, with no message - there is no remote to have said anything (SPEC 4.3.10, amended for #109)', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)

    expect(client.diagnostics().lastError).toBeNull() // baseline

    s.push({ type: 'unrecognised_type_here', timestamp: 1700000000, data: {} })

    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'bad_frame',
      message: '',
    })
  })

  it('the bad_frame LastError survives a valid frame on the same, already-admitted connection (cleared on the admission transition, not on re-entry, §4.3.10)', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets) // already admitted

    s.push({ type: 'unrecognised_type_here', timestamp: 1700000000, data: {} })
    expect(client.diagnostics().lastError).toMatchObject({ code: 'bad_frame' })

    s.push(validStatsEnvelope({ sessionAge: 6 })) // re-entering `connected`, not a new admission
    expect(client.diagnostics().lastError).toMatchObject({ code: 'bad_frame' })
  })

  it('the bad_frame LastError is cleared once a new connection is admitted', () => {
    const { client, sockets } = setup()
    client.connect()
    const s1 = toConnected(sockets)
    s1.push({ type: 'unrecognised_type_here', timestamp: 1700000000, data: {} })
    expect(client.diagnostics().lastError).toMatchObject({ code: 'bad_frame' })

    s1.serverClose(1006, '')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    const s2 = nth(sockets, sockets.length - 1)
    s2.open()
    s2.push(validStatsEnvelope({ sessionAge: 5 })) // admits the new connection

    expect(client.diagnostics().lastError).toBeNull()
  })

  // SPEC 4.3.11 (issue #109): "The try is scoped to JSON.parse alone.
  // Widening it to the guard below would also catch whatever
  // recordLocalError's call into notifyDiagnostics can throw ... and a
  // throw from there would land in this same catch and count one frame as
  // dropped twice." A throwing onDiagnostics subscriber is the one thing
  // that reaches inside that former scope, so it is the only fixture that
  // can tell the fixed boundary from the old, wider one: with the try
  // narrowed to JSON.parse, the subscriber's throw is no longer caught by
  // it at all and escapes the call that triggered it, but droppedFrames
  // must still have advanced by exactly one, not two, for the one frame
  // that was actually dropped.
  it('a throwing onDiagnostics subscriber does not cause a single dropped frame to be counted twice', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    client.onDiagnostics(() => {
      throw new Error('a diagnostics subscriber bug must not double-count')
    })

    expect(() =>
      s.push({ type: 'not_a_real_type_for_double_count', timestamp: 1700000000, data: {} }),
    ).toThrow() // not isolated the way the message-handler loop is (SPEC 4.3.11)

    expect(client.diagnostics().droppedFrames).toBe(1)
  })
})

// ---------------------------------------------------------------------------
// A well-formed frame still works, end to end, exercised once on its own as
// the suite-wide positive control rather than only folded into each area
// above.
// ---------------------------------------------------------------------------

describe('§4.3.11: positive control - a well-formed frame is unaffected by the boundary check', () => {
  it('acknowledgment and a correlated typed reply both still resolve a request normally', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_access_points')
    const messageId = s.sentOf('get_access_points')[0]?.['message_id'] as string
    s.push(withMessageId(ackEnvelope('get_access_points'), messageId))
    s.push(
      withMessageId(validAccessPointsEnvelope([validAccessPoint()]), messageId),
    )
    await expect(pending).resolves.toMatchObject({ type: 'access_points' })
    expect(client.diagnostics().droppedFrames).toBe(0)
  })
})
