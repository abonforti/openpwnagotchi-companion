// SPEC 4.5.2.3. The Wi-Fi view's list rules that are not wording: Nearby's
// own sort order, and the empty-versus-never-fetched decision. Every string
// that section fixes lives in lib/format.ts, the same way Dashboard's and
// Settings' copy does (SPEC 4.5.1.1); this file is the two rules that are
// not strings.

import type { AccessPoint } from './protocol'
import type { ConnectionState } from './ws'

export type NearbySortOrder = 'rssi' | 'channel'

/**
 * SPEC 4.5.2.3: an empty list and a list nobody has fetched have to read
 * differently, and the two are told apart by the connection state rather
 * than a second "have we ever received one" flag in lib/stores.ts, which
 * would be a second source of truth for something the connection state
 * almost always already answers. There is a window where this is wrong --
 * connected, the burst's `stats` arrived, its `access_points` has not yet
 * -- and SPEC 4.5.2.3 accepts it rather than papering over it: both frames
 * leave the unit in the same burst, so the window is one frame wide.
 */
export function hasWifiDataArrived(state: ConnectionState): boolean {
  return state === 'connected' || state === 'degraded'
}

/**
 * Nearby's own order (SPEC 4.5.2.3): RSSI strongest first, or channel
 * ascending, with the BSSID as a tie-break, ascending too, so both orders
 * are total -- two access points reporting the same reading hold their
 * places between frames instead of swapping every twenty seconds. Both
 * directions are pinned in that section rather than left to this file to
 * pick: neither is derivable from the data, and a test cannot assert an
 * order nobody chose. The BSSID is attacker-chosen like everything else on
 * this screen, and using it to order rows is not using it as truth.
 *
 * Captured is deliberately not sorted anywhere in this codebase: the
 * plugin applies the 500-entry cap to its own ordering (SPEC 2.7/2.8), so
 * the list arrives as the 500 newest, and re-sorting it client-side would
 * present exactly that set in an order that hides what it is a set of.
 * There is therefore no `sortHandshakes` beside this function, by design.
 */
export function sortAccessPoints(list: AccessPoint[], order: NearbySortOrder): AccessPoint[] {
  const sorted = [...list]
  sorted.sort((a, b) => {
    const primary = order === 'rssi' ? b.rssi - a.rssi : a.channel - b.channel
    if (primary !== 0) return primary
    if (a.bssid < b.bssid) return -1
    if (a.bssid > b.bssid) return 1
    return 0
  })
  return sorted
}
