<script lang="ts">
  // SPEC 4.5.1.1. Every rule that turns a value into a string lives in
  // lib/format.ts; this view only subscribes to the stores (lib/stores.ts)
  // and lays out what those functions return. No client, no socket, no
  // fetch: the adapter that writes these stores lives elsewhere (SPEC 4.8).
  import {
    CANCEL_LABEL,
    DASH,
    EMPTY_LABEL,
    NEVER_REFRESHED,
    formatAge,
    formatBatteryPercent,
    formatChannel,
    formatCharging,
    formatGpsFix,
    formatGpsSource,
    formatMode,
    formatRestartReason,
    formatTemperature,
    formatUnauthorizedCallToAction,
    formatUnauthorizedReason,
    formatUnitTime,
    formatUptime,
    type ControlId,
  } from '../lib/format'
  import {
    cancelControl,
    confirmControl,
    controls,
    requestControl,
  } from '../lib/controls'
  import { channel, connection, face, gps, stats } from '../lib/stores'

  const s = $derived($stats)
  const ch = $derived($channel)
  const gpsReading = $derived($gps)
  const faceStatus = $derived($face)
  const conn = $derived($connection)

  // SPEC 4.5.2.2: lib/controls.ts owns every rule about these four -- which
  // mode the switch offers, whether PASV is disabled, what is pending and
  // what a control currently says. This view lays out $controls in the
  // order the section fixes: mode, PASV, reboot, shutdown.
  const controlsView = $derived($controls)
  const CONTROL_IDS: ControlId[] = ['mode', 'pasv', 'reboot', 'shutdown']

  // SPEC 4.5.2.2: "focus moves to Cancel, not to Confirm" on arming -- the
  // button that was tapped no longer exists once a control arms, so focus
  // has to go somewhere, and landing it on Confirm would defeat the
  // two-step in exactly the case the two-step is for.
  //
  // A Svelte action, not a component-level ref. An action runs exactly
  // once, on its own element when that element is created, so there is no
  // shared slot for a second control's mount or unmount to write to -- the
  // ordering question this replaces cannot arise, rather than having been
  // shown to arise and then closed.
  //
  // The version it replaced -- one $state slot shared by all four
  // controls' Cancel buttons, written through bind:this, read by a single
  // $effect -- was not observed to fail. It was suspected on a reading of
  // Svelte 5's flush order: arming a lower-indexed control while a
  // higher-indexed one is armed changes both branches in the same
  // reactive flush, and if each-block reconciliation applies the new
  // control's bind:this write before the old control's teardown (which
  // nulls the slot on unmount), the teardown would clobber the write and
  // the effect would find nothing to focus. A test written to catch that
  // in both directions passed against the shared-slot version, so the
  // suspicion did not reproduce. What that does and does not show is
  // narrower than it looks: jsdom is the only place the armed state is
  // reachable today -- the e2e suite cannot reach it at all (issue #185)
  // -- so a real browser has never been asked, and "passed in jsdom" is
  // not "the hazard does not exist there". The action is kept because it
  // removes the question rather than because the question was answered.
  function focusOnMount(node: HTMLButtonElement): void {
    node.focus()
  }

  interface FieldRow {
    id: string
    label: string
    value: string
    empty: boolean
  }

  // SPEC 4.5.3 (issue #219): lastHandshake and lastPeer render a name next
  // to a time the client itself formatted (formatUnitTime), not two halves
  // of one remote string. Isolating the whole composed sentence in a single
  // <bdi> would still let a bidi override inside the name reorder the "at
  // <time>" that follows it, inside that same isolate -- the exact failure
  // the issue names. So the name is carried on its own here and only it is
  // wrapped, in the markup below, leaving the local "at" and the time
  // outside the isolate.
  interface NamedEventRow {
    id: string
    label: string
    name: string | null
    hasName: boolean
    time: string
    hasTime: boolean
    empty: boolean
  }

  // SPEC 4.5.1.1: on every field below except face, status, lastHandshake,
  // lastPeer and gpsSource, emptiness is decided by comparing the
  // formatter's own answer against DASH -- the value is a number (or an
  // enum with no attacker-chosen member) and no number formats to a dash
  // except by the rule that says it is unknown, so the formatted string
  // and the underlying value can never disagree. Re-deriving it from
  // `=== null` in this file would duplicate the rule and, on a non-finite
  // reading, disagree with it: format.ts already decided DASH, and a
  // second `=== null` here would let that dash through with no
  // data-empty and no accessible unavailable beside it.

  // The badge, not a field row (SPEC 4.5.1.1/4.5.2): the mode is the one
  // field the two-step controls of 4.5.2 change, so it is the field the eye
  // should find without reading a column. The data-field hook still lands
  // on the value.
  const modeValue = $derived(s === null ? DASH : formatMode(s.mode))
  const modeEmpty = $derived(modeValue === DASH)

  const uptimeValue = $derived(s === null ? DASH : formatUptime(s.uptime))
  const uptimeEmpty = $derived(uptimeValue === DASH)

  // SPEC 4.5.1.1: the battery is two rows, not one, so that
  // {percent: null, charging: true} still reports the charging state
  // instead of discarding it, and {percent: null, charging: false} does
  // not render as unavailable when the unit did in fact say something.
  const batteryPercentValue = $derived(
    s === null ? DASH : formatBatteryPercent(s.battery.percent),
  )
  const batteryPercentEmpty = $derived(batteryPercentValue === DASH)
  const chargingValue = $derived(
    s === null ? DASH : formatCharging(s.battery.charging),
  )
  const chargingEmpty = $derived(chargingValue === DASH)

  const temperatureValue = $derived(
    s === null ? DASH : formatTemperature(s.temperature),
  )
  const temperatureEmpty = $derived(temperatureValue === DASH)

  // SPEC 4.4.1/4.5.1.1: the channel is the first of the two exceptions to
  // the one-snapshot rule -- it comes from the channel store, which is the
  // later of channel_hop and stats.channel, so a hop is visible before the
  // next broadcast rather than waiting up to 20 s for it.
  const channelValue = $derived(formatChannel(ch))
  const channelEmpty = $derived(channelValue === DASH)

  const accessPointsValue = $derived(s === null ? DASH : `${s.accessPoints}`)
  const accessPointsEmpty = $derived(accessPointsValue === DASH)
  // SPEC 4.5.1.1/2.5: the authoritative count is handshakesTotal, not the
  // narrower *.pcapng count the unit's own display uses. handshakesTotal is
  // null when the plugin does not know where the captures are, which
  // dashes the counter exactly as a missing `stats` does.
  const handshakesValue = $derived(
    s === null || s.handshakesTotal === null ? DASH : `${s.handshakesTotal}`,
  )
  const handshakesEmpty = $derived(handshakesValue === DASH)
  const peersValue = $derived(s === null ? DASH : `${s.peers}`)
  const peersEmpty = $derived(peersValue === DASH)
  // SPEC 4.5.1.1/2.5: a second line, shown only when both handshake counts
  // are known and disagree, naming what it is rather than leaving the
  // reader to notice the mismatch against the Wi-Fi view's Captured badge
  // on their own. With a null on either side there is no disagreement to
  // explain, so the row is not rendered at all rather than rendered as a
  // dash. It is never empty: it is rendered only when stats.handshakes is
  // a known, disagreeing count.
  const showHandshakesOnUnit = $derived(
    s !== null &&
      s.handshakes !== null &&
      s.handshakesTotal !== null &&
      s.handshakes !== s.handshakesTotal,
  )
  // showHandshakesOnUnit already requires s to be non-null and
  // s.handshakes to be non-null; the assertion documents that guarantee
  // rather than re-testing it in an unreachable `s === null` arm. The DASH
  // side of the ternary is never rendered -- this row only appears behind
  // {#if showHandshakesOnUnit} below -- and exists only because the
  // expression needs some value on that branch.
  const handshakesOnUnitValue = $derived(
    showHandshakesOnUnit ? `${s!.handshakes}` : DASH,
  )

  // SPEC 4.5.1.1: named, not merely timed -- the ssid and the peer name are
  // the two hostile strings on this card (SPEC 4.5.3), next to face and
  // status. The name passes through unchanged rather than being dropped in
  // favour of a bare, safer-looking time of day; formatUnitTime formats the
  // time half, the one piece of this row the client itself produced.
  //
  // These two, like face, status and gpsSource below, decide emptiness
  // from the value behind them rather than from the formatted string
  // (SPEC 4.5.1.1): a dash is a legal SSID and a legal peer name, so
  // comparing text would let a remote party decide that a real reading
  // renders as unavailable. The row is empty when the whole object is
  // null, and also when the object carries neither half -- an empty name
  // and no timestamp.
  const lastHandshakeName = $derived(s?.lastHandshake?.ssid ?? null)
  const lastHandshakeHasName = $derived(
    lastHandshakeName !== null && lastHandshakeName !== '',
  )
  const lastHandshakeTimeText = $derived(
    formatUnitTime(s?.lastHandshake?.mtime ?? null),
  )
  const lastHandshakeHasTime = $derived(lastHandshakeTimeText !== DASH)
  const lastHandshakeEmpty = $derived(
    s === null ||
      s.lastHandshake === null ||
      (s.lastHandshake.ssid === '' && !Number.isFinite(s.lastHandshake.mtime)),
  )
  const lastPeerName = $derived(s?.lastPeer?.name ?? null)
  const lastPeerHasName = $derived(lastPeerName !== null && lastPeerName !== '')
  const lastPeerTimeText = $derived(
    formatUnitTime(s?.lastPeer?.lastSeen ?? null),
  )
  const lastPeerHasTime = $derived(lastPeerTimeText !== DASH)
  const lastPeerEmpty = $derived(
    s === null ||
      s.lastPeer === null ||
      (s.lastPeer.name === '' &&
        (s.lastPeer.lastSeen === null ||
          !Number.isFinite(s.lastPeer.lastSeen))),
  )

  // SPEC 4.4.1/4.5.1.1: the second and last exception to the one-snapshot
  // rule. `gps` (lib/stores.ts) is the later of stats.gps and a gps_update
  // push, so reading stats.gps here would leave a fix acquired between two
  // broadcasts invisible for up to 20 s while a channel hop appeared at
  // once -- the asymmetry the amended section exists to remove.
  const gpsFixValue = $derived(
    gpsReading === null ? DASH : formatGpsFix(gpsReading),
  )
  // `off` is a known answer, not a missing one (SPEC 4.5.1.1), and
  // formatGpsFix never itself returns DASH, so comparing its answer to
  // DASH only fires before any gps reading has arrived at all.
  const gpsFixEmpty = $derived(gpsFixValue === DASH)
  const gpsSourceValue = $derived(
    gpsReading === null ? DASH : formatGpsSource(gpsReading),
  )
  // gpsSource decides emptiness from the value, not the string (see the
  // comment above lastHandshakeEmpty): a null or empty source and a
  // formatted DASH happen to coincide here, but the rule is which one this
  // view asks. SPEC 4.5.1.1: a remote string is empty when it is null or ''.
  // The `''` arm is unreachable from a conformant payload but not
  // validated at the boundary (see the comment on formatGpsSource, issue
  // #109), and cast to string for the same reason.
  const gpsSourceEmpty = $derived(
    gpsReading === null ||
      gpsReading.source === null ||
      (gpsReading.source as string) === '',
  )

  // SPEC 4.5.1.1: a remote string is empty when it is null or '', so face
  // and status each decide emptiness from their own string rather than
  // sharing a single flag that only asked whether the object arrived.
  const faceValue = $derived(
    faceStatus === null || faceStatus.face === '' ? DASH : faceStatus.face,
  )
  const faceEmpty = $derived(faceStatus === null || faceStatus.face === '')
  const statusValue = $derived(
    faceStatus === null || faceStatus.status === '' ? DASH : faceStatus.status,
  )
  const statusEmpty = $derived(faceStatus === null || faceStatus.status === '')

  const primaryFields = $derived.by((): FieldRow[] => [
    { id: 'uptime', label: 'Uptime', value: uptimeValue, empty: uptimeEmpty },
    {
      id: 'battery',
      label: 'Battery',
      value: batteryPercentValue,
      empty: batteryPercentEmpty,
    },
    {
      id: 'charging',
      label: 'Charging',
      value: chargingValue,
      empty: chargingEmpty,
    },
    {
      id: 'temperature',
      label: 'Temperature',
      value: temperatureValue,
      empty: temperatureEmpty,
    },
    {
      id: 'channel',
      label: 'Channel',
      value: channelValue,
      empty: channelEmpty,
    },
  ])

  const counterFields = $derived.by((): FieldRow[] => [
    {
      id: 'accessPoints',
      label: 'Access points',
      value: accessPointsValue,
      empty: accessPointsEmpty,
    },
    {
      id: 'handshakes',
      label: 'Handshakes',
      value: handshakesValue,
      empty: handshakesEmpty,
    },
    { id: 'peers', label: 'Peers', value: peersValue, empty: peersEmpty },
  ])

  // SPEC 4.5.3 (issue #219): rendered by the namedEventField snippet below,
  // not by field/counterFields, because only the name half of each is
  // wrapped in <bdi>.
  const namedEventFields = $derived.by((): NamedEventRow[] => [
    {
      id: 'lastHandshake',
      label: 'Last handshake',
      name: lastHandshakeName,
      hasName: lastHandshakeHasName,
      time: lastHandshakeTimeText,
      hasTime: lastHandshakeHasTime,
      empty: lastHandshakeEmpty,
    },
    {
      id: 'lastPeer',
      label: 'Last peer',
      name: lastPeerName,
      hasName: lastPeerHasName,
      time: lastPeerTimeText,
      hasTime: lastPeerHasTime,
      empty: lastPeerEmpty,
    },
  ])

  // SPEC 4.5.1.1: gpsSource and gpsFix are two fields, not one -- which
  // provider resolved the position, and whether there is currently a
  // position at all.
  //
  // SPEC 4.5.3: gpsSource is rendered on its own with remoteField rather
  // than folded into this array, because formatGpsSource's own docstring
  // pins it as a remote value ("a compile-time claim about a remote
  // payload, not a fact about one") -- the same reasoning face and status
  // are rendered with remoteField for. gpsFix never echoes anything the
  // unit sent; formatGpsFix returns one of its own literal strings, so it
  // stays in this array and is rendered plain.
  const gpsFields = $derived.by((): FieldRow[] => [
    { id: 'gpsFix', label: 'GPS fix', value: gpsFixValue, empty: gpsFixEmpty },
  ])

  // SPEC 4.5.1.1: the banner is one element that always exists and carries
  // the state, with copy only where the state has something to say.
  // `connecting` and `connected` are §4.3.1's silent rows, not an omission
  // here, and every other sentence is pinned verbatim: a paraphrase that
  // merges the two `degraded` sentences, or swaps the two `unauthorized`
  // ones, is a wrong instruction to a user in a field and a test can only
  // catch it by asserting equality against wording that has to live
  // somewhere, which is why it lives here rather than in the component.
  const bannerText = $derived.by((): string => {
    switch (conn.state) {
      case 'connecting':
      case 'connected':
        return ''
      case 'degraded': {
        // Which sentence to use is decided by what formatAge returned, not
        // by testing sessionAge for null (SPEC 4.5.1.1): formatAge answers
        // NEVER_REFRESHED for a non-finite sessionAge too, and a
        // `=== null` guard here would let that value back into the first
        // sentence and reconstruct "the data is never refreshed old."
        const age = formatAge(s === null ? null : s.sessionAge)
        return age === NEVER_REFRESHED
          ? 'Connected, and the data has never refreshed.'
          : `Connected, and the data is ${age} old.`
      }
      case 'offline':
        return 'Not connected. Reconnecting automatically. Check that the Personal Hotspot is on.'
      case 'unauthorized': {
        // SPEC 4.5.2.1: this sentence and its call to action are one
        // source, shared with Settings' diagnostic -- lib/format.ts, not a
        // second typing of either half. `=== 'rejected'` picks 'required'
        // for anything else, same as before this composed the two: the
        // reason is never null while conn.state is 'unauthorized'
        // (SPEC 4.3.1), but the type admits it and there is no third
        // sentence to fall back to.
        const reason =
          conn.unauthorizedReason === 'rejected' ? 'rejected' : 'required'
        return `${formatUnauthorizedReason(reason)} ${formatUnauthorizedCallToAction(reason)}`
      }
      case 'restarting':
        // SPEC 4.4.1/4.5.1.1 (issue #131): `connection` (lib/stores.ts)
        // mirrors `restartReason` alongside the state, null in every state
        // but this one (SPEC 4.3.1 never leaves it null here), so the
        // distinct copy for mode_change/reboot and for shutdown lives in
        // one place, formatRestartReason, rather than being retyped here.
        return conn.restartReason === null
          ? ''
          : formatRestartReason(conn.restartReason)
    }
  })
  const bannerHasCopy = $derived(bannerText !== '')
</script>

{#snippet field(row: FieldRow)}
  <div class="field">
    <span class="field-label">{row.label}</span>
    <span
      class="field-value"
      data-field={row.id}
      data-empty={row.empty ? 'true' : undefined}
    >
      {row.value}{#if row.empty}<span class="visually-hidden">
          {EMPTY_LABEL}</span
        >{/if}
    </span>
  </div>
{/snippet}

<!-- SPEC 4.5.3 (issue #219): the whole value is a remote string (face,
     status), so the whole value is isolated -- unlike namedEventField
     below, there is no local text sharing this element for an override to
     reach. -->
{#snippet remoteField(row: FieldRow)}
  <div class="field">
    <span class="field-label">{row.label}</span>
    <span
      class="field-value"
      data-field={row.id}
      data-empty={row.empty ? 'true' : undefined}
      ><bdi>{row.value}</bdi>{#if row.empty}<span class="visually-hidden">
          {EMPTY_LABEL}</span
        >{/if}</span
    >
  </div>
{/snippet}

<!-- SPEC 4.5.3 (issue #219): only the name is remote; the "at" and the
     time are this client's own text and stay outside the <bdi>, so a bidi
     override carried in the name cannot reorder them. -->
{#snippet namedEventField(row: NamedEventRow)}
  <div class="field">
    <span class="field-label">{row.label}</span>
    <span
      class="field-value"
      data-field={row.id}
      data-empty={row.empty ? 'true' : undefined}
      >{#if row.hasName}<bdi>{row.name}</bdi
        >{#if row.hasTime}{' at '}{row.time}{/if}{:else}{row.time}{/if}{#if row.empty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}</span
    >
  </div>
{/snippet}

<!-- SPEC 4.5.2.2: the confirm is inline, not a dialog -- tapping a control
     replaces it with its own confirm/cancel pair in the space the control
     occupied, so the question stays next to the thing that asked it. -->
{#snippet control(id: ControlId)}
  {@const c = controlsView[id]}
  {#if c.visible}
    <div class="control" data-control={id} data-control-state={c.state}>
      {#if c.state === 'armed'}
        <div class="control-actions">
          <button
            type="button"
            data-action="confirm"
            onclick={() => confirmControl(id)}
          >
            {c.confirmLabel}
          </button>
          <button
            type="button"
            data-action="cancel"
            use:focusOnMount
            onclick={cancelControl}
          >
            {CANCEL_LABEL}
          </button>
        </div>
        <!-- SPEC 4.5.2.2: the consequence has no hook of its own -- it is
             this same data-control-message element, which data-control-state
             already says is carrying it. A second hook would exist only so
             a test could ask the question the state attribute answers. -->
        <p class="control-message" data-control-message role="alert">
          {c.consequence}
        </p>
      {:else}
        <button
          type="button"
          data-action="request"
          disabled={c.disabled}
          onclick={() => requestControl(id)}
        >
          {c.requestLabel}
        </button>
        {#if c.message !== null}
          <p class="control-message" data-control-message role="alert">
            {c.message}
          </p>
        {/if}
      {/if}
    </div>
  {/if}
{/snippet}

<section
  class="view"
  data-view="dashboard"
  aria-labelledby="view-title-dashboard"
>
  <h1 id="view-title-dashboard">Dashboard</h1>

  <!-- Live region so a state change (SPEC 4.3.1) is announced on its own,
       without the reader having to revisit the banner to notice it changed.
       Always present, per SPEC 4.5.1.1, even where the state has no copy;
       class:banner-quiet, not a CSS :empty selector, collapses the padding
       in that case -- the element wraps {bannerText} across a newline, so
       Svelte emits a whitespace text node and :empty never matches. -->
  <p
    class="banner"
    class:banner-quiet={!bannerHasCopy}
    data-banner={conn.state}
    role="status"
    aria-live="polite"
  >
    {bannerText}
  </p>

  <div class="identity">
    <span
      class="mode-badge"
      data-field="mode"
      data-empty={modeEmpty ? 'true' : undefined}
    >
      {modeValue}{#if modeEmpty}<span class="visually-hidden">
          {EMPTY_LABEL}</span
        >{/if}
    </span>

    <div class="face-card">
      {@render remoteField({
        id: 'face',
        label: 'Face',
        value: faceValue,
        empty: faceEmpty,
      })}
      {@render remoteField({
        id: 'status',
        label: 'Status',
        value: statusValue,
        empty: statusEmpty,
      })}
    </div>
  </div>

  <div class="fields">
    {#each primaryFields as row (row.id)}
      {@render field(row)}
    {/each}
  </div>

  <div class="fields">
    {#each counterFields as row (row.id)}
      {@render field(row)}
    {/each}
    {#each namedEventFields as row (row.id)}
      {@render namedEventField(row)}
    {/each}
    {#if showHandshakesOnUnit}
      {@render field({
        id: 'handshakesOnUnit',
        label: "On the unit's own display",
        value: handshakesOnUnitValue,
        empty: false,
      })}
    {/if}
  </div>

  <div class="fields">
    {@render remoteField({
      id: 'gpsSource',
      label: 'GPS source',
      value: gpsSourceValue,
      empty: gpsSourceEmpty,
    })}
    {#each gpsFields as row (row.id)}
      {@render field(row)}
    {/each}
  </div>

  <!-- SPEC 4.5.2.2: the controls sit at the end of the Dashboard, under the
       readings -- the card answers "is my unit all right", the controls are
       what an owner reaches for after reading the answer. -->
  <div class="controls">
    {#each CONTROL_IDS as id (id)}
      {@render control(id)}
    {/each}
  </div>
</section>

<style>
  .banner {
    margin: 0 0 1rem;
    padding: 0.75rem 1rem;
    border-radius: 8px;
    background: var(--surface);
    border: 1px solid var(--border);
  }

  /* Silent in `connecting` and `connected` (SPEC 4.3.1/4.5.1.1): the element
     still exists and still carries data-banner, but an empty box left at
     full padding on the two most common states is not worth the space. */
  .banner.banner-quiet {
    padding: 0;
    margin: 0;
    border: 0;
  }

  .identity {
    display: flex;
    align-items: flex-start;
    gap: 0.75rem;
    margin-bottom: 1rem;
  }

  .mode-badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 0 0.75rem;
    border-radius: 999px;
    background: var(--accent);
    color: var(--bg);
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .face-card {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    padding: 1rem;
    border-radius: 8px;
    background: var(--surface);
    border: 1px solid var(--border);
  }

  .face-card .field-value {
    font-size: 1.5rem;
  }

  .fields {
    display: flex;
    flex-direction: column;
    margin-bottom: 1rem;
  }

  .field {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.5rem 0;
  }

  .field + .field {
    border-top: 1px solid var(--border);
  }

  .field-label {
    color: var(--text-dim);
  }

  .field-value {
    font-variant-numeric: tabular-nums;
    text-align: right;
  }

  /* SPEC 4.5.2.2: the controls sit at the end of the card, under the
     readings. SPEC 4.5.1: adjacent controls carry a gap, not just a floor
     on their own size -- two 44px targets flush against each other are easy
     to hit wrongly, which matters most where the neighbour restarts or
     powers off the unit. */
  .controls {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  .control {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .control-actions {
    display: flex;
    gap: 0.75rem;
  }

  /* SPEC 4.5.2.2: one data-control-message element carries every sentence a
     control has to show -- the disabled reason, a failure, an abandonment,
     the pending sentence, and (while armed) the consequence -- so this rule
     is neutral rather than styled for the failure case alone. */
  .control-message {
    margin: 0;
    color: var(--text-dim);
    font-size: 0.875rem;
  }

  /* app.css sets a button's size and font but not its colours (see the same
     note in Settings.svelte): a button with nothing else applied paints
     WebKit's platform default under whatever foreground this view
     inherits, light text on light grey, invisible in the dark palette on
     that engine specifically. Every surface a control sits on is
     var(--bg), so an explicit background and colour are what stop the
     platform default from ever being reached. */
  .control button {
    min-height: 44px;
    padding: 0 1rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
  }

  .control button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }

  .control [data-action='confirm'] {
    border-color: var(--danger);
    color: var(--danger);
  }

  /* Present in the DOM and read by assistive tech, invisible otherwise. A
     dash is not itself readable aloud, so this is what carries EMPTY_LABEL
     alongside it (SPEC 4.5.1.1). */
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
