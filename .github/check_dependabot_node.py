#!/usr/bin/env python3
"""Checks Dependabot's updater against the Node floor `frontend/package.json` sets.

SPEC.md 5.1.1 (issue #202) keeps `engine-strict` in `.npmrc` rather than a CI
step that pins a Node version of its own: `engines.node` is already the one
place the floor is written, and a second copy in a workflow is a second thing
to keep in sync with it. What is not written down anywhere in this repository
is whether Dependabot's own updater still runs a Node new enough to open a
pull request against that floor at all - if the updater fell behind it, the
dependency it manages would simply stop moving, silently, which is the
"quiet week" the decision explicitly declines to wait for.

The updater's Node major is public: `dependabot/dependabot-core` writes it as
`ARG NODEJS_VERSION=<major>` in `npm_and_yarn/Dockerfile`. This fetches that
line from the default branch and compares its major against the major of
`engines.node`, the same way `check_pinned_facts.py` fetches upstream text
without a token and treats "could not check" as a failure distinct from
"checked and found wrong" (SPEC.md 13, rule 6).

Usage:
    python .github/check_dependabot_node.py
        Fetches npm_and_yarn/Dockerfile from dependabot/dependabot-core's
        default branch and reads frontend/package.json from this checkout.

    python .github/check_dependabot_node.py --dockerfile PATH --package-json PATH
        Reads both from local files instead, for a test to drive without a
        network call.

Exit status: 0 when the updater's Node major is at or above the floor's
major, printing both numbers. 1, named, when it is below. 2, named, when the
`ARG NODEJS_VERSION=` line is absent, two such lines disagree, the Dockerfile
cannot be fetched or read, or `frontend/package.json`'s floor cannot be
parsed - a check that could not run is not a clean result.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_JSON_PATH = ROOT / "frontend" / "package.json"

REPO = "dependabot/dependabot-core"
DOCKERFILE_PATH = "npm_and_yarn/Dockerfile"
CONTENTS_URL = f"https://api.github.com/repos/{REPO}/contents/{DOCKERFILE_PATH}"
TIMEOUT = 30
# The Dockerfile this reads is a few kilobytes. The cap exists so a
# malformed or hostile response fails cleanly rather than growing without
# bound, the same reasoning check_pinned_facts.py applies to its own fetch.
MAX_BYTES = 1024 * 1024

NODEJS_VERSION_RE = re.compile(r"^ARG NODEJS_VERSION=(\d+)", re.MULTILINE)
# `engines.node` is written as ">=22.12.0" (SPEC.md 5.1.1); this reads the
# major out of that fixed shape rather than parsing a general semver range.
FLOOR_RE = re.compile(r"^>=(\d+)")


def fail(message: str) -> NoReturn:
    """Exits 2: the check could not run, as distinct from running and finding
    the updater behind (exit 1) or ahead (exit 0).
    """
    print(f"check_dependabot_node: {message}", file=sys.stderr)
    raise SystemExit(2)


def floor_major(package_json: str) -> int | None:
    """The major version out of `engines.node` in `package_json`'s text.

    `None` for anything that is not valid JSON, not an object, missing
    `engines.node`, or whose value does not match the `>=MAJOR...` shape
    SPEC.md 5.1.1 fixes it to - one signal for "no usable floor here" rather
    than several exceptions for a caller to sort out.
    """
    try:
        data = json.loads(package_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    engines = data.get("engines")
    if not isinstance(engines, dict):
        return None
    node = engines.get("node")
    if not isinstance(node, str):
        return None
    match = FLOOR_RE.match(node)
    if match is None:
        return None
    return int(match.group(1))


def updater_versions(dockerfile: str) -> list[int]:
    """Every `ARG NODEJS_VERSION=<major>` value in `dockerfile`'s text, in
    the order they appear.

    A plain list, not deduplicated or reduced to one answer here: the
    Dockerfile builds more than one stage, and a caller that only kept the
    first match would silently trust a value that a later, disagreeing line
    contradicts. Empty when the line is absent, which is a fetch that
    succeeded but found nothing to read - still "could not check", not
    "checked and found zero".
    """
    return [int(value) for value in NODEJS_VERSION_RE.findall(dockerfile)]


def fetch_dockerfile() -> str:
    """Downloads `npm_and_yarn/Dockerfile` from `dependabot-core`'s default
    branch through the GitHub contents API, unauthenticated, the same way
    `check_pinned_facts.py` reaches the GitHub API without a token.

    The raw media type is requested so the response is the file's own text
    rather than a JSON envelope carrying it base64-encoded.
    """
    request = urllib.request.Request(
        CONTENTS_URL, headers={"Accept": "application/vnd.github.raw+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        fail(f"cannot fetch {DOCKERFILE_PATH} from {REPO}: HTTP {err.code}")
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as err:
        fail(f"cannot fetch {DOCKERFILE_PATH} from {REPO}: {err}")
    if len(payload) > MAX_BYTES:
        fail(f"{DOCKERFILE_PATH} from {REPO} is larger than {MAX_BYTES} bytes; refusing to read it")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as err:
        fail(f"{DOCKERFILE_PATH} from {REPO} is not valid UTF-8: {err}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dockerfile",
        type=Path,
        help="read the Dockerfile text from this path instead of fetching it",
    )
    parser.add_argument(
        "--package-json",
        type=Path,
        default=PACKAGE_JSON_PATH,
        help="read engines.node from this file instead of frontend/package.json",
    )
    args = parser.parse_args(argv)

    try:
        package_json_source = args.package_json.read_text(encoding="utf-8")
    except OSError as err:
        fail(f"cannot read {args.package_json}: {err}")

    floor = floor_major(package_json_source)
    if floor is None:
        fail(f"{args.package_json} has no usable engines.node floor")

    if args.dockerfile is not None:
        try:
            dockerfile_source = args.dockerfile.read_text(encoding="utf-8")
        except OSError as err:
            fail(f"cannot read {args.dockerfile}: {err}")
    else:
        dockerfile_source = fetch_dockerfile()

    versions = updater_versions(dockerfile_source)
    if not versions:
        fail(f"{DOCKERFILE_PATH} has no ARG NODEJS_VERSION= line")
    distinct = sorted(set(versions))
    if len(distinct) > 1:
        # Two stages naming different majors is not a number this check can
        # trust either of - "could not check", the same as the line being
        # absent, rather than a guess at which one governs the updater.
        fail(
            f"{DOCKERFILE_PATH} has disagreeing ARG NODEJS_VERSION values: {distinct}"
        )
    updater = distinct[0]

    if updater < floor:
        print(
            f"dependabot-core's updater runs Node {updater}, below the "
            f"engines.node floor of {floor}. Dependabot can no longer open "
            "pull requests that satisfy engine-strict; see SPEC.md 5.1.1.",
            file=sys.stderr,
        )
        return 1

    print(f"dependabot-core updater Node {updater} >= engines.node floor {floor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
