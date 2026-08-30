// Geolocation layer of SPEC 4.6/4.6.1/4.6.2. The only module in this app
// that calls into `navigator.geolocation` (SPEC 4.6.2): a permission prompt
// is not something a view should be able to raise as a side effect of being
// navigated to, so the acquisition, the tap that starts it and the push
// cadence all live here, and `views/Settings.svelte` only calls the entry
// points below and renders the `geoState` store.
//
// This module does not build or hold a `WsClient` (SPEC 4.6.2): it asks
// `lib/session.ts`'s `currentClient()` for the current one at every push, the
// same way `lib/stores.ts`'s coalesced refreshes never hold a client of
// their own either. `lib/ws.ts`'s `sendGps` already discards a frame it
// cannot send (SPEC 4.3.3), which is the whole of this module's offline
// rule: a position is only true at the instant it was taken, so there is
// nothing here to queue and nothing to replay on reconnect.
//
// No injected seam here, unlike `lib/ws.ts`'s `WsDeps` or `lib/session.ts`'s
// `SessionDeps`: `navigator.geolocation`, `setInterval`/`clearInterval` and
// `document`'s visibility event are read directly at the point of use, and
// a test drives them by replacing the globals themselves. A seam earns its
// place by being exercised; one that only ever wraps the real
// implementation is machinery nothing reaches, the same objection SPEC
// 4.6.2 already makes against calling `navigator.permissions` for an
// answer no sentence uses.

import { get, writable, type Readable } from 'svelte/store'

import type { Gps } from './protocol'
import { currentClient } from './session'
import { gps } from './stores'

/**
 * SPEC 4.6.2's state table. Client-local, and deliberately not §4.6.1's
 * four rows: this module only knows what this browser is doing, and the
 * one point where the two tables meet -- row 3, permission denied -- is
 * this module's own `denied` state, read by whatever composes §4.6.1's
 * table (issue #34, out of this module's scope -- SPEC 4.6.2 is explicit
 * that this module supplies the fact and does not compose the four rows).
 *
 * There is no state for "the browser accepted the watch and said
 * nothing": G1 (SPEC 4.6.2) makes that indistinguishable from `waiting`,
 * and inventing a distinction the platform does not offer is the same
 * defect as inventing a symbol.
 */
export type GeoState = 'off' | 'waiting' | 'sharing' | 'denied' | 'unsupported'

// SPEC 4.6.2: "not persisted". `controlOn` starts false on every module
// load, the same as a fresh app launch, and nothing anywhere writes it to
// storage -- a stored `true` could not produce the gesture the next launch
// needs (G1), so the app would come up believing it is sharing and push
// nothing.
let controlOn = false
// SPEC 4.6.2: sticky for the life of the app once `PERMISSION_DENIED`
// (code 1, G4) is seen, cleared only by a fresh attempt through `turnOn`.
let deniedSticky = false
let lastPosition: { latitude: number; longitude: number; accuracy: number | null } | null = null
let watchId: number | null = null
let pushTimer: ReturnType<typeof setInterval> | null = null

// SPEC 4.6.2: "a callback from a watch this module no longer owns is
// ignored, whatever it says". `clearWatch` does not promise that a
// callback the platform has already queued is cancelled, so a
// `PERMISSION_DENIED` from a torn-down watch can still land afterwards --
// including after a fresh watch has already been started, where it would
// otherwise re-deny the retry `denied`'s own recovery depends on. Each
// watch is started with a token (`watchCounter`, bumped in `beginWatch`);
// `activeWatchToken` is the one watch this module currently owns, `null`
// when none is running, and `onPosition`/`onError` below do nothing when
// the token their closure captured does not match it. Tearing a watch down
// invalidates the token immediately, in `stopWatchAndTimer`, so a callback
// from it is inert even before a new watch exists to replace it.
let watchCounter = 0
let activeWatchToken: number | null = null

const PUSH_INTERVAL_MS = 5_000

function geolocationSupported(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.geolocation
}

function computeState(): GeoState {
  if (!geolocationSupported()) return 'unsupported'
  if (deniedSticky) return 'denied'
  if (!controlOn) return 'off'
  return lastPosition !== null ? 'sharing' : 'waiting'
}

const stateWritable = writable<GeoState>(computeState())

/** The control's own state (SPEC 4.6.2's table), for `views/Settings.svelte` to render. */
export const geoState: Readable<GeoState> = { subscribe: stateWritable.subscribe }

function publish(): void {
  stateWritable.set(computeState())
}

/**
 * SPEC 4.6.2: pushes through `lib/ws.ts`'s `sendGps`, via the client
 * `lib/session.ts` holds, and never while `stats.gps.piFix` is true --
 * read from the `gps` store at push time, not latched when the control was
 * turned on, so a Pi fix that appears or disappears while this is running
 * is honoured without anyone touching the control. With nothing acquired
 * yet, or with no client to send through, there is nothing to do: `sendGps`
 * itself already discards a frame it cannot send (SPEC 4.3.3).
 */
function pushPosition(): void {
  if (lastPosition === null) return
  const currentGps: Gps | null = get(gps)
  if (currentGps?.piFix) return
  const client = currentClient()
  if (client === null) return
  client.sendGps({
    latitude: lastPosition.latitude,
    longitude: lastPosition.longitude,
    accuracy: lastPosition.accuracy,
  })
}

// No `pushTimer !== null` guard here: this function's only caller,
// `beginWatch`, always runs it immediately after `stopWatchAndTimer()` has
// already nulled `pushTimer`, so the guard would be a branch nothing can
// ever take.
function startTimer(): void {
  pushTimer = setInterval(pushPosition, PUSH_INTERVAL_MS)
}

function stopTimer(): void {
  if (pushTimer !== null) {
    clearInterval(pushTimer)
    pushTimer = null
  }
}

function stopWatchAndTimer(): void {
  // SPEC 4.6.2: invalidated before anything else, so a callback already
  // queued by the platform for the watch being torn down is inert from this
  // point on, whether or not a replacement watch ever starts.
  activeWatchToken = null
  if (watchId !== null) {
    navigator.geolocation?.clearWatch(watchId)
    watchId = null
  }
  stopTimer()
}

// No presence guard here: this function's only caller, `turnOn`, runs only
// while `controlOn` is false, and every path that sets `controlOn` false
// (`turnOff`, `onError`'s denial) also detaches in the same call, so
// `controlOn === false` already implies detached and the guard would be a
// branch nothing can ever take.
function attachVisibility(): void {
  document.addEventListener('visibilitychange', onVisibilityChange)
}

// No presence guard here either. `document.removeEventListener` on a
// listener that is not registered is defined to do nothing and cannot
// throw, so a guard around it does not change what either branch does --
// it only looks like it is protecting something. An earlier version of
// this function kept one on the theory that a late `PERMISSION_DENIED`
// (the same platform race `activeWatchToken` above now handles) could call
// this twice; the token is what actually closes that race, by making the
// stale callback a no-op before it ever reaches here, and a guard on top of
// that was solving a problem `removeEventListener`'s own contract already
// solves for free.
function detachVisibility(): void {
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

/**
 * SPEC 4.6.2: the watch and the timer, both together, and never started
 * without first tearing down whatever is already running -- the invariant
 * every call site that can start a watch (`turnOn`, and the visible branch
 * of `onVisibilityChange` below) now holds by construction rather than by
 * agreement among them. `stopWatchAndTimer()` is idempotent when there is
 * nothing to clear, so calling it unconditionally costs nothing on the
 * ordinary path and is what stops a second, unreachable watcher from ever
 * being created: assigning a new `watchId` over one that might still be
 * live would leak the earlier one, since only the newest id can ever be
 * passed to `clearWatch`.
 *
 * **`enableHighAccuracy: true`.** SPEC 4.6.2: the default is false, and
 * this app exists to say where a capture was taken -- a position good to
 * the nearest cell tower locates the neighbourhood, not the capture. The
 * battery cost is why the whole feature sits behind a tap rather than
 * something the app does on its own.
 *
 * **The guard on `geolocationSupported()` here is not the same permanently
 * uncovered branch as the ones deleted from `turnOn`, `startTimer` and
 * `attachVisibility`.** Those were provable dead from this module's own
 * call graph alone -- every caller had already checked, synchronously, with
 * nothing between the check and the call that could change the answer. This
 * one is reached again later, from `onVisibilityChange`, arbitrarily long
 * after `turnOn` made the same check, and what stands between them is not
 * this module's control flow but SPEC 4.6.2's own stated platform fact that
 * a browser does not remove `navigator.geolocation` from a document already
 * running. That is an assumption about the world this code does not
 * control, not a fact this code can derive from itself, and it is the kind
 * of guard this codebase keeps rather than deletes on the strength of an
 * argument holding today (see `lib/ws.ts`'s own "unreachable defence in
 * depth" comments for the same distinction drawn there).
 */
function beginWatch(): void {
  stopWatchAndTimer()
  if (!geolocationSupported()) return
  watchCounter += 1
  const token = watchCounter
  activeWatchToken = token
  watchId = navigator.geolocation.watchPosition(
    (position) => onPosition(token, position),
    (error) => onError(token, error),
    { enableHighAccuracy: true },
  )
  startTimer()
}

/**
 * SPEC 4.6.2's Backgrounding paragraph, and G3: `watchPosition` stops
 * delivering while the document is hidden and does not resume on its own,
 * so a watch that merely kept running would go on to push the last
 * position it saw, which after a walk to somewhere else is a wrong
 * position rather than a missing one -- the worse of the two. On hide the
 * watch and the timer are cleared; on the way back to visible, with the
 * control still on, both are rebuilt through `beginWatch`, which tears down
 * unconditionally first.
 *
 * **The last reading is dropped on hide, not on the way back.** That
 * placement, not the other one, is what keeps the state true throughout:
 * `sharing` means a position has been received *and is what the timer
 * sends*, and while hidden nothing is sent -- a hidden document is not
 * invisible either, iOS renders it in the app switcher, so a state left at
 * `sharing` for the whole trip would be this app describing something it is
 * not doing. Dropping the reading here makes the state `waiting` for the
 * whole trip, which is true, and `publish()` is what makes that visible
 * rather than latent. It also does the resume's job in the right place:
 * tearing the watch down on hide only stops the pushes that happen *while*
 * hidden, and the timer re-armed on the way back would otherwise fire up to
 * a full interval before the new watch has delivered anything, sending the
 * coordinate taken before the phone was pocketed -- the wrong position
 * rather than the missing one, which is the trade this whole paragraph
 * exists to refuse. With `lastPosition` already gone, the resume has
 * nothing stale to push, and the state on resume is `waiting` until the new
 * watch produces something, the same as an ordinary tap on `off`.
 *
 * This is not the answer to issue #120's question about the socket (SPEC
 * 4.6.2): a watch and a connection are two different things this app
 * happens to both hold, and tearing one down says nothing about the other.
 */
function onVisibilityChange(): void {
  if (!controlOn) return
  if (document.hidden) {
    lastPosition = null
    stopWatchAndTimer()
    publish()
    return
  }
  beginWatch()
}

/**
 * `token` is the value `beginWatch` minted for the watch this callback
 * belongs to (SPEC 4.6.2): a mismatch against `activeWatchToken` means the
 * watch that produced this callback has since been torn down, and the
 * callback is a stale echo from a platform that does not promise
 * `clearWatch` cancels what it has already queued. Ignored outright rather
 * than acted on in any way, because a state this module keeps cannot tell a
 * stale callback from a live one -- the token is what can.
 */
function onPosition(token: number, position: GeolocationPosition): void {
  if (token !== activeWatchToken) return
  lastPosition = {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
    accuracy: position.coords.accuracy,
  }
  publish()
  // SPEC 4.6.2: pushing on every `watchPosition` callback is the
  // optimisation on top of the timer's floor, not a replacement for it -- a
  // stationary phone produces no further callbacks and its position is
  // still true, which is what the timer alone is for.
  pushPosition()
}

/** See `onPosition`'s own docstring for what `token` guards against. */
function onError(token: number, error: GeolocationPositionError): void {
  if (token !== activeWatchToken) return
  // SPEC 4.6.2, G4: `PERMISSION_DENIED` (code 1), and only that code, is
  // what §4.6.1's row 3 is. `POSITION_UNAVAILABLE` (2) and `TIMEOUT` (3)
  // are not a reason to change state or tear anything down: the watch is
  // still live and a later position is still possible.
  if (error.code !== GeolocationPositionError.PERMISSION_DENIED) return
  // SPEC 4.6.2: reaching `denied` tears the watch and the timer down
  // rather than leaving them running behind a state that says the browser
  // refused -- a watch that has reported `PERMISSION_DENIED` will not
  // deliver a position afterwards, so keeping it is the module still
  // appearing to try at something it has been told it may not do.
  deniedSticky = true
  controlOn = false
  lastPosition = null
  stopWatchAndTimer()
  detachVisibility()
  publish()
}

/**
 * Turning the control on, and "try again" from `denied` alike (SPEC
 * 4.6.2): the only honest recovery from a sticky denial is letting the
 * owner attempt the watch again, which is this same function. The fresh
 * attempt starts a new watch -- `beginWatch()` tears down unconditionally
 * first -- and it is a new one rather than the old one resumed.
 *
 * **The bookkeeping is set before `beginWatch()` is called, not after.**
 * `watchPosition`'s callbacks are asynchronous on every platform this app
 * targets, so ordering them the other way around was harmless in practice
 * -- but it was harmless *because of* that guarantee, not independently of
 * it, and setting the flags first does not need it: a synchronous
 * `onError` (nothing here rules one out on a platform nobody has measured)
 * would otherwise have its denial overwritten by the three assignments
 * that follow it, leaving `controlOn` true and a live timer behind a state
 * that says the browser refused.
 *
 * Everything here is still synchronous, `beginWatch()` included: iOS shows
 * the permission prompt only for a call made from inside a user gesture's
 * own call stack (G1), and fails silently, with neither callback firing,
 * for one that is not. `toggleSharing` below calls this directly from the
 * control's `onclick`, and nothing between that tap and `beginWatch()`'s
 * own `watchPosition` call -- including the bookkeeping now ahead of it --
 * may be `await`ed or otherwise deferred.
 *
 * No `geolocationSupported()` guard of its own: this function's only
 * caller, `toggleSharing`, already refuses to reach it while unsupported,
 * and a second copy of that check here would be a branch nothing can ever
 * take, the same objection SPEC 4.6.2 makes against calling
 * `navigator.permissions` for an answer no sentence uses.
 */
function turnOn(): void {
  controlOn = true
  deniedSticky = false
  lastPosition = null
  attachVisibility()
  beginWatch()
  publish()
}

function turnOff(): void {
  controlOn = false
  lastPosition = null
  stopWatchAndTimer()
  detachVisibility()
  publish()
}

/**
 * The Settings control's own `onclick` (SPEC 4.6.2): on `off` or `denied`
 * this attempts the watch (`turnOn`); on `waiting` or `sharing` this stops
 * it (`turnOff`); on `unsupported` there is nothing this control can do and
 * the tap is a no-op, the same as SPEC 4.6.1 keeps "not fitted" out of the
 * interface's own four-row table.
 */
export function toggleSharing(): void {
  if (!geolocationSupported()) return
  if (controlOn) {
    turnOff()
    return
  }
  turnOn()
}
