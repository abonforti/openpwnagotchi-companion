"""The no-inline-script gate, `.github/check_no_inline_script.py` (SPEC 2.15.1, issue #141).

`script-src 'self'` in the CSP (SPEC 2.15.1) covers an external `<script src="...">` and does
nothing for an inline one. `vite-plugin-pwa` with `injectRegister: 'auto'` happens to emit an
external `registerSW.js` today, but that is a property of the build configuration, not of the
tools - a later change to that setting could emit an inline registration script instead, and the
CSP directive would then be silently wrong for the page it is supposed to protect. The rule
SPEC 2.15.1 states is exact and this module tests exactly that sentence:

    It fails on any `<script>` element with no `src`, [...] an external script is covered by
    `'self'`, an inline one is not. An empty or whitespace-only `src` counts as no `src`.
    A `src` that points at another origin fails as well [...] the value must be relative or
    root-relative: no scheme, and no leading `//`. A page with no `<script>` at all fails too
    [...] the check reads every HTML file under `dist/`, found by search rather than by name,
    recursively, in sorted order. Finding no HTML at all is a failure. The failure says which
    case it is, since they want different next actions: "no HTML file anywhere" says the build
    did not run, "no scripts in this file" says this page is wrong, "could not read this file"
    says this page could not be opened, and two more join them for a subtree that could not be
    read and one that was not followed. Every one of them names the file or directory it is
    about.

The check runs against every HTML file the real build emits under `dist/`, after `Build`, in
the `frontend` job of `.github/workflows/ci.yml`. SPEC 13 (and issues #112, #126) is explicit
that a gate which cannot run must not be read as a gate that found nothing: a missing or
unreadable file, or a `dist/` with no HTML anywhere, is a failure, never a silent pass.

This module does not read the implementation to decide what to assert - only enough of
`.github/check_no_inline_script.py` to invoke it correctly (no arguments, run the way CI runs
it: `python3 ../.github/check_no_inline_script.py` from a `frontend/` working directory, so
the script has to find every HTML file under `dist/` on its own, the same way it does in CI).

Every test in this module runs the real, committed `.github/check_no_inline_script.py` at
`SCRIPT_PATH`, copied byte-for-byte into a scratch tree that mirrors the two facts the script
needs from the real repository layout: a `frontend/` directory to run from and a `.github/`
directory one level up holding the script. There is no environment-variable seam to redirect it
onto a different copy - proving a mutant is caught is done by copying the whole tree this file
lives under to a directory outside the repository, mutating the copy of
`.github/check_no_inline_script.py` there, and running `pytest` from that copy; `SCRIPT_PATH`,
computed from `__file__`, then resolves to the mutant on its own.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / ".github" / "check_no_inline_script.py"
REAL_DIST_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"


# ---------------------------------------------------------------------------
# Scratch tree helpers
# ---------------------------------------------------------------------------


def _build_scratch_tree(tmp_path: Path) -> Path:
    """A scratch tree with `.github/check_no_inline_script.py` (a copy of the
    real, committed script) one level above an empty `frontend/` directory,
    mirroring the real repository layout closely enough that the script's
    own path resolution - whatever it is - finds the same relative shape it
    finds in CI. Returns the `frontend/` directory, which is the working
    directory the CI step actually runs from."""
    github_dir = tmp_path / ".github"
    github_dir.mkdir(parents=True)
    (github_dir / "check_no_inline_script.py").write_text(
        SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    return frontend_dir


def _write_index_html(frontend_dir: Path, body: str) -> Path:
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    index = dist_dir / "index.html"
    index.write_text(body, encoding="utf-8")
    return index


def _write_html(frontend_dir: Path, relative_path: str, body: str) -> Path:
    """Writes an HTML file at an arbitrary path under `dist/`, creating any
    subdirectories along the way. Used by the multi-file and nested-file
    cases, where `_write_index_html`'s fixed `dist/index.html` name is not
    enough."""
    target = frontend_dir / "dist" / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _clean_html(title: str) -> str:
    """A minimal page carrying exactly one external, same-origin
    `<script>` - the shape that must pass on its own."""
    return (
        "<!doctype html><html><head>"
        f"<title>{title}</title>"
        '<script type="module" src="/assets/index-abc123.js"></script>'
        "</head><body></body></html>"
    )


def _dirty_html(title: str) -> str:
    """A minimal page carrying one inline `<script>` - must fail."""
    return (
        "<!doctype html><html><head>"
        f"<title>{title}</title>"
        '<script>window.__leak = true;</script>'
        "</head><body></body></html>"
    )


def _run_check(frontend_dir: Path) -> subprocess.CompletedProcess:
    """Invoked exactly the way the `frontend` job of `ci.yml` invokes it:
    `python3 ../.github/check_no_inline_script.py`, with `frontend/` as the
    working directory (`defaults.run.working-directory: frontend`)."""
    return subprocess.run(
        [sys.executable, "../.github/check_no_inline_script.py"],
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        timeout=30,
    )


_EXTERNAL_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>TestNet companion</title>
    <script type="module" crossorigin src="/assets/index-ec8oGTRo.js"></script>
  <script id="vite-plugin-pwa:register-sw" src="/registerSW.js"></script></head>
  <body>
    <div id="app"></div>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# The happy path: external scripts only
# ---------------------------------------------------------------------------


def test_external_scripts_pass(tmp_path):
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_index_html(frontend_dir, _EXTERNAL_HTML)

    result = _run_check(frontend_dir)

    assert result.returncode == 0, (
        f"two external <script src> elements, the real build shape, must pass; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_the_real_dist_index_html_passes_if_present():
    """A read-only assertion against the actual checkout, mirroring
    `test_shipped_files.py`'s `test_the_real_repository_passes_as_it_stands`:
    if the real `frontend/dist/index.html` exists (a local build has run),
    it must pass the gate, invoked exactly as CI invokes it. Skipped rather
    than failed when no build has happened, since a missing `dist/` here
    just means nobody built the frontend in this checkout yet - that is a
    separate, deliberately-tested failure mode below, not this one.

    This test never runs in CI: `frontend/dist/` is gitignored and the only
    jobs that could produce it are `frontend` (which builds it and then
    runs the real gate as its own CI step, not through this suite) and
    `e2e` (which never runs `tests/`). It exists for a local checkout after
    `npm run build`, and its absence from every CI run is not a coverage
    gap this suite can close - the gate still runs, just not through this
    test."""
    if not REAL_DIST_INDEX.is_file():
        pytest.skip("frontend/dist/index.html not built in this checkout")

    # The script must find the real dist/index.html on its own; run it the
    # one way that matters, which is the way CI actually invokes it.
    result = _run_check(REPO_ROOT / "frontend")

    assert result.returncode == 0, (
        f"the real, built dist/index.html must pass the gate; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Every inline shape the task calls out, each failing and naming the file
# ---------------------------------------------------------------------------


_INLINE_SHAPES = {
    "block_with_body": '<script>console.log("inline");</script>',
    "empty_no_src": "<script></script>",
    "attributes_but_no_src": '<script type="module" defer></script>',
    "src_present_but_empty": '<script src=""></script>',
    "src_present_but_whitespace_only": '<script src="   "></script>',
}


@pytest.mark.parametrize("shape", sorted(_INLINE_SHAPES))
def test_every_inline_shape_fails_and_names_the_file(tmp_path, shape):
    frontend_dir = _build_scratch_tree(tmp_path)
    html = f"<!doctype html><html><head>{_INLINE_SHAPES[shape]}</head><body></body></html>"
    _write_index_html(frontend_dir, html)

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"shape {shape!r} ({_INLINE_SHAPES[shape]!r}) must fail the gate; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "index.html" in combined, (
        f"shape {shape!r}: the offending file must be named; got {combined!r}"
    )


_CROSS_ORIGIN_FAIL_SHAPES = {
    "absolute_https": '<script src="https://cdn.example.test/index.js"></script>',
    "protocol_relative": '<script src="//cdn.example.test/index.js"></script>',
    # Two leading backslashes: URL parsers treat '\\' as a path separator
    # for special schemes exactly like '/', so two of them resolve
    # protocol-relative to cdn.example.test just as "//cdn.example.test/..."
    # does above. A single backslash would normalise to a single slash - a
    # root-relative, same-origin path - so the count matters and this uses
    # two literal backslash characters (r"\\", a raw string, so nothing is
    # itself escaped away before the HTML is written).
    "leading_backslash": '<script src="' + r"\\" + 'cdn.example.test/index.js"></script>',
    # An ASCII tab splitting the scheme: URL parsers strip tab, LF and CR
    # from anywhere in the input before resolving it, so "ht<TAB>tps:" is
    # "https:" to the parser even though no literal "https:" substring
    # appears in the markup.
    "tab_split_scheme": '<script src="ht\tps://cdn.example.test/index.js"></script>',
}

_RELATIVE_SRC_PASS_SHAPES = {
    "root_relative": '<script src="/assets/index-abc123.js"></script>',
    "plain_relative": '<script src="index-abc123.js"></script>',
}


@pytest.mark.parametrize("shape", sorted(_CROSS_ORIGIN_FAIL_SHAPES))
def test_a_src_pointing_at_another_origin_fails(tmp_path, shape):
    """SPEC 2.15.1: "`script-src 'self'` covers a file this unit serves; it
    does not cover `https://cdn.example.com/index.js`, which the browser
    refuses exactly as it refuses an inline block. [...] the value must be
    relative or root-relative: no scheme, and no leading `//`." A `src`
    with a value is not automatically an external script the policy
    covers - it has to name a same-origin file."""
    frontend_dir = _build_scratch_tree(tmp_path)
    html = (
        f"<!doctype html><html><head>{_CROSS_ORIGIN_FAIL_SHAPES[shape]}</head>"
        "<body></body></html>"
    )
    _write_index_html(frontend_dir, html)

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"shape {shape!r} ({_CROSS_ORIGIN_FAIL_SHAPES[shape]!r}) points at another "
        f"origin and must fail the gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "index.html" in combined, (
        f"shape {shape!r}: the offending file must be named; got {combined!r}"
    )


@pytest.mark.parametrize("shape", sorted(_RELATIVE_SRC_PASS_SHAPES))
def test_a_relative_or_root_relative_src_passes(tmp_path, shape):
    """The other side of the same rule: a `src` with no scheme and no
    leading `//` is a same-origin file `script-src 'self'` covers, whether
    it is root-relative (the real build's shape) or plainly relative."""
    frontend_dir = _build_scratch_tree(tmp_path)
    html = (
        f"<!doctype html><html><head>{_RELATIVE_SRC_PASS_SHAPES[shape]}</head>"
        "<body></body></html>"
    )
    _write_index_html(frontend_dir, html)

    result = _run_check(frontend_dir)

    assert result.returncode == 0, (
        f"shape {shape!r} ({_RELATIVE_SRC_PASS_SHAPES[shape]!r}) is same-origin "
        f"and must pass; stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_a_file_mixing_one_inline_script_among_external_ones_still_fails(tmp_path):
    """One bad script among good ones must not be diluted by the others -
    the rule is per-element, not per-file-on-average."""
    frontend_dir = _build_scratch_tree(tmp_path)
    html = (
        "<!doctype html><html><head>"
        '<script type="module" src="/assets/index-abc123.js"></script>'
        '<script>window.__leak = true;</script>'
        '<script src="/registerSW.js"></script>'
        "</head><body></body></html>"
    )
    _write_index_html(frontend_dir, html)

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a single inline script among external ones must still fail the gate; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Missing or unreadable input fails - never a silent pass (SPEC 13, #112, #126)
# ---------------------------------------------------------------------------


def test_missing_dist_directory_fails(tmp_path):
    """No `dist/` at all - the build did not run, or ran somewhere else.
    This is the "gate that could not run reported as clean" shape SPEC 13
    names issues #112 and #126 for.

    Under the search-based rewrite of issue #144, a wholly missing `dist/`
    is the "Finding no HTML at all is a failure" case, not the older
    "could not read the file" one: with nothing to search, there is no
    specific filename left to say "could not read" *about*. It must still
    be reported and distinguishable from a found file's "no scripts
    found", which `test_the_three_failure_diagnostics_are_distinguished`
    exercises directly; this test only pins that it is not silent."""
    frontend_dir = _build_scratch_tree(tmp_path)
    # Deliberately no frontend/dist/ at all.

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a missing dist/ directory must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "a missing dist/index.html must be reported, not silent"
    assert "no html file was found" in combined.lower(), (
        f"a wholly missing dist/ must read as the search finding nothing, "
        f"not as some other failure; got {combined!r}"
    )
    assert "no <script> element" not in combined.lower(), (
        f"a search that found nothing must not print the per-file "
        f"no-<script>-element wording, which claims a file was found and "
        f"inspected; got {combined!r}"
    )


def test_missing_index_html_fails(tmp_path):
    """`dist/` exists (Vite ran) but holds no HTML file at all - the "no
    HTML at all" case of SPEC 2.15.1, distinct from the directory being
    entirely missing, and must fail the same way and, per the same
    reasoning as `test_missing_dist_directory_fails` above, must not read
    as a found file's "no scripts found" either."""
    frontend_dir = _build_scratch_tree(tmp_path)
    (frontend_dir / "dist").mkdir()

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a dist/ with no index.html must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "a dist/ with no HTML file must be reported, not silent"
    assert "no html file was found" in combined.lower(), (
        f"a dist/ with no HTML file anywhere must read as the search finding "
        f"nothing, not as some other failure; got {combined!r}"
    )
    assert "no <script> element" not in combined.lower(), (
        f"a search that found no HTML file must not print the per-file "
        f"no-<script>-element wording, which claims a file was found and "
        f"inspected; got {combined!r}"
    )
    assert "no scripts" not in combined.lower(), (
        f"a search that found nothing must not read as a file that was found "
        f"and inspected; got {combined!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_unreadable_index_html_fails(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignores file permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    index = _write_index_html(frontend_dir, _EXTERNAL_HTML)
    index.chmod(0)

    try:
        result = _run_check(frontend_dir)
    finally:
        index.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result.returncode != 0, (
        f"an unreadable dist/index.html must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "read" in combined.lower(), (
        f"an unreadable dist/index.html is a read failure and must read as one; got {combined!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_the_three_failure_diagnostics_are_distinguished(tmp_path):
    """SPEC 2.15.1 now names three distinct failure cases: no HTML file
    anywhere, no `<script>` in a found file, and a found file that could
    not be read - "each wants a different next action". Drives all three
    from the same helpers and proves none of them collapse into another,
    pairwise, so a change that makes the no-HTML-anywhere branch print the
    per-file no-`<script>` wording verbatim - which would still pass a
    test that only compares two of the three - is caught here.

    The no-HTML-anywhere case uses a `dist/` that exists and holds only a
    non-HTML build artefact (the same shape `test_dist_with_no_html_at_all_fails`
    uses), matching that test's fixture rather than for its own sake: this
    shape does not remove a confound, because there is none to remove. The
    "no HTML file anywhere" message and the "index.html does not exist"
    message are structurally inseparable - if `index.html` existed the
    search would have found it, so there is no reachable state where the
    search returns nothing while the named file is present - and that
    holds whether `dist/` is wholly missing or exists holding only
    non-HTML output; the combined message carries both lines either way
    (SPEC 2.15.1). The one case where "no HTML file anywhere" would print
    alone is `dist/` itself being unreadable, and that branch is
    suppressed there on purpose (a directory the gate could not open gets
    its own message instead, pinned by
    `test_dist_itself_unreadable_fails_with_a_named_message_not_a_traceback`),
    so it is not available to isolate this message either. What actually
    pins the `no_html` leg below is not the pairwise inequality - which
    the always-present `index.html does not exist` line would satisfy on
    its own regardless of the no-HTML wording - but the positive
    `"no html file was found"` substring assertion further down, which
    reads the message that must be there rather than merely noting it
    differs from the other two."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file permission bits, so this cannot be exercised as root")

    no_html_dir = _build_scratch_tree(tmp_path / "no-html-case")
    (no_html_dir / "dist" / "assets").mkdir(parents=True)
    (no_html_dir / "dist" / "assets" / "index-abc123.js").write_text(
        "console.log('built');\n", encoding="utf-8"
    )
    # dist/ exists and holds a real build artefact, but no HTML file: the
    # no-HTML-anywhere branch, without the missing-index.html confound.
    no_html = _run_check(no_html_dir)

    no_script_dir = _build_scratch_tree(tmp_path / "no-script-case")
    _write_index_html(
        no_script_dir,
        "<!doctype html><html><head><title>TestNet companion</title></head>"
        "<body><div id=\"app\"></div></body></html>",
    )
    no_script = _run_check(no_script_dir)

    unreadable_dir = _build_scratch_tree(tmp_path / "unreadable-case")
    unreadable_index = _write_index_html(unreadable_dir, _EXTERNAL_HTML)
    unreadable_index.chmod(0)
    try:
        unreadable = _run_check(unreadable_dir)
    finally:
        unreadable_index.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert no_html.returncode != 0
    assert no_script.returncode != 0
    assert unreadable.returncode != 0

    no_html_message = (no_html.stdout + no_html.stderr).strip()
    no_script_message = (no_script.stdout + no_script.stderr).strip()
    unreadable_message = (unreadable.stdout + unreadable.stderr).strip()

    assert no_html_message and no_script_message and unreadable_message, (
        f"all three failures must produce a message; "
        f"no_html={no_html_message!r} no_script={no_script_message!r} "
        f"unreadable={unreadable_message!r}"
    )
    messages = {
        "no_html": no_html_message,
        "no_script": no_script_message,
        "unreadable": unreadable_message,
    }
    for name_a, message_a in messages.items():
        for name_b, message_b in messages.items():
            if name_a == name_b:
                continue
            assert message_a != message_b, (
                f"{name_a!r} and {name_b!r} are different failures and must not "
                f"read as the same message; both were {message_a!r}"
            )

    assert "no html file was found" in no_html_message.lower(), (
        f"the no-HTML-anywhere case must name that as the problem; got {no_html_message!r}"
    )
    assert "script" in no_script_message.lower(), (
        f"the no-scripts-found case must name that as the problem; got {no_script_message!r}"
    )
    assert "read" not in no_script_message.lower(), (
        f"the no-scripts-found case must not read as a read failure; "
        f"got {no_script_message!r}"
    )
    assert "read" in unreadable_message.lower(), (
        f"the unreadable-file case must read as a read failure; got {unreadable_message!r}"
    )


# ---------------------------------------------------------------------------
# A file with no <script> element at all
# ---------------------------------------------------------------------------


def test_a_file_with_no_script_element_at_all_does_not_pass_silently(tmp_path):
    """SPEC 2.15.1's stated rule ("it fails on any `<script>` element with
    no `src`") is silent about zero `<script>` elements: read in isolation
    it would be satisfied vacuously by an empty page. The task brief for
    this gate is not silent about it, though: the shipped page always
    carries exactly two `<script>` elements (the module entry point and,
    unless the PWA plugin's `injectRegister` setting changes, the
    registration script), so a `dist/index.html` with none is far more
    likely to be a wrong or truncated file than a genuinely clean one -
    this repository has already been bitten twice by a gate that read
    "found nothing to check" as "found nothing wrong" (issues #112, #126).
    This test pins the "must not pass silently" reading rather than the
    vacuous one; see the report for the SPEC text this extends beyond its
    literal wording."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_index_html(
        frontend_dir,
        "<!doctype html><html><head><title>TestNet companion</title></head>"
        "<body><div id=\"app\"></div></body></html>",
    )

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a file with no <script> element at all must not pass silently - it is "
        f"more likely the wrong file than a clean build; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "a file with no <script> element must be reported, not silent"


# ---------------------------------------------------------------------------
# Every page, found by search (SPEC 2.15.1, issue #144)
# ---------------------------------------------------------------------------


def test_two_clean_html_files_both_pass(tmp_path):
    """SPEC 2.15.1: "The check reads every HTML file under `dist/`, found by
    search rather than by name." Two clean files must both pass, proved by
    the summary naming two files rather than only by the exit status, which
    a check that silently read only one of them would also produce.
    `dist/index.html` is separately required to exist by name (SPEC
    2.15.1's "dist/index.html must still exist by name"), so a true
    single-file isolation of `second.html` alone - a tree with no
    `index.html` at all - is not a meaningful case here, it would fail on
    that unrelated ground instead of proving anything about search;
    `test_violation_in_the_second_file_alone_fails_and_names_that_file`
    below is what actually proves `second.html`'s content is read, by
    making it dirty rather than merely present."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("first"))
    _write_html(frontend_dir, "second.html", _clean_html("second"))

    combined = _run_check(frontend_dir)
    assert combined.returncode == 0, (
        f"two clean HTML files must both pass; "
        f"stdout={combined.stdout!r} stderr={combined.stderr!r}"
    )
    assert "2 HTML file(s)" in combined.stdout, (
        f"the success summary must report both files were read, not just "
        f"one of them; got stdout={combined.stdout!r}"
    )

    # dist/index.html alone, satisfying the separate must-exist-by-name
    # requirement, must also pass on its own.
    only_index = _build_scratch_tree(tmp_path / "only-index")
    _write_html(only_index, "index.html", _clean_html("first"))
    result_index = _run_check(only_index)
    assert result_index.returncode == 0


def test_violation_in_the_second_file_alone_fails_and_names_that_file(tmp_path):
    """The case the ticket is actually about: `index.html` (first in sorted
    order) is clean, `second.html` (second in sorted order) carries the
    inline script. A check that stopped reading after the first file, or
    that only ever looked at `dist/index.html` by name, would report this
    tree as clean. SPEC 2.15.1: "the files are processed in sorted order so
    a failing build names the same file first every time" and "Every
    failure names the file it is about.\""""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("first"))
    _write_html(frontend_dir, "second.html", _dirty_html("second"))

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a violation confined to the second file, sorted after index.html, "
        f"must still fail the gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "second.html" in output, (
        f"the failure must name second.html, the file that is actually dirty; "
        f"got {output!r}"
    )
    assert "index.html" not in output.replace("second.html", ""), (
        f"the failure must not blame index.html, which is clean; got {output!r}"
    )


def test_html_file_nested_in_a_subdirectory_is_found(tmp_path):
    """SPEC 2.15.1: "The search is recursive, since an entry point emitted
    into a subdirectory is exactly the case worth catching." A non-recursive
    search (e.g. `glob("*.html")` rather than `rglob`/`os.walk`) would miss
    this file entirely and report the tree as clean because it found
    nothing to check."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    _write_html(frontend_dir, "sub/page.html", _dirty_html("nested"))

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a violation nested under dist/sub/ must be found and must fail the gate; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "page.html" in output, (
        f"the failure must name the nested file; got {output!r}"
    )


def test_a_clean_file_beside_a_dirty_one_does_not_rescue_it(tmp_path):
    """The other direction of the same rule: a clean `index.html` alongside
    a dirty `second.html` must not average out to a pass. This is
    `test_violation_in_the_second_file_alone_fails_and_names_that_file`
    read for its exit status alone, stated as its own case because the
    averaging failure mode - one clean file diluting a dirty one - is
    exactly what a per-file check must not do, distinct from the ordering
    and naming that test also pins."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("clean one"))
    _write_html(frontend_dir, "second.html", _dirty_html("dirty one"))

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a dirty file must fail the gate even with a clean file present beside it; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


def test_dist_with_no_html_at_all_fails(tmp_path):
    """SPEC 2.15.1: "Finding no HTML at all is a failure [...] a build step
    that produced no page did not run, and reporting that as clean is the
    vacuous gate again." Distinct from `test_missing_dist_directory_fails`
    and `test_missing_index_html_fails` above (which cover the directory
    being wholly absent, or empty): here `dist/` exists and holds a real
    build artefact that is not HTML, so a naive "does dist/index.html
    exist" check would already fail on the earlier shapes, but a
    search-based check that silently tolerated an empty search result
    would pass here. The message must read as the search finding nothing,
    not as the per-file no-`<script>` wording, which claims a file was
    found and inspected - the same three-way distinction SPEC 2.15.1 draws
    (`test_the_three_failure_diagnostics_are_distinguished` proves it
    pairwise), extended here to the "dist/ holds files but none of them
    are HTML" shape specifically."""
    frontend_dir = _build_scratch_tree(tmp_path)
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "assets").mkdir()
    (dist_dir / "assets" / "index-abc123.js").write_text(
        "console.log('built');\n", encoding="utf-8"
    )

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a dist/ with no HTML file anywhere must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "a dist/ with no HTML at all must be reported, not silent"
    assert "no html file was found" in combined.lower(), (
        f"a dist/ with only non-HTML build output must read as the search "
        f"finding nothing; got {combined!r}"
    )
    assert "no <script> element" not in combined.lower(), (
        f"a search that found no HTML file must not print the per-file "
        f"no-<script>-element wording, which claims a file was found and "
        f"inspected; got {combined!r}"
    )


def test_failures_are_reported_in_sorted_path_order(tmp_path):
    """SPEC 2.15.1: "the files are processed in sorted order so a failing
    build names the same file first every time." The search walks the tree
    with `os.walk`, which yields a directory's own files before it recurses
    into a subdirectory, so a top-level file whose name sorts after the
    subdirectory's name (`zzz_top.html` after `sub/`) is nonetheless
    encountered by the walk itself before anything inside `sub/` is - the
    two orders only diverge once the walk's own visiting order is compared
    against a lexicographic sort of full paths, which is exactly what this
    drives on. Both files are dirty
    so both are reported regardless of order; only the order of the two
    filenames within the combined output distinguishes "sorted by path"
    from "as the walk visits them", and this is written against the
    sorted() call rather than the recursion (`test_html_file_nested_in_a_subdirectory_is_found`
    already pins that the recursion itself happens). `index.html` is
    included as a third, clean file purely to satisfy the separate
    must-exist-by-name requirement (SPEC 2.15.1's "dist/index.html must
    still exist by name"), which is otherwise irrelevant to what this test
    drives on and would add an unrelated error message ahead of the two
    being compared if omitted."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    _write_html(frontend_dir, "sub/aaa_nested.html", _dirty_html("nested"))
    _write_html(frontend_dir, "zzz_top.html", _dirty_html("top"))

    result = _run_check(frontend_dir)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "aaa_nested.html" in output and "zzz_top.html" in output, (
        f"both dirty files must be named; got {output!r}"
    )
    assert output.index("aaa_nested.html") < output.index("zzz_top.html"), (
        f"'dist/sub/aaa_nested.html' sorts before 'dist/zzz_top.html' as a full "
        f"path, so it must be reported first, even though the recursive walk "
        f"itself would visit the top-level zzz_top.html first; got {output!r}"
    )


def test_every_file_is_checked_rather_than_stopping_at_the_first_failure(tmp_path):
    """Both files are dirty; both must be named in the same run, proving the
    check does not stop at the first failing file. A check that returned as
    soon as one file failed would still exit non-zero here - the exit
    status alone cannot distinguish "found one failure and stopped" from
    "found and reported every failure" - so this asserts both file names
    appear in the same combined output."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _dirty_html("first"))
    _write_html(frontend_dir, "second.html", _dirty_html("second"))

    result = _run_check(frontend_dir)

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "index.html" in output, (
        f"a run that stopped at the first failure would still report index.html "
        f"(sorted first), so this alone is a weak check, but its absence here "
        f"would already be wrong; got {output!r}"
    )
    assert "second.html" in output, (
        f"a run that stopped after index.html would never mention second.html; "
        f"a full report must name both. got {output!r}"
    )


# ---------------------------------------------------------------------------
# Widening this gate could have narrowed it: the named entry point and the
# two subtree cases the search alone cannot see into (SPEC 2.15.1)
# ---------------------------------------------------------------------------


def test_dist_index_html_must_exist_by_name(tmp_path):
    """SPEC 2.15.1: "`dist/index.html` must still exist by name [...] That
    is the one way widening this gate could have narrowed it, so the named
    file is asserted separately from the search." The old, by-name-only
    check read `dist/index.html` directly, so a build that never produced
    it failed; a search-only replacement would happily pass a `dist/`
    holding some other, clean page instead, because the search finds a
    page to approve and never asks whether it was the right one. `other.html`
    here is deliberately clean, so the only thing this tree can fail on is
    the missing entry point, not an element-rule violation - proving the
    failure is the named-file requirement and not a coincidence."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "other.html", _clean_html("other"))
    # Deliberately no dist/index.html.

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a dist/ with a clean page that is not index.html must still fail "
        f"the gate; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "index.html" in output, (
        f"the failure must name index.html, the missing entry point, even "
        f"though it never existed to be found by the search; got {output!r}"
    )
    assert "does not exist" in output.lower(), (
        f"the failure must say index.html does not exist, not merely that "
        f"something else was wrong; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_unreadable_subdirectory_under_dist_fails(tmp_path):
    """SPEC 2.15.1: "A subtree the gate could not read is a failure [...]
    A directory that cannot be opened is skipped silently by an ordinary
    recursive search, which is the vacuous pass again wearing a permission
    error [...] It fails instead, naming the directory." `dist/sub/`
    contains a page that would fail the element rule on its own -
    proof that the failure comes from the gate refusing to skip an
    unreadable subtree quietly, rather than from a page it happened to
    read and find clean; `page.html`'s own violation must never surface,
    because the gate could not open the directory to see it."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    dirty_page = _write_html(frontend_dir, "sub/page.html", _dirty_html("nested"))
    sub_dir = dirty_page.parent
    sub_dir.chmod(0)

    try:
        result = _run_check(frontend_dir)
    finally:
        sub_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"an unreadable subdirectory under dist/ must fail the gate, not "
        f"pass silently; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    # "sub" alone is not enough: the gate's own boilerplate says "subtree",
    # which contains that substring regardless of whether the directory is
    # actually named. Pin the path, not a fragment the surrounding prose
    # can already contain (SPEC 2.15.1).
    assert "dist/sub" in output, (
        f"the failure must name the unreadable directory by its path, not "
        f"merely use a word that happens to contain 'sub'; got {output!r}"
    )
    assert "page.html" not in output, (
        f"page.html was never opened - the gate could not read the "
        f"directory it lives in - so its own violation must not appear, or "
        f"this is proving the wrong thing; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_subdirectory_readable_but_not_searchable_fails_without_a_traceback(tmp_path):
    """The other real mode for an unreadable subtree, alongside
    `test_unreadable_subdirectory_under_dist_fails` above rather than in
    place of it: `chmod(0)` makes `scandir` itself fail on `dist/sub`, so
    `os.walk`'s `onerror` callback fires directly and the failure is
    reported cleanly. Mode `0444` - readable, not searchable - does not:
    `os.walk` can still list `dist/sub`'s own entries (`deep`), so no
    listing error is ever raised there, and it is the symlink check's
    `lstat` on the listed entry that then raises `EACCES`, one level below
    where the mode-0 case fails. Both are real conditions a genuinely
    misconfigured subtree can be in, and only one of them exercised the
    `os.walk` failure path directly - this drives the other one, the
    `lstat` failure the symlink check performs. `dist/sub/deep/page.html`
    carries an inline script, so the page that goes unexamined is one that
    *would* have failed the element rule, not a coincidentally clean one."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    _write_html(frontend_dir, "sub/deep/page.html", _dirty_html("nested"))
    sub_dir = frontend_dir / "dist" / "sub"
    sub_dir.chmod(0o444)

    try:
        result = _run_check(frontend_dir)
    finally:
        sub_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"a subdirectory that is readable but not searchable must fail the "
        f"gate, not pass silently; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "dist/sub" in output, (
        f"the failure must name the directory by its path; got {output!r}"
    )
    assert "Traceback" not in output, (
        f"a subdirectory that is readable but not searchable must be "
        f"reported, not raised - the symlink check's lstat is exactly "
        f"where an uncaught EACCES would surface as a traceback; "
        f"got {output!r}"
    )
    assert "page.html" not in output, (
        f"page.html was never opened - the gate could not search the "
        f"directory it lives in - so its own violation must not appear, or "
        f"this is proving the wrong thing; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="symlink creation is POSIX-specific here")
def test_symlinked_subdirectory_under_dist_fails(tmp_path):
    """SPEC 2.15.1: "A symlinked subdirectory fails [...] following one
    risks a loop that hangs the job, and not following it means the gate
    cannot vouch for what is inside, so it says which of the two it is
    rather than quietly choosing the second." The target holds a page that
    would fail the element rule - proof that the gate is refusing to
    follow the link rather than following it, silently passing, and never
    reaching the violation inside; if it had followed the link, either the
    violation would surface as an ordinary per-file failure with no
    mention of a symlink, or - worse - the page would be approved right
    along with the rest of the tree."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))

    target_dir = frontend_dir / "outside_dist_target"
    target_dir.mkdir()
    (target_dir / "page.html").write_text(_dirty_html("linked"), encoding="utf-8")

    link = frontend_dir / "dist" / "linked"
    link.symlink_to(target_dir, target_is_directory=True)

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a symlinked subdirectory under dist/ must fail the gate, not be "
        f"silently followed or silently skipped; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    # "linked" alone is not enough: the message body says "symlinked", which
    # contains that substring regardless of whether the directory is
    # actually named. Pin the path, not a fragment the surrounding prose
    # can already contain (SPEC 2.15.1).
    assert "dist/linked" in output, (
        f"the failure must name the symlinked directory by its path, not "
        f"merely use a word that happens to contain 'linked'; got {output!r}"
    )
    assert "symlink" in output.lower(), (
        f"the failure must say it is a symlink, since that is the reason "
        f"the gate declined to follow it, not merely 'something failed'; "
        f"got {output!r}"
    )
    assert "page.html" not in output, (
        f"the page inside the link must never have been read - the gate "
        f"must say it declined to follow the link, not that it found a "
        f"violation inside it; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_dist_itself_unreadable_fails_with_a_named_message_not_a_traceback(tmp_path):
    """SPEC 2.15.1: "`dist/` itself being unreadable is reported, not
    raised." [...] "a `dist/` with no read permission ended the gate in a
    traceback: no annotation for the reader, and an absolute local path on
    stderr that every other message here avoids [...] It is caught and
    named, and kept distinct from 'no HTML file anywhere', because a
    directory that will not open wants a different next action from one
    that is genuinely empty." `dist/` here is mode 0, so nothing under it
    can be read regardless of what it contains - the content is
    deliberately irrelevant, unlike the unreadable-subdirectory case
    above, where the content had to be a violation to prove the failure
    was not a coincidence. Here there is nothing left to coincide with:
    `dist/` itself is the thing that cannot be opened."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(_clean_html("root"), encoding="utf-8")
    dist_dir.chmod(0)

    try:
        result = _run_check(frontend_dir)
    finally:
        dist_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"an unreadable dist/ must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    # A bare word like "dist" alone would not distinguish a named path from
    # a fragment the surrounding prose happens to contain - pin the path
    # the gate actually printed, the same lesson as the subdirectory cases
    # above (SPEC 2.15.1).
    assert "frontend/dist" in output, (
        f"the failure must name the unreadable dist/ directory by its "
        f"path, not merely use a word that happens to contain 'dist'; "
        f"got {output!r}"
    )
    assert "Traceback" not in output, (
        f"an unreadable dist/ must be reported, not raised - a Python "
        f"traceback is exactly the failure this rule closes; got {output!r}"
    )
    assert "no html file was found" not in output.lower(), (
        f"an unreadable dist/ is a directory the gate could not open, not "
        f"one it opened and found genuinely empty - the two want different "
        f"next actions and must not share a message; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_dist_at_mode_0111_does_not_also_claim_no_html_was_found(tmp_path):
    """SPEC 2.15.1: "A failure that names `dist/` itself suppresses the
    'no HTML anywhere' line, and the test for it is decided by mode. The
    first version keyed the suppression on which *call* had raised, so a
    `dist/` at mode `0111` - searchable but not readable - printed both the
    named failure and a sentence saying the build had not run, which was
    false: `index.html` had been stat'd successfully three lines earlier.
    The condition is what the error is about, not which call found it."
    Mode `0` (`test_dist_itself_unreadable_fails_with_a_named_message_not_a_traceback`
    above) fails `is_file()` itself, so it never reaches this trap: at that
    mode, the suppression flag is set correctly almost by accident, the
    same way mode `0` for a subdirectory happens to take the `os.walk`
    failure path rather than the `lstat` one
    (`test_unreadable_subdirectory_under_dist_fails` versus
    `test_subdirectory_readable_but_not_searchable_fails_without_a_traceback`).
    Mode `0111` is the one where `is_file()` succeeds - execute permission
    is enough to stat a named child - and the walk still fails, which is
    the state that actually exercises the suppression logic rather than
    happening to look right."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    dist_dir = frontend_dir / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(_clean_html("root"), encoding="utf-8")
    dist_dir.chmod(0o111)

    try:
        result = _run_check(frontend_dir)
    finally:
        dist_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"a dist/ at mode 0111 must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "frontend/dist" in output, (
        f"the failure must name the unreadable dist/ directory by its path; "
        f"got {output!r}"
    )
    assert "Traceback" not in output, (
        f"a dist/ at mode 0111 must be reported, not raised; got {output!r}"
    )
    assert "no html file was found" not in output.lower(), (
        f"the gate already knows index.html exists - it stat'd it "
        f"successfully - so claiming no HTML file was found anywhere is a "
        f"false sentence, not merely a redundant one; got {output!r}"
    )


@pytest.mark.parametrize("filename", ["Page.HTML", "page.htm"])
def test_a_non_matching_html_extension_is_a_documented_gap_not_examined(tmp_path, filename):
    """Characterisation test, not a requirement: SPEC 2.15.1 states the
    limit rather than closing it - "The name must end in `.html`, matched
    case-sensitively [...] A page arriving as `.htm` or `.HTML` would pass
    unexamined [...] it is named here so the next person to add one knows
    to widen the pattern with it." A declared limit with no test is
    indistinguishable from an undeclared one the moment somebody edits the
    pattern by accident, and this is a security gate, so the gap is pinned
    here rather than left to be noticed the hard way. `filename` carries an
    inline `<script>` that would fail the element rule if it were examined;
    the gate must still exit 0 and report exactly the one file it does
    cover, `index.html`. The day the pattern widens to catch `filename`,
    this test is meant to fail and be rewritten to expect the catch, not
    quietly keep passing."""
    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    _write_html(frontend_dir, filename, _dirty_html("unexamined"))

    result = _run_check(frontend_dir)

    assert result.returncode == 0, (
        f"as documented in SPEC 2.15.1, a case-sensitive '.html' match "
        f"leaves {filename!r} unexamined, so this dirty page must not be "
        f"caught today; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "1 HTML file(s)" in result.stdout, (
        f"only index.html should have been found by the search; "
        f"got stdout={result.stdout!r}"
    )
    assert filename not in (result.stdout + result.stderr), (
        f"{filename!r} must never be named - it was never examined; "
        f"got stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_unreadable_subtree_does_not_also_claim_no_html_was_found(tmp_path):
    """The same class of contradiction the mode-`0111` test above catches
    for `dist/` itself, one level down: `dist/` here holds only `sub/` at
    mode 0 and no top-level page at all, so the gate has already said it
    could not see into `sub/` - it does not know whether a page lives in
    there, which is exactly the state "no HTML file was found ... more
    likely a build that did not run" denies. The two messages must not
    both print: a subtree the gate could not open is not the same claim as
    a build that emitted nothing, and printing both says the opposite of
    what the first message just said."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    # Deliberately no dist/index.html: dist/ holds only the unreadable sub/.
    sub_dir = frontend_dir / "dist" / "sub"
    sub_dir.mkdir(parents=True)
    (sub_dir / "page.html").write_text(_dirty_html("nested"), encoding="utf-8")
    sub_dir.chmod(0)

    try:
        result = _run_check(frontend_dir)
    finally:
        sub_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"an unreadable sub/ under dist/ must fail the gate, not pass "
        f"silently; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "dist/sub" in output and "could not be read" in output, (
        f"the accurate subtree failure must still be reported; got {output!r}"
    )
    assert "no html file was found" not in output.lower(), (
        f"the gate could not see into sub/, so it does not know whether a "
        f"page is in there - claiming no HTML file was found anywhere "
        f"contradicts the subtree failure just reported; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="symlink creation is POSIX-specific here")
def test_symlinked_subtree_does_not_also_claim_no_html_was_found(tmp_path):
    """The symlinked-subdirectory sibling of the case above: `dist/` here
    holds only `linked/` - a symlink the gate declines to follow - and no
    top-level page. The gate has said it will not vouch for what is inside
    `linked/`, which is the same "we do not know" state as an unreadable
    subtree, so "no HTML file was found ... more likely a build that did
    not run" must not print alongside it either."""
    frontend_dir = _build_scratch_tree(tmp_path)
    # Deliberately no dist/index.html: dist/ holds only the symlinked entry.
    (frontend_dir / "dist").mkdir()

    target_dir = frontend_dir / "outside_dist_target"
    target_dir.mkdir()
    (target_dir / "page.html").write_text(_dirty_html("linked"), encoding="utf-8")

    link = frontend_dir / "dist" / "linked"
    link.symlink_to(target_dir, target_is_directory=True)

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a symlinked linked/ under dist/ must fail the gate, not pass "
        f"silently; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "dist/linked" in output and "symlink" in output.lower(), (
        f"the accurate symlink failure must still be reported; got {output!r}"
    )
    assert "no html file was found" not in output.lower(), (
        f"the gate declined to follow linked/, so it does not know whether "
        f"a page is in there - claiming no HTML file was found anywhere "
        f"contradicts the symlink failure just reported; got {output!r}"
    )


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX-specific")
def test_three_subdirectories_under_one_unsearchable_parent_are_named_once(tmp_path):
    """The `root_failed` branch: when a stat on a child fails, the failing
    directory is recorded once and its remaining entries are skipped,
    rather than one message per child. Every other fixture in this module
    with an unsearchable directory (mode `0444`) puts exactly one
    subdirectory under it, so the branch that decides one message versus
    several has nothing here to exercise it before this test - three
    subdirectories under one mode-`0444` parent must still produce exactly
    one failure naming the parent, not three."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits, so this cannot be exercised as root")

    frontend_dir = _build_scratch_tree(tmp_path)
    _write_html(frontend_dir, "index.html", _clean_html("root"))
    parent_dir = frontend_dir / "dist" / "parent"
    for name in ("a", "b", "c"):
        _write_html(frontend_dir, f"parent/{name}/page.html", _dirty_html(name))
    parent_dir.chmod(0o444)

    try:
        result = _run_check(frontend_dir)
    finally:
        parent_dir.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    assert result.returncode != 0, (
        f"an unsearchable parent with children must fail the gate; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    output = result.stdout + result.stderr
    assert "dist/parent" in output and "could not be read" in output, (
        f"the failure must name the parent directory; got {output!r}"
    )
    assert output.count("could not be read") == 1, (
        f"one bad parent with three children must produce exactly one "
        f"failure message, not one per child; got {output!r}"
    )
    for child in ("dist/parent/a", "dist/parent/b", "dist/parent/c"):
        assert child not in output, (
            f"a child directory must never be named individually - the "
            f"parent is what could not be searched; got {output!r}"
        )


# ---------------------------------------------------------------------------
# CI actually invokes the script, on pull requests, after Build
# ---------------------------------------------------------------------------

# Pinned by workflow file, job key and step name, following the registry
# convention `tests/test_ci_gates.py` and `tests/test_shipped_files.py` use.
# A gate nobody invokes is not a gate, and neither is one that only runs
# somewhere pull requests never reach.
_WORKFLOW = "ci.yml"
_JOB_KEY = "frontend"
_STEP_NAME = "No inline script in the built page"
_BUILD_STEP_NAME = "Build"


def test_ci_actually_invokes_the_inline_script_check_after_build():
    """Reads the pinned workflow file directly, finds the pinned job and
    step by their exact keys, and requires that step's `run:` script to
    actually execute `.github/check_no_inline_script.py` - not merely
    mention it - requires the workflow to trigger on `pull_request`, and
    requires the step to run after `Build`, since asserting on
    `dist/index.html` before it exists checks nothing."""
    workflow_path = REPO_ROOT / ".github" / "workflows" / _WORKFLOW
    assert workflow_path.is_file(), f"expected workflow file at {workflow_path}"
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    # PyYAML's default loader parses the unquoted `on:` key as the boolean
    # True rather than the string "on" (YAML 1.1's bareword booleans).
    triggers = data.get("on", data.get(True, {})) or {}
    assert "pull_request" in triggers, (
        f"{_WORKFLOW} must trigger on pull_request, or the no-inline-script "
        f"gate it hosts never runs against a pull request; got triggers {sorted(triggers)!r}"
    )

    jobs = data.get("jobs", {})
    assert _JOB_KEY in jobs, f"expected job {_JOB_KEY!r} in {_WORKFLOW}; got jobs {sorted(jobs)!r}"
    steps = jobs[_JOB_KEY].get("steps", []) or []
    step_names = [s.get("name") for s in steps]

    step = next((s for s in steps if s.get("name") == _STEP_NAME), None)
    assert step is not None, (
        f"expected a step named {_STEP_NAME!r} in job {_JOB_KEY!r} of {_WORKFLOW}; "
        f"got steps {step_names!r}"
    )

    build_step = next((s for s in steps if s.get("name") == _BUILD_STEP_NAME), None)
    assert build_step is not None, (
        f"expected a step named {_BUILD_STEP_NAME!r} in job {_JOB_KEY!r} of {_WORKFLOW} "
        f"to compare ordering against; got steps {step_names!r}"
    )
    assert step_names.index(_BUILD_STEP_NAME) < step_names.index(_STEP_NAME), (
        f"{_STEP_NAME!r} must run after {_BUILD_STEP_NAME!r}, or it inspects a "
        f"dist/index.html that does not exist yet; got order {step_names!r}"
    )

    run_script = step.get("run") or ""
    command_lines = [ln for ln in run_script.splitlines() if not ln.strip().startswith("#")]
    assert any("check_no_inline_script.py" in ln for ln in command_lines), (
        f"step {_STEP_NAME!r} must actually run .github/check_no_inline_script.py, "
        f"not merely mention it; got {run_script!r}"
    )
