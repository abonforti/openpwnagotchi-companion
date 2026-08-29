// SPEC 4.5.2.3. Nearby's own sort order, the one Wi-Fi list rule that is not
// wording and not shared with another view. Every string that section fixes
// lives in lib/format.ts, the same way Dashboard's and Settings' copy does
// (SPEC 4.5.1.1). The empty-versus-never-fetched predicate used to live here
// too; it moved to lib/lists.ts (SPEC 4.5.2.4) once Peers needed the same
// rule and the Wi-Fi-specific name it had here stopped fitting either
// caller.

import { compareKnownFirst } from './lists'
import type { AccessPoint } from './protocol'

export type NearbySortOrder = 'rssi' | 'channel'

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
 * `rssi` and `channel` are required integers by schema, with no `null`
 * case of their own, but `lib/lists.ts`'s `compareKnownFirst` is used for
 * the primary comparison anyway rather than a bare subtraction: `lib/
 * stores.ts` writes the wire payload with no runtime validation (issue
 * #109), and a non-finite reading fed to `b - a` returns `NaN`, which
 * hands `Array.prototype.sort` an unspecified order -- the opposite of the
 * total order this paragraph promises.
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
    const primary =
      order === 'rssi'
        ? compareKnownFirst(a.rssi, b.rssi, 'desc')
        : compareKnownFirst(a.channel, b.channel, 'asc')
    if (primary !== 0) return primary
    if (a.bssid < b.bssid) return -1
    if (a.bssid > b.bssid) return 1
    return 0
  })
  return sorted
}
