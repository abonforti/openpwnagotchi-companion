<script module lang="ts">
  /**
   * The rail applies here and nowhere else (SPEC 4.5). The word "landscape"
   * alone is not the condition: `(orientation: landscape)` and
   * `(min-aspect-ratio: 1/1)` disagree, and on iOS the software keyboard flips
   * `orientation` underneath the layout, so the height bound is what keeps a
   * tall landscape window on the bottom bar.
   */
  export const RAIL_MEDIA_QUERY = '(orientation: landscape) and (max-height: 560px)'
</script>

<script lang="ts">
  import { onMount, type Component } from 'svelte'

  import { currentRoute, routes, startRouter, type ViewId } from './lib/router'
  import MoreSheet, { inertWhen } from './shell/MoreSheet.svelte'
  import Nav, { type NavLayout } from './shell/Nav.svelte'
  import Dashboard from './views/Dashboard.svelte'
  import Log from './views/Log.svelte'
  import MapView from './views/Map.svelte'
  import Mirror from './views/Mirror.svelte'
  import Peers from './views/Peers.svelte'
  import Settings from './views/Settings.svelte'
  import WiFi from './views/WiFi.svelte'

  const views: Record<ViewId, Component> = {
    dashboard: Dashboard,
    wifi: WiFi,
    map: MapView,
    log: Log,
    peers: Peers,
    mirror: Mirror,
    settings: Settings,
  }

  function currentLayout(): NavLayout {
    if (typeof window.matchMedia !== 'function') {
      return 'bar'
    }
    return window.matchMedia(RAIL_MEDIA_QUERY).matches ? 'rail' : 'bar'
  }

  // Read before the first paint rather than on mount, so a device already held
  // in the landscape range does not paint a bottom bar and then swap it.
  let layout = $state<NavLayout>(currentLayout())
  let sheetOpen = $state(false)
  let nav: ReturnType<typeof Nav> | undefined = $state()

  onMount(() => {
    const stopRouter = startRouter()

    if (typeof window.matchMedia !== 'function') {
      return stopRouter
    }
    const query = window.matchMedia(RAIL_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent): void => {
      layout = event.matches ? 'rail' : 'bar'
    }
    query.addEventListener('change', onChange)

    return () => {
      query.removeEventListener('change', onChange)
      stopRouter()
    }
  })

  function closeSheet(): void {
    sheetOpen = false
    // Focus goes back to the control that opened the dialog. Trapped focus that
    // is never returned is the defect screen-reader users notice first.
    nav?.focusMore()
  }
</script>

<!-- Everything outside the sheet goes inert while it is open. aria-modal alone
     is not enough: VoiceOver on iOS honours it inconsistently, and swipe
     navigation otherwise walks straight into the bar and the views behind the
     dialog. The sheet's own Tab trap stays as the keyboard half of this. -->
<div class="shell" data-layout={layout}>
  <!-- One line in both layouts; in the rail range it only moves inboard of the
       rail, which is all the collapse SPEC 4.5 asks of it. Unit name and
       connection state land here once the stores exist. -->
  <header class="app-header" use:inertWhen={sheetOpen}>
    <span class="app-title">companion</span>
  </header>

  <main use:inertWhen={sheetOpen}>
    <!-- Every view stays mounted. The Map keeps its viewport and zoom and the
         Log keeps its buffer and scroll position; re-initialising them on each
         visit reads as a bug (SPEC 4.5). -->
    {#each routes as route (route.id)}
      {@const View = views[route.id]}
      <div class="view-slot" hidden={$currentRoute.id !== route.id}>
        <View />
      </div>
    {/each}
  </main>

  <Nav
    bind:this={nav}
    {layout}
    {sheetOpen}
    inert={sheetOpen}
    onmore={() => (sheetOpen = true)}
  />
  <MoreSheet open={sheetOpen} onclose={closeSheet} />
</div>

<style>
  /* A pinned box rather than a flow-height one: the header and the navigation
     are positioned against the shell, not against the viewport, which is what
     lets the standalone rule below move all of them at once. */
  .shell {
    position: fixed;
    inset: 0;
    overflow: hidden;
  }

  /* iOS standalone with black-translucent declares a viewport shorter than the
     screen by exactly the top inset, so a box pinned to it leaves a dead band
     at the bottom of the screen that the page cannot paint (SPEC 4.2.1).
     100vh is the only unit that measured the full screen in that mode; it stays
     out of the way everywhere else, where 100vh is the large viewport and would
     put the bar under Safari toolbar. The flag is set from navigator.standalone
     in main.ts, because the display-mode query does not match on iOS. */
  :global(html[data-standalone=true]) .shell {
    bottom: auto;
    height: 100vh;
  }

  .app-header {
    position: absolute;
    inset: 0 0 auto 0;
    z-index: 10;
    display: flex;
    align-items: center;
    height: calc(var(--header-height) + var(--safe-top));
    padding-top: var(--safe-top);
    padding-inline: max(1rem, var(--safe-left)) max(1rem, var(--safe-right));
    background: var(--bg);
    border-bottom: 1px solid var(--border);
  }

  .app-title {
    font-size: 0.9375rem;
    font-weight: 600;
    letter-spacing: -0.01em;
  }

  /* The scroll container. The shell itself cannot scroll: the header and the
     navigation are positioned inside it and would scroll away with it. */
  main {
    position: absolute;
    inset: 0;
    overflow-y: auto;
    overscroll-behavior: contain;

    /* Nothing is painted under the bar or the rail: the content area is inset
       by their size plus the safe area they sit in. */
    padding-top: calc(var(--header-height) + var(--safe-top));
    padding-bottom: calc(var(--nav-bar-height) + var(--safe-bottom));
    padding-inline: var(--safe-left) var(--safe-right);
  }

  .view-slot {
    display: flow-root;
  }

  /* `hidden` alone loses to a display declaration, and a view that is merely
     off-screen is still read out and still focusable. */
  .view-slot[hidden] {
    display: none;
  }

  .shell[data-layout='rail'] .app-header {
    padding-inline-start: calc(var(--nav-rail-width) + var(--safe-left));
  }

  .shell[data-layout='rail'] main {
    padding-inline-start: calc(var(--nav-rail-width) + var(--safe-left));
    padding-inline-end: var(--safe-right);
    padding-bottom: var(--safe-bottom);
  }
</style>
