// SPEC 4.5.2.3/4.5.2.4: the view-side refresh trigger, shared because it is
// a rule about routes and client identity, not markup (SPEC 4.5.1.1) --
// WiFi.svelte and Peers.svelte both call watchViewRefresh below rather than
// each deriving the rule again. §4.5.2.3 keeps the reasoning for every
// decision here; this file only carries the mechanism.
//
// The two subscriptions watchViewRefresh opens are torn down through
// Svelte's own `onDestroy`, registered internally rather than handed back
// as a `destroy()` the caller must remember to call -- see that function's
// own docstring for why.

import { onDestroy } from 'svelte'
import { get } from 'svelte/store'

import { currentRoute, type ViewId } from './router'
import { currentClient } from './session'
import { connection } from './stores'
import type { WsClient } from './ws'

export interface ViewRefreshHandle {
  /**
   * SPEC 4.5.2.3's second occasion: the refresh control on the view. Not
   * gated on the route being current -- a control on a mounted, current
   * view is only ever tapped while it is current -- and coalesced the same
   * way the becoming-current trigger below is, through the same `refresh`
   * function's own owner in lib/stores.ts.
   */
  refreshNow(): void
}

/**
 * SPEC 4.5.2.3: three occasions ask for a refresh, and no timer does --
 * the route becoming this view's own, a control on the view (`refreshNow`
 * above), and the active host changing under a view that is already
 * current. This function is the first and the third; a caller wires the
 * second to its own refresh control by calling `refreshNow`.
 *
 * `refresh` is asked whenever a client exists, connected or not: a queued
 * read (`WsClient.request`) flushes on reconnect rather than being refused
 * outright the way a state command is (SPEC 4.3.3), so a gate on the
 * connection state here would be a second, wrong copy of a rule lib/ws.ts
 * already owns. Reading the `connection` store is what lets the wait for a
 * client to exist happen without an extra hook: this is called from a
 * view's own module scope, which can run before App.svelte's onMount has
 * called startSession() -- including on a cold start straight into this
 * view's own route, where `currentClient()` is still `null` on the first
 * check. lib/stores.ts's connectStores seeds `connection` the moment a
 * client attaches, which re-notifies this function's subscription with the
 * same route still current and a client that now exists.
 *
 * `askedForClient` holds the client the ask was made for, not a bare flag,
 * for the reason lib/stores.ts's own `createCoalescedRefresh` comment gives
 * about the same shape one layer down: a host switch (SPEC 4.4.2) can leave
 * the ask made for unit A still recorded while unit B is now attached.
 * lib/session.ts's `rebuild()` runs teardown, `resetStores()`, then
 * `connectStores(B)` -- all while the route can still be this view's own
 * the whole time, since a host switch is not a navigation -- and a bare
 * boolean would read that as "already asked" and never ask B. Comparing
 * identity is what tells "the same client asking again" apart from "a
 * different client now attached": `askedForClient` clears to `null` the
 * moment the route stops being this view's own, so the next time it
 * becomes current is a fresh ask regardless of which client is attached by
 * then.
 *
 * Must be called synchronously from a component's own initialisation, the
 * same requirement Svelte's own `onDestroy` carries, because this function
 * calls it internally to release the two subscriptions below. That is
 * deliberate rather than a caller's obligation: a first version returned a
 * `destroy()` for the caller to invoke, and both call sites forgot it, which
 * left every mount's subscriptions live for the rest of the process --
 * invisible in the app, where SPEC 4.5 keeps every view mounted for its
 * whole life, and compounding in a test suite that mounts the same view
 * repeatedly, where each leftover watcher goes on reacting to route and
 * connection changes and calling `refresh` against whatever client
 * `currentClient()` returns by then, which by that point belongs to a later
 * session than the one that created it. Tying the teardown to the
 * component's own destruction through `onDestroy` removes the chance to
 * forget it rather than documenting the risk.
 */
export function watchViewRefresh(
  routeId: ViewId,
  refresh: (client: WsClient) => void,
): ViewRefreshHandle {
  let askedForClient: WsClient | null = null

  function check(): void {
    const isCurrent = get(currentRoute).id === routeId
    if (!isCurrent) {
      askedForClient = null
      return
    }
    const client = currentClient()
    if (client === null) return
    if (askedForClient === client) return
    askedForClient = client
    refresh(client)
  }

  const unsubscribeRoute = currentRoute.subscribe(check)
  const unsubscribeConnection = connection.subscribe(check)

  onDestroy(() => {
    unsubscribeRoute()
    unsubscribeConnection()
  })

  return {
    refreshNow(): void {
      const client = currentClient()
      if (client === null) return
      refresh(client)
    },
  }
}
