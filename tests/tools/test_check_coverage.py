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


def test_update_records_the_figure_it_measured_not_the_one_it_replaced(checkout):
    """A recorded fall is legitimate - `--update` is how a drop is accepted -
    and the point of the flag is that the new number is what later runs compare
    against."""
    checkout.baseline({"plugin": {"lines": 99.0, "branches": 99.0}})
    checkout.measure(90.0, 90.0)

    code, output = checkout.run("plugin", "--update")

    assert code == 0, output
    assert checkout.recorded()["plugin"] == {"lines": 90.0, "branches": 90.0}


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


def test_update_on_a_malformed_baseline_does_not_lose_the_other_component(checkout):
    """Recovering the file is a legitimate use of `--update`, and it must not
    turn one bad entry into a file with one entry."""
    checkout.baseline(
        {"plugin": 91.66, "frontend": {"lines": 96.18, "branches": 89.55}}
    )
    checkout.measure(93.5, 92.25)

    code, output = checkout.run("plugin", "--update")

    assert_no_traceback(output)
    if code == 0:
        assert checkout.recorded()["frontend"] == {"lines": 96.18, "branches": 89.55}
        assert checkout.recorded()["plugin"] == {"lines": 93.5, "branches": 92.25}


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
        assert set(entry) == {"lines", "branches"}, (component, entry)
        for metric, value in entry.items():
            assert isinstance(value, (int, float)) and not isinstance(value, bool)
            assert value >= cc.FLOOR, (
                f"{component}.{metric} is recorded at {value}, under the "
                f"{cc.FLOOR:g} floor: every run compares against a failing figure"
            )
