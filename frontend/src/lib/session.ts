// Session layer of SPEC 4.8. The join between three layers that otherwise
// know nothing about each other: `lib/ws.ts` (4.3) holds a connection and
// asks no questions about which unit; `lib/stores.ts` (4.4) writes stores
// and never touches a client; `lib/settings.ts` (4.7) decides which unit
// and cannot attach one. This module owns the switch between them, which
// is why it is the only thing besides `lib/stores.ts` itself allowed to
// call `connectStores`.

import { activeHost, loadSettings, wsUrlFor, type Host } from './settings'
import { connectStores, resetStores } from './stores'
import { createWsClient, type WsClient, type WsClientOptions } from './ws'

export interface SessionDeps {
  createClient(options: WsClientOptions): WsClient
}

/**
 * SPEC 4.8: "the trigger is the identity of what the connection is made
 * of, the address, the ws port and the token, and not the fact that the
 * record was written." `httpPort` is deliberately absent: it is where the
 * app itself was served from, not part of the socket, so editing it
 * changes nothing about the connection and must not rebuild one. A label
 * edit, or any other field on `Host`, is not part of it either: rebuilding
 * on those would drop a working socket to produce an identical one, which
 * is the regression this module exists to avoid.
 *
 * This comparison has to be this module's own job rather than something
 * `activeHost`'s `derived` store already does. `svelte/store`'s
 * `safe_not_equal` treats any two non-null objects as unequal regardless
 * of reference or content, so `activeHost` re-notifies its subscribers on
 * every settings mutation -- including ones that never touch the active
 * host -- and even hands back the very same host object twice. Without the
 * tuple below, every keystroke in an unrelated label field would tear the
 * socket down and rebuild it.
 */
interface ConnectionIdentity {
  address: string
  wsPort: number
  token: string | null
}

function identityOf(host: Host): ConnectionIdentity {
  return { address: host.address, wsPort: host.wsPort, token: host.token }
}

function sameIdentity(a: ConnectionIdentity | null, b: ConnectionIdentity | null): boolean {
  if (a === null || b === null) return a === b
  return a.address === b.address && a.wsPort === b.wsPort && a.token === b.token
}

// SPEC 4.8: "nothing else creates a client." A single module-level client
// is what lets a view ask for the one that exists instead of building its
// own; `currentClient()` below is the only way in.
let client: WsClient | null = null

// SPEC 4.8: starting twice is refused here, in the same place the mistake
// is made, rather than left to surface downstream as connectStores'
// already-attached throw (SPEC 4.4.2) once a second client has already
// been built and a second socket is already opening. Cleared by the first
// call to the stop a session was started with (see the local `stopped`
// flag below), never latched: once stop() has run, a fresh startSession()
// is a supported sequence and must succeed.
let sessionActive = false

/** The client the active host is connected with, or `null` while none is active. */
export function currentClient(): WsClient | null {
  return client
}

/**
 * Subscribes to `activeHost` (SPEC 4.7) and performs the switch SPEC 4.4.2
 * requires whenever the connection-shaped identity of the active host
 * changes: teardown, then `resetStores()`, then attach, in that order.
 *
 * `loadSettings()` is called from here because nothing else in the app
 * calls it, and `lib/settings.ts` says as much: "the stores hold the
 * defaults until it runs." It runs before the subscription below is set
 * up, not after, so the very first evaluation of `activeHost` already
 * reflects whatever was persisted rather than the module's defaults.
 * That ordering is load-bearing, not a tidiness choice (SPEC 4.8, issue
 * #134): `defaultSettings()` activates `bluetooth`, so the module's
 * initial value already names a host, and subscribing before the load
 * would build a client and open a socket to `172.20.10.2` on every start
 * -- including one whose stored settings name a different unit -- before
 * storage had even been read.
 *
 * Returns its own stop, to be called once, when the shell unmounts. That is
 * the only moment this layer can observe: backgrounding the app on a phone
 * unmounts nothing, and releasing the socket then needs a lifecycle
 * listener nobody has written yet (issue #120).
 *
 * Throws if a session is already active. Calling this twice is a bug in
 * the caller -- the app shell, which starts exactly one of these -- and
 * SPEC 4.8 wants that mistake caught here rather than downstream: a second
 * call would otherwise build a second client and open a second socket
 * before `connectStores`' own already-attached guard (SPEC 4.4.2) ever
 * gets a chance to refuse it, which names the store layer for a mistake
 * made in this one. The message matches that guard's shape: what went
 * wrong, and what to call instead of it.
 */
export function startSession(deps?: Partial<SessionDeps>): () => void {
  if (sessionActive) {
    throw new Error(
      'startSession: a session is already active; call the previous stop before starting a new one',
    )
  }
  sessionActive = true

  // `setupDone` guards against a throw escaping the setup below --
  // `loadSettings()` and the first, synchronous firing of the `activeHost`
  // subscription -- before the `return`, which would otherwise latch
  // `sessionActive` for a start that never finished and never handed back
  // a stop to clear it. Contrast connectStores' analogous ownership check
  // in lib/stores.ts: this one is unreachable defence in depth, not a path
  // a test can reach. `loadSettings()` swallows its own storage failures
  // (SPEC 4.7), and `rebuild()` -- the only other fallible-looking call in
  // this span, run synchronously by the subscription above -- catches
  // everything it does. Kept for the same reason as that one: a future
  // change to either could make this span throw, and the flag must not be
  // wrong when it does.
  let setupDone = false
  try {
    const createClient = deps?.createClient ?? createWsClient

    let teardownStores: (() => void) | null = null
    let identity: ConnectionIdentity | null = null

    // SPEC 4.4.2/4.8: teardown happens before anything else, every time, so
    // that a rebuild cannot race and connectStores' single-attach guard
    // (SPEC 4.4.2) never has a reason to fire.
    function teardown(): void {
      if (teardownStores !== null) {
        teardownStores()
        teardownStores = null
      }
      if (client !== null) {
        client.close()
        client = null
      }
      identity = null
    }

    function rebuild(host: Host | null): void {
      teardown()
      // SPEC 4.4.2: the same reset applies whether the switch lands on a
      // new host or on none at all -- the previous unit's access points,
      // peers and handshakes are still that unit's data once it is no
      // longer the active one.
      resetStores()
      if (host === null) return

      // SPEC 4.8: nothing here may throw out of the `activeHost`
      // subscriber below. `createClient` reaches `new WebSocket(url)`
      // sooner or later -- through connect() if not immediately -- and
      // that constructor throws for a port the browser refuses to open
      // (SecurityError) or a URL its own parser rejects (SyntaxError);
      // SPEC 4.7 validates the address, not the browser's port policy.
      // Escaping this subscriber would abort the settingsWritable.update
      // that produced the notification half done, and Svelte's `set`
      // only clears its subscriber queue once the flush loop completes,
      // so a subscriber that throws leaves every later store notification
      // in the app silently dropped. So the build, the attach and the
      // connect are wrapped as one, and a failure becomes connection
      // state -- the client's own close() sets it `offline` and notifies
      // whoever is still listening -- rather than an exception.
      let built: WsClient | null = null
      try {
        // SPEC 4.8: the token is not passed. `options.token` would win for
        // the client's whole life, beside `companion.token`, the one
        // source of truth SPEC 4.3.9/4.7 already re-read on every attempt.
        built = createClient({ url: wsUrlFor(host) })
        // Stores attach before connect() so nothing the client emits
        // during the handshake is missed between the two calls.
        teardownStores = connectStores(built)
        client = built
        identity = identityOf(host)
        built.connect()
      } catch {
        // Same order as teardown() above: detach the stores, then close
        // the client. Kept in that order for consistency with teardown()
        // rather than for any difference it makes to what `connection`
        // ends up seeing -- reversing it here still leaves every test
        // passing, so no claim is made about the order mattering.
        if (teardownStores !== null) {
          teardownStores()
          teardownStores = null
        }
        if (built !== null) {
          built.close()
        }
        client = null
        identity = null
      }
    }

    loadSettings()

    const unsubscribe = activeHost.subscribe((host) => {
      const nextIdentity = host === null ? null : identityOf(host)
      if (sameIdentity(identity, nextIdentity)) return
      rebuild(host)
    })

    // Local to this call, not `sessionActive` itself: a second startSession()
    // is only reachable once this stop has already cleared the module flag,
    // and by then this closure's own `stopped` is what has to keep a stray
    // repeat call from tearing down whatever session (if any) is active by
    // that point instead of doing nothing, as a second call is supposed to.
    let stopped = false
    setupDone = true
    return () => {
      if (stopped) return
      stopped = true
      // SPEC 4.8: cleared before the teardown below, not after. `stopped`
      // above already keeps a repeat call a no-op; latching `sessionActive`
      // until unsubscribe() and teardown() both return would instead mean
      // a throw from either of them leaves no way to start a new session at
      // all, since the stop that could have retried is the very one that
      // just threw.
      sessionActive = false
      unsubscribe()
      teardown()
      // SPEC 4.4.2: the caller resets the stores after an explicit close,
      // and this module is that caller -- `rebuild()` already does the
      // same for a host switch; a stop without it would leave the previous
      // unit's stats, access points, peers and log on screen after the
      // session that owned them is gone.
      resetStores()
    }
  } finally {
    if (!setupDone) sessionActive = false
  }
}
