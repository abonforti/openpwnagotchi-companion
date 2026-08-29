// SPEC 4.5.2.3/4.5.2.4: list-view logic shared by every screen whose data
// lib/stores.ts does not fetch on its own -- today the Wi-Fi view's two
// segments and Peers. It moved here, out of lib/wifi.ts, because a name
// that names one screen invites the next list to write a fourth copy of
// the rule rather than read the third one and reuse it.

import type { ConnectionState } from './ws'

/**
 * SPEC 4.5.2.3 (the rule), SPEC 4.5.2.4 ("It is shared, not copied"): an
 * empty list and a list nobody has fetched have to read differently, and
 * the two are told apart by the connection state rather than a second
 * "have we ever received one" flag in lib/stores.ts, which would be a
 * second source of truth for something the connection state almost always
 * already answers. There is a window where this is wrong -- connected, the
 * burst's `stats` arrived, a list's own reply has not yet -- and SPEC
 * 4.5.2.3 accepts it rather than papering over it: both frames leave the
 * unit in the same burst, so the window is one frame wide.
 */
export function hasListDataArrived(state: ConnectionState): boolean {
  return state === 'connected' || state === 'degraded'
}

/**
 * A comparison between two per-row readings that may be missing (`null`)
 * or, despite a schema that calls some of them required integers, not a
 * finite number at runtime: `lib/stores.ts` writes the wire payload with no
 * runtime validation (issue #109), the same reasoning `lib/format.ts`'s
 * `formatPeerNumber` and `formatByteSize` are already built on. A missing
 * or non-finite reading always sorts last, in either direction -- it is not
 * a judgement about which end of the list an unknown belongs at, only that
 * a reading which is not there has nowhere later to go than last.
 *
 * Shared by `lib/wifi.ts`'s Nearby order and `lib/peers.ts`'s Peers order
 * (SPEC 4.5.2.3/4.5.2.4), and load-bearing for both: a bare `b - a` is
 * `NaN` for a non-finite operand, and a comparator that returns `NaN` gives
 * `Array.prototype.sort` an unspecified order, the opposite of the total
 * order each of those sections promises.
 */
export function compareKnownFirst(
  a: number | null,
  b: number | null,
  direction: 'asc' | 'desc',
): number {
  const aKnown = a !== null && Number.isFinite(a)
  const bKnown = b !== null && Number.isFinite(b)
  if (!aKnown && !bKnown) return 0
  if (!aKnown) return 1
  if (!bKnown) return -1
  return direction === 'desc' ? b - a : a - b
}
