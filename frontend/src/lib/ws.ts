// WebSocket client layer of SPEC 4.3. Owns the connection state machine
// (4.3.1), the reconnect timings (4.3.2), the read/command queue split
// (4.3.3) and the token in localStorage (4.3.6). Everything a view needs to
// know about the link comes through `onState`/`onMessage`/`lastStats`; the
// stores that translate that into the UI live in `lib/stores.ts`, not here.

import type {
  IncomingGetAccessPoints,
  IncomingGetFaceStatus,
  IncomingGetGpsData,
  IncomingGetHandshakes,
  IncomingGetLog,
  IncomingGetPeers,
  IncomingGetScreen,
  IncomingGetStats,
  IncomingGpsData,
  IncomingReboot,
  IncomingSetMode,
  IncomingSetPasv,
  IncomingShutdown,
  OutgoingMessage,
  RestartReason,
  Stats,
} from './protocol'

export type ConnectionState =
  | 'connecting'
  | 'connected'
  | 'degraded'
  | 'offline'
  | 'unauthorized'
  | 'restarting'

export interface SocketLike {
  send(data: string): void
  close(code?: number, reason?: string): void
  onopen: (() => void) | null
  onclose: ((event: { code: number; reason: string }) => void) | null
  onerror: ((error: unknown) => void) | null
  onmessage: ((event: { data: string }) => void) | null
}

export interface WsDeps {
  createSocket(url: string): SocketLike
  random(): number // the jitter draw, so a distribution can be asserted
  // SPEC 4.3.10: the clock the latency measurement is taken against, for the
  // same reason random() is already here -- a duration asserted against the
  // wall clock is asserted against how long the test host took to run the
  // test, not against the round trip it claims to measure.
  now(): number
}

export interface WsClientOptions {
  url: string
  token?: string
  deps?: Partial<WsDeps>
}

// SPEC 4.3.1: "rejected" (a token was sent and the plugin refused it) and
// "required" (no token was sent, whether because none is stored or the
// close that would have said so never arrived) differ only in the sentence
// shown, so this is a field on the unauthorized state rather than a state
// of its own.
export type UnauthorizedReason = 'rejected' | 'required'

// SPEC 4.3.10: the only three codes recordLocalError() ever writes. `LastError`
// carries this type, not `string`, for `source: 'local'`, so
// formatLastErrorCode's `source === 'local'` switch is checked total by the
// compiler instead of carrying a default arm no runtime value could ever
// reach. `socket_failed` (issue #122) is the third: `createSocket` throwing
// from inside the reconnect chain's bare `setTimeout` callback, where a
// `new WebSocket` that fails to open has nothing above it to catch it.
export type LocalErrorCode = 'pong_timeout' | 'connect_timeout' | 'socket_failed'

/**
 * SPEC 4.3.10: what the client keeps of the last thing that went wrong,
 * either an `error` frame's `code` and `message`, the close the app did not
 * ask for, or a drop this client noticed itself before any close event
 * arrived. `source` discriminates the three rather than leaving a caller to
 * guess whether a numeric-looking `code` is a wire code that happens to be
 * digits, and each arm carries the `code` type that source actually has: a
 * frame's `code` is remote-chosen and never trusted (SPEC 4.3.10), so it
 * stays `string`; a close's `code` is the browser's close code rendered in
 * digits, also `string`; `'local'` names the detection this client made
 * itself (`pong_timeout`, `connect_timeout`, `socket_failed`) rather than a
 * number, so it is typed to `LocalErrorCode`, and its `message` is always
 * empty, there being no remote to have said anything.
 *
 * `at` is a local timestamp (SPEC 4.3.10's clock, `WsDeps.now`), so a view
 * can say when this happened rather than leaving the reader to assume it
 * was now.
 */
export type LastError =
  | { source: 'frame'; code: string; message: string; at: number }
  | { source: 'close'; code: string; message: string; at: number }
  | { source: 'local'; code: LocalErrorCode; message: string; at: number }

/**
 * SPEC 4.3.10: the pair Settings needs and `onState` must not carry, because
 * latency changes without the state changing, once per ping interval, and a
 * client that fired `onState` to announce a round trip would be telling
 * every subscriber the state moved when it did not.
 */
export interface Diagnostics {
  lastError: LastError | null
  latencyMs: number | null
}

/**
 * SPEC 4.3.10: what `lastStats()` hands over -- the payload beside the
 * envelope's own `timestamp`, not the payload alone. `Stats` carries no
 * timestamp of its own; it lives on `OutgoingStats`, and a store adapter
 * seeding a channel from this at mount (§4.4.2) needs that stamp for the
 * same ordering rule §4.4.1 already applies to a live `stats`/`channel_hop`
 * push, or the seeded channel is written unguarded and the rule only starts
 * working from the next thing that arrives.
 */
export interface StatsSnapshot {
  stats: Stats
  timestamp: number
}

/**
 * SPEC 4.5.2.2: what an `error` frame rejects a read or a command with,
 * carrying `code` alongside the human `message` `new Error(message)` used to
 * keep alone. One class for both paths -- correlate() below constructs
 * exactly one of these, whichever kind of pending entry the frame settles --
 * because a read and a command disagreeing about what a rejection carries
 * would be two rules to keep in step for no gain.
 *
 * `code` is a plain `string`, not `ErrorCode`: `error.json`'s `code` is not
 * validated at the boundary (issue #109), so a unit -- or something in front
 * of one -- can put any string there, and typing it as the closed enum would
 * claim a guarantee the wire does not give. It is also never rendered
 * itself (SPEC 4.5.3): a caller matches it against the sentences it knows
 * and falls back to a generic one for anything else.
 *
 * Extends `Error` rather than replacing it, so `.message` keeps meaning what
 * it always did to a caller that only ever looked at that.
 */
export class RemoteError extends Error {
  readonly code: string

  constructor(message: string, code: string) {
    super(message)
    this.name = 'RemoteError'
    this.code = code
  }
}

// the read subset of IncomingMessage, every
// type request()/command() is allowed to send that is not auth (sent only
// from beginConnect's onopen), gps_data (its own method, sendGps) or ping
// (its own internal heartbeat, sendPing). Narrowed from the generated
// union in protocol.ts rather than declared in parallel with it.
type ReadRequest =
  | IncomingGetAccessPoints
  | IncomingGetFaceStatus
  | IncomingGetGpsData
  | IncomingGetHandshakes
  | IncomingGetLog
  | IncomingGetPeers
  | IncomingGetScreen
  | IncomingGetStats

// SPEC 4.3.3: the four state commands, and
// the only messages command() may ever send. Unlike a read, a state
// command is refused outright rather than queued, see the comment in
// command() itself.
type StateCommand = IncomingReboot | IncomingSetMode | IncomingSetPasv | IncomingShutdown

/** The payload half of a read or command frame: everything but the envelope. */
type Payload<T> = Omit<T, 'type' | 'message_id'>
// Optional only when there is nothing to pass. `Payload<T>` for `get_stats` is `{}`, which
// every object satisfies, so `data` may be omitted; for `set_mode` it carries a required
// `mode`, and omitting it would put a frame on the wire that the plugin answers with
// `bad_request` (SPEC 2.3).
type PayloadArgs<T> = {} extends Payload<T> ? [data?: Payload<T>] : [data: Payload<T>]

export interface WsClient {
  connect(): void
  close(): void
  state(): ConnectionState
  unauthorizedReason(): UnauthorizedReason | null
  // SPEC 4.4.1 (issue #131): null in every state but `restarting`, the same
  // rule unauthorizedReason above already follows for `unauthorized`.
  restartReason(): RestartReason | null
  lastStats(): StatsSnapshot | null
  diagnostics(): Diagnostics
  onState(handler: (state: ConnectionState) => void): () => void
  onMessage(handler: (message: OutgoingMessage) => void): () => void
  // SPEC 4.3.10: a second callback rather than folded into onState, see the
  // comment on Diagnostics above.
  onDiagnostics(handler: (diagnostics: Diagnostics) => void): () => void
  request<K extends ReadRequest['type']>(
    type: K,
    ...rest: PayloadArgs<Extract<ReadRequest, { type: K }>>
  ): Promise<OutgoingMessage>
  command<K extends StateCommand['type']>(
    type: K,
    ...rest: PayloadArgs<Extract<StateCommand, { type: K }>>
  ): Promise<OutgoingMessage>
  sendGps(data: Payload<IncomingGpsData>): void
}

// SPEC 4.3.2: the staleness threshold and the connecting bound share the same
// number by design, not by coincidence, see the "connecting has a bound" note.
const STALENESS_THRESHOLD_S = 55
const CONNECTING_BOUND_MS = 55_000
const PING_INTERVAL_MS = 15_000
const PONG_TIMEOUT_MS = 10_000
const REQUEST_TIMEOUT_MS = 15_000
const PATIENCE_MODE_CHANGE_MS = 90_000
const PATIENCE_REBOOT_MS = 180_000
// Full jitter backoff (SPEC 4.3.2): cap sequence in seconds, holding at the
// last value once exhausted rather than growing without bound.
const BACKOFF_CAPS_S = [1, 2, 4, 8, 16, 30, 30]

// SPEC 4.3.3/4.5.2.2: whether a state command may be sent at all is this
// file's rule, asked rather than restated. Exported so a control can ask it
// to decide its own enabled state instead of carrying a copy of the list --
// command()'s own guard below calls it too, so the two can never drift.
export function canSendCommand(state: ConnectionState): boolean {
  return state === 'connected' || state === 'degraded'
}

const TOKEN_STORAGE_KEY = 'companion.token'

export function readStoredToken(): string | null {
  // a stored empty string means no token is
  // set (SPEC 4.3.9), so it is folded into null here rather than left for
  // effectiveToken alone to coerce. The signature should mean what it says
  // at the one place a caller reads it directly.
  //
  // SPEC 4.7: storage that refuses to answer does not stop the app, the
  // same rule storeToken below is held to. This is reached from
  // effectiveToken on every connect() call, so a throw here (storage
  // disabled or full) would take the connection attempt down with it
  // instead of falling back to the tokenless path.
  let stored: string | null
  try {
    stored = window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    stored = null
  }
  return stored === '' ? null : stored
}

/**
 * Clearing (`null`) removes this device's copy only, it is not revocation
 * (SPEC 4.3.6): the plugin still accepts the token until `config.toml` changes.
 *
 * Refuses anything that is not a string or null: SPEC 4.7's Settings layer
 * builds a host patch as `Partial<Omit<Host, 'id'>>`, which TypeScript lets
 * carry an explicit `token: undefined`, and that would otherwise coerce to
 * the literal string "undefined" here rather than clearing the key. This
 * function does not trust its caller's types any more than the request
 * path trusts the network's.
 *
 * SPEC 4.7: storage that refuses to answer does not stop the app. This is
 * one of the two places lib/settings.ts calls unconditionally -- loadSettings
 * on every startup, activateHost on every switch -- so a throw here (storage
 * disabled or full) cannot be left to propagate: it would abort a settings
 * update after companion.settings was already persisted, leaving the
 * in-memory store never updated to match.
 *
 * SPEC 4.7: a token write that fails clears the key rather than leaving
 * it. Without this, a failed `setItem` (quota, storage disabled mid-way)
 * during activateHost(B) leaves TOKEN_STORAGE_KEY holding the previous
 * host's token, and the next connect() sends unit A's token to unit B.
 * An absent token fails authentication loudly and recoverably; a wrong
 * one authenticates to the wrong unit. The best-effort `removeItem` is
 * itself guarded: it can throw for the same reasons `setItem` just did.
 */
export function storeToken(token: string | null): void {
  try {
    if (typeof token !== 'string') {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY)
    } else {
      window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
    }
  } catch {
    try {
      window.localStorage.removeItem(TOKEN_STORAGE_KEY)
    } catch {
      // Nothing more to act on here: storage is not answering at all.
    }
  }
}

// SPEC 4.3.9: re-read on every connect attempt, not captured once at
// construction, otherwise a token the plugin already rejected is
// re-sent for ever, and clearing the stored token to reach the tokenless
// path does nothing. `options.token` is an explicit override for a caller
// that supplies one directly and wins when present; the stored value is
// the fallback, and an empty string there counts as no token.
function effectiveToken(options: WsClientOptions): string {
  return options.token || readStoredToken() || ''
}

function defaultCreateSocket(url: string): SocketLike {
  // The DOM WebSocket's handler properties are typed to accept an Event
  // argument SocketLike does not declare; the cast is the seam boundary and
  // nothing inside this file relies on the discarded argument.
  return new WebSocket(url) as unknown as SocketLike
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

// the DOM WebSocket throws InvalidStateError
// from send() when the socket is already CLOSING or CLOSED, a state
// reachable between the transport noticing a drop and onclose actually
// firing. From sendPing that throw would escape a setTimeout callback with
// nothing above it to catch it, and every other call site would tear down
// state twice over (once here, once from the onclose that is already on
// its way). The guard is not paranoia: it is what keeps a dying socket
// from taking the whole client down with it. handleClose does the actual
// recovery once the close event lands; this just keeps the throw from
// pre-empting it.
function trySend(socket: SocketLike, payload: string): void {
  try {
    socket.send(payload)
  } catch {
    // Nothing to act on here: onclose is on its way and will requeue
    // reads / reject in-flight commands as it always does.
  }
}

interface PendingRead {
  message_id: string
  type: string
  data: unknown
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
  timeoutHandle: ReturnType<typeof setTimeout> | null
}

interface PendingCommand {
  resolve: (message: OutgoingMessage) => void
  reject: (error: Error) => void
  timeoutHandle: ReturnType<typeof setTimeout> | null
}

export function createWsClient(options: WsClientOptions): WsClient {
  const deps: WsDeps = {
    createSocket: options.deps?.createSocket ?? defaultCreateSocket,
    random: options.deps?.random ?? (() => Math.random()),
    now: options.deps?.now ?? (() => Date.now()),
  }

  let currentState: ConnectionState = 'offline'
  let currentSocket: SocketLike | null = null
  let stopped = true // true until connect() is called at least once

  // Per-connection bookkeeping, reset in beginConnect().
  let hasSentAuth = false
  let connectionReady = false
  let firstStatsReceivedThisConnection = false
  let socketOpenedThisConnection = false
  // SPEC 4.3.10: also doubles as the "does a close overwrite the last
  // error" guard below. The rule is scoped to `unauthorized` alone, not to
  // any frame error on the connection -- a benign `pasv_unavailable` at ten
  // in the morning must not suppress the drop that ends the link four hours
  // later, so this is the same flag `enterUnauthorized('rejected')` already
  // reads, not a second one that would drift from it.
  let lastErrorWasUnauthorized = false

  // SPEC 4.3.1: the fallback for a transport that swallows the server's 1008
  // close along with everything else. Counted only while no token is stored,
  // only across connections that never saw a `stats` message, and only for a
  // socket that actually opened -- a connection refused never reached the
  // plugin at all and says nothing about authentication.
  //
  // close() does not reset this counter, so a client stopped mid-count and
  // later resumed through connect() would carry the old count into the new
  // run of attempts. Unreachable today -- lib/session.ts builds a fresh
  // client per host switch and never reconnects a closed one -- but the
  // next reconnect() has to meet this note before it meets the bug: a
  // counter that outlives what it counts.
  let closesBeforeAnyStats = 0

  let restartReasonValue: RestartReason | null = null
  let unauthorizedReasonValue: UnauthorizedReason | null = null
  // SPEC 4.3.10: the payload and the envelope stamp it arrived under, kept
  // side by side so lastStats() can hand both to a store adapter seeding a
  // channel at mount (§4.4.2) -- `Stats` alone has nowhere to carry it.
  let lastStatsValue: Stats | null = null
  let lastStatsTimestampValue: number | null = null
  let lastErrorValue: LastError | null = null
  let latencyMsValue: number | null = null
  // SPEC 4.3.10: the local send stamp for the outstanding ping, cleared the
  // moment a pong answers it (or the socket goes) so a later pong -- late,
  // duplicate or unsolicited -- has nothing to measure against.
  let pingSentAtMs: number | null = null

  let backoffAttempt = 0
  let backoffTimer: ReturnType<typeof setTimeout> | null = null
  let connectingBoundTimer: ReturnType<typeof setTimeout> | null = null
  let patienceTimer: ReturnType<typeof setTimeout> | null = null
  let pingTimer: ReturnType<typeof setTimeout> | null = null
  let pongTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatActive = false

  let messageCounter = 0
  const stateHandlers = new Set<(state: ConnectionState) => void>()
  const messageHandlers = new Set<(message: OutgoingMessage) => void>()
  const diagnosticsHandlers = new Set<(diagnostics: Diagnostics) => void>()

  const pendingReads = new Map<string, PendingRead>()
  let queue: string[] = [] // ids of pendingReads not yet sent on the current connection
  const inFlightCommands = new Map<string, PendingCommand>()

  function nextMessageId(): string {
    // the per-client counter alone already
    // guarantees uniqueness for this client, so the wall clock added
    // nothing to the id and cost a seam with no real call site.
    messageCounter += 1
    return `msg-${messageCounter}`
  }

  function notifyDiagnostics(): void {
    const value: Diagnostics = { lastError: lastErrorValue, latencyMs: latencyMsValue }
    for (const handler of [...diagnosticsHandlers]) handler(value)
  }

  function setLastError(next: LastError | null): void {
    lastErrorValue = next
    notifyDiagnostics()
  }

  function setLatency(next: number | null): void {
    latencyMsValue = next
    notifyDiagnostics()
  }

  function setState(next: ConnectionState): void {
    const wasLive = currentState === 'connected' || currentState === 'degraded'
    const enteringLive = next === 'connected' || next === 'degraded'
    const leavingLive = wasLive && !enteringLive
    // SPEC 4.3.10: "cleared on the transition, never on the state being
    // re-entered" -- a `stats` push calls setState('connected'/'degraded')
    // again once per broadcast interval while the connection is already
    // live, and a clearing rule keyed to the target state alone would erase
    // every error that arrives on a live connection seconds after it was
    // recorded. Computed the same way leavingLive is: from whether the
    // state was already admitted, not from where it is going.
    const admittingNow = enteringLive && !wasLive
    currentState = next
    if (enteringLive) {
      backoffAttempt = 0
      if (!heartbeatActive) armHeartbeat()
      // SPEC 4.3.10: cleared once the connection is admitted -- 'connected',
      // or 'degraded', which SPEC 4.3.10 counts as admitted too: a connection
      // carrying stale data, not a failed one. Not before: the failure that
      // matters is the one still happening, and a client that retries every
      // few seconds would otherwise erase the reason at the start of each
      // attempt.
      if (admittingNow && lastErrorValue !== null) setLastError(null)
    } else if (leavingLive) {
      disarmHeartbeat()
    }
    if (next !== 'restarting') clearPatienceTimer()
    for (const handler of [...stateHandlers]) handler(next)
  }

  function cancelBackoffTimer(): void {
    if (backoffTimer !== null) {
      clearTimeout(backoffTimer)
      backoffTimer = null
    }
  }

  function clearConnectingBoundTimer(): void {
    if (connectingBoundTimer !== null) {
      clearTimeout(connectingBoundTimer)
      connectingBoundTimer = null
    }
  }

  function clearPatienceTimer(): void {
    if (patienceTimer !== null) {
      clearTimeout(patienceTimer)
      patienceTimer = null
    }
  }

  function armHeartbeat(): void {
    heartbeatActive = true
    scheduleNextPing()
  }

  function disarmHeartbeat(): void {
    heartbeatActive = false
    if (pingTimer !== null) {
      clearTimeout(pingTimer)
      pingTimer = null
    }
    if (pongTimer !== null) {
      clearTimeout(pongTimer)
      pongTimer = null
    }
    pingSentAtMs = null
    // SPEC 4.3.10: latency describes a link, and a link whose heartbeat just
    // stopped has no round trip to show -- not carried across the gap, the
    // same reasoning that keeps a dropped session's stats off screen.
    if (latencyMsValue !== null) setLatency(null)
  }

  function scheduleNextPing(): void {
    // clears any timer already running before
    // arming a new one. armHeartbeat and onPong both call this, and an
    // unsolicited or duplicate pong would otherwise leave the old timer live
    // alongside the new one, doubling the ping rate from then on.
    if (pingTimer !== null) {
      clearTimeout(pingTimer)
    }
    pingTimer = setTimeout(sendPing, PING_INTERVAL_MS)
  }

  function sendPing(): void {
    pingTimer = null
    if (!currentSocket) return
    // SPEC 4.3.10: the local send stamp, taken here rather than read off the
    // reply -- pong.json carries its own timestamp and this measurement
    // never reads it, so the round trip is a duration and not a subtraction
    // of two clocks that may disagree by days on a unit with no RTC.
    pingSentAtMs = deps.now()
    trySend(currentSocket, JSON.stringify({ type: 'ping' }))
    pongTimer = setTimeout(onPongTimeout, PONG_TIMEOUT_MS)
  }

  function onPong(): void {
    if (pongTimer !== null) {
      clearTimeout(pongTimer)
      pongTimer = null
    }
    // SPEC 4.3.10: only a pong answering an outstanding ping updates the
    // latency -- guarded on the send stamp itself rather than on pongTimer,
    // so a pong that somehow arrives with no outstanding ping to answer
    // measures nothing instead of a NaN duration.
    if (pingSentAtMs !== null) {
      setLatency(deps.now() - pingSentAtMs)
      pingSentAtMs = null
    }
    scheduleNextPing()
  }

  // SPEC 4.3.10: a drop this client detected itself, recorded the same as
  // any other last error -- `source: 'local'` because it is neither a frame
  // nor a close, `message` empty because there is no remote to have said
  // anything. Called from both places this client notices a dead link on
  // its own, before discardSocketSilently() unhooks onclose and the
  // browser's own close event (if it ever arrives at all) is lost.
  function recordLocalError(code: LocalErrorCode): void {
    setLastError({ source: 'local', code, message: '', at: deps.now() })
  }

  function onPongTimeout(): void {
    pongTimer = null
    // SPEC 4.3.1/4.3.2: no pong within the timeout is a dead socket, not a
    // slow one. `connected` and `degraded` both go straight back to
    // `connecting`, immediately rather than through the backoff schedule.
    recordLocalError('pong_timeout')
    discardSocketSilently()
    disarmHeartbeat()
    rejectInFlightCommands('the connection dropped while the command was in flight')
    requeueUnresolvedReads()
    connectionReady = false
    currentSocket = null
    try {
      beginConnect(true)
    } catch {
      // SPEC 4.3.10 (issue #122): the same `new WebSocket` throw guarded in
      // scheduleReconnectAttempt's callback, reached here too -- this timer
      // calls beginConnect directly, immediately, rather than through the
      // backoff schedule (SPEC 4.3.1/4.3.2), and has nothing above it either.
      // This one is a one-shot: the pong timeout fires once and has already
      // discarded its socket, so doing nothing on a throw would leave the
      // client stopped with no timer pending and nothing to restart it.
      // Recording the failure and falling back into the ordinary backoff
      // schedule keeps that from being a dead end.
      recordLocalError('socket_failed')
      scheduleReconnectAttempt(true)
    }
  }

  function discardSocketSilently(): void {
    if (!currentSocket) return
    currentSocket.onopen = null
    currentSocket.onclose = null
    currentSocket.onerror = null
    currentSocket.onmessage = null
    try {
      currentSocket.close()
    } catch {
      // The socket may already be closing; nothing to act on either way.
    }
  }

  function rejectInFlightCommands(message: string): void {
    for (const entry of inFlightCommands.values()) {
      if (entry.timeoutHandle !== null) clearTimeout(entry.timeoutHandle)
      entry.reject(new Error(message))
    }
    inFlightCommands.clear()
  }

  function requeueUnresolvedReads(): void {
    // SPEC 4.3.3: a read is queued and re-sent, so everything still pending,
    // sent on the dead connection or never sent at all, goes back on the
    // queue whole, to be resent once the next connection is usable. SPEC
    // 4.3.8: the 15 s clock started at the call and runs through the drop,
    // so the timeout set in request() is left untouched here.
    queue = Array.from(pendingReads.keys())
  }

  function backoffDelayMs(): number {
    const index = Math.min(backoffAttempt, BACKOFF_CAPS_S.length - 1)
    const capS = BACKOFF_CAPS_S[index] as number
    // Full jitter (SPEC 4.3.2): drawn uniformly from [0, cap], so the cap is
    // an upper bound and not a schedule.
    return deps.random() * capS * 1000
  }

  function scheduleReconnectAttempt(enterConnectingState: boolean): void {
    cancelBackoffTimer()
    const delay = backoffDelayMs()
    backoffAttempt = Math.min(backoffAttempt + 1, BACKOFF_CAPS_S.length - 1)
    backoffTimer = setTimeout(() => {
      backoffTimer = null
      try {
        beginConnect(enterConnectingState)
      } catch {
        // SPEC 4.3.10 (issue #122): the same `new WebSocket` throw §4.8
        // protects the first attempt from, here on the path taken hundreds
        // of times more often -- this callback has nothing above it either.
        // Recorded as `socket_failed` rather than left to escape into the
        // event loop, and the backoff loop tries again: a browser that
        // refused to open a socket once may not refuse the next one.
        recordLocalError('socket_failed')
        scheduleReconnectAttempt(enterConnectingState)
      }
    }, delay)
  }

  function enterOffline(): void {
    setState('offline')
    scheduleReconnectAttempt(true)
  }

  function enterUnauthorized(reason: UnauthorizedReason): void {
    // SPEC 4.3.1: the one state that suspends reconnection, it cannot
    // resolve without the user, so retrying it forever just spams the log.
    cancelBackoffTimer()
    unauthorizedReasonValue = reason
    setState('unauthorized')
  }

  function handleRestartingClose(): void {
    if (restartReasonValue === 'shutdown') {
      // SPEC 4.3.1: terminal by guard, not by event, there is nothing to be
      // patient for and nothing to reconnect to.
      return
    }
    scheduleReconnectAttempt(false)
  }

  function onPatienceExpired(): void {
    // SPEC 4.3.1: patience expiring hands off to the ordinary offline/backoff
    // cycle rather than continuing the hidden restart-specific retry, so a
    // slow reboot degrades into an experience identical to any other drop.
    patienceTimer = null
    cancelBackoffTimer()
    discardSocketSilently()
    disarmHeartbeat()
    rejectInFlightCommands('the connection dropped while the command was in flight')
    requeueUnresolvedReads()
    connectionReady = false
    currentSocket = null
    enterOffline()
  }

  function handleRestarting(data: { reason: RestartReason; mode: unknown }): void {
    restartReasonValue = data.reason
    clearConnectingBoundTimer()
    setState('restarting')
    clearPatienceTimer()
    if (data.reason !== 'shutdown') {
      const patienceMs = data.reason === 'reboot' ? PATIENCE_REBOOT_MS : PATIENCE_MODE_CHANGE_MS
      patienceTimer = setTimeout(onPatienceExpired, patienceMs)
    }
  }

  function handleStats(stats: Stats, timestamp: number): void {
    lastStatsValue = stats
    lastStatsTimestampValue = timestamp
    const fresh = stats.sessionAge !== null && stats.sessionAge < STALENESS_THRESHOLD_S
    if (!firstStatsReceivedThisConnection) {
      firstStatsReceivedThisConnection = true
      closesBeforeAnyStats = 0
      clearConnectingBoundTimer()
    }
    if (
      currentState === 'connecting' ||
      currentState === 'restarting' ||
      currentState === 'connected' ||
      currentState === 'degraded'
    ) {
      setState(fresh ? 'connected' : 'degraded')
    }
  }

  function correlate(message: OutgoingMessage): void {
    // SPEC 4.3.8: a read resolves on its typed reply, a command on its
    // `acknowledgment`, not on whichever arrives first. Router.handle
    // appends the acknowledgment before the typed reply for every message
    // that carried a message_id, so the first correlated frame is always
    // the acknowledgment; a read that settled there would resolve with a
    // receipt and never see the handshakes/stats/access_points/log frame
    // it actually asked for. An `error` carrying the message_id settles
    // either kind, as a failure.
    const id = message.message_id
    if (!id) return

    if (message.type === 'error') {
      // SPEC 4.5.2.2: the rejection carries the code, for reads as well as
      // commands -- one code path, not two.
      const err = new RemoteError(message.data.message, message.data.code)
      const read = pendingReads.get(id)
      if (read) {
        if (read.timeoutHandle !== null) clearTimeout(read.timeoutHandle)
        pendingReads.delete(id)
        read.reject(err)
        return
      }
      const command = inFlightCommands.get(id)
      if (command) {
        if (command.timeoutHandle !== null) clearTimeout(command.timeoutHandle)
        inFlightCommands.delete(id)
        command.reject(err)
      }
      return
    }

    if (message.type === 'acknowledgment') {
      // For set_mode/set_pasv/reboot/shutdown the acknowledgment is the
      // reply (SPEC 4.3.4); for a read it is only a transit receipt, so a
      // read entry is left pending here for its typed reply instead.
      const command = inFlightCommands.get(id)
      if (command) {
        if (command.timeoutHandle !== null) clearTimeout(command.timeoutHandle)
        inFlightCommands.delete(id)
        command.resolve(message)
      }
      return
    }

    const read = pendingReads.get(id)
    if (read) {
      if (read.timeoutHandle !== null) clearTimeout(read.timeoutHandle)
      pendingReads.delete(id)
      read.resolve(message)
    }
  }

  function flushQueue(): void {
    // SPEC 4.3.8: the timeout is armed once, in request(), from the call
    // rather than from the send. A read queued while offline must not get
    // a fresh 15 s here every time a new connection tries to flush it.
    if (!connectionReady || !currentSocket) return
    const socket = currentSocket
    while (queue.length > 0) {
      const id = queue.shift() as string
      const entry = pendingReads.get(id)
      if (!entry) continue // resolved, rejected or timed out already
      // the envelope is written after the
      // caller's data, not before it, so a payload key named `type` or
      // `message_id` can never overwrite the frame's own. See the same
      // ordering in command() and sendGps() below.
      const frame: Record<string, unknown> = {}
      if (isRecord(entry.data)) Object.assign(frame, entry.data)
      frame.type = entry.type
      frame.message_id = entry.message_id
      trySend(socket, JSON.stringify(frame))
    }
  }

  function handleMessage(raw: string): void {
    let message: OutgoingMessage
    try {
      const parsed: unknown = JSON.parse(raw)
      if (!isRecord(parsed) || typeof parsed.type !== 'string') return
      message = parsed as unknown as OutgoingMessage
    } catch {
      return
    }

    if (!connectionReady && !hasSentAuth) {
      // SPEC 4.3.9: with no token configured, the arrival of any frame is
      // the unit saying it admitted the socket (its burst goes out on
      // accept, SPEC 2.4), that is what unblocks the queue instead of
      // `onopen`.
      connectionReady = true
      flushQueue()
    }

    // each subscriber is isolated so a bug in
    // one cannot swallow the frame's own correlation below, otherwise a
    // read that arrived cleanly would still wait out its full 15 s because
    // correlate() never ran.
    for (const handler of [...messageHandlers]) {
      try {
        handler(message)
      } catch {
        // The subscriber's own concern; this file has nothing to add.
      }
    }

    switch (message.type) {
      case 'stats':
        handleStats(message.data, message.timestamp)
        break
      case 'restarting':
        handleRestarting(message.data)
        break
      case 'error':
        if (message.data.code === 'unauthorized') lastErrorWasUnauthorized = true
        // SPEC 4.3.10: every code goes through, including the ones SPEC
        // 4.3.5 routes elsewhere -- log_unavailable has its own sentence in
        // the Log view and it is still the last error the connection saw.
        setLastError({
          source: 'frame',
          code: message.data.code,
          message: message.data.message,
          at: deps.now(),
        })
        break
      case 'pong':
        onPong()
        break
      case 'acknowledgment':
        if (message.data.acknowledged === 'auth') {
          // SPEC 4.3.1: the client sends nothing before its auth is
          // acknowledged, which is what keeps the unauthorized/offline
          // discrimination on close honest.
          connectionReady = true
          flushQueue()
        }
        break
      default:
        break
    }

    correlate(message)
  }

  function handleClose(event: { code: number; reason: string }): void {
    clearConnectingBoundTimer()
    disarmHeartbeat()
    rejectInFlightCommands('the connection dropped while the command was in flight')
    requeueUnresolvedReads()
    currentSocket = null
    connectionReady = false

    // SPEC 4.3.10: a close records an error only when the app did not ask
    // for it. close() itself never reaches here -- discardSocketSilently()
    // detaches this handler before the socket actually closes, which is
    // what keeps a locally-requested disconnect from recording anything;
    // `!stopped` is the same rule held a second time, at the one place the
    // caller's intent is known for certain. A close reached while
    // `restarting` is the other kind of close the app asked for --
    // `reboot`, `shutdown` and a mode change all end the socket on
    // purpose -- and records nothing either: the app knows what it did and
    // the owner watched themselves do it, so `The connection closed (code
    // 1006)` would explain an event that needs no explanation, and for
    // `shutdown` the client is terminal by guard and never re-admitted, so
    // the clearing rule never fires and that sentence would stay for the
    // life of the app. A close does not overwrite a last error whose code
    // was `unauthorized`, and only that code: the rule exists for the
    // plugin sending `unauthorized` and then closing with 1008, not for any
    // frame error on the connection, so a benign `pasv_unavailable` earlier
    // on this same connection must not suppress the drop that ends it
    // later. Same scope SPEC 4.3.1 gives the unauthorized latch, and the
    // same flag.
    if (!stopped && currentState !== 'restarting' && !lastErrorWasUnauthorized) {
      setLastError({ source: 'close', code: `${event.code}`, message: event.reason, at: deps.now() })
    }

    if (currentState === 'restarting') {
      handleRestartingClose()
      return
    }
    if (stopped) return

    if (lastErrorWasUnauthorized) {
      // SPEC 4.3.1: an `error` frame with code unauthorized means a token
      // was sent and refused.
      enterUnauthorized('rejected')
      return
    }
    if (event.code === 1008 && !hasSentAuth) {
      // SPEC 4.3.1: a bare 1008 with no `auth` frame sent is the
      // authentication timeout closing an unauthenticated socket.
      enterUnauthorized('required')
      return
    }
    if (event.code === 1008 && hasSentAuth) {
      enterOffline()
      return
    }

    // Any other close, or the socket failed to open.
    if (!effectiveToken(options) && !firstStatsReceivedThisConnection && socketOpenedThisConnection) {
      closesBeforeAnyStats += 1
      if (closesBeforeAnyStats >= 3) {
        closesBeforeAnyStats = 0
        // SPEC 4.3.1: inferred, for a transport that can swallow the 1008
        // above along with everything else, the fallback reason is the
        // same as the close it stands in for.
        enterUnauthorized('required')
        return
      }
    }
    enterOffline()
  }

  function beginConnect(enterConnectingState: boolean): void {
    cancelBackoffTimer()
    hasSentAuth = false
    connectionReady = false
    firstStatsReceivedThisConnection = false
    socketOpenedThisConnection = false
    lastErrorWasUnauthorized = false
    unauthorizedReasonValue = null

    if (enterConnectingState) {
      setState('connecting')
      clearConnectingBoundTimer()
      // SPEC 4.3.1: the same 55 s bound as staleness, so a socket open
      // against a wedged plugin, or a dropped tether whose close never
      // arrives, still reaches `offline` rather than sitting here for ever.
      connectingBoundTimer = setTimeout(() => {
        connectingBoundTimer = null
        // SPEC 4.3.10: this is the same "detected the drop itself" case as
        // onPongTimeout, for the socket that never got as far as a
        // heartbeat to notice on.
        recordLocalError('connect_timeout')
        discardSocketSilently()
        disarmHeartbeat()
        rejectInFlightCommands('the connection dropped while the command was in flight')
        requeueUnresolvedReads()
        connectionReady = false
        currentSocket = null
        enterOffline()
      }, CONNECTING_BOUND_MS)
    }

    let socket: SocketLike
    try {
      socket = deps.createSocket(options.url)
    } catch (err) {
      // SPEC 4.3.10 (issue #122): a throw here leaves the state exactly as
      // set above -- `connecting`, because a retry is scheduled and that is
      // what §4.3.1 calls trying -- but the bound armed a few lines up was
      // armed for a socket that does not exist. Left running, it fires
      // `connect_timeout` over the `socket_failed` every caller of this
      // function already records, hiding the real cause from the owner.
      clearConnectingBoundTimer()
      throw err
    }
    currentSocket = socket
    socket.onopen = () => {
      socketOpenedThisConnection = true
      const token = effectiveToken(options)
      if (token) {
        hasSentAuth = true
        trySend(socket, JSON.stringify({ type: 'auth', token }))
        return
      }
      // SPEC 4.3.9: no token configured, send nothing on open. Flushing
      // here would draw an `error unauthorized` from the client's own read
      // against a unit that does require one, landing it in *rejected*
      // when it belongs in *required*. The unit's own first frame, handled
      // in handleMessage, is what proves the socket was admitted.
    }
    socket.onclose = (event) => handleClose(event)
    socket.onerror = () => {
      // The close event that follows carries the code the table above
      // discriminates on; nothing here needs a separate reaction.
    }
    socket.onmessage = (event) => handleMessage(event.data)
  }

  function connect(): void {
    stopped = false
    cancelBackoffTimer()
    if (currentState === 'connected' || currentState === 'degraded') {
      return // already usable; no table row tears down a live connection
    }
    discardSocketSilently()
    clearPatienceTimer()
    beginConnect(true)
  }

  function close(): void {
    stopped = true
    cancelBackoffTimer()
    clearPatienceTimer()
    clearConnectingBoundTimer()
    disarmHeartbeat()
    discardSocketSilently()
    rejectInFlightCommands('the client was closed')
    for (const entry of pendingReads.values()) {
      if (entry.timeoutHandle !== null) clearTimeout(entry.timeoutHandle)
      entry.reject(new Error('the client was closed'))
    }
    pendingReads.clear()
    queue = []
    currentSocket = null
    connectionReady = false
    // beginConnect resets this on every fresh
    // attempt, but close() never reached beginConnect again to do it, so a
    // close() from `unauthorized` left the reason readable underneath a
    // state that no longer carries it.
    unauthorizedReasonValue = null
    setState('offline')
  }

  function state(): ConnectionState {
    return currentState
  }

  function unauthorizedReason(): UnauthorizedReason | null {
    return unauthorizedReasonValue
  }

  // SPEC 4.4.1 (issue #131): gated on the current state rather than reset at
  // every beginConnect(), the way unauthorizedReasonValue is -- the restart
  // that begun in `restarting` calls beginConnect(false) itself, from the
  // same `restarting` state, to reconnect the socket the close just took
  // down (the `mode_change`/`reboot` row), and resetting the reason there
  // would blank it while the state it describes is still current. Reading it
  // through the state instead needs no such call site to remember to clear
  // it, and stays correct however this client leaves `restarting`.
  function restartReason(): RestartReason | null {
    return currentState === 'restarting' ? restartReasonValue : null
  }

  function lastStats(): StatsSnapshot | null {
    // lastStatsValue and lastStatsTimestampValue are only ever written
    // together, in handleStats, so a non-null payload always has a stamp
    // beside it.
    if (lastStatsValue === null || lastStatsTimestampValue === null) return null
    return { stats: lastStatsValue, timestamp: lastStatsTimestampValue }
  }

  function diagnostics(): Diagnostics {
    return { lastError: lastErrorValue, latencyMs: latencyMsValue }
  }

  function onState(handler: (state: ConnectionState) => void): () => void {
    stateHandlers.add(handler)
    return () => stateHandlers.delete(handler)
  }

  function onMessage(handler: (message: OutgoingMessage) => void): () => void {
    messageHandlers.add(handler)
    return () => messageHandlers.delete(handler)
  }

  function onDiagnostics(handler: (diagnostics: Diagnostics) => void): () => void {
    diagnosticsHandlers.add(handler)
    return () => diagnosticsHandlers.delete(handler)
  }

  // Typed against the read subset of the generated IncomingMessage union
  // (ReadRequest above) rather than against `string`, so a state command
  // cannot be asked for as a read and inherit the re-send the queue gives
  // reads (SPEC 4.3.3).
  //
  // One hole the compiler will not close: for a read whose payload is empty,
  // `Payload<T>` is `{}`, and TypeScript suppresses its excess-property check
  // against a target type with no members, so `request('get_stats',
  // { type: 'reboot' })` compiles. It cannot reach the wire as a reboot: the
  // envelope is written after the payload below, so neither `type` nor
  // `message_id` can be taken over by a caller, and a test asserts exactly
  // that. A payload with the wrong members, or a missing required one, is a
  // compile error rather than a runtime surprise (see PayloadArgs).
  function request<K extends ReadRequest['type']>(
    type: K,
    ...rest: PayloadArgs<Extract<ReadRequest, { type: K }>>
  ): Promise<OutgoingMessage> {
    const data = rest[0]
    return new Promise((resolve, reject) => {
      const id = nextMessageId()
      const entry: PendingRead = { message_id: id, type, data, resolve, reject, timeoutHandle: null }
      pendingReads.set(id, entry)
      queue.push(id)
      // SPEC 4.3.8: the clock starts here, at the call, not at the send,
      // a read asked for while offline is queued (4.3.3) rather than sent,
      // and the caller is a view that would otherwise wait for ever.
      entry.timeoutHandle = setTimeout(() => {
        entry.timeoutHandle = null
        pendingReads.delete(id)
        // left in as hygiene but unobservable
        // through behaviour, flushQueue already skips any id absent from
        // pendingReads, and the delete just above already removed this
        // one, so nothing outside this file can tell whether the id is
        // also filtered out of `queue` here. Kept because SPEC 4.3.3 says
        // "a read that times out while queued leaves the queue with it";
        // there is no test for this line on its own.
        queue = queue.filter((queuedId) => queuedId !== id)
        reject(new Error('request timed out'))
      }, REQUEST_TIMEOUT_MS)
      flushQueue()
    })
  }

  // typed against the four state commands
  // (StateCommand above), not against `string`, the queue-replay hazard
  // this refusal exists to prevent (SPEC 4.3.3) only holds if nothing but
  // those four can ever reach this method in the first place.
  function command<K extends StateCommand['type']>(
    type: K,
    ...rest: PayloadArgs<Extract<StateCommand, { type: K }>>
  ): Promise<OutgoingMessage> {
    const data = rest[0]
    const current = state()
    // SPEC 4.3.3: a state command is refused at the point of tapping and
    // never enters the queue, unlike a read, replaying it once the tether
    // returns could deliver a reboot or shutdown to a unit doing something
    // else entirely by then. the
    // `!currentSocket` half is defensive rather than reachable: every path
    // that nulls currentSocket (onPongTimeout, onPatienceExpired, the
    // connecting-bound timeout, handleClose) also leaves 'connected'/
    // 'degraded' synchronously, so `current` is never one of those two
    // states with a null socket. Folded into one guard rather than left as
    // a second branch with a message that would have read wrong.
    if (!canSendCommand(current) || !currentSocket) {
      return Promise.reject(new Error(`'${type}' is not allowed while ${current}`))
    }
    const socket = currentSocket
    return new Promise((resolve, reject) => {
      const id = nextMessageId()
      const entry: PendingCommand = { resolve, reject, timeoutHandle: null }
      inFlightCommands.set(id, entry)
      // the envelope is written after the
      // caller's data, not before it. See flushQueue() and sendGps().
      const frame: Record<string, unknown> = {}
      if (isRecord(data)) Object.assign(frame, data)
      frame.type = type
      frame.message_id = id
      trySend(socket, JSON.stringify(frame))
      entry.timeoutHandle = setTimeout(() => {
        entry.timeoutHandle = null
        inFlightCommands.delete(id)
        reject(new Error('command timed out'))
      }, REQUEST_TIMEOUT_MS)
    })
  }

  function sendGps(data: Payload<IncomingGpsData>): void {
    // SPEC 4.3.3: gps_data is discarded rather than queued or replayed, a
    // position is only true at the instant it was taken, so an old fix
    // resent after a reconnect would misattribute a handshake captured in
    // that window.
    if (!connectionReady || !currentSocket) return
    // the envelope is written after the
    // caller's data, not before it. See flushQueue() and command().
    const frame: Record<string, unknown> = {}
    if (isRecord(data)) Object.assign(frame, data)
    frame.type = 'gps_data'
    trySend(currentSocket, JSON.stringify(frame))
  }

  return {
    connect,
    close,
    state,
    unauthorizedReason,
    restartReason,
    lastStats,
    diagnostics,
    onState,
    onMessage,
    onDiagnostics,
    request,
    command,
    sendGps,
  }
}
