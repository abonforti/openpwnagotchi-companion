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

export interface ViewFollowHandle {
  /** Arms or disarms the timer. Called from the Log view's own follow control. */
  setFollowing(following: boolean): void
}

/**
 * SPEC 4.5.2.5: the one timer this client is allowed, and the mechanism for
 * it, not the interval or which view carries it -- both of those are the
 * Log's own and are passed in rather than hardcoded here, so this file goes
 * on knowing nothing about any one screen.
 *
 * The exception is argued in SPEC 4.5.2.5, not repeated here: `log_lines`
 * only ever answers `get_log` (SPEC 2.9), so nothing arrives on its own, and
 * a tail that follows means asking again on a schedule -- the one case
 * where §4.5.2.3's "and no timer" does not hold.
 *
 * `following` is opt-in and starts disarmed; only a call to `setFollowing`
 * arms it, and it is not reset by navigation -- SPEC 4.5 keeps the view
 * mounted, so the toggle still reads "on" on return and the timer resumes
 * with it, rather than the app quietly turning a control back off that the
 * owner left on. Armed or not, the timer runs **only** while `routeId` is
 * the current route, checked the same way `watchViewRefresh` checks it
 * above: navigating away stops the asking, and only the asking -- the
 * toggle itself is untouched, so returning is what starts it again, not a
 * second tap. Torn down through this file's own `onDestroy`, not a
 * `destroy()` the caller has to remember: that function's docstring already
 * explains why a caller-owned teardown is the leak this file exists to
 * prevent, and a leaked interval is the same mistake in a worse shape,
 * because it goes on firing `tick` against whatever `currentClient()`
 * returns long after the view that armed it stopped being on screen.
 *
 * **Starting asks immediately, then every `intervalMs`.** SPEC 4.5.2.5:
 * waiting out the first interval before anything happens is a control that
 * appears not to work, and somebody arming this -- by the toggle, or by
 * navigating back to a view where it was already on -- wants to see what is
 * happening now, not in ten seconds.
 *
 * Unlike `watchViewRefresh`, this does not also fire on the route becoming
 * current or on a host switch on its own: SPEC 4.5.2.5 adopts §4.5.2.3's
 * three occasions for the Log through `watchViewRefresh`, called alongside
 * this with the same `tick`/`refresh` function, and follow is an addition
 * on top of that -- a fourth occasion, on a clock, while the toggle is on --
 * not a replacement for the other three. This is also the whole of SPEC
 * 4.5.2.5's exception to "no view holds a timer": nothing else in this
 * client may reach for a second one, which is why this function takes a
 * `routeId` rather than being armed from any view that imports it, and
 * `intervalMs` rather than a constant baked in here, so the one call site
 * that exists today is also the only one a reviewer needs to check.
 */
export function watchViewFollow(
  routeId: ViewId,
  intervalMs: number,
  tick: (client: WsClient) => void,
): ViewFollowHandle {
  let following = false
  let timer: ReturnType<typeof setInterval> | null = null

  function ask(): void {
    const client = currentClient()
    if (client === null) return
    tick(client)
  }

  function stop(): void {
    if (timer === null) return
    clearInterval(timer)
    timer = null
  }

  function start(): void {
    if (timer !== null) return
    ask()
    timer = setInterval(ask, intervalMs)
  }

  function sync(): void {
    const isCurrent = get(currentRoute).id === routeId
    if (isCurrent && following) {
      start()
    } else {
      stop()
    }
  }

  const unsubscribeRoute = currentRoute.subscribe(sync)

  onDestroy(() => {
    unsubscribeRoute()
    stop()
  })

  return {
    setFollowing(next: boolean): void {
      following = next
      sync()
    },
  }
}
