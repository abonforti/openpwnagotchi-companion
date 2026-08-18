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
  itself added in this same unreleased cycle and has never been in a tagged version.
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
