"""`.github/check_dependabot_node.py` (SPEC.md 5.1.1, "The decision is to
keep it and to make the silence loud", issue #202).

SPEC.md 5.1.1's hazard is that Dependabot's npm updater runs its own,
unrelated Node version, pinned in `dependabot-core`'s
`npm_and_yarn/Dockerfile` as `ARG NODEJS_VERSION=<major>`, and that version
can fall below the major this repository's `frontend/package.json` requires
in `engines.node`. When it does, `engine-strict=true` (SPEC.md 5.1.1) makes
Dependabot's own `npm install` refuse with `EBADENGINE`, and no pull request
appears - "an update that does not happen looks exactly like a week with no
updates". This script exists to turn that silence into a scheduled, named,
red run instead.

The contract, as SPEC.md 5.1.1 and the workflow step that calls this script
describe it: compare the major of `ARG NODEJS_VERSION=<major>` against the
major of `engines.node` in `frontend/package.json`.

- Exit 0, printing both numbers, when the Dockerfile's major is at or above
  the floor's major.
- Exit 1, with a named message citing both numbers and SPEC.md 5.1.1, when
  it is below.
- Exit 2 - SPEC.md 13's "a check that could not run is not a clean one" -
  when the `ARG` line cannot be found, when either file cannot be read (a
  bad path included), or when `engines.node`'s floor cannot be parsed as
  `>=X.Y.Z`. No traceback, in every one of those cases: a script two rungs
  removed from a pull request going red must fail as a message, not a stack
  dump, per SPEC.md 13's "a check that could not run" rule.

Two families of test:

- The comparison, the multi-`ARG`-line handling and every exit-2 shape
  reachable through `--dockerfile PATH` and `--package-json PATH` are
  driven entirely as a subprocess against fixtures written under
  `tmp_path` from strings, exactly as SPEC.md 10.6 asks for a gate script:
  the exit status and the absence of a traceback are the observable
  contract, not anything importing the module in-process could assert more
  directly. No network: both flags are given explicitly, so the script
  never reaches for the real `dependabot/dependabot-core` checkout or this
  repository's own `frontend/package.json`.
- `fetch_dockerfile`'s failure modes - `--dockerfile` is the only path a
  local fixture can drive, and the workflow never passes it, so
  `fetch_dockerfile` is what CI actually calls every scheduled run. Those
  are driven by loading the script as a module (the same
  `load_module` pattern `tests/tools/test_pinned_facts.py` uses for
  `check_pinned_facts.py`, for the same reason: `.github` is not an
  importable package) and monkeypatching `urllib.request.urlopen` on it,
  then calling `main([])` in-process and catching the `SystemExit` it
  raises - the no-`--dockerfile` argument is exactly the branch that takes
  the fetch path.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "check_dependabot_node.py"


def load_module():
    """Imports the checker by path: `.github` is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_dependabot_node", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cdn = load_module()


def run(dockerfile: Path, package_json: Path, *, timeout: float = 30):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dockerfile",
            str(dockerfile),
            "--package-json",
            str(package_json),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def dockerfile_with(tmp_path: Path, major: int, name: str = "Dockerfile") -> Path:
    path = tmp_path / name
    path.write_text(
        "FROM node:lts AS build\n"
        f"ARG NODEJS_VERSION={major}\n"
        "RUN corepack enable\n",
        encoding="utf-8",
    )
    return path


def dockerfile_with_two_args(
    tmp_path: Path, first_major: int, second_major: int, name: str = "Dockerfile"
) -> Path:
    """A multi-stage Dockerfile naming `ARG NODEJS_VERSION` twice, once per

    stage - the shape `updater_versions` is documented to read as a list
    rather than a single answer.
    """
    path = tmp_path / name
    path.write_text(
        "FROM node:lts AS build\n"
        f"ARG NODEJS_VERSION={first_major}\n"
        "RUN corepack enable\n"
        "FROM node:lts AS test\n"
        f"ARG NODEJS_VERSION={second_major}\n"
        "RUN npm test\n",
        encoding="utf-8",
    )
    return path


def package_json_with(tmp_path: Path, floor: str, name: str = "package.json") -> Path:
    path = tmp_path / name
    path.write_text(
        "{\n"
        '  "name": "companion",\n'
        '  "engines": {\n'
        f'    "node": "{floor}"\n'
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# The comparison itself: major-to-major, not the full three-component floor
# (SPEC.md 5.1.1 is explicit that the check "cannot establish that the
# runner's resolved version is at or above the floor's minor" - it only
# compares majors).
# ---------------------------------------------------------------------------


def test_above_the_floor_exits_zero_and_prints_both_numbers(tmp_path):
    """24 vs `>=22.12.0`: the Dockerfile's major (24) is above the floor's

    major (22), the ordinary "no drift" case.
    """
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 0
    assert "24" in stdout
    assert "22" in stdout


def test_equal_majors_exits_zero(tmp_path):
    """22 vs `>=22.12.0`: equal majors satisfy "at or above"."""
    dockerfile = dockerfile_with(tmp_path, 22)
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 0
    assert "22" in stdout


def test_one_below_the_floor_exits_one_and_names_both_numbers_and_the_spec(tmp_path):
    """20 vs `>=22.12.0`: the hazard SPEC.md 5.1.1 describes actually

    happening. The message must name both numbers, not just say "drifted",
    and must cite SPEC.md 5.1.1 so a reader lands on the paragraph that
    explains why a red scheduled run - and not a silently skipped update -
    is the correct outcome.
    """
    dockerfile = dockerfile_with(tmp_path, 20)
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 1
    assert "20" in stderr
    assert "22" in stderr
    assert "5.1.1" in stderr
    assert "Traceback" not in stderr


# ---------------------------------------------------------------------------
# Multi-stage Dockerfiles: more than one `ARG NODEJS_VERSION` line.
# ---------------------------------------------------------------------------


def test_two_agreeing_arg_lines_exit_zero(tmp_path):
    """Two stages naming the same major is one answer, not two - the

    ordinary case for a real multi-stage Dockerfile that just happens to
    declare the build arg more than once.
    """
    dockerfile = dockerfile_with_two_args(tmp_path, 24, 24)
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 0
    assert "24" in stdout


def test_two_disagreeing_arg_lines_exit_two_and_name_both_values(tmp_path):
    """Two stages naming different majors is not a number this check can

    trust either of - "could not check", the same outcome as the line
    being absent, and the message must name both disagreeing values so a
    reader is not left to find them by re-reading the Dockerfile.
    """
    dockerfile = dockerfile_with_two_args(tmp_path, 24, 20)
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "24" in stderr
    assert "20" in stderr
    assert "Traceback" not in stderr


# ---------------------------------------------------------------------------
# Exit 2: the check itself could not run (SPEC.md 13).
# ---------------------------------------------------------------------------


def test_arg_line_absent_exits_two_and_names_the_file(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:lts AS build\nRUN corepack enable\n", encoding="utf-8"
    )
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "NODEJS_VERSION" in stderr
    assert "Traceback" not in stderr


def test_commented_arg_line_does_not_count_as_present(tmp_path):
    """A commented-out `ARG` line must be treated exactly like an absent

    one (exit 2), not parsed as though it declared a version - the
    Dockerfile line SPEC.md 5.1.1 reads is a live `ARG` instruction, and a
    commented-out one is not what the updater's build actually uses.
    """
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM node:lts AS build\n# ARG NODEJS_VERSION=24\nRUN corepack enable\n",
        encoding="utf-8",
    )
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_dockerfile_path_does_not_exist_exits_two_without_a_traceback(tmp_path):
    dockerfile = tmp_path / "does-not-exist" / "Dockerfile"
    package_json = package_json_with(tmp_path, ">=22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_package_json_path_does_not_exist_exits_two_without_a_traceback(tmp_path):
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = tmp_path / "does-not-exist" / "package.json"

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_engines_node_missing_exits_two(tmp_path):
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = tmp_path / "package.json"
    package_json.write_text('{"name": "companion"}\n', encoding="utf-8")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_engines_node_not_of_the_gte_form_exits_two(tmp_path):
    """A bare version with no `>=` prefix is not the floor's documented

    shape ("`engines.node` is a lower bound with three components,
    `>=X.Y.Z`", SPEC.md 5.1.1) and must not be silently parsed as though it
    were.
    """
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = package_json_with(tmp_path, "22.12.0")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_engines_node_empty_string_exits_two(tmp_path):
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = package_json_with(tmp_path, "")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


def test_package_json_invalid_json_exits_two(tmp_path):
    dockerfile = dockerfile_with(tmp_path, 24)
    package_json = tmp_path / "package.json"
    package_json.write_text("not valid json", encoding="utf-8")

    returncode, stdout, stderr = run(dockerfile, package_json)

    assert returncode == 2
    assert "Traceback" not in stderr


# ---------------------------------------------------------------------------
# fetch_dockerfile() - the only path CI actually takes. The workflow never
# passes --dockerfile (SPEC.md 5.1.1's line is read from dependabot-core's
# default branch), so every fetch failure mode below is exercised through
# main([]), the no-argument entry point, with urllib.request.urlopen
# monkeypatched on the loaded module. Every case must end in SystemExit(2)
# with a message and never a traceback - a scheduled check two rungs removed
# from a red pull request is exactly the place SPEC.md 13's "a check that
# could not run is not a clean one" has to hold without a human watching.
# ---------------------------------------------------------------------------


class FakeResponse:
    """A minimal stand-in for the context manager `urlopen` returns."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, amount: int = -1) -> bytes:
        if amount is None or amount < 0:
            return self._body
        return self._body[:amount]


def raising_urlopen(exc: BaseException):
    def fake(request, timeout=None):
        raise exc

    return fake


def test_fetch_dockerfile_http_error_exits_two(monkeypatch):
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        raising_urlopen(
            urllib.error.HTTPError(
                "https://example.invalid/Dockerfile", 404, "Not Found", None, None
            )
        ),
    )
    with pytest.raises(SystemExit) as excinfo:
        cdn.main([])
    assert excinfo.value.code == 2


def test_fetch_dockerfile_url_error_exits_two(monkeypatch):
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        raising_urlopen(urllib.error.URLError("name resolution failed")),
    )
    with pytest.raises(SystemExit) as excinfo:
        cdn.main([])
    assert excinfo.value.code == 2


def test_fetch_dockerfile_timeout_exits_two(monkeypatch):
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        raising_urlopen(TimeoutError("timed out")),
    )
    with pytest.raises(SystemExit) as excinfo:
        cdn.main([])
    assert excinfo.value.code == 2


def test_fetch_dockerfile_body_over_the_size_limit_exits_two(monkeypatch):
    """A body far larger than any believable size limit - refusing to read

    it rather than growing without bound is the same defence
    `check_pinned_facts.py` applies to its own fetch (the script's own
    docstring).
    """
    oversized_body = (b"ARG NODEJS_VERSION=24\n" * 500_000)  # well over 10 MB
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(oversized_body),
    )
    with pytest.raises(SystemExit) as excinfo:
        cdn.main([])
    assert excinfo.value.code == 2


def test_fetch_dockerfile_undecodable_bytes_exits_two(monkeypatch):
    undecodable = b"\xff\xfe\x00ARG NODEJS_VERSION=24\n"
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(undecodable),
    )
    with pytest.raises(SystemExit) as excinfo:
        cdn.main([])
    assert excinfo.value.code == 2


def test_fetch_dockerfile_success_exits_zero(tmp_path, monkeypatch):
    """A successful fetch returning a body naming `ARG NODEJS_VERSION=24`

    reaches the ordinary comparison, exactly as a local `--dockerfile`
    fixture would - `--package-json` is still given explicitly so the
    comparison does not depend on this checkout's own, changing floor.
    """
    body = (
        b"FROM node:lts AS build\n"
        b"ARG NODEJS_VERSION=24\n"
        b"RUN corepack enable\n"
    )
    monkeypatch.setattr(
        cdn.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(body),
    )
    package_json = package_json_with(tmp_path, ">=22.12.0")

    # main() returns an ordinary int for the two outcomes it decides itself
    # (0 and 1); only fail() raises SystemExit(2). A successful fetch and
    # comparison is the former, so this is a plain return-value assertion,
    # not a SystemExit catch like every failure case above.
    assert cdn.main(["--package-json", str(package_json)]) == 0
