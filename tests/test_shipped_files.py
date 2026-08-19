"""The shipped-inventory gate, `.github/check_shipped_files.py` (SPEC 13.2).

`frontend/public/` is Vite's static directory: everything under it is copied
verbatim into `dist/`, packed into the release archive, and served by the
plugin's static server to anyone who can reach the unit over the tether
(SPEC 2.15). The rule under test, stated in SPEC 13.2:

- Every directory copied verbatim into the build declares its exact contents
  in `.github/check_shipped_files.py`.
- A file present that is not declared fails the check.
- A file declared that is not present fails the check too - both directions,
  not just the first, because a manifest entry that vanished ships an app
  that does not install.
- Both git's index and the disk are read: the index alone misses the
  untracked scratch that `git add -A` is about to sweep in, and the disk
  alone misses a tracked file deleted locally.
- The check exits nonzero and names the offending path.
- Anything under a shipped directory that is not a real directory counts as
  an entry - a symlink included, whether it resolves to a directory, to a
  file, or to nothing at all - and is named rather than followed or skipped.
- The same script runs in CI and in `.githooks/pre-commit`, and the hook
  runs it before the denylist scan, unconditionally, and reports a missing
  script by name rather than crashing.

This module does not read the implementation to decide what to assert -
only enough of `.github/check_shipped_files.py` to invoke it correctly
(no arguments, its own `__file__` fixes the repository root it inspects,
exit status is the verdict) and its declared inventory, which the test
needs to build a repository that starts from a clean baseline. The
declaration is loaded from the script at run time rather than copied into
this file by hand, so a manifest change does not go stale here.

Every test in this module runs the real, committed `.github/check_shipped_files.py`
at `SCRIPT_PATH` - there is no environment-variable seam to redirect it onto a
different copy. A suite whose subject can be swapped at run time does not
describe the committed script, which is the one property these tests exist to
establish. Proving a mutant is caught is done by copying the whole tree this
file lives under to a directory outside the repository, mutating the copy of
`.github/check_shipped_files.py` there, and running `pytest` from that copy -
`SCRIPT_PATH`, computed from `__file__`, then resolves to the mutant on its
own.

Building scratch repositories rather than a stub `git` on `PATH` for most
cases here, because the rule this module pins is specifically about the
union of git's index and the disk, and a stub can only assert what it is
told to answer - a real throwaway repository is the only way to prove the
index is genuinely consulted for a file that is in it and not on disk. The
one exception is the hook-driving tests, which follow
`tests/test_pre_commit_hook.py`'s stub-`git`-on-`PATH` convention because
they drive the real, unmodified `.githooks/pre-commit` against the real
repository and must not write anywhere under it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "check_shipped_files.py"
PRE_COMMIT_HOOK = REPO_ROOT / ".githooks" / "pre-commit"


def _load_declared_inventory(script_path: Path) -> dict[str, set[str]]:
    """The script's own `SHIPPED_DIRECTORIES` mapping, read by executing it
    as a module rather than copied into this file by hand, so a manifest
    change does not require updating this test."""
    spec = importlib.util.spec_from_file_location("shipped_check_under_test", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SHIPPED_DIRECTORIES


def _run_git(args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, (
        f"git {' '.join(args)} failed: {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout


def _init_scratch_repo(tmp_path: Path) -> None:
    _run_git(["init", "-q"], tmp_path)
    _run_git(["config", "user.email", "shipped-files-test@example.invalid"], tmp_path)
    _run_git(["config", "user.name", "Shipped Files Test"], tmp_path)


def _write_declared_baseline(tmp_path: Path, inventory: dict[str, set[str]]) -> None:
    """Writes every file the declaration expects, with synthetic content."""
    for directory, names in inventory.items():
        for name in names:
            path = tmp_path / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith(".webmanifest") or name.endswith(".json"):
                path.write_text(json.dumps({"name": "TestNet companion"}), encoding="utf-8")
            else:
                path.write_bytes(b"synthetic-fixture-content")


def _install_script_under_test(tmp_path: Path, script_source: str) -> None:
    github_dir = tmp_path / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)
    (github_dir / "check_shipped_files.py").write_text(script_source, encoding="utf-8")


def _run_check(tmp_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(tmp_path / ".github" / "check_shipped_files.py")],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )


def _new_scratch_repo(tmp_path: Path) -> tuple[Path, dict[str, set[str]]]:
    """A scratch git repository, seeded with the script under test and a
    baseline that exactly satisfies its declared inventory, committed. The
    caller mutates it from there and runs the check."""
    inventory = _load_declared_inventory(SCRIPT_PATH)
    _init_scratch_repo(tmp_path)
    _install_script_under_test(tmp_path, SCRIPT_PATH.read_text(encoding="utf-8"))
    _write_declared_baseline(tmp_path, inventory)
    _run_git(["add", "-A"], tmp_path)
    _run_git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline"], tmp_path)
    return tmp_path, inventory


# ---------------------------------------------------------------------------
# The real repository, read-only
# ---------------------------------------------------------------------------


def test_the_real_repository_passes_as_it_stands():
    """A read-only assertion against the actual checkout: if the declared
    inventory in `.github/check_shipped_files.py` has drifted from what is
    really under `frontend/public/`, that is caught here, not first in
    CI."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"the real repository's shipped inventory must pass as-is; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Both directions of the inventory mismatch
# ---------------------------------------------------------------------------


def test_a_stray_untracked_directory_fails_and_names_a_path_inside_it(tmp_path):
    """The incident SPEC 13.2 was written for, reproduced: an untracked
    directory of files under a shipped directory - never staged, never
    committed - must fail the check and name a path inside it. This is the
    scenario `git add -A` was about to sweep in."""
    repo, inventory = _new_scratch_repo(tmp_path)
    shipped_directory = sorted(inventory)[0]

    stray_dir = repo / shipped_directory / "src"
    stray_dir.mkdir(parents=True)
    (stray_dir / "App.svelte").write_text("<script></script>", encoding="utf-8")
    (stray_dir / "fixtures.json").write_text("{}", encoding="utf-8")

    result = _run_check(repo)

    assert result.returncode != 0, (
        f"an untracked stray directory must fail the check; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "src/App.svelte" in combined, f"the offending path must be named; got {combined!r}"
    assert "src/fixtures.json" in combined, f"every stray file must be named; got {combined!r}"


def test_a_declared_file_removed_fails_and_is_reported_as_missing(tmp_path):
    """The other direction: a file the manifest declares, removed from both
    the index and disk, must fail - and the failure must read as an absence
    ("missing", "not there", ...), not be indistinguishable from the
    unexpected-file case above. A check that reports both directions with
    the same words cannot tell a caller which fix to apply."""
    repo, inventory = _new_scratch_repo(tmp_path)
    shipped_directory = sorted(inventory)[0]
    declared_name = sorted(inventory[shipped_directory])[0]

    _run_git(["rm", "-q", f"{shipped_directory}/{declared_name}"], repo)
    _run_git(["-c", "commit.gpgsign=false", "commit", "-q", "-m", "remove a declared file"], repo)

    result = _run_check(repo)

    assert result.returncode != 0, (
        f"a removed declared file must fail the check; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert declared_name in combined, f"the missing file must be named; got {combined!r}"

    missing_synonyms = ("missing", "not there", "not present", "does not exist")
    line_with_name = next((ln for ln in combined.splitlines() if declared_name in ln), "")
    assert any(word in line_with_name.lower() for word in missing_synonyms), (
        f"the line reporting a removed declared file should read as an absence, "
        f"not as an unexpected file; got {line_with_name!r}"
    )
    assert "unexpected" not in line_with_name.lower()


def test_a_file_in_the_index_but_not_on_disk_still_counts_as_present(tmp_path):
    """Proves the index is genuinely read, not just the disk: a declared
    file committed into git, then deleted from the working tree only (never
    staged as removed), is still tracked. If the check read only the disk it
    would report this file missing; SPEC 13.2 says the union of both sources
    is what decides presence, so this must still pass. A test that only ever
    creates untracked files cannot distinguish this from a check that never
    consults the index at all."""
    repo, inventory = _new_scratch_repo(tmp_path)
    shipped_directory = sorted(inventory)[0]
    declared_name = sorted(inventory[shipped_directory])[0]

    (repo / shipped_directory / declared_name).unlink()

    tracked = _run_git(["ls-files", "--", shipped_directory], repo)
    assert declared_name in tracked, (
        "test setup error: the file must still be in git's index after a plain "
        "filesystem delete, or this test is not proving what it claims"
    )

    result = _run_check(repo)

    assert result.returncode == 0, (
        f"a file still in the index, only removed from disk, must not be reported "
        f"missing; stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_a_clean_scratch_baseline_passes(tmp_path):
    """The control for the three tests above: an untouched scratch baseline,
    built from the same helper, must pass on its own. Without this, a bug in
    the fixture itself (an inventory mismatch introduced by
    `_write_declared_baseline`) could masquerade as the check working."""
    repo, _inventory = _new_scratch_repo(tmp_path)

    result = _run_check(repo)

    assert result.returncode == 0, (
        f"an unmodified scratch baseline must pass; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_a_symlink_to_a_directory_is_named_and_never_followed(tmp_path):
    """The #117 incident in one line instead of twenty files: a symlink
    under a shipped directory that points at a real directory answers False
    to `is_file()` and, unfollowed, cannot be descended into either - a scan
    that filters on `is_file()` alone skips it entirely. It must instead be
    reported as its own entry, by its own name, without the check following
    it to report what is on the other side."""
    repo, inventory = _new_scratch_repo(tmp_path)
    shipped_directory = sorted(inventory)[0]

    target = repo / "outside-the-shipped-directory"
    target.mkdir()
    (target / "App.svelte").write_text("<script></script>", encoding="utf-8")

    symlink = repo / shipped_directory / "src"
    symlink.symlink_to(target, target_is_directory=True)

    result = _run_check(repo)

    assert result.returncode != 0, (
        f"a symlink to a directory must fail the check, not be skipped; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert f"{shipped_directory}/src" in combined, (
        f"the symlink itself must be named as the offending entry; got {combined!r}"
    )
    assert "App.svelte" not in combined, (
        f"the check must not follow the symlink and report what is on the "
        f"other side of it; got {combined!r}"
    )


def test_a_dangling_symlink_is_named_and_not_skipped(tmp_path):
    """The same gap, the other shape: a symlink whose target does not exist
    also answers False to `is_file()`, for the same reason a directory
    symlink does. Neither is declared, so both must be reported."""
    repo, inventory = _new_scratch_repo(tmp_path)
    shipped_directory = sorted(inventory)[0]

    symlink = repo / shipped_directory / "ghost.png"
    symlink.symlink_to(repo / "nonexistent-target-does-not-exist")

    result = _run_check(repo)

    assert result.returncode != 0, (
        f"a dangling symlink must fail the check, not be skipped; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert f"{shipped_directory}/ghost.png" in combined, (
        f"the dangling symlink must be named as the offending entry; got {combined!r}"
    )


# ---------------------------------------------------------------------------
# The pre-commit hook runs it, unconditionally, before the denylist
# ---------------------------------------------------------------------------


def test_pre_commit_hook_reports_a_missing_shipped_check_by_name(tmp_path):
    """`.githooks/pre-commit` resolves `.github/check_shipped_files.py`
    relative to its own location, which only works when the hook is reached
    through `core.hooksPath` rather than copied into `.git/hooks/` on its
    own. A copy of the hook with nothing beside it must report that by
    name, not crash with a traceback - a `FileNotFoundError` there tells
    nobody what to fix.

    Driven honestly: a real copy of the unmodified hook script, placed in a
    scratch tree without the shipped-inventory script beside it, run for
    real. No git stub is needed, because this guard is meant to fire before
    the hook's first `git` call."""
    hook_dir = tmp_path / ".githooks"
    hook_dir.mkdir()
    hook_copy = hook_dir / "pre-commit"
    hook_copy.write_text(PRE_COMMIT_HOOK.read_text(encoding="utf-8"), encoding="utf-8")
    mode = hook_copy.stat().st_mode
    hook_copy.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # Deliberately no .github/check_shipped_files.py anywhere under tmp_path.

    result = subprocess.run(
        ["python3", str(hook_copy)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0, (
        f"a hook with no shipped-inventory script beside it must refuse to commit; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined, (
        f"a missing script must be reported by name, not crash; got {combined!r}"
    )
    assert "check_shipped_files.py" in combined, (
        f"the missing path must be named; got {combined!r}"
    )
    assert "is missing" in combined, (
        f"the message must read as an absence, not a generic failure; got {combined!r}"
    )


def _write_git_stub_with_fake_shipped_entry(stub_dir: Path, fake_entry: str) -> Path:
    """A `git` stub for driving the real, unmodified `.githooks/pre-commit`
    against the real `.github/check_shipped_files.py`, without writing
    anything under the real repository.

    `check_shipped_files.py` fixes its own repository root from its own
    `__file__`, so it always inspects the real checkout - there is no way to
    point it at a scratch tree while still exercising the real hook by its
    real path. Its disk scan of the real `frontend/public/` therefore still
    runs for real and, per `test_the_real_repository_passes_as_it_stands`,
    finds nothing unexpected. This stub answers the script's one `git`
    invocation, `git -C <repo> ls-files -- <directory>`, with one line the
    real inventory does not declare, which is enough to make the union of
    index and disk contain an unexpected file without ever touching disk.

    Follows the shape of `_write_git_stub` in `tests/test_pre_commit_hook.py`:
    a small stub script, only the invocations it is told about are answered,
    anything else fails loudly rather than reaching the real `git`.
    """
    script = stub_dir / "git"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"FAKE_ENTRY = {fake_entry!r}\n"
        "if args[:1] == ['-C'] and 'ls-files' in args:\n"
        "    sys.stdout.write(FAKE_ENTRY + chr(10))\n"
        "    sys.exit(0)\n"
        "if args[:1] == ['config']:\n"
        "    sys.exit(1)\n"
        "if args[:1] == ['diff']:\n"
        "    sys.exit(0)\n"
        "sys.stderr.write('unstubbed git invocation: ' + ' '.join(args) + chr(10))\n"
        "sys.exit(99)\n",
        encoding="utf-8",
    )
    mode = script.stat().st_mode
    script.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def test_pre_commit_hook_refuses_before_it_ever_needs_a_denylist(tmp_path):
    """SPEC 13.2: the shipped-inventory gate runs first in the hook and runs
    unconditionally. This drives the real `.githooks/pre-commit` with no
    `companion.denylist` configured at all (the stub's `config` branch
    always exits 1, "not set") and an inventory violation reported by the
    stubbed `git -C ... ls-files`. The hook must still refuse the commit,
    and for the shipped-inventory reason - proving the ordering, not just
    that the hook fails somehow."""
    inventory = _load_declared_inventory(SCRIPT_PATH)
    shipped_directory = sorted(inventory)[0]
    fake_entry = f"{shipped_directory}/scratch/leaked-from-src.js"

    stub_dir = Path(tempfile.mkdtemp(prefix="shipped-hook-git-stub-", dir=tmp_path))
    _write_git_stub_with_fake_shipped_entry(stub_dir, fake_entry)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"

    result = subprocess.run(
        ["python3", str(PRE_COMMIT_HOOK)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0, (
        f"the hook must refuse when a shipped directory holds an undeclared file; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "companion.denylist is not configured" not in combined, (
        "the shipped-inventory gate must be reached and fail before the denylist "
        f"lookup ever runs; got {combined!r}"
    )
    assert "leaked-from-src.js" in combined, f"the offending path must be named; got {combined!r}"
    assert "holds a file nobody declared" in combined, (
        f"the hook's own wrapper message must appear; got {combined!r}"
    )


# ---------------------------------------------------------------------------
# CI actually invokes the script, on pull requests
# ---------------------------------------------------------------------------

# Pinned by workflow file, job key and step name, following the registry
# convention `tests/test_ci_gates.py` uses for the ban scans it drives. A gate
# nobody invokes is not a gate, and neither is one that only runs somewhere
# pull requests never reach: a rename of the step, or a move to a workflow
# whose `on:` does not include `pull_request` (a schedule-only drift-check
# workflow, for instance), must fail here rather than pass silently because
# some other step elsewhere happens to mention the script's filename.
_SHIPPED_CHECK_WORKFLOW = "ci.yml"
_SHIPPED_CHECK_JOB_KEY = "hygiene"
_SHIPPED_CHECK_STEP_NAME = "Shipped directories hold only what they declare"


def test_ci_actually_invokes_the_shipped_files_check():
    """Reads the pinned workflow file directly, finds the pinned job and
    step by their exact keys, and requires that step's `run:` script to
    actually execute `.github/check_shipped_files.py` - not merely mention
    it - and requires the workflow to trigger on `pull_request`, since a job
    that only runs on a schedule or on push to `master` never gates a pull
    request at all."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / _SHIPPED_CHECK_WORKFLOW
    assert workflow_path.is_file(), f"expected workflow file at {workflow_path}"
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    # PyYAML's default loader parses the unquoted `on:` key as the boolean
    # True rather than the string "on" (YAML 1.1's bareword booleans), so
    # both are checked.
    triggers = data.get("on", data.get(True, {})) or {}
    assert "pull_request" in triggers, (
        f"{_SHIPPED_CHECK_WORKFLOW} must trigger on pull_request, or the "
        f"shipped-inventory gate it hosts never runs against a pull request; "
        f"got triggers {sorted(triggers)!r}"
    )

    jobs = data.get("jobs", {})
    assert _SHIPPED_CHECK_JOB_KEY in jobs, (
        f"expected job {_SHIPPED_CHECK_JOB_KEY!r} in {_SHIPPED_CHECK_WORKFLOW}; "
        f"got jobs {sorted(jobs)!r}"
    )
    steps = jobs[_SHIPPED_CHECK_JOB_KEY].get("steps", []) or []
    step = next((s for s in steps if s.get("name") == _SHIPPED_CHECK_STEP_NAME), None)
    assert step is not None, (
        f"expected a step named {_SHIPPED_CHECK_STEP_NAME!r} in job "
        f"{_SHIPPED_CHECK_JOB_KEY!r} of {_SHIPPED_CHECK_WORKFLOW}; "
        f"got steps {[s.get('name') for s in steps]!r}"
    )

    run_script = step.get("run") or ""
    command_lines = [ln for ln in run_script.splitlines() if not ln.strip().startswith("#")]
    assert any("check_shipped_files.py" in ln for ln in command_lines), (
        f"step {_SHIPPED_CHECK_STEP_NAME!r} must actually run "
        f".github/check_shipped_files.py, not merely mention it; got {run_script!r}"
    )
