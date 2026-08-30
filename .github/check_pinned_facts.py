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
import http.client
import io
import json
import re
import sys
import tarfile
import warnings
import urllib.error
import urllib.parse
import urllib.request
import zlib
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
# Neither cap above bounds how many members an archive lists: a tarball of
# many small, highly compressible entries stays under both while `getmembers()`
# still has to materialise every header before the first one is read. This
# bounds the walk itself. It is sized against upstream's whole tree, not the
# manifest - the manifest's path count bounds MAX_TOTAL_BYTES below, and has
# nothing to do with how many members an archive is allowed to list. A real
# pwnagotchi checkout runs to a few thousand files at most, so 100,000 stays
# generously above any legitimate release while remaining cheap to reach
# (headers, not file contents, and most of them skipped before extraction),
# and it is far below what a crafted archive can pack into `MAX_ARCHIVE_BYTES`
# of compressed input.
MAX_MEMBERS = 100_000
# Bounds what fetch_tree keeps in memory across every wanted member combined,
# not just each one alone. The number of paths the manifest names times
# MAX_MEMBER_BYTES is not a safe stand-in for this: the manifest already names
# twenty, so that product is eighty mebibytes and grows every time a fact is
# added, and an archive that serves each of those twenty real paths padded to
# just under the per-member cap compresses to almost nothing, passes the
# download cap, and passes the member count (issue #165). What the manifest
# actually names are ordinary Python, TOML and shell-profile sources from a
# pwnagotchi checkout, none of which run anywhere near the per-member cap
# today: twenty paths at a generous 512 KiB each is 10 mebibytes. Doubling
# that - the manifest growing to about forty paths before this needs
# revisiting - lands at 20 mebibytes: comfortably above any real checkout and
# far short of what padding twenty members up to the per-member cap would
# need to pass. This counts UTF-8 bytes read, not the memory the decoded `str`
# holds them in: PEP 393 gives every character in a string the width its
# widest character needs, so one character outside Latin-1 in an otherwise
# huge ASCII file can make the retained `str` several times the bytes counted
# here - up to four times, for a character outside the Basic Multilingual
# Plane. This bound is on what was read, not a promise about what is held.
# One more piece of arithmetic that has to be done rather than assumed: the
# check below runs after a member has already been read and added to
# `total_bytes`, so the bound catches the *next* member over the line, not
# this one. The real peak this allows is MAX_TOTAL_BYTES plus one more member
# at MAX_MEMBER_BYTES - up to 24 mebibytes, not 20. Checking before the read
# would cut a wanted member short instead, which is the truncation this file
# already refuses to do for the per-member cap above; the overshoot is the
# cheaper wrong to be.
MAX_TOTAL_BYTES = 20 * 1024 * 1024


def fail(message: str) -> NoReturn:
    """Exits 2, which is what separates "the check broke" from "upstream moved".

    The workflow files a ticket on 1 and goes red on anything higher, so a
    network blip exiting 1 would open an issue announcing a drift that never
    happened.
    """
    print(f"check_pinned_facts: {message}", file=sys.stderr)
    raise SystemExit(2)


def fetch_tree(repo: str, ref: str, wanted: set[str]) -> dict[str, str]:
    """Downloads a repository tarball at `ref`, returning path -> text for the
    paths in `wanted`.

    A tarball rather than a git clone: no git in the loop, one request, and
    nothing left on disk to clean up.

    `wanted` is the manifest's own set of paths, not a precaution added on top
    of it: every text member of the whole upstream tree used to be decoded and
    kept, of which the manifest ever asks about a handful, so the memory this
    uses was a function of upstream's size rather than of what this check
    actually reads. Filtering by name as each member is read, rather than
    after, is what keeps an archive of many small members from being decoded
    at all. It does not, on its own, bound what survives: MAX_TOTAL_BYTES
    does that, checked as members are kept rather than derived from `wanted`'s
    size, because that arithmetic was tried and was wrong (see the constant).
    """
    # Quoted: a ref containing ../ would otherwise walk the URL to another
    # repository and quietly answer about the wrong source.
    url = f"https://codeload.github.com/{repo}/tar.gz/{urllib.parse.quote(ref, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as err:
        fail(f"cannot fetch {repo}@{ref}: HTTP {err.code}")
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as err:
        # `http.client.IncompleteRead` (raised for a connection cut short
        # while `response.read()` is still waiting for the promised bytes)
        # is an `HTTPException`, not an `OSError`, so it needs its own name
        # in this tuple rather than falling out of it uncaught.
        fail(f"cannot fetch {repo}@{ref}: {err}")
    if len(payload) > MAX_ARCHIVE_BYTES:
        fail(f"{repo}@{ref} is larger than {MAX_ARCHIVE_BYTES} bytes; refusing to read it")

    files: dict[str, str] = {}
    total_bytes = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz")
    except (tarfile.TarError, EOFError, zlib.error, OSError, http.client.HTTPException) as err:
        # `tarfile.open()` reads the gzip header and the first member's data
        # before it returns, so a truncated stream can raise here rather than
        # from the loop below - the same `EOFError`/`zlib.error` shapes that
        # loop guards against, just before the loop ever runs. This used to
        # catch only `tarfile.TarError`, so a cut landing early enough in the
        # first member's compressed data raised `EOFError` straight out of
        # this constructor, uncaught, and the process exited 1 - the status
        # that means upstream drifted (SPEC.md section 11.3) - for a stream
        # that never finished downloading.
        fail(f"{repo}@{ref} could not be opened as a tarball: {err}")
    try:
        with archive:
            # Iterated, not `archive.getmembers()`. `TarFile.next()` still builds
            # the same index as it goes - each member it reads is appended to
            # `archive.members` either way - but `getmembers()` forces every
            # header to be read and appended before it returns anything, so the
            # bound below could not apply until a hostile archive had already
            # finished building the index it is meant to stop. Iterating lets the
            # bound apply after each member, while that index is still growing.
            for count, member in enumerate(archive, start=1):
                if count > MAX_MEMBERS:
                    fail(
                        f"{repo}@{ref} lists more than {MAX_MEMBERS} members; "
                        "refusing to read it"
                    )
                if not member.isfile():
                    # Kept, not closed (SPEC.md section 11.3): a pinned path
                    # served as a symlink or a directory upstream is arguably
                    # real drift, unlike the decode failure this checker was
                    # fixed to stop fabricating - but it is arguable, which is
                    # why it stays a skip rather than a `fail()`. Do not close
                    # this by making it fail the run, and do not copy it as a
                    # pattern for the next skip.
                    continue
                # Strip the leading `<repo>-<ref>/` component the tarball adds.
                _, _, relative = member.name.partition("/")
                if not relative or relative not in wanted:
                    continue
                # No `handle is None` guard: `TarFile.extractfile()` returns a
                # file object for every member `isfile()` admits (`isfile()` is
                # `isreg()`), so that branch would be dead and uncoverable.
                handle = archive.extractfile(member)
                # Reads one byte past the cap to tell "exactly at the cap" from
                # "over it", the same trick used on the whole download above. A
                # member over the cap is refused, not truncated: `.read(n)` stops
                # at n and hands back what it got, which is a truncated file this
                # checker then asks questions of. A `contains_all` fact whose
                # pinned string sits past the cut would read as upstream no
                # longer containing it, and `upstream-drift.yml` would file an
                # issue saying upstream moved when nothing did.
                raw = handle.read(MAX_MEMBER_BYTES + 1)
                if len(raw) > MAX_MEMBER_BYTES:
                    fail(
                        f"{repo}@{ref}: {relative} is larger than {MAX_MEMBER_BYTES} "
                        "bytes; refusing to read it"
                    )
                # A member the manifest named by path but that does not decode
                # as UTF-8 is a third kind of failure, not a fourth `continue`:
                # skipping it would leave `relative` absent from `files`, and
                # the reporter reads that as upstream no longer containing the
                # path (SPEC.md section 11.3) - exactly the fabricated drift
                # this checker exists to rule out, arriving through a name the
                # manifest itself asked for.
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError as err:
                    fail(f"{repo}@{ref}: {relative} is not valid UTF-8: {err}")
                total_bytes += len(raw)
                if total_bytes > MAX_TOTAL_BYTES:
                    fail(
                        f"{repo}@{ref} keeps more than {MAX_TOTAL_BYTES} bytes across "
                        "the paths the manifest names; refusing to read it"
                    )
                files[relative] = text
    except (tarfile.TarError, EOFError, zlib.error, OSError, http.client.HTTPException) as err:
        # A truncated or corrupt gzip stream raises `EOFError` or a `zlib.error`
        # while members are still being iterated, and a connection cut short
        # partway through extraction can surface as `http.client.IncompleteRead`
        # here too, not only on the download above - none of these is an
        # `OSError`, so the exceptions on the download's `except` above do not
        # see them, and this loop used to sit outside every `try`: the process
        # died with the interpreter's own status, 1, and `upstream-drift.yml`
        # reads exactly that as upstream having moved. Exit 1 means upstream
        # moved and nothing else may produce it (SPEC.md section 11.3), so the
        # iteration, the extraction and the read all fail the run through
        # `fail()` (exit 2) rather than reporting a fact.
        fail(f"{repo}@{ref}: archive ended unexpectedly while reading it: {err}")
    return files


def latest_tag(repo: str) -> str:
    """Returns the tag name of the newest release, type-checked before the
    caller interpolates it into the fetch URL below.

    `tag_name` is the one value read from the network that reaches URL
    construction: `urllib.parse.quote` raises `TypeError` for a non-string,
    uncaught, which exits 1 - the status that means upstream drifted, for a
    response GitHub's own API is not expected to send. Unlikely against the
    real API and beside the point: every value is type-checked before use,
    and this is a value.
    """
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            tag = json.load(response)["tag_name"]
    except Exception as err:
        fail(f"cannot resolve the latest release: {err}")
    if not isinstance(tag, str):
        fail(f"the latest release for {repo} has a non-string tag_name: {tag!r}")
    return tag


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
# Keys a kind accepts beyond REQUIRED_KEYS, without needing them: `function`'s
# `params` narrows which parameters are checked and is meaningless without a
# name to check them on, so it stays optional rather than joining the
# required set.
OPTIONAL_KEYS = {
    "function": ("params",),
}
# Present on every fact regardless of kind. `why` belongs here as of #150: the
# `absent` branch used to carry a reason of its own and was cut back to
# reporting only what it observed, on the strength of `main()` already
# printing the fact's `why` underneath. That is only true if every fact has
# one - a fact with no `why` would report a drift with no reason at all,
# which is worse than the wrong reason it replaced.
FACT_REQUIRED_KEYS = ("id", "file", "kind", "why")


def validate(manifest: dict) -> str | None:
    """Rejects a manifest that would pass without checking anything, or that
    would let a later step read a value of a type it never guarded against.

    The shape that matters for a fact: `"text": "foo"` where a list was meant
    iterates the characters of the string, and every one of them is in any
    Python file, so the fact passes vacuously for ever. A checker that cannot
    fail is worse than no checker, so a malformed manifest is an error rather
    than a green run.

    This is also where every manifest value gets type-checked before use, not
    only the shape of `facts` (SPEC.md section 11.3, issue #167): a value used
    unguarded elsewhere - `".." in repo`, a regex match, `urllib.parse.quote`,
    `wanted = {fact["file"] ...}` - raises whatever exception Python happens
    to raise for the type it got, and an uncaught exception exits 1, the
    status that means upstream drifted. A manifest that is merely broken must
    not read as that. The first version of this function checked four values
    and left `file`, `id`, `count`, `name` and `class` to whatever guard
    happened to be nearest their point of use, and there was none: a `file`
    that is a list is unhashable and raises building the wanted set, a fact
    with no `id` raises in the reporter, and a `count` that is a string
    compares unequal to an integer for ever and reports a drift that never
    clears. Every key any branch reaches is checked here instead - including
    `params`, the one optional field: `fact.get("params", [])` guarded nothing,
    so `params: ""` made `if required:` false and skipped the signature check
    silently (exit 0), and `params: "celsius"` iterated characters and reported
    a fabricated drift that the function no longer takes `c`, `e`, `i`, `l`,
    `s`, `u` (exit 1). And a key on a fact that is neither required nor
    optional for its kind is refused rather than ignored - the same argument
    §2.2.1 makes about an unknown configuration key, stronger here because
    nobody reads this file except a script: a misspelt `param:` disables the
    same check `params: ""` did, silently, and leaves nothing behind to notice.

    Two guards come before any of that. The manifest itself must be an
    object: a top-level list, string or number reaches `manifest.get()` and
    raises `AttributeError`, uncaught, which is the same exit-1-for-broken
    misreport this function exists to stop, one level up. And `facts` must be
    present, a list, and non-empty: it used to be read as `manifest["facts"]`,
    which raised for a manifest that lost the key and turned the job red.
    Routing that read through a validated local with a default (`.get("facts",
    [])`) turned a lost or emptied `facts` into a clean run reporting zero
    facts checked - a scan that did not run, reporting as if it had, on the
    required check every pull request is judged by. This is written first
    because it is the one way this function could make the check worse than
    it found it.
    """
    if not isinstance(manifest, dict):
        return f"the manifest must be an object, got {type(manifest).__name__}"

    facts = manifest.get("facts")
    if not isinstance(facts, list):
        return f"'facts' must be a list, got {type(facts).__name__}"
    if not facts:
        return "'facts' must not be empty"
    for fact in facts:
        if not isinstance(fact, dict):
            return f"a fact must be an object, got {type(fact).__name__}"
        for key in FACT_REQUIRED_KEYS:
            if key not in fact:
                return f"a fact is missing {key!r}"

        identifier = fact["id"]
        # Type-checked and non-empty: `id: ""` is well-typed and still makes a
        # drift line that identifies nothing, and the run still exits 1, the
        # status that means upstream moved (SPEC.md section 11.3).
        if not isinstance(identifier, str) or not identifier:
            return f"a fact's 'id' must be a non-empty string, got {identifier!r}"
        if not isinstance(fact["file"], str) or not fact["file"]:
            # `file: ""` is not vacuous the way an empty `text` list is - it
            # makes `check()` permanently false (`files.get("")` is never a
            # real upstream member), so the run exits 1 reporting " no longer
            # exists upstream", the same fabricated drift a missing value
            # would cause.
            return f"{identifier}: 'file' must be a non-empty string, got {fact['file']!r}"
        if not isinstance(fact["why"], str) or not fact["why"]:
            # Empty is as vacuous as absent: `check()` never sets its own reason,
            # it only ever reports the fact's own `why` (see the `absent` branch
            # and `main()`'s reporter), so an empty one would print a drift with
            # no reason at all - worse than the fixed reason it replaced.
            return f"{identifier}: 'why' must be a non-empty string"

        kind = fact["kind"]
        if not isinstance(kind, str):
            return f"{identifier}: 'kind' must be a string, got {type(kind).__name__}"
        if kind not in REQUIRED_KEYS:
            return f"{identifier}: unknown kind {kind!r}"
        for key in REQUIRED_KEYS[kind]:
            if key not in fact:
                return f"{identifier}: kind {kind} needs {key!r}"

        # A key neither required nor optional for this kind is refused, not
        # ignored (SPEC.md section 11.3, section 2.2.1's argument applied
        # here): a misspelt key disables whatever check it was meant to
        # narrow and leaves nothing behind to notice, same as `params: ""`
        # did before it was type-checked below.
        allowed = (
            set(FACT_REQUIRED_KEYS)
            | set(REQUIRED_KEYS[kind])
            | set(OPTIONAL_KEYS.get(kind, ()))
        )
        unknown = set(fact) - allowed
        if unknown:
            return f"{identifier}: unknown key(s) {sorted(unknown)!r}"

        if kind in TEXT_KINDS:
            # `text: []` checks nothing - `contains_all` and `absent` both loop
            # over it, and a loop over nothing finds nothing missing and nothing
            # present, which is the check passing by asking no question at all.
            # `text: [""]` is the same failure with a non-empty list: every
            # source contains the empty string, so `contains_all` always holds
            # and `absent` never does. Both are excluded here, not just the
            # list-of-strings shape.
            text = fact["text"]
            if (
                not isinstance(text, list)
                or not text
                or not all(isinstance(item, str) and item for item in text)
            ):
                return (
                    f"{identifier}: 'text' must be a non-empty list of non-empty "
                    f"strings for kind {kind}"
                )
        if kind == "contains_count" and (
            not isinstance(fact["text"], str) or not fact["text"]
        ):
            # An empty string is a substring of everything, so `source.count("")`
            # is `len(source) + 1` rather than testing for anything - not a
            # check that always passes, but not a check of the fact's claim
            # either, so it is refused for the same reason as the two kinds above.
            return f"{identifier}: 'text' must be a non-empty string for kind {kind}"
        # `bool` subclasses `int`, so `isinstance(True, int)` is true and
        # `"count": true` would otherwise validate and then compare as 1.
        if kind in {"function_count", "contains_count"} and (
            not isinstance(fact["count"], int) or isinstance(fact["count"], bool)
        ):
            return f"{identifier}: 'count' must be an integer"
        if kind == "methods":
            names = fact["names"]
            # `names: []` makes `set(fact["names"]) - found` empty regardless of
            # what `found` is, the same vacuous-loop failure as `text: []` above.
            if (
                not isinstance(names, list)
                or not names
                or not all(isinstance(item, str) and item for item in names)
            ):
                return f"{identifier}: 'names' must be a non-empty list of non-empty strings"
            if not isinstance(fact["class"], str) or not fact["class"]:
                # `class: ""` never matches an `ast.ClassDef.name`, so `check()`
                # reports "m.py no longer defines class " for ever - the same
                # permanently-false shape as an empty `file`, not a vacuous pass.
                return f"{identifier}: 'class' must be a non-empty string"
        if kind in {"function", "function_count"} and (
            not isinstance(fact["name"], str) or not fact["name"]
        ):
            # `name: ""` never matches a real function name either, and reports
            # "m.py no longer defines ()" - fabricated drift, not a vacuous check.
            return f"{identifier}: 'name' must be a non-empty string"
        if kind == "function" and "params" in fact:
            params = fact["params"]
            # `params: []` reaches `check()`'s `if required:` as an empty set,
            # which is falsy, so the signature check is skipped exactly as it
            # was for `params: ""` before that string was rejected below -
            # the same vacuous-empty-collection failure, one type over.
            # `params` is optional: a fact with nothing to narrow should omit
            # the key rather than carry an empty one that disables the check.
            if (
                not isinstance(params, list)
                or not params
                or not all(isinstance(item, str) and item for item in params)
            ):
                return f"{identifier}: 'params' must be a non-empty list of non-empty strings"

    upstream = manifest.get("upstream")
    if not isinstance(upstream, str):
        return f"'upstream' must be a string, got {type(upstream).__name__}"
    # `..` is rejected outright, on top of the shape check below: it is an
    # ordinary character inside a segment, so "owner/.." and "../owner" both
    # match UPSTREAM_RE while still being able to walk the path curl builds
    # from it. `tools/install-on-pi.sh` refuses any `..` in `--repo` for the
    # same reason with its own `*..*` arm, regardless of slash count, and
    # this mirrors it rather than tightening the pattern instead.
    if ".." in upstream or not UPSTREAM_RE.match(upstream):
        return f"'upstream' must look like owner/name, got {upstream!r}"

    verified_against = manifest.get("verified_against")
    if verified_against is not None and not isinstance(verified_against, str):
        return f"'verified_against' must be a string, got {type(verified_against).__name__}"

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
        # None of the entries using this kind is a section 11.1 guard; every
        # one pins a positive claim about upstream's shape (SPEC.md section
        # 11.3, issue #150). The branch does not know which fact it is
        # checking, so it says only what it observed - why the absence
        # matters is the fact's own `why`, already reported alongside this
        # message rather than folded into it.
        present = [text for text in fact["text"] if text in source]
        if present:
            return f"{path} now contains {present!r}"
        return None

    # `validate()` already restricts `kind` to the keys of REQUIRED_KEYS, and
    # every one of them has a branch above, so this can only be reached by a
    # kind added to REQUIRED_KEYS without a matching branch here - a bug in
    # this file, not a fact about upstream. Returning a string here used to
    # read as a normal drift (exit 1, "upstream moved"), which is the wrong
    # answer for a mistake made in this script rather than upstream; raising
    # instead lets the `except Exception` in main() route it through fail()
    # (exit 2), the same path a malformed manifest takes.
    raise AssertionError(f"unhandled check kind {kind!r}; validate() should have rejected this")


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

    if arguments.ref is not None:
        # Truthiness would let `--ref ""` slip past both this check and the
        # `elif arguments.latest` below silently landing on the *default*
        # question (verified_against) instead of failing loudly on a ref that
        # names nothing - the same emptied-value pattern as `text: []`, just on
        # a CLI argument instead of a manifest field.
        if not arguments.ref:
            fail("--ref must not be empty")
        if arguments.latest:
            fail("--ref and --latest are two different questions; pass one, not both")

    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
        # `UnicodeDecodeError` is a `ValueError`, not a `JSONDecodeError`: a
        # manifest with one invalid UTF-8 byte raised uncaught here, exiting
        # 1 - the status that means upstream drifted - for a file this repo
        # cannot even read.
        fail(f"cannot read {MANIFEST.name}: {err}")

    problem = validate(manifest)
    if problem is not None:
        # Same path as "the check itself could not run": a malformed manifest
        # is not upstream drifting, so it must not read as one.
        fail(f"{MANIFEST.name} is malformed: {problem}")

    # validate() has already required `facts` to be a non-empty list, so a
    # default here would be a fallback nothing can reach - the kind of thing
    # this section argues against everywhere else.
    facts = manifest["facts"]
    repo = manifest["upstream"]

    if arguments.ref is not None:
        # Emptiness was already rejected above; this only distinguishes "given"
        # from "absent", which `is not None` does and truthiness does not.
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

    wanted = {fact["file"] for fact in facts}
    files = fetch_tree(repo, ref, wanted)

    drifted: list[dict] = []
    for fact in facts:
        try:
            problem = check(fact, files)
        except Exception as err:
            # Exit 1 means "upstream moved" and the workflow files a ticket
            # saying so. A bug in here is not that, and must not be reported
            # as if it were.
            fail(f"checking {fact['id']} raised {type(err).__name__}: {err}")
        if problem is not None:
            # `why` is required and type-checked by validate(), which has
            # already run by this point, so this is not a second fallback -
            # it is reading the guarantee validate() gave.
            drifted.append({"id": fact["id"], "problem": problem, "why": fact["why"]})

    if arguments.json:
        print(json.dumps({
            "repo": repo,
            "ref": ref,
            "question": question,
            "checked": len(facts),
            "drifted": drifted,
        }, indent=2))
    else:
        asked = QUESTION_LABELS[question]
        print(f"{repo}@{ref} ({asked}): {len(facts)} fact(s) checked")
        for entry in drifted:
            print(f"  {entry['id']}: {entry['problem']}", file=sys.stderr)
            # `validate()` now rejects an empty or missing `why`, so this can
            # no longer be false; the guard that used to be here belonged to
            # a `.get("why", "")` fallback that main() no longer has.
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
