#!/usr/bin/env python3
"""Extracts a tag's own section out of `CHANGELOG.md` for the Release body.

`--generate-notes` builds a Release body from pull request titles, which is a
list of what merged, not what an owner deciding whether to update needs to
read. `CHANGELOG.md` is maintained for exactly that reader (SPEC.md 12), so
cutting a release means renaming `## [Unreleased]` to `## [X.Y.Z] -
YYYY-MM-DD` in the commit the tag points at, and this script extracts that
section, headings and all, for `release.yml` (SPEC.md 5.2, issue #179) to
hand to `gh release create --notes-file`. Run in the `checks` job before
anything is built: a tag with nothing to say costs no build minutes and
never touches the job that runs third-party lifecycle scripts.

Usage:
    python .github/release_notes.py <tag> [--changelog PATH] [--output PATH]
        <tag> is the pushed tag, e.g. v0.1.0 or v0.1.0-rc1. Prints the
        section to stdout, or writes it to PATH with --output. The heading
        this looks for carries the tag's version verbatim: v0.3.0-rc1 wants
        `## [0.3.0-rc1] - YYYY-MM-DD`, not `## [0.3.0] - YYYY-MM-DD`.

Exit status: 0 when the section was found and holds at least one entry, 1
when the tag does not match SPEC.md 12's grammar (the same exit
`check_release_version.py` uses for the same reason, since the two run
against the same tag in the same job) or the tag's own section is missing,
has a heading with no date, is present but empty, or an `## [Unreleased]`
section is still present with entries in it (the rename to a dated heading
did not happen), 2 when the changelog cannot be read - a check that could
not run is not a clean result (SPEC.md 13).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import NoReturn

CHECK_RELEASE_VERSION_PATH = Path(__file__).resolve().parent / "check_release_version.py"


def _load_tag_re() -> re.Pattern[str]:
    """`check_release_version.py`'s own `TAG_RE`, loaded from that file by path.

    Not a duplicated regex: the two scripts validate the same tag against
    the same SPEC.md 12 grammar in the same `checks` job, and a copy here
    would be one more place that grammar could drift out of step with the
    script CI actually treats as authoritative for it.
    """
    spec = importlib.util.spec_from_file_location(
        "check_release_version", CHECK_RELEASE_VERSION_PATH
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {CHECK_RELEASE_VERSION_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TAG_RE


TAG_RE = _load_tag_re()

HEADING_RE = re.compile(r"^## ")
UNRELEASED_HEADING_RE = re.compile(r"^## \[Unreleased\]\s*$")
SUBSECTION_RE = re.compile(r"^### ")
# The date, with Keep a Changelog 1.1.0's optional " [YANKED]" mark after it.
DATED_RE = re.compile(r"^ - \d{4}-\d{2}-\d{2}(?: \[YANKED\])?\s*$")


def fail(message: str) -> NoReturn:
    """Exits 2: the changelog cannot be read.

    Distinct from a bad tag or a missing/empty section (exit 1): those are
    this script doing its job, this is it unable to do it (SPEC.md 13).
    """
    print(f"::error::release_notes: {message}", file=sys.stderr)
    raise SystemExit(2)


def refuse(message: str) -> NoReturn:
    """Exits 1: a bad tag, or a changelog that was read but has nothing usable."""
    print(f"::error::release_notes: {message}", file=sys.stderr)
    raise SystemExit(1)


def matches_tag_grammar(tag: str) -> bool:
    """Whether `tag` is `vMAJOR.MINOR.PATCH`, optionally `-`-suffixed (SPEC.md 12).

    Pure and total, so a caller validates before relying on anything
    downstream that assumes the shape holds.
    """
    return TAG_RE.match(tag) is not None


def tag_version(tag: str) -> str:
    """The tag with its leading `v` removed: `v0.1.0-rc1` -> `0.1.0-rc1`.

    Used verbatim as the heading's version: a pre-release tag's section
    stays under its own `-rcN` heading, not the stable version it precedes,
    so the rc section remains in the file as history once the stable one
    follows (SPEC.md 5.2, 12).
    """
    return tag[1:] if tag.startswith("v") else tag


def section_bounds(lines: list[str]) -> list[tuple[int, int]]:
    """The `(start, end)` line ranges of every `## ` heading in `lines`.

    `end` is exclusive: the index of the next `## ` heading, or `len(lines)`
    for the last section. A pure function over already-split text so it can
    be handed a fixture directly.
    """
    starts = [i for i, line in enumerate(lines) if HEADING_RE.match(line)]
    bounds = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        bounds.append((start, end))
    return bounds


def section_has_entry(body: list[str]) -> bool:
    """Whether `body` (a section, heading excluded) holds a `### ` subsection

    with at least one `- ` line under it (SPEC.md 12's own structure rule).
    A section that renamed cleanly but records nothing is not one to publish.
    """
    seen_subsection = False
    for line in body:
        if SUBSECTION_RE.match(line):
            seen_subsection = True
            continue
        if seen_subsection and line.lstrip().startswith("- "):
            return True
    return False


def extract_section(text: str, tag: str) -> str:
    """The tag's own `## [X.Y.Z] - YYYY-MM-DD` section, headings included.

    Trailing blank lines are trimmed. Raises `SystemExit(1)` (via `refuse`)
    when the section is missing, has a heading with no date, holds no
    entry, or an `## [Unreleased]` section is still present with entries in
    it.
    """
    version = tag_version(tag)
    bracket_re = re.compile(r"^## \[" + re.escape(version) + r"\](.*)$")
    lines = text.splitlines()
    bounds = section_bounds(lines)

    for start, end in bounds:
        if UNRELEASED_HEADING_RE.match(lines[start]) and section_has_entry(lines[start + 1 : end]):
            refuse(
                "an ## [Unreleased] section is still present with entries in "
                "it: the rename to a dated heading did not happen"
            )

    for start, end in bounds:
        match = bracket_re.match(lines[start])
        if match is None:
            continue
        if not DATED_RE.match(match.group(1)):
            refuse(
                f"the heading for {tag} is missing its date: expected "
                f"## [{version}] - YYYY-MM-DD, optionally followed by [YANKED]"
            )
        body = lines[start + 1 : end]
        if not section_has_entry(body):
            refuse(f"the section for {tag} has no entry under it")
        section = lines[start:end]
        while section and section[-1].strip() == "":
            section.pop()
        return "\n".join(section) + "\n"

    refuse(f"no ## [{version}] - YYYY-MM-DD section found for tag {tag}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the pushed tag, e.g. v0.1.0 or v0.1.0-rc1")
    parser.add_argument(
        "--changelog",
        default="CHANGELOG.md",
        help="path to the changelog to extract from (default: CHANGELOG.md)",
    )
    parser.add_argument(
        "--output",
        help="write the extracted section here instead of printing it to stdout",
    )
    args = parser.parse_args(argv)

    if not matches_tag_grammar(args.tag):
        refuse(
            f"tag {args.tag!r} does not match SPEC.md 12's grammar "
            "vMAJOR.MINOR.PATCH, optionally followed by a hyphenated "
            "pre-release suffix (for example v0.1.0 or v0.1.0-rc1)."
        )

    changelog_path = Path(args.changelog)
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except OSError as err:
        fail(f"cannot read {changelog_path}: {err}")

    notes = extract_section(text, args.tag)

    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
    else:
        print(notes, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
