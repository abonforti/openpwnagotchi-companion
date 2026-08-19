#!/usr/bin/env python3
"""Refuses to let an unexpected file into a directory that ships to the unit.

`frontend/public/` is Vite's static directory: everything in it is copied
verbatim into `dist/`, packed, unpacked into `web_root` by
`tools/install-on-pi.sh`, and served by the plugin's static server (SPEC 2.15).
A stray file there is not untidiness, it is a file published to anyone who can
reach the unit over the tether.

That nearly happened once. An agent left a full copy of `frontend/src/` at
`frontend/public/src/`, including test fixtures, and the only thing between it
and a commit was somebody reading `git status` at the right moment (#117). This
script is that reading, done mechanically.

The inventory is exact in both directions. An extra file fails, and so does a
missing one: a declared file that disappears means the app shipped without its
icon or its manifest, which is the same class of mistake seen from the other
side.

Both what git tracks and what is on disk are checked, because the two catch
different things. Git alone misses the untracked scratch that `git add -A` is
about to sweep in; the disk alone misses a file that is tracked but was deleted
locally.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every directory whose contents are copied verbatim into the build, mapped to
# exactly what belongs in it, relative to the directory itself.
#
# `frontend/public` is the only one today: `dist/` is built rather than copied,
# and the plugin ships as a single file named explicitly by the installer. Add a
# directory here the day the build starts copying one, rather than the day
# something unexpected turns up in it.
SHIPPED_DIRECTORIES = {
    "frontend/public": {
        "icons/apple-touch-icon-180.png",
        "icons/icon-192.png",
        "icons/icon-512.png",
        "icons/icon-maskable-512.png",
        "manifest.webmanifest",
    },
}


class GitUnavailable(Exception):
    """Raised when git could not be read, which is never a clean result."""


def tracked_files(directory: str) -> set[str]:
    """What git has under `directory`, relative to it.

    Reads the exit status rather than only stdout: a git that could not run
    returns nothing, and nothing looks exactly like an empty directory, which
    would turn a check that did not happen into a pass (SPEC 13). Reported as a
    message and a failing status rather than as a traceback, because the caller
    is a commit hook and a stack trace there says nothing about what to do.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", directory],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(
            f"::error::`git ls-files -- {directory}` failed with status {result.returncode}. "
            "The shipped inventory has not been checked, so this is a failure rather than a pass."
        )
        raise GitUnavailable
    prefix = f"{directory}/"
    return {line[len(prefix) :] for line in result.stdout.splitlines() if line.startswith(prefix)}


def present_files(directory: str) -> set[str]:
    """What is on disk under `directory`, relative to it.

    Anything that is not a real directory counts as an entry, rather than only
    what `is_file()` accepts. A symlink to a directory answers False to both
    `is_file()` and, for walking purposes, cannot be descended without following
    it, so filtering on `is_file()` alone would let `frontend/public/src ->
    ../src` through: the #117 incident exactly, in one line instead of twenty
    files. A dangling symlink is skipped by `is_file()` for the same reason.
    Neither is declared, so both must be reported.

    Symlinks are named but not followed. What is on the other side is a question
    about a path outside the shipped directory, and the answer does not change
    the verdict: an undeclared entry fails either way.
    """
    root = REPO_ROOT / directory
    if not root.is_dir():
        return set()
    return {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }


def main() -> int:
    failed = False

    for directory, expected in sorted(SHIPPED_DIRECTORIES.items()):
        try:
            found = tracked_files(directory) | present_files(directory)
        except GitUnavailable:
            return 1

        unexpected = sorted(found - expected)
        missing = sorted(expected - found)

        for name in unexpected:
            print(
                f"::error file={directory}/{name}::{directory}/{name} is not in the shipped "
                f"inventory. Everything in {directory} is served by the unit."
            )
            failed = True

        for name in missing:
            print(
                f"::error::{directory}/{name} is declared as shipped and is not there. "
                "Either restore it or remove it from .github/check_shipped_files.py."
            )
            failed = True

        if not unexpected and not missing:
            print(f"{directory}: {len(expected)} files, inventory matches")

    if failed:
        print(
            "\nThe inventory is in .github/check_shipped_files.py. If a file belongs in the "
            "build, add it there in the same commit; if it is scratch, it belongs outside the "
            "repository entirely."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
