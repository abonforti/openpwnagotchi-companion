// SPEC 4.5.2.7. Every rule that decides which captures are trusted enough to
// plot, where the unit's own marker comes from, and how the tile layer is
// built lives here; views/Map.svelte only lays out what these functions
// return and drives Leaflet from them, the same split lib/wifi.ts and
// lib/peers.ts already draw for their own views.

import type { Gps, HandshakeEntry } from './protocol'

/** A capture worth a pin: the fields the marker and its popup need, nothing more. */
export interface MapPin {
  ssid: string
  lat: number
  lon: number
}

/** The unit's own position, read from `stats.gps`, never from a browser fix. */
export interface UnitPosition {
  lat: number
  lon: number
}

function isFiniteCoordinate(value: number | null): value is number {
  return value !== null && Number.isFinite(value)
}

/**
 * The captures worth a pin. `HandshakeEntry.gps` is `HandshakeGps | null`,
 * and even where it is not `null`, `lib/stores.ts` writes the wire payload
 * with no runtime validation (issue #109), so `lat`/`lon` are not trusted to
 * be finite numbers just because the schema calls them required. A capture
 * with no sidecar, or a sidecar whose coordinates fail this check, is left
 * off the map rather than plotted at `(0, 0)` or at `NaN`, which Leaflet
 * would not refuse the way this function does.
 */
export function capturesWithGps(entries: HandshakeEntry[]): MapPin[] {
  const pins: MapPin[] = []
  for (const entry of entries) {
    const gps = entry.gps
    if (gps === null) continue
    const { lat, lon } = gps
    if (!isFiniteCoordinate(lat) || !isFiniteCoordinate(lon)) continue
    pins.push({ ssid: entry.ssid, lat, lon })
  }
  return pins
}

/**
 * The unit's own marker position, read from `stats.gps` (`lib/stores.ts`'s
 * `gps` store) and never from `lib/geo.ts`'s browser fix: that module holds
 * what this phone is offering the unit, not what the unit currently has, and
 * plotting it here would show the reader their own position labelled as the
 * unit's. Not gated on `fix` as a second condition: `lat`/`lon` are `null`
 * exactly when there is no reading to show, so a finite pair already says
 * there is one.
 */
export function unitPosition(gps: Gps | null): UnitPosition | null {
  if (gps === null) return null
  const { lat, lon } = gps
  if (!isFiniteCoordinate(lat) || !isFiniteCoordinate(lon)) return null
  return { lat, lon }
}

/**
 * The only external origin on this screen (SPEC 2.15.1's `img-src` admits
 * `https://*.tile.openstreetmap.org` for exactly this). Leaflet substitutes
 * `{s}`/`{z}`/`{x}`/`{y}` itself from the coordinates it computed; nothing
 * that arrived on the wire is ever interpolated into this string.
 */
export const TILE_URL_TEMPLATE =
  'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
export const TILE_MAX_ZOOM = 19

/**
 * SPEC 4.5.2.7's "the attribution keeps the credit and loses the decoration":
 * Leaflet's own default `prefix` for its attribution control concatenates a
 * small flag drawn as inline SVG ahead of its "Leaflet" credit, and that
 * mark fails this project's contrast floor (1.34:1 and 1.74:1 against a
 * floor of 3:1). This is that same default with the flag markup removed and
 * the credit -- link, title and text -- copied verbatim, set through the
 * control's own `setPrefix`, never by patching the library.
 */
export const LEAFLET_ATTRIBUTION_PREFIX =
  '<a href="https://leafletjs.com" title="A JavaScript library for interactive maps">Leaflet</a>'

/** No position known yet: a plain world view, not a guess at where the unit is. */
export const DEFAULT_CENTER: [number, number] = [20, 0]
export const DEFAULT_ZOOM = 2
/** Zoom used the one time this view centres on a real position. */
export const FOCUSED_ZOOM = 15
