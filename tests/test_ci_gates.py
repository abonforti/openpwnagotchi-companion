"""Tests for the CI ban scans' exit-status rule (SPEC.md 13 point 6, SPEC.md 5.1).

SPEC.md 13, enforcement layer 6: every gate that scans through an external
tool must distinguish "no match" from "the scan did not run" by exit status,
and fail on the second. `git grep` exits 0 on a match, 1 when nothing
matched (the clean result), and 128 when it could not compile the pattern or
read the tree. Plain `grep` exits 0 on a match, 1 when nothing matched, and
2 or more when it could not run the pattern at all. Collapsing "did not run"
into the clean branch reports a broken gate as a clean one.

SPEC.md 5.1 restates the same rule for the CI job inventory: "Each of the ban
scans reads the exit status of `git grep` in three cases rather than two: a
match fails the job, exit 1 is the clean result, and anything else fails the
job as a scan that did not run."

Scope of this module, stated explicitly because it does not cover every
`grep` invocation under `.github/workflows/` uniformly:

- Every step in any workflow file whose `run` script invokes `git grep` is
  collected generically and driven with a stub `git` that controls the exit
  status `git grep` reports. These are the "ban scan" shape used throughout
  `ci.yml`: a match against the banned pattern fails the job outright.
- Steps that invoke plain `grep` (not `git grep`) are collected too, but
  driving one honestly means stubbing whatever else it calls, which differs
  per step. Only one such step exists at the time of writing -
  `pull-request.yml`'s "Enable auto-merge when every changed file is
  documentation" - and it also calls `gh`, so it is driven by a dedicated
  test with its own `grep` and `gh` stubs rather than the generic driver.
  Its case-statement shape differs from the `git grep` steps too: exit 0
  and exit 1 both mean "the scan ran" there (0: some changed file is not
  documentation, 1: every changed file is), and only 2 or more means the
  scan could not run at all.
- A plain-`grep` step this module does not recognise by name fails a
  dedicated guard test rather than being silently left out of coverage.

This test does not read `plugin/companion.py` or any production code; its
subject is the shell scripts embedded in `.github/workflows/*.yml`, extracted
by parsing the workflow YAML and exercised against stub tools whose exit
status and call log are controlled from the test.
"""
import os
import re
import stat
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# A real invocation, not a mention: "grep" bounded by non-word characters,
# checked only against lines that are not shell comments. A comment
# explaining the rule (this module's own docstring included) mentions the
# word "grep" freely without that being a scan to drive.
_GREP_WORD_RE = re.compile(r"(?<![\w-])grep(?![\w-])")
_GIT_GREP_RE = re.compile(r"\bgit\s+grep\b")


def _script_command_lines(script):
    for line in script.splitlines():
        if line.strip().startswith("#"):
            continue
        yield line


def _script_has_grep_invocation(script):
    return any(_GREP_WORD_RE.search(line) for line in _script_command_lines(script))


def _script_has_git_grep_invocation(script):
    return any(_GIT_GREP_RE.search(line) for line in _script_command_lines(script))


def _iter_workflow_files():
    # GitHub Actions honours both extensions for a workflow file, so a
    # workflow added as `.yaml` must be covered by the same guarantee as
    # one added as `.yml`, or the "unregistered plain-grep step fails
    # loudly" promise below would quietly stop applying to it.
    files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    assert files, f"no workflow files found under {WORKFLOWS_DIR}"
    return files


def _collect_grep_steps():
    """Every step, in every workflow file, whose `run` script invokes grep
    (either `git grep` or a plain `grep`)."""
    steps = []
    for workflow_path in _iter_workflow_files():
        with open(workflow_path, encoding="utf-8") as handle:
            workflow = yaml.safe_load(handle)
        jobs = (workflow or {}).get("jobs", {})
        for job_key, job in jobs.items():
            for step in job.get("steps", []):
                run = step.get("run")
                if run and _script_has_grep_invocation(run):
                    steps.append(
                        {
                            "workflow": workflow_path.name,
                            "job_key": job_key,
                            "step_name": step.get("name", "<unnamed step>"),
                            "script": run,
                        }
                    )
    return steps


ALL_GREP_STEPS = _collect_grep_steps()
GIT_GREP_STEPS = [s for s in ALL_GREP_STEPS if _script_has_git_grep_invocation(s["script"])]
PLAIN_GREP_STEPS = [s for s in ALL_GREP_STEPS if s not in GIT_GREP_STEPS]

GIT_GREP_IDS = [f"{s['workflow']}::{s['step_name']}" for s in GIT_GREP_STEPS]

# The only plain-grep scan this module knows how to drive honestly. A step
# collected here that is not in this set fails a dedicated guard test
# instead of silently falling outside every parametrization.
KNOWN_PLAIN_GREP_STEP_IDS = {
    (
        "pull-request.yml",
        "auto-merge",
        "Enable auto-merge when every changed file is documentation",
    ),
}


def _plain_grep_step_id(step):
    return (step["workflow"], step["job_key"], step["step_name"])


def _branch_message(script, status):
    """The message a `case "$status" in ... esac` block prints for the
    branch whose pattern list includes the given status.

    Extracted by parsing the case block's branches rather than by a bare
    regex against the whole script: a branch header can combine patterns
    (`0 | 1)`), and a search for e.g. `1\\)` alone could match inside an
    earlier branch's message text instead of the branch that actually
    handles that status. Matching on the branch structure avoids both.
    """
    case_match = re.search(r'case\s+"\$status"\s+in(.*?)esac', script, re.S)
    assert case_match is not None, (
        "no `case \"$status\" in ... esac` block found in the script; the "
        "shape this extraction expects may have changed"
    )
    for branch in case_match.group(1).split(";;"):
        header_match = re.match(r"\s*([^)]*)\)(.*)", branch, re.S)
        if header_match is None:
            continue
        patterns = [p.strip() for p in header_match.group(1).split("|")]
        if status in patterns:
            echo_match = re.search(r'echo\s+"([^"]*)"', header_match.group(2))
            assert echo_match is not None, (
                f"the status {status!r} branch of the case statement has no "
                "echo message to extract"
            )
            return echo_match.group(1)
    raise AssertionError(f"no branch matching status {status!r} found in the case statement")


def _clean_message(script):
    """The message the script prints for the exit-1 (clean) branch."""
    return _branch_message(script, "1")


def _match_message(script):
    """The message the script prints for the exit-0 (match found) branch.

    Specific to each step - "screenshot baselines are banned", "'# pragma:
    no cover' is banned", and so on - unlike the exit-anything-else branch,
    which prints the same generic "the scan itself failed" text in every
    step. Asserting this specific text, rather than just a nonzero exit
    code and the presence of `::error::`, is what distinguishes "the match
    branch actually ran" from "the stub was bypassed and the catch-all
    branch ran instead", since the catch-all branch also exits nonzero and
    also prints `::error::`.
    """
    return _branch_message(script, "0")


def _write_executable(path, content):
    path.write_text(content, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_git_stub(tmp_path, exit_status):
    """A `git` stub that intercepts exactly `git grep ...` and exits with a
    controlled status. Any other invocation - including a variant this
    module was not written to expect, such as `git -C . grep` or
    `git --no-pager grep`, where `$1` is not literally `grep` - fails
    loudly instead of silently reaching a real `git` and scanning the real
    tree. A silent fallback there would let a scan step bypass the stub
    with no signal that the exit-status behaviour under test was never
    actually exercised.
    """
    stub_dir = Path(tempfile.mkdtemp(prefix="git-stubbin-", dir=tmp_path))
    _write_executable(
        stub_dir / "git",
        textwrap.dedent(
            f"""\
            #!/bin/sh
            if [ "$1" = "grep" ]; then
                exit {exit_status}
            fi
            echo "unstubbed git invocation: $*" >&2
            exit 99
            """
        ),
    )
    return stub_dir


def _run_with_git_stub(script, tmp_path, exit_status):
    stub_dir = _make_git_stub(tmp_path, exit_status)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    # None of today's three git-grep steps call `gh` or anything else that
    # would read a token, but this still executes arbitrary workflow shell
    # with the real ambient environment otherwise passed through. Scrub the
    # credential a future step could reach for, the same way the auto-merge
    # driver below overrides GH_TOKEN, so a real token is never in scope.
    env["GH_TOKEN"] = "test-token"
    env["GITHUB_TOKEN"] = "test-token"
    # `-e` matches how GitHub Actions runs a `run:` block by default: none
    # of these steps set `shell: bash`, so the runner uses `bash -e {0}`,
    # not `-o pipefail` (that flag is only added when `shell: bash` is
    # given explicitly). These scripts have no pipeline whose non-final
    # command matters to the outcome, so this is exact rather than merely
    # close, and it is not a claim that production adds pipefail here.
    wrapped = "set -e\n" + script
    return subprocess.run(
        ["bash", "-c", wrapped],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_more_than_one_grep_based_scan_exists():
    # An empty or single-element set here would make the parametrized tests
    # below vacuous. Fail loudly instead of passing on an empty collection.
    assert len(ALL_GREP_STEPS) > 1, (
        "expected more than one grep-based scan across .github/workflows/*.yml "
        f"(SPEC 5.1, SPEC 13 point 6), found {len(ALL_GREP_STEPS)}"
    )


def test_git_grep_steps_were_found():
    assert len(GIT_GREP_STEPS) > 0, (
        "expected at least one `git grep`-based ban scan; found none, so the "
        "git-grep-specific tests below would run against an empty set"
    )


def test_plain_grep_steps_are_explicitly_accounted_for():
    """Every plain-`grep` step this collector finds must be a step this
    module knows how to drive by name. A newly added plain-`grep` scan
    that is not registered here must fail this test rather than be left
    outside every parametrization silently.
    """
    found_ids = {_plain_grep_step_id(s) for s in PLAIN_GREP_STEPS}
    assert found_ids == KNOWN_PLAIN_GREP_STEP_IDS, (
        "the set of plain-grep steps found does not match the steps this "
        f"module is written to drive.\nFound: {sorted(found_ids)}\n"
        f"Known: {sorted(KNOWN_PLAIN_GREP_STEP_IDS)}\n"
        "Add a dedicated driver test for a new step, or update the registry "
        "if a known step was removed or renamed."
    )


@pytest.mark.parametrize("step", GIT_GREP_STEPS, ids=GIT_GREP_IDS)
def test_git_grep_match_fails_the_job(tmp_path, step):
    """git grep exit 0 (a match against the banned pattern) must fail via
    the match branch specifically, not merely fail via any branch.

    A nonzero exit plus a generic `::error::` line is not enough: the
    catch-all branch (an unrecognised exit status) prints its own
    `::error::` too and also exits nonzero, so a script that fails to
    reach the stub's match branch at all - because it no longer invokes
    `git grep` in the shape the stub recognises - would satisfy both of
    those checks for the wrong reason. The match branch's message is
    specific to the step and absent from the catch-all branch, so it is
    the one assertion that actually distinguishes them.
    """
    script = step["script"]
    result = _run_with_git_stub(script, tmp_path, 0)
    assert result.returncode != 0, (
        f"step {step['step_name']!r} ({step['workflow']}): git grep exit 0 "
        f"(match found) must fail the job, got returncode {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    match_message = _match_message(script)
    assert match_message in result.stdout, (
        f"step {step['step_name']!r} ({step['workflow']}): expected the "
        f"match-specific message {match_message!r} in stdout, got "
        f"{result.stdout!r}; a generic `::error::` from the catch-all "
        "branch is not proof the match branch ran"
    )


@pytest.mark.parametrize("step", GIT_GREP_STEPS, ids=GIT_GREP_IDS)
def test_git_grep_no_match_is_the_clean_result(tmp_path, step):
    """git grep exit 1 (nothing matched) is the clean result and must succeed."""
    result = _run_with_git_stub(step["script"], tmp_path, 1)
    assert result.returncode == 0, (
        f"step {step['step_name']!r} ({step['workflow']}): git grep exit 1 "
        f"(no match) is the clean result and must succeed the job, got "
        f"returncode {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("step", GIT_GREP_STEPS, ids=GIT_GREP_IDS)
def test_git_grep_scan_failure_fails_and_is_not_reported_clean(tmp_path, step):
    """git grep exit 128 (scan could not run) must fail, and must not be
    reported as though it were the clean result.

    128 is the case git grep uses for a pattern it cannot compile or a tree
    it cannot read (SPEC 13 point 6); collapsing it into the clean branch is
    exactly what turns a broken ban silently off.
    """
    script = step["script"]
    result = _run_with_git_stub(script, tmp_path, 128)
    assert result.returncode != 0, (
        f"step {step['step_name']!r} ({step['workflow']}): git grep exit 128 "
        f"(the scan itself did not run) must fail the job rather than be "
        f"treated as clean, got returncode {result.returncode}"
    )
    clean_message = _clean_message(script)
    combined_output = result.stdout + result.stderr
    assert clean_message not in combined_output, (
        f"step {step['step_name']!r} ({step['workflow']}): a broken scan (git "
        f"grep exit 128) printed its own clean-result message "
        f"{clean_message!r}; a scan that did not run must be distinguishable "
        "from one that found nothing (SPEC 13 point 6)"
    )


def test_git_stub_reports_each_invocation_independently(tmp_path):
    """The stub must behave like a real program, not a one-shot answer: a
    script that calls `git grep` twice must see the controlled exit status
    both times, through one stub environment, in one process.

    This drives a synthetic two-call script rather than one of the
    collected workflow steps, since none of them happens to call `git grep`
    more than once; it exists to keep the stub itself trustworthy for
    whichever script does.
    """
    script = textwrap.dedent(
        """\
        git grep -n -E 'x' -- 'y' && first=0 || first=$?
        git grep -n -E 'x' -- 'y' && second=0 || second=$?
        echo "first=$first second=$second"
        """
    )
    result = _run_with_git_stub(script, tmp_path, 1)
    assert result.returncode == 0
    assert "first=1 second=1" in result.stdout, (
        f"expected both git grep calls to report the controlled exit status "
        f"independently, got stdout={result.stdout!r}"
    )


def _find_plain_grep_step(step_id):
    for step in PLAIN_GREP_STEPS:
        if _plain_grep_step_id(step) == step_id:
            return step
    return None


_AUTO_MERGE_STEP_ID = (
    "pull-request.yml",
    "auto-merge",
    "Enable auto-merge when every changed file is documentation",
)
_AUTO_MERGE_STEP = _find_plain_grep_step(_AUTO_MERGE_STEP_ID)


def _run_pull_request_auto_merge_script(script, tmp_path, grep_exit_status, merge_exit_status=0):
    """Drive the auto-merge step's script with `grep` and `gh` stubbed, so
    it runs without touching a real repository tree or the network.

    `grep` is intercepted unconditionally rather than dispatched on its
    arguments: the script under test calls it exactly one way, so there is
    nothing to distinguish. Its stub drains stdin (`cat >/dev/null`) so it
    behaves like a real filter either way, but that detail is not evidence
    about the real script's pipe: the real `$?` under `set -euo pipefail`
    is the whole pipeline's status, and it coincides with grep's only
    because `grep -vP` without `-q` reads all of stdin before exiting. This
    stub sidesteps that by controlling the exit status directly rather than
    by matching input, so it stays correct even if the pipeline is dropped;
    it is not proof that the real, un-stubbed pipeline behaves the same way.

    `gh api` returns one fixed, harmless filename so the script's "no files
    changed" early exit is never taken; the actual documentation-only
    decision is entirely controlled by `grep_exit_status`. `gh pr merge` is
    recorded to a marker file - its full argument list, not just whether it
    ran - and exits with `merge_exit_status`, so a test can assert both
    whether auto-merge was attempted and with which arguments, and can
    exercise the "could not enable auto-merge" branch by making it fail.
    Anything else asked of either stub fails loudly rather than reaching a
    real binary.
    """
    stub_dir = Path(tempfile.mkdtemp(prefix="pr-stubbin-", dir=tmp_path))
    merge_marker = Path(tempfile.mkdtemp(prefix="pr-marker-", dir=tmp_path)) / "merge-called"

    _write_executable(
        stub_dir / "grep",
        textwrap.dedent(
            f"""\
            #!/bin/sh
            cat >/dev/null
            case "{grep_exit_status}" in
                0) echo "not-a-doc.py"; exit 0 ;;
                1) exit 1 ;;
                *) exit {grep_exit_status} ;;
            esac
            """
        ),
    )
    _write_executable(
        stub_dir / "gh",
        textwrap.dedent(
            f"""\
            #!/bin/sh
            if [ "$1" = "api" ]; then
                echo "docs/example.md"
                exit 0
            fi
            if [ "$1" = "pr" ] && [ "$2" = "merge" ]; then
                printf '%s\\n' "$*" > "{merge_marker}"
                exit {merge_exit_status}
            fi
            echo "unstubbed gh invocation: $*" >&2
            exit 99
            """
        ),
    )

    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    env["GH_TOKEN"] = "test-token"
    env["GITHUB_TOKEN"] = "test-token"
    env["PR"] = "123"
    env["REPO"] = "example-owner/example-repo"
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, merge_marker


def test_pull_request_auto_merge_step_was_found():
    assert _AUTO_MERGE_STEP is not None, (
        "the pull-request.yml auto-merge step was not found by the collector; "
        "either it was renamed/removed (update the registry above) or the "
        "collector's grep-invocation detection stopped matching it"
    )


def test_pull_request_auto_merge_leaves_non_docs_pull_request_alone(tmp_path):
    """grep exit 0: at least one changed file is not documentation. The job
    must succeed without attempting auto-merge."""
    result, merge_marker = _run_pull_request_auto_merge_script(
        _AUTO_MERGE_STEP["script"], tmp_path, 0
    )
    assert result.returncode == 0, (
        f"expected the step to succeed without deciding to merge, got "
        f"returncode {result.returncode}; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "Not a documentation-only pull request" in result.stdout
    assert not merge_marker.exists(), "gh pr merge must not run on a non-docs pull request"


def test_pull_request_auto_merge_enables_merge_when_every_file_is_docs(tmp_path):
    """grep exit 1: no changed file is anything but documentation. The job
    must succeed and attempt auto-merge with the arguments the step's own
    comment gives as its whole safety argument: `--auto` and `--squash`,
    against the pull request the workflow is actually running for. A step
    rewritten to merge immediately (e.g. `--merge` in place of `--auto`)
    would bypass the wait for required checks and must be caught here."""
    result, merge_marker = _run_pull_request_auto_merge_script(
        _AUTO_MERGE_STEP["script"], tmp_path, 1
    )
    assert result.returncode == 0, (
        f"expected the step to succeed and enable auto-merge, got returncode "
        f"{result.returncode}; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "Every changed file is documentation" in result.stdout
    assert merge_marker.exists(), "gh pr merge must run on a documentation-only pull request"
    merge_args = merge_marker.read_text(encoding="utf-8").strip()
    assert "pr merge" in merge_args, f"expected a `pr merge` invocation, got {merge_args!r}"
    assert "123" in merge_args, f"expected the pull request number, got {merge_args!r}"
    assert "--repo example-owner/example-repo" in merge_args, (
        f"expected the target repository, got {merge_args!r}"
    )
    assert "--auto" in merge_args, (
        f"expected `--auto`, which is what makes this wait for required "
        f"checks instead of merging immediately; got {merge_args!r}"
    )
    assert "--squash" in merge_args, f"expected `--squash`, got {merge_args!r}"


def test_pull_request_auto_merge_reports_but_does_not_fail_when_gh_merge_fails(tmp_path):
    """`gh pr merge` failing - already enabled, or the pull request cannot
    take it - must not fail the job; the step's own comment says this is
    deliberate, since failing the run here would colour the checks red for
    no reason. Covered explicitly rather than left implicit: this is the
    one branch of the step's logic that a passing `grep_exit_status=1` case
    alone does not exercise, because that case's `gh pr merge` stub always
    succeeds."""
    result, merge_marker = _run_pull_request_auto_merge_script(
        _AUTO_MERGE_STEP["script"], tmp_path, 1, merge_exit_status=1
    )
    assert result.returncode == 0, (
        f"a failed `gh pr merge` must not fail the job, got returncode "
        f"{result.returncode}; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "::notice::Could not enable auto-merge" in result.stdout
    assert merge_marker.exists(), "gh pr merge must still have been attempted"


@pytest.mark.parametrize("broken_grep_status", [2, 128])
def test_pull_request_auto_merge_fails_when_scan_does_not_run(tmp_path, broken_grep_status):
    """grep exit 2 or more: the pattern could not be run at all (no PCRE
    support, or some other failure to execute). This is the exact bug the
    step exists to avoid (SPEC 13 point 6): a `|| true` shape here used to
    collapse "no match" and "did not run" into the same empty `not_docs`,
    concluding every file was documentation and enabling auto-merge on a
    pull request full of code. The job must fail and must not decide
    either way.
    """
    result, merge_marker = _run_pull_request_auto_merge_script(
        _AUTO_MERGE_STEP["script"], tmp_path, broken_grep_status
    )
    assert result.returncode != 0, (
        f"expected the step to fail when grep could not run (exit "
        f"{broken_grep_status}), got returncode {result.returncode}"
    )
    combined_output = result.stdout + result.stderr
    assert "the documentation-only scan did not run" in combined_output, (
        f"expected the scan-failed error message, got {combined_output!r}"
    )
    assert "Every changed file is documentation" not in result.stdout, (
        "a broken scan must not be reported as a documentation-only pull request"
    )
    assert "Not a documentation-only pull request" not in result.stdout, (
        "a broken scan must not be reported as a non-documentation pull request "
        "either - it must refuse to decide at all"
    )
    assert not merge_marker.exists(), "gh pr merge must not run when the scan did not run"
