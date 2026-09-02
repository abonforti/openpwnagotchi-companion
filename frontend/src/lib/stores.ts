// Svelte store layer of SPEC 4.4. `lib/ws.ts` knows nothing about Svelte and
// nothing about this file: `connectStores` is the adapter that subscribes to
// a `WsClient` and writes these stores, which is why 4.3 could be built and
// reviewed before this section existed.

import { derived, type Readable, writable } from 'svelte/store'

import {
  RemoteError,
  type ConnectionState,
  type LastError,
  type UnauthorizedReason,
  type WsClient,
} from './ws'
import type {
  AccessPoint,
  Capabilities,
  FaceStatus,
  Gps,
  OutgoingHandshakesList,
  OutgoingMessage,
  OutgoingScreenImage,
  Peer,
  RestartReason,
  Stats,
} from './protocol'

/**
 * SPEC 4.3.1 owns the state machine; this is a mirror of it, not a second
 * opinion. `lastError` and `latencyMs` are SPEC 4.3.10's diagnostics pair,
 * mirrored the same way: written from `client.diagnostics()`, never
 * computed here.
 */
/**
 * SPEC 4.4.1 (issue #131): `restartReason` is null in every state but
 * `restarting`, the same way `unauthorizedReason` is null in every state but
 * `unauthorized`. `restarting`'s `shutdown` reason is terminal (SPEC 4.3.1)
 * and the other two are not, so a view that cannot tell them apart tells
 * somebody who just tapped shut down that the unit is coming back.
 */
export interface ConnectionView {
  state: ConnectionState
  unauthorizedReason: UnauthorizedReason | null
  restartReason: RestartReason | null
  lastError: LastError | null
  latencyMs: number | null
}

/**
 * SPEC 4.4.1: the whole `handshakes_list` payload, not `entries` alone.
 * `truncated` and `total` are what SPEC 4.5.2's truncation notice and
 * `500+` badge read, and a view subscribes to stores, never to the client,
 * so a field dropped here is a field no view can ever reach.
 */
export type HandshakesList = OutgoingHandshakesList['data']

const EMPTY_HANDSHAKES: HandshakesList = {
  entries: [],
  truncated: false,
  total: 0,
}

/** SPEC 4.4.1's `screen` row: the `screen_image` reply's `data`, `png` and `mtime` both. */
export type ScreenFrame = OutgoingScreenImage['data']

const connectionWritable = writable<ConnectionView>({
  state: 'offline',
  unauthorizedReason: null,
  restartReason: null,
  lastError: null,
  latencyMs: null,
})
const statsWritable = writable<Stats | null>(null)
const accessPointsWritable = writable<AccessPoint[]>([])
const handshakesWritable = writable<HandshakesList>(EMPTY_HANDSHAKES)
const peersWritable = writable<Peer[]>([])
const logWritable = writable<string[]>([])
// SPEC 4.5.2.5: "an unreadable log is not an empty one" -- whether the last
// get_log answered log_unavailable, read by the Log view alongside the log
// store itself rather than folded into it, since the two questions ("what
// are the lines" and "could the unit read the file at all") are answered by
// two different frame types (log_lines, error) and collapsing them would
// lose which one last happened.
const logUnavailableWritable = writable<boolean>(false)
const gpsWritable = writable<Gps | null>(null)
const faceWritable = writable<FaceStatus | null>(null)
// SPEC 4.4.1's `screen` row: held like every other store rather than read off
// the request's promise, so the one message type heavy enough to tempt a
// shortcut gets none. Only ever written from the `screen_image` case below --
// a failed `get_screen`, including a `no_frame` answer, writes nothing, so a
// previously held frame is not discarded by a later ask that did not
// succeed. `no_frame` and a store that was already empty read the same way
// on screen regardless (SPEC 4.5.2.6's copy table treats them as one
// sentence), so there is nothing this store needs to record about the answer
// beyond what arrived.
const screenWritable = writable<ScreenFrame | null>(null)
// SPEC 4.4.1 (issue #162): `channel` is the later of its two sources, `stats`
// and `channel_hop`, compared by the unit's own timestamp rather than by
// arrival order -- see setChannel below. `lastChannelTimestamp` is this
// store's half of that comparison, reset alongside it in resetStores().
const channelWritable = writable<number | null>(null)
let lastChannelTimestamp: number | null = null

/**
 * SPEC 4.4.1 (issue #162): "whichever came later" is the unit's own
 * `timestamp`, not arrival order. `stats` is broadcast on a ticker and
 * `channel_hop` is pushed when the hop happens, so a `stats` produced before
 * a hop can be delivered after it and overwrite a newer channel with an
 * older one; a plain writable set from both handlers gives *last arrived*,
 * not *latest*. Both timestamps come from the same unit, which is the one
 * clock comparison SPEC 4.3.10 does not refuse -- that section only refuses
 * subtracting the unit's clock from the phone's. An equal timestamp is
 * accepted: two messages can carry the same second, and the one that
 * arrived second is the newer of them.
 */
function setChannel(value: number | null, timestamp: number): void {
  if (lastChannelTimestamp !== null && timestamp < lastChannelTimestamp) return
  lastChannelTimestamp = timestamp
  channelWritable.set(value)
}

export const connection: Readable<ConnectionView> = {
  subscribe: connectionWritable.subscribe,
}
export const stats: Readable<Stats | null> = {
  subscribe: statsWritable.subscribe,
}
export const accessPoints: Readable<AccessPoint[]> = {
  subscribe: accessPointsWritable.subscribe,
}
export const handshakes: Readable<HandshakesList> = {
  subscribe: handshakesWritable.subscribe,
}
export const peers: Readable<Peer[]> = { subscribe: peersWritable.subscribe }
export const log: Readable<string[]> = { subscribe: logWritable.subscribe }
export const logUnavailable: Readable<boolean> = {
  subscribe: logUnavailableWritable.subscribe,
}
export const gps: Readable<Gps | null> = { subscribe: gpsWritable.subscribe }
export const face: Readable<FaceStatus | null> = {
  subscribe: faceWritable.subscribe,
}
export const screen: Readable<ScreenFrame | null> = {
  subscribe: screenWritable.subscribe,
}
export const channel: Readable<number | null> = {
  subscribe: channelWritable.subscribe,
}

// SPEC 4.4.1: capabilities has no writer of its own, it arrives inside
// stats and having two sources for one fact is how they disagree, so it is
// read off the stats store rather than written from a message handler. This
// is the only svelte/store derived() in this file: channel looks similar
// from the outside and is a plain writable with two writers.
export const capabilities: Readable<Capabilities | null> = derived(
  stats,
  ($stats) => $stats?.capabilities ?? null,
)

function upsertPeer(peer: Peer): void {
  peersWritable.update((current) => {
    // SPEC 4.4.1: '???' is peer.identity()'s documented default for a unit
    // that has not advertised one, not an identity, so it never matches an
    // existing row: two unadvertised peers are two rows, not one row that
    // keeps getting overwritten. Order is the view's business (SPEC 4.5.2
    // sorts by signal and last seen); this file promises none, so both the
    // append below and the in-place update keep whatever order they find.
    if (peer.fingerprint === '???') return [...current, peer]
    const index = current.findIndex(
      (existing) => existing.fingerprint === peer.fingerprint,
    )
    if (index === -1) return [...current, peer]
    const next = [...current]
    next[index] = peer
    return next
  })
}

/**
 * SPEC 4.4.1/4.5.2.3/4.5.2.4/4.5.2.5: a per-client coalescing owner for a
 * read that must have at most one request in flight, shared by
 * refreshHandshakes, refreshAccessPoints, refreshPeers and refreshLog below
 * rather than pasted a fourth time -- the first correction to any one of
 * them is how it would fail to reach the others. `refreshLog` used to be a
 * hand-written copy of exactly this owner protocol, kept apart on the
 * reasoning that this helper swallows `send`'s rejection outright and that
 * one call site must not; `onSettled` below is what let the copy be
 * deleted instead of kept in step with this one by hand.
 *
 * Owner, not a bare flag, because a host switch (SPEC 4.4.2) can leave A's
 * refresh pending after B attaches; comparing the owner to the requesting
 * client, rather than reusing attachedClient here, is what tells B's push
 * apart from A's stale one and lets B send its own request instead of
 * coalescing into a reply that will never reach a store B did not write
 * from. Cleared from the settle handler below only, and only when the
 * owner is still the settling client: every request() eventually settles,
 * either on its reply, on its own 15 s timeout, or via close() rejecting
 * pending reads, so the owner cannot wedge and a reset would only break
 * coalescing across a remount on the same client.
 *
 * Comparing `client` identity, rather than a per-request token like
 * lib/controls.ts's nextConfirmToken (issue #186), is enough on its own for
 * every caller of this function, and that was checked rather than assumed
 * for `refreshLog`, the one caller a stale settle would have been visible
 * from (SPEC 4.5.2.5's `log_unavailable` flag): a single client can never
 * have two requests through this helper outstanding at once, because the
 * `if (owner === client) return` guard coalesces a second call from the
 * same client into the first before it is ever sent -- there is no second
 * request for a stale settle to collide with. And switching back to a
 * previously active unit does not hand back the same client to collide
 * with in the first place: lib/session.ts's rebuild() calls createClient()
 * unconditionally on every switch, so a unit reached again after a trip
 * through another one is a new object, and `owner === client` is false for
 * a stale settle from the earlier one in exactly the case that matters. An
 * earlier version of refreshLog carried a token guarding the sequence "A
 * asks, switch to B, switch back to A, A's first settles and releases what
 * A's second request holds" -- that sequence has no step where one client
 * object holds two requests through this helper, so it cannot happen, and
 * the token was a branch nothing could reach, which SPEC 4.5.2.1 already
 * argues is worse than absent: it reads as a case somebody handled and
 * invites the next reader to go looking for the sequence it is for.
 *
 * `send`'s rejection is always swallowed past `onSettled`: none of this
 * function's callers may see it escape past this helper, each for its own
 * reason given beside its own call below (or, for `refreshLog`, inside
 * `onSettled` itself), and every read settles regardless.
 */
function createCoalescedRefresh(
  send: (client: WsClient) => Promise<unknown>,
  // SPEC 4.5.2.5: the seam refreshLog needed and the other three callers
  // do not. Called only while `client` is still this call's owner -- the
  // same guard the release below reads -- so an outcome from a client this
  // helper has already stopped coalescing for (a stale settle) never
  // reaches it. None of the other three callers pass one: a dropped
  // outcome there costs only coalescing, not a store write, so they stay
  // on the plain swallow-and-release behaviour this parameter defaults to.
  onSettled?: (
    client: WsClient,
    outcome: { ok: true } | { ok: false; error: unknown },
  ) => void,
): (client: WsClient) => void {
  let owner: WsClient | null = null
  return (client: WsClient) => {
    if (owner === client) return
    owner = client
    send(client)
      .then(() => {
        if (owner === client) onSettled?.(client, { ok: true })
      })
      .catch((error: unknown) => {
        if (owner === client) onSettled?.(client, { ok: false, error })
        // See the comment beside each call site (or, for refreshLog,
        // inside its own onSettled) for why this is not fatal.
      })
      .finally(() => {
        if (owner === client) {
          owner = null
        }
      })
  }
}

// SPEC 4.4.1/4.5.2.7: not fatal here: the reply, when it does arrive, still
// writes the list through the handshakes_list case below like any other. A
// failed or timed-out attempt just means this particular capture is not
// reflected until the next push or a manual reload of the view asks again.
// Exported: the Map view's own refresh (SPEC 4.5.2.7) is this same function
// through lib/viewRefresh.ts's watchViewRefresh, its second caller alongside
// refreshWifiLists below, rather than a second coalesced read wrapping the
// same request.
export const refreshHandshakes = createCoalescedRefresh((client) =>
  client.request('get_handshakes'),
)

// SPEC 4.5.2.3: not fatal here either: access_points is also pushed on
// wifi_update and once at the start of every session (initial_burst), so a
// failed or timed-out refresh only means this particular ask did not
// shorten the wait for one of those.
const refreshAccessPoints = createCoalescedRefresh((client) =>
  client.request('get_access_points'),
)

// SPEC 4.5.2.4: not fatal here either, and for the strongest version of the
// same reason -- peers_list has no push counterpart at all (unlike
// wifi_update/handshake), so a failed refresh here is not "the next push
// will carry it", it is "nothing will until this view asks again", which
// is exactly what its own refresh control and its own becoming-current
// trigger (lib/viewRefresh.ts) are for.
export const refreshPeers = createCoalescedRefresh((client) =>
  client.request('get_peers'),
)

/**
 * SPEC 4.5.2.3: the Wi-Fi view's own refresh, asked for on three occasions
 * -- the route becoming `/wifi`, its own refresh control, and the active
 * host changing under a view that is already current (lib/viewRefresh.ts)
 * -- and by no timer, the argument SPEC 4.3.7 already made against client
 * polling being stronger here: a list re-fetched on a schedule spends round
 * trips on a link whose round trips are the expensive part.
 *
 * Both lists are fetched together, not only the visible segment, so a
 * badge nobody is looking at still carries a current count. This lives
 * beside refreshHandshakes rather than in a module of its own: it is the
 * same per-client coalescing pattern applied to a second read, sharing this
 * file's writable stores and its WsClient import, and a wrapper module
 * would only exist to re-export what is already here.
 */
export function refreshWifiLists(client: WsClient): void {
  refreshAccessPoints(client)
  refreshHandshakes(client)
}

/**
 * SPEC 4.5.2.5: called from the Log view's three-occasion refresh
 * (lib/viewRefresh.ts's watchViewRefresh) and from its follow timer
 * (watchViewFollow) alike -- one function, so a correction to what "asking
 * for the log" means reaches both callers.
 *
 * Each ask carries no `lines`, so the plugin's own default and clamp decide
 * the window (SPEC 2.9): naming a number here would be a second opinion
 * about how much log is worth sending, kept in a second place from the one
 * the plugin already owns.
 *
 * Coalesced through createCoalescedRefresh above like the other three
 * reads, but with an `onSettled` the others do not pass: `log_unavailable`
 * is a state of the Log screen, not a discarded promise, so the outcome is
 * written to logUnavailableWritable for the view to read rather than being
 * swallowed. createCoalescedRefresh's own comment covers why comparing
 * client identity is enough to keep a stale settle from reaching this
 * callback at all, so there is nothing further to guard here.
 *
 * A successful reply needs no write of its own beyond clearing the flag:
 * the `log_lines` case in handleMessage below already wrote the buffer by
 * the time this settles, since router.handle's messageHandlers run before
 * correlate() resolves the request's own promise (lib/ws.ts), and that
 * write is safe on its own terms -- `client.onMessage` is unsubscribed the
 * moment a client is torn down (connectStores' own teardown), so a message
 * actually arriving late on a detached client's socket cannot reach
 * `logWritable` at all. The flag is set on a `log_unavailable` failure
 * specifically; any other failure -- a timeout, a different code -- is left
 * alone, the same as the three refreshes above: the screen keeps showing
 * what it already had, and the next occasion asks again. SPEC 4.5.2.5
 * gives its own sentence to `log_unavailable` alone, not to every possible
 * failure.
 */
export const refreshLog = createCoalescedRefresh(
  (client) => client.request('get_log'),
  (_client, outcome) => {
    if (outcome.ok) {
      logUnavailableWritable.set(false)
      return
    }
    if (
      outcome.error instanceof RemoteError &&
      outcome.error.code === 'log_unavailable'
    ) {
      logUnavailableWritable.set(true)
    }
  },
)

/**
 * SPEC 4.5.2.6: the Mirror view's own refresh, asked for on the same three
 * occasions as every other list view (lib/viewRefresh.ts's watchViewRefresh)
 * and, on the terms that section grants, by the second timer it allows
 * (watchViewFollow) -- both calling this one function, the same shape
 * refreshLog gives the Log's own two callers.
 *
 * Not fatal here, for the reason `refreshWifiLists`'s own comment gives for
 * the two reads it wraps: a failed or timed-out `get_screen` -- including a
 * `no_frame` answer -- just means the screen keeps whatever it already held,
 * and the next occasion asks again. There is no flag to set for a `no_frame`
 * answer the way `refreshLog` sets one for `log_unavailable`: SPEC 4.5.2.6's
 * copy table gives that answer the same sentence a store that was never
 * written gets, so nothing beyond the plain swallow-and-release this helper
 * already does is needed.
 */
export const refreshScreen = createCoalescedRefresh((client) =>
  client.request('get_screen'),
)

function handleMessage(message: OutgoingMessage, client: WsClient): void {
  switch (message.type) {
    case 'stats':
      // SPEC 4.4.1: the payload as it arrived, never patched with a later
      // channel_hop or gps_update.
      statsWritable.set(message.data)
      setChannel(message.data.channel, message.timestamp)
      gpsWritable.set(message.data.gps)
      break
    case 'channel_hop':
      setChannel(message.data.channel, message.timestamp)
      break
    case 'access_points':
    case 'wifi_update':
      // SPEC 4.4.1: replaced whole, never merged, for both the reply and the push.
      accessPointsWritable.set(message.data)
      break
    case 'handshakes_list':
      // The only writer of this store (SPEC 4.4.1), including for the
      // reply to the refresh issued below: that reply carries this same
      // type and this case does not itself call refreshHandshakes, so
      // there is no path back into the `'handshake'` case below.
      handshakesWritable.set(message.data)
      break
    case 'handshake':
      // SPEC 4.4.1: a signal, not a payload. The push carries {filename, ap,
      // station, gps}, not the {ssid, bssid, mtime, size} a list entry
      // needs, and those come from parsing the capture name and stat-ing
      // the file server-side (SPEC 2.7). Rather than fabricate them or
      // duplicate that parsing here, ask for the list again. The push is
      // also a notification that a capture just happened; nothing here
      // keeps that fact, on purpose, because a notification layer belongs
      // to a subscriber of the client directly (client.onMessage), same as
      // this adapter is, not to a store, and should not be added here by
      // reflex.
      refreshHandshakes(client)
      break
    case 'peers_list':
      peersWritable.set(message.data.entries)
      break
    case 'peer_detected':
      upsertPeer(message.data)
      break
    case 'log_lines':
      // SPEC 4.4.1: replaces, never appends. The plugin owns the tail and
      // the clamp (SPEC 2.9).
      logWritable.set(message.data.lines)
      break
    case 'gps_update':
      gpsWritable.set(message.data)
      break
    case 'screen_image':
      // SPEC 4.4.1's `screen` row: on demand only, never pushed (SPEC 2.10),
      // so this case only ever fires as the reply to refreshScreen's own
      // get_screen.
      screenWritable.set(message.data)
      break
    case 'face_status':
      faceWritable.set(message.data)
      break
    case 'status_change':
      // SPEC 2.13/4.4.1: only the status half is this hook's own; face and
      // mode come from face_status only, and the mood carried alongside is
      // deliberately dropped, FaceStatus has no field for it. Nothing to
      // merge into before the first face_status has arrived.
      faceWritable.update((current) =>
        current === null
          ? current
          : { ...current, status: message.data.status },
      )
      break
    default:
      break
  }
}

/**
 * Empties every data store. Exported so a caller pairing it with an
 * explicit client.close() (SPEC 4.4.2) can clear the screen at the same
 * point the app asked to disconnect; connectStores also calls it itself on
 * unauthorized, where the data belongs to a session the unit just refused.
 * Leaves `connection` alone: it is a mirror of the client, and reporting
 * offline while the client is still connected is the second opinion that
 * store forbids.
 */
export function resetStores(): void {
  statsWritable.set(null)
  accessPointsWritable.set([])
  handshakesWritable.set(EMPTY_HANDSHAKES)
  peersWritable.set([])
  logWritable.set([])
  logUnavailableWritable.set(false)
  gpsWritable.set(null)
  faceWritable.set(null)
  channelWritable.set(null)
  lastChannelTimestamp = null
  screenWritable.set(null)
}

// SPEC 4.4.2: one client at a time. The stores above are module singletons,
// so a second attach while one is live would have two adapters writing the
// same stores, and either one's unauthorized would blank the other's data.
let attachedClient: WsClient | null = null

/**
 * Wires a WsClient into the stores above. lib/ws.ts knows nothing about
 * Svelte, so this is the only place the two layers meet.
 *
 * Throws if a client is already attached: switching hosts, or anything
 * else that wants a second client, is teardown (call the returned
 * function), then resetStores(), then attach again (SPEC 4.4.2). The
 * returned teardown unsubscribes every listener it registered and sets
 * `connection` to `offline`, since a mirror with no client behind it shows
 * nothing connected (SPEC 4.4.1). It registers no duplicate handler on the
 * underlying client, which lives for the whole session and outlives any
 * single subscriber. Note that this makes a mount/unmount cycle visible in
 * `connection`, so the single caller SPEC 4.8 allows is also what keeps a
 * view swap from reporting `offline` under a live client.
 *
 * The teardown is idempotent, and the `attachedClient === client` check
 * inside it is unreachable defence in depth, not a path a test can reach:
 * `attachedClient` is written in exactly two places, connectStores throws
 * while it is still non-null, so a second closure can only exist once
 * `torndown` above has already intercepted the first one's re-entry, which
 * happens before this check ever sees a mismatched owner. It guards against
 * a future second writer of `attachedClient`, nothing more. Contrast the
 * same-looking ownership check inside refreshHandshakes's `finally` above:
 * that one is reachable, through a late settle from a detached client after
 * a new one has attached, and it is covered by a test.
 */
export function connectStores(client: WsClient): () => void {
  if (attachedClient !== null) {
    throw new Error(
      'connectStores: a client is already attached; call the previous teardown, ' +
        'then resetStores(), before attaching a new one',
    )
  }
  attachedClient = client

  // Seed from whatever the client already knows, in case connect() ran
  // before this adapter subscribed (a remount, or a view mounted after
  // startup already established a connection). SPEC 4.3.10: diagnostics()
  // seeded the same way, beside the state and unauthorized reason it
  // already was.
  const seededDiagnostics = client.diagnostics()
  connectionWritable.set({
    state: client.state(),
    unauthorizedReason: client.unauthorizedReason(),
    restartReason: client.restartReason(),
    lastError: seededDiagnostics.lastError,
    latencyMs: seededDiagnostics.latencyMs,
  })
  const seededStats = client.lastStats()
  if (seededStats !== null) {
    statsWritable.set(seededStats.stats)
    // SPEC 4.4.1/4.3.10 (issue #162): seeded through setChannel, with the
    // envelope stamp `client.lastStats()` now hands over beside the
    // payload, so a channel seeded at mount is gated the same as one set
    // from a live push -- the gate no longer has a hole for the first
    // message this store ever sees.
    setChannel(seededStats.stats.channel, seededStats.timestamp)
    gpsWritable.set(seededStats.stats.gps)
  }

  const unsubscribeState = client.onState((state) => {
    // SPEC 4.3.10: read through client.diagnostics() rather than carried
    // from the previous store value, the same reason unauthorizedReason is
    // read through the client here rather than assumed unchanged --
    // lib/ws.ts may have cleared lastError as part of this very state
    // change (entering 'connected'/'degraded'), and by the time this
    // handler runs that clearing has already happened.
    const { lastError, latencyMs } = client.diagnostics()
    connectionWritable.set({
      state,
      unauthorizedReason: client.unauthorizedReason(),
      restartReason: client.restartReason(),
      lastError,
      latencyMs,
    })
    if (state === 'unauthorized') {
      // SPEC 4.4.2: the data belongs to a session the unit refused; leaving
      // it on screen behind a token prompt would show it to whoever is
      // looking at that prompt.
      resetStores()
    }
  })
  const unsubscribeMessage = client.onMessage((message) =>
    handleMessage(message, client),
  )
  // SPEC 4.3.10: a second callback, not folded into onState -- latency
  // changes without the state changing, once per ping interval.
  const unsubscribeDiagnostics = client.onDiagnostics(
    ({ lastError, latencyMs }) => {
      connectionWritable.update((current) => ({
        ...current,
        lastError,
        latencyMs,
      }))
    },
  )

  let torndown = false
  return () => {
    if (torndown) return
    torndown = true
    unsubscribeState()
    unsubscribeMessage()
    unsubscribeDiagnostics()
    if (attachedClient === client) {
      attachedClient = null
    }
    // SPEC 4.4.1/4.4.2: with no client attached, `connection` reads
    // `offline` -- the same mirror rule as ever, not an exception to it,
    // because there is nothing left to mirror. This is what makes a build
    // failure in lib/session.ts visible: `createClient` can throw before a
    // client ever exists to emit a state of its own, and without this the
    // store would keep echoing whatever the previous client last reported,
    // describing a connection that no longer exists. Holds for every
    // detach, not only that failure path -- an explicit stop (SPEC 4.4.2)
    // leaves `connection` honest the same way it leaves the data stores
    // empty.
    connectionWritable.set({
      state: 'offline',
      unauthorizedReason: null,
      restartReason: null,
      lastError: null,
      latencyMs: null,
    })
  }
}
