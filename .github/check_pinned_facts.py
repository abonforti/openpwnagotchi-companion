#!/usr/bin/env python3
"""Checks the symbols this plugin depends on against upstream pwnagotchi.

SPEC.md section 11 is an allowlist built by reading the fork's source at one
version. Nothing re-reads it afterwards, so if upstream renames an attribute the
whole test suite stays green - the tests run against a stub built from section
11, and a stub can only be as correct as the document it was built from. The
failure would surface on a device, as a plugin that loads and does nothing.

This closes that gap in the only way that costs nothing: it downloads upstream
at a ref and confirms each symbol still exists with the shape claimed.

Three invocations, because they answer two different questions and a usage
error is a third thing again. The default mode checks a **fixed tag** -
`verified_against` in the manifest - so nothing upstream does can turn it red;
the only way it fails is that this repository changed a fact without
re-verifying it, or lost the tag it verifies against, which is exactly a
defect worth blocking a merge for. It runs in CI, on every pull request, as a
required check (SPEC.md section 11.2). `--latest` asks the other question,
whether upstream has moved since, and is expected to fail eventually; it stays
out of CI and runs on a schedule instead, filing a ticket rather than
reddening somebody's unrelated pull request. `--ref` names an explicit tag,
branch or SHA, overriding both, for examining a candidate version before
anybody decides to move to it.

What it cannot do: check behaviour. That `pwnagotchi.restart` really touches
those files, that `agent.session()` really blocks - those are what the on-device
run is for. This sees names and shapes.

Usage:
    python .github/check_pinned_facts.py [--json]
        Default: checks against `verified_against` in the manifest (SPEC.md
        section 11.2). This is "do the facts still hold where we said they
        held", and it is what makes the recorded version load-bearing.

    python .github/check_pinned_facts.py --latest [--json]
        Checks against the newest upstream release. This is "has upstream
        moved since", and is expected to fail eventually.

    python .github/check_pinned_facts.py --ref <tag> [--json]
        Checks against a named tag, branch or SHA, overriding both of the
        above.

Exit status: 0 when every fact holds, 1 when one does not, 2 on a usage or
network error, including a manifest with no `verified_against` to default to
(so "upstream drifted" is distinguishable from "the check broke").
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tarfile
import warnings
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NoReturn

MANIFEST = Path(__file__).resolve().parent / "pinned_symbols.json"
TIMEOUT = 60
# `upstream` is read from the manifest and used to build the fetch URL below.
# This now runs on pull requests (a change on this branch put it there), and a
# fork PR can edit `pinned_symbols.json` to steer the fetch at any repository
# it names - a security audit flagged this. The workflow grants `contents:
# read`, fork PRs get no secrets, and the fetched text is only parsed and
# string-matched, so the risk is contained, not dangerous; it is still a
# diff-controlled value driving a network call in CI and deserves the same
# shape check `tools/install-on-pi.sh` applies to `--repo` for the same
# reason.
UPSTREAM_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
# The upstream tarball is a few megabytes. The cap exists so a malformed or
# hostile response fails cleanly instead of taking the runner's memory with it.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024


def fail(message: str) -> NoReturn:
    """Exits 2, which is what separates "the check broke" from "upstream moved".

    The workflow files a ticket on 1 and goes red on anything higher, so a
    network blip exiting 1 would open an issue announcing a drift that never
    happened.
    """
    print(f"check_pinned_facts: {message}", file=sys.stderr)
    raise SystemExit(2)


def fetch_tree(repo: str, ref: str) -> dict[str, str]:
    """Downloads a repository tarball at `ref`, returning path -> text.

    A tarball rather than a git clone: no git in the loop, one request, and
    nothing left on disk to clean up.
    """
    # Quoted: a ref containing ../ would otherwise walk the URL to another
    # repository and quietly answer about the wrong source.
    url = f"https://codeload.github.com/{repo}/tar.gz/{urllib.parse.quote(ref, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as err:
        fail(f"cannot fetch {repo}@{ref}: HTTP {err.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        fail(f"cannot fetch {repo}@{ref}: {err}")
    if len(payload) > MAX_ARCHIVE_BYTES:
        fail(f"{repo}@{ref} is larger than {MAX_ARCHIVE_BYTES} bytes; refusing to read it")

    files: dict[str, str] = {}
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")
    except tarfile.TarError as err:
        fail(f"{repo}@{ref} is not a readable tarball: {err}")
    with archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            # Strip the leading `<repo>-<ref>/` component the tarball adds.
            _, _, relative = member.name.partition("/")
            if not relative:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                files[relative] = handle.read(MAX_MEMBER_BYTES).decode("utf-8")
            except UnicodeDecodeError:
                continue
    return files


def latest_tag(repo: str) -> str:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)["tag_name"]
    except Exception as err:
        fail(f"cannot resolve the latest release: {err}")


def all_functions(tree: ast.Module) -> list[str]:
    """Every function anywhere, including methods.

    A symbol the plugin calls as `agent.session()` is a method, and one it calls
    as `pwnagotchi.uptime()` is a module-level function, but for "does this name
    still exist" the distinction is not worth two kinds of check.
    """
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def parameters_of(tree: ast.Module, name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            args = node.args
            found = {argument.arg for argument in args.args + args.kwonlyargs}
            if args.vararg:
                found.add(args.vararg.arg)
            if args.kwarg:
                found.add(args.kwarg.arg)
            return found
    return set()


def methods_of(tree: ast.Module, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


# §11.2: the two questions read the same but mean opposite things, so the
# report says which one this run answers rather than leaving a reader to
# guess from the ref alone. Module scope so a test can read the wording
# instead of restating it.
QUESTION_LABELS = {
    "verified": "checked against verified_against",
    "latest": "checked against the latest release",
    "asked": "checked against the requested ref",
}

TEXT_KINDS = {"contains_all", "absent"}
REQUIRED_KEYS = {
    "function": ("name",),
    "function_count": ("name", "count"),
    "methods": ("class", "names"),
    "contains_all": ("text",),
    "contains_count": ("text", "count"),
    "absent": ("text",),
}


def validate(fact: dict) -> str | None:
    """Rejects an entry that would pass without checking anything.

    The shape that matters: `"text": "foo"` where a list was meant iterates the
    characters of the string, and every one of them is in any Python file, so
    the fact passes vacuously for ever. A checker that cannot fail is worse than
    no checker, so a malformed manifest is an error rather than a green run.
    """
    identifier = fact.get("id", "<no id>")
    kind = fact.get("kind")
    if kind not in REQUIRED_KEYS:
        return f"{identifier}: unknown kind {kind!r}"
    if "file" not in fact:
        return f"{identifier}: no file"
    for key in REQUIRED_KEYS[kind]:
        if key not in fact:
            return f"{identifier}: kind {kind} needs {key!r}"
    if kind in TEXT_KINDS and not isinstance(fact["text"], list):
        return f"{identifier}: 'text' must be a list for kind {kind}"
    if kind == "contains_count" and not isinstance(fact["text"], str):
        return f"{identifier}: 'text' must be a string for kind {kind}"
    if kind == "methods" and not isinstance(fact["names"], list):
        return f"{identifier}: 'names' must be a list"
    return None


def check(fact: dict, files: dict[str, str]) -> str | None:
    """Returns a description of what is wrong, or None when the fact holds."""
    path = fact["file"]
    source = files.get(path)
    if source is None:
        return f"{path} no longer exists upstream"

    kind = fact["kind"]

    if kind in {"function", "function_count", "methods"}:
        try:
            # Upstream carries a few unescaped backslashes in regex literals,
            # which are somebody else's warnings and not this check's business.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source)
        except SyntaxError as err:
            return f"{path} does not parse as Python: {err}"

    if kind == "function":
        name = fact["name"]
        if name not in all_functions(tree):
            return f"{path} no longer defines {name}()"
        required = set(fact.get("params", []))
        if required:
            missing = required - parameters_of(tree, name)
            if missing:
                return f"{name}() no longer takes {', '.join(sorted(missing))}"
        return None

    if kind == "function_count":
        name = fact["name"]
        actual = all_functions(tree).count(name)
        expected = fact["count"]
        if actual != expected:
            return f"{path} defines {name}() {actual} time(s), expected {expected}"
        return None

    if kind == "methods":
        class_name = fact["class"]
        found = methods_of(tree, class_name)
        if not found:
            return f"{path} no longer defines class {class_name}"
        missing = set(fact["names"]) - found
        if missing:
            return f"{class_name} no longer has {', '.join(sorted(missing))}"
        return None

    if kind == "contains_all":
        missing = [text for text in fact["text"] if text not in source]
        if missing:
            return f"{path} no longer contains {missing!r}"
        return None

    if kind == "contains_count":
        text = fact["text"]
        actual = source.count(text)
        expected = fact["count"]
        if actual != expected:
            return f"{path} contains {text!r} {actual} time(s), expected {expected}"
        return None

    if kind == "absent":
        # Section 11.1 is as load-bearing as section 11: a name that reappears
        # upstream is a name somebody will reach for.
        present = [text for text in fact["text"] if text in source]
        if present:
            return f"{path} now contains {present!r}, which section 11.1 forbids relying on"
        return None

    return f"unknown check kind {kind!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="tag, branch or SHA; overrides --latest and the manifest")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="check against the newest release instead of verified_against (discovery)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    arguments = parser.parse_args()

    if arguments.ref and arguments.latest:
        fail("--ref and --latest are two different questions; pass one, not both")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        fail(f"cannot read {MANIFEST.name}: {err}")

    for fact in manifest.get("facts", []):
        problem = validate(fact)
        if problem is not None:
            fail(f"{MANIFEST.name} is malformed: {problem}")

    repo = manifest["upstream"]
    # `..` is rejected outright, on top of the shape check below: it is an
    # ordinary character inside a segment, so "owner/.." and "../owner" both
    # match UPSTREAM_RE while still being able to walk the path curl builds
    # from it. `tools/install-on-pi.sh` refuses any `..` in `--repo` for the
    # same reason with its own `*..*` arm, regardless of slash count, and
    # this mirrors it rather than tightening the pattern instead.
    if ".." in repo or not UPSTREAM_RE.match(repo):
        # Same path as "the check itself could not run": a malformed repo is
        # not upstream drifting, so it must not read as one.
        fail(f"{MANIFEST.name} has a malformed upstream: {repo!r}")

    if arguments.ref:
        ref = arguments.ref
        question = "asked"
    elif arguments.latest:
        ref = latest_tag(repo)
        question = "latest"
    else:
        ref = manifest.get("verified_against")
        if not ref:
            # §11.2: the default question is "do the facts still hold where we
            # said they held", and that question has no answer without a
            # recorded answer to check against. A manifest that lost this
            # value is the check failing to run, not a clean result.
            fail(f"{MANIFEST.name} has no verified_against to check the facts against")
        question = "verified"

    files = fetch_tree(repo, ref)

    drifted: list[dict] = []
    for fact in manifest["facts"]:
        try:
            problem = check(fact, files)
        except Exception as err:
            # Exit 1 means "upstream moved" and the workflow files a ticket
            # saying so. A bug in here is not that, and must not be reported
            # as if it were.
            fail(f"checking {fact.get('id', '?')} raised {type(err).__name__}: {err}")
        if problem is not None:
            drifted.append({"id": fact["id"], "problem": problem, "why": fact.get("why", "")})

    if arguments.json:
        print(json.dumps({
            "repo": repo,
            "ref": ref,
            "question": question,
            "checked": len(manifest["facts"]),
            "drifted": drifted,
        }, indent=2))
    else:
        asked = QUESTION_LABELS[question]
        print(f"{repo}@{ref} ({asked}): {len(manifest['facts'])} fact(s) checked")
        for entry in drifted:
            print(f"  {entry['id']}: {entry['problem']}", file=sys.stderr)
            if entry["why"]:
                print(f"      matters because: {entry['why']}", file=sys.stderr)
        if not drifted:
            print("  every pinned symbol still holds")
        else:
            print(
                f"\n{len(drifted)} fact(s) drifted. SPEC.md section 11 is now wrong about them, "
                "and\nthe test stub built from it is wrong in the same way. Re-read the source "
                "before\nchanging any code: the fix may be to widen what the plugin tolerates "
                "rather than\nto follow upstream.",
                file=sys.stderr,
            )

    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
