# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`openpwnagotchi-companion`: a free, self-hostable **PWA** companion for pwnagotchi (jayofelony
fork), replacing the paid iOS app "Pwnagotchi Companion" (`BraedenP232/PwnIOS`). Backend is a
hardened GPL fork of that project's plugin `pwnios.py`; frontend is an installable PWA the user
builds and serves from their own Pi over HTTPS with a private CA.

Full build brief and locked decisions: [`SPEC.md`](SPEC.md). Read it before writing code. The
Decisions Ledger (§0) is not to be re-litigated.

## Components

- `plugin/companion.py` — pwnagotchi plugin: WSS (8082) + HTTPS static (8443), bound only to
  tether interfaces, TLS mandatory. Speaks the contract in `docs/schemas/`.
- `frontend/` — Svelte + TypeScript + Vite + `vite-plugin-pwa`, Leaflet map.
- `tools/` — CA/cert generation, protocol type generation, on-Pi install.
- `tests/` — pytest suite for the plugin (no pwnagotchi install needed) and for the
  certificate scripts. Frontend tests live in `frontend/src/__tests__/` (vitest).
- `.github/` — CI (plugin lint, tests, language check, schema sync, frontend build), release
  (dist.tgz), project labels/templates.

## Reference hardware

Development targets a Pi Zero 2 W running jayofelony 2.9.5.6 with a Waveshare 2.13" v3 display
and a PiSugar 3, reached over BT PAN (the `172.20.10.0/28` subnet every iPhone Personal Hotspot
uses) or the USB gadget interface (`10.0.0.2`, desktop only — an iPhone cannot use it). The
`pasv_mode.py` plugin adds a third state PASV between AUTO and MANU; the companion maps it as a
soft dependency.

## Workflow (automatic — do not wait to be asked)

Work lands through a pull request against `master`. For every change, in this order:

1. Branch. `master` is protected locally by `.githooks/pre-push` and on the remote.
2. Implement (`companion-implementer`) and test (`companion-test-author`) as **separate**
   agents. The test author writes from `SPEC.md` and `docs/schemas/` without reading the
   implementation — that independence is the only reason the tests can catch an implementation
   bug rather than mirror it.
3. `companion-qa` runs the gates and reports the real numbers.
4. `companion-reviewer` reviews the diff against §11, §11.1 and §2.14.
5. `companion-security-auditor` audits before **every** commit and before **every** push.
6. Open the pull request only once QA, review and audit are all green.

These agents are invoked as a matter of course. Do not ask permission to run them, and do not
skip one because a change looks small — the small changes are where a leak or an invented API
gets through.

One-time local setup, per clone:

```
git config core.hooksPath .githooks
git config companion.denylist /path/to/your/denylist.txt   # outside this repo
```

## Tickets

**Every problem becomes a GitHub issue.** A defect, a gap, an audit finding, a dead link, a
TODO left behind: if it is worth mentioning, it is worth a ticket. Nothing lives only in a
conversation, because a conversation is not a backlog and neither of us will remember.

Group related work into one issue rather than filing several near-duplicates. Label it, put it
in a milestone if it belongs to one, and give it acceptance criteria that someone else could
check.

**Relaxed until the first test on real hardware.** While the plugin has never run on a device,
a defect found in unmerged work is fixed in place rather than filed: a ticket describing code
that is about to change is bookkeeping, not a backlog. This relaxation ends at the first
end-to-end run on the device, after which the rule above applies without exception.

## Rules

- Present a plan and get explicit approval before non-trivial code. The owner drives plan→code.
- **The entire repository is English.** Code, comments, docstrings, log strings, documentation,
  commit messages, issue and PR text. Enforced by review, not by CI: a word-list check produced
  more false positives than findings, so it was removed rather than tuned into noise.
- Do not guess pwnagotchi APIs, attribute names, or bettercap field names. Every symbol you may
  use is pinned in SPEC.md §11 with its evidence, and §11.1 lists what is forbidden. **If a
  symbol is not in §11, you may not use it** — stop and ask instead of inventing it.
- `docs/schemas/*.json` is the authoritative wire format. `frontend/src/lib/protocol.ts` is
  generated from it and must never be hand-edited.
- Every change ships with its tests (SPEC.md §10). A task is done when its tests pass, not
  before.
- Match existing style. No features beyond SPEC scope. No emojis, no em-dashes in prose.
- Never commit secrets: private keys, CA keys, tokens, or a pwnagotchi whitelist. Extend
  `.gitignore` before creating such a file.
- **Device access is read-only and gated.** How the owner's device is reached is deliberately
  not recorded in this repository. Ask; do not look for it here, and never write it down here.
  No writes to the device under any circumstances.
- **Never commit anything about the owner's infrastructure**: hostnames, SSH aliases, jump
  hosts, private addresses, network topology, machine names. This repository is public and this
  rule has no exceptions. The `companion-security-auditor` agent gates every commit.
- **Never `git push` without the owner's explicit instruction.** `master` is the principal
  branch and is protected: work happens on a branch and lands through a pull request, never by
  a direct push.
- **Versioning**: SemVer, one version line for the whole project (see SPEC.md §12). Tags are
  `vMAJOR.MINOR.PATCH`, optionally with an `-rcN` suffix. The project is in `0.x`. Milestones are
  named for their deliverable, never after a version number.
- **Every commit is GPG-signed** (SPEC.md §13.1). `commit.gpgsign` is on; never disable it and
  never use `--no-gpg-sign`. If signing fails, report it rather than working around it.
- Public repo: keep it well-structured (labels, milestones, issues, roadmap) per SPEC §6.
