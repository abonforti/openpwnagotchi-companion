"""`.github/check_release_version.py` (SPEC.md 5.2, 12; issue #128 follow-up).

SPEC.md 5.2 gained a paragraph after `release.yml`'s pre-release decision was
first tested as shell: "logic inside a `run:` block is logic no test can
reach." A workflow-as-data test could only assert that a `case` mentioning
the tag sat near the word `prerelease`, and that assertion passed just as
happily with the condition inverted - every stable release published as a
release candidate. That mutant is `tests/test_release_workflow.py`'s
`test_publish_step_text_ties_prerelease_status_to_the_tag`; it is gone from
that file now that there is a function to drive directly, and the "prefer
the behavioural test and say so" instruction is why.

Two families of test:

- The pure functions (`plugin_version`, `frontend_version`, `tag_version`,
  `matches_tag_grammar`, `disagreements`, `is_prerelease`) are called
  directly with strings, exactly as SPEC.md 5.2 describes the interface:
  "importable and callable without a subprocess." No file, no workflow, no
  shell. `matches_tag_grammar` is SPEC.md 5.2's newest rule: the trigger is
  `v*`, which matches `v0.1.0.4` and `vwip` as happily as a real version, so
  the tag is validated against SPEC.md 12's grammar before anything else.
- `main()`'s exit status is the one part a pure-function test cannot reach,
  so it is driven as a subprocess against a copied script and a synthetic
  `plugin/`, `frontend/` tree - `tests/tools/test_check_coverage.py`'s own
  pattern, for the same reason: the script resolves its own paths from
  `__file__`, so a copy in `tmp_path/.github/` reads fixtures that belong to
  the test rather than this checkout's real files.

Does not read `.github/workflows/release.yml`; that half of the contract is
`tests/test_release_workflow.py`'s.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "check_release_version.py"


def load_module():
    """Imports the checker by path: `.github` is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crv = load_module()


# ---------------------------------------------------------------------------
# plugin_version - a string, not a path
# ---------------------------------------------------------------------------


def test_plugin_version_reads_the_module_level_literal_assignment():
    source = '__author__ = "someone"\n__version__ = "0.2.0"\n__license__ = "GPL3"\n'
    assert crv.plugin_version(source) == "0.2.0"


def test_plugin_version_is_none_when_the_assignment_is_absent():
    source = '__author__ = "someone"\n__license__ = "GPL3"\n'
    assert crv.plugin_version(source) is None


def test_plugin_version_ignores_an_indented_assignment():
    """SPEC.md 2.1 puts `__version__` at module scope. The regex is anchored
    to the start of a line (`^`), so an assignment indented inside a class -
    a re-assignment to a class attribute, say - is not the module-level fact
    this check exists to read, and must not be picked up as though it were.
    """
    source = 'class Companion:\n    __version__ = "9.9.9"\n'
    assert crv.plugin_version(source) is None


def test_plugin_version_uses_the_first_match_when_more_than_one_exists():
    source = '__version__ = "0.2.0"\nsomething_else = 1\n__version__ = "0.9.9"\n'
    assert crv.plugin_version(source) == "0.2.0"


def test_plugin_version_ignores_an_unquoted_value():
    source = "__version__ = 0.2\n"
    assert crv.plugin_version(source) is None


# ---------------------------------------------------------------------------
# frontend_version - a string, not a path
# ---------------------------------------------------------------------------


def test_frontend_version_reads_the_version_key():
    assert crv.frontend_version('{"name": "companion", "version": "0.2.0"}') == "0.2.0"


def test_frontend_version_is_none_for_invalid_json():
    assert crv.frontend_version("not json at all") is None


def test_frontend_version_is_none_when_the_document_is_not_an_object():
    assert crv.frontend_version('["0.2.0"]') is None


def test_frontend_version_is_none_when_the_key_is_missing():
    assert crv.frontend_version('{"name": "companion"}') is None


def test_frontend_version_is_none_when_the_value_is_not_a_string():
    assert crv.frontend_version('{"version": 0.2}') is None
    assert crv.frontend_version('{"version": null}') is None


# ---------------------------------------------------------------------------
# tag_version - strips exactly one leading v
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v0.1.0", "0.1.0"),
        ("v0.1.0-rc1", "0.1.0-rc1"),
        ("0.1.0", "0.1.0"),  # no leading v: passed through unchanged
        ("vv0.1.0", "v0.1.0"),  # exactly one v stripped, not every leading v
        ("v", ""),
    ],
)
def test_tag_version(tag, expected):
    assert crv.tag_version(tag) == expected


# ---------------------------------------------------------------------------
# matches_tag_grammar - SPEC.md 12's vMAJOR.MINOR.PATCH[-suffix], checked
# "before anything else this script does" per its own docstring: the trigger
# is `v*`, which matches `v0.1.0.4` and `vwip` as happily as an actual
# version (SPEC.md 5.2).
#
# SPEC.md 12 was rewritten after this table was first written, closing a
# divergence between it and SPEC.md 5.2: SPEC.md 12 now says explicitly "The
# grammar accepts any hyphenated suffix ... -rcN remains what to write; the
# wider grammar is what is refused against." So `v0.1.0-beta1` and similar
# are specified as *accepted* by SPEC.md 12 itself, not merely tolerated by
# an implementation reading broader than the spec - `-rcN` is the
# recommended spelling to write, not the only one the grammar admits.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        # Specified by SPEC.md 12: vMAJOR.MINOR.PATCH, optionally suffixed by
        # a hyphen introducing any suffix - `-rcN` is the convention, not a
        # restriction on what the grammar accepts.
        ("v0.1.0", True),
        ("v0.0.1", True),
        ("v1.0.0", True),
        ("v0.1.0-rc1", True),
        ("v0.1.0-rc12", True),
        ("v0.1.0-beta1", True),
        # Ruled out by SPEC.md 12's own examples of what v* wrongly accepts.
        ("v0.1.0.4", False),
        ("vwip", False),
        # Other shapes the grammar excludes.
        ("0.1.0", False),  # no leading v
        ("v0.1", False),  # not three components
        ("v0.1.0.0", False),  # four components
        ("v01.1.0", True),  # leading zeros: not excluded by \d+, not a
        # claim SPEC.md 12 makes either way - recorded as the grammar's
        # actual answer.
        ("v0.1.0-", False),  # hyphen with nothing after it
        ("V0.1.0", False),  # case sensitive: capital V is not v
        ("v0.1.0 ", False),  # trailing whitespace
        (" v0.1.0", False),  # leading whitespace
        ("", False),
    ],
)
def test_matches_tag_grammar(tag, expected):
    assert crv.matches_tag_grammar(tag) is expected


# ---------------------------------------------------------------------------
# is_prerelease - the exact condition test_release_workflow.py's surviving
# mutant (M11) inverted. SPEC.md 12, since being rewritten to agree with
# SPEC.md 5.2, specifies that any hyphenated suffix marks a pre-release, with
# `-rcN` named as the convention for what to actually write - so the split
# below is between versions SPEC.md 12 makes a claim about (those a real tag
# reaching this function could actually carry, having already passed
# `matches_tag_grammar`) and versions it does not, not between "specified"
# and "implementation-only" the way this table read before the rewrite.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        # Specified by SPEC.md 12: MAJOR.MINOR.PATCH with no suffix is stable.
        ("0.1.0", False),
        ("0.0.1", False),
        ("1.0.0", False),
        # Specified by SPEC.md 12: any hyphenated suffix is a pre-release.
        # `-rcN` is the convention for what to write; the versions below are
        # all shapes `matches_tag_grammar` accepts, so all are real input
        # this function is specified to answer for, not only `-rcN` itself.
        ("0.1.0-rc1", True),
        ("0.1.0-rc12", True),
        ("1.0.0-rc1", True),
        ("0.1.0-beta1", True),
        ("0.1.0-rc1-extra", True),  # a suffix may itself contain a hyphen;
        # matches_tag_grammar's suffix class ([0-9A-Za-z.-]+) admits this.
        # Not a claim SPEC.md 12 makes about any real tag: a bare "-" and a
        # trailing "+build" are not a shape a v* tag that already passed
        # `matches_tag_grammar` could ever produce via tag_version(), so
        # these exercise this pure function's own total behaviour on
        # arbitrary input rather than pin anything SPEC.md 12 specifies.
        ("-", True),
        ("0.1.0+build5", False),
        ("", False),
    ],
)
def test_is_prerelease(version, expected):
    assert crv.is_prerelease(version) is expected


# ---------------------------------------------------------------------------
# disagreements - the version check itself, driven with three strings and no
# workflow at all. "Named individually" (SPEC.md 5.2): every assertion below
# checks not just emptiness but which problem strings came back, since a
# check that returns *a* problem when the wrong file is at fault is not the
# same guarantee as one that names the right file.
# ---------------------------------------------------------------------------


def test_disagreements_is_empty_when_all_three_agree():
    assert crv.disagreements("v0.1.0", "0.1.0", "0.1.0") == []


def test_disagreements_is_empty_for_a_pre_release_tag_when_all_three_agree():
    assert crv.disagreements("v0.1.0-rc1", "0.1.0-rc1", "0.1.0-rc1") == []


def test_disagreements_names_only_the_plugin_when_only_the_plugin_disagrees():
    """plugin != tag, but plugin == frontend is impossible here: if the
    plugin disagrees with the tag while the frontend still matches it, the
    plugin and frontend also disagree with each other by transitivity, so
    two problems come back, not one - and this is the case that pins the
    plugin-vs-frontend check's wording specifically.
    """
    problems = crv.disagreements("v0.2.0", "0.1.0", "0.2.0")
    assert len(problems) == 2
    assert any("plugin/companion.py" in p and "0.1.0" in p and "does not match the tag" in p
               for p in problems)
    assert any("does not match" in p and "frontend/package.json" in p and "plugin/companion.py" in p
               for p in problems)


def test_disagreements_names_only_the_frontend_when_only_the_frontend_disagrees():
    problems = crv.disagreements("v0.2.0", "0.2.0", "0.1.0")
    assert len(problems) == 2
    assert any("frontend/package.json" in p and "0.1.0" in p and "does not match the tag" in p
               for p in problems)
    assert any("does not match" in p and "frontend/package.json" in p and "plugin/companion.py" in p
               for p in problems)


def test_disagreements_names_only_the_tag_when_plugin_and_frontend_agree_with_each_other():
    """plugin == frontend but neither matches the tag: exactly two problems,
    one per file's tag comparison, and no plugin-vs-frontend problem - the
    contrast case for the mutant that deletes the tag-vs-plugin or
    tag-vs-frontend check specifically rather than the pairwise one.
    """
    problems = crv.disagreements("v9.9.9", "0.1.0", "0.1.0")
    assert len(problems) == 2
    assert any("plugin/companion.py" in p and "does not match the tag" in p for p in problems)
    assert any("frontend/package.json" in p and "does not match the tag" in p for p in problems)
    assert not any("frontend/package.json" in p and "plugin/companion.py" in p for p in problems)


def test_disagreements_names_all_three_when_all_three_differ():
    """The case that kills a mutant deleting the plugin-vs-frontend check
    specifically: `plugin == tag_version` and `frontend == tag_version`
    holding simultaneously forces `plugin == frontend` by transitivity, so
    there is no reachable input where plugin and frontend disagree while
    both individually match the tag. The equivalent, reachable case is all
    three distinct - three named problems, the third naming the
    plugin/frontend pair specifically rather than being redundant with the
    other two.
    """
    problems = crv.disagreements("v0.3.0", "0.1.0", "0.2.0")
    assert len(problems) == 3
    assert any("plugin/companion.py" in p and "0.1.0" in p and "the tag" in p for p in problems)
    assert any("frontend/package.json" in p and "0.2.0" in p and "the tag" in p for p in problems)
    assert any(
        "plugin/companion.py" in p and "frontend/package.json" in p and "the tag" not in p
        for p in problems
    )


def test_disagreements_reports_a_missing_plugin_version_distinctly():
    """`plugin=None` (SPEC.md 5.2's "cannot be found at all") produces a
    message that says so, not the numeric-mismatch wording a real
    disagreement uses - a human mid-release needs to know the file has no
    parseable version at all, not that two numbers differ.
    """
    problems = crv.disagreements("v0.1.0", None, "0.1.0")
    assert len(problems) == 1
    assert "no __version__" in problems[0]
    assert "does not match" not in problems[0]


def test_disagreements_reports_a_missing_frontend_version_distinctly():
    problems = crv.disagreements("v0.1.0", "0.1.0", None)
    assert len(problems) == 1
    assert 'no string "version"' in problems[0]
    assert "does not match" not in problems[0]


def test_disagreements_does_not_compare_plugin_and_frontend_when_either_is_missing():
    """Both missing: two distinct "cannot be found" problems, and no third
    "does not match" problem manufactured out of `None != None` or similar -
    that would misreport a missing-version problem as a mismatch.
    """
    problems = crv.disagreements("v0.1.0", None, None)
    assert len(problems) == 2
    assert all("does not match" not in p for p in problems)


def test_disagreements_handles_a_tag_without_its_leading_v():
    """`tag_version` passes a `v`-less tag through unchanged (its own
    documented behaviour). Exercised through `disagreements` too, since a
    tag reaching this function is not itself validated against SPEC.md 12's
    `v*` grammar - the workflow's trigger already filters that upstream.
    """
    assert crv.disagreements("0.1.0", "0.1.0", "0.1.0") == []
    problems = crv.disagreements("0.1.0", "0.2.0", "0.1.0")
    assert len(problems) == 2


# ---------------------------------------------------------------------------
# main()'s exit status - the one thing only a subprocess can observe.
# Mirrors tests/tools/test_check_coverage.py's Checkout fixture: the script
# resolves plugin/companion.py and frontend/package.json from its own
# location, so a copy under tmp_path/.github/ reads a synthetic tree.
# ---------------------------------------------------------------------------


class Checkout:
    def __init__(self, root: Path):
        self.root = root
        (root / ".github").mkdir(parents=True, exist_ok=True)
        self.script = root / ".github" / "check_release_version.py"
        shutil.copy(SCRIPT, self.script)
        (root / "plugin").mkdir(exist_ok=True)
        (root / "frontend").mkdir(exist_ok=True)

    def plugin(self, source: str) -> None:
        (self.root / "plugin" / "companion.py").write_text(source, encoding="utf-8")

    def remove_plugin(self) -> None:
        (self.root / "plugin" / "companion.py").unlink(missing_ok=True)

    def frontend(self, package_json: str) -> None:
        (self.root / "frontend" / "package.json").write_text(package_json, encoding="utf-8")

    def remove_frontend(self) -> None:
        (self.root / "frontend" / "package.json").unlink(missing_ok=True)

    def run(self, tag: str, *, github_output: Path | None = None):
        # Allow-listed, not a filtered copy of the ambient environment
        # (tests/test_release_workflow.py's _run_step_script does the same
        # for the same reason): this child is a local, network-free Python
        # script that only reads plugin/companion.py and
        # frontend/package.json under self.root and, when asked, writes to
        # GITHUB_OUTPUT, so it starts from nothing rather than
        # dict(os.environ) and gains only what main() actually reads.
        # Harmless today - nothing here talks to a network or a
        # credential store - but dict(os.environ) is exactly the pattern
        # that gets copied into the next subprocess call that does.
        env = {}
        if github_output is not None:
            env["GITHUB_OUTPUT"] = str(github_output)
        result = subprocess.run(
            [sys.executable, str(self.script), tag],
            capture_output=True,
            text=True,
            cwd=self.root,
            env=env,
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr


@pytest.fixture
def checkout(tmp_path):
    c = Checkout(tmp_path)
    c.plugin('__version__ = "0.1.0"\n')
    c.frontend('{"name": "companion", "version": "0.1.0"}')
    return c


def test_main_exits_zero_when_all_three_agree(checkout):
    returncode, stdout, stderr = checkout.run("v0.1.0")
    assert returncode == 0
    assert "versions agree" in stdout


def test_main_exits_one_on_a_real_disagreement(checkout):
    returncode, stdout, stderr = checkout.run("v0.2.0")
    assert returncode == 1
    assert "::error::" in stderr


def test_main_exits_one_when_the_tag_does_not_match_the_grammar(checkout):
    returncode, stdout, stderr = checkout.run("vwip")
    assert returncode == 1
    assert "::error::" in stderr
    assert "grammar" in stderr


def test_main_checks_the_grammar_before_reading_any_file():
    """The script's own docstring: matches_tag_grammar is checked 'before
    anything else this script does'. Proven, not just documented, by giving
    it a tag that fails the grammar in a checkout where the plugin file is
    also missing - if the grammar check ran second, this would exit 2
    ('cannot read'); since it runs first, it must exit 1 with the grammar
    message instead, and never mention the missing file at all.
    """
    with tempfile.TemporaryDirectory() as tmp:
        c = Checkout(Path(tmp))
        c.remove_plugin()
        c.frontend('{"name": "companion", "version": "0.1.0"}')
        returncode, stdout, stderr = c.run("vwip")
        assert returncode == 1, (
            f"a grammar failure must win over a missing file (exit 1, not 2); "
            f"got {returncode}, stderr={stderr!r}"
        )
        assert "grammar" in stderr
        assert "cannot read" not in stderr


def test_main_exits_two_when_the_plugin_file_cannot_be_read(checkout):
    """SPEC.md 13: a check that could not run has not passed. A missing
    `plugin/companion.py` is not "no version found" in the sense
    `disagreements` reports (that path never runs); it is the script unable
    to do its job at all, and gets the exit status SPEC.md 5.2's
    docstring reserves for that: 2, distinct from the ordinary
    disagreement's 1.
    """
    checkout.remove_plugin()
    returncode, stdout, stderr = checkout.run("v0.1.0")
    assert returncode == 2
    assert "::error::" in stderr, (
        "a check that could not run must still annotate, not print a bare line "
        "that a dropped annotation would still satisfy"
    )
    assert "cannot read" in stderr


def test_main_exits_two_when_the_frontend_file_cannot_be_read(checkout):
    checkout.remove_frontend()
    returncode, stdout, stderr = checkout.run("v0.1.0")
    assert returncode == 2
    assert "::error::" in stderr
    assert "cannot read" in stderr


def test_main_exits_one_not_two_when_the_plugin_file_exists_but_has_no_version():
    """The distinction this module was asked to pin: a file present but
    unparseable is not the same failure as a file absent. `plugin_version`
    returning `None` from a readable file flows into `disagreements` as an
    ordinary named problem (exit 1), not into the exit-2 path, which is
    reserved for `read_text` itself raising - confirmed here rather than
    assumed, since the two look identical from "no version" alone.
    """
    with tempfile.TemporaryDirectory() as tmp:
        c = Checkout(Path(tmp))
        c.plugin("# no __version__ assignment in this file\n")
        c.frontend('{"name": "companion", "version": "0.1.0"}')
        returncode, stdout, stderr = c.run("v0.1.0")
        assert returncode == 1, (
            f"a readable file with no extractable version must fail as an ordinary "
            f"disagreement (exit 1), not as an unreadable-file failure (exit 2); "
            f"got {returncode}, stderr={stderr!r}"
        )
        assert "no __version__" in stderr


def test_main_exits_one_not_two_when_the_frontend_file_is_invalid_json(checkout):
    checkout.frontend("not valid json")
    returncode, stdout, stderr = checkout.run("v0.1.0")
    assert returncode == 1, (
        f"invalid JSON is a readable file with no usable version, the same "
        f"exit-1 case as a missing key; got {returncode}, stderr={stderr!r}"
    )


def test_main_writes_the_prerelease_output(tmp_path, checkout):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    checkout.plugin('__version__ = "0.1.0-rc1"\n')
    checkout.frontend('{"name": "companion", "version": "0.1.0-rc1"}')
    returncode, stdout, stderr = checkout.run("v0.1.0-rc1", github_output=output_path)
    assert returncode == 0
    assert output_path.read_text(encoding="utf-8").strip() == "prerelease=true"


def test_main_writes_the_stable_output(tmp_path, checkout):
    output_path = tmp_path / "github_output"
    output_path.write_text("", encoding="utf-8")
    returncode, stdout, stderr = checkout.run("v0.1.0", github_output=output_path)
    assert returncode == 0
    assert output_path.read_text(encoding="utf-8").strip() == "prerelease=false"
