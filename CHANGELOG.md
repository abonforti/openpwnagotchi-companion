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
