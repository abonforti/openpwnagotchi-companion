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
) -> Path:
    """A `git` stub answering the four calls the hook, directly or through
    `.github/check_shipped_files.py`, makes: `-C <repo root> ls-files --
    <directory>`, `config --get companion.denylist`, `diff --cached
    --name-only --diff-filter=ACM`, and `diff --cached --unified=0 --
    <files>`. Each call's stdout and exit status are controlled
    independently, so a test can make either `diff` call fail without
    touching the other, and can make the config lookup fail with a status
    other than the one the hook tolerates.

    `ls-files` defaults to the real declared inventory
    (`_real_shipped_ls_files_stdout()`) so that, unless a test asks
    otherwise, the shipped-inventory check the hook now runs first passes
    and the hook proceeds to the denylist logic every test here besides the
    shipped-inventory one is actually about. The `ls-files` invocation is
    `git -C <path> ls-files -- <directory>`, so it is matched by the
    presence of the `ls-files` subcommand anywhere in the arguments, not by
    position - `args[:1]` is `-C` for that call, unlike the other three.

    Any invocation that matches none of the four fails loudly instead of
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
        "if 'ls-files' in args:\n"
        "    sys.stdout.write(LS_FILES_STDOUT)\n"
        "    sys.exit(LS_FILES_STATUS)\n"
        "if args[:1] == ['config']:\n"
        "    sys.stdout.write(CONFIG_STDOUT)\n"
        "    sys.exit(CONFIG_STATUS)\n"
        "if args[:1] == ['diff'] and '--name-only' in args:\n"
        "    sys.stdout.write(STAGED_STDOUT)\n"
        "    sys.exit(STAGED_STATUS)\n"
        "if args[:1] == ['diff'] and '--unified=0' in args:\n"
        "    sys.stdout.write(DIFF_STDOUT)\n"
        "    sys.exit(DIFF_STATUS)\n"
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
    """The regression itself: `git diff --cached --name-only
    --diff-filter=ACM` failing must not be read as "nothing is staged". It
    must fail the hook and say the scan did not run, not exit 0."""
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
