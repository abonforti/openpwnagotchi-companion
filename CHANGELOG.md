# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The project is in `0.x`: the WebSocket contract is **not** stable, and a breaking change to it
bumps the minor version and appears under `Changed` or `Removed`. See `SPEC.md` §12. Version
`1.0.0` will be cut only once the v1 feature set is complete and the protocol has run end to end
against real hardware.

## [Unreleased]

### Security

- The unprompted `stats` / `access_points` / `face_status` burst is no longer sent before a
  client has authenticated, and a connection no longer joins the broadcast set on accept. With
  a token configured, an unauthenticated peer previously received the unit's name, its access
  point list, its face and every push message for the whole `auth_timeout` window simply by
  opening a socket, and could renew that window by reconnecting.
- The authentication deadline is now enforced against a client that sends nothing. It was only
  checked when a frame arrived, so a peer that opened a socket and stayed silent - the case the
  rule exists for - held it open indefinitely.

### Added

- `tools/gen-ca.sh` and `tools/gen-cert.sh`: a private CA and an `iPAddress`-SAN server
  certificate, non-interactive and idempotent, encoding the three constraints iOS enforces
  silently (critical `basicConstraints`, IP SANs and never `DNS:`, validity under 825 days).
- `tests/test_integration_ws.py`: a real WSS server and a real client over TLS material issued
  by those scripts, driving every command end to end.
- `tests/tools/test_certs.py`: the certificate scripts driven with `subprocess` and inspected
  with `openssl`, in pytest rather than bats.
- `keepalive` is now actually emitted, every `keepalive_interval` seconds (default 20, `0`
  disables). It was in the schema and in `docs/PROTOCOL.md` but nothing sent it.
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
