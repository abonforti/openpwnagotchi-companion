# Contributing

Thanks for looking. This is a small project with a few rules that are stricter than usual, and
they exist for reasons worth reading before you write code.

## The short version

- The entire repository is in **English**.
- `docs/schemas/` is the wire format. Change it there first, never in generated files.
- You may only use pwnagotchi symbols listed in `SPEC.md` §11. If it is not listed, ask.
- Every change ships with its tests.
- Work lands through a pull request against `master`.

## Read SPEC.md first

[`SPEC.md`](SPEC.md) is the build specification: what the plugin does, what the protocol is,
what is decided and no longer up for discussion (§0, the Decisions Ledger), and what is pinned
about pwnagotchi's internals (§11). It is long, and it is the reason this project can be worked
on by people, and by agents, without each one rediscovering the same traps.

If you find SPEC ambiguous, that is a bug in SPEC. Say so in the issue or the pull request
rather than resolving it silently in code: an ambiguity resolved in code is a decision nobody
else can see.

## English only

Code, comments, docstrings, log strings, documentation, commit messages, issue and pull request
text. All of it.

This is not a preference about language. The project depends on being readable by contributors
who do not share a first language with the author, and a codebase that mixes two languages is
one where half the comments are invisible to half the readers.

It is enforced by review, not by CI. A word-list check was tried and removed: it produced more
false positives than findings, because words like `serve`, `come`, `fare`, `solo` and `prima`
are English too.

Two related conventions: no emojis anywhere, and no em-dashes in prose.

## The protocol is schema-first

`docs/schemas/*.json` is the single authoritative definition of every message on the wire
(SPEC decision D15). Everything else follows from it:

- `frontend/src/lib/protocol.ts` is **generated** by `tools/gen-protocol-types.mjs`. Never edit
  it by hand; your change will be overwritten and CI will catch the drift.
- `docs/PROTOCOL.md` documents framing and lifecycle rules a schema cannot express. It
  deliberately does not restate the schemas.
- The plugin validates against the schemas in its tests.

To change the protocol: edit the schema, regenerate the types, update both sides, update the
tests. In that order.

```sh
node tools/gen-protocol-types.mjs           # regenerate
node tools/gen-protocol-types.mjs --check   # verify in sync, as CI does
python3 .github/check_schemas.py            # validity and framing invariants
```

## Do not invent pwnagotchi APIs

`SPEC.md` §11 is an allowlist. Every pwnagotchi attribute, method and bettercap field the plugin
may touch is listed there with the file and line that proves it exists. §11.1 lists symbols that
look plausible and are wrong.

**If a symbol is not in §11, you may not use it.** Open an issue, or verify it against the
[jayofelony fork](https://github.com/jayofelony/pwnagotchi) and add it to the table with its
evidence in the same pull request.

This rule exists because the project it forks from is full of calls that do not do what they
appear to. `agent.mode = "manual"` looks like it switches mode; it sets a label and nothing
else. `agent.reboot()` does not exist. `agent.access_points` is not the attribute you want. Each
of those was in the original and each was found by checking rather than by testing, because they
fail quietly.

## Tests

Every change ships with its tests, and a task is done when they pass.

```sh
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest
```

The suite needs no pwnagotchi installation: `tests/fakes/pwnagotchi_stub/` stands in for it, and
exposes exactly the symbols §11 pins - so a test that needs an unpinned symbol fails to import,
which is the point.

Two conventions that are load-bearing:

**Tests are written against the specification, not against the implementation.** Where practical
they are written by someone who has not read the code being tested. This is not ceremony. The
last time it was done here, tests written blind from SPEC found three defects that a careful
review of the same diff had passed, including a data disclosure and a rule that SPEC explicitly
warned about. Tests written by reading the code tend to encode the same misunderstanding twice.

**A test you have not seen fail is a test you do not know works.** When you fix a bug, put the
bug back and watch the new test fail before you keep the fix.

If a test contradicts the implementation, do not adjust the test to match. Decide which one is
wrong against SPEC, and if SPEC does not say, settle it there first.

## Pull requests

`master` is protected. Work on a branch and open a pull request.

- Keep it to one subject. A diff large enough to make review a formality is how defects land.
- CI must be green: schemas, generated types in sync, plugin tests, secret scan, CodeQL.
- Explain **why** in the description, not just what. The what is in the diff.
- Commits are squashed on merge, so the pull request title and description become the permanent
  record. Write them for someone reading `git log` in a year.

Commits from the maintainer are GPG-signed. Contributor commits need not be.

## Comments

Comment the reason, not the mechanics. `# increment the counter` above `count += 1` is noise.
The comments worth writing are the ones that stop the next person from "simplifying" something
load-bearing: why `basicConstraints` is critical, why a leading-zero octet is rejected, why the
websockets server has to be built on the loop rather than merely awaited there.

Match the surrounding style. Do not reformat code you are not otherwise changing.

## Security

Never commit private keys, tokens, certificates or a pwnagotchi whitelist. `.gitignore` covers
the obvious names and `.github/check_secrets.py` runs in CI, but neither is a substitute for
looking.

If you find a vulnerability, report it privately through
[GitHub's private vulnerability reporting](https://github.com/abonforti/openpwnagotchi-companion/security/advisories/new)
rather than opening a public issue. See [SECURITY.md](SECURITY.md).

## Issues

Anything worth mentioning is worth an issue: a defect, a gap, a dead link, a TODO left behind.
Group related work into one issue rather than filing near-duplicates, label it, and give it
acceptance criteria someone else could check.

## Licence

GPL-3.0, inherited from the project this forks. By contributing you agree your work is licensed
the same way.
