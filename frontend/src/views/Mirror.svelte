<script lang="ts">
  // SPEC 4.5.2.6. Every rule that turns a value into a string lives in
  // lib/format.ts and the base64 shape check lives in lib/screen.ts, the same
  // split lib/log.ts already draws for the Log view (SPEC 4.5.2.5). This view
  // subscribes to the screen store (lib/stores.ts) and lays out what those
  // functions return; it does not itself decide which sentence a state gets
  // or whether a `png` is safe to place in a `src`.
  import {
    DASH,
    EMPTY_LABEL,
    formatMirrorAutoLabel,
    formatMirrorEmptyMessage,
    formatUnitTime,
    MIRROR_FRAME_LABEL,
    MIRROR_UNREADABLE_MESSAGE,
    REFRESH_LABEL,
  } from '../lib/format'
  import { hasListDataArrived } from '../lib/lists'
  import { screenFrameSrc } from '../lib/screen'
  import { connection, refreshScreen, screen } from '../lib/stores'
  import { watchViewFollow, watchViewRefresh } from '../lib/viewRefresh'

  // SPEC 4.5.2.6: "the interval is five seconds as already written, and the
  // first hardware run is expected to revisit it" -- the same number
  // §4.5.2 and §2.10 already carry, not a new one, and not a measurement:
  // nobody has watched a frame arrive over BT PAN. See that section's own
  // paragraph, and lib/viewRefresh.ts's watchViewFollow, for the mechanism
  // this reuses rather than a second one built for this screen.
  const AUTO_INTERVAL_MS = 5_000

  // SPEC 4.5.2.6: "opt-in, off on arrival" -- the Log's own terms
  // (§4.5.2.5), granted here rather than assumed. Not reset by navigation:
  // §4.5 keeps this view mounted, so leaving Mirror and coming back reads
  // whatever the owner last left this as.
  let auto = $state(false)

  const conn = $derived($connection)
  const fetched = $derived(hasListDataArrived(conn.state))
  const frame = $derived($screen)

  // SPEC 4.5.2.6: "the shape is checked before the string becomes a URL, in
  // lib/" -- lib/screen.ts's own job, not this view's. `null` here is a
  // frame held but not safe to render, told apart from no frame held at all
  // by `frame` itself below.
  const frameSrc = $derived(frame === null ? null : screenFrameSrc(frame.png))

  const emptyText = $derived(
    frame === null ? formatMirrorEmptyMessage(fetched) : MIRROR_UNREADABLE_MESSAGE,
  )

  // SPEC 4.5.2.6's DOM hooks: "the field is present whether or not a frame
  // is held, dashed when it is not" -- formatUnitTime(null) is DASH, so the
  // row below renders unconditionally and this is the only branching its
  // value needs. Held but unreadable still renders a real time: the
  // timestamp is a fact about the reply that arrived, not about whether this
  // app could render its picture.
  const frameTimeText = $derived(formatUnitTime(frame === null ? null : frame.mtime))
  const frameTimeEmpty = $derived(frameTimeText === DASH)

  const autoLabel = $derived(formatMirrorAutoLabel(auto))

  // SPEC 4.5.2.6: the shared three-occasion trigger (lib/viewRefresh.ts,
  // reasoned in full at SPEC 4.5.2.3) -- this view becoming current, its own
  // refresh control, and the active host changing under it while it stays
  // current -- and the auto timer, the second exception §4.5.2.5 argues for
  // and this section grants on the same terms, both drive lib/stores.ts's
  // refreshScreen, the one place that decides what "ask for the frame"
  // means.
  const screenRefresh = watchViewRefresh('mirror', refreshScreen)
  const screenFollow = watchViewFollow('mirror', AUTO_INTERVAL_MS, refreshScreen)

  function requestRefresh(): void {
    screenRefresh.refreshNow()
  }

  function toggleAuto(): void {
    auto = !auto
    screenFollow.setFollowing(auto)
  }
</script>

<section class="view" data-view="mirror" aria-labelledby="view-title-mirror">
  <div class="header-row">
    <h1 id="view-title-mirror">Mirror</h1>
    <button type="button" data-action="refresh" onclick={requestRefresh}>
      {REFRESH_LABEL}
    </button>
  </div>

  <div class="controls-row">
    <button type="button" data-action="auto" aria-pressed={auto} onclick={toggleAuto}>
      {autoLabel}
    </button>
  </div>

  <!-- SPEC 4.5.2.6's DOM hooks: "the field is present whether or not a
       frame is held, dashed when it is not" -- outside the {#if} below on
       purpose, so a screen with no frame yet changes value, not shape. See
       the comment beside frameTimeText above. -->
  <p class="frame-time" data-field="frameTime" data-empty={frameTimeEmpty ? 'true' : undefined}>
    {frameTimeText}{#if frameTimeEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
  </p>

  {#if frameSrc !== null}
    <!-- SPEC 4.5.2.6: data:, never blob: (§2.15.1's img-src carries data: and
         deliberately not blob:). The accessible name says what the image is,
         not what it shows: the face and status are on the wire separately,
         in face_status, and reading them out of the picture is not
         something this client does. -->
    <img class="frame" data-screen-frame src={frameSrc} alt={MIRROR_FRAME_LABEL} />
  {:else}
    <p class="empty-message" data-empty-message role="status">{emptyText}</p>
  {/if}
</section>

<style>
  .header-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  h1 {
    margin: 0;
  }

  .controls-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  /* app.css sets a button's size and font but not its colours: a button
     with nothing else applied paints WebKit's platform default under
     whatever foreground this view inherits, light text on light grey,
     invisible in the dark palette on that engine specifically (the same
     note WiFi.svelte, Peers.svelte, Log.svelte and Settings.svelte carry).
     Every surface here is var(--bg), so an explicit background and colour
     are what stop the platform default from ever being reached. */
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

  /* SPEC 4.5.1: a toggle says what it is, not what it does -- aria-pressed
     carries the state for assistive tech; this is the sighted equivalent,
     the same convention Log.svelte's follow control and Segmented.svelte's
     aria-selected already get a visual match for. */
  button[data-action='auto'][aria-pressed='true'] {
    border-color: var(--accent);
    color: var(--accent);
  }

  .frame-time {
    margin: 0 0 0.75rem;
    color: var(--text-dim);
  }

  /* SPEC 4.5.1's geometry rule, applied here rather than restated: the
     picture must not overflow its area in either orientation (issue #38 is
     what that looks like when it goes wrong). `main` already scrolls (SPEC
     4.5.1's bottom-bar rule), so there is no fixed height to fill the way
     Log.svelte's box does -- only a width to stay inside, which the view's
     own reading measure (app.css's `.view`) already bounds. */
  .frame {
    display: block;
    max-width: 100%;
    height: auto;
    border: 1px solid var(--border);
    border-radius: 6px;
  }

  .empty-message {
    margin: 0;
    color: var(--text-dim);
  }

  /* Present in the DOM and read by assistive tech, invisible otherwise
     (SPEC 4.5.1.1's rule, applied here the same way WiFi.svelte,
     Peers.svelte, Log.svelte and Dashboard.svelte already do). */
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
