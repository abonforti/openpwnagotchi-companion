import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  Battery,
  Capabilities,
  ErrorCode,
  Gps,
  Mode,
  OutgoingAcknowledgment,
  OutgoingError,
  OutgoingPong,
  OutgoingRestarting,
  OutgoingStats,
  RestartReason,
  Stats,
} from '../lib/protocol'
import type {
  ConnectionState,
  Diagnostics,
  UnauthorizedReason,
  WsClientOptions,
  WsDeps,
} from '../lib/ws'
import { createWsClient, readStoredToken, storeToken } from '../lib/ws'

// Written from SPEC.md §4.3 and its subsections, docs/schemas/, and the
// public surface of lib/ws.ts - never from its implementation. Three rounds
// of review have since moved both the spec and the client under this file;
// each pass is folded in directly rather than kept as a changelog here.
//
// Timings pinned by §4.3.2: 15s ping, 10s pong timeout, 15s request timeout,
// 20s server broadcast cadence, 55s staleness/connecting bound, backoff caps
// 1/2/4/8/16/30/30s. Every test below uses vitest fake timers so none of that
// costs wall-clock time, and advances by "at least the worst-case delay"
// rather than an exact scheduled instant wherever the exact instant is not
// itself the thing under test - full jitter promises a delay in [0, cap], not
// a delay at a particular millisecond, and a test that assumes the latter is
// testing random() rather than the client (SPEC 10.5).

// ---------------------------------------------------------------------------
// Fixture builders - schema-shaped envelopes, built from docs/schemas and
// frontend/src/lib/protocol.ts, never from the implementation under test.
// ---------------------------------------------------------------------------

function defaultGps(): Gps {
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

function defaultBattery(): Battery {
  return { percent: 80, charging: false }
}

function defaultCapabilities(): Capabilities {
  return {
    pasv: true,
    pisugar: false,
    gpsSource: 'none',
    pluginVersion: '0.1.0',
  }
}

function defaultStats(): Stats {
  return {
    uptime: 120,
    mode: 'AUTO',
    channel: 6,
    battery: defaultBattery(),
    temperature: 45.2,
    handshakes: 3,
    handshakesTotal: 4,
    peers: 1,
    accessPoints: 5,
    lastHandshake: null,
    lastPeer: null,
    gps: defaultGps(),
    sessionAge: 5,
    capabilities: defaultCapabilities(),
  }
}

/** sessionAge is the one field every staleness test drives directly. */
function statsEnvelope(
  sessionAge: number | null,
  overrides: Partial<Stats> = {},
): OutgoingStats {
  return {
    type: 'stats',
    timestamp: 1700000000,
    data: { ...defaultStats(), sessionAge, ...overrides },
  }
}

function errorEnvelope(
  code: ErrorCode,
  message = 'error',
  message_id?: string,
): OutgoingError {
  return {
    type: 'error',
    timestamp: 1700000000,
    data: { code, message },
    ...(message_id !== undefined ? { message_id } : {}),
  }
}

function restartingEnvelope(
  reason: RestartReason,
  mode: Mode | null,
): OutgoingRestarting {
  return { type: 'restarting', timestamp: 1700000000, data: { reason, mode } }
}

function ackEnvelope(
  acknowledged: string,
  message_id?: string,
): OutgoingAcknowledgment {
  return {
    type: 'acknowledgment',
    timestamp: 1700000000,
    data: { acknowledged },
    ...(message_id !== undefined ? { message_id } : {}),
  }
}

function pongEnvelope(message_id?: string): OutgoingPong {
  return {
    type: 'pong',
    timestamp: 1700000000,
    data: {},
    ...(message_id !== undefined ? { message_id } : {}),
  }
}

/** Stamps message_id onto an envelope whose builder does not take one directly - used to make a `stats` push read as the typed reply to a specific `get_stats` (§4.3.8), not merely another broadcast. */
function withMessageId<T extends { message_id?: string }>(
  envelope: T,
  message_id: string,
): T {
  return { ...envelope, message_id }
}

// ---------------------------------------------------------------------------
// A hand-driven fake socket. No real network, no real waiting: every state
// change is caused by an explicit call from the test.
// ---------------------------------------------------------------------------

class FakeSocket {
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onclose: ((event: { code: number; reason: string }) => void) | null = null
  onerror: ((error: unknown) => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null

  send(data: string): void {
    // Simulates the DOM WebSocket's InvalidStateError from send() on a
    // socket already CLOSING/CLOSED. Since every
    // send in the client now goes through trySend, this throw no longer
    // escapes to fail a test on its own - it is swallowed exactly like a
    // real InvalidStateError would be. What proves the guard now is the
    // dedicated test below asserting nothing escapes AND nothing is
    // recorded, not this method throwing in some other, unrelated test by
    // accident. The throw is kept (rather than a silent no-op) because it
    // is the only way to prove trySend's catch is actually reached, not
    // merely never hit.
    if (this.closed) throw new Error('FakeSocket.send() called after close()')
    this.sent.push(data)
  }

  close(): void {
    this.closed = true
  }

  /** The server accepted the connection. */
  open(): void {
    this.onopen?.()
  }

  /** The server pushed a frame. */
  push(envelope: unknown): void {
    this.onmessage?.({ data: JSON.stringify(envelope) })
  }

  /** The server pushed bytes that are not a well-formed envelope at all - not even JSON, or JSON missing `type`. */
  pushRaw(data: string): void {
    this.onmessage?.({ data })
  }

  /** The server (or the transport) closed the connection. */
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
    // A fixed, unremarkable draw by default. Tests that care about the
    // jitter distribution override this explicitly.
    random: () => 0.5,
    // §4.3.10: WsDeps.now(), defaulting to the wall clock like the real
    // client's own default. Tests that assert a latency value override this
    // explicitly with an injected clock (makeClock below) - asserting a
    // duration against the default here would be asserting against how long
    // the test host took to run the test, which is exactly what injecting
    // the clock exists to avoid.
    now: () => Date.now(),
    ...overrides,
  }
  return { deps, sockets }
}

/** Indexed access that throws instead of silently typing as possibly-undefined, matching the project's noUncheckedIndexedAccess setting. A throw here means the test's own setup assumption was wrong, not the client under test. */
function nth<T>(list: readonly T[], index: number): T {
  const item = list[index]
  if (item === undefined)
    throw new Error(
      `expected an element at index ${index}, only ${list.length} present`,
    )
  return item
}

function setup(
  optionOverrides: Partial<Omit<WsClientOptions, 'deps'>> = {},
  depsOverrides: Partial<WsDeps> = {},
) {
  const { deps, sockets } = makeDeps(depsOverrides)
  const client = createWsClient({
    url: 'wss://192.0.2.1:8082/ws',
    ...optionOverrides,
    deps,
  })
  return { client, sockets, deps }
}

/** Opens the newest socket and delivers a first `stats` frame. */
function firstStats(
  sockets: FakeSocket[],
  sessionAge: number | null,
): FakeSocket {
  const socket = nth(sockets, sockets.length - 1)
  socket.open()
  socket.push(statsEnvelope(sessionAge))
  return socket
}

/** Drives a fresh client straight to `connected`. */
function toConnected(sockets: FakeSocket[]): FakeSocket {
  return firstStats(sockets, 5)
}

// Backoff caps in seconds, §4.3.2: "1, 2, 4, 8, 16, 30, 30, ..."
const BACKOFF_CAPS_S = [1, 2, 4, 8, 16, 30, 30]

/** Waits long enough to guarantee any full-jitter delay bounded by `cap` has elapsed, regardless of the draw. */
function waitOutBackoff(capSeconds: number): void {
  vi.advanceTimersByTime(capSeconds * 1000)
}

function clearStoredToken(): void {
  storeToken(null)
}

/**
 * A hand-driven clock for WsDeps.now() (§4.3.10). Independent of vitest's
 * fake setTimeout clock: a test advances this by exactly the amount it
 * advances the fake timers by, so a round trip measured against it is
 * asserted against a value the test controls completely, never against how
 * long the test host took to run - the whole reason §4.3.10 makes the clock
 * injectable at all.
 */
function makeClock(start = 1700000000000) {
  let value = start
  return {
    now: (): number => value,
    advance(ms: number): void {
      value += ms
    },
    value(): number {
      return value
    },
  }
}

function allStoredValues(): string[] {
  const values: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key !== null) values.push(localStorage.getItem(key) ?? '')
  }
  return values
}

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------
// §4.3.6 - where the token lives
// ---------------------------------------------------------------------------

describe('token storage (§4.3.6)', () => {
  it('round-trips through storeToken/readStoredToken', () => {
    storeToken('a-sample-token-value')
    expect(readStoredToken()).toBe('a-sample-token-value')

    storeToken(null)
    expect(readStoredToken()).toBeNull()
  })

  it('actually persists the token in localStorage rather than only in memory', () => {
    storeToken('a-persisted-token-value')
    expect(allStoredValues()).toContain('a-persisted-token-value')
  })

  it('sends {type: "auth", token} as the very first frame when a token is configured', () => {
    const { client, sockets } = setup({ token: 'configured-token-value' })
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sent).toHaveLength(1)
    expect(nth(sockets, 0).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'configured-token-value',
    })
  })

  it('a token-configured client sends nothing before its auth is acknowledged, then flushes the queue once it is (§4.3.1)', () => {
    const { client, sockets } = setup({ token: 'configured-token-value' })
    client.connect()
    const s = nth(sockets, 0)
    s.open()
    expect(s.parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'configured-token-value',
    })

    void client.request('get_stats')
    expect(s.sentOf('get_stats')).toHaveLength(0) // queued: the auth is not acknowledged yet

    s.push(ackEnvelope('auth'))
    expect(s.sentOf('get_stats')).toHaveLength(1) // the auth acknowledgment is what opens the gate
  })

  it('sends no auth frame at all when no token is configured (D5 default), and still reaches connected', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sentOf('auth')).toHaveLength(0)
    nth(sockets, 0).push(statsEnvelope(5))
    expect(client.state()).toBe('connected')
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.7: storage that refuses to answer does not stop the app. This is
// readStoredToken's own guard, reached from effectiveToken on every
// connect() call - a regression here would not fail at startup or at a
// settings write, since neither of those touches this function, but at the
// first connect() attempt after storage stops answering.
// ---------------------------------------------------------------------------

describe('a disabled localStorage does not stop readStoredToken or connect() (SPEC 4.7)', () => {
  it('storeToken survives a storage that refuses both the write and the clear', () => {
    // The fallback that clears the key when a write fails can itself throw,
    // which is why it carries its own guard. Nothing in the fake storage can
    // produce that, so it is spied here or it is never exercised at all: a
    // guard whose failure the coverage report cannot see is the shape SPEC
    // 10.7 asks to be named rather than assumed.
    const setSpy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new DOMException('quota', 'QuotaExceededError')
      })
    const removeSpy = vi
      .spyOn(Storage.prototype, 'removeItem')
      .mockImplementation(() => {
        throw new DOMException('disabled', 'SecurityError')
      })
    try {
      expect(() => storeToken('a-token-that-cannot-be-written')).not.toThrow()
    } finally {
      setSpy.mockRestore()
      removeSpy.mockRestore()
    }
  })

  it('readStoredToken returns null rather than throwing when storage is disabled', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('storage is disabled')
      })
    try {
      expect(() => readStoredToken()).not.toThrow()
      expect(readStoredToken()).toBeNull()
    } finally {
      spy.mockRestore()
    }
  })

  it('connect() does not throw when storage is disabled, and falls back to the tokenless path', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('storage is disabled')
      })
    try {
      const { client, sockets } = setup()
      expect(() => client.connect()).not.toThrow()
      nth(sockets, 0).open()
      expect(nth(sockets, 0).sentOf('auth')).toHaveLength(0)
      nth(sockets, 0).push(statsEnvelope(5))
      expect(client.state()).toBe('connected')
    } finally {
      spy.mockRestore()
    }
  })
})

// ---------------------------------------------------------------------------
// Two guards: onMessage subscriber isolation, and trySend swallowing a
// send against an already-closing/closed socket. Their
// catch bodies are empty, so an uncovered-branch report shows nothing for
// either - these are the tests that actually exercise them.
// ---------------------------------------------------------------------------

describe('guards: a throwing subscriber, and a send against an already-closed socket', () => {
  it('a throwing onMessage subscriber does not stop the frame from settling its own correlated promise', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const unsubscribe = client.onMessage(() => {
      throw new Error("a subscriber bug must not be this file's problem")
    })

    const pending = client.request('get_stats')
    const messageId = s.sentOf('get_stats')[0]?.['message_id'] as string
    expect(messageId).toBeTruthy()
    s.push(withMessageId(statsEnvelope(9), messageId))
    await expect(pending).resolves.toMatchObject({ type: 'stats' })

    unsubscribe()
  })

  it('a send against a socket the transport has already closed does not throw and records no frame', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    // The onclose event has not fired yet, but the transport has already
    // moved past OPEN - the window trySend exists for.
    s.closed = true

    expect(() =>
      client.sendGps({ latitude: 12.34, longitude: 56.78 }),
    ).not.toThrow()
    expect(s.sent).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// §4.3.1 - the connection state machine, one test per transition-table row
// ---------------------------------------------------------------------------

describe('state machine: connecting', () => {
  it('a malformed frame does not throw and does not admit a tokenless socket (§4.3.9): neither invalid JSON nor JSON missing "type" counts as the unit\'s first word', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open()

    expect(() => s.pushRaw('this is not json at all')).not.toThrow()
    expect(() =>
      s.pushRaw(JSON.stringify({ no: 'type field here' })),
    ).not.toThrow()
    expect(client.state()).toBe('connecting') // still waiting for a real admitting frame

    void client.request('get_stats')
    expect(s.sent).toHaveLength(0) // the queued read has not been flushed either
  })

  it('connecting -> connected: first stats of the connection, sessionAge under 55s and not null', () => {
    const { client, sockets } = setup()
    client.connect()
    firstStats(sockets, 10)
    expect(client.state()).toBe('connected')
  })

  it('connecting -> degraded: first stats of the connection, sessionAge 55s or more', () => {
    const { client, sockets } = setup()
    client.connect()
    firstStats(sockets, 55)
    expect(client.state()).toBe('degraded')
  })

  it('connecting -> degraded: first stats of the connection, sessionAge null', () => {
    const { client, sockets } = setup()
    client.connect()
    firstStats(sockets, null)
    expect(client.state()).toBe('degraded')
  })

  it('connecting -> offline: 55s elapse with no stats at all (the connecting bound)', () => {
    const { client } = setup()
    client.connect()
    vi.advanceTimersByTime(55000)
    expect(client.state()).toBe('offline')
  })

  it('connecting -> offline: not one instant early - just under 55s must not yet time out', () => {
    const { client } = setup()
    client.connect()
    vi.advanceTimersByTime(54999)
    expect(client.state()).not.toBe('offline')
  })

  it('connecting -> restarting: a restarting message arrives before any stats', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).push(restartingEnvelope('reboot', null))
    expect(client.state()).toBe('restarting')
  })

  it('connecting -> unauthorized (rejected): an error frame with code unauthorized, then close', () => {
    const { client, sockets } = setup({ token: 'wrong-token' })
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).push(errorEnvelope('unauthorized', 'bad token'))
    nth(sockets, 0).serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'rejected' satisfies UnauthorizedReason,
    )
  })

  it('connecting -> unauthorized (required): a bare 1008 with no auth frame ever sent', () => {
    const { client, sockets } = setup() // no token -> no auth frame is sent
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sentOf('auth')).toHaveLength(0)
    nth(sockets, 0).serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'required' satisfies UnauthorizedReason,
    )
  })

  it('connecting -> offline: a bare 1008 after an auth frame WAS sent (not unauthorized)', () => {
    const { client, sockets } = setup({ token: 'configured-token-value' })
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sentOf('auth')).toHaveLength(1)
    nth(sockets, 0).serverClose(1008, '')
    expect(client.state()).toBe('offline')
  })

  it('connecting -> offline: any other close code before any stats', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })

  it('connecting -> offline: the socket fails to open at all', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).onerror?.(new Error('connection refused'))
    // Regardless of how the implementation signals a failed open, it must not
    // hang in connecting forever - the 55s bound below already proves the
    // absolute worst case, this proves the fast path is not worse than it.
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })

  it('connecting -> unauthorized (required), by inference: three consecutive closes OF SOCKETS THAT OPENED, before any stats, no token stored', () => {
    // §4.3.1: "The counter counts closes, and a socket that never opened is
    // not one." Each socket here fires onopen before its close, which is
    // the shape a lost 1008 actually has - unlike the failure-to-open test
    // below (issue #139), where onopen is never called at all.
    clearStoredToken()
    const { client, sockets } = setup() // no token option, none stored either
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, '') // 1st: opened, then an ordinary drop
    expect(client.state()).toBe('offline')

    client.connect() // the user asks to reconnect
    nth(sockets, 1).open()
    nth(sockets, 1).serverClose(1006, '') // 2nd
    expect(client.state()).toBe('offline')

    client.connect()
    nth(sockets, 2).open()
    nth(sockets, 2).serverClose(1006, '') // 3rd consecutive open-then-drop, still nothing but drops
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'required' satisfies UnauthorizedReason,
    )
  })

  it('does not infer unauthorized-required when a token IS stored - three drops just stay offline', () => {
    const { client, sockets } = setup({ token: 'configured-token-value' })
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, '')
    client.connect()
    nth(sockets, 1).open()
    nth(sockets, 1).serverClose(1006, '')
    client.connect()
    nth(sockets, 2).open()
    nth(sockets, 2).serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })

  it('three consecutive sockets that FAIL TO OPEN never infer unauthorized, and the backoff loop keeps trying a fourth time (issue #139)', () => {
    // §4.3.1, "The counter counts closes, and a socket that never opened is
    // not one." This is the regression that took down the app on hardware:
    // a unit restarting produces many refused connections in a row, and
    // reading those as lost 1008s landed the client in `unauthorized`,
    // which is the one state that suspends reconnection - "the app gives up
    // for ever after any unit restart" (issue #139). None of these sockets
    // ever fires onopen, unlike the "opened" test above: that is the one
    // fact this test exists to keep distinct from it.
    clearStoredToken()
    const { client, sockets } = setup() // no token option, none stored either
    client.connect()
    nth(sockets, 0).serverClose(1006, '') // never opened: a refused connection
    expect(client.state()).toBe('offline')
    expect(client.unauthorizedReason()).toBeNull()

    waitOutBackoff(nth(BACKOFF_CAPS_S, 0)) // the backoff loop, not a manual tap
    expect(sockets.length).toBe(2)
    nth(sockets, 1).serverClose(1006, '') // never opened, 2nd
    expect(client.state()).toBe('offline')
    expect(client.unauthorizedReason()).toBeNull()

    waitOutBackoff(nth(BACKOFF_CAPS_S, 1))
    expect(sockets.length).toBe(3)
    nth(sockets, 2).serverClose(1006, '') // never opened, 3rd - the count the old bug tripped on
    expect(client.state()).toBe('offline') // NOT unauthorized
    expect(client.unauthorizedReason()).toBeNull()

    // The behaviour the old bug actually removed: the loop is still
    // running underneath, so a fourth attempt happens on its own. "It
    // stayed offline" is also true of a client that has given up - only a
    // fourth socket proves it has not.
    waitOutBackoff(nth(BACKOFF_CAPS_S, 2))
    expect(sockets.length).toBe(4)
  })

  it('a stale socketOpenedThisConnection left over from a socket that DID open must not let later failures-to-open count towards the inference (issue #139, the commonest sequence)', () => {
    // The test above never precedes its failures with an opened socket, so
    // it cannot see a flag that was set true once and then never reset.
    // That is exactly the everyday sequence, though: the app is connected,
    // the unit restarts, the live socket closes having opened - and if the
    // per-connection reset of that flag is missing from beginConnect, every
    // connection attempt after it inherits "opened" from the one that
    // actually did, and a run of ordinary refused reconnections gets read
    // as lost 1008s all over again.
    clearStoredToken()
    const { client, sockets } = setup() // no token option, none stored either
    client.connect()
    nth(sockets, 0).open() // a socket that DID open ...
    nth(sockets, 0).serverClose(1006, '') // ... then closed, before any stats: 1 legitimate count
    expect(client.state()).toBe('offline')

    // Three subsequent connections that never open at all. If the flag from
    // socket 0 survives uncleared, each of these is wrongly read as another
    // close of a socket that opened, and the count reaches unauthorized
    // well before a fourth attempt is ever made.
    for (let i = 0; i < 3; i++) {
      waitOutBackoff(nth(BACKOFF_CAPS_S, i))
      const socket = nth(sockets, sockets.length - 1)
      socket.serverClose(1006, '') // never opened
      expect(client.state()).toBe('offline')
      expect(client.unauthorizedReason()).toBeNull()
    }

    // The loop must still be running: a further attempt happens on its own.
    waitOutBackoff(nth(BACKOFF_CAPS_S, 3))
    expect(sockets.length).toBe(5) // 1 opened + 3 failed-to-open + this one
  })
})

describe('state machine: connected', () => {
  it('connected -> connected: a stats frame whose sessionAge stays under 55s', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(statsEnvelope(12))
    expect(client.state()).toBe('connected')
  })

  it('connected -> degraded: a stats frame whose sessionAge reaches 55s or more', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(statsEnvelope(55))
    expect(client.state()).toBe('degraded')
  })

  it('connected -> degraded: a stats frame whose sessionAge is null', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(statsEnvelope(null))
    expect(client.state()).toBe('degraded')
  })

  it('connected -> connecting: no pong within its 10s timeout after the 15s ping', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    vi.advanceTimersByTime(15000)
    expect(s.sentOf('ping')).toHaveLength(1)
    vi.advanceTimersByTime(10000)
    expect(client.state()).toBe('connecting')
  })

  it('a close arriving between a ping and its pong clears the pong timer, so a dead pong timer cannot open a second connection alongside the backoff reconnect', () => {
    const { client, sockets } = setup(
      { token: 'configured-token-value' },
      { random: () => 1 },
    )
    client.connect()
    const s = toConnected(sockets)
    vi.advanceTimersByTime(15000) // the ping goes out, arming a 10s pong timer
    expect(s.sentOf('ping')).toHaveLength(1)

    s.serverClose(1006, '') // the close beats the pong
    expect(client.state()).toBe('offline')

    waitOutBackoff(nth(BACKOFF_CAPS_S, 0)) // the one, legitimate reconnect
    expect(sockets.length).toBe(2)

    // If the pong timer had survived the close it would fire here - 10s
    // after the ping, only 5s after the close - and call its own immediate
    // reconnect on top of the one the backoff already produced.
    vi.advanceTimersByTime(10000)
    expect(sockets.length).toBe(2) // still exactly one reconnect, not two
  })

  it('connect() while already connected does not tear down the live socket', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    client.connect() // the user taps reconnect while already connected
    expect(client.state()).toBe('connected')
    expect(sockets).toHaveLength(1) // no new socket was created
    expect(s.closed).toBe(false) // and the live one was not closed either
  })

  it('connected -> restarting: a restarting message arrives', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('mode_change', 'MANUAL'))
    expect(client.state()).toBe('restarting')
  })

  it('connected -> unauthorized (rejected): error(unauthorized) then close', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(errorEnvelope('unauthorized', 'token rejected'))
    s.serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'rejected' satisfies UnauthorizedReason,
    )
  })

  it('connected -> unauthorized (required): a bare 1008 with no auth frame ever sent - the table\'s "same four close rules" applies here too, not only from connecting', () => {
    const { client, sockets } = setup() // no token -> no auth frame is sent
    client.connect()
    const s = toConnected(sockets)
    expect(s.sentOf('auth')).toHaveLength(0)
    s.serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'required' satisfies UnauthorizedReason,
    )
  })

  it('connected -> offline: a bare 1008 after an auth frame WAS sent (not unauthorized) - the fourth close rule, from connected', () => {
    const { client, sockets } = setup({ token: 'configured-token-value' })
    client.connect()
    const s = toConnected(sockets)
    expect(s.sentOf('auth')).toHaveLength(1)
    s.serverClose(1008, '')
    expect(client.state()).toBe('offline')
  })

  it('connected -> offline: any other close', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })
})

describe('state machine: degraded', () => {
  function toDegraded(sockets: FakeSocket[]): FakeSocket {
    return firstStats(sockets, 60)
  }

  it('connect() while already degraded does not tear down the live socket either', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toDegraded(sockets)
    client.connect()
    expect(client.state()).toBe('degraded')
    expect(sockets).toHaveLength(1)
    expect(s.closed).toBe(false)
  })

  it('degraded -> connected: sessionAge back under 55s and not null', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toDegraded(sockets)
    expect(client.state()).toBe('degraded')
    s.push(statsEnvelope(3))
    expect(client.state()).toBe('connected')
  })

  it('degraded -> degraded: still stale, the displayed age simply updates', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toDegraded(sockets)
    s.push(statsEnvelope(90))
    expect(client.state()).toBe('degraded')
  })

  it('degraded -> connecting: no pong within its timeout', () => {
    const { client, sockets } = setup()
    client.connect()
    toDegraded(sockets)
    vi.advanceTimersByTime(15000)
    vi.advanceTimersByTime(10000)
    expect(client.state()).toBe('connecting')
  })

  it('degraded -> restarting: a restarting message arrives', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toDegraded(sockets)
    s.push(restartingEnvelope('reboot', null))
    expect(client.state()).toBe('restarting')
  })

  it('degraded -> offline: any other close, same as connecting/connected', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toDegraded(sockets)
    s.serverClose(1006, '')
    expect(client.state()).toBe('offline')
  })

  it('degraded is reached from a live link with stale data, and controls stay enabled - it is not the presentation of a dropped tether', () => {
    // §4.3.1: "A dropped tether goes connected -> connecting -> offline and
    // never touches degraded." Proven here by the opposite path: a dropped
    // tether (no reply to the heartbeat) lands in connecting, not degraded,
    // even though the last stats it saw could have been arbitrarily stale.
    const { client, sockets } = setup()
    client.connect()
    firstStats(sockets, 90) // starts degraded: stale from the first frame
    expect(client.state()).toBe('degraded')
    vi.advanceTimersByTime(15000)
    vi.advanceTimersByTime(10000) // heartbeat catches the dead link
    expect(client.state()).toBe('connecting')
  })
})

describe('state machine: offline', () => {
  it('state() is offline before connect() has ever been called (§4.3.9) - the honest default, not shown to the user until they ask', () => {
    const { client } = setup()
    expect(client.state()).toBe('offline')
  })

  it('offline -> connecting: the backoff delay elapses', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    expect(sockets.length).toBe(2)
    expect(client.state()).toBe('connecting')
  })

  it('offline -> connecting: the user asks to reconnect, without waiting for backoff', () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
    client.connect()
    expect(client.state()).toBe('connecting')
    expect(sockets.length).toBe(2)
  })
})

describe('state machine: unauthorized', () => {
  it('unauthorized -> connecting: the stored token changes, and the CORRECTED token is what goes on the wire (§4.3.9: re-read from storage on every attempt)', () => {
    clearStoredToken()
    storeToken('wrong-token')
    const { client, sockets } = setup() // no options.token: storage wins and is re-read per attempt
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'wrong-token',
    })
    nth(sockets, 0).push(errorEnvelope('unauthorized', 'bad token'))
    nth(sockets, 0).serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')

    storeToken('a-corrected-token-value')
    client.connect()
    expect(client.state()).toBe('connecting')
    nth(sockets, 1).open()
    expect(nth(sockets, 1).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'a-corrected-token-value',
    })
  })

  it('unauthorizedReason() is null whenever the state is not unauthorized, including right after close() resets it from unauthorized', () => {
    const { client, sockets } = setup()
    expect(client.unauthorizedReason()).toBeNull()
    client.connect()
    expect(client.unauthorizedReason()).toBeNull()
    const s = toConnected(sockets)
    expect(client.unauthorizedReason()).toBeNull()

    s.push(errorEnvelope('unauthorized', 'token rejected'))
    s.serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    expect(client.unauthorizedReason()).toBe(
      'rejected' satisfies UnauthorizedReason,
    ) // the claim this test used to skip

    client.close()
    expect(client.unauthorizedReason()).toBeNull()
  })

  it('unauthorized suspends reconnection: it does not resolve on its own, unlike offline', () => {
    const { client, sockets } = setup() // no token -> "required"
    client.connect()
    nth(sockets, 0).serverClose(1008, '')
    expect(client.state()).toBe('unauthorized')
    // Wait far past every backoff step there is.
    for (const cap of BACKOFF_CAPS_S) waitOutBackoff(cap)
    expect(client.state()).toBe('unauthorized')
    expect(sockets).toHaveLength(1) // no reconnection attempt was ever made
  })
})

describe('state machine: restarting', () => {
  it('restarting survives the close for reason mode_change, backoff running underneath, reaching connected on fresh first stats of the new connection', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('mode_change', 'MANUAL'))
    expect(client.state()).toBe('restarting')
    s.serverClose(1000, 'restarting')
    expect(client.state()).toBe('restarting') // the state survives the close

    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    expect(sockets.length).toBe(2)
    expect(client.state()).toBe('restarting') // still restarting while reconnecting underneath

    nth(sockets, 1).open()
    nth(sockets, 1).push(statsEnvelope(4))
    expect(client.state()).toBe('connected')
  })

  it('restarting reaching degraded on a stale/null first stats of the new connection - the ordinary case after a reboot', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('reboot', null))
    s.serverClose(1000, 'restarting')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    nth(sockets, 1).open()
    nth(sockets, 1).push(statsEnvelope(null))
    expect(client.state()).toBe('degraded')
  })

  it('restarting: reason shutdown is terminal - no reconnection is attempted at all', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('shutdown', null))
    s.serverClose(1000, 'shutdown')
    expect(client.state()).toBe('restarting')

    const socketCountAtShutdown = sockets.length
    for (const cap of BACKOFF_CAPS_S) waitOutBackoff(cap)
    expect(sockets.length).toBe(socketCountAtShutdown) // never even tried
    expect(client.state()).toBe('restarting')
  })

  it('restarting: patience expires at 90s for mode_change and falls through to offline', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('mode_change', 'AUTO'))
    s.serverClose(1000, 'restarting')
    vi.advanceTimersByTime(89999)
    expect(client.state()).toBe('restarting')
    vi.advanceTimersByTime(1)
    expect(client.state()).toBe('offline')
  })

  it('restarting: patience expires at 180s for reboot and falls through to offline', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('reboot', null))
    s.serverClose(1000, 'restarting')
    vi.advanceTimersByTime(179999)
    expect(client.state()).toBe('restarting')
    vi.advanceTimersByTime(1)
    expect(client.state()).toBe('offline')
  })

  it('restarting: shutdown has no patience timer at all - it never reaches offline, however long it waits', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('shutdown', null))
    s.serverClose(1000, 'shutdown')
    // Well past the 180s ceiling of the longest patience timer that DOES exist.
    vi.advanceTimersByTime(300000)
    expect(client.state()).toBe('restarting')
  })

  it('restarting: the user can force a reconnect regardless of reason, shutdown included', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('shutdown', null))
    s.serverClose(1000, 'shutdown')
    client.connect()
    expect(client.state()).toBe('connecting')
  })

  // SPEC 4.4.1/review issue #131 follow-up: "null in every state but
  // `restarting`". Against the real client, not a copy of the accessor
  // (that copy is what stores.spec.ts's own double reimplements, and its
  // comment there says as much) - a mutation to restartReason() itself that
  // returned the value ungated would pass every store-layer test in the
  // suite because none of them exercise the real accessor.
  it('restartReason() reads the reason only while restarting, and null again once the reconnect leaves it (§4.4.1, issue #131)', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    expect(client.restartReason()).toBeNull() // connected: not restarting yet

    s.push(restartingEnvelope('reboot', null))
    expect(client.state()).toBe('restarting')
    expect(client.restartReason()).toBe('reboot')

    s.serverClose(1000, 'restarting')
    expect(client.state()).toBe('restarting') // the state survives the close
    expect(client.restartReason()).toBe('reboot') // and so does the reason

    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    nth(sockets, 1).open()
    nth(sockets, 1).push(statsEnvelope(4))
    expect(client.state()).toBe('connected') // left restarting via the reconnect
    expect(client.restartReason()).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Staleness is a property of the payload, not of arrival (§4.3.1, §10.5)
// ---------------------------------------------------------------------------

describe('staleness comes from sessionAge in the payload, never from when a frame arrived', () => {
  it('a frame arriving exactly on the 20s broadcast cadence still degrades if its own sessionAge is stale', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    expect(client.state()).toBe('connected')
    vi.advanceTimersByTime(20000) // right on schedule
    s.push(statsEnvelope(60)) // but the payload itself is stale
    expect(client.state()).toBe('degraded')
  })

  it('a late-arriving frame does not degrade the connection if its own sessionAge is fresh', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    // Answer the heartbeat so the gap is read as a slow broadcast rather
    // than a dead link forcing a reconnect of its own.
    vi.advanceTimersByTime(15000)
    const ping = s.sentOf('ping')[0]
    s.push(pongEnvelope(ping?.['message_id'] as string | undefined))
    vi.advanceTimersByTime(20000) // 35s since the last stats: a late broadcast
    expect(client.state()).toBe('connected') // no degrade purely from the gap
    s.push(statsEnvelope(4)) // finally arrives, and it is fresh
    expect(client.state()).toBe('connected')
  })
})

// ---------------------------------------------------------------------------
// §4.3.2 - the timings that are not already covered above
// ---------------------------------------------------------------------------

describe('timings (§4.3.2)', () => {
  it('answering the pong in time keeps the connection alive past the timeout', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    vi.advanceTimersByTime(15000)
    const ping = s.sentOf('ping')[0]
    s.push(pongEnvelope(ping?.['message_id'] as string | undefined))
    vi.advanceTimersByTime(10000) // would have timed out with no pong
    expect(client.state()).toBe('connected')
  })

  it('a second, unsolicited pong while a fresh ping timer is already armed clears the old timer instead of orphaning it, so the ping rate does not double', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)

    vi.advanceTimersByTime(15000) // t=15000: the first ping goes out
    expect(s.sentOf('ping')).toHaveLength(1)
    const firstPing = s.sentOf('ping')[0]
    s.push(pongEnvelope(firstPing?.['message_id'] as string | undefined))
    // The solicited pong above reschedules the ping timer for t=30000 -
    // call it timer A. Nothing was armed yet at that moment, so this first
    // reschedule alone cannot exercise the clear this test is about.

    vi.advanceTimersByTime(1) // t=15001: a beat later
    s.push(pongEnvelope()) // a second, UNSOLICITED pong - no ping asked for it
    // This reschedule is the one that matters: timer A (due at t=30000) is
    // already armed when it runs, so it must clear timer A before arming
    // timer B (due at t=30001). If that clear were missing, timer A would
    // still be live underneath and fire on its own schedule regardless of
    // what the `pingTimer` variable now points at.

    vi.advanceTimersByTime(14999) // t=30000: exactly when the orphan would fire
    expect(s.sentOf('ping')).toHaveLength(1) // still just the one - timer A did not survive to fire here

    vi.advanceTimersByTime(1) // t=30001: timer B's own, legitimate deadline
    expect(s.sentOf('ping')).toHaveLength(2) // exactly one new ping, not two
  })

  it('a request receives no reply at all within 15s and its promise rejects', async () => {
    const { client, sockets } = setup()
    client.connect()
    toConnected(sockets)
    const pending = client.request('get_stats')
    vi.advanceTimersByTime(15000)
    await expect(pending).rejects.toBeDefined()
  })

  it('the typed reply arriving just under the 15s deadline still resolves the request', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_stats')
    const sent = s.sentOf('get_stats')
    expect(sent).toHaveLength(1)
    const messageId = sent[0]?.['message_id'] as string
    expect(messageId).toBeTruthy()
    vi.advanceTimersByTime(14000)
    s.push(withMessageId(statsEnvelope(9), messageId))
    await expect(pending).resolves.toMatchObject({ type: 'stats' })
  })

  it('a request made while offline times out 15s after the call itself, not after any later reconnect', async () => {
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
    const pending = client.request('get_stats')
    vi.advanceTimersByTime(15000)
    await expect(pending).rejects.toBeDefined()
  })

  it('a command that receives no acknowledgment within 15s times out and rejects (§4.3.8: commands share the read clock)', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.command('reboot')
    expect(s.sentOf('reboot')).toHaveLength(1)
    vi.advanceTimersByTime(15000)
    await expect(pending).rejects.toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// §4.3.8 - what a request resolves with, and when its clock starts
// ---------------------------------------------------------------------------

describe('§4.3.8: a read resolves on its typed reply, a command on its acknowledgment', () => {
  it('a read does NOT settle on the acknowledgment that precedes its typed reply', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_log')
    const sent = s.sentOf('get_log')
    expect(sent).toHaveLength(1)
    const messageId = sent[0]?.['message_id'] as string

    // Router.handle sends the acknowledgment BEFORE the typed reply for
    // every message carrying a message_id (§4.3.8) - a read watching for
    // "the first correlated frame" would settle right here, wrongly.
    s.push(withMessageId(ackEnvelope('get_log'), messageId))
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
    expect(settled).toBe(false)

    s.push({
      type: 'log_lines',
      timestamp: 1700000000,
      data: { lines: ['a sample log line'], path: '/var/log/pwnagotchi.log' },
      message_id: messageId,
    })
    await expect(pending).resolves.toMatchObject({ type: 'log_lines' })
  })

  it('a read carrying parameters puts them on the wire, not only its type', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    void client.request('get_log', { lines: 50 })
    expect(s.sentOf('get_log')[0]).toMatchObject({ type: 'get_log', lines: 50 })
  })

  it('the wire frame keeps its own "type" even when the caller\'s data carries a competing one - the envelope is built AFTER the payload', () => {
    // Payload<IncomingGetStats> is {} (a read with no fields), so
    // TypeScript's excess-property check does not reject this call: it
    // compiles cleanly. The runtime
    // guarantee is what actually protects the queue split of SPEC 4.3.3 -
    // this is the test that pins it rather than the compiler.
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    void client.request('get_stats', { type: 'reboot' })
    const sent = s.sentOf('get_stats')
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ type: 'get_stats' })
    expect(s.sentOf('reboot')).toHaveLength(0)
  })

  it('a command DOES settle on its acknowledgment, because the acknowledgment IS the reply', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.command('reboot')
    const sent = s.sentOf('reboot')
    expect(sent).toHaveLength(1)
    const messageId = sent[0]?.['message_id'] as string
    s.push(withMessageId(ackEnvelope('reboot'), messageId))
    await expect(pending).resolves.toMatchObject({ type: 'acknowledgment' })
  })

  it('an error carrying the message_id settles a read as a failure', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_handshakes')
    const sent = s.sentOf('get_handshakes')
    const messageId = sent[0]?.['message_id'] as string
    s.push(errorEnvelope('internal_error', 'boom', messageId))
    await expect(pending).rejects.toBeDefined()
  })

  it('an error carrying the message_id settles a command as a failure', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.command('set_pasv', { on: true })
    const sent = s.sentOf('set_pasv')
    const messageId = sent[0]?.['message_id'] as string
    s.push(errorEnvelope('pasv_requires_auto', 'not in auto', messageId))
    await expect(pending).rejects.toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// §4.3.9 - the token re-read, the tokenless client's silence, and close()
// ---------------------------------------------------------------------------

describe('§4.3.9: token re-read per attempt, and a tokenless client staying silent', () => {
  it('re-reads the token from storage on every connect attempt, not only once at construction', () => {
    clearStoredToken()
    storeToken('first-token-value')
    const { client, sockets } = setup() // no options.token: storage drives it
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'first-token-value',
    })

    nth(sockets, 0).serverClose(1006, '') // an ordinary drop, unrelated to the token
    storeToken('second-token-value')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    nth(sockets, 1).open()
    expect(nth(sockets, 1).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'second-token-value',
    })
  })

  it('options.token overrides storage on every attempt when a caller supplies one explicitly', () => {
    storeToken('stored-token-that-must-be-ignored')
    const { client, sockets } = setup({ token: 'explicit-token-value' })
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).parsedSent()[0]).toMatchObject({
      type: 'auth',
      token: 'explicit-token-value',
    })
  })

  it('an empty stored token is treated as absent, the same as clearing it', () => {
    storeToken('')
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sentOf('auth')).toHaveLength(0)
  })

  it('a tokenless client sends nothing at all until the first frame arrives from the unit, even a queued read', () => {
    const { client, sockets } = setup()
    client.connect()
    void client.request('get_peers')
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sent).toHaveLength(0) // socket open, unit silent so far: still nothing sent
    nth(sockets, 0).push(statsEnvelope(5)) // the unit speaks first
    expect(nth(sockets, 0).sentOf('get_peers')).toHaveLength(1)
  })
})

describe('close()', () => {
  it('tears down: clears timers, rejects pending promises, reports offline, and lets connect() resume afterwards', async () => {
    const { client, sockets } = setup()
    client.connect()
    toConnected(sockets)
    const readPending = client.request('get_stats')
    const commandPending = client.command('reboot')

    client.close()
    expect(client.state()).toBe('offline')
    await expect(readPending).rejects.toBeDefined()
    await expect(commandPending).rejects.toBeDefined()

    const socketCountAfterClose = sockets.length
    vi.advanceTimersByTime(300000) // well past every timer the client owns
    expect(sockets.length).toBe(socketCountAfterClose) // no reconnect was scheduled
    expect(client.state()).toBe('offline')

    client.connect()
    expect(client.state()).toBe('connecting')
    expect(sockets.length).toBe(socketCountAfterClose + 1) // resumable
  })
})

describe('reconnect backoff: full jitter over a growing, capped sequence (§4.3.2)', () => {
  it('draws the reconnect delay from the literal cap sequence 1, 2, 4, 8, 16, 30, 30s, one attempt at a time', () => {
    // random() pinned at the exact top of its domain: full jitter is
    // `cap * 1000 * random()`, so a draw of exactly 1 lands the delay on
    // the cap to the millisecond. That makes "not yet at cap - 1ms, fired
    // by cap" a literal pin of the SPEC sequence rather than a bounded
    // check a nearby sequence (1, 1.5, 3, 7, 15, 29, 29s) would also pass,
    // replacing an earlier interval-based version of this test that only
    // proved monotonic growth.
    // A token is configured so the unrelated three-consecutive-closes
    // inference (also tested above, under "connecting") cannot fire and
    // reclassify a later iteration as unauthorized instead of offline.
    const { client, sockets } = setup(
      { token: 'configured-token-value' },
      { random: () => 1 },
    )
    client.connect()

    for (const capS of BACKOFF_CAPS_S) {
      const before = sockets.length
      nth(sockets, before - 1).serverClose(1006, '')
      expect(client.state()).toBe('offline')

      vi.advanceTimersByTime(capS * 1000 - 1)
      expect(sockets.length).toBe(before) // one millisecond short of THIS literal cap
      vi.advanceTimersByTime(1)
      expect(sockets.length).toBe(before + 1) // fires exactly at it
    }
  })

  it('a draw of 0 reconnects immediately rather than waiting out the cap - the jitter is a distribution, not a fixed schedule', () => {
    const { client, sockets } = setup({}, { random: () => 0 })
    client.connect()
    nth(sockets, 0).serverClose(1006, '')
    expect(client.state()).toBe('offline')
    vi.advanceTimersByTime(0)
    expect(sockets.length).toBe(2)
  })

  it('a draw near 1 does not reconnect at t=0, unlike a draw of 0 - the two draws produce different delays', () => {
    const { client, sockets } = setup({}, { random: () => 0.999999 })
    client.connect()
    nth(sockets, 0).serverClose(1006, '')
    vi.advanceTimersByTime(0)
    expect(sockets.length).toBe(1) // not yet - contrast with the draw-of-0 case above
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    expect(sockets.length).toBe(2) // but it does happen within the cap
  })
})

// ---------------------------------------------------------------------------
// §4.3.3 - the outbound queue distinguishes reads from commands
// ---------------------------------------------------------------------------

describe('the outbound queue (§4.3.3)', () => {
  it('queues a read before the socket exists, and sends it once the socket is open AND the unit has spoken (§4.3.9: a tokenless client stays silent until then)', () => {
    const { client, sockets } = setup()
    client.connect()
    void client.request('get_stats')
    expect(nth(sockets, 0).sent).toHaveLength(0) // no socket yet
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sent).toHaveLength(0) // socket open, but the unit has not spoken yet
    nth(sockets, 0).push(statsEnvelope(5)) // the unit's first frame
    expect(nth(sockets, 0).sentOf('get_stats')).toHaveLength(1)
  })

  it('a read still in flight when the socket drops is resent on the reconnected socket, correlated by the same message_id, and resolves on its typed reply rather than on an acknowledgment', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets) // open + the unit's first frame, so the queue can flush
    const pending = client.request('get_stats')
    const firstAttempt = s.sentOf('get_stats')
    expect(firstAttempt).toHaveLength(1)
    const messageId = firstAttempt[0]?.['message_id'] as string
    expect(messageId).toBeTruthy()

    s.serverClose(1006, '') // lost mid-flight
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    const s2 = nth(sockets, 1)
    s2.open()
    expect(s2.sent).toHaveLength(0) // the gate is per SOCKET: a new connection is silent again too
    s2.push(statsEnvelope(5)) // the new connection's own first frame from the unit
    const resent = s2.sentOf('get_stats')
    expect(resent).toHaveLength(1)
    expect(resent[0]?.['message_id']).toBe(messageId) // the SAME read, not a new one

    s2.push(withMessageId(ackEnvelope('get_stats'), messageId)) // the receipt SPEC 4.3.8 says precedes the reply
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
    expect(settled).toBe(false) // must not have settled on the acknowledgment alone

    s2.push(withMessageId(statsEnvelope(9), messageId)) // the typed reply
    await expect(pending).resolves.toMatchObject({ type: 'stats' })
  })

  it('a state command is refused outside connected/degraded and never enters the queue', async () => {
    const { client, sockets } = setup()
    client.connect() // state: connecting, not connected/degraded
    const rejected = client.command('reboot')
    await expect(rejected).rejects.toBeDefined()
    expect(nth(sockets, 0).sentOf('reboot')).toHaveLength(0)

    // and it must not have been silently queued for once the link comes up
    toConnected(sockets)
    expect(nth(sockets, sockets.length - 1).sentOf('reboot')).toHaveLength(0)
  })

  it('a state command in flight when the socket drops is discarded, reported as failed, and NEVER resent - the reboot-then-pocket scenario', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.command('reboot')
    expect(s.sentOf('reboot')).toHaveLength(1)

    s.serverClose(1006, '') // the tether drops mid-flight
    await expect(pending).rejects.toBeDefined() // reported as failed

    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    const rejoined = nth(sockets, sockets.length - 1)
    rejoined.open()
    expect(rejoined.sentOf('reboot')).toHaveLength(0) // must never reach the unit
  })

  it('every one of the four state commands is refused outside connected/degraded, not only reboot', async () => {
    const { client, sockets } = setup()
    client.connect()
    const commands = [
      ['set_mode', { mode: 'auto' }],
      ['set_pasv', { on: true }],
      ['reboot', undefined],
      ['shutdown', undefined],
    ] as const
    for (const [type, data] of commands) {
      await expect(client.command(type, data)).rejects.toBeDefined()
      expect(nth(sockets, 0).sentOf(type)).toHaveLength(0)
    }
  })

  it('gps_data is never replayed on reconnect: discarded if in flight when the socket drops', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    client.sendGps({ latitude: 12.34, longitude: 56.78 })
    expect(s.sentOf('gps_data')).toHaveLength(1)

    s.serverClose(1006, '')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    const rejoined = nth(sockets, sockets.length - 1)
    rejoined.open()
    expect(rejoined.sentOf('gps_data')).toHaveLength(0)
  })

  it('gps_data sent while offline is dropped rather than queued for later delivery', () => {
    const { client, sockets } = setup()
    client.connect() // still connecting: no socket open yet
    client.sendGps({ latitude: 12.34, longitude: 56.78 })
    nth(sockets, 0).open()
    expect(nth(sockets, 0).sentOf('gps_data')).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// onState / onMessage subscriptions
// ---------------------------------------------------------------------------

describe('subscriptions', () => {
  it('onState notifies on a transition and stops after the returned unsubscribe is called', () => {
    const { client, sockets } = setup()
    const seen: ConnectionState[] = []
    const unsubscribe = client.onState((state) => seen.push(state))
    client.connect()
    firstStats(sockets, 5)
    expect(seen).toContain('connected')

    unsubscribe()
    const s = nth(sockets, sockets.length - 1)
    s.serverClose(1006, '')
    expect(seen).not.toContain('offline') // no longer listening
  })

  it('onMessage delivers every envelope pushed by the server, and stops after unsubscribe', () => {
    const { client, sockets } = setup()
    const messages: Array<Record<string, unknown>> = []
    const unsubscribe = client.onMessage((message) =>
      messages.push(message as unknown as Record<string, unknown>),
    )
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('mode_change', 'AUTO'))
    expect(messages.some((m) => m['type'] === 'restarting')).toBe(true)

    unsubscribe()
    s.push(restartingEnvelope('reboot', null))
    expect(messages.filter((m) => m['type'] === 'restarting')).toHaveLength(1) // the second push was not delivered
  })

  it('lastStats() is null before any stats has arrived and reflects the most recent one after', () => {
    const { client, sockets } = setup()
    expect(client.lastStats()).toBeNull()
    client.connect()
    firstStats(sockets, 7)
    expect(client.lastStats()?.stats.sessionAge).toBe(7)
  })

  // SPEC 4.3.10: lastStats() hands over a StatsSnapshot, the envelope's own
  // `timestamp` alongside the payload -- not the payload alone -- so a store
  // adapter seeding a channel at mount (§4.4.2) has the same stamp §4.4.1's
  // ordering rule needs for a live push. A client that remembered the
  // payload but dropped the stamp would pass the assertion above and fail
  // only this one.
  it('lastStats() carries the envelope timestamp the stats frame arrived under, beside the payload', () => {
    const { client, sockets } = setup()
    client.connect()
    firstStats(sockets, 7)
    expect(client.lastStats()?.timestamp).toBe(1700000000)
  })
})

// ---------------------------------------------------------------------------
// §4.3.10 - the last error and the round trip (issue #176). Written from the
// spec's own surface: `Diagnostics`, `LastError`, `WsClient.diagnostics()` /
// `onDiagnostics()`, and `WsDeps.now()`. Never read from ws.ts's diagnostics
// code, which is authored in parallel.
// ---------------------------------------------------------------------------

describe('diagnostics: baseline (§4.3.10)', () => {
  it('is null/null/0 before connect() has ever been called, not a placeholder shape', () => {
    const { client } = setup()
    expect(client.diagnostics()).toEqual({
      lastError: null,
      latencyMs: null,
      droppedFrames: 0,
    })
  })
})

describe('diagnostics: the last error records every code, including the ones §4.3.5 routes elsewhere', () => {
  // Three distinct codes: one §4.3.5 sends to a specific control, one
  // §4.3.5 sends to the Log view with its own sentence, and one no table
  // entry names at all - "everything else" is still the point of the last
  // row, and a code with no dedicated surface is the case that proves it.
  // SPEC 4.3.11 (issue #109, amended): `ErrorCode` is an enum in
  // common.json and the boundary guard enforces enums, so the third case
  // must be a value the enum actually carries - an invented code is now
  // dropped before it ever reaches the last error at all (see the separate
  // describe block below). `unknown_command` is one of the four enum
  // members §4.3.5's table gives no dedicated row to (the others are
  // `bad_request`, `not_supported` and `internal_error`, all already used
  // by name elsewhere in this file), so it is picked here specifically for
  // being unused anywhere else in this file.
  const codes: Array<{ code: string; label: string }> = [
    {
      code: 'pasv_requires_auto',
      label: 'a code §4.3.5 routes to a specific control',
    },
    { code: 'log_unavailable', label: 'a code §4.3.5 routes to the Log view' },
    {
      code: 'unknown_command',
      label: 'a code with no dedicated §4.3.5 surface, routed to the generic diagnostics line',
    },
  ]

  for (const { code, label } of codes) {
    it(`records an error frame's code and message as the last error - ${label}`, () => {
      const clock = makeClock()
      const { client, sockets } = setup({}, { now: clock.now })
      client.connect()
      const s = toConnected(sockets)
      clock.advance(250)
      s.push(errorEnvelope(code as unknown as ErrorCode, `message for ${code}`))
      expect(client.diagnostics().lastError).toEqual({
        source: 'frame',
        code,
        message: `message for ${code}`,
        at: clock.value(),
      })
    })
  }

  it('also records an error frame that carries a message_id and settles a request as a failure - "every code goes through"', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_handshakes')
    const sent = s.sentOf('get_handshakes')
    const messageId = sent[0]?.['message_id'] as string
    s.push(errorEnvelope('internal_error', 'a correlated failure', messageId))
    await expect(pending).rejects.toBeDefined()
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'frame',
      code: 'internal_error',
      message: 'a correlated failure',
    })
  })

  it('a later error frame replaces the earlier one', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(errorEnvelope('bad_request', 'first'))
    s.push(errorEnvelope('not_supported', 'second'))
    expect(client.diagnostics().lastError).toMatchObject({
      code: 'not_supported',
      message: 'second',
    })
  })
})

// SPEC 4.3.11 (amended, issue #109): "An `error` whose `code` is not in
// `ErrorCode` is dropped too... a dropped one leaves that request to fail
// on its own 15s timeout (SPEC 4.3.8) instead of immediately, with the
// Settings row saying the unit sent something this app could not read
// rather than saying what the unit actually refused." A code outside the
// enum fails the same guard as every other invalid frame (SPEC 4.3.11):
// it is dropped before it reaches the last error, before it can settle a
// correlated request, and it is counted the same as any other guard
// failure. This is the behaviour change the amendment names, and nothing
// before it in this file exercised an `error` frame that fails its own
// guard.
describe('diagnostics: an error frame carrying a code outside ErrorCode is dropped, not admitted (SPEC 4.3.11, amended)', () => {
  it('is dropped and counted, recorded as bad_frame rather than the code the frame carried, and leaves a correlated request to its own 15s timeout instead of settling it', async () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const pending = client.request('get_handshakes')
    const sent = s.sentOf('get_handshakes')
    const messageId = sent[0]?.['message_id'] as string
    expect(messageId).toBeTruthy()

    s.push(
      errorEnvelope(
        'a_code_the_enum_does_not_carry' as unknown as ErrorCode,
        'whatever the unit meant to refuse',
        messageId,
      ),
    )

    // Not settled by the dropped frame, despite carrying the correlating
    // message_id: still pending just after it arrives.
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
    expect(settled).toBe(false)

    expect(client.diagnostics().droppedFrames).toBe(1)
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'bad_frame',
    })

    // Left to its own request timeout (SPEC 4.3.8), not settled early.
    vi.advanceTimersByTime(15000)
    await expect(pending).rejects.toBeDefined()
  })
})

describe('diagnostics: a close writes the last error only when the app did not ask for it', () => {
  it('a close with no prior error frame records the close code in digits as `code`, source "close"', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)
    clock.advance(10)
    s.serverClose(1006, 'link dropped')
    expect(client.diagnostics().lastError).toEqual({
      source: 'close',
      code: '1006',
      message: 'link dropped',
      at: clock.value(),
    })
  })

  it('message is the empty string when the close carried no reason - the ordinary shape of a 1006', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.serverClose(1006, '')
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'close',
      code: '1006',
      message: '',
    })
  })

  it('a close following an error frame on the same connection does NOT overwrite the frame - the unauthorized shape from §4.3.1', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(errorEnvelope('unauthorized', 'token rejected'))
    s.serverClose(1008, '')
    expect(client.diagnostics().lastError).toEqual(
      expect.objectContaining({
        source: 'frame',
        code: 'unauthorized',
        message: 'token rejected',
      }),
    )
    // The mutation this guards against: recording the close last, which
    // would replace "unauthorized" with a number that only says something
    // ended.
    expect(client.diagnostics().lastError?.code).not.toBe('1008')
  })

  it('a close following a NON-fatal frame error DOES overwrite it - the rule is scoped to unauthorized, not to "any frame error"', () => {
    // §4.3.10 (amended): "the rule is scoped to the code it was written
    // for: a close does not overwrite a last error whose code is
    // unauthorized ... every other frame error is overwritten by whatever
    // ends the connection, because by then the connection ending is the
    // newer fact." Read as "any frame error blocks any later close" - the
    // old rule - this would keep pasv_unavailable on screen and hide the
    // close that ended the link afterwards.
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(errorEnvelope('pasv_unavailable', 'PASV mode requires AUTO'))
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'frame',
      code: 'pasv_unavailable',
    })

    s.serverClose(1006, 'link dropped four hours later')
    expect(client.diagnostics().lastError).toEqual({
      source: 'close',
      code: '1006',
      message: 'link dropped four hours later',
      at: expect.any(Number),
    })
  })

  it('a close() the app itself called suppresses recording, proven against the handler close() itself set up, not a re-fired stand-in', () => {
    // s.serverClose() calls FakeSocket's own onclose property directly, and
    // a close() that (as §4.3.10 describes) unhooks the socket's onclose
    // before calling socket.close() would leave that property null by the
    // time this test could call s.serverClose() - which would then be a
    // silent no-op, passing this assertion whether or not any "app asked
    // for it" guard exists inside the handler at all. Capturing the
    // handler reference *before* close() runs, and invoking it directly
    // afterwards, calls into the exact closure the client wired up (which
    // closes over its own "stopped" state, not over the still-live
    // property on `s`) regardless of whether the property was nulled out.
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const closeHandler = s.onclose
    expect(closeHandler).not.toBeNull()
    client.close()
    closeHandler?.({ code: 1005, reason: '' })
    expect(client.diagnostics().lastError).toBeNull()
  })

  it('a close() called while a last error was already recorded does not add a new, close-shaped one on top of it', () => {
    // Distinguishes "close() suppresses recording a NEW error" from "close()
    // clears whatever was already there" - neither §4.3.10 rule requires
    // the latter, and this proves the former without assuming it. Same
    // captured-handler technique as the test above, for the same reason:
    // s.serverClose() after client.close() would call into an unhooked
    // property and prove nothing either way.
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(errorEnvelope('internal_error', 'already broken'))
    const closeHandler = s.onclose
    expect(closeHandler).not.toBeNull()
    client.close()
    closeHandler?.({ code: 1005, reason: '' })
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'frame',
      code: 'internal_error',
    })
  })
})

describe('diagnostics: the last error survives a failed retry, and is cleared only on reaching connected', () => {
  it('a new connect() attempt after a close does not clear the last error on its own', () => {
    clearStoredToken()
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, 'first drop')
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'close',
      code: '1006',
    })

    // The backoff loop reconnects underneath; the reconnection itself must
    // not be read as "fixed", only an admitted connection is.
    waitOutBackoff(1)
    expect(sockets.length).toBe(2)
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'close',
      code: '1006',
    })

    nth(sockets, 1).open()
    nth(sockets, 1).serverClose(1006, 'second drop, still failing')
    expect(client.state()).toBe('offline')
    expect(client.diagnostics().lastError).not.toBeNull() // still there: the retry failed too
  })

  it('is cleared once a connection reaches connected, and not before', () => {
    clearStoredToken()
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, 'a drop before recovery')
    expect(client.diagnostics().lastError).not.toBeNull()

    waitOutBackoff(1)
    const s = nth(sockets, 1)
    s.open()
    expect(client.diagnostics().lastError).not.toBeNull() // opening alone is not admission
    s.push(statsEnvelope(5)) // first stats, sessionAge under 55s -> connected
    expect(client.state()).toBe('connected')
    expect(client.diagnostics().lastError).toBeNull()
  })

  it('is cleared once a connection reaches degraded, too - an admitted connection carrying stale data, not a failed one', () => {
    clearStoredToken()
    const { client, sockets } = setup()
    client.connect()
    nth(sockets, 0).open()
    nth(sockets, 0).serverClose(1006, 'a drop before recovery')
    expect(client.diagnostics().lastError).not.toBeNull()

    waitOutBackoff(1)
    const s = nth(sockets, 1)
    s.open()
    expect(client.diagnostics().lastError).not.toBeNull() // opening alone is not admission
    s.push(statsEnvelope(55)) // first stats of this connection, sessionAge 55s or more -> degraded
    expect(client.state()).toBe('degraded')
    expect(client.diagnostics().lastError).toBeNull()
  })

  it('cleared on the transition into an admitted state, never on the state being re-entered - an error recorded while already connected survives the next periodic stats push (§4.3.10)', () => {
    // The blocking implementation defect §4.3.10 was amended to describe: a
    // clearing rule written against "the target state is connected/degraded"
    // alone fires on every `stats` broadcast, because the connection is
    // already connected when each one arrives - erasing every
    // still-current error, such as a rejected control, within one
    // broadcast interval. No reconnection happens anywhere in this test:
    // the connection is admitted once, an error frame arrives on that
    // live connection, and then an ordinary periodic `stats` push arrives
    // on the same connection with the state staying `connected`
    // throughout. The rule under test is "the state was not already
    // admitted", computed the same way teardown-side leaving is; a rule
    // written against the target state alone clears the error here, which
    // is exactly the failure this test exists to catch.
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    expect(client.state()).toBe('connected')

    s.push(errorEnvelope('pasv_unavailable', 'PASV mode requires AUTO'))
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'frame',
      code: 'pasv_unavailable',
    })

    // An ordinary broadcast on the same, still-live connection - not a
    // reconnection, and the state was already `connected` before this
    // frame arrived.
    s.push(statsEnvelope(6))
    expect(client.state()).toBe('connected')
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'frame',
      code: 'pasv_unavailable',
    })
  })

  it('a transition into a state that is NOT admitted - restarting - does not clear the last error, the negative that keeps the rule honest', () => {
    clearStoredToken()
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.serverClose(1006, 'a drop')
    expect(client.diagnostics().lastError).not.toBeNull()

    waitOutBackoff(1)
    const s2 = nth(sockets, 1)
    s2.open()
    s2.push(restartingEnvelope('reboot', null)) // arrives before any stats admits this connection
    expect(client.state()).toBe('restarting')
    expect(client.diagnostics().lastError).not.toBeNull() // restarting is not connected or degraded
  })
})

// ---------------------------------------------------------------------------
// SPEC 4.3.10 (amended): "A restart is asked for, and so is the close that
// carries it ... A close arriving in it records nothing." Every existing
// `restarting` test above reaches that state through a `restarting` frame
// and never fires a close while the state is `restarting` - the review's own
// finding was that the client records one today, which none of those tests
// could ever catch because none of them takes this path. Both halves of the
// rule are asserted: a close in `restarting` records nothing, and the same
// close in a state that is not `restarting` still records it, so the second
// test rules out "the client stopped recording closes altogether."
// ---------------------------------------------------------------------------

describe('diagnostics: a restart is asked for, and so is the close that carries it (§4.3.10)', () => {
  it('a close arriving while the state is restarting records nothing', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    s.push(restartingEnvelope('reboot', null))
    expect(client.state()).toBe('restarting')
    expect(client.diagnostics().lastError).toBeNull() // nothing recorded on the way in

    s.serverClose(1006, '')
    expect(client.state()).toBe('restarting') // survives the close, per the tests above
    expect(client.diagnostics().lastError).toBeNull() // and the close itself wrote nothing
  })

  it('the same close in a state that is not restarting still records it - the control for the test above', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = nth(sockets, 0)
    s.open() // connecting: admitted by nothing, but specifically not restarting
    expect(client.state()).not.toBe('restarting')
    s.serverClose(1006, 'an ordinary drop, nothing asked for it')
    expect(client.diagnostics().lastError).toEqual({
      source: 'close',
      code: '1006',
      message: 'an ordinary drop, nothing asked for it',
      at: expect.any(Number),
    })
  })
})

describe('diagnostics: a drop this client detects itself is recorded too, source "local" (§4.3.10)', () => {
  // "An implementation that records only on a close event ... shows a dash
  // next to offline for the single failure this screen exists to explain."
  // Neither of these two detections ever fires a browser close event in
  // this harness (nothing calls s.serverClose()) - if a `local` source were
  // implemented as a side effect of some close-event handler, these two
  // tests would still see `lastError` stay null, which is exactly the
  // regression §4.3.10 was amended to name.

  it('a pong that never arrives records source "local", code "pong_timeout", empty message', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)
    vi.advanceTimersByTime(15000) // the heartbeat ping goes out (§4.3.2)
    expect(s.sentOf('ping')).toHaveLength(1)

    clock.advance(10000)
    vi.advanceTimersByTime(10000) // the 10s pong timeout expires with no pong
    expect(client.state()).toBe('connecting')
    expect(client.diagnostics().lastError).toEqual({
      source: 'local',
      code: 'pong_timeout',
      message: '',
      at: clock.value(),
    })
  })

  it('the connecting bound expiring with no admitting frame at all records source "local", code "connect_timeout", empty message', () => {
    const clock = makeClock()
    const { client } = setup({}, { now: clock.now })
    client.connect()
    clock.advance(55000)
    vi.advanceTimersByTime(55000) // the 55s connecting bound, §4.3.9
    expect(client.state()).toBe('offline')
    expect(client.diagnostics().lastError).toEqual({
      source: 'local',
      code: 'connect_timeout',
      message: '',
      at: clock.value(),
    })
  })
})

// ---------------------------------------------------------------------------
// Issue #122: "The first attempt is already protected; the retry is not,
// and it runs inside a bare timer callback." Every other test in this file
// that drives createSocket through makeDeps' default never throws at all,
// so none of them can distinguish a client that guards every attempt from
// one that only guards the first - this is the one that makes createSocket
// fail on a LATER attempt, specifically to prove the guard survives past
// attempt 1.
// ---------------------------------------------------------------------------

describe('reconnect: createSocket throwing on a later attempt does not escape the backoff timer (issue #122)', () => {
  it('a throw on the SECOND attempt (not the first) is caught, recorded as socket_failed, and the retry schedule keeps running past it', () => {
    clearStoredToken()
    const liveSockets: FakeSocket[] = []
    let attempt = 0
    const { client } = setup(
      {},
      {
        createSocket: (_url: string) => {
          attempt += 1
          if (attempt === 2) {
            // The retry itself, not the first attempt: connect()'s own call
            // to createSocket already has to survive this file's other
            // tests, so a version of this test that threw on attempt 1
            // would prove only what §4.3.9's guard already proves.
            throw new Error(
              'ENETUNREACH: the browser refused this attempt synchronously',
            )
          }
          const socket = new FakeSocket()
          liveSockets.push(socket)
          return socket
        },
      },
    )

    client.connect()
    expect(attempt).toBe(1)
    expect(liveSockets).toHaveLength(1)
    nth(liveSockets, 0).serverClose(1006, '') // never opened: an ordinary refused connection, sets up the retry

    // The retry runs inside the backoff timer callback. If it is
    // unprotected the way ticket #122 describes, createSocket's throw on
    // attempt 2 escapes that callback, and vitest's fake timers surface an
    // uncaught exception from inside a scheduled callback as
    // advanceTimersByTime itself throwing - which is the assertion below,
    // not an incidental detail of it. An implementation missing the guard
    // fails this line, not a later one.
    expect(() => waitOutBackoff(nth(BACKOFF_CAPS_S, 0))).not.toThrow()
    expect(attempt).toBe(2)
    // SPEC 4.3.1/review issue #131 follow-up: beginConnect sets `connecting`
    // before it asks for a socket, and neither the catch nor the reschedule
    // moves it back - a retry is scheduled, and that is what §4.3.1 calls
    // trying. Dropping to `offline` would tell a screen the app had given
    // up while a timer is pending.
    expect(client.state()).toBe('connecting') // survives: not stuck, not thrown into some other state
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'socket_failed',
    })

    // The schedule itself is still alive underneath the caught throw: a
    // further backoff step produces a third attempt, and this one is
    // allowed to succeed and reach connected, exactly like the "the loop is
    // still running" assertions in the connecting-state suite above (issue
    // #139) prove for a socket that fails to open rather than fails to
    // construct at all.
    waitOutBackoff(nth(BACKOFF_CAPS_S, 1))
    expect(attempt).toBe(3)
    expect(liveSockets).toHaveLength(2)
    nth(liveSockets, 1).open()
    nth(liveSockets, 1).push(statsEnvelope(5))
    expect(client.state()).toBe('connected')
  })
})

// ---------------------------------------------------------------------------
// The pong timeout is the second caller SPEC §4.3.10 names for this guard:
// "The reconnect schedule is one such caller and the pong timeout is
// another: it discards the dead socket and rebuilds immediately ... with
// the same nothing above it." The #122 suite above proves the reconnect
// schedule's own beginConnect is guarded; this proves the pong timeout's is
// too, since a fix that guarded only the caller a ticket named would leave
// this one unprotected until the next ticket found it independently.
// ---------------------------------------------------------------------------

describe('reconnect: the pong timeout also rebuilds through a guarded beginConnect, not just the backoff schedule (§4.3.10, the second #122 caller)', () => {
  it('createSocket throwing on the immediate rebuild after a pong timeout is caught, recorded as socket_failed, and the client is not left dead: a further backoff step still reconnects', () => {
    clearStoredToken()
    const clock = makeClock()
    const liveSockets: FakeSocket[] = []
    let attempt = 0
    const { client } = setup(
      {},
      {
        now: clock.now,
        createSocket: (_url: string) => {
          attempt += 1
          if (attempt === 2) {
            // The pong timeout's own immediate rebuild, not the first
            // connect(): the first attempt has to succeed and reach
            // 'connected' for a pong timeout to be possible at all.
            throw new Error(
              'ENETUNREACH: the browser refused this attempt synchronously',
            )
          }
          const socket = new FakeSocket()
          liveSockets.push(socket)
          return socket
        },
      },
    )

    client.connect()
    expect(attempt).toBe(1)
    const s = nth(liveSockets, 0)
    s.open()
    s.push(statsEnvelope(5))
    expect(client.state()).toBe('connected')

    vi.advanceTimersByTime(15000) // the heartbeat ping goes out (§4.3.2)
    expect(s.sentOf('ping')).toHaveLength(1)

    clock.advance(10000)
    // The pong timeout fires inside this advance: it discards the dead
    // socket and calls beginConnect immediately, from inside its own
    // setTimeout callback, where createSocket throws on this (the second)
    // attempt. An unguarded caller lets the throw escape the timer
    // callback, which vitest surfaces as advanceTimersByTime itself
    // throwing - the same signature the #122 suite above relies on.
    expect(() => vi.advanceTimersByTime(10000)).not.toThrow()
    expect(attempt).toBe(2)
    expect(client.diagnostics().lastError).toMatchObject({
      source: 'local',
      code: 'socket_failed',
    })

    // SPEC 4.3.1/review issue #131 follow-up: a retry is scheduled, so the
    // state stays `connecting`, the same as the #122 suite above - dropping
    // to `offline` would claim the app had given up while a timer is
    // pending. The assertion the guard exists for: catching the throw and
    // doing nothing else would also pass this line (both leave the state
    // untouched from its already-`connecting` value), and would leave the
    // client stopped with no timer pending. It is the backoff step below
    // that tells the two apart - nothing schedules it unless the guard's
    // catch handed off to the ordinary backoff.
    expect(client.state()).toBe('connecting')
    waitOutBackoff(nth(BACKOFF_CAPS_S, 0))
    expect(attempt).toBe(3)
    expect(liveSockets).toHaveLength(2)
    nth(liveSockets, 1).open()
    nth(liveSockets, 1).push(statsEnvelope(5))
    expect(client.state()).toBe('connected')
  })
})

describe('diagnostics: latency is a measured local round trip, never the remote clock (§4.3.10)', () => {
  it('measures the round trip against the injected clock, not a constant and not the wall clock', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)

    clock.advance(15000)
    vi.advanceTimersByTime(15000) // the heartbeat ping goes out (§4.3.2)
    const pings = s.sentOf('ping')
    expect(pings).toHaveLength(1)
    const pingId = pings[0]?.['message_id'] as string

    clock.advance(37)
    s.push(withMessageId(pongEnvelope(), pingId))
    expect(client.diagnostics().latencyMs).toBe(37)
  })

  it('the remote pong.timestamp is never used - a pong stamped years off still reports the local measurement', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)

    clock.advance(15000)
    vi.advanceTimersByTime(15000)
    const pingId = s.sentOf('ping')[0]?.['message_id'] as string

    clock.advance(64)
    const remoteClockDisagreesByYears = 1_000_000_000 // seconds, i.e. 1970-ish - wildly wrong on purpose
    s.push({
      ...withMessageId(pongEnvelope(), pingId),
      timestamp: remoteClockDisagreesByYears,
    })
    expect(client.diagnostics().latencyMs).toBe(64)
  })

  it('a pong with no outstanding ping does not update the latency', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    expect(client.diagnostics().latencyMs).toBeNull()
    s.push(pongEnvelope()) // unsolicited: no ping was ever sent
    expect(client.diagnostics().latencyMs).toBeNull()
  })

  it('an unsolicited pong arriving after a real measurement does not change it - the duplicate case §4.3.10 names', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)

    clock.advance(15000)
    vi.advanceTimersByTime(15000)
    const pingId = s.sentOf('ping')[0]?.['message_id'] as string
    clock.advance(20)
    s.push(withMessageId(pongEnvelope(), pingId))
    expect(client.diagnostics().latencyMs).toBe(20)

    clock.advance(9000) // still well inside the pong timeout, no second ping sent yet
    s.push(pongEnvelope()) // a second, unsolicited pong on the same connection
    expect(client.diagnostics().latencyMs).toBe(20) // unchanged
  })

  it('latency is cleared when the socket goes, not carried across the gap', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)

    clock.advance(15000)
    vi.advanceTimersByTime(15000)
    const pingId = s.sentOf('ping')[0]?.['message_id'] as string
    clock.advance(5)
    s.push(withMessageId(pongEnvelope(), pingId))
    expect(client.diagnostics().latencyMs).toBe(5)

    s.serverClose(1006, '')
    expect(client.diagnostics().latencyMs).toBeNull()
  })
})

describe('diagnostics: onDiagnostics', () => {
  it('notifies on a diagnostics change and stops after the returned unsubscribe is called', () => {
    const { client, sockets } = setup()
    client.connect()
    const s = toConnected(sockets)
    const seen: Diagnostics[] = []
    const unsubscribe = client.onDiagnostics((d) => seen.push(d))

    s.push(errorEnvelope('internal_error', 'boom'))
    expect(seen.length).toBeGreaterThan(0)
    expect(seen[seen.length - 1]?.lastError).toMatchObject({
      code: 'internal_error',
      message: 'boom',
    })

    unsubscribe()
    const countBefore = seen.length
    s.push(errorEnvelope('bad_request', 'again'))
    expect(seen.length).toBe(countBefore) // no longer listening
  })

  it('is a second callback, not folded into onState: a latency change alone does not fire onState', () => {
    const clock = makeClock()
    const { client, sockets } = setup({}, { now: clock.now })
    client.connect()
    const s = toConnected(sockets)
    const states: ConnectionState[] = []
    client.onState((state) => states.push(state)) // subscribed after toConnected: nothing queued yet

    clock.advance(15000)
    vi.advanceTimersByTime(15000)
    const pingId = s.sentOf('ping')[0]?.['message_id'] as string
    clock.advance(11)
    s.push(withMessageId(pongEnvelope(), pingId))

    expect(client.diagnostics().latencyMs).toBe(11) // the measurement did happen
    expect(states).toHaveLength(0) // but onState was not the way it was announced
  })
})
