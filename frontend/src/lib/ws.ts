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
  lastStats(): Stats | null
  onState(handler: (state: ConnectionState) => void): () => void
  onMessage(handler: (message: OutgoingMessage) => void): () => void
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
  }

  let currentState: ConnectionState = 'offline'
  let currentSocket: SocketLike | null = null
  let stopped = true // true until connect() is called at least once

  // Per-connection bookkeeping, reset in beginConnect().
  let hasSentAuth = false
  let connectionReady = false
  let firstStatsReceivedThisConnection = false
  let socketOpenedThisConnection = false
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

  let restartReason: RestartReason | null = null
  let unauthorizedReasonValue: UnauthorizedReason | null = null
  let lastStatsValue: Stats | null = null

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

  function setState(next: ConnectionState): void {
    const enteringLive = next === 'connected' || next === 'degraded'
    const leavingLive = (currentState === 'connected' || currentState === 'degraded') && !enteringLive
    currentState = next
    if (enteringLive) {
      backoffAttempt = 0
      if (!heartbeatActive) armHeartbeat()
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
    trySend(currentSocket, JSON.stringify({ type: 'ping' }))
    pongTimer = setTimeout(onPongTimeout, PONG_TIMEOUT_MS)
  }

  function onPong(): void {
    if (pongTimer !== null) {
      clearTimeout(pongTimer)
      pongTimer = null
    }
    scheduleNextPing()
  }

  function onPongTimeout(): void {
    pongTimer = null
    // SPEC 4.3.1/4.3.2: no pong within the timeout is a dead socket, not a
    // slow one. `connected` and `degraded` both go straight back to
    // `connecting`, immediately rather than through the backoff schedule.
    discardSocketSilently()
    disarmHeartbeat()
    rejectInFlightCommands('the connection dropped while the command was in flight')
    requeueUnresolvedReads()
    connectionReady = false
    currentSocket = null
    beginConnect(true)
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
      beginConnect(enterConnectingState)
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
    if (restartReason === 'shutdown') {
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
    restartReason = data.reason
    clearConnectingBoundTimer()
    setState('restarting')
    clearPatienceTimer()
    if (data.reason !== 'shutdown') {
      const patienceMs = data.reason === 'reboot' ? PATIENCE_REBOOT_MS : PATIENCE_MODE_CHANGE_MS
      patienceTimer = setTimeout(onPatienceExpired, patienceMs)
    }
  }

  function handleStats(stats: Stats): void {
    lastStatsValue = stats
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
      const err = new Error(message.data.message)
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
        handleStats(message.data)
        break
      case 'restarting':
        handleRestarting(message.data)
        break
      case 'error':
        if (message.data.code === 'unauthorized') lastErrorWasUnauthorized = true
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
        discardSocketSilently()
        disarmHeartbeat()
        rejectInFlightCommands('the connection dropped while the command was in flight')
        requeueUnresolvedReads()
        connectionReady = false
        currentSocket = null
        enterOffline()
      }, CONNECTING_BOUND_MS)
    }

    const socket = deps.createSocket(options.url)
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

  function lastStats(): Stats | null {
    return lastStatsValue
  }

  function onState(handler: (state: ConnectionState) => void): () => void {
    stateHandlers.add(handler)
    return () => stateHandlers.delete(handler)
  }

  function onMessage(handler: (message: OutgoingMessage) => void): () => void {
    messageHandlers.add(handler)
    return () => messageHandlers.delete(handler)
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
    if ((current !== 'connected' && current !== 'degraded') || !currentSocket) {
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
    lastStats,
    onState,
    onMessage,
    request,
    command,
    sendGps,
  }
}
