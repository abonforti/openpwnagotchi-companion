# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is in `0.x`: the WebSocket contract is **not** stable, and a breaking change to it
bumps the minor version and appears under `Changed` or `Removed`. See `SPEC.md` §12. Version
`1.0.0` will be cut only once the v1 feature set is complete and the protocol has run end to end
against real hardware.

## [Unreleased]

### Added

- **The PWA is an app.** It began as a navigation shell around seven routes, six of which were
  nine-line placeholders. All seven views now exist, and each arrived with the rule it needed
  decided before it was drawn.
  - **The navigation shell**: five slots for seven views, the seventh and the two spare ones
    reached through a More sheet, a rail instead of a bottom bar in short landscape, and views
    that stay mounted when navigated away from so a screen does not lose its place (issue #40).
  - **Dashboard**, a status card of the unit's readings rather than a summary of them, saying
    plainly what it does not know instead of showing a confident zero (issue #121).
  - **Settings**, which is what made the app reachable at all: before it there was no screen that
    called `activateHost`, so no client was ever built and the socket was never opened on a fresh
    install, behind a Dashboard advising the owner to check a hotspot that nothing was using. It
    derives the first-run host from the address the page was served from, and carries the host
    list, the token field and the diagnostics (issue #134).
  - **The four state controls** on the Dashboard, as an inline two-step confirm rather than a
    dialog: one control armed at a time, arming that does not expire, focus moving to Cancel and
    not to Confirm, and a refusal appearing next to the control that was tapped rather than
    somewhere global (issue #184).
  - **Wi-Fi**, two segments over one screen: networks in range, ordered by signal or by channel
    on request, and captures on disk, left in the order the unit sent them. A read queues while
    the socket is down and a state command is refused, which is not the same rule and is not made
    to look like one (issue #187).
  - **Peers**, and with it the refresh rule stopped being the Wi-Fi view's private business and
    became shared (issue #190).
  - **The Map**, the seventh view and the last placeholder: captures plotted where they were
    taken, the track drawn client-side from successive positions because the wire carries no
    track, and the one external origin this app talks to (issue #198).
  - **The Settings diagnostics**, which asked for three readings and could render one. The client
    now keeps the last error - a frame's code and message, the code of a close it did not ask
    for, or the drop it detected itself when neither ever arrives - and measures the round trip
    on the ping it was already sending. It never reads the unit's own timestamp: a pwnagotchi
    with no RTC boots days wrong, and a duration across two clocks measures their disagreement as
    well as the link (issue #176).
  - **The Log**, live-following on the one timer this client is allowed, with the exception
    argued rather than assumed, and a font-size control for reading it outdoors. Its filter
    narrows what is on the screen and never what is asked for. The level filter §4.5.1 asks for
    is deliberately **not** built: nothing here has seen a real log line, so its format is not a
    fact this project may rely on (issue #193, blocked on issue #194).
  - **The Mirror**, the unit's own e-ink screen, on the second timer, granted on the record with
    what has not been measured written down beside it (issue #196).
- **Tagging now produces something to install.** `.github/workflows/release.yml` builds the
  frontend on a pushed `v*` tag, packs `frontend/dist` into `dist.tgz`, and publishes a Release
  carrying that archive and a `SHA256SUMS` beside it - which is what `tools/install-on-pi.sh` has
  always downloaded and verified. The workflow was specified in five places and had never been
  written, so a tag built nothing, attached nothing, and failed at nothing (issue #128). It
  refuses a tag that is not an ancestor of `master`, which catches a tag on a commit that was
  never merged - though not a tag carrying its own copy of the workflow, since GitHub runs the one
  it finds on the tagged ref, and closing that needs a tag protection ruleset (issue #181). It
  refuses a tag whose version disagrees with
  `plugin/companion.py` or `frontend/package.json` - which is the check SPEC §2.1 already claimed
  CI performed. A tag with a pre-release suffix publishes a pre-release, so an `-rc` does not
  become the version an installer picks up by default.
- **A configuration key the plugin does not know is named at `WARNING`, once, at load.** The
  message gives the key and the set of keys that are accepted, and the plugin comes up on its
  defaults as before. Nothing was said until now, so `bind_address` for `bind_addresses` - a
  singular that reads correctly and binds nothing - looked exactly like a working configuration.
  `enabled` is pwnagotchi's own key and never warns, and the withdrawn `interfaces` keeps its own
  message from the breaking change under `Changed` rather than being reported twice. A block with
  nothing but valid keys logs nothing at all (issue #137).
- `tools/gen-ca.sh` and `tools/gen-cert.sh`: a private CA and an `iPAddress`-SAN server
  certificate, non-interactive and idempotent, encoding the three constraints iOS enforces
  silently (critical `basicConstraints`, IP SANs and never `DNS:`, validity under 825 days).
- `tests/test_integration_ws.py`: a real WSS server and a real client over TLS material issued
  by those scripts, driving every command end to end.
- `tests/tools/test_certs.py`: the certificate scripts driven with `subprocess` and inspected
  with `openssl`, in pytest rather than bats.
- `keepalive` is now actually emitted, every `keepalive_interval` seconds (default 20). It was in
  the schema and in `docs/PROTOCOL.md` but nothing sent it. The `0`-disables behaviour described
  here was withdrawn later in this same unreleased cycle; see `Changed` below.
- `.github/labels.json` and `.github/sync_labels.sh`: the label taxonomy as a file, and the
  script that applies it.
- Build specification (`SPEC.md`), with a pinned-facts allowlist of every pwnagotchi symbol the
  plugin may use, verified against the tag named by `verified_against` in
  `.github/pinned_symbols.json`. The version was written here too until that key became the
  one place it is named (issue #151).
- WebSocket contract as JSON Schema (`docs/schemas/`), the single source of truth for the wire
  format, plus `docs/PROTOCOL.md` for the framing and lifecycle rules a schema cannot express.
- `tools/gen-protocol-types.mjs`, which generates `frontend/src/lib/protocol.ts` from the
  schemas and can verify the two are in sync.
- Repository scaffold: `README.md`, `SECURITY.md`, GPL-3.0 `LICENSE`, `.gitignore`.
- Local quality gates: `.githooks/pre-commit` (leak scan against an out-of-repo denylist) and
  `.githooks/pre-push` (refuses a direct push to `master`).
- Project agent definitions in `.claude/agents/` for implementation, test authoring, review, QA
  and security auditing.

- **A `log_path` config key**, so a unit with no agent can be told where its log is instead of
  only being able to say it cannot find one. A unit in manual mode never gets an agent, so the
  log was unavailable for the life of the process on exactly the units somebody is most likely to
  be debugging. `get_log` still carries a line count and nothing else: a message that could name
  a file would make this an arbitrary-file reader reachable over a socket (issue #154).

### Changed

- **`stats` carries the unit's own name, and the field is required.** `Stats.name` is
  `pwnagotchi.name()`, the hostname, or `null` when the unit has none; the header names the
  unit by it when the owner gave the host no label of their own (issues #30, #200). A change to
  the shape of an existing message, so a MINOR bump under SPEC §12: an app newer than its
  plugin drops every `stats` frame at the boundary rather than showing readings it cannot vouch
  for, and the Settings diagnostics count the drops. Update the plugin and the app together.
- **`handshakes_list.total` is nullable.** An empty list with a total of zero read as a unit with
  no captures, which is a claim, and the plugin had not looked anywhere to support it. `null` now
  means the directory is unknown - no agent and no `handshake_dir` - and `0` means it was found
  and holds nothing. The same honesty the counts got, at the field that still had the confident
  zero (issue #153).
- **Three config keys were removed rather than implemented.** `save_gps_log`, `gps_log_path` and
  `mirror_auto_interval` were accepted, defaulted, documented and read by nothing. A key that is
  advertised and does nothing is worse than a missing one: whoever sets it has been answered, and
  the answer is false. A config carrying one of them is now named by the unknown-key scan, which
  is the true statement (issue #156).
- **`gpsd_host` must be an address literal.** The poll promised to be bounded by construction and
  was bounded only when the host was an address: a socket timeout covers the connect, not the
  `getaddrinfo` before it, so a hostname behind a dead resolver blocked the thread that also
  refreshes the session cache. A hostname is refused when the config is read and disables the
  gpsd source alone; the plugin degrades rather than taking the unit down (issue #163).

- **`rebind_interval` is clamped to 5-300 seconds**, and a configured `0` now takes the floor of
  5 rather than running the reconcile on every pass. Unlike `keepalive_interval = 0`, which means
  the default, `0` here never meant disable. A value that is not a usable number, `nan` and both
  infinities take the default 30 and are reported at `WARNING`, as the other two intervals
  already were. This is the fix for a value that could stop far more than itself: the interval
  was read with a bare `float()` at the entry of the background thread, outside that loop's own
  `try`, so `rebind_interval = "30s"` killed the session refresh, the gpsd poll and the listener
  reconcile together, for the life of the process, with the sockets still open and the data
  served from then on stale. A bare `nan`, which TOML has a literal for, stopped the reconcile
  silently after the first pass. If your `rebind_interval` is a number in range, nothing changes
  for you (issue #105).
- **`install-on-pi.sh` reads the plugins directory from your unit, and refuses rather than
  guessing.** It used to fall back to `/usr/local/share/pwnagotchi/custom-plugins/` when
  `/etc/pwnagotchi/config.toml` set no `main.custom_plugins`. That was upstream's default at
  2.9.5.6; upstream moved it at 2.9.5.8, so on a newer unit with a silent configuration the
  installer wrote the plugin to a directory pwnagotchi no longer reads, printed success, and left
  a plugin that never loads. It now reads `custom_plugins` from the `defaults.toml` of the
  pwnagotchi installed on the unit, and if neither file answers it **exits 1** where it used to
  exit 0, naming every place it looked. If your unit sets `main.custom_plugins`, nothing changes
  for you (issue #157).
- **New `--pwn-prefix DIR` on the installer**, default `/opt/.pwn`, naming where the pwnagotchi
  package tree lives. Only needed if yours is somewhere else.
- **The pinned pwnagotchi version is named in one place**, `verified_against` in
  `.github/pinned_symbols.json`, and `check_pinned_facts.py` now runs against that tag by default
  instead of against the latest release. `--latest` asks the other question, has upstream moved,
  and is what the weekly job runs (issue #151).
- **`mode` is null when the plugin has no agent**, on both `Stats` and `FaceStatus`, where it
  used to be `AUTO`. This is a re-shaped message and therefore the project's first MINOR bump
  under SPEC §12, to **0.1.0**. It was found on the first end-to-end run on real hardware
  (issue #140): the unit's own display read MANU while the app read AUTO, which is worse than a
  missing value because nothing about it looks wrong. A client that assumed `mode` was always a
  string now has a null to handle; the app renders it as a dash, the same as every other value it
  does not know.
- **`handshakes` and `handshakesTotal` are null when the capture directory is unknown**, where
  they used to report a confident zero. This is the same defect as `mode` at a different field
  (issue #146): the directory was read only from `agent._config['bettercap']['handshakes']`, so a
  unit with no agent reported zero handshakes regardless of how many captures sat on disk. Another
  re-shaped message and therefore the project's second MINOR bump under SPEC §12, to **0.2.0**. A
  client that assumed the two counts were always integers now has nulls to handle, and the two are
  null together or neither: they come from one scan of one directory. Half of the fix is the null;
  the other half is a new plugin config key, `handshake_dir`, empty by default, so a unit whose
  agent never arrives can be told where its captures are instead of only being able to say it does
  not know.
- **The plugin's threads start in `on_loaded` rather than in `on_ready`.** In manual mode
  pwnagotchi never calls `on_ready`, so the background pass and the stats ticker never started:
  no session refresh, no gpsd poll, and no listener reconcile, which is the pass that binds a
  tether when it comes up. `on_ready` now captures the agent and nothing else. Its one-shot
  session refresh is gone with it, which is a fix rather than a casualty: `agent.session()` is a
  bettercap GET with a 30 second timeout, and it was being made inside a pwnagotchi lifecycle
  hook.
- **The two loop intervals are clamped: `keepalive_interval` to 5-20 s, `session_poll_interval`
  to 1-5 s.** `stats` now rides a ticker of its own at `keepalive_interval` (SPEC §2.4, §4.3.7,
  issue #65), so between
  them they decide whether the app can tell fresh data from stale. The staleness threshold is
  derived from the refresh episode rather than from the broadcast spacing, so above the ceiling
  the app does not report a stall falsely; it reports it late, or steps over it between two
  samples and misses it. `session_poll_interval` is clamped as well because it sets how soon
  `sessionAge` is reset once bettercap answers again. **`degraded` now leaves the state-changing
  controls enabled**, reversing an earlier decision: it means the unit is answering and its
  bettercap data is old, not that the link is in doubt, and refusing a mode change there would
  punish the user for a failure the unit can still act around. A configured `keepalive_interval = 0`, which used
  to disable the broadcast, now takes the default 20 rather than the floor, so a unit that had it
  switched off does not begin sending a full payload every second. The clamp is logged when it
  applies. Nothing is broken for anyone by this: the `0`-disables behaviour it withdraws was
  itself added in this same unreleased cycle and has never been in a tagged version. A value
  that is not a usable number at all - a string, a boolean, `nan`, either infinity - takes the
  default and is reported at `WARNING`, one level above the clamp, because a number outside
  the range is a preference the plugin overrode while `"20s"` is a mistake worth surfacing.
- **Breaking configuration change.** The plugin binds by address rather than by interface name:
  `interfaces = ["bnep0", "usb0"]` is replaced by
  `bind_addresses = ["172.20.10.0/28", "10.0.0.2"]`, whose entries are IPv4 addresses or CIDR
  blocks. A configuration still carrying `interfaces` is warned about and ignored; it binds
  nothing, so anyone who installed from `master` must edit their `config.toml`.
- `acknowledgment` no longer requires `message_id`: a successful `auth` is acknowledged whether
  or not the request carried one, because it is the client's only signal that the connection is
  usable.

### Security

- **The generic leak scan runs before the commit exists, not after the push.** It lived only in
  CI, where the earliest it can speak is after the content is on a remote and in that remote's
  reflog; a scanner that reports a published secret has reported an incident rather than
  prevented one. It now runs in `.githooks/pre-commit` over the staged content, read from the
  index rather than the working tree so what is scanned is what would be committed. Fixing the
  gate exposed that both leak gates were blind to a file renamed and modified in the same commit:
  they filtered for added, copied and modified paths, and a rename returned nothing, so the
  denylist gate did not run at all (issue #203).
- **The secret scanner has tests, and two of its ten patterns were the only ones ever exercised.**
  A dead pattern and a live one read identically from the outside: both produce no hits. Every
  pattern is now probed in both directions by a test that enumerates the pattern table itself, so
  a pattern added without a probe fails rather than passing quietly. The scan also stopped
  reporting a clean tree for files it never opened, and now exits 2 for a run that considered
  nothing (issue #199).

- Binding by interface name was unsound. `bnep` devices are numbered in the order PAN links come
  up, so `bnep0` is whichever Bluetooth peer connected first - on a unit paired with more than
  one machine, not necessarily the one the owner meant. With `token` empty by default, that put
  an unauthenticated WebSocket and an HTTPS server on a network nobody chose. An address is a
  network the owner selected; a device name is not.
- A `bind_addresses` value that is present but unusable no longer falls back to the shipped
  default. It resolved to a default that binds an entire subnet, so a caller nulling the key -
  the plausible way to stop the plugin listening - achieved the opposite.
- The wildcard is refused as a configured entry as well as at bind time. `0.0.0.0` parses as a
  valid IPv4 address, so the generic validation path accepted it; a block wider than `/24` is
  refused for the same reason, which is that neither is a network anyone chose.
- The unprompted `stats` / `access_points` / `face_status` burst is no longer sent before a
  client has authenticated, and a connection no longer joins the broadcast set on accept. With
  a token configured, an unauthenticated peer previously received the unit's name, its access
  point list, its face and every push message for the whole `auth_timeout` window simply by
  opening a socket, and could renew that window by reconnecting.
- The authentication deadline is now enforced against a client that sends nothing. It was only
  checked when a frame arrived, so a peer that opened a socket and stayed silent - the case the
  rule exists for - held it open indefinitely.
- Connecting to the HTTPS port and sending nothing used to freeze the whole static server, not
  just that one connection: every other visitor's PWA hung mid-request, and even reloading the
  plugin could not clear it, since the same stall blocked shutdown too. No token guards this
  port, so anyone who could reach it at all could trigger it with a single idle connection.
