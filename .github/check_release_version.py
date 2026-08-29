#!/usr/bin/env python3
"""Checks the tag `release.yml` is building against the two version strings.

`plugin/companion.py`'s `__version__`, `frontend/package.json`'s `version` and
the tag with its leading `v` removed must all agree (SPEC.md 12, 5.2) before
anything is built or published. This used to be a comparison written straight
into a `run:` block, and a mutation of the pre-release condition to its exact
opposite passed every test that block had, because a workflow-as-data test can
only assert that a `case` mentioning the tag sits near the word `prerelease` -
it cannot tell a correct condition from a backwards one. The rule this script
exists to satisfy is the one SPEC.md 4.5.1.1 states for views and SPEC.md 10.7
states for coverage, in a third place: the part that computes goes where a
test can drive it directly, not only observe it through a shell.

The three-way comparison and the pre-release decision are both plain functions
below, importable and callable without a subprocess. `main()` is the thin
shell around them that a workflow step calls, and the only part a test cannot
reach without going through the process.

Usage:
    python .github/check_release_version.py <tag>
        <tag> is the pushed tag, e.g. v0.1.0 or v0.1.0-rc1. Reads
        plugin/companion.py and frontend/package.json from this checkout.

Exit status: 0 when the tag matches SPEC.md 12's grammar and all three
versions agree, 1 when the tag does not match that grammar or one or more
versions disagree (each named on stderr), 2 when a file cannot be read or
parsed at all - a check that could not run is not a clean result (SPEC.md
13). The distinction between 1 and 2 is for whoever reads the log: the
workflow step that calls this script fails on either, because both mean the
release cannot proceed, and it does not need to tell them apart to do that.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PATH = ROOT / "plugin" / "companion.py"
FRONTEND_PACKAGE_PATH = ROOT / "frontend" / "package.json"

# Matches the exact assignment SPEC.md 2.1 describes: a plain string literal,
# not an f-string or a name built from parts, because a version a checker
# cannot read is a version nobody else can trust either.
VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)

# SPEC.md 12: vMAJOR.MINOR.PATCH, with an optional pre-release suffix
# introduced by a hyphen, and nothing else. The workflow trigger is `v*`,
# which matches `v0.1.0.4` and `vwip` just as happily as an actual version, so
# this is checked before anything else this script does. \Z, not a trailing
# $: in Python, $ also matches just before a trailing newline, so a value
# ending "\nanything" - unreachable from a git ref, but this is an exported
# total predicate a test can call with anything - would pass with a bare $.
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?\Z")


def fail(message: str) -> NoReturn:
    """Exits 2: a file this check needs is missing or unreadable.

    Distinct from a version mismatch (exit 1, SPEC.md 5.2): that is the check
    doing its job, this is the check unable to do it. Still an `::error::`
    annotation, not a bare line: "the check could not run" is not a quieter
    finding than "the versions disagree", and it must not read as one in the
    Actions UI.
    """
    print(f"::error::check_release_version: {message}", file=sys.stderr)
    raise SystemExit(2)


def plugin_version(source: str) -> str | None:
    """`__version__` out of `plugin/companion.py`'s own source text.

    Takes the text rather than a path so a test can hand it a fixture without
    touching the real file (issue #128 review, following check_pinned_facts.py).
    """
    match = VERSION_RE.search(source)
    return match.group(1) if match else None


def frontend_version(package_json: str) -> str | None:
    """`version` out of `frontend/package.json`'s own text.

    `None` for anything that is not valid JSON, not an object, or whose
    `version` is missing or not a string - callers get one signal, "no usable
    version here", rather than three different exceptions to catch.
    """
    try:
        data = json.loads(package_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("version")
    return value if isinstance(value, str) else None


def matches_tag_grammar(tag: str) -> bool:
    """Whether `tag` is `vMAJOR.MINOR.PATCH`, optionally `-`-suffixed.

    Pure and total - never raises - so a caller validates before relying on
    anything downstream that assumes the shape holds, and a test can call it
    directly with whatever string it likes.
    """
    return TAG_RE.match(tag) is not None


def tag_version(tag: str) -> str:
    """The tag with its leading `v` removed: `v0.1.0-rc1` -> `0.1.0-rc1`.

    Assumes the SPEC.md 12 grammar. `release.yml` only ever calls this with a
    tag the `v*` trigger already matched; a tag without the prefix is passed
    through unchanged rather than raised on, so this stays a pure string
    transform a test can call with anything.
    """
    return tag[1:] if tag.startswith("v") else tag


def disagreements(tag: str, plugin: str | None, frontend: str | None) -> list[str]:
    """Every pairwise mismatch among the tag, the plugin and the frontend.

    Named individually, not folded into a single yes/no: whoever reads this is
    mid-release and wants to know which file to edit, not that "a check
    failed" (SPEC.md 5.2). Empty means all three agree.
    """
    version = tag_version(tag)
    problems: list[str] = []

    if plugin is None:
        problems.append("plugin/companion.py has no __version__ = \"...\" assignment")
    elif plugin != version:
        problems.append(
            f"plugin/companion.py __version__ ({plugin}) does not match the tag {tag} ({version})"
        )

    if frontend is None:
        problems.append("frontend/package.json has no string \"version\"")
    elif frontend != version:
        problems.append(
            f"frontend/package.json version ({frontend}) does not match the tag {tag} ({version})"
        )

    if plugin is not None and frontend is not None and plugin != frontend:
        problems.append(
            f"plugin/companion.py __version__ ({plugin}) does not match "
            f"frontend/package.json version ({frontend})"
        )

    return problems


def is_prerelease(version: str) -> bool:
    """SPEC.md 12: any hyphenated suffix marks a pre-release, not only `-rcN`.

    `matches_tag_grammar` has already confirmed a hyphen here can only be the
    one introducing a pre-release suffix, so its mere presence is what this
    checks. A narrower pattern matching `-rcN` specifically would silently
    treat an unrecognised pre-release spelling as stable and publish a
    candidate as the latest release; erring towards pre-release is the safe
    direction, since the cost is a release nobody auto-installs rather than a
    candidate everybody does.
    """
    return "-" in version


def report_prerelease(prerelease: bool) -> None:
    """Writes `prerelease=true`/`false` to `$GITHUB_OUTPUT`, when set.

    The one place this script talks to the shell rather than to a caller: a
    workflow step reads `steps.<id>.outputs.prerelease` to decide the
    `--prerelease` flag on `gh release create`. Silently does nothing outside
    a workflow, so running this by hand does not require the variable.
    """
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"prerelease={'true' if prerelease else 'false'}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the pushed tag, e.g. v0.1.0 or v0.1.0-rc1")
    args = parser.parse_args(argv)

    if not matches_tag_grammar(args.tag):
        print(
            f"::error::tag {args.tag!r} does not match SPEC.md 12's grammar "
            "vMAJOR.MINOR.PATCH, optionally followed by a hyphenated "
            "pre-release suffix (for example v0.1.0 or v0.1.0-rc1).",
            file=sys.stderr,
        )
        return 1

    try:
        plugin_source = PLUGIN_PATH.read_text(encoding="utf-8")
    except OSError as err:
        fail(f"cannot read {PLUGIN_PATH}: {err}")

    try:
        frontend_source = FRONTEND_PACKAGE_PATH.read_text(encoding="utf-8")
    except OSError as err:
        fail(f"cannot read {FRONTEND_PACKAGE_PATH}: {err}")

    plugin = plugin_version(plugin_source)
    frontend = frontend_version(frontend_source)

    problems = disagreements(args.tag, plugin, frontend)
    if problems:
        for problem in problems:
            print(f"::error::{problem}", file=sys.stderr)
        return 1

    version = tag_version(args.tag)
    prerelease = is_prerelease(version)
    print(f"versions agree: {version}")
    print(f"prerelease={'true' if prerelease else 'false'}")
    report_prerelease(prerelease)
    return 0


if __name__ == "__main__":
    sys.exit(main())
