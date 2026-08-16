<script module lang="ts">
  /** Bottom bar in portrait, leading-edge rail in the landscape range of SPEC 4.5. */
  export type NavLayout = 'bar' | 'rail'
</script>

<script lang="ts">
  import { barRoutes, currentRoute, followLink, sheetRoutes } from '../lib/router'
  import { inertWhen } from './MoreSheet.svelte'

  interface Props {
    layout: NavLayout
    /** Drives aria-expanded on the More control; the sheet itself lives in App. */
    sheetOpen: boolean
    /** True while the sheet is open, so the bar leaves the accessibility tree. */
    inert?: boolean
    onmore: () => void
  }

  let { layout, sheetOpen, inert = false, onmore }: Props = $props()

  let moreEl: HTMLButtonElement | undefined = $state()

  /**
   * The sheet returns focus here when it closes (SPEC 4.5). Focus that is
   * trapped but never returned is the defect a screen-reader user notices
   * first, so the control that opened the dialog is exposed rather than
   * looked up by id from the outside.
   */
  export function focusMore(): void {
    moreEl?.focus()
  }

  const sheetPaths = sheetRoutes.map((route) => route.path)

  // Without this the indicator vanishes from the bar entirely while the user is
  // in Peers, Mirror or Settings, which are reached through More.
  let moreIsCurrent = $derived(sheetPaths.includes($currentRoute.path))
</script>

{#snippet icon(id: string)}
  <svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    {#if id === 'dashboard'}
      <path d="M3 11 12 4l9 7" />
      <path d="M5 10v10h14V10" />
    {:else if id === 'wifi'}
      <path d="M2 8.5a15 15 0 0 1 20 0" />
      <path d="M5.5 12.5a10 10 0 0 1 13 0" />
      <path d="M9 16.5a5 5 0 0 1 6 0" />
      <circle cx="12" cy="20" r="1" />
    {:else if id === 'map'}
      <path d="M9 4 3 6.5v13L9 17l6 3 6-2.5v-13L15 7Z" />
      <path d="M9 4v13M15 7v13" />
    {:else if id === 'log'}
      <path d="M4 5h16M4 10h16M4 15h11M4 20h7" />
    {:else}
      <circle cx="5" cy="12" r="1.5" />
      <circle cx="12" cy="12" r="1.5" />
      <circle cx="19" cy="12" r="1.5" />
    {/if}
  </svg>
{/snippet}

<!-- A nav, not a tablist: the fifth slot opens a dialog rather than switching a
     panel, so tab semantics would be a lie (SPEC 4.5). -->
<nav class="nav" data-layout={layout} aria-label="Primary" use:inertWhen={inert}>
  <ul>
    {#each barRoutes as route (route.id)}
      <li>
        <a
          href={route.path}
          data-nav={route.id}
          aria-label={route.label}
          aria-current={$currentRoute.id === route.id ? 'page' : undefined}
          onclick={(event) => followLink(event, route.path)}
        >
          {@render icon(route.id)}
          <!-- 72px does not hold a readable label, and a truncated one is worse
               than none, so the rail is icon-only and leans on aria-label. -->
          {#if layout === 'bar'}<span class="label">{route.label}</span>{/if}
        </a>
      </li>
    {/each}

    <li>
      <!-- A button, unlike the four route entries: this one opens a dialog
           rather than going anywhere, so it has to answer to Space as well as
           Enter. aria-current is valid on a button and still has to be here,
           or the indicator disappears while a sheet-hosted view is current. -->
      <button
        type="button"
        id="nav-more"
        data-nav="more"
        bind:this={moreEl}
        aria-label="More"
        aria-haspopup="dialog"
        aria-controls="more-sheet"
        aria-expanded={sheetOpen}
        aria-current={moreIsCurrent ? 'page' : undefined}
        onclick={onmore}
      >
        {@render icon('more')}
        {#if layout === 'bar'}<span class="label">More</span>{/if}
      </button>
    </li>
  </ul>
</nav>

<style>
  .nav {
    /* SPEC 4.5.1: "touch targets need separation as well as size". 44px says
       how large an entry is and nothing about how far it sits from the next
       one, and two large targets flush against each other are easy to hit
       wrongly. The separation is non-tappable bar, so it comes out of the
       entries rather than being added to the bar: at the 402px reference
       profile five entries share 402 - 4 x 4 = 386px, 77.2px each, still well
       over the floor. This is a minimum rather than a house figure: the rail
       already sat further apart than this and keeps its own spacing, because
       the rule is that entries must not touch, not that every layout has to
       separate them by the same number of pixels. */
    --nav-entry-gap: 4px;

    /* Absolute, not fixed: fixed would position against the viewport the engine
       declares, which in iOS standalone is shorter than the screen (SPEC
       4.2.1). The shell is the box that knows the right height. */
    position: absolute;
    z-index: 20;
    background: var(--surface);
    border-color: var(--border);
  }

  ul {
    display: flex;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  a,
  button {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.25rem;
    min-width: 44px;
    min-height: 44px;
    padding: 0;
    border: 0;
    background: none;
    color: var(--text-dim);
    text-decoration: none;
    -webkit-tap-highlight-color: transparent;
  }

  a[aria-current='page'],
  button[aria-current='page'] {
    color: var(--accent);
  }

  a:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }

  .icon {
    width: 24px;
    height: 24px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.75;
    stroke-linecap: round;
    stroke-linejoin: round;
  }

  .label {
    font-size: 0.6875rem;
    line-height: 1;
    letter-spacing: 0.01em;
  }

  /* Portrait: a bar across the bottom, clear of the home indicator.

     The height is stated here with the same expression main uses to reserve
     room for it, and box-sizing is border-box globally, so the hairline is
     counted inside the figure instead of being added on top of it. Sizing the
     list to the token and letting the border grow the bar past it left the last
     row of scrollable content one pixel under the bar in both engines. Two
     places computing the same number is how that came back; one expression,
     used by both sides, is how it cannot. The rail below already worked this
     way, which is why the rail seam was exact and this one was not. */
  .nav[data-layout='bar'] {
    inset: auto 0 0 0;
    height: calc(var(--nav-bar-height) + var(--safe-bottom));
    padding-bottom: var(--safe-bottom);
    padding-inline: var(--safe-left) var(--safe-right);
    border-top: 1px solid var(--border);
  }

  .nav[data-layout='bar'] ul {
    align-items: stretch;
    /* What the bar has left after its own border and the home indicator. */
    height: 100%;
    /* The gap is taken out of the row, not added to it: `flex: 1 1 0` on the
       items divides what is left, so the bar's own height and the space main
       reserves for it are untouched (SPEC 4.5.1). */
    gap: var(--nav-entry-gap);
  }

  .nav[data-layout='bar'] li {
    flex: 1 1 0;
    display: flex;
  }

  .nav[data-layout='bar'] a,
  .nav[data-layout='bar'] button {
    flex: 1 1 auto;
    flex-direction: column;
    justify-content: center;
    gap: 0.2rem;
  }

  /* Landscape range: a rail on the leading edge. The 72px is content width
     outside the safe-area inset, because the leading edge in landscape is
     exactly where that inset is non-zero and which side it lands on depends on
     the rotation direction (SPEC 4.5). */
  .nav[data-layout='rail'] {
    inset: 0 auto 0 0;
    width: calc(var(--nav-rail-width) + var(--safe-left));
    padding-inline-start: var(--safe-left);
    padding-block: var(--safe-top) var(--safe-bottom);
    border-inline-end: 1px solid var(--border);
  }

  .nav[data-layout='rail'] ul {
    flex-direction: column;
    justify-content: center;
    height: 100%;
    width: var(--nav-rail-width);
    /* Unchanged, and comfortably past --nav-entry-gap: the rail has always held
       its entries apart, so SPEC 4.5.1 asks nothing of it. Narrowing this to
       the bar's 4px would be tightening a layout that was already right. */
    gap: 0.5rem;
  }

  .nav[data-layout='rail'] a,
  .nav[data-layout='rail'] button {
    width: var(--nav-rail-width);
    height: 48px;
  }
</style>
