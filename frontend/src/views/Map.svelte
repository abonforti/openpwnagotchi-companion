<script lang="ts">
  // SPEC 4.5.2.7. Which captures are trusted enough to plot, where the
  // unit's own marker comes from, and the tile layer's own constants all
  // live in lib/map.ts; the three empty-state sentences live in
  // lib/format.ts, the same split every other list view draws. This view
  // subscribes to the handshakes and gps stores (lib/stores.ts), drives
  // Leaflet from what lib/map.ts returns, and owns nothing about either
  // rule itself.
  import 'leaflet/dist/leaflet.css'

  import * as L from 'leaflet'
  import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
  import markerIcon from 'leaflet/dist/images/marker-icon.png'
  import markerShadow from 'leaflet/dist/images/marker-shadow.png'
  import { onDestroy, onMount } from 'svelte'

  import { formatMapCaption, formatMapEmptyMessage, REFRESH_LABEL } from '../lib/format'
  import { hasListDataArrived } from '../lib/lists'
  import {
    capturesWithGps,
    DEFAULT_CENTER,
    DEFAULT_ZOOM,
    FOCUSED_ZOOM,
    LEAFLET_ATTRIBUTION_PREFIX,
    TILE_ATTRIBUTION,
    TILE_MAX_ZOOM,
    TILE_URL_TEMPLATE,
    unitPosition,
  } from '../lib/map'
  import { currentRoute } from '../lib/router'
  import { connection, gps, handshakes, refreshHandshakes } from '../lib/stores'
  import { watchViewRefresh } from '../lib/viewRefresh'

  // Leaflet's default marker locates its own images relative to wherever it
  // guesses its script was loaded from, a heuristic that does not survive a
  // bundler (SPEC 4.1/D2: bundled locally, so there is no CDN path for it to
  // guess correctly either). `IconDefault.prototype._getIconUrl` (own method,
  // not inherited) prefixes whatever `mergeOptions` sets with a path it
  // detects from `leaflet.css`'s own stylesheet or a `<link>` tag -- neither
  // exists in a bundled page, so the prefix silently becomes `''` today and
  // would become garbage the moment Vite's asset naming changes. Deleting the
  // override drops back to `Icon.prototype._getIconUrl`, which returns the
  // option verbatim with no path guessing at all, so the URLs Vite bundled
  // are used exactly as bundled. `@types/leaflet` never declares this
  // private method, so reaching it at all is a narrow cast rather than a
  // widened type.
  delete (L.Icon.Default.prototype as { _getIconUrl?: unknown })._getIconUrl
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: markerIcon2x,
    iconUrl: markerIcon,
    shadowUrl: markerShadow,
  })

  const conn = $derived($connection)
  const fetched = $derived(hasListDataArrived(conn.state))

  const captureList = $derived($handshakes.entries)
  const pins = $derived(capturesWithGps(captureList))
  const unit = $derived(unitPosition($gps))

  // SPEC 4.5.2.7's three empty states for the captures layer: rendered
  // alongside the map, never in place of it -- the unit's own marker can
  // still be worth showing with zero captures on the wire. Gated on `pins`
  // alone, not on `fetched` as well: losing the connection does not erase
  // what was already plotted, and an empty sentence must never sit over a
  // map that is still showing pins from before the disconnect.
  const showEmptyMessage = $derived(pins.length === 0)
  const emptyText = $derived(formatMapEmptyMessage(fetched, captureList.length > 0))

  // SPEC 4.5.2.7's caption: "state three is a spectrum rather than a
  // switch" -- shown once there is a reply to count, the same gate the
  // empty-state sentence above reads `fetched` for. Counts `$handshakes.total`,
  // not the entries actually returned: a truncated reply still holds the
  // unit's real capture count, and a caption reading the array length would
  // understate it the same way an uncapped badge would (SPEC 4.5.2.3).
  const captionText = $derived(formatMapCaption($handshakes.total, pins.length))

  // SPEC 4.5.2.7: the Map's own refresh, the second caller of
  // lib/stores.ts's refreshHandshakes alongside the Wi-Fi view's -- the same
  // three-occasion trigger (lib/viewRefresh.ts), not a rule rewritten here.
  const mapRefresh = watchViewRefresh('map', refreshHandshakes)

  function requestRefresh(): void {
    mapRefresh.refreshNow()
  }

  let mapEl: HTMLDivElement | undefined = $state()
  // $state, not a plain `let`: the three `$effect`s below read `map`, and a
  // plain `let` only reaches them because `onMount`'s callback happens to run
  // before those effects in source order today. Reordering the script would
  // silently turn every one of them into a no-op forever, with nothing to
  // catch it.
  let map: L.Map | undefined = $state()
  let unitMarker: L.Marker | undefined
  let pinLayer: L.LayerGroup | undefined
  // SPEC 4.5.2.7: which source centres the map is decided by what is
  // available, not by which arrived first. Two flags rather than one, so the
  // unit's own fix -- whenever it arrives -- can still correct a centring
  // already made from a fallback capture; a single shared flag would let
  // whichever effect ran first with data lock the other out for good, which
  // is exactly the race SPEC 4.5.2.7 names. Once the unit's own position has
  // centred the map, that is final -- panning or zooming away from it is not
  // fought on the next `stats` frame -- and a capture-based centring is
  // final in the same way unless the unit's position later arrives.
  let centeredOnUnit = false
  let centeredOnCapture = false

  onMount(() => {
    if (!mapEl) return
    const instance = L.map(mapEl).setView(DEFAULT_CENTER, DEFAULT_ZOOM)
    // SPEC 4.5.2.7: the flag Leaflet's default prefix draws fails the
    // contrast floor; this keeps both credits and drops only the decoration.
    instance.attributionControl.setPrefix(LEAFLET_ATTRIBUTION_PREFIX)
    L.tileLayer(TILE_URL_TEMPLATE, {
      attribution: TILE_ATTRIBUTION,
      maxZoom: TILE_MAX_ZOOM,
    }).addTo(instance)
    pinLayer = L.layerGroup().addTo(instance)
    map = instance
  })

  onDestroy(() => {
    map?.remove()
    map = undefined
    pinLayer = undefined
    unitMarker = undefined
  })

  // SPEC 4.5.2.7's own Leaflet-lifecycle note: every view stays mounted
  // (SPEC 4.5), so this map is created once, hidden, at app start, and a
  // hidden element measures zero. Leaflet keeps whatever size it read at
  // that moment unless told otherwise, so the one thing this view does on
  // becoming current is ask it to measure again.
  $effect(() => {
    if ($currentRoute.id === 'map' && map) {
      map.invalidateSize()
    }
  })

  $effect(() => {
    const instance = map
    if (!instance) return
    if (unit === null) {
      unitMarker?.remove()
      unitMarker = undefined
      return
    }
    const latlng: L.LatLngExpression = [unit.lat, unit.lon]
    if (unitMarker) {
      unitMarker.setLatLng(latlng)
    } else {
      unitMarker = L.marker(latlng).addTo(instance)
    }
    if (!centeredOnUnit) {
      instance.setView(latlng, FOCUSED_ZOOM)
      centeredOnUnit = true
    }
  })

  $effect(() => {
    const instance = map
    const layer = pinLayer
    if (!instance || !layer) return
    // SPEC 4.4.1: the same "replaced whole, never merged" rule the stores
    // this reads already follow -- the pin layer mirrors handshakes.entries
    // rather than being diffed against its own previous contents.
    layer.clearLayers()
    for (const pin of pins) {
      // SPEC 4.5.3: the SSID is a string a stranger chose, the same as every
      // other hostile string this app renders. `bindPopup` treats a string
      // argument as HTML, which the row views never do -- Svelte's own
      // `{entry.ssid}` interpolation escapes it instead -- so the popup
      // content is built as a text node explicitly rather than handed the
      // string directly.
      const content = document.createElement('span')
      content.textContent = pin.ssid
      L.marker([pin.lat, pin.lon]).bindPopup(content).addTo(layer)
    }
    if (!centeredOnUnit && !centeredOnCapture && pins.length > 0) {
      const first = pins[0]
      if (first) {
        instance.setView([first.lat, first.lon], FOCUSED_ZOOM)
        centeredOnCapture = true
      }
    }
  })
</script>

<section class="view map-view" data-view="map" aria-labelledby="view-title-map">
  <div class="header-row">
    <h1 id="view-title-map">Map</h1>
    <button type="button" data-action="refresh" onclick={requestRefresh}>
      {REFRESH_LABEL}
    </button>
  </div>

  {#if fetched}
    <!-- SPEC 4.5.2.7: "a caption says how many captures have a position",
         observable evidence of lib/map.ts's finite-coordinate rule without a
         test counting Leaflet's own markers. -->
    <p class="caption" data-map-caption>{captionText}</p>
  {/if}

  {#if showEmptyMessage}
    <p class="empty-message" data-empty-message role="status">{emptyText}</p>
  {/if}

  <!-- SPEC 4.5.1's geometry rule, applied here rather than restated: the map
       surface is never as wide or as tall as the views area, in either
       orientation, so a drag always has somewhere to begin a page scroll
       instead of panning the map. data-map-surface, not Leaflet's own
       .leaflet-container, is what frontend/tests/e2e/geometry.spec.ts
       measures (SPEC 4.5.2.7): a library's own class names are its private
       vocabulary, and a test written against them breaks on a Leaflet
       upgrade that changed nothing this app cares about. -->
  <div class="map-container" data-map-surface bind:this={mapEl}></div>
</section>

<style>
  /* SPEC 4.5.1's geometry rule failed in landscape when the map surface's
     own height was expressed against the viewport (`min(60vh, 480px)`):
     `vh` has no notion of the header and the navigation the shell has
     already spent, so a value that left room in portrait, where the
     viewport is tall, overflowed the shell in landscape, where it is not.
     The fix mirrors Log's own solution to the neighbouring problem
     (Log.svelte's Geometry comment, and app.css's `[data-view-slot='map']`
     rule this class now shares with `[data-view-slot='log']`): a flex
     column filling the height the view slot hands down, so the map
     surface below is sized against the space this view actually has
     rather than against the whole screen. */
  .map-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    gap: 0.75rem;
  }

  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
  }

  h1 {
    margin: 0;
  }

  /* app.css sets a button's size and font but not its colours: a button
     with nothing else applied paints WebKit's platform default under
     whatever foreground this view inherits, light text on light grey,
     invisible in the dark palette on that engine specifically (the same
     note WiFi.svelte, Peers.svelte, Log.svelte, Mirror.svelte and
     Settings.svelte carry). Every surface here is var(--bg), so an explicit
     background and colour are what stop the platform default from ever
     being reached. */
  button {
    min-height: 44px;
    padding: 0 1rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
  }

  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }

  .caption,
  .empty-message {
    margin: 0;
    color: var(--text-dim);
  }

  /* SPEC 4.5.1: narrower and shorter than the views area in both
     orientations -- app.css's own `.view[data-view='map']` rule already
     drops the reading-measure max-width the way Log's does, so the width
     bound here has to come from this element instead of from the view root
     staying narrow. The height comes from `.map-view`'s flex column above:
     `flex: 1` claims whatever is left after the header row and the
     caption/empty-message, the same role Log's `.log-lines` plays in its
     own column, so the surface is always shorter than the views area by at
     least that much and never depends on how tall the viewport itself is.
     `min-height` is a floor for a window shorter than this app's reference
     profile (SPEC 4.2.1) can shrink the flex item below, not a ceiling. */
  .map-container {
    width: 100%;
    flex: 1;
    min-height: 220px;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
</style>
