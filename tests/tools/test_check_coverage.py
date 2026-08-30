"""`.github/check_coverage.py`, the other half of the SPEC 10.7 gate.

`--cov-fail-under` and the vitest thresholds fail below the floor. This script
restates that floor - so bypassing the pytest option does not bypass the gate -
and adds what a flat threshold cannot express: a *fall* that stays above the
floor is reported, because a slide from 99% to 86% is exactly the regression the
floor was never going to catch.

Which makes its failure mode silence, like the pinned-facts checker next door: a
comparison that never fires reads identically to a project whose coverage never
dropped. So most of what follows is about making it complain, and about the
inputs that could stop it complaining - a baseline entry that is a bare number,
or one missing a metric, has to be an error rather than a comparison that
quietly evaluates to "no change".

The ratchet (SPEC 10.7.1, issue #191) adds a second silent-pass shape to guard
against: a re-run measuring exactly the coverage already on file, where the
full-precision figure needed rounding *up* to match the two decimals the file
stores, must not read as a drop against its own rounded record and must not be
refused as a lowering that never happened.

Driven as a subprocess, not by importing `main`. The script resolves everything
from its own location, so a copy dropped into `tmp_path/.github/` reads a report
and a baseline that belong to the test; that keeps the exit status, the printed
message and the absence of a traceback under test, which is all a workflow step
can actually see. The two report readers are exercised in-process, where the
returned figures can be inspected directly.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "check_coverage.py"
REAL_BASELINE = REPO_ROOT / ".github" / "coverage-baseline.json"


def load_module():
    """Imports the checker by path: `.github` is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cc = load_module()


# ---------------------------------------------------------------------------
# Reports, in the shapes the two tools really emit
# ---------------------------------------------------------------------------

NUM_STATEMENTS = 1234
NUM_BRANCHES = 10_000


def plugin_report(lines: float, branches: float) -> dict:
    """`coverage json`'s totals block, with the three figures kept distinct.

    coverage.py publishes more than one percentage and they are different
    quantities. `percent_statements_covered` is line coverage.
    `percent_branches_covered` is branch coverage, and equals
    `covered_branches / num_branches`. `percent_covered` is neither: with
    `--cov-branch` in effect it is the combined ratio over statements *and*
    branches, so a reader that takes it reports a weighted mean that can sit
    above the floor while line coverage sits below it.

    The combined figure is therefore computed honestly here rather than set to
    one of the other two: it stays realistic, and it differs from both, so a
    reader reaching for the wrong key produces a wrong number rather than the
    right one by luck.
    """
    covered_lines = round(lines / 100 * NUM_STATEMENTS)
    covered_branches = round(branches / 100 * NUM_BRANCHES)
    combined = (covered_lines + covered_branches) / (NUM_STATEMENTS + NUM_BRANCHES) * 100
    return {
        "meta": {"branch_coverage": True},
        "files": {},
        "totals": {
            "covered_lines": covered_lines,
            "num_statements": NUM_STATEMENTS,
            "percent_covered": combined,
            "percent_covered_display": f"{combined:.0f}",
            "missing_lines": NUM_STATEMENTS - covered_lines,
            "excluded_lines": 0,
            "percent_statements_covered": lines,
            "percent_statements_covered_display": f"{lines:.0f}",
            "num_branches": NUM_BRANCHES,
            "num_partial_branches": 27,
            "covered_branches": covered_branches,
            "missing_branches": NUM_BRANCHES - covered_branches,
            "percent_branches_covered": branches,
            "percent_branches_covered_display": f"{branches:.0f}",
        },
    }


def frontend_report(lines: float, branches: float) -> dict:
    """vitest's `json-summary` reporter, whose totals are already percentages."""
    return {
        "total": {
            "lines": {"total": 131, "covered": 126, "skipped": 0, "pct": lines},
            "statements": {"total": 200, "covered": 195, "skipped": 0, "pct": 97.5},
            "functions": {"total": 62, "covered": 62, "skipped": 0, "pct": 100},
            "branches": {"total": 67, "covered": 60, "skipped": 0, "pct": branches},
            "branchesTrue": {"total": 0, "covered": 0, "skipped": 0, "pct": 100},
        }
    }


class Checkout:
    """A synthetic repository with the checker in it, run as the workflow does."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / ".github").mkdir()
        self.script = root / ".github" / "check_coverage.py"
        shutil.copy(SCRIPT, self.script)
        self.baseline_path = root / ".github" / "coverage-baseline.json"

    def baseline(self, content) -> None:
        self.baseline_path.write_text(json.dumps(content), encoding="utf-8")

    def raw_baseline(self, text: str) -> None:
        self.baseline_path.write_text(text, encoding="utf-8")

    def recorded(self):
        return json.loads(self.baseline_path.read_text(encoding="utf-8"))

    def measure(self, lines: float, branches: float, *, component="plugin") -> Path:
        """Leaves behind the report a coverage run of those figures would."""
        if component == "plugin":
            path = self.root / "coverage.json"
            content = plugin_report(lines, branches)
        else:
            path = self.root / "frontend" / "coverage" / "coverage-summary.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            content = frontend_report(lines, branches)
        path.write_text(json.dumps(content), encoding="utf-8")
        return path

    def run(self, *argv: str) -> tuple[int, str]:
        result = subprocess.run(
            [sys.executable, str(self.script), *argv],
            capture_output=True,
            text=True,
            cwd=self.root,
        )
        return result.returncode, result.stdout + result.stderr


@pytest.fixture
def checkout(tmp_path):
    return Checkout(tmp_path)


def diagnostics(output: str) -> list[str]:
    """The lines a human or a workflow annotation would notice.

    GitHub renders `::warning::` and `::error::` as annotations; anything else
    scrolls past in a log nobody reads. A drop that produced no such line is a
    drop that went unreported, whatever the exit status was.
    """
    return [
        line
        for line in output.splitlines()
        if "::warning::" in line or "::error::" in line
    ]


def assert_no_traceback(output: str) -> None:
    """A traceback fails the step for what reads as a bug in the checker, and
    the fix people reach for then is to stop running the checker."""
    assert "Traceback (most recent call last)" not in output, output


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


def test_the_floor_is_the_number_spec_10_7_states():
    """Pinned here rather than left to the script, because it is the one
    constant three files repeat: this one, pytest.ini and vitest.config.ts."""
    assert cc.FLOOR == 85.0


@pytest.mark.parametrize(
    "lines,branches,short",
    [
        (84.9, 91.0, "lines"),
        (91.0, 84.9, "branches"),
        (10.0, 10.0, "both"),
    ],
    ids=["lines-short", "branches-short", "both-short"],
)
def test_a_run_below_the_floor_fails(checkout, lines, branches, short):
    checkout.baseline({"plugin": {"lines": 99.0, "branches": 99.0}})
    checkout.measure(lines, branches)

    code, output = checkout.run("plugin")

    assert code == 1, f"{short} under the floor and the build stayed green: {output}"
    assert diagnostics(output), output
    assert "85" in output, output


def test_a_run_exactly_on_the_floor_passes(checkout):
    """85 is the floor, not the first failing value: a gate that fails at
    exactly the stated number is a gate at a different number."""
    checkout.baseline({"plugin": {"lines": 85.0, "branches": 85.0}})
    checkout.measure(85.0, 85.0)

    code, output = checkout.run("plugin")

    assert code == 0, output


def test_the_floor_message_at_the_boundary_does_not_contradict_itself(checkout):
    """SPEC 10.7.1 (refreshed): "The floor is the one comparison still made
    on the raw measurement, and its message has to say so. The floor is not
    a ratchet: nothing is written when it fails, and a build measuring
    84.999 is below eighty-five however the number is displayed. But
    printing that as `line coverage is 85.00%, below the 85% floor` is a
    sentence contradicting itself, so the message shows enough digits to be
    true rather than the two the file stores."

    84.999 rounds to 85.00 at the two decimals the baseline file stores,
    which is exactly the self-contradicting sentence quoted above. The
    build must still fail - the floor compares the raw measurement, not the
    rounded one the ratchet uses - and the printed figure must not read as
    "85.00%" while saying it is below 85%.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(84.999, 90.05)

    code, output = checkout.run("plugin")

    assert code == 1, (
        f"84.999% lines, below the 85% floor on the raw measurement, passed: {output}"
    )
    assert "85.00%, below the 85% floor" not in output, (
        f"the floor message still prints the self-contradicting 85.00%: {output}"
    )
    assert "84.999" in output, (
        f"the floor message does not show enough digits to be true: {output}"
    )


def test_the_frontend_component_is_gated_by_the_same_floor(checkout):
    """Two jobs, one rule. A floor enforced on one of them is half a floor."""
    checkout.baseline({"frontend": {"lines": 96.18, "branches": 89.55}})
    checkout.measure(70.0, 60.0, component="frontend")

    code, output = checkout.run("frontend")

    assert code == 1, output
    assert "85" in output, output


# ---------------------------------------------------------------------------
# The comparison against the recorded figure
# ---------------------------------------------------------------------------


def test_a_component_with_no_recorded_figure_warns_and_passes(checkout):
    """The first run for a component, and the state the file is in for one that
    has never been measured. Not a failure - there is nothing to compare - but
    not silence either, or a baseline nobody ever records reads for ever as a
    baseline that never dropped."""
    checkout.baseline({"frontend": {"lines": 96.18, "branches": 89.55}})
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output), "an unrecorded component said nothing at all"


def test_a_null_entry_is_a_figure_not_yet_recorded(checkout):
    """`"plugin": null` is a supported state, not a malformed one: it is how the
    file names a component whose figure has not been taken yet. It must behave
    exactly like the absent case, and must not be mistaken for the malformed
    entries below."""
    checkout.baseline({"plugin": None, "frontend": {"lines": 96.18, "branches": 89.55}})
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output), "a null entry passed without a word"
    assert_no_traceback(output)


def test_an_unchanged_figure_passes_quietly(checkout):
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(91.66, 90.05)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output) == [], output


def test_a_rise_is_not_reported_as_a_fall(checkout):
    """Obvious, and the sign error that would make this whole script backwards
    while still looking like it works."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(95.0, 94.0)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output) == [], output


@pytest.mark.parametrize(
    "lines,branches,fallen",
    [
        (86.0, 90.05, "line"),
        (91.66, 86.0, "branch"),
        (86.0, 86.0, "line"),
    ],
    ids=["lines-fell", "branches-fell", "both-fell"],
)
def test_a_fall_that_stays_above_the_floor_warns_without_failing(
    checkout, lines, branches, fallen
):
    """SPEC 10.7's stated reason for this script existing: a regression from 99%
    to 86% clears the floor and would otherwise pass in silence.

    A warning and not a failure, because coverage moves for legitimate reasons -
    deleting a well-tested module lowers the percentage without anything getting
    worse - and a gate that blocks those is a gate people route around.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(lines, branches)

    code, output = checkout.run("plugin")

    assert code == 0, "a fall above the floor must not fail the build"
    reported = diagnostics(output)
    assert reported, f"{fallen} coverage fell and nothing was reported"
    assert any(fallen in line for line in reported), (
        f"the {fallen} figure fell and the report does not say which metric: {reported}"
    )


def test_a_branch_fall_is_reported_when_lines_are_untouched(checkout):
    """The metric that matters most and the one easiest to leave uncompared.

    SPEC 10.7 chose branch coverage precisely because line coverage hides `if a
    and b`; a script that watches only the line figure reports "no change" while
    a quarter of the outcomes stop being executed, and every run since would
    have read green.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(91.66, 87.0)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert any("branch" in line for line in diagnostics(output)), (
        f"branch coverage fell 3 points and the run reported: {output}"
    )
    assert "87" in output, output


@pytest.mark.parametrize(
    "lines,branches",
    [(91.60, 90.05), (91.66, 89.99), (91.57, 89.96)],
    ids=["lines", "branches", "both"],
)
def test_a_fall_within_the_tolerance_is_not_reported(checkout, lines, branches):
    """Both figures are ratios of whole counts, so adding a line of code moves
    them by a hundredth with nothing getting worse. Warning on that trains
    everyone to ignore the warning, which costs the real one its audience."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(lines, branches)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output) == [], (
        f"a fall of at most {cc.DROP_TOLERANCE} points was reported: {output}"
    )


def test_the_tolerance_is_a_tenth_of_a_point():
    assert cc.DROP_TOLERANCE == 0.1


@pytest.mark.parametrize(
    "lines,branches",
    [(91.45, 90.05), (91.66, 89.84)],
    ids=["lines", "branches"],
)
def test_a_fall_past_the_tolerance_is_reported(checkout, lines, branches):
    """The other side of the same boundary. Without this pair the tolerance
    could be any number at all, including one that swallows every fall."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(lines, branches)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output), output


def test_only_the_component_asked_for_is_compared(checkout):
    """The two jobs run in different workflows and neither can see the other's
    report. A frontend fall must not fail or warn the plugin job."""
    checkout.baseline(
        {
            "plugin": {"lines": 91.66, "branches": 90.05},
            "frontend": {"lines": 96.18, "branches": 89.55},
        }
    )
    checkout.measure(91.66, 90.05, component="plugin")
    checkout.measure(50.0, 50.0, component="frontend")

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output) == [], output


def test_the_measured_figures_are_printed_whether_or_not_anything_is_wrong(checkout):
    """The number is the point of running the step. A silent success leaves the
    log with no record of what coverage was on that commit."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(91.66, 90.05)

    _, output = checkout.run("plugin")

    assert "91.66" in output and "90.05" in output, output


# ---------------------------------------------------------------------------
# Inputs that must not pass silently
# ---------------------------------------------------------------------------


def test_a_missing_report_is_a_message_not_a_traceback(checkout):
    """The report is written by the step before this one. When that step is
    reordered away, or writes somewhere else, the operator needs to be told
    which file was not found - and a check that passes when nothing measured
    anything is not a check."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})

    code, output = checkout.run("plugin")

    assert code == 1, output
    assert_no_traceback(output)
    assert "coverage.json" in output, output


def test_a_report_that_is_not_json_is_a_message_not_a_traceback(checkout):
    """A truncated report is what a runner that ran out of disk leaves behind."""
    (checkout.root / "coverage.json").write_text("{ truncated", encoding="utf-8")

    code, output = checkout.run("plugin")

    assert code == 1, output
    assert_no_traceback(output)


def test_a_report_missing_its_totals_is_a_message_not_a_traceback(checkout):
    """Some other tool's JSON, or a report from a run that measured nothing."""
    (checkout.root / "coverage.json").write_text('{"meta": {}}', encoding="utf-8")

    code, output = checkout.run("plugin")

    assert code == 1, output
    assert_no_traceback(output)


def test_a_frontend_summary_missing_its_totals_is_a_message_not_a_traceback(checkout):
    report = checkout.root / "frontend" / "coverage" / "coverage-summary.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"total": {"lines": {"pct": 96.18}}}', encoding="utf-8")

    code, output = checkout.run("frontend")

    assert code == 1, output
    assert_no_traceback(output)


@pytest.mark.parametrize(
    "entry",
    [
        {"lines": 91.66},
        {"branches": 90.05},
        {},
        91.66,
        "91.66",
        [91.66, 90.05],
        {"lines": 91.66, "branches": "90.05"},
        {"lines": None, "branches": 90.05},
    ],
    ids=[
        "missing-branches",
        "missing-lines",
        "empty-object",
        "bare-number",
        "string",
        "list",
        "metric-not-a-number",
        "metric-null",
    ],
)
def test_a_malformed_baseline_entry_is_reported_rather_than_ignored(checkout, entry):
    """The vacuous-pass cases this validation exists for.

    An entry missing a metric, or one that is a bare number where an object was
    meant, has no figure to compare against. Treating that as "nothing to
    report" switches the comparison off for that component permanently, while
    the file still looks recorded, so nobody goes looking.

    Distinct from `null`, which is the supported "not recorded yet" state and is
    asserted above to warn and pass. Whether a malformed entry fails the run or
    only warns is the script's to decide; what it may not do is say nothing.
    """
    checkout.baseline({"plugin": entry})
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert_no_traceback(output)
    assert diagnostics(output) or code != 0, (
        f"a baseline entry of {entry!r} was accepted without a word: {output}"
    )


def test_a_baseline_that_is_not_json_is_reported_rather_than_ignored(checkout):
    checkout.raw_baseline("{ not json")
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert_no_traceback(output)
    assert diagnostics(output) or code != 0, output


def test_a_baseline_that_is_not_an_object_is_reported_rather_than_ignored(checkout):
    """A JSON list parses, and every lookup against it then raises."""
    checkout.raw_baseline("[]")
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert_no_traceback(output)
    assert diagnostics(output) or code != 0, output


def test_an_absent_baseline_file_is_not_fatal(checkout):
    """A fresh clone, or a checkout that predates the file. There is nothing to
    compare against, which is the unrecorded case, not a failure."""
    assert not checkout.baseline_path.exists()
    checkout.measure(91.0, 90.0)

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert_no_traceback(output)


# ---------------------------------------------------------------------------
# Reading the real report shapes
# ---------------------------------------------------------------------------


def test_the_plugin_reader_reports_lines_and_branches_separately(monkeypatch, tmp_path):
    """Not `percent_covered`, which under `--cov-branch` is the combined figure.
    Reading it reports one number for two things, and the baseline then records
    something other than what SPEC 10.7 asks to be watched."""
    (tmp_path / "coverage.json").write_text(
        json.dumps(plugin_report(lines=91.66, branches=90.05)), encoding="utf-8"
    )
    monkeypatch.setattr(cc, "ROOT", tmp_path)

    path, lines, branches = cc.read_plugin()

    assert path == tmp_path / "coverage.json"
    assert lines == pytest.approx(91.66, abs=0.005)
    assert branches == pytest.approx(90.05, abs=0.005)


def test_the_frontend_reader_takes_the_totals_from_the_summary(monkeypatch, tmp_path):
    report = tmp_path / "frontend" / "coverage" / "coverage-summary.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(frontend_report(lines=96.18, branches=89.55)), encoding="utf-8"
    )
    monkeypatch.setattr(cc, "ROOT", tmp_path)

    path, lines, branches = cc.read_frontend()

    assert path == report
    assert lines == pytest.approx(96.18, abs=0.005)
    assert branches == pytest.approx(89.55, abs=0.005)


def test_a_full_precision_figure_is_compared_against_a_rounded_one(checkout):
    """coverage.py reports 91.65628891656289 and the baseline records 91.66. If
    the comparison did not tolerate that, every unchanged run would look like a
    fall of six thousandths - under the tolerance today, but the comparison
    would be lying, and a real fall of that size would hide behind it."""
    checkout.measure(91.65628891656289, 90.05376344086021)
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})

    code, output = checkout.run("plugin")

    assert code == 0, output
    assert diagnostics(output) == [], output


# ---------------------------------------------------------------------------
# --update
# ---------------------------------------------------------------------------


def test_update_records_the_measured_figures(checkout):
    checkout.baseline({"plugin": {"lines": 88.0, "branches": 88.0}})
    checkout.measure(93.5, 92.25)

    code, output = checkout.run("plugin", "--update")

    assert code == 0, output
    assert checkout.recorded()["plugin"] == {"lines": 93.5, "branches": 92.25}


def test_update_leaves_the_other_component_alone(checkout):
    """One job updates one entry. Rewriting the file from a single run would
    drop the figure the other job recorded, and the loss would look exactly like
    a component that had never been measured."""
    checkout.baseline(
        {
            "frontend": {"lines": 96.18, "branches": 89.55},
            "plugin": {"lines": 91.66, "branches": 90.05},
        }
    )
    checkout.measure(93.5, 92.25)

    checkout.run("plugin", "--update")

    assert checkout.recorded()["frontend"] == {"lines": 96.18, "branches": 89.55}


def test_update_records_a_component_that_had_no_entry(checkout):
    checkout.baseline({"frontend": {"lines": 96.18, "branches": 89.55}})
    checkout.measure(93.5, 92.25)

    checkout.run("plugin", "--update")

    assert checkout.recorded()["plugin"] == {"lines": 93.5, "branches": 92.25}


def test_update_writes_a_file_that_reads_back(checkout):
    checkout.measure(93.5, 92.25)

    code, output = checkout.run("plugin", "--update")

    assert code == 0, output
    text = checkout.baseline_path.read_text(encoding="utf-8")
    assert json.loads(text)["plugin"]["branches"] == 92.25
    assert text.endswith("\n"), "a file without a trailing newline churns every diff"


def test_update_with_allow_drop_records_the_figure_it_measured_not_the_one_it_replaced(
    checkout,
):
    """A recorded fall is legitimate once justified - `--update --allow-drop` is
    how a drop is accepted (SPEC 10.7.1, issue #191) - and the point of the flag
    is that the new number is what later runs compare against, not some blend of
    the old and new.

    Was `test_update_records_the_figure_it_measured_not_the_one_it_replaced`,
    which exercised a bare `--update` lowering the figure. SPEC 10.7.1 now
    requires `--allow-drop` and a reason for exactly that case; the refusal of
    the bare form is asserted separately below.
    """
    checkout.baseline({"plugin": {"lines": 99.0, "branches": 99.0}})
    checkout.measure(90.0, 90.0)

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "synthetic reason for the test"
    )

    assert code == 0, output
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 90.0
    assert recorded["branches"] == 90.0


def test_update_will_not_quietly_record_a_figure_below_the_floor(checkout):
    """`--update` is the escape hatch, and an escape hatch that also lowers the
    floor is how a floor stops existing: run it once on a bad build and every
    later run compares against a figure that was already failing."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(70.0, 60.0)

    code, output = checkout.run("plugin", "--update")

    assert_no_traceback(output)
    assert code != 0 or diagnostics(output), (
        f"a below-floor figure was recorded with nothing said: {output}"
    )
    assert "85" in output, output
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}, (
        "the recorded figure was replaced by one under the floor"
    )


# ---------------------------------------------------------------------------
# The ratchet: --update refuses to lower without --allow-drop and a reason
# (SPEC 10.7.1, issue #191)
# ---------------------------------------------------------------------------


def test_update_refuses_to_lower_without_allow_drop(checkout):
    """SPEC 10.7.1: "`--update` writes a figure at or above the recorded one
    and refuses one below it." No `--allow-drop` in sight, so the refusal is
    the only outcome; a warning printed while the file is rewritten anyway is
    not a refusal."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)

    code, output = checkout.run("plugin", "--update")

    assert_no_traceback(output)
    assert code != 0, f"a lowering --update without --allow-drop was accepted: {output}"
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}, (
        "the recorded figure changed even though the update was refused"
    )


def test_update_refuses_a_drop_larger_than_the_tolerance(checkout):
    """The case issue #191 was actually opened about: frontend branches moved
    95.14 -> 94.61 across three merges, 0.53 off the peak, and nothing failed.
    A bare `--update` recording that move today must be refused, not warned
    about and accepted."""
    checkout.baseline({"frontend": {"lines": 98.87, "branches": 95.14}})
    checkout.measure(98.87, 94.61, component="frontend")

    code, output = checkout.run("frontend", "--update")

    assert code != 0, f"a drop of 0.53, well past the tolerance, was recorded: {output}"
    assert checkout.recorded()["frontend"] == {"lines": 98.87, "branches": 95.14}


def test_update_refuses_a_drop_smaller_than_the_warning_tolerance(checkout):
    """SPEC 10.7.1: "The comparison is exact rather than tolerant, unlike the
    drop warning next to it: the warning forgives a tenth of a point ... and a
    gate that forgives is not what this bullet is for."

    A drop of 0.01 is well inside `cc.DROP_TOLERANCE` (0.1) - the read-only
    comparison would not even mention it - so an implementation that reused
    the warning's tolerance for the ratchet, rather than comparing exactly,
    would let this one through. That distinction is the whole point of the
    "exact rather than tolerant" sentence, so it is the one this test has to
    catch, not a drop large enough that either reading of the rule would
    refuse it.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(91.65, 90.05)

    code, output = checkout.run("plugin", "--update")

    assert code != 0, (
        f"a drop of 0.01, inside the warning's own tolerance, was accepted by "
        f"a bare --update: {output}"
    )
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}


def test_update_of_a_figure_that_rounds_up_to_the_recorded_one_is_not_a_refused_drop(
    checkout,
):
    """SPEC 10.7.1: "Round first, then compare, so the question asked is the
    one the file can answer." The defect this closes was in the ratchet
    itself: `--update` measuring the exact coverage already on file, where
    the true figure needed rounding up to match the record, was refused as a
    lowering - `branch coverage would drop from 90.04% to 90.04%` - because
    the comparison used the unrounded measurement. A round-number fixture
    cannot expose this; the figure has to be one where `round(x, 2) > x`.
    """
    true_branches = 90.03999999999999
    assert round(true_branches, 2) > true_branches, "the fixture no longer needs rounding up"
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.04}})
    checkout.measure(91.66, true_branches)

    code, output = checkout.run("plugin", "--update")

    assert code == 0, (
        f"a bare --update measuring the same coverage already on record "
        f"({true_branches} rounds to 90.04) was refused as a drop: {output}"
    )
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.04}


def test_update_allow_drop_lowers_the_figure(checkout):
    """SPEC 10.7.1: "Lowering is `--update --allow-drop`". With a reason
    supplied, the lowering is accepted and the measured figure is recorded
    exactly, not merged with the old one."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "removed a well-tested module"
    )

    assert code == 0, output
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 89.0
    assert recorded["branches"] == 89.0


def test_update_allow_drop_without_a_reason_is_refused(checkout):
    """SPEC 10.7.1: "the flag requires a reason." `--allow-drop` given with no
    reason at all must refuse exactly like the bare form - it is not enough to
    ask for a lowering, the lowering has to be justified."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)

    code, output = checkout.run("plugin", "--update", "--allow-drop")

    assert code != 0, f"--allow-drop with no reason at all was accepted: {output}"
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}


def test_the_lowering_reason_is_recorded_beside_the_number(checkout):
    """SPEC 10.7.1: "the flag requires a reason, which is written into the
    baseline file beside the number it lowered." Read back and asserted, as
    the brief for this suite asks, rather than trusted because the run exited
    zero."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)
    reason = "synthetic-drop-reason-c3f1a9"

    code, output = checkout.run("plugin", "--update", "--allow-drop", "--reason", reason)

    assert code == 0, output
    recorded = checkout.recorded()
    assert recorded["plugin"]["lines"] == 89.0
    assert recorded["plugin"]["branches"] == 89.0
    assert recorded["plugin"]["reason"] == reason, (
        f"the reason is not recorded under the 'reason' key beside the plugin "
        f"entry's figures (SPEC 10.7.1): {recorded}"
    )
    assert set(recorded["plugin"]) == {"lines", "branches", "reason"}, (
        f"the plugin entry has a key other than the two figures and the "
        f"reason: {recorded['plugin']}"
    )
    assert "reason" not in recorded.get("frontend", {}), (
        "the reason for a plugin drop leaked into an unrelated component"
    )


def test_a_later_upward_update_removes_the_stale_reason(checkout):
    """SPEC 10.7.1: "The reason does not outlive the number it explains. It
    describes why *that* figure was accepted, so the next update that raises
    the figure removes it along with the number it was about." A reason left
    behind after coverage has recovered would excuse a drop that is no longer
    on record - a stale excuse for a number nobody can see any more."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)
    reason = "synthetic-drop-reason-for-removal-9f2b"

    code, output = checkout.run("plugin", "--update", "--allow-drop", "--reason", reason)
    assert code == 0, output
    dropped = checkout.recorded()["plugin"]
    assert dropped.get("reason") == reason, (
        f"sanity check failed: the drop reason was never recorded: {dropped}"
    )

    checkout.measure(95.0, 95.0)
    code, output = checkout.run("plugin", "--update")

    assert code == 0, output
    risen = checkout.recorded()["plugin"]
    assert risen["lines"] == 95.0
    assert risen["branches"] == 95.0
    assert "reason" not in risen, (
        f"a stale drop reason survived the upward update that should have "
        f"dropped it: {risen}"
    )


def test_an_update_that_repeats_the_same_figure_keeps_its_reason(checkout):
    """SPEC 10.7.1: "An equal figure keeps its reason; only a rise removes
    it. ... re-recording the same number leaves that number, and therefore
    its justification, standing."

    `test_a_later_upward_update_removes_the_stale_reason` raises the figure
    and so cannot tell "removed on a rise" apart from "removed on any
    update"; this one re-records the identical figure with a bare
    `--update` and the reason must survive.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)
    reason = "synthetic-drop-reason-repeat-4b7e"

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", reason
    )
    assert code == 0, output
    assert checkout.recorded()["plugin"].get("reason") == reason, (
        "sanity check failed: the drop reason was never recorded"
    )

    checkout.measure(89.0, 89.0)
    code, output = checkout.run("plugin", "--update")

    assert code == 0, (
        f"a bare --update re-recording the same figure already on file was "
        f"refused as a lowering: {output}"
    )
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 89.0
    assert recorded["branches"] == 89.0
    assert recorded.get("reason") == reason, (
        f"an update that repeated the same figure removed the reason that "
        f"explains it: {recorded}"
    )


def test_a_partial_entry_keeps_its_reason_when_the_missing_metric_is_first_recorded(
    checkout,
):
    """SPEC 10.7.1 (refreshed): the equal-figure comparison "compares only
    the metrics the old entry actually has, and records the missing one for
    the first time without treating it as a mismatch." A baseline entry
    holding only `lines` and a `reason` - `branches` never having been
    established - must keep that reason across an `--update` that repeats
    the recorded `lines` figure and records `branches` for the first time;
    the missing metric is not a drop from nothing, it is the first
    measurement of it.
    """
    checkout.baseline({"plugin": {"lines": 90.0, "reason": "r"}})
    checkout.measure(90.0, 95.0)

    code, output = checkout.run("plugin", "--update")

    assert code == 0, (
        f"an --update repeating the one recorded metric and recording the "
        f"other for the first time was refused as a lowering: {output}"
    )
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 90.0
    assert recorded["branches"] == 95.0
    assert recorded.get("reason") == "r", (
        f"the reason on a partial entry did not survive recording its "
        f"missing metric for the first time: {recorded}"
    )


def test_a_fall_in_lines_is_a_drop_even_when_branches_rise(checkout):
    """SPEC 10.7.1: "A drop in either figure is a drop, and the two are never
    averaged. An update where lines fall and branches rise is a lowering and
    needs the flag, even though the pair read together looks like an
    improvement." Lines fall 91.66 -> 90.0, branches rise 90.05 -> 95.0; the
    net reads as an improvement and must still be refused without the flag."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(90.0, 95.0)

    code, output = checkout.run("plugin", "--update")

    assert code != 0, (
        f"lines fell from 91.66 to 90.0 and the update was accepted because "
        f"branches rose: {output}"
    )
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}


def test_a_fall_in_lines_with_a_rising_branch_figure_is_accepted_with_the_flag(checkout):
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(90.0, 95.0)

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "lines fell, branches rose"
    )

    assert code == 0, output
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 90.0
    assert recorded["branches"] == 95.0


def test_a_fall_in_branches_is_a_drop_even_when_lines_rise(checkout):
    """The mirror of the case above. SPEC 10.7.1 states the rule for lines
    falling and branches rising; a per-metric rule written for one column and
    not the other is exactly the bug this test exists to catch. Branches fall
    90.05 -> 89.0, lines rise 91.66 -> 95.0."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(95.0, 89.0)

    code, output = checkout.run("plugin", "--update")

    assert code != 0, (
        f"branches fell from 90.05 to 89.0 and the update was accepted because "
        f"lines rose: {output}"
    )
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}


def test_a_fall_in_branches_with_a_rising_line_figure_is_accepted_with_the_flag(checkout):
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(95.0, 89.0)

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "branches fell, lines rose"
    )

    assert code == 0, output
    recorded = checkout.recorded()["plugin"]
    assert recorded["lines"] == 95.0
    assert recorded["branches"] == 89.0


def test_allow_drop_and_reason_on_an_update_that_lowers_nothing_is_refused(checkout):
    """SPEC 10.7.1: "The flag and the reason on an update that lowers nothing
    are a usage error. Not ignored, and not recorded: recording would put a
    justification beside a figure it does not justify, and ignoring would let
    somebody believe a lowering had been explained when nothing was lowered
    and nothing was written."

    Both figures rise, so nothing dropped; `--allow-drop --reason` is given
    anyway. The file must be left byte-identical - not merely "the exit code
    was non-zero" - because "warned and wrote anyway" is exactly the failure
    this rule forbids, and an exit-status assertion alone cannot tell that
    apart from a correct refusal.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(95.0, 95.0)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "nothing actually dropped"
    )

    assert code != 0, (
        f"--allow-drop --reason on a non-lowering update was accepted: {output}"
    )
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though the update was refused as a "
        "usage error - the file must be byte-identical, not merely unparsed "
        "differently"
    )


def test_allow_drop_without_update_is_a_usage_error(checkout):
    """SPEC 10.7.1: "`--allow-drop` without `--update`, and `--reason`
    without `--allow-drop`, are usage errors refused during argument
    parsing." Read-only invocations only ever warn or fail on the floor;
    `--allow-drop` has no meaning without `--update` and must be refused
    before anything is read or written.
    """
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(91.66, 90.05)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run("plugin", "--allow-drop")

    assert code != 0, f"--allow-drop without --update was accepted: {output}"
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though --allow-drop without "
        "--update is a usage error"
    )


def test_reason_without_allow_drop_is_a_usage_error(checkout):
    """SPEC 10.7.1, same bullet. `--reason` given on an `--update` that does
    not also carry `--allow-drop` is a usage error, not a reason silently
    discarded and not a lowering silently permitted."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run(
        "plugin", "--update", "--reason", "no allow-drop given"
    )

    assert code != 0, f"--reason without --allow-drop was accepted: {output}"
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though --reason without "
        "--allow-drop is a usage error"
    )


def test_a_whitespace_only_reason_is_refused_like_an_absent_one(checkout):
    """SPEC 10.7.1: "the flag requires a reason." A reason made only of
    whitespace carries no information a reviewer could read in the diff, so
    it must be refused the same way an absent `--reason` already is
    (`test_update_allow_drop_without_a_reason_is_refused`)."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(89.0, 89.0)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "   "
    )

    assert code != 0, f"a whitespace-only --reason was accepted as a reason: {output}"
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though the whitespace-only reason "
        "was refused"
    )


def test_a_non_string_stored_reason_is_refused_like_one_from_the_command_line(checkout):
    """SPEC 10.7.1 (refreshed): "A `reason` read back off disk is checked the
    way one arriving on the command line is. The same string cannot be
    refused as an argument and accepted as a file value: a `reason` that is
    not a string ... makes the entry malformed like any other unreadable
    field."

    A stored `reason: 123` on an entry whose figures are being re-recorded
    unchanged (no `--allow-drop` needed) must be refused rather than carried
    onto the update verbatim - the way `--reason 123` would be refused as an
    argument."""
    checkout.baseline({"plugin": {"lines": 90.0, "branches": 90.0, "reason": 123}})
    checkout.measure(90.0, 90.0)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run("plugin", "--update")

    assert code != 0, (
        f"a stored reason of 123, not a string, was accepted on an equal "
        f"update: {output}"
    )
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though the stored reason was not a "
        "string"
    )


def test_a_blank_stored_reason_is_refused_like_one_from_the_command_line(checkout):
    """The other half of the same sentence: "or is blank" alongside "not a
    string". A stored `reason: "  "` must be refused the same way
    `--reason "  "` already is
    (`test_a_whitespace_only_reason_is_refused_like_an_absent_one`), not
    carried onto the next update unexamined."""
    checkout.baseline({"plugin": {"lines": 90.0, "branches": 90.0, "reason": "  "}})
    checkout.measure(90.0, 90.0)
    before = checkout.baseline_path.read_text(encoding="utf-8")

    code, output = checkout.run("plugin", "--update")

    assert code != 0, (
        f"a stored reason of whitespace only was accepted on an equal "
        f"update: {output}"
    )
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though the stored reason was blank"
    )


def test_update_with_allow_drop_still_refuses_below_the_floor(checkout):
    """SPEC 10.7.1: "`--update` refuses to record a figure below the floor."
    That sentence names no exception for `--allow-drop`, and giving a reason
    for a below-floor figure would still leave a committed number the build
    itself cannot pass."""
    checkout.baseline({"plugin": {"lines": 91.66, "branches": 90.05}})
    checkout.measure(70.0, 60.0)

    code, output = checkout.run(
        "plugin", "--update", "--allow-drop", "--reason", "reason given anyway"
    )

    assert code != 0, f"a below-floor figure was recorded with a reason attached: {output}"
    assert "85" in output, output
    assert checkout.recorded()["plugin"] == {"lines": 91.66, "branches": 90.05}


def test_a_fall_past_the_tolerance_still_only_warns_without_update(checkout):
    """The read-only comparison path - no `--update` at all - is the existing
    drop-tolerance behaviour SPEC 10.7 already described, and the ratchet must
    not have turned it into a build failure: the acceptance criteria for issue
    #191 ask only that `--update` itself refuse a lowering, not that every run
    that merely observes one now fails the build."""
    checkout.baseline({"frontend": {"lines": 98.87, "branches": 95.14}})
    checkout.measure(98.87, 94.61, component="frontend")

    code, output = checkout.run("frontend")

    assert code == 0, f"observing a drop without --update failed the build: {output}"
    assert diagnostics(output), "a drop past the tolerance produced no report at all"
    assert checkout.recorded()["frontend"] == {"lines": 98.87, "branches": 95.14}, (
        "a run without --update rewrote the baseline"
    )


def test_update_on_a_malformed_baseline_does_not_lose_the_other_component(checkout):
    """SPEC 10.7.1: "A malformed entry is not repaired by `--update`. ...
    the repair is a hand edit ... and the refusal is the same one the
    read-only path already gives."

    `--update` on a malformed `plugin` entry must be refused, not used as a
    chance to overwrite a figure the script could not read. Refused, and the
    other component's entry - and the whole file - must survive untouched.
    """
    checkout.baseline(
        {"plugin": 91.66, "frontend": {"lines": 96.18, "branches": 89.55}}
    )
    before = checkout.baseline_path.read_text(encoding="utf-8")
    checkout.measure(93.5, 92.25)

    code, output = checkout.run("plugin", "--update")

    assert_no_traceback(output)
    assert code != 0, (
        f"--update on a malformed plugin entry was accepted rather than "
        f"refused, repairing a figure the script could not read: {output}"
    )
    after = checkout.baseline_path.read_text(encoding="utf-8")
    assert after == before, (
        "the baseline file changed even though the malformed entry was "
        "refused - refusing is refusing, per SPEC 10.7.1"
    )
    assert checkout.recorded()["frontend"] == {"lines": 96.18, "branches": 89.55}


# ---------------------------------------------------------------------------
# The command line as the workflow invokes it
# ---------------------------------------------------------------------------


def test_the_components_are_the_two_jobs_and_nothing_else():
    assert set(cc.COMPONENTS) == {"frontend", "plugin"}


def test_an_unknown_component_is_rejected_by_the_command_line():
    """`check_coverage.py backend` must not silently check nothing."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "backend"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode != 0
    assert "backend" in result.stderr


def test_no_argument_at_all_is_rejected():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=REPO_ROOT
    )

    assert result.returncode != 0


def test_the_script_is_run_from_anywhere_the_workflow_puts_it(checkout):
    """The frontend job runs `python3 ../.github/check_coverage.py frontend` from
    `frontend/`, so the working directory is not the repository root. Everything
    must resolve from the script's own location."""
    checkout.baseline({"frontend": {"lines": 96.18, "branches": 89.55}})
    checkout.measure(96.18, 89.55, component="frontend")

    result = subprocess.run(
        [sys.executable, "../.github/check_coverage.py", "frontend"],
        capture_output=True,
        text=True,
        cwd=checkout.root / "frontend",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert diagnostics(result.stdout + result.stderr) == [], result.stdout


# ---------------------------------------------------------------------------
# The file this repository actually ships
# ---------------------------------------------------------------------------


def assert_entry_is_well_formed(component: str, entry: dict) -> None:
    """SPEC 10.7.1: an entry is the two figures, optionally beside a `reason`
    for a lowering, and the reason - wherever it came from - must be a
    non-blank string.

    Shared between the committed file below and the synthetic fixtures right
    after it: the committed baseline has never carried a `reason`, so a check
    reachable only through that file never runs its own `reason` branch.
    """
    assert {"lines", "branches"} <= set(entry) <= {"lines", "branches", "reason"}, (
        "SPEC 10.7.1: an entry is the two figures, optionally beside a "
        f"'reason' for a lowering: {component} = {entry}"
    )
    if "reason" in entry:
        assert isinstance(entry["reason"], str) and entry["reason"].strip(), (
            f"{component}.reason must be a non-empty string when present: {entry}"
        )
    for metric in ("lines", "branches"):
        value = entry[metric]
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert value >= cc.FLOOR, (
            f"{component}.{metric} is recorded at {value}, under the "
            f"{cc.FLOOR:g} floor: every run compares against a failing figure"
        )


def test_the_committed_baseline_is_well_formed_and_above_the_floor():
    """The validation is worth nothing if the file it guards was never run past
    it, and a committed figure below the floor is a build that cannot pass."""
    baseline = json.loads(REAL_BASELINE.read_text(encoding="utf-8"))

    assert isinstance(baseline, dict)
    assert set(baseline) <= set(cc.COMPONENTS), (
        f"{REAL_BASELINE.name} records a component the script does not know about"
    )
    for component, entry in baseline.items():
        if entry is None:
            continue
        assert_entry_is_well_formed(component, entry)


@pytest.mark.parametrize(
    "entry",
    [
        {"lines": 90.0, "branches": 90.0, "reason": ""},
        {"lines": 90.0, "branches": 90.0, "reason": 7},
    ],
    ids=["blank-reason", "non-string-reason"],
)
def test_an_entry_with_a_malformed_reason_fails_the_shared_validation(entry):
    """Drives `assert_entry_is_well_formed`'s `reason` branch directly. The
    committed baseline in `test_the_committed_baseline_is_well_formed_and_
    above_the_floor` has no `reason` recorded today, so without a fixture
    that has one, that branch is executed by nothing and its assertion is
    dead - a check that passed because it never ran."""
    with pytest.raises(AssertionError):
        assert_entry_is_well_formed("plugin", entry)
