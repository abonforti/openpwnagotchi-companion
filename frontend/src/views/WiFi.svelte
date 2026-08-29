<script lang="ts">
  // SPEC 4.5.2.3. Every rule that turns a value into a string, or decides
  // Nearby's order, or tells an empty list apart from one never fetched,
  // lives in lib/format.ts and lib/wifi.ts; this view subscribes to the
  // accessPoints and handshakes stores (lib/stores.ts) and lays out what
  // those functions return.
  import {
    DASH,
    EMPTY_LABEL,
    formatByteSize,
    formatGpsPin,
    formatRemoteString,
    formatSegmentBadge,
    formatTruncationNotice,
    formatUnitTime,
    formatWifiEmptyMessage,
    formatWifiSortLabel,
    isRemoteStringUnknown,
    WIFI_REFRESH_LABEL,
    type WifiSegment,
  } from '../lib/format'
  import { currentRoute } from '../lib/router'
  import { currentClient } from '../lib/session'
  import { accessPoints, connection, handshakes, refreshWifiLists } from '../lib/stores'
  import { hasWifiDataArrived, sortAccessPoints, type NearbySortOrder } from '../lib/wifi'
  import type { WsClient } from '../lib/ws'
  import Segmented, { type SegmentTab } from '../shell/Segmented.svelte'

  // `Segmented` is generic over its tab id (see its own file), so fixing
  // this array's element type to `SegmentTab<WifiSegment>` is what carries
  // `WifiSegment` all the way through `onchange`, and the `tab`/`panel`
  // snippets below: no runtime guard or cast turns a bare `string` back
  // into `WifiSegment` anywhere in this file, because nothing here ever
  // holds one.
  const SEGMENT_TABS: SegmentTab<WifiSegment>[] = [
    { id: 'nearby', label: 'Nearby' },
    { id: 'captured', label: 'Captured' },
  ]

  // SPEC 4.5: kept while the app is open, not persisted across launches and
  // not written to the URL -- an ordinary component field does exactly
  // that, since views stay mounted for the whole session (SPEC 4.5).
  let activeSegment = $state<WifiSegment>('nearby')
  let sortOrder = $state<NearbySortOrder>('rssi')

  const conn = $derived($connection)
  const fetched = $derived(hasWifiDataArrived(conn.state))

  const nearbyList = $derived(sortAccessPoints($accessPoints, sortOrder))
  const capturedList = $derived($handshakes.entries)
  const capturedTruncated = $derived($handshakes.truncated)
  const capturedTotal = $derived($handshakes.total)

  // SPEC 4.5.2.3's DOM hooks: a badge's text is the count and nothing else
  // -- the bare number, or TRUNCATED_BADGE -- and which of the two is the
  // one thing here that can be wrong, so lib/format.ts decides it rather
  // than a ternary in this file. Nearby has no cap and so never truncates.
  const nearbyBadge = $derived(formatSegmentBadge(nearbyList.length, false))
  const capturedBadge = $derived(formatSegmentBadge(capturedList.length, capturedTruncated))

  const nearbyEmptyText = $derived(formatWifiEmptyMessage('nearby', fetched))
  const capturedEmptyText = $derived(formatWifiEmptyMessage('captured', fetched))
  const truncationText = $derived(formatTruncationNotice(capturedTotal))
  const sortLabel = $derived(formatWifiSortLabel(sortOrder))

  // `id: WifiSegment`, not `string`: with `SEGMENT_TABS` typed as above,
  // `t.id` inside the `tab` snippet below is already `WifiSegment`, so this
  // signature is what makes the compiler check the call rather than a
  // fallthrough ternary silently accepting anything wider.
  function badgeFor(id: WifiSegment): string {
    return id === 'nearby' ? nearbyBadge : capturedBadge
  }

  function requestRefresh(): void {
    const client = currentClient()
    if (client === null) return
    refreshWifiLists(client)
  }

  function toggleSort(): void {
    sortOrder = sortOrder === 'rssi' ? 'channel' : 'rssi'
  }

  // SPEC 4.5.2.3: "somebody has to ask, and today nobody does" -- the route
  // becoming `/wifi` is one of the two occasions that asks, and by no
  // timer. Becoming current, not mounting: SPEC 4.5 keeps every view
  // mounted when it is navigated away from, so a mount-time fetch would
  // fire once per app launch, and an owner who looks in at ten and again
  // at eleven would read an hour-old list with nothing on screen saying so.
  //
  // The view asks whenever it becomes current, connected or not: there is
  // no connection-state gate here, and there must not be one -- `request()`
  // (lib/ws.ts) queues unconditionally and flushes on reconnect, unlike
  // `command()`, which refuses outright while offline (SPEC 4.3.3). A gate
  // on `conn.state` here would be a second, wrong copy of a rule
  // `lib/ws.ts` already owns. The only real precondition is that a client
  // exists at all -- `client === null` -- because there is nothing to call
  // `request()` on yet.
  //
  // `askedForClient` holds the client the ask was made for, not a bare
  // flag, for the reason `lib/stores.ts`'s own comment above
  // `handshakesRefreshOwner` gives about the same shape one layer down: a
  // host switch (SPEC 4.4.2) can leave the ask made for unit A still
  // recorded while unit B is now attached. `lib/session.ts`'s `rebuild()`
  // runs teardown, `resetStores()`, then `connectStores(B)` -- all while
  // the route can still be `/wifi` the whole time, since a host switch is
  // not a navigation -- and a bare boolean would read that as "already
  // asked" and never ask B, leaving Captured empty (resetStores() just
  // cleared it, and initial_burst() sends access_points but not
  // handshakes_list) while the badge reads B as connected. That is exactly
  // the invisible failure this section exists to remove, reintroduced one
  // layer above the fetch this comparison protects.
  //
  // Comparing identity, not a flag, is what tells "the same client asking
  // again" apart from "a different client now attached": `askedForClient`
  // clears to `null` the moment the route stops being `/wifi`, so the next
  // time it becomes current is a fresh ask regardless of which client is
  // attached by then; while it stays current, a reconnect of the *same*
  // client does not re-ask, because `currentClient()` still returns the
  // client already recorded here. That is safe rather than merely
  // convenient: `request()` arms SPEC 4.3.8's 15 s timeout at the call,
  // before `flushQueue()`, so an outage longer than that already ends the
  // queued ask in a rejection `refreshWifiLists` swallows, and a shorter
  // one flushes and fills the list on its own -- a bonus, not something
  // this effect needs to build on by re-asking.
  //
  // The cold-start race moves here with the trigger rather than
  // disappearing: this view, like every other one, is mounted once at
  // startup regardless of which route is current, and a child's own
  // effects run before its parent's, so this can run before
  // App.svelte's onMount has called startSession() -- including on a
  // cold start straight into `/wifi`, where `currentRoute` already reads
  // `wifi` on the very first run and `currentClient()` is still `null`.
  // Reading `$connection` is what lets the ask wait that out -- waiting for
  // a client to exist (or a different one to replace it), never for a
  // particular state of one: the store notifies again once
  // lib/stores.ts's connectStores seeds it from a freshly attached client,
  // re-running this effect with the same route still current and a client
  // that now exists, whatever state it is in.
  let askedForClient: WsClient | null = null
  $effect(() => {
    const isCurrent = $currentRoute.id === 'wifi'
    void $connection
    if (!isCurrent) {
      askedForClient = null
      return
    }
    const client = currentClient()
    if (client === null) return
    if (askedForClient === client) return
    askedForClient = client
    refreshWifiLists(client)
  })
</script>

{#snippet nearbyRow(ap: (typeof nearbyList)[number])}
  {@const hostnameEmpty = isRemoteStringUnknown(ap.hostname)}
  {@const encryptionEmpty = isRemoteStringUnknown(ap.encryption)}
  {@const vendorEmpty = isRemoteStringUnknown(ap.vendor)}
  <li class="row" data-row="nearby" data-row-key={ap.bssid}>
    <span class="row-field" data-field="hostname" data-empty={hostnameEmpty ? 'true' : undefined}>
      {formatRemoteString(ap.hostname)}{#if hostnameEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
    </span>
    <span class="row-field" data-field="bssid">{ap.bssid}</span>
    <span class="row-field" data-field="channel">{ap.channel}</span>
    <span class="row-field" data-field="rssi">{ap.rssi}</span>
    <!-- SPEC 4.5.2.3: every remote string on a row follows §4.5.1.1 -- vendor
         and encryption dash with the accessible label when empty, exactly
         as hostname does. channel/rssi/clients are non-nullable numbers, so
         they carry no such rule (a branch nothing can reach, per §4.5.2.1). -->
    <span
      class="row-field"
      data-field="encryption"
      data-empty={encryptionEmpty ? 'true' : undefined}
    >
      {formatRemoteString(ap.encryption)}{#if encryptionEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
    </span>
    <span class="row-field" data-field="vendor" data-empty={vendorEmpty ? 'true' : undefined}>
      {formatRemoteString(ap.vendor)}{#if vendorEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
    </span>
    <span class="row-field" data-field="clients">{ap.clients}</span>
  </li>
{/snippet}

{#snippet capturedRow(entry: (typeof capturedList)[number])}
  {@const bssidEmpty = isRemoteStringUnknown(entry.bssid)}
  {@const gpsEmpty = formatGpsPin(entry.gps) === DASH}
  <li class="row" data-row="captured" data-row-key={entry.filename}>
    <span class="row-field" data-field="ssid">{entry.ssid}</span>
    <span class="row-field" data-field="bssid" data-empty={bssidEmpty ? 'true' : undefined}>
      {formatRemoteString(entry.bssid)}{#if bssidEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
    </span>
    <span class="row-field" data-field="time">{formatUnitTime(entry.mtime)}</span>
    <span class="row-field" data-field="size">{formatByteSize(entry.size)}</span>
    <span class="row-field" data-field="gps" data-empty={gpsEmpty ? 'true' : undefined}>
      {formatGpsPin(entry.gps)}{#if gpsEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
    </span>
  </li>
{/snippet}

<section class="view" data-view="wifi" aria-labelledby="view-title-wifi">
  <div class="header-row">
    <h1 id="view-title-wifi">Wi-Fi</h1>
    <!-- SPEC 4.5.2.3: both lists are fetched together, so one button covers
         both segments regardless of which panel is showing. -->
    <button type="button" data-action="refresh" onclick={requestRefresh}>
      {WIFI_REFRESH_LABEL}
    </button>
  </div>

  <Segmented
    idPrefix="wifi"
    label="Wi-Fi"
    tabs={SEGMENT_TABS}
    active={activeSegment}
    onchange={(id) => (activeSegment = id)}
  >
    {#snippet tab(t)}
      {t.label}
      <span class="badge" data-segment-badge>{badgeFor(t.id)}</span>
    {/snippet}
    {#snippet panel(t)}
      {#if t.id === 'nearby'}
        <div class="panel-toolbar">
          <button type="button" data-action="sort" onclick={toggleSort}>{sortLabel}</button>
        </div>
        {#if nearbyList.length === 0}
          <p class="empty-message" data-empty-message role="status">{nearbyEmptyText}</p>
        {:else}
          <!-- SPEC 4.5.2.3 names the BSSID as data-row-key and as the sort
               tie-break, never as a claim of uniqueness -- it contrasts it
               with the filename, "the one field SPEC 2.7 guarantees
               unique". `map_access_point` (plugin/companion.py) sets
               `"bssid": ap.get("mac") or ""`, so two bettercap entries with
               no mac are two empty-string BSSIDs, and Svelte 5 throws
               `each_key_duplicate` on a duplicate key -- in production, not
               only in dev -- which would take the whole view down over a
               single hostile or malfunctioning access point (SPEC 4.5.3).
               The list index is keyed here instead: it is trivially unique
               within one render of one array, at the cost of the identity
               tracking a stable key gives across re-sorts, which this list
               does not need to keep (no row carries local component state
               of its own to preserve). `data-row-key` still carries the
               BSSID, which is what the section actually asks for. -->
          <ul class="row-list">
            {#each nearbyList as ap, i (i)}
              {@render nearbyRow(ap)}
            {/each}
          </ul>
        {/if}
      {:else}
        {#if capturedTruncated}
          <p class="truncation-notice" data-truncation-notice>{truncationText}</p>
        {/if}
        {#if capturedList.length === 0}
          <p class="empty-message" data-empty-message role="status">{capturedEmptyText}</p>
        {:else}
          <ul class="row-list">
            {#each capturedList as entry (entry.filename)}
              {@render capturedRow(entry)}
            {/each}
          </ul>
        {/if}
      {/if}
    {/snippet}
  </Segmented>
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

  /* app.css sets a button's size and font but not its colours: a button
     with nothing else applied paints WebKit's platform default under
     whatever foreground this view inherits, light text on light grey,
     invisible in the dark palette on that engine specifically (the same
     note Dashboard.svelte and Settings.svelte carry). Every surface here is
     var(--bg), so an explicit background and colour are what stop the
     platform default from ever being reached. */
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

  .panel-toolbar {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 0.75rem;
  }

  .badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.5rem;
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
    background: var(--bg);
    font-size: 0.75rem;
    font-variant-numeric: tabular-nums;
  }

  .row-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.25rem 0.75rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }

  .row-field {
    font-variant-numeric: tabular-nums;
  }

  .empty-message,
  .truncation-notice {
    margin: 0 0 0.75rem;
    color: var(--text-dim);
  }

  /* Present in the DOM and read by assistive tech, invisible otherwise
     (SPEC 4.5.1.1's rule, applied here the same way Dashboard.svelte and
     Settings.svelte already do). */
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
