#!/usr/bin/env python3
"""Fails the build on a `<script>` in `frontend/dist/index.html` that `script-src
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

Only `dist/index.html` is inspected, because the build emits exactly one HTML
file today. A second entry point would need a second path added here; this
check would otherwise pass while never having looked at it.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "frontend" / "dist" / "index.html"

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


def main() -> int:
    # A missing or unreadable file is a failure, not a clean result: this step
    # runs after the build, so its absence means the build did not produce
    # what CI thinks it did, not that there is nothing to check (SPEC 13).
    try:
        html = INDEX_HTML.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"::error file={INDEX_HTML}::could not read {INDEX_HTML}: {exc}")
        return 1

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
            f"::error file={INDEX_HTML}::no <script> element was found in {INDEX_HTML}. "
            "The build is expected to emit at least one; this is more likely a wrong or "
            "empty file than a clean result, so the check is failing rather than passing "
            "vacuously."
        )
        return 1

    if parser.uncovered:
        for line, column in parser.uncovered:
            print(
                f"::error file={INDEX_HTML},line={line}::{INDEX_HTML}:{line}:{column} has a "
                "<script> element that script-src 'self' does not cover: its src is missing, "
                "empty, whitespace-only, or names another origin. Give it a relative or "
                "root-relative src to a file this build produces."
            )
        return 1

    print(f"{INDEX_HTML}: {parser.total} <script> element(s), all same-origin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
