// Svelte store layer of SPEC 4.4. `lib/ws.ts` knows nothing about Svelte and
// nothing about this file: `connectStores` is the adapter that subscribes to
// a `WsClient` and writes these stores, which is why 4.3 could be built and
// reviewed before this section existed.

import { derived, type Readable, writable } from 'svelte/store'

import type { ConnectionState, UnauthorizedReason, WsClient } from './ws'
import type {
  AccessPoint,
  Capabilities,
  FaceStatus,
  Gps,
  OutgoingHandshakesList,
  OutgoingMessage,
  Peer,
  Stats,
} from './protocol'

/** SPEC 4.3.1 owns the state machine; this is a mirror of it, not a second opinion. */
export interface ConnectionView {
  state: ConnectionState
  unauthorizedReason: UnauthorizedReason | null
}

/**
 * SPEC 4.4.1: the whole `handshakes_list` payload, not `entries` alone.
 * `truncated` and `total` are what SPEC 4.5.2's truncation notice and
 * `500+` badge read, and a view subscribes to stores, never to the client,
 * so a field dropped here is a field no view can ever reach.
 */
export type HandshakesList = OutgoingHandshakesList['data']

const EMPTY_HANDSHAKES: HandshakesList = { entries: [], truncated: false, total: 0 }

const connectionWritable = writable<ConnectionView>({ state: 'offline', unauthorizedReason: null })
const statsWritable = writable<Stats | null>(null)
const accessPointsWritable = writable<AccessPoint[]>([])
const handshakesWritable = writable<HandshakesList>(EMPTY_HANDSHAKES)
const peersWritable = writable<Peer[]>([])
const logWritable = writable<string[]>([])
const gpsWritable = writable<Gps | null>(null)
const faceWritable = writable<FaceStatus | null>(null)
// SPEC 4.4.1: `channel` is the later of its two sources, `stats` and
// `channel_hop`; a plain writable set from both handlers below already
// gives that for free.
const channelWritable = writable<number | null>(null)

export const connection: Readable<ConnectionView> = { subscribe: connectionWritable.subscribe }
export const stats: Readable<Stats | null> = { subscribe: statsWritable.subscribe }
export const accessPoints: Readable<AccessPoint[]> = { subscribe: accessPointsWritable.subscribe }
export const handshakes: Readable<HandshakesList> = { subscribe: handshakesWritable.subscribe }
export const peers: Readable<Peer[]> = { subscribe: peersWritable.subscribe }
export const log: Readable<string[]> = { subscribe: logWritable.subscribe }
export const gps: Readable<Gps | null> = { subscribe: gpsWritable.subscribe }
export const face: Readable<FaceStatus | null> = { subscribe: faceWritable.subscribe }
export const channel: Readable<number | null> = { subscribe: channelWritable.subscribe }

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
    const index = current.findIndex((existing) => existing.fingerprint === peer.fingerprint)
    if (index === -1) return [...current, peer]
    const next = [...current]
    next[index] = peer
    return next
  })
}

// SPEC 4.4.1: at most one refresh in flight per client, so a burst of
// captures costs one round trip and not one per capture. Owner, not a bare
// flag, because a host switch (SPEC 4.4.2) can leave A's refresh pending
// after B attaches; comparing the owner to the requesting client, rather
// than reusing attachedClient here, is what tells B's push apart from A's
// stale one and lets B send its own request instead of coalescing into a
// reply that will never reach a store B did not write from. Cleared from
// the settle handlers below only, and only when the owner is still the
// settling client: every request() eventually settles, either on its
// reply, on its own 15 s timeout, or via close() rejecting pending reads,
// so the owner cannot wedge and a reset would only break coalescing across
// a remount on the same client.
let handshakesRefreshOwner: WsClient | null = null

function refreshHandshakes(client: WsClient): void {
  if (handshakesRefreshOwner === client) return
  handshakesRefreshOwner = client
  client
    .request('get_handshakes')
    .catch(() => {
      // Not fatal here: the reply, when it does arrive, still writes the
      // list through the handshakes_list case below like any other. A
      // failed or timed-out attempt just means this particular capture
      // is not reflected until the next push or a manual reload of the
      // view asks again.
    })
    .finally(() => {
      if (handshakesRefreshOwner === client) {
        handshakesRefreshOwner = null
      }
    })
}

function handleMessage(message: OutgoingMessage, client: WsClient): void {
  switch (message.type) {
    case 'stats':
      // SPEC 4.4.1: the payload as it arrived, never patched with a later
      // channel_hop or gps_update.
      statsWritable.set(message.data)
      channelWritable.set(message.data.channel)
      gpsWritable.set(message.data.gps)
      break
    case 'channel_hop':
      channelWritable.set(message.data.channel)
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
    case 'face_status':
      faceWritable.set(message.data)
      break
    case 'status_change':
      // SPEC 2.13/4.4.1: only the status half is this hook's own; face and
      // mode come from face_status only, and the mood carried alongside is
      // deliberately dropped, FaceStatus has no field for it. Nothing to
      // merge into before the first face_status has arrived.
      faceWritable.update((current) =>
        current === null ? current : { ...current, status: message.data.status },
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
  gpsWritable.set(null)
  faceWritable.set(null)
  channelWritable.set(null)
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
 * returned teardown unsubscribes both listeners it registered, and nothing
 * else: a component that mounts and unmounts this more than once (as a
 * view swap does) must not accumulate a duplicate handler on the
 * underlying client, which lives for the whole session and outlives any
 * single subscriber.
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
  // startup already established a connection).
  connectionWritable.set({ state: client.state(), unauthorizedReason: client.unauthorizedReason() })
  const seededStats = client.lastStats()
  if (seededStats !== null) {
    statsWritable.set(seededStats)
    channelWritable.set(seededStats.channel)
    gpsWritable.set(seededStats.gps)
  }

  const unsubscribeState = client.onState((state) => {
    connectionWritable.set({ state, unauthorizedReason: client.unauthorizedReason() })
    if (state === 'unauthorized') {
      // SPEC 4.4.2: the data belongs to a session the unit refused; leaving
      // it on screen behind a token prompt would show it to whoever is
      // looking at that prompt.
      resetStores()
    }
  })
  const unsubscribeMessage = client.onMessage((message) => handleMessage(message, client))

  let torndown = false
  return () => {
    if (torndown) return
    torndown = true
    unsubscribeState()
    unsubscribeMessage()
    if (attachedClient === client) {
      attachedClient = null
    }
  }
}
