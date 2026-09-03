"""`tools/gen-protocol-types.mjs`'s guard-keyword allowlist (SPEC.md 4.3.11).

Driven the same way `test_certs.py` drives the shell scripts: as an ordinary
non-interactive command via `subprocess`, so a black-box run of the tool is
the check rather than an import of its internals. The generator is Node
rather than a shell script, but the reasoning for putting it in this suite
rather than in `frontend/`'s vitest is the same one that reason applies to
the shell scripts: this is repository tooling that touches both
`docs/schemas` (source) and `frontend/src/lib/protocol.ts` (generated
output), not application code under test by the app's own suite, and
`tests/tools/` is already where a script in `tools/` is exercised as a
subprocess regardless of the language it is written in. It also keeps this
suite - which already asserts things about `frontend/src/lib/protocol.ts`
being in sync via CI - the one place that knows how the generator is
invoked.

SPEC.md 4.3.11: "the discipline is an allowlist of keywords, not a list of
things that break it... a `minimum` added to an outgoing schema would emit a
guard with no bound and no error." Before this section, the guard walk
recognised only the constructs it knew how to translate and passed
everything else through silently, so a keyword the walk did not know about
was accepted with no guard for it at all. The two things worth pinning are
therefore the two directions of that regression: an unknown keyword on a
node the guard walk reaches must stop the build now, naming the keyword; and
the two keywords SPEC.md 4.3.11 names as already present in `docs/schemas`
today - `contentEncoding` on `outgoing/screen_image.json` and
`minimum`/`maximum` on `incoming/get_log.json` - must not have started
failing the build now that the walk enforces an allowlist rather than
passing everything through.

Run against a copy of `docs/schemas` in a temporary directory, laid out the
same way the generator expects the real repository to be (`tools/`,
`docs/schemas/`, `frontend/src/lib/`) so its own `ROOT`, computed from the
script's own location, resolves inside the copy rather than the real
checkout. Nothing under the real repository is written to.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tools" / "gen-protocol-types.mjs"
SCHEMA_DIR = REPO_ROOT / "docs" / "schemas"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not on PATH; the generator cannot be exercised",
)


def build_workspace(tmp_path: Path) -> Path:
    """A copy of the repository's `tools/gen-protocol-types.mjs` and
    `docs/schemas`, under a fresh `frontend/src/lib/` the generator can write
    into. Laid out identically to the real repository so the generator's own
    `ROOT = join(HERE, '..')` resolves inside the copy.
    """
    workspace = tmp_path / "workspace"
    (workspace / "tools").mkdir(parents=True)
    shutil.copy2(GENERATOR, workspace / "tools" / GENERATOR.name)
    shutil.copytree(SCHEMA_DIR, workspace / "docs" / "schemas")
    (workspace / "frontend" / "src" / "lib").mkdir(parents=True)
    return workspace


def run_generator(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(workspace / "tools" / GENERATOR.name), *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=60,
    )


def inject_top_level_keyword(schema_path: Path, keyword: str, value: object) -> None:
    """Adds one extra top-level keyword to a schema document, the same
    level `$schema`/`$id`/`type`/`properties` already sit at - the node the
    generator's guard walk visits first for that message, so a keyword
    unknown to the allowlist is caught at the outermost node reached rather
    than requiring it to be buried in a nested property to be found.
    """
    doc = json.loads(schema_path.read_text())
    assert keyword not in doc, (
        f"{schema_path} already declares {keyword!r}; pick an untouched fixture"
    )
    doc[keyword] = value
    schema_path.write_text(json.dumps(doc, indent=2) + "\n")


# ---------------------------------------------------------------------------
# The schemas as they stand today still generate cleanly.
# ---------------------------------------------------------------------------


def test_screen_image_still_declares_contentEncoding_and_get_log_still_declares_minimum_maximum():
    """A guard against the positive-control test below passing for the
    wrong reason: if these fixtures stopped carrying the keywords SPEC.md
    4.3.11 names, the "generates cleanly" test would pass trivially, having
    exercised nothing about the allowlist's ignore-list at all.
    """
    screen_image = json.loads(
        (SCHEMA_DIR / "outgoing" / "screen_image.json").read_text()
    )
    assert "contentEncoding" in json.dumps(screen_image)

    get_log = json.loads((SCHEMA_DIR / "incoming" / "get_log.json").read_text())
    get_log_text = json.dumps(get_log)
    assert "minimum" in get_log_text
    assert "maximum" in get_log_text


def test_the_schemas_as_they_stand_generate_cleanly(tmp_path):
    workspace = build_workspace(tmp_path)

    result = run_generator(workspace)

    assert result.returncode == 0, result.stderr
    out = workspace / "frontend" / "src" / "lib" / "protocol.ts"
    assert out.is_file()
    assert out.stat().st_size > 0
    # The two keywords named above are specifically what this run must not
    # have failed on - contentEncoding by being ignored on an outgoing
    # schema the guard walk reaches, minimum/maximum by never being reached
    # at all since the guard walk covers only outgoing schemas.
    assert "unsupported keyword" not in result.stderr


# ---------------------------------------------------------------------------
# An unsupported keyword on an outgoing schema fails the build, naming it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keyword,value", [("minimum", 0), ("pattern", "^[a-z]+$")])
def test_an_unsupported_keyword_on_an_outgoing_schema_fails_the_build(
    tmp_path, keyword, value
):
    """SPEC.md 4.3.11's own motivating example is `minimum`; a second,
    unrelated keyword (`pattern`) is included so this does not merely prove
    that the string "minimum" is special-cased somewhere rather than that
    the allowlist itself is what is doing the rejecting.
    """
    workspace = build_workspace(tmp_path)
    inject_top_level_keyword(
        workspace / "docs" / "schemas" / "outgoing" / "pong.json", keyword, value
    )

    result = run_generator(workspace)

    assert result.returncode != 0
    assert keyword in result.stderr
    assert not (workspace / "frontend" / "src" / "lib" / "protocol.ts").exists()


def test_the_same_unsupported_keyword_on_an_incoming_only_schema_does_not_fail_the_build(
    tmp_path,
):
    """The guard walk is for outgoing schemas only (SPEC.md 4.3.11: "the
    guards are for what the unit sends, and the plugin validates what the
    app sends"). The type-only walk that also reads incoming schemas has no
    keyword allowlist of its own to enforce, so the same keyword that fails
    the build on an outgoing schema must not fail it here - proving the
    positive control above is not passing merely because the generator
    never looked at the incoming schema this keyword was added to, but
    because the guard walk specifically does not reach it.
    """
    workspace = build_workspace(tmp_path)
    inject_top_level_keyword(
        workspace / "docs" / "schemas" / "incoming" / "get_stats.json", "minimum", 0
    )

    result = run_generator(workspace)

    assert result.returncode == 0, result.stderr
