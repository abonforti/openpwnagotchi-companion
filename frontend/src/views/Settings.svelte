<script lang="ts">
  // SPEC 4.5.2 (the Settings bullet) over lib/settings.ts (SPEC 4.7). This
  // view only reads the settings store and calls its mutators; the switch
  // itself -- teardown, resetStores(), attach -- belongs to lib/session.ts,
  // which subscribes to activeHost, and is not reimplemented here.
  import { DASH, EMPTY_LABEL, formatConnectionState, formatUnauthorizedReason } from '../lib/format'
  import {
    activateHost,
    addHost,
    DEFAULT_HTTP_PORT,
    DEFAULT_WS_PORT,
    isUsableAddress,
    removeHost,
    settings,
    updateHost,
    type Host,
  } from '../lib/settings'
  import { capabilities, connection } from '../lib/stores'

  const hostsList = $derived($settings.hosts)
  const activeId = $derived($settings.activeHostId)
  const conn = $derived($connection)
  const caps = $derived($capabilities)

  // SPEC 4.5.1: "ports are diagnostics, not settings" -- this screen never
  // offers a field for them, on a new host or an existing one, so a host
  // created here always takes the same two defaults lib/settings.ts falls
  // back to for a port it cannot make sense of. SPEC 4.5.2.1: those
  // defaults are imported, not restated, so a change to either in
  // lib/settings.ts cannot leave this screen quietly minting hosts on the
  // old port.

  // SPEC 4.5.2.1: the form calls lib/settings.ts's own predicate rather
  // than carrying a copy of the grammar. addHost/updateHost enforce the
  // same rule and throw on a miss (SPEC 4.7); this is what lets the form
  // catch it before that throw is the only way to find out, and because
  // it is the same function the mutator's own check calls, the two can
  // never disagree -- the throw stays as the backstop, unreachable now
  // because there is one grammar, not because two copies happen to match.
  const ADDRESS_HELP =
    'Four numbers 0-255 separated by dots, such as 172.20.10.2. No hostname, no port, no scheme.'

  const TOKEN_HELP =
    "Clearing this removes it from this device only. The unit keeps accepting it until its own token is changed in config.toml and it restarts."

  const CSP_NOTE =
    "This copy of the app was served by one unit, and that unit's Content-Security-Policy only " +
    'allows the addresses it names. Pointing it at a different unit here can be blocked by that ' +
    'policy; if the connection fails, open Settings from the unit you actually want to reach.'

  // ---------------------------------------------------------------------
  // Add host. Always rendered, not behind a toggle: an address typed here
  // that fails the grammar is refused with a message next to the field
  // (SPEC 4.7), never a thrown error.
  // ---------------------------------------------------------------------
  let addLabel = $state('')
  let addAddress = $state('')
  let addToken = $state('')
  let addError = $state<string | null>(null)

  function resetAddForm(): void {
    addLabel = ''
    addAddress = ''
    addToken = ''
    addError = null
    if (revealedTokenField?.kind === 'add') revealedTokenField = null
  }

  function submitAddForm(): void {
    const address = addAddress.trim()
    if (!isUsableAddress(address)) {
      addError = `"${address}" is not an IPv4 address. ${ADDRESS_HELP}`
      return
    }
    try {
      addHost({
        label: addLabel.trim(),
        address,
        wsPort: DEFAULT_WS_PORT,
        httpPort: DEFAULT_HTTP_PORT,
        token: addToken.trim() === '' ? null : addToken.trim(),
      })
      resetAddForm()
    } catch (err) {
      // SPEC 4.7: the backstop. addHost throws only on an address the check
      // above already rejects, so this is unreachable from this form today;
      // kept because a throw reaching the user is a bug in this screen and
      // this is what keeps one from doing that if the two ever disagree.
      addError = err instanceof Error ? err.message : 'Could not add this host.'
    }
  }

  // ---------------------------------------------------------------------
  // Editing a host's label/address. Behind a per-row toggle: unlike the
  // token below, an edit here can be refused (a bad address), so it needs
  // an explicit save rather than committing on every change.
  // ---------------------------------------------------------------------
  let editingId = $state<string | null>(null)
  let editLabel = $state('')
  let editAddress = $state('')
  let editError = $state<string | null>(null)

  function startEdit(host: Host): void {
    editingId = host.id
    editLabel = host.label
    editAddress = host.address
    editError = null
  }

  function cancelEdit(): void {
    editingId = null
    editError = null
  }

  function submitEdit(id: string): void {
    const address = editAddress.trim()
    if (!isUsableAddress(address)) {
      editError = `"${address}" is not an IPv4 address. ${ADDRESS_HELP}`
      return
    }
    try {
      updateHost(id, { label: editLabel.trim(), address })
      cancelEdit()
    } catch (err) {
      editError = err instanceof Error ? err.message : 'Could not save this host.'
    }
  }

  function handleRemove(id: string): void {
    if (editingId === id) cancelEdit()
    if (revealedTokenField?.kind === 'row' && revealedTokenField.hostId === id) revealedTokenField = null
    removeHost(id)
  }

  function handleActivate(id: string): void {
    // SPEC 4.3.4/4.8: this only activates the host. Whether the socket that
    // follows ever reaches `connected` is reported by the diagnostics below,
    // never assumed here.
    activateHost(id)
  }

  // SPEC 4.7: a token has no grammar to fail, so it commits directly from
  // the row rather than going through the label/address edit form and its
  // save/cancel pair. The existing address on the record has already
  // passed isUsableAddress once (on creation or the last successful edit),
  // so a patch that touches only `token` can never hit updateHost's address
  // throw.
  function commitToken(id: string, value: string): void {
    updateHost(id, { token: value.trim() === '' ? null : value.trim() })
  }

  // SPEC 4.5.2.1: masked by default, one field revealed at a time across
  // the whole screen -- a saved host's token and the one being typed into
  // the add-host form are masked on the same terms, because what changes
  // is how long the secret is on screen, not who can see it. One shared
  // state (rather than an independent reveal per field) keeps that "one
  // at a time" true screen-wide instead of only within a row: revealing a
  // second field would otherwise leave an earlier one still showing
  // whatever the owner typed or the unit issued.
  //
  // TokenField is tagged rather than a bare id: a stored host's id comes
  // out of parseHost (lib/settings.ts) as any non-empty string, so a blob
  // that named a host `add` would collide with a string sentinel for the
  // add-host form and reveal both fields together. The `kind` discriminant
  // makes the add form's identity distinguishable from any row's by
  // construction, not by an id space the parser does not actually bound.
  type TokenField = { kind: 'add' } | { kind: 'row'; hostId: string }

  function sameTokenField(a: TokenField | null, b: TokenField): boolean {
    if (a === null) return false
    if (a.kind === 'add') return b.kind === 'add'
    return b.kind === 'row' && a.hostId === b.hostId
  }

  let revealedTokenField = $state<TokenField | null>(null)

  function toggleTokenReveal(field: TokenField): void {
    revealedTokenField = sameTokenField(revealedTokenField, field) ? null : field
  }

  // SPEC 4.5.2.1: choosing a sentence by rule from a value is computing,
  // so the mapping lives in lib/format.ts (SPEC 4.5.1.1's rule for the
  // Dashboard, applied here) and this view only lays out what it returns.
  const connectionLabel = $derived(formatConnectionState(conn.state))

  // SPEC 4.5.2.1: the last error and the latency have no surface in
  // lib/ws.ts today (issue #176) and this view must not manufacture one.
  // The one thing the state itself names, when it is `unauthorized`, is
  // the reason -- rendered as what it is rather than folded into a generic
  // "last error" that would claim nothing happened when a TLS failure or a
  // refused connection is exactly what did.
  const unauthorizedReasonText = $derived(
    conn.state === 'unauthorized' && conn.unauthorizedReason !== null
      ? formatUnauthorizedReason(conn.unauthorizedReason)
      : null,
  )

  // capabilities.pluginVersion is a string the plugin put on the wire
  // (SPEC 4.5.3 treats it like any other remote string), rendered as text
  // by Svelte's default {...} escaping and never through the raw-markup
  // escape hatch SPEC 4.5.3 bans. That hatch is not named here on purpose:
  // the CI gate greps this tree for its name, and a comment saying it is
  // not used reads to a grep exactly like using it.
  const pluginVersionText = $derived(caps === null || caps.pluginVersion === '' ? DASH : caps.pluginVersion)
  const pluginVersionEmpty = $derived(pluginVersionText === DASH)
</script>

<section class="view" data-view="settings" aria-labelledby="view-title-settings">
  <h1 id="view-title-settings">Settings</h1>

  <h2>Hosts</h2>
  {#if hostsList.length === 0}
    <p class="placeholder">No hosts yet. Add one below to connect.</p>
  {:else}
    <ul class="host-list">
      {#each hostsList as host (host.id)}
        <li
          class="host"
          data-host-id={host.id}
          data-host-active={host.id === activeId ? 'true' : undefined}
        >
          {#if editingId === host.id}
            <form
              class="host-form"
              onsubmit={(event) => {
                event.preventDefault()
                submitEdit(host.id)
              }}
            >
              <label for={`edit-label-${host.id}`}>
                Label
                <input
                  id={`edit-label-${host.id}`}
                  type="text"
                  data-host-field="label"
                  bind:value={editLabel}
                />
              </label>
              <label for={`edit-address-${host.id}`}>
                Address
                <input
                  id={`edit-address-${host.id}`}
                  type="text"
                  data-host-field="address"
                  bind:value={editAddress}
                  aria-describedby={`edit-addr-help-${host.id}`}
                  required
                />
              </label>
              <p id={`edit-addr-help-${host.id}`} class="help">{ADDRESS_HELP}</p>
              {#if editError}
                <p class="error" role="alert" data-field-error="address">{editError}</p>
              {/if}
              <div class="row-actions">
                <button type="submit" data-action="save">Save</button>
                <button type="button" data-action="cancel" onclick={cancelEdit}>Cancel</button>
              </div>
            </form>
          {:else}
            <div class="host-identity">
              <span class="host-label" data-host-field="label">{host.label}</span>
              {#if host.id === activeId}
                <span class="host-active-badge">Active</span>
              {/if}
            </div>
            {#if host.id === 'usb'}
              <!-- SPEC 4.7: the desktop-only note belongs here, not in the
                   persisted label, because a label is never re-derived and
                   an owner renaming this entry must not delete a warning
                   tied to what the entry is, not what it is called. -->
              <p class="host-note">Desktop only. An iPhone cannot use the USB gadget path.</p>
            {/if}
            <div class="host-address" data-host-field="address">{host.address}</div>
            <div class="row-actions">
              <button type="button" data-action="edit" onclick={() => startEdit(host)}>Edit</button>
            </div>
          {/if}

          <!-- SPEC 4.5.1: ports are diagnostics, not settings -- shown,
               never editable, and shown regardless of whether the row is
               being edited. -->
          <div class="host-ports">
            WS <span data-host-field="wsPort">{host.wsPort}</span>
            &middot; HTTPS <span data-host-field="httpPort">{host.httpPort}</span>
          </div>

          <!-- SPEC 4.7: a token has no grammar to fail, so it is edited
               directly here rather than behind the label/address save.
               SPEC 4.5.2.1: masked by default -- a screen opened outdoors
               otherwise shows every unit's shared secret at once -- with a
               reveal control that acts on one field at a time, screen-wide. -->
          <label class="token-field" for={`token-${host.id}`}>
            Token (optional)
            <div class="token-input-row">
              <input
                id={`token-${host.id}`}
                type={sameTokenField(revealedTokenField, { kind: 'row', hostId: host.id }) ? 'text' : 'password'}
                data-host-field="token"
                value={host.token ?? ''}
                autocomplete="off"
                onchange={(event) => commitToken(host.id, (event.currentTarget as HTMLInputElement).value)}
              />
              <button
                type="button"
                data-action="reveal"
                onclick={() => toggleTokenReveal({ kind: 'row', hostId: host.id })}
              >
                {sameTokenField(revealedTokenField, { kind: 'row', hostId: host.id }) ? 'Hide' : 'Show'}
              </button>
            </div>
          </label>
          <p class="help">{TOKEN_HELP}</p>

          <div class="row-actions">
            {#if host.id !== activeId}
              <button type="button" data-action="activate" onclick={() => handleActivate(host.id)}>
                Connect
              </button>
            {/if}
            <button type="button" class="danger" data-action="remove" onclick={() => handleRemove(host.id)}>
              Remove
            </button>
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  <h2>Add host</h2>
  <form
    class="host-form"
    onsubmit={(event) => {
      event.preventDefault()
      submitAddForm()
    }}
  >
    <label for="add-label">
      Label (optional)
      <input id="add-label" type="text" data-add-field="label" bind:value={addLabel} />
    </label>
    <label for="add-address">
      Address
      <input
        id="add-address"
        type="text"
        data-add-field="address"
        bind:value={addAddress}
        aria-describedby="add-addr-help"
        required
      />
    </label>
    <p id="add-addr-help" class="help">{ADDRESS_HELP}</p>
    <label for="add-token">
      Token (optional)
      <div class="token-input-row">
        <input
          id="add-token"
          type={sameTokenField(revealedTokenField, { kind: 'add' }) ? 'text' : 'password'}
          data-add-field="token"
          bind:value={addToken}
          autocomplete="off"
        />
        <button
          type="button"
          data-action="reveal"
          onclick={() => toggleTokenReveal({ kind: 'add' })}
        >
          {sameTokenField(revealedTokenField, { kind: 'add' }) ? 'Hide' : 'Show'}
        </button>
      </div>
    </label>
    {#if addError}
      <p class="error" role="alert" data-field-error="address">{addError}</p>
    {/if}
    <!-- SPEC 4.7: said here rather than left to be discovered as a silent
         failure once a second host is pointed at a different unit than the
         one that served this copy of the app. -->
    <p class="help">{CSP_NOTE}</p>
    <div class="row-actions">
      <button type="submit" data-action="add">Add host</button>
    </div>
  </form>

  <h2>Diagnostics</h2>
  <div class="fields">
    <div class="field">
      <span class="field-label">Connection</span>
      <span class="field-value" data-field="connectionState">{connectionLabel}</span>
    </div>
    <div class="field">
      <span class="field-label">Plugin version</span>
      <span
        class="field-value"
        data-field="pluginVersion"
        data-empty={pluginVersionEmpty ? 'true' : undefined}
      >
        {pluginVersionText}{#if pluginVersionEmpty}<span class="visually-hidden"> {EMPTY_LABEL}</span>{/if}
      </span>
    </div>
    {#if unauthorizedReasonText !== null}
      <div class="field">
        <span class="field-label">Unauthorized reason</span>
        <span class="field-value" data-field="unauthorizedReason">{unauthorizedReasonText}</span>
      </div>
    {/if}
  </div>
</section>

<style>
  h2 {
    margin: 1.5rem 0 0.5rem;
    font-size: 1rem;
  }

  .host-list {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  .host {
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    padding: 0.75rem 1rem;
  }

  .host-identity {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .host-label {
    font-weight: 700;
  }

  .host-active-badge {
    display: inline-flex;
    align-items: center;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--accent);
    color: var(--bg);
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .host-note,
  .host-ports,
  .help {
    margin: 0.25rem 0 0;
    color: var(--text-dim);
    font-size: 0.875rem;
  }

  .host-address {
    margin-top: 0.25rem;
    font-variant-numeric: tabular-nums;
  }

  .token-field {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    margin-top: 0.5rem;
  }

  .token-input-row {
    display: flex;
    gap: 0.5rem;
  }

  .token-input-row input {
    flex: 1;
  }

  .error {
    margin: 0.5rem 0 0;
    color: var(--danger);
  }

  .row-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin-top: 0.75rem;
  }

  /* app.css sets a button's size and font but not its colours, so a button
     with nothing else applied paints WebKit's platform default (ButtonFace)
     under whatever foreground this screen inherits -- light text on a light
     grey, invisible in the dark palette on that engine specifically. Every
     surface a button in this view sits on (.host, .host-form) is
     var(--surface), so an explicit transparent background lets that surface
     show through instead, the same way Nav.svelte's bar buttons do; the
     explicit colour is what stops the platform default from ever being
     reached in the first place, in either palette. */
  button {
    background: none;
    color: var(--text);
  }

  .danger {
    color: var(--danger);
    border: 1px solid var(--danger);
    border-radius: 6px;
    background: none;
  }

  .host-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    padding: 0.75rem 1rem;
  }

  .host-form label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }

  .host-form input,
  .token-input-row input {
    min-height: 44px;
    padding: 0 0.5rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font: inherit;
  }

  .host-form input:focus-visible,
  .token-input-row input:focus-visible,
  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
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

  /* Present in the DOM and read by assistive tech, invisible otherwise
     (SPEC 4.5.1.1's rule, applied here the same way Dashboard.svelte does). */
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
