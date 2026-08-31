#!/usr/bin/env python3
"""Fails the build on a `<script>` in `frontend/dist/` that `script-src
'self'` does not cover.

SPEC 2.15.1 pins `script-src 'self'` in the CSP the plugin serves. That
directive covers a script this unit serves and nothing else: not an inline
block, which runs regardless of the header, and not a `src` naming another
origin, which the browser refuses exactly as it refuses an inline block.
Today's build never emits either: `vite-plugin-pwa` with `injectRegister:
'auto'` writes an external, same-origin `registerSW.js`, and the app bundle is
always an external, same-origin module script. Both of those are a property
of the current build configuration, not of the tools, and either could change
under an option flip, a `base` pointed elsewhere, or a plugin emitting an
absolute URL, without anyone noticing, because a page like that still loads
fine in a browser - only the policy silently stops meaning what it says, and
the failure shows up on the unit rather than in CI.

This is that check, done mechanically instead of by someone reading a diff of
`vite.config.ts` and knowing what it implies for the CSP (issue #141).

The rule is exactly what SPEC 2.15.1 states: a `<script>` element fails unless
its `src` is a relative or root-relative path to a file this build produced -
no scheme, and no leading `//`. That excludes an element with no `src`, one
whose `src` is empty or whitespace-only (SPEC 2.15.1), and one whose `src`
names another origin (SPEC 2.15.1) - all three are read as the same property
rather than as separate cases: is this a file the policy can cover.

Every HTML file under `dist/` is inspected, found by a recursive search rather
than by naming `dist/index.html` alone (SPEC 2.15.1, issue #144). The build
emits exactly one page today, but a second entry point - including one placed
in a subdirectory - would otherwise ship unchecked while this check reported
success: not wrong about the file it read, wrong about the question it was
asked. Files are processed in sorted order, so a failing build names the same
file first on every run, and finding no HTML file at all is itself a failure:
a build step that produced no page did not run, and reporting that as clean
would be the same vacuous gate from the other side.

Widening the search to "every page" gave up the one thing naming a single file
got for free: a build that never wrote `dist/index.html` used to fail outright,
and a search does not notice its absence on its own if some other page exists.
So `dist/index.html` is asserted to exist by name, separately from the search
- the app has one entry point (SPEC 4.2), and a build without it is broken
whatever else it emitted.

The search itself does not use `Path.rglob`, which silently drops a
subdirectory it cannot list and never follows a symlinked one - both are a
gate reporting success having examined fewer pages than exist, which is the
same failure this rewrite exists to close. A directory `os.walk` cannot read
is reported as a failure naming that directory, and a symlinked directory is
neither followed (a symlink loop would hang the job) nor silently skipped
(the gate cannot vouch for what is inside) - it fails instead, naming itself
as the reason (SPEC 2.15.1, issue #144).
"""

from __future__ import annotations

import errno
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "frontend" / "dist"

# RFC 3986 scheme syntax: a leading letter, then letters, digits, '+', '.' or
# '-', followed by ':'. Matching this catches "https:", "data:", "blob:" and
# any other scheme, not only the ones seen in practice.
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def is_same_origin_path(src: str) -> bool:
    """True if `src` can only resolve to a file this build serves.

    A scheme (`https:`, `data:`, ...) or a leading `//` (protocol-relative,
    resolved against whatever scheme the page was loaded with) both name an
    origin that is not necessarily this one, and `script-src 'self'` does not
    cover either. Anything else - a root-relative path, a path relative to the
    page, or a bare filename - resolves against this origin and is covered.

    The value is normalised first the way the WHATWG URL parser normalises it
    before resolving: ASCII tab, LF and CR are removed wherever they appear,
    which reunites a scheme split by one of them ("ht\tps://..." resolves as
    "https://..."), and a backslash is treated as a slash, because a special
    scheme resolves "\\host/x.js" as protocol-relative exactly like
    "//host/x.js". Neither rewrite defends against a deliberately crafted
    `src` - this gate is for an accidental build-configuration regression, and
    anyone able to craft one can also edit this script - it is done because it
    is three lines and a known, unclosed blind spot invites doubt about the
    ones that are closed. NUL and the other C0 controls are left unstripped,
    for the same reason: a browser strips a leading one too, so
    "\x00https://host/x.js" still reads as same-origin here, a named gap
    rather than a closed one.
    """
    normalized = src.translate(str.maketrans("", "", "\t\n\r")).replace("\\", "/")
    return not normalized.startswith("//") and not SCHEME.match(normalized)


class ScriptTagFinder(HTMLParser):
    """Collects every `<script>` start tag and flags the ones `'self'` does not cover.

    `html.parser` is used rather than a regular expression: a `<script>` can
    carry other attributes in any order and its content can contain `<` and
    `>`, both of which a regular expression over the raw markup would get
    wrong for no gain, on a file this small.
    """

    def __init__(self) -> None:
        super().__init__()
        self.total = 0
        self.uncovered: list[tuple[int, int]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        self.total += 1
        src = next((value for name, value in attrs if name == "src"), None)
        if src is None or not src.strip() or not is_same_origin_path(src.strip()):
            self.uncovered.append(self.getpos())


def check_file(path: Path) -> int | None:
    """Checks a single HTML file, printing every failure found in it.

    Returns the number of same-origin `<script>` elements found if the file
    passed, or None if it failed. A file is checked to completion even if it
    fails, so one run reports everything wrong in it rather than stopping at
    the first bad element.
    """
    rel = _rel_or_raw(path)

    # A missing or unreadable file is a failure, not a clean result: this step
    # runs after the build, so its absence means the build did not produce
    # what CI thinks it did, not that there is nothing to check (SPEC 13).
    #
    # A read that fails on a file is the one ambiguous case this gate has: the
    # call really was made on `path`, so `path`'s own mode is a candidate, but
    # so is its parent's - a directory made unreadable makes every open()
    # beneath it fail the same way, and naming only the file points the
    # reader at the wrong inode to fix (issue #160). Both are named, but only
    # where a parent's mode can actually have caused the failure, which is
    # EACCES and nothing else: path-resolution denial is EACCES, while EPERM
    # from open() is about a property of the file itself - an immutable or
    # append-only attribute, O_NOATIME without ownership, sealing - and never
    # about a parent directory's mode, so appending the clause there points
    # the reader at an inode that cannot be implicated (issue #160, inverted).
    #
    # ENOTDIR is excluded for a different reason than mode: it is reachable
    # here only by a race after os.walk already listed the parent as a
    # directory - a parent path component replaced by a non-directory between
    # the listing and this open(). The inode to fix there is the replaced
    # component itself, which the reader already has from `rel`, not the
    # parent's mode.
    #
    # The reachable errno set for this call is small: ENOENT from a dangling
    # symlink, ELOOP from a symlink loop (html.parser sees it as a file and
    # gets no clause, correctly), ENOTDIR as above, and EACCES, the one
    # genuinely ambiguous case. EISDIR is not merely hard to fixture here: it
    # is essentially unreachable, because os.walk puts a real directory in
    # dirnames rather than filenames, and a symlinked one is caught and
    # removed by find_html_files() before check_file() ever sees it - the
    # only route left is the DT_UNKNOWN/failed-classification hole this file
    # already names as open, above.
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as exc:
        parent_rel = _rel_or_raw(path.parent)
        reason = f"could not read {rel}: {exc.strerror or exc.errno}"
        if exc.errno == errno.EACCES:
            reason += f". {parent_rel} may be the directory whose mode is actually the cause."
        print(f"::error file={rel}::{reason}")
        return None
    except UnicodeDecodeError as exc:
        # A page that opened but is not valid UTF-8 is a different failure
        # from one that would not open at all: this one wants a wrong or
        # corrupt build artifact investigated, not a permission or mode
        # (issue #159). Handled here, separately from OSError, because
        # UnicodeDecodeError is a ValueError and would otherwise escape this
        # function entirely and end the run in a traceback with no
        # `::error file=` annotation for the reader.
        print(
            f"::error file={rel}::{rel} could not be decoded as UTF-8: {exc.reason} at byte "
            f"offset {exc.start}. This is a wrong or corrupt build artifact, not a permission "
            "problem - the file opened, its bytes just are not valid UTF-8."
        )
        return None

    parser = ScriptTagFinder()
    parser.feed(html)
    # Flushes whatever html.parser is still holding for an incomplete
    # trailing construct. Without it, a truncated artifact simply leaves that
    # construct unprocessed and unreported, which is the wrong failure mode
    # to be silent about even though, for a bare truncated opening tag,
    # html.parser discards it either way rather than surfacing it: the
    # element was never complete enough to register as a <script> at all, so
    # it cannot land in `total` or `uncovered` with or without this call. A
    # browser would not run that tag either. close() is called regardless,
    # because relying on that discard behaviour rather than on an explicit
    # end-of-input is not a distinction worth keeping the buffered state for.
    parser.close()

    # The app ships exactly two script tags today: the module bundle and the
    # service-worker registration. A file with none is far more likely to be
    # an empty or truncated build artifact than a page that has stopped
    # needing a script, and a check that passes on that file would be the
    # vacuous gate this repository has already been bitten by twice. Fail
    # rather than treat "found nothing to check" as "found nothing wrong".
    if parser.total == 0:
        print(
            f"::error file={rel}::no <script> element was found in {rel}. "
            "The build is expected to emit at least one; this is more likely a wrong or "
            "empty file than a clean result, so the check is failing rather than passing "
            "vacuously."
        )
        return None

    if parser.uncovered:
        for line, column in parser.uncovered:
            print(
                f"::error file={rel},line={line}::{rel}:{line}:{column} has a "
                "<script> element that script-src 'self' does not cover: its src is missing, "
                "empty, whitespace-only, or names another origin. Give it a relative or "
                "root-relative src to a file this build produces."
            )
        return None

    return parser.total


def _rel_or_raw(path: Path) -> str:
    """`path` relative to the repo root, or its raw string if it lies outside
    that root. An `OSError` from `os.walk` can name a path anywhere the walk
    reached, not only inside `dist/`, and `relative_to` raises rather than
    returning something approximate for a path it cannot make relative.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def find_html_files(dist_dir: Path) -> tuple[list[Path], list[OSError], list[Path]]:
    """Walks `dist_dir` for `.html` files, reporting what a plain recursive
    search would silently drop.

    `Path.rglob` is not used here: it drops a subdirectory it cannot list
    without a trace, and it never descends into a symlinked one either, both
    silently. `os.walk` is used instead, with an `onerror` callback that
    records rather than swallows a listing failure, plus an explicit check
    for a symlinked entry in `dirnames` - `os.walk` already declines to
    follow one on its own (`followlinks` defaults to False), but declining
    quietly is the same vacuous pass wearing a different cause, so it is
    recorded and removed from `dirnames` rather than left unremarked.

    One case stays open: `os.walk` swallows an error raised while classifying
    an entry as a file or a directory and reclassifies it as a file, so on a
    filesystem answering `DT_UNKNOWN`, or for an entry whose `stat` fails
    after its name was listed, a subdirectory can go undescended without
    reaching `onerror` at all. Closing that means walking with `os.scandir`
    directly; this function does not (SPEC 2.15.1).

    A directory that `os.walk` could list but not stat into - readable but
    not executable (mode `0444`), rather than unreadable outright - reaches
    here with the failure still ahead of it: `is_symlink()` on a child of
    such a directory calls `lstat` and can raise `OSError` the same way
    `scandir` does. Any such stat-like call made while walking a directory's
    children is treated the same as the walk itself failing on that
    directory (SPEC 2.15.1): caught, recorded into `unreadable`, and reported
    with the same named message a listing failure gets, rather than left to
    propagate as a traceback with a local path on stderr. The directory named
    is the one the failing call was made on - the parent whose mode is
    actually wrong - not the child `lstat` happened to be asked about
    (SPEC 2.15.1): a child's name says which call broke, not what the reader
    has to fix. Once one child of a directory fails this way, the rest of
    that directory's entries are skipped rather than each raising the same
    error again for the same cause.

    Returns the HTML files found (unsorted - the caller sorts), the OSErrors
    `os.walk` could not read past (or stat past), and the directories found
    to be symlinks.
    """
    html_files: list[Path] = []
    unreadable: list[OSError] = []
    symlinked: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(dist_dir, onerror=unreadable.append):
        root = Path(dirpath)
        root_failed = False
        for name in list(dirnames):
            if root_failed:
                dirnames.remove(name)
                continue
            child = root / name
            try:
                is_symlink = child.is_symlink()
            except OSError as exc:
                # The wrong mode is on `root`, not on `child`: `lstat` needed
                # execute permission on `root` to look `child` up at all, so
                # `root` is what the reader has to fix, not the name that
                # happened to be asked about when the permission ran out.
                exc.filename = str(root)
                unreadable.append(exc)
                dirnames.remove(name)
                root_failed = True
                continue
            if is_symlink:
                symlinked.append(child)
                dirnames.remove(name)
        for name in filenames:
            if name.endswith(".html"):
                html_files.append(root / name)

    return html_files, unreadable, symlinked


def main() -> int:
    all_ok = True
    dist_rel = _rel_or_raw(DIST_DIR)

    # A search over every page gave up the one thing naming a single file got
    # for free: a build that never wrote dist/index.html used to fail
    # outright, and a search does not notice its absence if some other page
    # exists. The app has one entry point (SPEC 4.2), so this is asserted
    # separately from the search below (SPEC 2.15.1, issue #144).
    index_html = DIST_DIR / "index.html"
    index_rel = _rel_or_raw(index_html)
    # dist/ itself can be unreadable (e.g. mode 000), and `Path.is_file()`
    # does not swallow that the way it swallows a merely missing path - it
    # propagates the OSError. That is a different failure from "the build
    # ran and produced no entry point", so it gets its own message naming
    # the directory rather than the file, and the search below is skipped
    # rather than rediscovering the same unreadable directory a second way.
    dist_unreadable = False
    try:
        index_missing = not index_html.is_file()
    except OSError as exc:
        print(
            f"::error file={dist_rel}::{dist_rel} could not be read: {exc.strerror or exc.errno}. "
            "A directory the gate cannot open is a failure, not a build that ran and "
            "produced no entry point."
        )
        all_ok = False
        dist_unreadable = True
    else:
        if index_missing:
            print(
                f"::error file={index_rel}::{index_rel} does not exist. The app has a single "
                "entry point; a build that did not produce it is broken, whatever else it "
                "emitted."
            )
            all_ok = False

    html_files: list[Path] = []
    unreadable_dirs: list[OSError] = []
    symlinked_dirs: list[Path] = []
    if not dist_unreadable and DIST_DIR.is_dir():
        html_files, unreadable_dirs, symlinked_dirs = find_html_files(DIST_DIR)

    # dist/ itself can also turn up here rather than through the is_file()
    # check above: at mode 0111 (searchable but not readable), stat'ing the
    # single named file dist/index.html succeeds, and the failure to read
    # comes from the walk's own scandir(dist/) call instead. What matters for
    # the "no HTML anywhere" message below is what the failure is about, not
    # which call surfaced it, so this is checked against every recorded
    # failure rather than only the is_file() branch.
    dist_named_in_failure = dist_unreadable or any(
        _rel_or_raw(Path(exc.filename or DIST_DIR)) == dist_rel for exc in unreadable_dirs
    )

    for exc in sorted(unreadable_dirs, key=lambda exc: exc.filename or ""):
        dir_rel = _rel_or_raw(Path(exc.filename or DIST_DIR))
        print(
            f"::error file={dir_rel}::{dir_rel} could not be read: {exc.strerror or exc.errno}. "
            "A subtree the gate cannot see into is a failure, not a pass having examined "
            "fewer pages than exist."
        )
        all_ok = False

    for dir_path in sorted(symlinked_dirs):
        dir_rel = _rel_or_raw(dir_path)
        print(
            f"::error file={dir_rel}::{dir_rel} is a symlinked directory and was not "
            "followed. Following it risks a symlink loop that would hang the job; not "
            "following it means the gate cannot vouch for what is inside, so it fails "
            "rather than silently choosing the second."
        )
        all_ok = False

    # No HTML at all is a failure, not a vacuous pass: a build step that
    # produced no page did not run, and there is nothing here for this check
    # to have looked at (SPEC 2.15.1). Skipped when dist/ itself is named in
    # a failure above, by whichever call found it, and skipped just the same
    # when any subtree could not be read or was a symlink not followed: the
    # gate has just said it cannot see into part of the tree, so it does not
    # know whether a page is in there, and asserting "no page anywhere" right
    # after would contradict the message printed one line above it.
    if not html_files:
        if not dist_named_in_failure and not unreadable_dirs and not symlinked_dirs:
            print(
                f"::error file={dist_rel}::no HTML file was found under {dist_rel}. The "
                "build is expected to emit at least one page; this is more likely a build "
                "that did not run than a clean result, so the check is failing rather than "
                "passing vacuously."
            )
        return 1

    total_scripts = 0
    for path in sorted(html_files):
        result = check_file(path)
        if result is None:
            all_ok = False
        else:
            total_scripts += result

    if not all_ok:
        return 1

    print(
        f"{len(html_files)} HTML file(s), {total_scripts} <script> element(s), "
        "all same-origin"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
