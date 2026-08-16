#!/usr/bin/env python3
"""Compare a coverage run against the floor and against the recorded figure.

SPEC 10.7 asks for two different things from one number, and a flat threshold
only does the first:

1. **CI fails below 85%** of lines and branches. That floor is stated here as
   well as in `pytest.ini` and `frontend/vitest.config.ts` on purpose. A gate
   that lives only in the runner's own configuration is one flag away from being
   turned off, and the flag is not visible in the diff that turns it off.
2. **A drop is reported even when it stays above the floor.** A fall from 99% to
   86% passes a flat floor in silence, which is the failure mode the floor has.
   The current figure is recorded in `coverage-baseline.json` and a fall of more
   than a tenth of a point produces a GitHub Actions warning - a warning, not a
   failure, because a legitimate refactor that removes covered code should not
   need a red build to land.

Raising the recorded figure is `check_coverage.py <component> --update`, one
documented command rather than an edit to a JSON file by hand.

Standard library only: this runs in a job that has installed the component's own
dependencies and nothing else.

Usage:
    python .github/check_coverage.py plugin
    python .github/check_coverage.py frontend
    python .github/check_coverage.py frontend --update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / ".github" / "coverage-baseline.json"

# SPEC 10.7. Duplicated from pytest.ini and vitest.config.ts deliberately; see
# the module docstring.
FLOOR = 85.0

# Below a tenth of a point a "drop" is the report's own rounding, not a lost
# test, and a warning nobody can act on is a warning everybody learns to skip.
DROP_TOLERANCE = 0.1

COMPONENTS = ("frontend", "plugin")


def _load_json(path: Path) -> dict:
    """Read a JSON object, turning every failure into one clear message."""
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise SystemExit(
            f"error: no coverage report at {path}.\n"
            "Run the component's suite with coverage before this check: a check "
            "that passes when nothing measured anything is not a check."
        )
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot read {path}: {exc}")


def read_frontend() -> tuple[Path, float, float]:
    """Percentages from the vitest `json-summary` reporter.

    `total.lines.pct` and `total.branches.pct` are already percentages, so they
    are taken as they stand rather than recomputed from the counts.
    """
    path = ROOT / "frontend" / "coverage" / "coverage-summary.json"
    report = _load_json(path)
    try:
        total = report["total"]
        return path, float(total["lines"]["pct"]), float(total["branches"]["pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"error: {path} is not a vitest coverage summary: {exc}")


def read_plugin() -> tuple[Path, float, float]:
    """Percentages from the coverage.py JSON report (`--cov-report=json`).

    Three figures live in that report and only two of them are the ones SPEC
    10.7 names:

    - `percent_statements_covered` is line coverage, and it is what this file
      gates on. `"lines"` in the baseline therefore means lines for the plugin
      exactly as it does for the frontend.
    - `percent_covered` is what `--cov-fail-under` gates on, and with
      `--cov-branch` in effect it is a combined statements-and-branches figure,
      not lines. Being a weighted mean of the two it can sit above the floor
      while line coverage sits below it, so checking it would not enforce what
      SPEC 10.7 says is enforced.
    - The branch percentage is computed from the counts because coverage.py's
      own `percent_branches_covered` is the newer of the two spellings and this
      has to keep working on whatever version the job resolves. It agrees with
      that field exactly.
    """
    path = ROOT / "coverage.json"
    report = _load_json(path)
    try:
        totals = report["totals"]
        lines = float(totals["percent_statements_covered"])
        num_branches = int(totals["num_branches"])
        covered_branches = int(totals["covered_branches"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"error: {path} is not a coverage.py JSON report: {exc}")

    # No branches at all is full branch coverage, not a division by zero. It is
    # also not a state this repository reaches, but the crash would land in CI
    # rather than here, so it is answered rather than assumed away.
    branches = 100.0 if num_branches == 0 else covered_branches / num_branches * 100.0
    return path, lines, branches


READERS = {"frontend": read_frontend, "plugin": read_plugin}


def load_baseline() -> dict:
    """The recorded figures, or an empty record if none have been written yet."""
    if not BASELINE_PATH.exists():
        return {}
    baseline = _load_json(BASELINE_PATH)
    if not isinstance(baseline, dict):
        raise SystemExit(f"error: {BASELINE_PATH} does not hold a JSON object")
    return baseline


def read_recorded(component: str, entry: object) -> dict[str, float]:
    """The recorded figures for one component, as a possibly partial mapping.

    Two states are told apart deliberately:

    - **Nothing recorded.** An absent key and an explicit `null` both mean the
      component has no figure yet, and so does an entry that omits one of the
      two metrics. The caller warns for each metric it did not get back and
      carries on, because a first run has to be able to pass.
    - **Something recorded that is not a number.** A scalar entry, a list, or a
      metric holding a string is a malformed record, and it is reported rather
      than skipped: `.get(name, 0.0)` on a broken file turns the drop check off
      without saying so, which is the one failure this script must not have.
    """
    if entry is None:
        return {}
    if not isinstance(entry, dict):
        raise SystemExit(
            f"error: {BASELINE_PATH.name} entry for {component} is "
            f"{type(entry).__name__}, not an object of coverage percentages"
        )
    figures: dict[str, float] = {}
    for key in ("lines", "branches"):
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SystemExit(
                f"error: {BASELINE_PATH.name} entry for {component} has a "
                f"non-numeric {key!r} value: {value!r}"
            )
        figures[key] = float(value)
    return figures


def write_baseline(baseline: dict) -> None:
    """Rewrite the record, sorted and newline-terminated so diffs stay small."""
    text = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    BASELINE_PATH.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check a coverage run against the SPEC 10.7 floor and baseline.",
    )
    parser.add_argument("component", choices=COMPONENTS)
    parser.add_argument(
        "--update",
        action="store_true",
        help="record the current figures as the new baseline instead of checking them",
    )
    args = parser.parse_args(argv)

    path, lines, branches = READERS[args.component]()
    measured = f"{lines:.2f}% lines, {branches:.2f}% branches"

    below = [
        f"{name} coverage is {value:.2f}%, below the {FLOOR:g}% floor"
        for name, value in (("line", lines), ("branch", branches))
        if value < FLOOR
    ]

    if args.update:
        # Recording a figure below the floor is refused rather than warned
        # about. The build fails on it anyway, since the floor check does not
        # consult the baseline, so the only thing writing it would achieve is a
        # committed number telling the next reader the floor was once lower.
        if below:
            for message in below:
                print(f"::error::{args.component}: {message} (SPEC 10.7)")
            print(
                f"{args.component}: refusing to record {measured}; "
                "raise the coverage first, the baseline is not the floor."
            )
            return 1
        baseline = load_baseline()
        baseline[args.component] = {"lines": round(lines, 2), "branches": round(branches, 2)}
        write_baseline(baseline)
        print(f"{args.component}: baseline recorded at {measured} (from {path})")
        return 0

    if below:
        for message in below:
            print(f"::error::{args.component}: {message} (SPEC 10.7)")
        print(f"{args.component}: {measured}, measured from {path}")
        return 1

    recorded = read_recorded(args.component, load_baseline().get(args.component))

    for name, key, value in (("line", "lines", lines), ("branch", "branches", branches)):
        if key not in recorded:
            print(
                f"::warning::{args.component}: no {name} figure recorded in "
                f"{BASELINE_PATH.name}. Run `python .github/check_coverage.py "
                f"{args.component} --update` to record one."
            )
            continue
        before = recorded[key]
        if before - value > DROP_TOLERANCE:
            print(
                f"::warning::{args.component}: {name} coverage fell from "
                f"{before:.2f}% to {value:.2f}%, still above the {FLOOR:g}% floor. "
                "Restore it, or record the new figure with "
                f"`python .github/check_coverage.py {args.component} --update`."
            )

    shown = " / ".join(
        f"{recorded[key]:.2f}%" if key in recorded else "none"
        for key in ("lines", "branches")
    )
    print(f"{args.component}: {measured} (recorded {shown})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
