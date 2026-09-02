<script module lang="ts">
  /**
   * SPEC 4.5.2.3: shell furniture, not view markup -- see the file-level
   * note below. Generic over `Id` rather than `string`: a caller's `tabs`
   * prop fixes `Id` to its own literal union (`WifiSegment`, for
   * `WiFi.svelte`), so `onchange`, the `tab`/`panel` snippets and every
   * `t.id` this component hands back are typed to exactly the tabs that
   * caller passed in -- not a wider `string` a caller then has to narrow
   * back down with a runtime guard or an unchecked cast.
   */
  export interface SegmentTab<Id extends string = string> {
    id: Id
    label: string
  }
</script>

<script lang="ts" generics="Id extends string">
  import type { Snippet } from 'svelte'

  /**
   * SPEC 4.5.2.3: this lives in `src/shell/`, beside `Nav.svelte`, and not in
   * `views/WiFi.svelte`, the one view that uses it today. The reason is not
   * reuse, it is SPEC 10.7: `src/views/` is excused from the coverage gate
   * on the grounds that a view is markup, and roving tabindex, Arrow/Home/End
   * and automatic activation are not markup -- the same argument 10.7 already
   * made once for the shell's own navigation. A tablist implemented inside a
   * view would be keyboard logic sitting outside every gate this project has.
   *
   * A real `tablist`: roving tabindex, Arrow/Home/End movement, automatic
   * activation (selection follows focus), each tab owning a `role="tabpanel"`
   * labelled back by `aria-labelledby`. Both panels stay mounted, always --
   * the caller renders both `panel` snippets below -- so an expanded row
   * survives a switch, but the inactive one carries `hidden`: left merely
   * present, it would expose two tab panels to VoiceOver at once, the exact
   * defect the role is being claimed to avoid.
   *
   * Controlled, not owning its own selection: `active` and `onchange` are
   * the caller's, the same shape Nav.svelte's own routing already has, so
   * which tab is selected -- kept while the app is open, never persisted --
   * is a decision this component has no opinion about.
   */
  interface Props {
    /** Unique per instance, so two Segmented controls on one page never collide on id. */
    idPrefix: string
    /** The tablist's own accessible name -- it is a landmark, and the first control a screen-reader user reaches on the screen that hosts it, the same reason `Nav.svelte` names its own `<nav>`. */
    label: string
    tabs: SegmentTab<Id>[]
    active: Id
    onchange: (id: Id) => void
    /** The tab button's own content -- typically a label and a badge. */
    tab: Snippet<[SegmentTab<Id>]>
    /** The panel's content for one tab. */
    panel: Snippet<[SegmentTab<Id>]>
  }

  let { idPrefix, label, tabs, active, onchange, tab, panel }: Props = $props()

  // Arrow-key movement needs to focus a tab it is not currently rendering
  // as focused, which a click never has to do, so it needs a way to reach
  // an element by position. A per-tab `bind:this` into a keyed object
  // (`tabEls[t.id]`) was the first shape this took, and it is wrong for the
  // same reason `Dashboard.svelte`'s `focusOnMount` comment gives for its
  // own predecessor: `bind:this` into a plain object property is a write
  // Svelte 5 does not track (`binding_property_non_reactive`), so nothing
  // says the reference is there by the time something reads it, only that
  // it usually has been. Wrapping the object in `$state` would silence the
  // warning without answering the actual question, which is why this is a
  // single ref on the tablist container instead -- the same one
  // `MoreSheet.svelte`'s `focusable()` already uses for its own focus trap
  // -- and the buttons are found by querying it in DOM order, which is
  // `tabs` order, rather than kept in a second collection that could ever
  // disagree with the DOM about it.
  let tablistEl: HTMLDivElement | undefined = $state()

  // `:scope > [role="tab"]`, not a bare descendant selector: the buttons
  // this component renders are the only intended match, but the `tab`
  // snippet's content is rendered inside them and is the caller's markup,
  // not this component's. A descendant query would also match a
  // `role="tab"` the caller happened to put inside its own snippet content,
  // which would silently break the positional mapping to `tabs`. Scoping to
  // direct children is what makes "DOM order is tabs order" actually true
  // rather than true only for the callers this file has seen so far.
  function tabButtons(): HTMLButtonElement[] {
    if (!tablistEl) return []
    return Array.from(
      tablistEl.querySelectorAll<HTMLButtonElement>(':scope > [role="tab"]'),
    )
  }

  // Arrow/Home/End movement, with automatic activation: moving focus also
  // selects, rather than requiring a separate activation key (SPEC 4.5).
  function onKeydown(event: KeyboardEvent, index: number): void {
    let nextIndex: number
    switch (event.key) {
      case 'ArrowRight':
        nextIndex = (index + 1) % tabs.length
        break
      case 'ArrowLeft':
        nextIndex = (index - 1 + tabs.length) % tabs.length
        break
      case 'Home':
        nextIndex = 0
        break
      case 'End':
        nextIndex = tabs.length - 1
        break
      default:
        return
    }
    event.preventDefault()
    const next = tabs[nextIndex]
    if (!next) return
    onchange(next.id)
    tabButtons()[nextIndex]?.focus()
  }
</script>

<div class="segmented">
  <div class="tablist" role="tablist" aria-label={label} bind:this={tablistEl}>
    {#each tabs as t, index (t.id)}
      <button
        type="button"
        role="tab"
        id={`${idPrefix}-tab-${t.id}`}
        data-segment={t.id}
        aria-selected={t.id === active}
        aria-controls={`${idPrefix}-panel-${t.id}`}
        tabindex={t.id === active ? 0 : -1}
        onclick={() => onchange(t.id)}
        onkeydown={(event) => onKeydown(event, index)}
      >
        {@render tab(t)}
      </button>
    {/each}
  </div>
  {#each tabs as t (t.id)}
    <div
      class="tabpanel"
      role="tabpanel"
      id={`${idPrefix}-panel-${t.id}`}
      data-segment-panel={t.id}
      aria-labelledby={`${idPrefix}-tab-${t.id}`}
      hidden={t.id !== active}
    >
      {@render panel(t)}
    </div>
  {/each}
</div>

<style>
  .tablist {
    display: flex;
    /* SPEC 4.5.1: touch targets need separation as well as size, on any row
       of adjacent controls, not only the bottom bar Nav.svelte already
       spaces out this way. */
    gap: 0.5rem;
    margin-bottom: 0.75rem;
  }

  .tablist button {
    flex: 1 1 0;
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.375rem;
    padding: 0 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    /* Every surface here is var(--bg), so an explicit background and colour
       are what stop WebKit's platform default (light text on light grey)
       from ever being reached in the dark palette (SPEC's contrast gate,
       the same note Dashboard.svelte and Settings.svelte carry). */
    background: var(--surface);
    color: var(--text-dim);
    font: inherit;
  }

  .tablist button[aria-selected='true'] {
    border-color: var(--accent);
    color: var(--accent);
  }

  .tablist button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
</style>
