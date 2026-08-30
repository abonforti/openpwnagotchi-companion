<script lang="ts">
  // SPEC 4.5.2.5. Every rule that turns a value into a string lives in
  // lib/format.ts, the filter lives in lib/log.ts, and the empty/not-fetched
  // split lives in lib/lists.ts, shared with the Wi-Fi and Peers views (SPEC
  // 4.5.2.3/4.5.2.4). This view subscribes to the log and logUnavailable
  // stores (lib/stores.ts) and lays out what those functions return; it does
  // not itself decide which sentence a state gets.
  import { untrack } from 'svelte'

  import {
    formatFollowLabel,
    formatLogEmptyMessage,
    LOG_FILTER_LABEL,
    LOG_FONT_SIZE_LABEL,
    LOG_NO_FILTER_MATCH_MESSAGE,
    REFRESH_LABEL,
  } from '../lib/format'
  import { hasListDataArrived } from '../lib/lists'
  import { filterLogLines } from '../lib/log'
  import { connection, log, logUnavailable, refreshLog } from '../lib/stores'
  import { watchViewFollow, watchViewRefresh } from '../lib/viewRefresh'

  // SPEC 4.5.2.5: "the interval is 10 seconds", a bracket rather than a
  // preference -- see lib/viewRefresh.ts's watchViewFollow and the
  // section's own paragraph for the reasoning. Named here, not in that
  // file, because it belongs to this screen alone.
  const FOLLOW_INTERVAL_MS = 10_000

  // SPEC 4.5.2.5: one control, no popover, cycling three named steps in a
  // fixed order and wrapping. Not persisted, kept only while the app is
  // open, the same rule §4.5 already gives the Wi-Fi segment and Peers
  // carry no state of their own for.
  type LogFontSize = 'small' | 'medium' | 'large'
  const FONT_SIZES: LogFontSize[] = ['small', 'medium', 'large']

  // SPEC 4.5.2.5: the scrolling container itself, bound so the effect below
  // can move it -- the one DOM read/write this view needs, since Svelte has
  // no declarative way to say "scroll to the end".
  let logLinesEl: HTMLDivElement | undefined = $state()

  let filterText = $state('')
  // SPEC 4.5.2.5: opt-in and off on arrival -- this starts false, and only
  // the follow control below ever sets it true. Not reset by navigation:
  // §4.5 keeps this view mounted, so leaving Log and coming back reads
  // whatever the owner last left it as.
  let following = $state(false)
  // Starts at 'medium' (index 1): the order the control cycles in is
  // small/medium/large either way, and medium is the size every other view
  // already renders at (app.css's 16px body font), so opening the Log for
  // the first time looks the same as it always has, before anyone has
  // touched the control.
  let fontSizeIndex = $state(1)

  const conn = $derived($connection)
  const fetched = $derived(hasListDataArrived(conn.state))
  const unavailable = $derived($logUnavailable)

  const filteredLines = $derived(filterLogLines($log, filterText))

  const emptyText = $derived(formatLogEmptyMessage(fetched, unavailable))
  const followLabel = $derived(formatFollowLabel(following))
  // SPEC 4.5.2.5: "data-font-size on the view root carries the current one"
  // -- the styling below reads the same attribute a test would, rather than
  // the view root's markup and its own CSS inferring the size from two
  // different facts.
  const fontSize = $derived(FONT_SIZES[fontSizeIndex])

  // SPEC 4.5.2.5: the shared three-occasion trigger (lib/viewRefresh.ts,
  // reasoned in full at SPEC 4.5.2.3) -- this view becoming current, its own
  // refresh control, and the active host changing under it while it stays
  // current -- and the follow timer, the one exception to "no timer" that
  // section argues for, both drive lib/stores.ts's refreshLog, which is the
  // one place that decides what "ask for the log" means.
  const logRefresh = watchViewRefresh('log', refreshLog)
  const logFollow = watchViewFollow('log', FOLLOW_INTERVAL_MS, refreshLog)

  function requestRefresh(): void {
    logRefresh.refreshNow()
  }

  function toggleFollow(): void {
    following = !following
    logFollow.setFollowing(following)
  }

  function cycleFontSize(): void {
    fontSizeIndex = (fontSizeIndex + 1) % FONT_SIZES.length
  }

  // SPEC 4.5.2.5: "scrolling the container to its end after every buffer
  // change, and only while following" -- following and scrolling are one
  // control, and this is the half that was missing. `$log`, the raw buffer
  // (§4.4.1 replaces it whole rather than appending), is what triggers
  // this, not `filteredLines`: the filter is a view-side concern, and
  // scrolling on every keystroke there would fight an owner scrolling back
  // through what the filter just found, which is the section's own
  // reason not to scroll "on every render". `following` is read through
  // `untrack` so toggling it does not itself scroll -- only a subsequent
  // buffer change does, which is also what carries the interval's
  // immediate ask (lib/viewRefresh.ts's watchViewFollow) to the screen as
  // a scroll rather than the tap that armed it.
  $effect(() => {
    void $log
    const container = logLinesEl
    if (container && untrack(() => following)) {
      container.scrollTop = container.scrollHeight
    }
  })
</script>

<section
  class="view log-view"
  data-view="log"
  data-font-size={fontSize}
  aria-labelledby="view-title-log"
>
  <div class="header-row">
    <h1 id="view-title-log">Log</h1>
    <button type="button" data-action="refresh" onclick={requestRefresh}>
      {REFRESH_LABEL}
    </button>
  </div>

  <div class="controls-row">
    <button
      type="button"
      data-action="follow"
      aria-pressed={following}
      onclick={toggleFollow}
    >
      {followLabel}
    </button>
    <button type="button" data-action="font-size" onclick={cycleFontSize}>
      {LOG_FONT_SIZE_LABEL}
    </button>
  </div>

  <!-- SPEC 4.5.2.5: filters what is on the screen, never the request -- see
       lib/log.ts. Labelled with a `<label for>`, not a `placeholder`: a
       placeholder is not an accessible name, and every other input in this
       app is labelled the same way (Settings.svelte's host and add-host
       forms). -->
  <label class="filter-row" for="log-filter">
    {LOG_FILTER_LABEL}
    <input
      id="log-filter"
      class="filter-input"
      type="text"
      data-log-filter
      bind:value={filterText}
    />
  </label>

  <!-- SPEC 4.5.2.5: the container is always present, empty buffer or not --
       it is the box the geometry rule is about, and issue #38's own
       criteria require measuring it both empty and full, which a container
       that only exists once lines arrive cannot be. data-empty-message
       stays absent whenever a line is rendered, unchanged. -->
  <div class="log-lines" data-log-lines bind:this={logLinesEl}>
    {#if filteredLines.length === 0}
      <!-- SPEC 4.5.2.5's copy table: which of the three sentences this is
           depends first on whether there are lines at all (lib/format.ts's
           formatLogEmptyMessage) and only once there are some on whether
           the filter excluded every one of them. -->
      <p class="empty-message" data-empty-message role="status">
        {$log.length === 0 ? emptyText : LOG_NO_FILTER_MATCH_MESSAGE}
      </p>
    {:else}
      <!-- SPEC 4.5.3: every line is a string a stranger chose, with a
           timestamp in front of it -- rendered as text, like everything
           else, and never split to highlight a filter match, which would
           mean building markup out of a hostile string. The list index is
           the {#each} key: a log line has no identity of its own and two
           units can legitimately log the same line twice. -->
      {#each filteredLines as line, i (i)}
        <div class="log-line" data-log-line>{line}</div>
      {/each}
    {/if}
  </div>
</section>

<style>
  /* SPEC 4.5.2.5's Geometry paragraph carries the reasoning in full: a
     percentage height, not an inset, so this resolves against main's
     content box rather than its padding box (issue #38). `height: 100%`
     needs app.css's `[data-view-slot='log']` to give the slot around this
     section a height to resolve against -- that file's own comment covers
     why -- but the flex column is only declared here, the one stylesheet
     that can already reach this exact element. */
  .log-view {
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

  .controls-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  /* app.css sets a button's size and font but not its colours: a button
     with nothing else applied paints WebKit's platform default under
     whatever foreground this view inherits, light text on light grey,
     invisible in the dark palette on that engine specifically (the same
     note WiFi.svelte, Peers.svelte, Dashboard.svelte and Settings.svelte
     carry). Every surface here is var(--bg), so an explicit background and
     colour are what stop the platform default from ever being reached. */
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

  /* A toggle names what it offers, and `aria-pressed` carries whether it is
     on; this is the sighted equivalent of that. A precedent in this codebase,
     set by the Log's follow control and the Mirror's auto-refresh control, and
     not a rule 4.5.1 states: this comment cited 4.5.1 for it in three files
     and the sentence is not there. SPEC 4.6.2 caught the same fabrication in
     its own prose and says where the convention actually comes from. */
  button[data-action='follow'][aria-pressed='true'] {
    border-color: var(--accent);
    color: var(--accent);
  }

  /* The label is `<label class="filter-row" for="log-filter">`, wrapping
     its own text and the input -- the same shape Settings.svelte's host and
     add-host fields already use. Row rather than stacked, to match this
     view's other single-line controls. */
  .filter-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .filter-input {
    flex: 1;
    min-height: 44px;
    padding: 0 0.75rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
  }

  .filter-input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }

  .empty-message {
    margin: 0;
    color: var(--text-dim);
  }

  /* SPEC 4.5.2.5: always in the DOM, empty buffer or not (see the comment
     beside the markup above) -- it is what claims the leftover space in
     the flex column and scrolls on its own, the box issue #38's criteria
     ask to be measured both empty and full. */
  .log-lines {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }

  .log-line {
    padding: 0.125rem 0;
    white-space: pre-wrap;
    word-break: break-word;
    border-bottom: 1px solid var(--border);
  }

  .log-line:last-child {
    border-bottom: none;
  }

  /* SPEC 4.5.2.5: "data-font-size on the view root carries the current
     one" -- the styling reads that same attribute rather than a value kept
     nowhere else, so the fact a test asserts and the fact the page renders
     from are the same fact. */
  .log-view[data-font-size='small'] .log-lines {
    font-size: 0.8125rem;
  }

  .log-view[data-font-size='medium'] .log-lines {
    font-size: 1rem;
  }

  .log-view[data-font-size='large'] .log-lines {
    font-size: 1.25rem;
  }
</style>
