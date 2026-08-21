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
    [...] the failure says which of the two cases it is, since "no scripts found" and "could
    not read the file" want different next actions.

The check runs against the real built `dist/index.html`, after `Build`, in the `frontend` job
of `.github/workflows/ci.yml`. SPEC 13 (and issues #112, #126) is explicit that a gate which
cannot run must not be read as a gate that found nothing: a missing or unreadable file is a
failure, never a silent pass.

This module does not read the implementation to decide what to assert - only enough of
`.github/check_no_inline_script.py` to invoke it correctly (no arguments, run the way CI runs
it: `python3 ../.github/check_no_inline_script.py` from a `frontend/` working directory, so
the script has to find `dist/index.html` on its own, the same way it does in CI).

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
    names issues #112 and #126 for. SPEC 2.15.1 requires this to read as a
    read failure ("could not read the file"), not as "no scripts found" -
    the two want different next actions."""
    frontend_dir = _build_scratch_tree(tmp_path)
    # Deliberately no frontend/dist/ at all.

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a missing dist/ directory must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "a missing dist/index.html must be reported, not silent"
    assert "read" in combined.lower(), (
        f"a missing dist/index.html is a read failure and must read as one, per "
        f"SPEC 2.15.1's 'could not read the file'; got {combined!r}"
    )


def test_missing_index_html_fails(tmp_path):
    """`dist/` exists (Vite ran) but `index.html` itself is absent -
    distinct from the directory being entirely missing, and must fail the
    same way, and the same read-failure diagnostic."""
    frontend_dir = _build_scratch_tree(tmp_path)
    (frontend_dir / "dist").mkdir()

    result = _run_check(frontend_dir)

    assert result.returncode != 0, (
        f"a dist/ with no index.html must fail the gate, not pass silently; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    combined = result.stdout + result.stderr
    assert "read" in combined.lower(), (
        f"a dist/ with no index.html is a read failure and must read as one; got {combined!r}"
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


def test_the_two_failure_diagnostics_are_distinguished(tmp_path):
    """SPEC 2.15.1: "The failure says which of the two cases it is, since
    'no scripts found' and 'could not read the file' want different next
    actions." Drives both failure shapes from the same helpers and proves
    they do not collapse to the same message - the gap a single generic
    "failed" line, or one shared string used for both branches, would
    leave open."""
    unreadable_dir = _build_scratch_tree(tmp_path / "unreadable-case")
    # No frontend/dist/ at all: the read-failure branch.
    read_failure = _run_check(unreadable_dir)

    empty_dir = _build_scratch_tree(tmp_path / "no-script-case")
    _write_index_html(
        empty_dir,
        "<!doctype html><html><head><title>TestNet companion</title></head>"
        "<body><div id=\"app\"></div></body></html>",
    )
    no_script = _run_check(empty_dir)

    assert read_failure.returncode != 0
    assert no_script.returncode != 0

    read_failure_message = (read_failure.stdout + read_failure.stderr).strip()
    no_script_message = (no_script.stdout + no_script.stderr).strip()

    assert read_failure_message and no_script_message, (
        f"both failures must produce a message; "
        f"read_failure={read_failure_message!r} no_script={no_script_message!r}"
    )
    assert read_failure_message != no_script_message, (
        f"a missing file and a file with no <script> element are different "
        f"failures and must not read as the same message; both were "
        f"{read_failure_message!r}"
    )
    assert "script" in no_script_message.lower(), (
        f"the no-scripts-found case must name that as the problem; got {no_script_message!r}"
    )
    assert "read" not in no_script_message.lower(), (
        f"the no-scripts-found case must not read as a read failure; "
        f"got {no_script_message!r}"
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
