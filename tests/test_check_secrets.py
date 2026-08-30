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

Nothing else in `.github/check_secrets.py` is this module's job: the rest
of `PATTERNS` and `main()`'s file-walking behaviour (forbidden suffixes,
the fixture exemption, unreadable files) have no test module at all, which
is issue #199 and deliberately not touched here.

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


@pytest.mark.parametrize(
    "probe",
    [
        pytest.param(
            "//registry.example.test/:_authToken=synthetic-example-value",
            id="slash-prefixed-authToken",
        ),
        pytest.param(
            "//registry.example.test/:_auth=synthetic-example-value",
            id="slash-prefixed-auth",
        ),
        pytest.param(
            "//registry.example.test/:_password=synthetic-example-value",
            id="slash-prefixed-password",
        ),
        pytest.param(
            "@example-scope:_authToken=synthetic-example-value",
            id="at-prefixed-scoped-registry",
        ),
        pytest.param(
            "_authToken=synthetic-example-value",
            id="unprefixed-authToken",
        ),
        pytest.param(
            "_auth=synthetic-example-value",
            id="unprefixed-auth",
        ),
        pytest.param(
            "_password=synthetic-example-value",
            id="unprefixed-password",
        ),
    ],
)
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
