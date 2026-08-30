"""The pre-commit leak gate, `.githooks/pre-commit` (SPEC 13).

This is a separate file from `tests/test_hooks.py` on purpose, even though the
two share the word "hook": `test_hooks.py` is about the pwnagotchi plugin's own
hook surface (`on_handshake`, `on_ui_update`, and the rest of the lifecycle the
agent drives), which is a wire-format and threading contract with the device.
`.githooks/pre-commit` is a local git hook, unrelated to the plugin and to the
agent - it scans a staged diff against an owner-specific denylist before a
commit is made. Filing tests for it under the plugin's hook file would put
"hook" readers looking for either subject in the wrong place. Keep them apart.

The contract under test:

- Before any of that, the hook runs `.github/check_shipped_files.py`
  unconditionally - whether or not anything is staged - because the file
  that check exists to catch is usually untracked, and there is no staged
  diff to look at until `git add -A` sweeps it in (SPEC 13.2). A nonzero
  exit from that script makes the hook refuse before it ever looks at the
  denylist.
- `git(*args, tolerate=())` runs git, and a nonzero exit status not listed in
  `tolerate` calls `fail()`, which prints to stderr and exits 1 without
  returning to the caller.
- The only caller passing `tolerate` is the `companion.denylist` config
  lookup, with `tolerate=(1,)`, because `git config --get` exits 1 for a key
  that is simply not set, and that case already has its own "not configured"
  error distinct from "git failed".
- Both `git diff --cached` calls take no `tolerate` at all, so any failure
  reading either one must refuse to commit rather than be read as "nothing is
  staged".

This is the regression the tests below are named for: the hook's `git()`
helper used to run with `check=False` and return only stdout, so a failed
`git diff --cached` came back as an empty string, an empty staged list read
as "nothing to scan", and the hook exited 0 - a gate that could not read the
diff reported the same thing as a gate that read it and found nothing (SPEC
13 point 6, the same rule the CI ban scans in `test_ci_gates.py` are held
to).

These tests put a stub `git` on PATH, the technique already used against the
CI workflow scans in `test_ci_gates.py`, so the hook's own
`subprocess.run(["git", ...])` reaches the stub rather than a real
repository. No real denylist path or pattern is used anywhere here: the
denylist file the hook reads is a throwaway one written into `tmp_path` with
synthetic patterns, never the owner's real one (SPEC 13).

The hook's `shipped_check` subprocess call runs `.github/check_shipped_files.py`
by its real path (SPEC 13.2's REPO_ROOT is fixed from that script's own
`__file__`, not from an argument), and that script itself calls
`git -C <repo root> ls-files -- <directory>` for every shipped directory. The
stub therefore has to answer that call too, or every test here would be
exercising a shipped-inventory failure instead of the denylist logic it is
actually named for. It answers with the real declared inventory, read from
`.github/check_shipped_files.py`'s own `SHIPPED_DIRECTORIES` at run time
rather than a snapshot copied into this file, so the stub does not go stale
the day a shipped file is added or renamed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Stubbing and running the hook
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT_HOOK = REPO_ROOT / ".githooks" / "pre-commit"
SHIPPED_FILES_CHECK = REPO_ROOT / ".github" / "check_shipped_files.py"


def _real_shipped_ls_files_stdout() -> str:
    """What `git -C <repo root> ls-files -- <directory>` would really answer
    for every directory the shipped-inventory check declares, read from
    `SHIPPED_DIRECTORIES` at run time so this does not drift from the
    declaration it is standing in for."""
    spec = importlib.util.spec_from_file_location("shipped_check_under_hook", SHIPPED_FILES_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lines = [
        f"{directory}/{name}"
        for directory, names in module.SHIPPED_DIRECTORIES.items()
        for name in names
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _write_git_stub(
    stub_dir: Path,
    *,
    ls_files_stdout: str | None = None,
    ls_files_status: int = 0,
    config_stdout: str = "",
    config_status: int = 0,
    staged_stdout: str = "",
    staged_status: int = 0,
    diff_stdout: str = "",
    diff_status: int = 0,
    show_stdout: str = "",
    show_status: int = 0,
    staged_z_stdout: str = "",
    staged_z_status: int = 0,
) -> Path:
    """A `git` stub answering the six calls the hook, directly or through
    `.github/check_shipped_files.py` or `.github/check_secrets.py --staged`,
    makes: `-C <repo root> ls-files -- <directory>`, `config --get
    companion.denylist`, the hook's own `diff --cached --name-only
    --diff-filter=... -- <files>` staged-name listing, `diff --cached
    --unified=0 -- <files>`, `show :<path>`, and `check_secrets.py
    --staged`'s own staged-name listing. Each call's stdout and exit status
    are controlled independently, so a test can make any one of them fail
    without touching the others, and can make the config lookup fail with a
    status other than the one the hook tolerates.

    The hook's own staged-name listing and `check_secrets.py --staged`'s
    are two separate calls with the same shape - both are `git diff
    --cached --name-only --diff-filter=<something excluding D> --
    <optional files>` - and the only thing distinguishing them on the
    command line is that `check_secrets.py` adds `-z` (it splits the
    output on NUL rather than newline) and the hook does not. `staged_z_*`
    answers the `-z` one; `staged_*` answers the hook's own. Matched on
    `-z` ahead of the plain `--name-only` case for that reason - checking
    the more specific shape first is what keeps the two calls independently
    controllable, which is exactly what SPEC.md 13.3's exit-1-vs-exit-2
    hook contract needs a test to be able to do: fail the scanner's own
    listing while leaving the hook's own gates untouched, or the reverse.
    Neither call's exact filter letters are pinned here - SPEC.md 13.3
    notes the filter spelling has already changed twice (`ACM` to `ACMR` to
    the exclusion form `--diff-filter=d`) for the same underlying reason
    each time, so this stub matches on the calls' *shape* (`--name-only`,
    with or without `-z`) rather than on which letters follow
    `--diff-filter=`.

    `show :<path>` is what `check_secrets.py --staged` uses to read a
    staged file's content out of the index rather than off disk (SPEC.md
    13.3, issue #203) - discovered by running the real, unmodified
    `check_secrets.py --staged` against a real repository and observing
    which `git` subcommand it issues, not by reading the script. It
    defaults to empty stdout and a clean exit, so every test in this module
    that predates the `--staged` integration keeps exercising the denylist
    logic it was written for, rather than incidentally failing on a git
    call the stub did not know to answer.

    `ls-files` defaults to the real declared inventory
    (`_real_shipped_ls_files_stdout()`) so that, unless a test asks
    otherwise, the shipped-inventory check the hook now runs first passes
    and the hook proceeds to the generic scan and denylist logic every test
    here besides the shipped-inventory one is actually about. The
    `ls-files` invocation is `git -C <path> ls-files -- <directory>`, so it
    is matched by the presence of the `ls-files` subcommand anywhere in the
    arguments, not by position - `args[:1]` is `-C` for that call, unlike
    the others.

    Any invocation that matches none of these fails loudly instead of
    silently reaching a real `git` and reading the real repository.
    """
    if ls_files_stdout is None:
        ls_files_stdout = _real_shipped_ls_files_stdout()
    script = stub_dir / "git"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        f"LS_FILES_STDOUT = {ls_files_stdout!r}\n"
        f"LS_FILES_STATUS = {ls_files_status!r}\n"
        f"CONFIG_STDOUT = {config_stdout!r}\n"
        f"CONFIG_STATUS = {config_status!r}\n"
        f"STAGED_STDOUT = {staged_stdout!r}\n"
        f"STAGED_STATUS = {staged_status!r}\n"
        f"DIFF_STDOUT = {diff_stdout!r}\n"
        f"DIFF_STATUS = {diff_status!r}\n"
        f"SHOW_STDOUT = {show_stdout!r}\n"
        f"SHOW_STATUS = {show_status!r}\n"
        f"STAGED_Z_STDOUT = {staged_z_stdout!r}\n"
        f"STAGED_Z_STATUS = {staged_z_status!r}\n"
        "if 'ls-files' in args:\n"
        "    sys.stdout.write(LS_FILES_STDOUT)\n"
        "    sys.exit(LS_FILES_STATUS)\n"
        "if args[:1] == ['config']:\n"
        "    sys.stdout.write(CONFIG_STDOUT)\n"
        "    sys.exit(CONFIG_STATUS)\n"
        "if args[:1] == ['diff'] and '--name-only' in args and '-z' in args:\n"
        "    sys.stdout.write(STAGED_Z_STDOUT)\n"
        "    sys.exit(STAGED_Z_STATUS)\n"
        "if args[:1] == ['diff'] and '--name-only' in args:\n"
        "    sys.stdout.write(STAGED_STDOUT)\n"
        "    sys.exit(STAGED_STATUS)\n"
        "if args[:1] == ['diff'] and '--unified=0' in args:\n"
        "    sys.stdout.write(DIFF_STDOUT)\n"
        "    sys.exit(DIFF_STATUS)\n"
        "if args[:1] == ['show']:\n"
        "    sys.stdout.write(SHOW_STDOUT)\n"
        "    sys.exit(SHOW_STATUS)\n"
        "sys.stderr.write('unstubbed git invocation: ' + ' '.join(args) + chr(10))\n"
        "sys.exit(99)\n",
        encoding="utf-8",
    )
    mode = script.stat().st_mode
    script.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _run_pre_commit_hook(tmp_path: Path, **git_stub_kwargs) -> subprocess.CompletedProcess:
    stub_dir = Path(tempfile.mkdtemp(prefix="hook-git-stubbin-", dir=tmp_path))
    _write_git_stub(stub_dir, **git_stub_kwargs)
    env = dict(os.environ)
    env["PATH"] = f"{stub_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        ["python3", str(PRE_COMMIT_HOOK)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _write_denylist(tmp_path: Path, *patterns: str) -> Path:
    """A throwaway denylist file, synthetic patterns only, never the
    owner's real one."""
    path = tmp_path / "denylist.txt"
    path.write_text("\n".join(patterns) + "\n", encoding="utf-8")
    return path


def test_a_failed_staged_file_list_read_refuses_to_commit(tmp_path):
    """The regression itself: the hook's own `git diff --cached --name-only
    --diff-filter=...` staged-name listing failing must not be read as
    "nothing is staged". It must fail the hook and say the scan did not
    run, not exit 0.

    `staged_status` (not `staged_z_status`) is what is set here - the
    hook's own denylist listing, matched by `--name-only` without `-z`, as
    opposed to `check_secrets.py --staged`'s identically-shaped but
    separately-controlled listing (SPEC.md 13.3, issue #203). Left at its
    default clean answer, `check_secrets.py --staged` finds nothing staged
    and exits 0, so this failure is reached and reported in the hook's own
    words rather than being intercepted upstream - confirmed live, not
    assumed, by running this exact scenario and reading which gate's
    wording came back before writing the assertion below.
    """
    denylist = _write_denylist(tmp_path, "NeverMatchesAnythingSynthetic9000")

    result = _run_pre_commit_hook(
        tmp_path,
        config_stdout=str(denylist),
        config_status=0,
        staged_status=2,
    )

    assert result.returncode != 0, (
        f"a failed git diff must not exit 0, got returncode {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "could not read the staged diff" in result.stderr
    assert "has not run" in result.stderr


def test_a_failed_unified_diff_read_refuses_to_commit(tmp_path):
    """The second `git diff --cached` call - over the unified diff of the
    files the first call already listed - is the other half of the same
    regression. Fixing only the first call would leave this one silent."""
    denylist = _write_denylist(tmp_path, "NeverMatchesAnythingSynthetic9000")

    result = _run_pre_commit_hook(
        tmp_path,
        config_stdout=str(denylist),
        config_status=0,
        staged_stdout="notes.md\n",
        staged_status=0,
        diff_status=2,
    )

    assert result.returncode != 0, (
        f"a failed git diff must not exit 0, got returncode {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "could not read the staged diff" in result.stderr
    assert "has not run" in result.stderr


def test_a_failed_ls_files_read_refuses_before_the_denylist_is_ever_reached(tmp_path):
    """The fail-closed direction of the shipped-inventory gate SPEC 13.2
    added in front of the denylist scan: `.github/check_shipped_files.py`
    calls `git -C <repo root> ls-files -- <directory>` for every shipped
    directory, and a failing `git` there is not a clean result. The script
    itself now reports this as a failure and says the inventory was not
    checked (rather than raising), and the hook must still refuse the
    commit for it - before it ever looks at `companion.denylist`, which
    this test leaves unconfigured to prove the ordering."""
    result = _run_pre_commit_hook(tmp_path, ls_files_status=2, ls_files_stdout="")

    assert result.returncode != 0, (
        f"a failed `git ls-files` must refuse the commit, "
        f"got returncode {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "companion.denylist is not configured" not in combined, (
        "the shipped-inventory gate must fail before the denylist lookup is "
        f"ever reached; got {combined!r}"
    )
    assert "has not been checked" in combined, (
        f"a failed `ls-files` must say the inventory was not checked, "
        f"not merely that the commit was refused; got {combined!r}"
    )


def test_an_unset_denylist_is_the_existing_failure_not_a_git_failure(tmp_path):
    """`git config --get` exits 1 for a key that is simply not set, and
    `tolerate=(1,)` is the one place the hook is meant to accept that. This
    must still produce the original "not configured" message, distinct from
    the new "git failed" one, or the tolerate path is not actually reached."""
    result = _run_pre_commit_hook(tmp_path, config_stdout="", config_status=1)

    assert result.returncode != 0
    assert "companion.denylist is not configured" in result.stderr
    assert "failed with status" not in result.stderr


def test_a_git_config_failure_other_than_unset_is_treated_as_a_failure(tmp_path):
    """Only status 1 is tolerated. Any other nonzero status from `git config
    --get` - a corrupt config, a git that cannot run - must be reported as
    the scan not having run, not misread as "the key is simply unset"."""
    result = _run_pre_commit_hook(tmp_path, config_stdout="", config_status=2)

    assert result.returncode != 0
    assert "git config --get companion.denylist" in result.stderr
    assert "failed with status 2" in result.stderr
    assert "companion.denylist is not configured" not in result.stderr


def test_a_clean_staged_diff_exits_zero(tmp_path):
    denylist = _write_denylist(tmp_path, r"MyRealSecretHost\d+")

    result = _run_pre_commit_hook(
        tmp_path,
        config_stdout=str(denylist),
        staged_stdout="README.md\n",
        diff_stdout="+Nothing here matches the denylist.\n",
    )

    assert result.returncode == 0, (
        f"a clean staged diff must exit 0, got returncode {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# SPEC.md 13.3, issue #203: the hook now also runs `.github/check_secrets.py
# --staged` before a commit, alongside the denylist gate, rather than
# leaving the generic scan to CI alone. These tests run the real hook
# against real `git` in a genuine throwaway repository under `tmp_path`,
# instead of the `_write_git_stub` technique the rest of this module uses:
# the stub answers every call itself, so it cannot produce a real staged
# index for `check_secrets.py --staged` to read out from underneath it.
#
# Both `.githooks/pre-commit` and `.github/check_secrets.py` fix their own
# repository root from their own `__file__` rather than from the caller's
# cwd (the same fact `_real_shipped_ls_files_stdout`'s docstring records for
# `check_shipped_files.py`'s `REPO_ROOT`), confirmed live for this module by
# running the hook from a throwaway cwd and observing it read this
# checkout's own state rather than the scratch one. Running the real hook
# at its real path against a scratch cwd therefore cannot exercise a
# scratch commit at all - it would always inspect this checkout's own
# index. `_scratch_repo_with_gates` works around this the way
# `_scratch_scanner_repo` in `tests/test_check_secrets.py` already does for
# `check_secrets.py` alone: mechanically copying the hook and both scripts
# it shells out to into a throwaway repository at the same relative
# layout, so every one of their `__file__`-derived roots resolves inside
# the scratch tree instead of this checkout.
#
# No real denylist path or pattern is used here either - `companion.
# denylist` is set to a throwaway file with a pattern built not to match
# anything, so these tests exercise the generic-scan gate specifically
# rather than the denylist one.
# ---------------------------------------------------------------------------

CHECK_SECRETS_SCRIPT = REPO_ROOT / ".github" / "check_secrets.py"


def _scratch_repo_with_gates(tmp_path, *, init_git=True):
    """A throwaway repository carrying mechanically copied, unmodified
    copies of `.githooks/pre-commit`, `.github/check_shipped_files.py` and
    `.github/check_secrets.py` at their real relative layout - `shutil.
    copy2` moves bytes, it is not this module reading either script to
    learn how it works.
    """
    repo = tmp_path / "repo"
    githooks_dir = repo / ".githooks"
    github_dir = repo / ".github"
    githooks_dir.mkdir(parents=True)
    github_dir.mkdir(parents=True)
    shutil.copy2(PRE_COMMIT_HOOK, githooks_dir / "pre-commit")
    shutil.copy2(SHIPPED_FILES_CHECK, github_dir / "check_shipped_files.py")
    shutil.copy2(CHECK_SECRETS_SCRIPT, github_dir / "check_secrets.py")
    if init_git:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "scratch@example.test"],
            cwd=repo, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "scratch"],
            cwd=repo, check=True, capture_output=True,
        )
    return repo


def _populate_and_stage_declared_shipped_files(repo):
    """Creates and stages a harmless placeholder at every path
    `check_shipped_files.py`'s own `SHIPPED_DIRECTORIES` declares, read at
    run time the same way `_real_shipped_ls_files_stdout` reads it above,
    so the shipped-inventory gate - which SPEC.md 13.2 runs first,
    unconditionally, ahead of both the denylist and the generic scan -
    passes cleanly in these scratch repositories rather than refusing the
    commit for a reason unrelated to what each test is actually about.
    """
    spec = importlib.util.spec_from_file_location(
        "shipped_check_under_hook_scratch", SHIPPED_FILES_CHECK
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for directory, names in module.SHIPPED_DIRECTORIES.items():
        for name in names:
            path = repo / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"harmless placeholder, no credential shape at all")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)


def _configure_harmless_denylist(repo, tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("NeverMatchesAnythingSynthetic9000\n", encoding="utf-8")
    subprocess.run(
        ["git", "config", "--local", "companion.denylist", str(denylist)],
        cwd=repo, check=True, capture_output=True,
    )
    return denylist


def _credential_probe():
    """A `keyword = "value"` line assembled from separate pieces at
    runtime, the same reasoning `tests/test_check_secrets.py` states for its
    own `_credential_assignment_probe`: this module's whole subject here
    includes credential-shaped strings, and a probe spelled out as one
    contiguous literal would be exactly what a real pasted credential looks
    like.
    """
    return "api_key = " + '"synthetic-example-value"'


def _run_scratch_hook(repo):
    return subprocess.run(
        ["python3", str(repo / ".githooks" / "pre-commit")],
        cwd=str(repo),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_hook_refuses_a_commit_carrying_a_synthetic_credential(tmp_path):
    """The scan SPEC.md 13.3 moves in front of the commit: a staged file
    carrying a credential-shaped line must refuse the commit, and the
    hook's *own* line must say `found a possible credential` - SPEC.md
    13.3's pinned contract, added after a mutation run showed that
    asserting only on the wrapped scanner's own "possible credential
    assignment" text is not enough: the hook echoes that scanner's stderr
    on both its exit-1 and its exit-not-0-or-1 paths, so a hook that has
    misrouted a real finding into the broken-gate branch still produces
    output containing that phrase. Asserting the hook's own sentence is
    what tells the two branches apart.
    """
    repo = _scratch_repo_with_gates(tmp_path)
    _populate_and_stage_declared_shipped_files(repo)
    _configure_harmless_denylist(repo, tmp_path)
    target = repo / "config.py"
    target.write_text(_credential_probe() + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "config.py"], cwd=repo, check=True, capture_output=True)

    result = _run_scratch_hook(repo)

    assert result.returncode != 0, (
        f"a commit carrying a synthetic credential must be refused\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "found a possible credential" in combined, (
        f"expected the hook's own SPEC.md 13.3 sentence - not merely the wrapped "
        f"scanner's own stderr - to say a credential was found: {combined!r}"
    )


def test_hook_refuses_a_credential_added_while_renaming_a_file(tmp_path):
    """The headline defect this change closes (SPEC.md 13.3), at the hook
    level: a file renamed and modified in the same staged commit, with the
    credential only in the added content. `git mv` plus an edit is what git
    itself classifies `R` (asserted below via `--name-status`, the same way
    `tests/test_check_secrets.py`'s scanner-level version of this test
    does), and a filter that only admitted `A`, `C` and `M` returned
    nothing for it, so the hook took its "nothing staged" path and never
    ran the owner denylist gate on the commit at all - a rename that also
    adds a credential walked straight past it. A plain rename with no
    modification is not this case: it has nothing to find, and would pass
    under either filter.
    """
    repo = _scratch_repo_with_gates(tmp_path)
    _populate_and_stage_declared_shipped_files(repo)
    _configure_harmless_denylist(repo, tmp_path)

    original_lines = [f"line {i}" for i in range(50)]
    old_path = repo / "sub" / "old_name.py"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "sub/old_name.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "scratch fixture base commit"],
        cwd=repo, check=True, capture_output=True,
    )

    new_path = repo / "sub" / "new_name.py"
    subprocess.run(
        ["git", "mv", "sub/old_name.py", "sub/new_name.py"],
        cwd=repo, check=True, capture_output=True,
    )
    new_path.write_text(
        "\n".join(original_lines) + "\n" + _credential_probe() + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "sub/new_name.py"], cwd=repo, check=True, capture_output=True)

    name_status = subprocess.run(
        ["git", "diff", "--cached", "--name-status"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert name_status.startswith("R"), (
        f"fixture did not produce a rename git itself classifies as R, so this test would "
        f"not exercise the case it is named for: {name_status!r}"
    )

    result = _run_scratch_hook(repo)

    assert result.returncode != 0, (
        f"a commit renaming a file and adding a credential to it must be refused\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "found a possible credential" in combined, (
        f"expected the hook's own line to say a credential was found: {combined!r}"
    )


def test_hook_relays_the_scanners_own_exit_2_and_does_not_claim_a_credential(tmp_path):
    """SPEC.md 13.3's fail-closed rule applied to the new gate, reached
    properly this time: an earlier version of this test set `init_git=False`
    on `_scratch_repo_with_gates`, which also breaks the shipped-inventory
    gate's own `git ls-files` call - that gate runs first and unconditionally
    (SPEC.md 13.3's own stated order: shipped inventory, then the generic
    scan, then the denylist), so the hook refused before the secret-scan
    block was ever reached at all. That version passed with the entire
    exit-2 relay, the missing-script branch and the exit-1 branch deleted,
    which is exactly the failure mode a mutation run is for.

    This version leaves the shipped-inventory gate its default clean
    answer (`_write_git_stub`'s `ls_files_stdout` default) and fails only
    `check_secrets.py --staged`'s own staged-name listing (`staged_z_status`,
    matched on `-z` and independent of the hook's own denylist listing -
    see `_write_git_stub`'s docstring). SPEC.md 13.3's pinned contract:
    `found a possible credential` must appear in the hook's own line when,
    and only when, the scanner exited 1 - here it exited 2, so that phrase
    must be absent, and the hook's own line must instead name the status it
    got (`2`) as one it does not understand.
    """
    denylist = _write_denylist(tmp_path, "NeverMatchesAnythingSynthetic9000")

    result = _run_pre_commit_hook(
        tmp_path,
        config_stdout=str(denylist),
        staged_z_status=2,
    )

    assert result.returncode != 0, (
        f"a scanner that cannot run to completion must refuse the commit\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "found a possible credential" not in combined, (
        f"the scanner exited 2, not 1; the hook's own line must not claim a "
        f"credential was found: {combined!r}"
    )
    assert re.search(r"\b2\b", combined), (
        f"expected the hook's own line to name the status it got (2): {combined!r}"
    )


def test_a_staged_diff_matching_the_denylist_refuses_and_never_prints_the_match(tmp_path):
    denied_pattern = "TotallyFakeInternalHostname"
    matching_line = f"+  our internal box is {denied_pattern}42, do not publish this"
    denylist = _write_denylist(tmp_path, denied_pattern)

    result = _run_pre_commit_hook(
        tmp_path,
        config_stdout=str(denylist),
        staged_stdout="notes.md\n",
        diff_stdout=matching_line + "\n",
    )

    assert result.returncode != 0, (
        f"a staged diff matching a denylist pattern must refuse to commit, "
        f"got returncode {result.returncode}"
    )
    combined = result.stdout + result.stderr
    assert denied_pattern not in combined, (
        "the matching line is the secret and must never be printed "
        f"(SPEC 13); got {combined!r}"
    )
    assert matching_line not in combined
    assert "denylist pattern(s) matched" in combined
