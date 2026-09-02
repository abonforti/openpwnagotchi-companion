<script lang="ts">
  // SPEC 4.5.2.4. Every rule that turns a value into a string, or tells an
  // empty list apart from one never fetched, lives in lib/format.ts and
  // lib/lists.ts; the sort order lives in lib/peers.ts, the split
  // lib/wifi.ts already draws for the Wi-Fi view (SPEC 4.5.2.3). This view
  // subscribes to the peers store (lib/stores.ts) and lays out what those
  // functions return.
  import {
    DASH,
    EMPTY_LABEL,
    formatPeerNumber,
    formatPeersEmptyMessage,
    formatRemoteString,
    formatUnitTime,
    isRemoteStringUnknown,
    REFRESH_LABEL,
  } from '../lib/format'
  import { hasListDataArrived } from '../lib/lists'
  import { sortPeers } from '../lib/peers'
  import { connection, peers, refreshPeers } from '../lib/stores'
  import { watchViewRefresh } from '../lib/viewRefresh'

  const conn = $derived($connection)
  const fetched = $derived(hasListDataArrived(conn.state))

  // SPEC 4.5.2.4: sorted by last seen, most recent first, signal as the
  // tie-break, fingerprint after that -- lib/peers.ts's rule, not this
  // view's.
  const peerList = $derived(sortPeers($peers))

  const emptyText = $derived(formatPeersEmptyMessage(fetched))

  // SPEC 4.5.2.4/4.5.2.3: the shared view-side trigger -- this view
  // becoming current, its own refresh control, and the active host
  // changing under it while it stays current -- and the per-client
  // coalescing behind refreshPeers, are lib/viewRefresh.ts's and
  // lib/stores.ts's, shared with WiFi.svelte rather than derived here a
  // second time.
  const peersRefresh = watchViewRefresh('peers', refreshPeers)

  function requestRefresh(): void {
    peersRefresh.refreshNow()
  }
</script>

{#snippet peerRow(peer: (typeof peerList)[number])}
  {@const nameEmpty = isRemoteStringUnknown(peer.name)}
  {@const fullNameEmpty = isRemoteStringUnknown(peer.fullName)}
  {@const fingerprintEmpty = isRemoteStringUnknown(peer.fingerprint)}
  {@const faceEmpty = isRemoteStringUnknown(peer.face)}
  {@const versionEmpty = isRemoteStringUnknown(peer.version)}
  {@const rssiEmpty = formatPeerNumber(peer.rssi) === DASH}
  {@const channelEmpty = formatPeerNumber(peer.channel) === DASH}
  {@const lastSeenEmpty = formatUnitTime(peer.lastSeen) === DASH}
  {@const encountersEmpty = formatPeerNumber(peer.encounters) === DASH}
  {@const pwndRunEmpty = formatPeerNumber(peer.pwndRun) === DASH}
  {@const pwndTotalEmpty = formatPeerNumber(peer.pwndTotal) === DASH}
  <!-- SPEC 4.5.2.4: data-row-key carries the fingerprint, as the section
       asks, but it is never the {#each} key -- lib/stores.ts upserts a
       peer by fingerprint and its own comment explains why '???' is
       peer.identity()'s documented default rather than an edge case, so
       the store deliberately produces a list where several rows share it.
       Keying {#each} on that throws each_key_duplicate in production,
       taking the whole view down over the first unadvertised peer, the
       same defect issue #187 hit on AccessPoint.bssid and the same fix
       WiFi.svelte's nearby list already carries: the list index is keyed
       instead, trivially unique within one render of one array. -->
  <li class="row" data-row="peer" data-row-key={peer.fingerprint}>
    <!-- SPEC 4.5.2.4: name and fingerprint carry '???' rather than null
         when unadvertised, and '???' is rendered, not translated into a
         dash -- it is what the peer's own unit reports, and collapsing it
         into the same dash a field the app failed to fetch gets would make
         a deliberately anonymous peer indistinguishable from one this app
         could not read. That is a rule about '???' as a value, not about
         emptiness: name, fullName and fingerprint are required strings by
         schema with no null case of their own, the same shape
         AccessPoint.hostname/vendor/encryption already have on the Wi-Fi
         view, and a peer-chosen '' is exactly the "required but not a
         reading" case formatRemoteString/isRemoteStringUnknown exist for
         (SPEC 4.5.3) -- so all three route through them like every other
         remote string on this row, and only '' dashes; '???' passes
         through unchanged because it is not ''. -->
    <span
      class="row-field"
      data-field="name"
      data-empty={nameEmpty ? 'true' : undefined}
    >
      <bdi>{formatRemoteString(peer.name)}</bdi>{#if nameEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="fullName"
      data-empty={fullNameEmpty ? 'true' : undefined}
    >
      <bdi>{formatRemoteString(peer.fullName)}</bdi>{#if fullNameEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="fingerprint"
      data-empty={fingerprintEmpty ? 'true' : undefined}
    >
      <bdi>{formatRemoteString(peer.fingerprint)}</bdi
      >{#if fingerprintEmpty}<span class="visually-hidden">
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="face"
      data-empty={faceEmpty ? 'true' : undefined}
    >
      <bdi>{formatRemoteString(peer.face)}</bdi>{#if faceEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="version"
      data-empty={versionEmpty ? 'true' : undefined}
    >
      <bdi>{formatRemoteString(peer.version)}</bdi>{#if versionEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="rssi"
      data-empty={rssiEmpty ? 'true' : undefined}
    >
      {formatPeerNumber(peer.rssi)}{#if rssiEmpty}<span class="visually-hidden">
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="channel"
      data-empty={channelEmpty ? 'true' : undefined}
    >
      {formatPeerNumber(peer.channel)}{#if channelEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="lastSeen"
      data-empty={lastSeenEmpty ? 'true' : undefined}
    >
      {formatUnitTime(peer.lastSeen)}{#if lastSeenEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="encounters"
      data-empty={encountersEmpty ? 'true' : undefined}
    >
      {formatPeerNumber(peer.encounters)}{#if encountersEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="pwndRun"
      data-empty={pwndRunEmpty ? 'true' : undefined}
    >
      {formatPeerNumber(peer.pwndRun)}{#if pwndRunEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
    <span
      class="row-field"
      data-field="pwndTotal"
      data-empty={pwndTotalEmpty ? 'true' : undefined}
    >
      {formatPeerNumber(peer.pwndTotal)}{#if pwndTotalEmpty}<span
          class="visually-hidden"
        >
          {EMPTY_LABEL}</span
        >{/if}
    </span>
  </li>
{/snippet}

<section class="view" data-view="peers" aria-labelledby="view-title-peers">
  <div class="header-row">
    <h1 id="view-title-peers">Peers</h1>
    <button type="button" data-action="refresh" onclick={requestRefresh}>
      {REFRESH_LABEL}
    </button>
  </div>

  {#if peerList.length === 0}
    <p class="empty-message" data-empty-message role="status">{emptyText}</p>
  {:else}
    <ul class="row-list">
      {#each peerList as peer, i (i)}
        {@render peerRow(peer)}
      {/each}
    </ul>
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

  /* app.css sets a button's size and font but not its colours: a button
     with nothing else applied paints WebKit's platform default under
     whatever foreground this view inherits, light text on light grey,
     invisible in the dark palette on that engine specifically (the same
     note WiFi.svelte, Dashboard.svelte and Settings.svelte carry). Every
     surface here is var(--bg), so an explicit background and colour are
     what stop the platform default from ever being reached. */
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

  .empty-message {
    margin: 0;
    color: var(--text-dim);
  }

  /* Present in the DOM and read by assistive tech, invisible otherwise
     (SPEC 4.5.1.1's rule, applied here the same way WiFi.svelte,
     Dashboard.svelte and Settings.svelte already do). */
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
