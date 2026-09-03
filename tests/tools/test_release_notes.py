"""`.github/release_notes.py` (SPEC.md 5.2, "The Release body is the
changelog's own entry for that version", issue #179).

The contract this module exercises, mostly as a subprocess (SPEC.md 10.6:
gate scripts are driven as subprocesses):

    .github/release_notes.py <tag> [--changelog PATH] [--output PATH]

prints (or, with `--output`, writes) the tag's own `## [X.Y.Z] - YYYY-MM-DD`
section - headings included, through the line before the next `## ` heading,
trailing blank lines trimmed - and exits 0. The heading carries the tag's
version verbatim, so a pre-release tag `v0.3.0-rc1` is matched against
`## [0.3.0-rc1] - YYYY-MM-DD`, not `## [0.3.0] - YYYY-MM-DD`; a `[YANKED]`
mark after the date is accepted, since Keep a Changelog's own format
sanctions it. Exits 1, with a named message, for a tag outside the grammar
`.github/check_release_version.py` accepts (the same exit that script uses
for the same reason - "the two run against the same tag in the same job"),
or when the tag's own section is missing, has a heading with no date, holds
no entry (no `### ` subsection with at least one `- ` line), or when an
`## [Unreleased]` section is still present carrying entries of its own.
Exits 2 only when the changelog cannot be read at all.

Every fixture changelog here is written from a plain string under
`tmp_path`; the real `CHANGELOG.md` is never read (SPEC.md 10, this task's
own brief). The tag grammar is not retyped: `TAG_RE` is loaded from
`.github/check_release_version.py` the same way
`tests/test_release_workflow.py` already loads it (`_load_check_release_version`),
and `release_notes.py` is checked to be reading that same pattern rather
than a copy of its own, since a copy is exactly the kind of divergence
SPEC.md 5.2 calls out for this pair of scripts sharing one grammar.

`section_bounds` and `section_has_entry` are also driven in-process, one
case each, mirroring how `tests/tools/test_check_release_version.py` drives
`check_release_version.py`'s own pure functions directly: both scripts'
docstrings advertise a pure, importable core, and `main()`'s exit status
alone does not prove `section_bounds` stops where it says it does - only
`extract_section`'s behaviour through the CLI does that, which is why the
happy-path subprocess test below is built around three sections and the
middle one, not two.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "release_notes.py"
CHECK_RELEASE_VERSION_SCRIPT = REPO_ROOT / ".github" / "check_release_version.py"


def load_module(path: Path, name: str):
    """Imports a script by path: neither `.github` module is an importable
    package. Mirrors `tests/tools/test_check_release_version.py`'s own
    `load_module` and `tests/test_release_workflow.py`'s
    `_load_check_release_version`.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rn = load_module(SCRIPT, "release_notes")
crv = load_module(CHECK_RELEASE_VERSION_SCRIPT, "check_release_version")


def run_release_notes(tag, changelog_path=None, *, output_path=None, extra_args=None):
    args = [sys.executable, str(SCRIPT), tag]
    if changelog_path is not None:
        args += ["--changelog", str(changelog_path)]
    if output_path is not None:
        args += ["--output", str(output_path)]
    if extra_args:
        args += extra_args
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


def write_changelog(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The tag grammar is one file's, read, not retyped (SPEC.md 5.2: "the two
# run against the same tag in the same job").
# ---------------------------------------------------------------------------


def test_release_notes_reads_check_release_versions_own_tag_grammar():
    assert rn.TAG_RE.pattern == crv.TAG_RE.pattern, (
        f"release_notes.py's TAG_RE ({rn.TAG_RE.pattern!r}) has drifted from "
        f"check_release_version.py's ({crv.TAG_RE.pattern!r}); SPEC.md 5.2 "
        f"requires the two scripts to validate the same tag against the same "
        f"grammar rather than each carrying its own copy"
    )


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2"])
def test_fixture_bad_tags_are_actually_outside_the_grammar(tag):
    """Sanity check on the fixtures used below: `crv.matches_tag_grammar` is
    the authoritative answer for what SPEC.md 12's grammar accepts, so a bad
    tag used to prove an exit code is verified against it rather than
    assumed by construction.
    """
    assert crv.matches_tag_grammar(tag) is False


# ---------------------------------------------------------------------------
# Happy path: three release sections, the middle one asked for, so a mutant
# that fails to stop extraction at the next `## ` heading pulls in the
# section below it (v0.1.0's own lines) and one that starts too early pulls
# in the section above it (v0.3.0's).
# ---------------------------------------------------------------------------

THREE_VERSION_CHANGELOG = """\
# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.3.0] - 2026-09-01
### Added
- Widget C is now configurable.

### Fixed
- Widget B no longer leaks memory.

## [0.2.0] - 2026-08-15
### Added
- Widget B ships enabled by default.

## [0.1.0] - 2026-07-01
### Added
- Initial release with Widget A.
"""


def test_happy_path_extracts_exactly_the_requested_middle_section(tmp_path):
    changelog = write_changelog(tmp_path, THREE_VERSION_CHANGELOG)
    returncode, stdout, stderr = run_release_notes("v0.2.0", changelog)
    assert returncode == 0, f"stderr={stderr!r}"
    assert stdout == (
        "## [0.2.0] - 2026-08-15\n"
        "### Added\n"
        "- Widget B ships enabled by default.\n"
    ), f"unexpected extraction: {stdout!r}"
    # The neighbouring sections' own lines must not leak into this one.
    assert "Widget C" not in stdout
    assert "Widget A" not in stdout
    assert "0.3.0" not in stdout
    assert "0.1.0" not in stdout


def test_output_flag_writes_the_same_bytes_as_stdout(tmp_path):
    changelog = write_changelog(tmp_path, THREE_VERSION_CHANGELOG)
    returncode, stdout, stderr = run_release_notes("v0.2.0", changelog)
    assert returncode == 0, f"stderr={stderr!r}"

    output_path = tmp_path / "notes.md"
    returncode2, stdout2, stderr2 = run_release_notes(
        "v0.2.0", changelog, output_path=output_path
    )
    assert returncode2 == 0, f"stderr={stderr2!r}"
    assert output_path.read_bytes() == stdout.encode("utf-8"), (
        f"--output must write the identical bytes the plain stdout run produced; "
        f"got {output_path.read_bytes()!r} vs {stdout.encode('utf-8')!r}"
    )


# ---------------------------------------------------------------------------
# Exit 1: the section for the tag is missing.
# ---------------------------------------------------------------------------


def test_missing_section_exits_one_and_names_the_tag(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n## [0.1.0] - 2026-07-01\n### Added\n- Initial release.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.9.0", changelog)
    assert returncode == 1
    assert "0.9.0" in stderr, f"expected the missing tag named in stderr, got {stderr!r}"


# ---------------------------------------------------------------------------
# Exit 1: the section exists but holds no entry.
# ---------------------------------------------------------------------------


def test_section_with_no_subsection_at_all_exits_one(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n## [0.4.0] - 2026-09-02\n\n## [0.1.0] - 2026-07-01\n### Added\n- x.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.4.0", changelog)
    assert returncode == 1
    assert "0.4.0" in stderr


def test_section_with_a_heading_but_no_dash_line_exits_one(tmp_path):
    """A `### ` subsection is present, but it holds no `- ` entry under it -
    the distinct empty shape from having no `### ` heading at all.
    """
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n## [0.4.0] - 2026-09-02\n### Added\n\n"
        "## [0.1.0] - 2026-07-01\n### Added\n- x.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.4.0", changelog)
    assert returncode == 1
    assert "0.4.0" in stderr


# ---------------------------------------------------------------------------
# Exit 1: an `## [Unreleased]` section still carrying entries.
# ---------------------------------------------------------------------------


def test_unreleased_with_entries_exits_one_and_names_unreleased(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n"
        "## [Unreleased]\n"
        "### Added\n"
        "- Something not yet released.\n\n"
        "## [0.1.0] - 2026-07-01\n"
        "### Added\n"
        "- Initial release.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.1.0", changelog)
    assert returncode == 1
    assert "Unreleased" in stderr, (
        f"expected the message to name Unreleased specifically, got {stderr!r}"
    )


def test_empty_unreleased_heading_is_not_a_refusal_when_the_tags_section_is_fine(tmp_path):
    """SPEC.md 5.2 refuses only an `## [Unreleased]` section 'with entries'.
    A bare `## [Unreleased]` heading with nothing under it - the ordinary
    shape right after a release, before anything new has landed - must not
    trip that refusal.
    """
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [0.1.0] - 2026-07-01\n"
        "### Added\n"
        "- Initial release.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.1.0", changelog)
    assert returncode == 0, f"stderr={stderr!r}"
    assert "Initial release" in stdout


# ---------------------------------------------------------------------------
# Pre-release tags: the heading carries the tag's version verbatim
# (SPEC.md 5.2, 12). An rc section is matched against its own `-rcN`
# heading, never against the stable heading of the same numbers.
# ---------------------------------------------------------------------------

RC_CHANGELOG = """\
# Changelog

## [Unreleased]

## [0.3.0-rc1] - 2026-09-03
### Added
- Widget D behind a flag.

## [0.2.0] - 2026-08-15
### Added
- Widget B ships enabled by default.
"""


def test_rc_tag_extracts_its_own_dated_heading(tmp_path):
    changelog = write_changelog(tmp_path, RC_CHANGELOG)
    returncode, stdout, stderr = run_release_notes("v0.3.0-rc1", changelog)
    assert returncode == 0, f"stderr={stderr!r}"
    assert stdout == (
        "## [0.3.0-rc1] - 2026-09-03\n"
        "### Added\n"
        "- Widget D behind a flag.\n"
    ), f"unexpected extraction: {stdout!r}"


def test_rc_tag_does_not_match_the_stable_heading_of_the_same_numbers(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n## [0.3.0] - 2026-09-03\n### Added\n- Widget D ships.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.3.0-rc1", changelog)
    assert returncode == 1, f"stdout={stdout!r} stderr={stderr!r}"
    assert "0.3.0-rc1" in stderr, (
        f"expected the missing rc heading named in stderr, got {stderr!r}"
    )


# ---------------------------------------------------------------------------
# `[YANKED]` (Keep a Changelog 1.1.0) after the date is accepted; a heading
# with no date at all is refused by a message naming the date, not the
# section, as missing.
# ---------------------------------------------------------------------------


def test_yanked_heading_extracts(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n"
        "## [0.5.0] - 2026-09-03 [YANKED]\n"
        "### Fixed\n"
        "- Critical regression in Widget E.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.5.0", changelog)
    assert returncode == 0, f"stderr={stderr!r}"
    assert stdout == (
        "## [0.5.0] - 2026-09-03 [YANKED]\n"
        "### Fixed\n"
        "- Critical regression in Widget E.\n"
    ), f"unexpected extraction: {stdout!r}"


def test_heading_with_no_date_exits_one_and_names_the_date(tmp_path):
    changelog = write_changelog(
        tmp_path,
        "# Changelog\n\n## [0.6.0]\n### Added\n- Something.\n",
    )
    returncode, stdout, stderr = run_release_notes("v0.6.0", changelog)
    assert returncode == 1, f"stdout={stdout!r} stderr={stderr!r}"
    assert "date" in stderr, (
        f"expected the message to say the date is what's missing, got {stderr!r}"
    )


# ---------------------------------------------------------------------------
# Exit 1 for a bad tag (the same exit check_release_version.py uses for the
# same reason); exit 2 only for an unreadable changelog.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["1.2.3", "v1.2"])
def test_bad_tag_exits_one(tmp_path, tag):
    changelog = write_changelog(tmp_path, THREE_VERSION_CHANGELOG)
    returncode, stdout, stderr = run_release_notes(tag, changelog)
    assert returncode == 1, f"tag={tag!r}: stdout={stdout!r} stderr={stderr!r}"


def test_unreadable_changelog_exits_two(tmp_path):
    missing = tmp_path / "does-not-exist.md"
    returncode, stdout, stderr = run_release_notes("v0.1.0", missing)
    assert returncode == 2, f"stdout={stdout!r} stderr={stderr!r}"


# ---------------------------------------------------------------------------
# No traceback ever reaches a user (SPEC.md 13): every failure mode above is
# checked here too.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "changelog_text"),
    [
        ("v0.9.0", "# Changelog\n\n## [0.1.0] - 2026-07-01\n### Added\n- x.\n"),
        ("v0.4.0", "# Changelog\n\n## [0.4.0] - 2026-09-02\n\n"),
        ("v0.6.0", "# Changelog\n\n## [0.6.0]\n### Added\n- Something.\n"),
        ("1.2.3", THREE_VERSION_CHANGELOG),
        ("v1.2", THREE_VERSION_CHANGELOG),
    ],
)
def test_no_traceback_on_any_failure(tmp_path, tag, changelog_text):
    changelog = write_changelog(tmp_path, changelog_text)
    returncode, stdout, stderr = run_release_notes(tag, changelog)
    assert returncode != 0
    assert "Traceback" not in stderr, f"stderr leaked a traceback: {stderr!r}"
    assert "Traceback" not in stdout, f"stdout leaked a traceback: {stdout!r}"


def test_no_traceback_on_unreadable_changelog(tmp_path):
    missing = tmp_path / "does-not-exist.md"
    returncode, stdout, stderr = run_release_notes("v0.1.0", missing)
    assert returncode == 2
    assert "Traceback" not in stderr
    assert "Traceback" not in stdout


# ---------------------------------------------------------------------------
# In-process: `section_bounds` and `section_has_entry`, one case each. The
# subprocess tests above already exercise these exhaustively through
# `extract_section`'s exit status; this is the direct call the module's own
# docstring advertises ("A pure function over already-split text so it can
# be handed a fixture directly"), not a second copy of the exit-status
# coverage.
# ---------------------------------------------------------------------------


def test_section_bounds_stops_each_range_at_the_next_heading():
    lines = [
        "## [Unreleased]",
        "",
        "## [0.1.0] - 2026-01-01",
        "### Added",
        "- x",
    ]
    assert rn.section_bounds(lines) == [(0, 2), (2, 5)]


def test_section_has_entry_requires_a_dash_line_under_a_subsection_heading():
    assert rn.section_has_entry(["### Added", "- x"]) is True
    assert rn.section_has_entry(["### Added"]) is False
