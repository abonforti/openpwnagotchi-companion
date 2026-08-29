// SPEC 4.5.2.4. The Peers view's one list rule that is not wording: sort
// order. Every string that section fixes lives in lib/format.ts, the same
// discipline lib/wifi.ts already follows for the Wi-Fi view (SPEC 4.5.2.3).

import { compareKnownFirst } from './lists'
import type { Peer } from './protocol'

/**
 * Sorted by last seen, most recent first, with the signal as the tie-break,
 * strongest first, and the fingerprint after that so the order is total in
 * the sense SPEC 4.5.2.3 requires -- rows that do not swap places between
 * frames carrying the same data. Last seen leads rather than signal because
 * a peer list answers *who has been around*, and a peer met an hour ago at
 * a strong signal is further from that question than one met a minute ago
 * at a weak one (SPEC 4.5.2.4).
 *
 * A `null` `lastSeen` sorts last, and a `null` `rssi` sorts after a known
 * one within its group. Neither is a judgement about the peer; it is what
 * keeps the order total when the wire is missing a field -- and `lib/
 * lists.ts`'s `compareKnownFirst`, not a bare subtraction, is what keeps it
 * total when the wire instead carries a non-finite one (issue #109).
 */
export function sortPeers(list: Peer[]): Peer[] {
  const sorted = [...list]
  sorted.sort((a, b) => {
    const lastSeenCompare = compareKnownFirst(a.lastSeen, b.lastSeen, 'desc')
    if (lastSeenCompare !== 0) return lastSeenCompare
    const rssiCompare = compareKnownFirst(a.rssi, b.rssi, 'desc')
    if (rssiCompare !== 0) return rssiCompare
    if (a.fingerprint < b.fingerprint) return -1
    if (a.fingerprint > b.fingerprint) return 1
    return 0
  })
  return sorted
}
