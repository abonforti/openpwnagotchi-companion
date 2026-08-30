"""`CHANGELOG.md`'s structure rule (SPEC.md 12, "The structure is checked,
because it had already gone wrong twice").

SPEC.md 12 states the rule directly, in the bullet added for this change:

    Under a version heading, each type heading appears at most once, every
    type heading is one of Keep a Changelog's six, and they appear in that
    specification's own order.

and names the two defects that had already landed and motivate checking it
at all: `Unreleased` carried two separate `### Changed` blocks, and six
entries that add something sat under the second of them.

Keep a Changelog (https://keepachangelog.com/en/1.1.0/, named by SPEC.md 12
itself) fixes the six type headings and their canonical order: Added,
Changed, Deprecated, Removed, Fixed, Security.

Where the check lives, and why it is not imported from elsewhere
------------------------------------------------------------------
SPEC.md 13.2 names `.github/check_shipped_files.py` as the implementation of
the shipped-inventory gate that `tests/test_shipped_files.py` drives. SPEC.md
12's changelog bullet names no such script - unlike that rule, this one has
not been given a home anywhere in the repository yet. Rather than invent an
implementation file under `.github/` on the test author's own authority (out
of scope for this task, and the kind of guess CLAUDE.md asks not to make),
the parsing and validation this rule needs is written here, as a small,
self-contained module-level function, the same way `tests/test_ci_gates.py`
extracts and interprets shell script structure inline rather than importing
a checker that does not exist.

This module is nonetheless a gate, and it is worth being exact about how.
CI's plugin-tests job runs `pytest` from the repository root, which collects
this file, so a changelog that breaks the rule fails a required check. What
is not here is a script under `.github/` that a person can run against an
arbitrary file, which is what the other hygiene rules have and what this one
would want if it ever needed to be invoked outside the suite.

Two kinds of assertion live here:

- Against the real `CHANGELOG.md`, read from the repository root: a
  regression proof that the hand restructuring this change made actually
  satisfies the rule today, and, since CI collects this file, the thing that
  keeps it true.
- Against synthetic fixture text, reproducing the shape of the two named
  historical defects (two `### Changed` blocks under one version; a type
  heading that is not one of the six; headings out of Keep a Changelog's
  order) and confirming the rule rejects each independently, and a
  compliant document is accepted.

What this module does not assert, three different ways, since "not
mentioned above" would blur three different reasons into one:

- **Deliberately out of scope, because SPEC.md says so.** SPEC.md 12's
  second new bullet - "what lands in the changelog is what a person
  installing this would want to know" - is explicitly an author's
  judgement call ("the judgement is the author's"), and SPEC.md itself
  declines to gate it (issue #179 is named as carrying that open
  question). Whether a given entry sits under the *right* type heading
  (the second half of the historical `Changed`/`Added` defect, "entries
  filed under the wrong one") is exactly that kind of judgement about
  content, not structure: nothing here reads the entries under `Changed`
  in today's `CHANGELOG.md` and asserts any of them should say `Added`
  instead. What is checked is the structural shape a wrongly-filed entry
  happens to have produced last time - a second `### Changed` heading -
  which is observable without reading a single entry's meaning.
- **Checked, and deliberately strict rather than lenient.** A heading like
  `### Added (breaking)` is treated as not one of the six, full stop -
  `_TYPE_HEADING_RE` does not strip a parenthetical or otherwise try to
  recover the canonical name from a near-miss. SPEC.md 12 does not say
  whether trailing text should be tolerated; requiring an exact match is
  the reading that fails a near-miss loudly instead of silently accepting
  a heading spelled a new way each time.
- **Cannot happen yet, so left untested rather than handled.**
  `_VERSION_HEADING_RE` only recognises `## ...` as a version heading,
  Keep a Changelog's own level; a document with version headings at some
  other level is not a shape `CHANGELOG.md` or any fixture here has ever
  taken, and there is nothing in SPEC.md 12 to say what should happen if
  one did. This is not the same claim as the two above: it is not a
  judgement call being deferred and not a strictness decision already
  made, it is a case this parser has never been asked to handle because
  the input that would exercise it does not exist.

Fenced code blocks are a fourth case, and unlike the three above it *is*
handled: a line inside a triple-backtick fence (` ``` `) is never read as
either heading, opening or closing, because `CHANGELOG.md` already
discusses `bind_addresses`, `custom_plugins` and `config.toml`, and a
`## ` or `### ` inside a `toml` or `sh` example is one edit away rather
than hypothetical. A fenced `## ` failing open - silently starting a
phantom version section and hiding a real duplicate that follows it in
the section it should have stayed in - is the sharper failure mode, and is
exercised below alongside a fenced `### `. A triple-tilde fence (`~~~`) is
not recognised, the same "cannot happen yet" reasoning as the heading
level above: neither `CHANGELOG.md` nor Keep a Changelog's own convention
uses one.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

# Keep a Changelog's six type headings, in the specification's own order.
KEEP_A_CHANGELOG_TYPES = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
KEEP_A_CHANGELOG_ORDER = {name: index for index, name in enumerate(KEEP_A_CHANGELOG_TYPES)}

_VERSION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_TYPE_HEADING_RE = re.compile(r"^###\s+(.+?)\s*$")
# A fence delimiter line: three or more backticks, optionally indented,
# optionally followed by a language tag (` ```toml `). Both the opening and
# the closing line match this, so a plain toggle on every match is correct.
# Only backtick fences are recognised; see the module docstring for why a
# `~~~` fence is not.
_FENCE_DELIMITER_RE = re.compile(r"^\s*```")


def parse_version_sections(text):
    """Split changelog text into version sections.

    A version section starts at a line matching `## ...` (Keep a Changelog's
    version heading level) and runs to the next such line or the end of the
    text. The document title (`# Changelog`, a single `#`) and any text
    before the first `##` heading form no section and are not inspected.

    A line inside a fenced code block - between a `` ``` `` line and the
    `` ``` `` that closes it - is never read as a heading, whether it looks
    like a version heading or a type heading. Without this, a `## ` inside a
    `toml` or `sh` example would open a phantom version section and every
    real heading after it, including a real duplicate, would be attributed
    to that phantom section instead of the one it actually belongs to -
    the fail-open direction, and the one that matters, since it hides a
    duplicate rather than manufacturing a spurious one.

    Returns a list of `(version_heading, [type_heading, ...])`, where the
    inner list is every `### ...` heading found in that section's body
    outside any fence, in the order they appear - duplicates included,
    exactly as written, since detecting a duplicate is the caller's job,
    not this parser's.
    """
    sections = []
    current_heading = None
    current_types = None
    in_fence = False
    for line in text.splitlines():
        if _FENCE_DELIMITER_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        version_match = _VERSION_HEADING_RE.match(line)
        if version_match:
            if current_heading is not None:
                sections.append((current_heading, current_types))
            current_heading = version_match.group(1)
            current_types = []
            continue
        if current_heading is None:
            continue
        type_match = _TYPE_HEADING_RE.match(line)
        if type_match:
            current_types.append(type_match.group(1))
    if current_heading is not None:
        sections.append((current_heading, current_types))
    return sections


def find_structure_violations(text):
    """Every way a changelog's structure can break SPEC.md 12's rule.

    Returns a list of human-readable violation strings, one per broken
    version section per broken rule; an empty list means the whole document
    is structurally compliant. Each of the three checks is independent, so a
    single version section can produce more than one violation (a duplicate
    heading that is also not one of the six, for instance) - the caller who
    only wants a yes/no answer can just check the list is empty.
    """
    violations = []
    for version_heading, type_headings in parse_version_sections(text):
        # Rule: every type heading is one of Keep a Changelog's six.
        for heading in type_headings:
            if heading not in KEEP_A_CHANGELOG_TYPES:
                violations.append(
                    f"{version_heading!r}: {heading!r} is not one of Keep a "
                    f"Changelog's six type headings {KEEP_A_CHANGELOG_TYPES}"
                )

        # Rule: each type heading appears at most once under a version
        # heading. Checked over every heading found, including one that
        # already failed the "one of six" check above, since a duplicate
        # unknown heading is still a duplicate.
        seen = set()
        for heading in type_headings:
            if heading in seen:
                violations.append(
                    f"{version_heading!r}: {heading!r} appears more than once"
                )
            seen.add(heading)

        # Rule: the headings present appear in Keep a Changelog's own
        # order. Checked only over headings that are one of the six -
        # an unknown heading has no position in that order to violate,
        # and is already reported by the check above.
        known_order_positions = [
            KEEP_A_CHANGELOG_ORDER[heading]
            for heading in type_headings
            if heading in KEEP_A_CHANGELOG_ORDER
        ]
        if known_order_positions != sorted(known_order_positions):
            violations.append(
                f"{version_heading!r}: type headings are not in Keep a "
                f"Changelog's order {KEEP_A_CHANGELOG_TYPES}, got "
                f"{[h for h in type_headings if h in KEEP_A_CHANGELOG_ORDER]}"
            )

    return violations


# ---------------------------------------------------------------------------
# Regression: the real CHANGELOG.md, as restructured by hand for this change.
# ---------------------------------------------------------------------------


def _read_real_changelog():
    assert CHANGELOG_PATH.is_file(), f"expected a changelog at {CHANGELOG_PATH}"
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def test_real_changelog_has_version_sections_to_check():
    # Guards every assertion below: an empty parse (no `## ` heading found
    # at all) would make every subsequent check vacuously true instead of
    # actually inspecting anything.
    sections = parse_version_sections(_read_real_changelog())
    assert sections, "expected at least one version section in CHANGELOG.md"


def test_real_changelog_is_structurally_compliant():
    """The whole rule, against the file as it stands after the hand fix
    described in SPEC.md 12: no duplicate type heading under any version,
    every type heading one of the six, in the specification's order."""
    violations = find_structure_violations(_read_real_changelog())
    assert violations == [], (
        "CHANGELOG.md violates SPEC.md 12's structure rule:\n" + "\n".join(violations)
    )


def _real_unreleased_type_headings():
    """The type headings under the real `[Unreleased]` section, found by
    scanning the parsed section list rather than collapsing it into a
    `dict`. `dict(parse_version_sections(...))` would keep only the last
    entry for any heading string that repeats - silently discarding the
    exact duplicate-section case this module exists to catch, if a document
    ever had two version headings that read the same. Asserting there is
    exactly one match also catches a version heading that happens to
    contain the substring "Unreleased" more than once, rather than
    picking one arbitrarily.
    """
    sections = parse_version_sections(_read_real_changelog())
    matches = [type_headings for heading, type_headings in sections if "Unreleased" in heading]
    assert len(matches) == 1, (
        f"expected exactly one version section matching 'Unreleased', found {len(matches)}"
    )
    return matches[0]


def test_real_changelog_unreleased_section_has_no_duplicate_type_heading():
    """The specific historical defect named in SPEC.md 12: `Unreleased`
    carried two separate `### Changed` blocks. Checked directly against the
    `Unreleased` section by name, not only through the whole-document sweep
    above, so a regression there is pinned even if some other, unrelated
    check in this module were ever loosened."""
    type_headings = _real_unreleased_type_headings()
    assert len(type_headings) == len(set(type_headings)), (
        f"[Unreleased] repeats a type heading: {type_headings}"
    )


# The type headings [Unreleased] carries today, pinned by hand rather than
# read from the file at test time: reading it from the same
# `parse_version_sections` call the assertion below checks would make the
# check trivially true no matter what the file says, since the parser
# would simply report back whatever is there, deleted heading included.
# This is a snapshot, and it is meant to need updating whenever a type
# heading is added to or removed from [Unreleased] - that update is the
# signal a reviewer reads the change, which is the whole point of pinning
# it rather than only asserting "non-empty" (see below for why "non-empty"
# alone does not suffice).
TODAYS_UNRELEASED_TYPE_HEADINGS = ("Added", "Changed", "Security")


def test_real_changelog_unreleased_section_has_the_type_headings_it_has_today():
    """SPEC.md 12 names its own motivation directly: "the whole of the
    frontend came to be missing from a changelog that had a section for
    it." The structural rule as SPEC.md 12 states it - duplicate, unknown,
    and out-of-order headings - does not by itself require any heading to
    be present, so deleting `### Added` and everything under it, while
    `### Changed` and `### Security` stay put, leaves the section
    non-empty and would pass a check that only asked "is this list
    non-empty" - which is not a hypothetical weaker test, it is the first
    one this test replaces. Against a scratch copy with `### Added` and its
    entries deleted, the non-emptiness version stayed green, because
    `Changed` and `Security` are still there; this one fails with
    `['Changed', 'Security'] != ['Added', 'Changed', 'Security']`. Pinning
    the exact set, not just its non-emptiness, is what SPEC.md 12's own
    phrasing asks for ("[Unreleased] carries the type headings it carries
    today") and what actually catches that mutation, since the resulting
    set differs from today's.
    """
    assert _real_unreleased_type_headings() == list(TODAYS_UNRELEASED_TYPE_HEADINGS), (
        "[Unreleased] in CHANGELOG.md no longer carries the type headings "
        f"it carried when this test was written ({TODAYS_UNRELEASED_TYPE_HEADINGS}), "
        f"got {_real_unreleased_type_headings()}. If a type heading was "
        "deliberately added, removed, or reordered under [Unreleased], "
        "update TODAYS_UNRELEASED_TYPE_HEADINGS to match; if not, this is "
        "the failure mode SPEC.md 12 names as its motivation: 'the whole "
        "of the frontend came to be missing from a changelog that had a "
        "section for it'"
    )


# ---------------------------------------------------------------------------
# The rule itself, against synthetic fixtures. Fixture content is invented
# and unrelated to any real release; only the structural shape matters.
# ---------------------------------------------------------------------------

COMPLIANT_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Something new.

### Changed

- Something adjusted.

### Security

- Something hardened.

## [0.1.0]

### Added

- The first release.
"""


def test_compliant_fixture_has_no_violations():
    # A control: proves the checker does not flag a document with no
    # defect at all, so a "violations found" result elsewhere in this
    # module is evidence of the fixture's defect and not of an
    # over-eager checker.
    assert find_structure_violations(COMPLIANT_CHANGELOG) == []


DUPLICATE_CHANGED_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- The frontend views landed.

### Changed

- The rebind interval is clamped.

### Changed

- The Wi-Fi view sorts by signal.
"""


def test_duplicate_type_heading_under_one_version_is_detected():
    """SPEC.md 12: "each type heading appears at most once". Reproduces the
    exact named defect: `Unreleased` carrying two separate `### Changed`
    blocks."""
    violations = find_structure_violations(DUPLICATE_CHANGED_CHANGELOG)
    assert any("Changed" in v and "more than once" in v for v in violations), violations


UNKNOWN_TYPE_HEADING_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Something new.

### Notes

- Something that is not one of the six.
"""


def test_type_heading_outside_the_six_is_detected():
    """SPEC.md 12: "every type heading is one of Keep a Changelog's six"."""
    violations = find_structure_violations(UNKNOWN_TYPE_HEADING_CHANGELOG)
    assert any("Notes" in v and "not one of" in v for v in violations), violations


OUT_OF_ORDER_CHANGELOG = """\
# Changelog

## [Unreleased]

### Security

- Something hardened.

### Added

- Something new.
"""


def test_out_of_order_type_headings_are_detected():
    """SPEC.md 12: type headings "appear in that specification's own
    order". `Security` (last of the six) before `Added` (first) violates
    it even though neither heading is duplicated and both are valid on
    their own."""
    violations = find_structure_violations(OUT_OF_ORDER_CHANGELOG)
    assert any("not in Keep a Changelog's order" in v for v in violations), violations


SAME_TYPE_ACROSS_DIFFERENT_VERSIONS_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Something not yet released.

## [0.1.0]

### Added

- Something released.
"""


def test_same_type_heading_in_two_different_versions_is_not_a_violation():
    """The "at most once" rule is scoped to a single version heading, not to
    the whole document: `### Added` legitimately recurs once per release as
    the changelog grows. A checker that flattened all sections together
    would wrongly reject every changelog with more than one version."""
    assert find_structure_violations(SAME_TYPE_ACROSS_DIFFERENT_VERSIONS_CHANGELOG) == []


def test_duplicate_and_unknown_heading_in_the_same_section_are_both_reported():
    """The two structural checks are independent: a section with both
    defects at once must not have one violation masked by the other."""
    text = """\
# Changelog

## [Unreleased]

### Changed

- First.

### Oddity

- Second.

### Changed

- Third.
"""
    violations = find_structure_violations(text)
    assert any("Changed" in v and "more than once" in v for v in violations), violations
    assert any("Oddity" in v and "not one of" in v for v in violations), violations


# ---------------------------------------------------------------------------
# Fenced code blocks: a `## ` or `### ` inside one must never be read as a
# real heading, in either direction (spurious violation, or - the sharper
# failure - a hidden real duplicate).
# ---------------------------------------------------------------------------

FENCED_VERSION_HEADING_HIDES_REAL_DUPLICATE_CHANGELOG = """\
# Changelog

## [Unreleased]

### Changed

- First entry, filed correctly.

```toml
# example config.toml excerpt
## this looks like a version heading but is inside a fence
some_key = "a value that exists only in this fixture"
```

### Changed

- Second entry: the real duplicate, which a parser fooled by the fenced
  `## ` above would attribute to a phantom section instead of this one.
"""


def test_fenced_version_heading_does_not_hide_a_real_duplicate():
    """The sharper of the two fence failures: a `## ` inside a fence must
    not open a phantom version section, because doing so would silently
    move every heading after it - including a real duplicate - out of the
    section it actually belongs to, which fails open exactly the way
    SPEC.md 12's own historical defect did."""
    violations = find_structure_violations(FENCED_VERSION_HEADING_HIDES_REAL_DUPLICATE_CHANGELOG)
    assert any("Changed" in v and "more than once" in v for v in violations), violations


FENCED_TYPE_HEADING_CHANGELOG = """\
# Changelog

## [Unreleased]

### Added

- Something new.

```markdown
### Not Really A Heading
```
"""


def test_fenced_type_heading_is_not_read_as_a_heading():
    """A `### ` inside a fence must not be read as a type heading either -
    the milder direction of the same rule, checked separately so a fix
    that only handles `## ` (the sharper case above) is not mistaken for
    handling both."""
    assert find_structure_violations(FENCED_TYPE_HEADING_CHANGELOG) == []
