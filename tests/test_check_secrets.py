"""`.github/check_secrets.py`'s two npm patterns (SPEC.md 5.1.1, issue #132's
security follow-up, tracked as issue #199 for the rest of the scanner).

`frontend/.npmrc` started being tracked from issue #132 onward, and a
project-level `.npmrc` is exactly where `npm config set --location=project`
writes a registry credential. The pre-existing `credential assignment`
pattern cannot see one: npm writes `:_authToken=<value>` with no quotes,
where that pattern requires a quoted literal, and the `_` in front of
`authToken` is a word character, so the `\\b` the pattern opens with never
matches there. `PATTERNS` gained two entries for this - `"npm access
token"` and `"npm registry credential"` - and this module is their tests.

The rest of `PATTERNS` and `main()`'s file-walking behaviour (forbidden
suffixes, the fixture exemption, unreadable files, the three exit-status
outcomes and the latin-1 decode that replaced a `UnicodeDecodeError`
skip-and-warn) is issue #199, and is covered further down in this same
module rather than in one of its own: SPEC.md 5.1.2 is the authority for
that half.

Both new patterns are exercised as the actual compiled `re.Pattern` objects
imported from the script by path, not as regexes retyped in this file - a
copy that ships nowhere cannot catch its original drifting from it, the
same reasoning `tests/test_release_workflow.py` states for
`check_release_version.py`. `main()` itself is driven once, as a real
subprocess over this checkout's tracked tree, to pin that adding the two
patterns introduced no false positive anywhere real files already say.

Every probe below is built to match the *shape* the pattern looks for - an
`npm_`-prefixed body of npm's own token length, an `.npmrc` credential
line - since that shape is exactly what makes the probe fire at all. The
*value* carried in that shape is never real: `synthetic-example-value`,
`example.test` and the deterministic filler `_synthetic_npm_token_body`
produces are placeholders, not anything a token generator or a real
registry ever issued.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "check_secrets.py"
NPMRC_PATH = REPO_ROOT / "frontend" / ".npmrc"


def _load_check_secrets():
    """Imports the checker by path: `.github` is not an importable package,
    the same idiom `tests/tools/test_check_release_version.py` uses for
    `check_release_version.py`.
    """
    spec = importlib.util.spec_from_file_location("check_secrets", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = _load_check_secrets()


def _pattern(label):
    matches = [pattern for entry_label, pattern in cs.PATTERNS if entry_label == label]
    assert matches, (
        f"expected a PATTERNS entry labelled {label!r}; "
        f"got labels {[entry_label for entry_label, _ in cs.PATTERNS]!r}"
    )
    assert len(matches) == 1, f"expected exactly one PATTERNS entry labelled {label!r}"
    return matches[0]


NPM_ACCESS_TOKEN = _pattern("npm access token")
NPM_REGISTRY_CREDENTIAL = _pattern("npm registry credential")


def _synthetic_npm_token_body(length=36):
    """A 36-character alphanumeric run with no resemblance to a real npm
    token: a deterministic, obviously-synthetic sequence rather than
    anything drawn from a token generator's own alphabet distribution.
    """
    import string

    alphabet = string.ascii_letters + string.digits
    return "".join(alphabet[i % len(alphabet)] for i in range(length))


def _npm_registry_credential_probe(
    prefix="", keyword="_authToken", value="synthetic-example-value"
):
    """Assembles an `npm registry credential` line - optional prefix, one
    of the three setting names, `=`, a value - from separate pieces joined
    at runtime, so the shape never sits as one contiguous literal in this
    module's own tracked source. This module's whole subject is
    credential-shaped strings, and a probe spelled out in one piece would
    be exactly what a real credential pasted in here would look like too -
    see `test_no_probe_value_appears_contiguously_in_this_modules_own_source`
    below, which is the test that makes that failure mode impossible rather
    than merely unlikely.
    """
    return f"{prefix}{keyword}" + f"={value}"


# ---------------------------------------------------------------------------
# "npm access token": SPEC 5.1.1 / issue #132's security follow-up. The
# pattern is `\bnpm_[A-Za-z0-9]{36}\b` - the literal prefix npm itself uses
# for its own access tokens, plus the exact 36-character body length npm
# generates.
# ---------------------------------------------------------------------------


def test_npm_access_token_fires_on_a_probe():
    probe = f"npm_{_synthetic_npm_token_body(36)}"
    assert NPM_ACCESS_TOKEN.search(probe), f"expected a match on {probe!r}"


def test_npm_access_token_does_not_fire_on_frontend_npmrc():
    text = NPMRC_PATH.read_text(encoding="utf-8")
    assert not NPM_ACCESS_TOKEN.search(text), (
        "frontend/.npmrc's own tracked content must not read as an npm access token"
    )


def test_npm_access_token_still_fires_inside_a_comment():
    """`npm access token`, unlike `npm registry credential` below, is not
    anchored to a line's start - it is a bare `\\bnpm_...\\b` shape, so a
    `#`-commented line carrying a real 36-character body is still exactly
    as much of a leak as an uncommented one and must still be caught. This
    is the opposite of a tolerated shape: it is here to pin that a
    commenting-out reflex does not accidentally launder a real token, and
    to document why this pattern has no "commented-out line" exemption
    where the registry-credential pattern does.
    """
    probe = f"# example: npm_{_synthetic_npm_token_body(36)}"
    assert NPM_ACCESS_TOKEN.search(probe), (
        f"expected a match on {probe!r} - a real token does not stop being a leak "
        f"for being preceded by a comment marker"
    )


def test_npm_access_token_does_not_fire_on_a_documentation_placeholder():
    """A documentation line naming the shape without supplying a real value:
    the placeholder breaks the alphanumeric run with `<...>` well before 36
    characters accumulate, which is what a genuine token's body could never
    do.
    """
    probe = "npm access tokens look like npm_<36-character-token-value-here>"
    assert not NPM_ACCESS_TOKEN.search(probe), f"expected no match on {probe!r}"


def test_npm_access_token_does_not_fire_on_a_body_shorter_than_36_characters():
    """Pins the exact body length rather than a lower bound: `npm_` followed
    by fewer than 36 alphanumeric characters is not npm's own token shape,
    and a pattern loosened to `{20,}` (say) would start flagging shorter,
    unrelated `npm_`-prefixed identifiers this repository may legitimately
    contain.
    """
    probe = f"npm_{_synthetic_npm_token_body(20)}"
    assert not NPM_ACCESS_TOKEN.search(probe), f"expected no match on {probe!r}"


# ---------------------------------------------------------------------------
# "npm registry credential": SPEC 5.1.1 / issue #132's security follow-up.
# Keyed on the setting name (`_authToken`, `_auth`, `_password`), not on the
# value's shape, because a private registry issues whatever it likes -
# stated explicitly in the pattern's own comment. The registry prefix
# (`//host/...:` or `@scope:...:`) is optional, per that same comment: `npm
# config set _authToken <value> --location=project` writes the key at
# column 0 with no prefix at all, and a pattern that required one missed
# exactly that line. Pinned here by exercising all three names, both
# prefixed line shapes, and the unprefixed shape the widening exists for.
#
# What is deliberately not asserted here: `check_secrets.py`'s own comment
# names the remaining `^\s*` line anchor "a concession and ... not a
# policy" - it keeps documentation prose such as `_authToken=<your token>`
# from firing, at the price that a credential pasted after a `#` in any
# tracked file escapes this pattern. That is a stated, accepted limit of
# the anchor, not a requirement anyone chose for it to have, so nothing
# below asserts either that a `#` defeats the scanner or that it must not:
# doing either would pin today's accident of the anchor as a contract this
# pattern never promised.
# ---------------------------------------------------------------------------


# A named, module-level table rather than an inline list literal, on
# purpose: `test_no_probe_value_appears_contiguously_in_this_modules_own_source`
# enumerates this same list, so every shape pinned here is also checked for
# not sitting whole in this file's own source. Each entry is built by
# `_npm_registry_credential_probe` rather than typed as one string, for the
# same reason.
NPM_REGISTRY_CREDENTIAL_PROBES = [
    pytest.param(
        _npm_registry_credential_probe("//registry.example.test/:", "_authToken"),
        id="slash-prefixed-authToken",
    ),
    pytest.param(
        _npm_registry_credential_probe("//registry.example.test/:", "_auth"),
        id="slash-prefixed-auth",
    ),
    pytest.param(
        _npm_registry_credential_probe("//registry.example.test/:", "_password"),
        id="slash-prefixed-password",
    ),
    pytest.param(
        _npm_registry_credential_probe("@example-scope:", "_authToken"),
        id="at-prefixed-scoped-registry",
    ),
    pytest.param(
        _npm_registry_credential_probe(keyword="_authToken"),
        id="unprefixed-authToken",
    ),
    pytest.param(
        _npm_registry_credential_probe(keyword="_auth"),
        id="unprefixed-auth",
    ),
    pytest.param(
        _npm_registry_credential_probe(keyword="_password"),
        id="unprefixed-password",
    ),
]


@pytest.mark.parametrize("probe", NPM_REGISTRY_CREDENTIAL_PROBES)
def test_npm_registry_credential_fires_on_each_setting_name_and_line_shape(probe):
    """The unprefixed cases pin the widening `npm config set _authToken
    <value> --location=project` needs: that command writes its line with no
    `//registry/:` or `@scope:` in front of it, at column 0.
    """
    assert NPM_REGISTRY_CREDENTIAL.search(probe), f"expected a match on {probe!r}"


def test_npm_registry_credential_does_not_fire_on_frontend_npmrc():
    text = NPMRC_PATH.read_text(encoding="utf-8")
    assert not NPM_REGISTRY_CREDENTIAL.search(text), (
        "frontend/.npmrc's own tracked content (engine-strict=true and its comment "
        "block) must not read as a registry credential line"
    )


def test_npm_registry_credential_does_not_fire_on_a_documentation_line():
    """A prose line naming `_authToken` as npm's own setting name, with no
    assignment at all and not shaped like an .npmrc line - the natural way
    this repository's own documentation (SPEC.md 5.1.1, this module's
    docstring) refers to the setting without becoming a finding itself.
    """
    probe = (
        "The _authToken setting is npm's own name for a registry credential, "
        "not something this scanner types twice."
    )
    assert not NPM_REGISTRY_CREDENTIAL.search(probe), f"expected no match on {probe!r}"


# ---------------------------------------------------------------------------
# main() over the real tree: pins that adding the two patterns did not turn
# any file this checkout already tracks into a false positive. It reaches
# frontend/.npmrc along with everything else, since the scanner enumerates
# `git ls-files`, so nothing here passes that path explicitly. The two
# does_not_fire_on_frontend_npmrc tests above are not redundant with it:
# they read the file through `Path` and drive one compiled pattern each, so
# they say which pattern stayed quiet, where this one says only that the
# whole scan did.
# ---------------------------------------------------------------------------


def test_scanner_is_clean_over_the_tracked_tree():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"expected check_secrets.py to exit 0 over the tracked tree with the two new "
        f"patterns in place, got {result.returncode}\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Issue #199, SPEC.md 5.1.2: the rest of the scanner. `main()` is driven
# in-process below, through `monkeypatch.setattr(sys, "argv", ...)` and
# `capsys`, for every case that only needs an existing readable file - that
# path does not depend on where `check_secrets.py` itself lives, so the real
# script is exercised directly rather than through a subprocess. The
# forbidden-suffix/name exemption is the one exception: SPEC.md 5.1.2 says
# the exemption is a `tests/fakes/fixtures/`-*prefixed path*, and that only
# means anything relative to wherever `git ls-files` was run - which this
# script roots at its own location, not at the caller's cwd, confirmed by
# probing it live before writing anything here (a bare
# `check_secrets.py <path outside this repo>` never exempts, regardless of
# how the tail of that path is spelled). Those tests therefore copy the
# unread, unmodified script into a disposable git repository under
# `tmp_path` and drive it with no arguments, the same `git ls-files`
# fallback `main()` uses on this repository - `shutil.copy2` moves bytes,
# it is not this module reading the script to learn how it works.
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, capsys, *argv):
    """Drives the real `cs.main()` in-process with a patched `sys.argv`,
    the same way a `console_scripts` entry point or a pre-commit hook would
    call it, and returns `(exit_status, stdout, stderr)`.
    """
    monkeypatch.setattr(sys, "argv", ["check_secrets.py", *argv])
    rc = cs.main()
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _credential_assignment_probe(keyword="api_key", value="synthetic-example-value"):
    """Assembles a `keyword = "value"` line from two pieces at runtime, so
    that shape never appears as one contiguous, matchable literal anywhere
    in this module's own tracked source - `test_check_secrets.py` is itself
    part of the tree `check_secrets.py` scans, and `test_scanner_is_clean_
    over_the_tracked_tree` below runs the real scanner over it.
    """
    return f"{keyword} = " + f'"{value}"'


def _url_credential_probe(scheme="https", user="svc", pw="s3cr3tvalue", host="host.example.test"):
    """Same reasoning as `_credential_assignment_probe`, for the `scheme://
    user:password@host` shape. The parameter is named `pw`, not `password`,
    on purpose: a default of `password="..."` would itself be a
    `credential assignment` match sitting right here in this function's own
    signature.
    """
    return f"{scheme}://{user}:" + f"{pw}@{host}"


# --- 1. every PATTERNS label fires on a probe, and none fire on the three
# placeholder shapes SPEC.md 5.1.2 itself names. -----------------------------

PROBES = {
    "PEM private key": "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 64,
    "GitHub token": "ghp_" + "A" * 36,
    "GitHub fine-grained PAT": "github_pat_" + "A" * 55,
    "AWS access key id": "AKIA" + "B" * 16,
    "Slack token": "xoxb-" + "1" * 15,
    "Google API key": "AIza" + "C" * 35,
    "npm access token": f"npm_{_synthetic_npm_token_body(36)}",
    "npm registry credential": _npm_registry_credential_probe(),
    "private key in a URL": _url_credential_probe(),
    "credential assignment": _credential_assignment_probe(),
}


def test_every_pattern_label_in_PATTERNS_has_a_probe():
    """Enumerates `cs.PATTERNS` itself, per SPEC.md 5.1.2: an eleventh
    pattern must arrive with a probe in this dict or this test names it and
    fails, rather than the suite silently saying nothing about a pattern
    nobody proved alive.
    """
    labels = [label for label, _ in cs.PATTERNS]
    missing = [label for label in labels if label not in PROBES]
    assert not missing, (
        f"PATTERNS grew a label with no probe in this test module: {missing!r}; "
        f"every entry in PATTERNS must arrive with its own fire/no-fire test"
    )
    stale = [label for label in PROBES if label not in labels]
    assert not stale, f"PROBES carries a probe for a label PATTERNS no longer has: {stale!r}"


@pytest.mark.parametrize("label,pattern", cs.PATTERNS, ids=[label for label, _ in cs.PATTERNS])
def test_pattern_fires_on_its_own_probe(label, pattern):
    probe = PROBES[label]
    assert pattern.search(probe), f"{label!r} pattern did not fire on its own probe {probe!r}"


PLACEHOLDER_SHAPES = [
    'token = ""',
    "password = <redacted>",
    'api_key = "your-key-here"',
]


@pytest.mark.parametrize("label,pattern", cs.PATTERNS, ids=[label for label, _ in cs.PATTERNS])
@pytest.mark.parametrize(
    "shape",
    PLACEHOLDER_SHAPES,
    ids=["empty-token-value", "unquoted-redacted-password", "your-key-here-doc-placeholder"],
)
def test_pattern_does_not_fire_on_a_tolerated_placeholder_shape(label, pattern, shape):
    """SPEC.md 5.1.2 names these three placeholder shapes explicitly as ones
    a pattern's own tolerance must not turn into a false positive. None of
    the ten patterns has any business matching any of the three - checked
    against all of them, not only `credential assignment`, so a pattern
    that grows an accidental overlap with one of these exact shapes is
    caught here rather than only in code review.
    """
    assert not pattern.search(shape), f"{label!r} incorrectly matched placeholder shape {shape!r}"


# Probes exactly one character short of the length floor each pattern's own
# quantifier sets: AWS's {16}, Google's {35}, the PEM body's {60,}, and the
# URL password's {6,}. Each probe here is the floor minus exactly one
# character - not merely "short" - so that a mutant which loosens the floor
# by one (`{16}` to `{15}`, `{6,}` to `{5,}`) is the one thing this
# parametrization is built to catch; a probe that is short by more than one
# character would still pass against that mutant and prove nothing.
QUANTIFIER_FLOOR_MINUS_ONE = [
    ("PEM private key", "-----BEGIN PRIVATE KEY-----\n" + "A" * 59 + "\n-----END PRIVATE KEY-----"),
    ("AWS access key id", "AKIA" + "B" * 15),
    ("Google API key", "AIza" + "C" * 34),
    ("private key in a URL", "https://user:" + "short@host.example.test"),
]


@pytest.mark.parametrize(
    "label,probe",
    QUANTIFIER_FLOOR_MINUS_ONE,
    ids=[
        "pem-body-fifty-nine-of-sixty",
        "aws-key-fifteen-of-sixteen",
        "google-key-thirty-four-of-thirty-five",
        "url-password-five-of-six",
    ],
)
def test_pattern_does_not_fire_one_character_below_its_length_floor(label, probe):
    pattern = dict(cs.PATTERNS)[label]
    assert not pattern.search(probe), f"{label!r} incorrectly matched below-floor probe {probe!r}"


# These two are not length floors and a "minus one character" probe would
# not mean anything for either: GitHub token's fire probe is exactly a fixed
# `gh` + one of five letters, and Slack token's is a fixed hyphen after the
# `xox` + team-letter prefix. What is probed instead is the actual
# structural boundary each pattern draws - a prefix outside the closed
# `[pousr]` class, and a body with the delimiting hyphen removed - which a
# mutant widening either class or dropping the hyphen requirement would
# turn into a false match.
STRUCTURAL_NOFIRE = [
    ("GitHub token", "ghx_" + "A" * 36),
    ("Slack token", "xoxb" + "1" * 15),
]


@pytest.mark.parametrize(
    "label,probe",
    STRUCTURAL_NOFIRE,
    ids=["github-token-prefix-outside-closed-class", "slack-token-missing-its-hyphen"],
)
def test_pattern_does_not_fire_outside_its_own_fixed_shape(label, probe):
    pattern = dict(cs.PATTERNS)[label]
    assert not pattern.search(probe), f"{label!r} incorrectly matched out-of-shape probe {probe!r}"


# --- 2. a credential in a file that is not valid UTF-8 is found, not
# warned about and skipped. --------------------------------------------------


def test_credential_in_a_non_utf8_file_is_found_not_warned_about(tmp_path, monkeypatch, capsys):
    """The hole SPEC.md 5.1.2 closes: nine tracked files (icons, a banner,
    capture fixtures) never decoded as UTF-8, and a credential pasted into
    any of them used to produce a warning and a clean exit rather than a
    finding. This probe mirrors that shape - invalid UTF-8 continuation
    bytes on lines before the credential, inside a `.png`-suffixed file (a
    suffix `FORBIDDEN_SUFFIXES` does not refuse) - and pins that it is now
    reported as a finding at the correct line, with no warning text and no
    complaint about the file being unreadable or undecodable anywhere in
    the output.
    """
    target = tmp_path / "icon.png"
    content = (
        b"\xff\xfe garbage line one\n"
        b"second line still garbage \x80\x81\n"
        + _credential_assignment_probe().encode("ascii")
        + b"\n"
    )
    target.write_bytes(content)

    rc, out, err = _run_main(monkeypatch, capsys, str(target))

    assert rc == 1, f"expected a finding (exit 1), got exit {rc}\nstdout:\n{out}\nstderr:\n{err}"
    assert f"{target}:3: possible credential assignment" in err, (
        f"expected the finding on line 3 (the credential line, after two invalid-UTF-8 "
        f"lines), got stderr:\n{err}"
    )
    assert "warn" not in err.lower(), f"a found credential must not also read as a warning: {err!r}"
    assert "decod" not in err.lower(), (
        f"the file must not be reported as undecodable when a credential inside it was "
        f"found: {err!r}"
    )
    assert "unreadable" not in err.lower(), (
        f"a decodable-as-latin-1 file is not unreadable: {err!r}"
    )


# --- 3. three outcomes: 0 clean, 1 a finding, 2 the scan did not run. ------


def test_exit_code_is_1_for_a_finding(tmp_path, monkeypatch, capsys):
    target = tmp_path / "config.py"
    target.write_text(_credential_assignment_probe() + "\n", encoding="utf-8")

    rc, out, err = _run_main(monkeypatch, capsys, str(target))

    assert rc == 1, f"expected exit 1 for a real finding, got {rc}\nstderr:\n{err}"


def test_exit_code_is_2_for_a_run_that_scanned_nothing(tmp_path, monkeypatch, capsys):
    """`check_secrets.py <a path that is not a file>` walks zero files.
    SPEC.md 5.1.2 calls a verdict earned by examining nothing "the vacuity
    fail-open in its plainest form" and requires it to fail the gate (exit
    2), not pass it (exit 0) - distinct from a run that found a real
    credential (exit 1), and distinct from a path that exists but cannot be
    read, which is a different exit-2 outcome pinned separately below. A
    directory is used as the argument rather than a path that simply does
    not exist: the latter is now itself an unreadable-path finding (its own
    test below), so it no longer walks zero files - a directory is filtered
    out before any read is attempted and is the case that still does.
    """
    not_a_file = tmp_path / "a-directory"
    not_a_file.mkdir()

    rc, out, err = _run_main(monkeypatch, capsys, str(not_a_file))

    assert rc == 2, f"expected exit 2 for a scan of zero files, got {rc}\nstderr:\n{err}"
    assert "0 file" in err or "0 file" in out, (
        f"expected the zero-files outcome to be stated in the summary; "
        f"stdout:\n{out}\nstderr:\n{err}"
    )


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores a file's own permission bits, so chmod(0o000) does not deny "
    "the read and this test would fail for a reason unrelated to what it checks",
)
def test_exit_code_is_2_for_a_file_that_cannot_be_read(tmp_path, monkeypatch, capsys):
    """An `OSError` while reading a path leaves that file unexamined, and
    SPEC.md 5.1.2 says an unexamined file must not be called clean: it is
    reported as not scanned and the run fails with exit 2, not exit 0 and
    not exit 1 (there is no credential to report - the file was never
    opened).
    """
    target = tmp_path / "secret.txt"
    target.write_text("nothing interesting", encoding="utf-8")
    target.chmod(0o000)
    try:
        rc, out, err = _run_main(monkeypatch, capsys, str(target))
    finally:
        target.chmod(0o600)

    assert rc == 2, (
        f"expected exit 2 for an unreadable file, got {rc}\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert str(target) in err, f"expected the unreadable path to be named in stderr, got: {err!r}"


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores a file's own permission bits, so chmod(0o000) does not deny "
    "the read and this test would fail for a reason unrelated to what it checks",
)
def test_exit_code_is_2_when_a_finding_and_an_unreadable_file_both_occur(
    tmp_path, monkeypatch, capsys
):
    """SPEC.md 5.1.2: when a run turns up both a real finding and a file
    that could not be read, the status is 2 - the broken gate outranks the
    finding, since a caller cannot trust that the unreadable file was not
    hiding a second one - and the findings already made are still printed
    with the same remediation paragraph as a plain exit-1 run, rather than
    being discarded because the run as a whole did not complete.
    """
    readable = tmp_path / "config.py"
    readable.write_text(_credential_assignment_probe() + "\n", encoding="utf-8")
    unreadable = tmp_path / "secret.txt"
    unreadable.write_text("nothing interesting", encoding="utf-8")
    unreadable.chmod(0o000)
    try:
        rc, out, err = _run_main(monkeypatch, capsys, str(readable), str(unreadable))
    finally:
        unreadable.chmod(0o600)

    assert rc == 2, f"expected exit 2 when a finding and an unreadable file both occur, got {rc}"
    assert f"error: {unreadable}: unreadable" in err, (
        f"expected an error: line naming the unreadable file, got stderr:\n{err}"
    )
    assert f"{readable}:1: possible credential assignment" in err, (
        f"expected the finding from the readable file to still be reported, got stderr:\n{err}"
    )
    assert "content must move outside the repository" in err and "must be rotated" in err, (
        f"expected the same remediation paragraph a plain finding gets, got stderr:\n{err}"
    )


def test_a_run_over_only_exempt_paths_exits_0_not_2(monkeypatch, capsys):
    """A run that considered at least one path, all of which turned out
    exempt (the scanner exempting its own file, per SPEC.md 5.1.2), is not
    the vacuity SPEC.md 5.1.2 requires exit 2 for - that rule is for a run
    that considered nothing at all, e.g. a mistyped path. Driven against
    the real `check_secrets.py` targeting only itself, since self-exemption
    is the one exemption this module can invoke without needing the
    fixture-prefix machinery that only activates through `git ls-files`.
    """
    rc, out, err = _run_main(monkeypatch, capsys, str(SCRIPT))

    assert rc == 0, (
        f"a run over only exempt paths must not fail the gate\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert "nothing was examined" not in out and "nothing was examined" not in err, (
        f"a run that considered an exempt path is not the same outcome as a run that "
        f"considered nothing: stdout:\n{out}\nstderr:\n{err}"
    )
    assert "did not run to completion" not in out and "did not run to completion" not in err, (
        f"a run over only exempt paths is not the unreadable-file outcome either: "
        f"stdout:\n{out}\nstderr:\n{err}"
    )
    assert "exempt" in out, (
        f"expected the summary to say the file was considered and exempt, distinct from "
        f"either failure wording above: stdout:\n{out}"
    )


def test_a_path_that_cannot_be_stat_produces_an_error_line_and_exit_2(
    tmp_path, monkeypatch, capsys
):
    """A dangling symlink cannot be opened for a reason distinct from a
    permission bit - there is nothing at the far end to stat - and SPEC.md
    5.1.2 requires this to surface the same way any other unreadable path
    does: an `error:` line naming it and exit 2, not a silent skip that
    would leave it indistinguishable from a path nobody asked about.
    """
    target = tmp_path / "dangling-link"
    target.symlink_to(tmp_path / "does-not-exist-target")

    rc, out, err = _run_main(monkeypatch, capsys, str(target))

    assert rc == 2, f"expected exit 2 for a path that cannot be stat'ed, got {rc}\nstderr:\n{err}"
    assert "error:" in err and "unreadable" in err, (
        f"expected the same error: line an unreadable file gets, got stderr:\n{err}"
    )


# --- 4. FORBIDDEN_SUFFIXES / FORBIDDEN_NAMES are refused by name whatever
# the content, and the one exemption is the tests/fakes/fixtures/ prefix. ---


def _scratch_scanner_repo(tmp_path):
    """A disposable git repository under `tmp_path` carrying an unmodified,
    mechanically copied `check_secrets.py` at `.github/check_secrets.py` -
    the same relative position it holds in this repository, since `main()`
    with no arguments walks `git ls-files` and this is the only way to
    drive that fallback against a synthetic tree instead of the real one.
    Returns the repo root; the caller writes files under it, commits, and
    runs the copied script with no arguments.
    """
    repo = tmp_path / "repo"
    github_dir = repo / ".github"
    github_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT, github_dir / "check_secrets.py")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    return repo


def _commit_all(repo):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=scratch@example.test",
            "-c",
            "user.name=scratch",
            "commit",
            "-q",
            "-m",
            "scratch fixture commit",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _run_scanner_over(repo):
    return subprocess.run(
        [sys.executable, str(repo / ".github" / "check_secrets.py")],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_forbidden_suffix_is_refused_by_name_whatever_the_content(tmp_path):
    repo = _scratch_scanner_repo(tmp_path)
    outside = repo / "other" / "sample.pcap"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"harmless bytes, no credential shape at all")
    _commit_all(repo)

    result = _run_scanner_over(repo)

    assert result.returncode == 1, (
        f"a .pcap outside the fixture prefix must be refused whatever its content\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "must never be tracked" in result.stderr, (
        f"expected the by-name refusal message, not a content-based finding, got "
        f"stderr:\n{result.stderr}"
    )


def test_forbidden_suffix_exemption_is_a_path_prefix_not_a_free_pass(tmp_path):
    """The same filename, one directory up, is refused; only the literal
    `tests/fakes/fixtures/` prefix is exempt.
    """
    repo = _scratch_scanner_repo(tmp_path)
    inside = repo / "tests" / "fakes" / "fixtures" / "sample.pcap"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"harmless bytes, no credential shape at all")
    outside = repo / "other" / "sample.pcap"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"harmless bytes, no credential shape at all")
    _commit_all(repo)

    result = _run_scanner_over(repo)

    assert result.returncode == 1, (
        f"expected exactly the outside copy to be refused\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "tests/fakes/fixtures/sample.pcap" not in result.stderr, (
        f"the fixture-prefixed .pcap must not be refused: stderr:\n{result.stderr}"
    )
    assert "other/sample.pcap" in result.stderr and "must never be tracked" in result.stderr, (
        f"the same filename one directory up must be refused by name: stderr:\n{result.stderr}"
    )


def test_forbidden_name_is_refused_by_name_whatever_the_content(tmp_path):
    """`FORBIDDEN_NAMES` (`id_rsa`, `ca.crt`, `server.crt`, `.env`, ...) is a
    separate rule from `FORBIDDEN_SUFFIXES`, keyed on the exact filename
    rather than its extension, and carries the same fixture exemption.
    """
    repo = _scratch_scanner_repo(tmp_path)
    inside = repo / "tests" / "fakes" / "fixtures" / "id_rsa"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"harmless bytes, no credential shape at all")
    outside = repo / "id_rsa"
    outside.write_bytes(b"harmless bytes, no credential shape at all")
    _commit_all(repo)

    result = _run_scanner_over(repo)

    assert result.returncode == 1, (
        f"expected the top-level id_rsa to be refused by name\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "tests/fakes/fixtures/id_rsa" not in result.stderr, (
        f"the fixture-prefixed id_rsa must not be refused: stderr:\n{result.stderr}"
    )
    lines = [line for line in result.stderr.splitlines() if "must never be tracked" in line]
    assert any(line.startswith("id_rsa:") for line in lines), (
        f"expected the top-level id_rsa to be named in a by-name refusal line: {lines!r}"
    )


# --- 5. matched text is never echoed. ---------------------------------------

# A module-level constant, assembled from two pieces, rather than a literal
# inline in the test below: `test_no_probe_value_appears_contiguously_in_
# this_modules_own_source` checks this value too, and a value only a test
# function's local scope knows about could not be enumerated there.
MATCHED_SECRET_VALUE = "synthetic-example-value" + "-9f3c2a"


def test_matched_secret_text_is_never_echoed_in_output(tmp_path, monkeypatch, capsys):
    target = tmp_path / "config.py"
    secret_value = MATCHED_SECRET_VALUE
    target.write_text(f'api_key = "{secret_value}"\n', encoding="utf-8")

    rc, out, err = _run_main(monkeypatch, capsys, str(target))

    assert rc == 1
    assert secret_value not in out and secret_value not in err, (
        f"the matched secret text must never be echoed - the finding is a path, a line "
        f"number and a label\nstdout:\n{out}\nstderr:\n{err}"
    )
    assert "possible credential assignment" in err, (
        f"expected the finding to still name the path, line and label, got: {err!r}"
    )


# --- the two behaviours SPEC.md 5.1.2 says are deliberately unchanged. -----


def test_scanner_exempts_its_own_file_from_scanning(tmp_path):
    """`check_secrets.py` necessarily contains the patterns it looks for
    and exempts itself. Pinned by appending a genuine `credential
    assignment` probe as a comment to a copy of the script at its own
    `.github/check_secrets.py` location, alongside one harmless tracked
    file, and checking that only the harmless file is counted as scanned -
    the script's own copy, despite carrying a real fire probe, produces
    neither a finding nor a place in the file count.
    """
    repo = _scratch_scanner_repo(tmp_path)
    script_copy = repo / ".github" / "check_secrets.py"
    probe_line = "\n# self-scan probe: " + _credential_assignment_probe() + "\n"
    with script_copy.open("a", encoding="utf-8") as f:
        f.write(probe_line)
    (repo / "README.md").write_text("nothing interesting here\n", encoding="utf-8")
    _commit_all(repo)

    result = _run_scanner_over(repo)

    assert result.returncode == 0, (
        f"the script's own file must not turn into a finding against itself\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "1 file(s)" in result.stdout, (
        f"only the harmless README.md should have been counted as scanned, meaning the "
        f"script's own file was excluded from the count entirely: stdout:\n{result.stdout}"
    )


# --- the invariant a security audit found broken: no probe value may sit
# whole in this module's own tracked source. ---------------------------------


def _all_registered_probe_values():
    """The probe tables the other tests in this module already enumerate:
    `PROBES` (one fire probe per `PATTERNS` label), the seven shapes
    `test_npm_registry_credential_fires_on_each_setting_name_and_line_shape`
    drives, the value `test_matched_secret_text_is_never_echoed_in_output`
    plants and checks is never echoed, and the two no-fire tables
    `QUANTIFIER_FLOOR_MINUS_ONE` and `STRUCTURAL_NOFIRE` - included even
    though neither matches any pattern today, because "built not to match
    anything" is a statement of intent, not something that protects a file
    whose whole subject is credential-shaped strings; a real credential
    that happened to land in one of those slots would be exactly as silent
    as a probe would. `PLACEHOLDER_SHAPES` is the one table left out: those
    three strings (`token = ""`, `password = <redacted>`, `api_key =
    "your-key-here"`) are not credential-shaped at all - each is
    deliberately empty, unquoted or a stock documentation placeholder - and
    splitting them into pieces would make them mean nothing as literals
    while gaining nothing this check is for.
    """
    values = list(PROBES.values())
    values.extend(param.values[0] for param in NPM_REGISTRY_CREDENTIAL_PROBES)
    values.append(MATCHED_SECRET_VALUE)
    values.extend(probe for _, probe in QUANTIFIER_FLOOR_MINUS_ONE)
    values.extend(probe for _, probe in STRUCTURAL_NOFIRE)
    return values


def test_no_probe_value_appears_contiguously_in_this_modules_own_source():
    """The finding a security audit made against this module: a probe
    string spelled out as one contiguous literal sits in the tracked source
    exactly the way a real credential pasted into this file would, and
    whether today's scanner patterns happen to catch that particular shape
    is an accident of an anchor or a word boundary, not something this
    module should ever depend on for looking clean - this file's whole
    subject is credential-shaped strings, so a literal one is the single
    most plausible leak this repository could produce.

    What this actually covers is exactly `_all_registered_probe_values()`:
    the module-level probe tables the other tests in this file already
    enumerate. A probe built and used only inside a test function's own
    local scope is outside it, because nothing here can enumerate a name
    this module never bound - which is why every probe this file needs
    checked is bound at module level rather than inline in the test that
    uses it. This is what turns "built that way, so it holds" into
    "checked to hold" for those tables; it is not a guarantee that a
    contiguous probe is impossible anywhere in the file.

    Reads this file's own bytes straight off disk, not any value already
    reconstructed in memory: the failure mode is specifically about what is
    written to the tracked source, and comparing against an in-memory copy
    would not see it.
    """
    source = Path(__file__).read_bytes().decode("utf-8")
    offenders = sorted({value for value in _all_registered_probe_values() if value in source})
    assert not offenders, (
        f"probe value(s) appear as one contiguous literal in this module's own tracked "
        f"source, the same shape a real pasted credential would take - assemble them from "
        f"separate pieces at runtime instead: {offenders!r}"
    )
