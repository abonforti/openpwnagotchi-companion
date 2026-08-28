# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is in `0.x`: the WebSocket contract is **not** stable, and a breaking change to it
bumps the minor version and appears under `Changed` or `Removed`. See `SPEC.md` §12. Version
`1.0.0` will be cut only once the v1 feature set is complete and the protocol has run end to end
against real hardware.

## [Unreleased]

### Changed

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

### Security

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

### Added

- **A configuration key the plugin does not know is named at `WARNING`, once, at load.** The
  message gives the key and the set of keys that are accepted, and the plugin comes up on its
  defaults as before. Nothing was said until now, so `bind_address` for `bind_addresses` - a
  singular that reads correctly and binds nothing - looked exactly like a working configuration.
  `enabled` is pwnagotchi's own key and never warns, and the withdrawn `interfaces` keeps its own
  message from the breaking change above rather than being reported twice. A block with nothing
  but valid keys logs nothing at all (issue #137).
- `tools/gen-ca.sh` and `tools/gen-cert.sh`: a private CA and an `iPAddress`-SAN server
  certificate, non-interactive and idempotent, encoding the three constraints iOS enforces
  silently (critical `basicConstraints`, IP SANs and never `DNS:`, validity under 825 days).
- `tests/test_integration_ws.py`: a real WSS server and a real client over TLS material issued
  by those scripts, driving every command end to end.
- `tests/tools/test_certs.py`: the certificate scripts driven with `subprocess` and inspected
  with `openssl`, in pytest rather than bats.
- `keepalive` is now actually emitted, every `keepalive_interval` seconds (default 20). It was in
  the schema and in `docs/PROTOCOL.md` but nothing sent it. The `0`-disables behaviour described
  here was withdrawn later in this same unreleased cycle; see `Changed` above.
- `.github/labels.json` and `.github/sync_labels.sh`: the label taxonomy as a file, and the
  script that applies it.

### Changed

- `acknowledgment` no longer requires `message_id`: a successful `auth` is acknowledged whether
  or not the request carried one, because it is the client's only signal that the connection is
  usable.

- Build specification (`SPEC.md`), with a pinned-facts allowlist of every pwnagotchi symbol the
  plugin may use, verified against jayofelony v2.9.5.6.
- WebSocket contract as JSON Schema (`docs/schemas/`), the single source of truth for the wire
  format, plus `docs/PROTOCOL.md` for the framing and lifecycle rules a schema cannot express.
- `tools/gen-protocol-types.mjs`, which generates `frontend/src/lib/protocol.ts` from the
  schemas and can verify the two are in sync.
- Repository scaffold: `README.md`, `SECURITY.md`, GPL-3.0 `LICENSE`, `.gitignore`.
- Local quality gates: `.githooks/pre-commit` (leak scan against an out-of-repo denylist) and
  `.githooks/pre-push` (refuses a direct push to `master`).
- Project agent definitions in `.claude/agents/` for implementation, test authoring, review, QA
  and security auditing.
