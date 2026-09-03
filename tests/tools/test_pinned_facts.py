"""`.github/check_pinned_facts.py`, the tool that watches upstream for drift.

This one needs tests more than most, and for an unusual reason: its failure mode
is silence. A checker with a bug that makes every fact pass reports "every
pinned symbol still holds" for ever, which is indistinguishable from working,
and the thing it was built to catch goes on being missed. So the cases below are
mostly about making it *fail* - a checker that has only ever said yes is not
known to work.

No network. `check()` takes the tree as a dict, so the whole checking layer is
driven with synthetic sources; the two functions that do reach out are covered
only for their argument handling.
"""

from __future__ import annotations

import collections
import http.client
import importlib.util
import io
import json
import re
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "check_pinned_facts.py"
MANIFEST = REPO_ROOT / ".github" / "pinned_symbols.json"


def load_module():
    """Imports the checker by path: `.github` is not an importable package."""
    spec = importlib.util.spec_from_file_location("check_pinned_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cpf = load_module()


def fact_number(entry_id: str) -> str:
    """`F28b` and `F9a` both belong to the fact `F28` and `F9`.

    One fact often needs several manifest entries, because an entry checks one
    file and a fact can rest on three. The suffix is how they are told apart.

    This used to be `id.rstrip("abc")`, which quietly capped a fact at three
    entries: `F28d` came back as `F28d`, matched nothing in SPEC, and the
    cross-check below failed. A fact that needs a fourth file to be verifiable
    would have had to go unverified, which is the opposite of what these two
    tests exist to enforce.
    """
    match = re.match(r"(F\d+)", entry_id)
    assert match is not None, f"manifest entry id {entry_id!r} does not start with an F-number"
    return match.group(1)


SOURCE = '''
import os

CONSTANT = "/usr/local/share/pwnagotchi/custom-plugins/"


def uptime():
    return 1


def temperature(celsius=True):
    return 40


def duplicated():
    return "first"


def duplicated():
    return "second"


class Peer:
    def __init__(self):
        self.last_channel = 1
        self.rssi = -50

    def full_name(self):
        return "a"

    def pwnd_total(self):
        return 0
'''

TREE = {"mod.py": SOURCE, "conf.toml": 'key = "value"\n# commented = "no"\n'}


# ---------------------------------------------------------------------------
# Each kind finds what is there, and misses what is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fact",
    [
        {"id": "a", "file": "mod.py", "kind": "function", "name": "uptime"},
        {"id": "b", "file": "mod.py", "kind": "function", "name": "temperature",
         "params": ["celsius"]},
        {"id": "c", "file": "mod.py", "kind": "function_count", "name": "duplicated",
         "count": 2},
        {"id": "d", "file": "mod.py", "kind": "methods", "class": "Peer",
         "names": ["full_name", "pwnd_total"]},
        {"id": "e", "file": "mod.py", "kind": "contains_all",
         "text": ["last_channel", "rssi"]},
        {"id": "f", "file": "mod.py", "kind": "contains_count", "text": "def duplicated",
         "count": 2},
        {"id": "g", "file": "mod.py", "kind": "absent", "text": ["pwnd_tot("]},
    ],
    ids=["function", "params", "function_count", "methods", "contains_all",
         "contains_count", "absent"],
)
def test_a_fact_that_holds_reports_nothing(fact):
    assert cpf.check(fact, TREE) is None


@pytest.mark.parametrize(
    "fact,expected",
    [
        ({"id": "a", "file": "mod.py", "kind": "function", "name": "renamed"},
         "no longer defines renamed()"),
        ({"id": "b", "file": "mod.py", "kind": "function", "name": "temperature",
          "params": ["fahrenheit"]}, "no longer takes fahrenheit"),
        ({"id": "c", "file": "mod.py", "kind": "function_count", "name": "duplicated",
          "count": 1}, "2 time(s), expected 1"),
        ({"id": "d", "file": "mod.py", "kind": "methods", "class": "Peer",
          "names": ["pwnd_tot"]}, "no longer has pwnd_tot"),
        ({"id": "e", "file": "mod.py", "kind": "methods", "class": "Gone",
          "names": ["x"]}, "no longer defines class Gone"),
        ({"id": "f", "file": "mod.py", "kind": "contains_all", "text": ["absent_marker"]},
         "no longer contains"),
        ({"id": "g", "file": "mod.py", "kind": "contains_count", "text": "def duplicated",
          "count": 3}, "expected 3"),
        ({"id": "i", "file": "gone.py", "kind": "contains_all", "text": ["x"]},
         "no longer exists upstream"),
    ],
    ids=["renamed", "signature", "duplicate-cleaned-up", "forbidden-method",
         "class-gone", "text-gone", "wrong-count", "file-gone"],
)
def test_a_fact_that_no_longer_holds_is_reported(fact, expected):
    """The half that matters. Each of these is a drift that reached a device once.

    `absent` used to live in this list too, checked with `in` like the rest.
    Pulled out into `test_check_reports_only_the_observation_for_an_absent_fact`
    below with an exact-equality assertion instead: the pre-#150 message was
    "mod.py now contains ['Peer'], which section 11.1 forbids relying on",
    and "now contains" - the substring this row used to assert - is true of
    that sentence as well as of the fixed one, so it passed unchanged against
    the defect it was meant to catch. Nothing else in this list was ever
    touched by #150's branch, so `in` is left as it was for them.
    """
    problem = cpf.check(fact, TREE)
    assert problem is not None, "drift went unreported"
    assert expected in problem, problem


def test_check_reports_only_the_observation_for_an_absent_fact():
    """SPEC.md 11.3 / issue #150: "the `absent` branch says what it observed

    and stops, which is what every other kind already did." Asserted with
    `==`, not `in`: the pre-fix message, "mod.py now contains ['Peer'],
    which section 11.1 forbids relying on", contains this test's expected
    text as a substring too, so only exact equality tells the fixed branch
    apart from the one it replaced.
    """
    fact = {"id": "h", "file": "mod.py", "kind": "absent", "text": ["Peer"]}

    problem = cpf.check(fact, TREE)

    assert problem == "mod.py now contains ['Peer']"


def test_a_method_is_found_wherever_the_class_is_defined():
    """`methods_of` walks rather than scanning the module body.

    A class nested inside a function or a conditional is still the class, and
    reporting it missing would be a false drift report - which costs trust in
    every future report from this tool.
    """
    nested = {
        "mod.py": "if True:\n    class Peer:\n        def full_name(self):\n            pass\n"
    }
    fact = {"id": "x", "file": "mod.py", "kind": "methods", "class": "Peer",
            "names": ["full_name"]}

    assert cpf.check(fact, TREE | nested) is None


def test_a_file_that_stopped_parsing_is_reported_rather_than_passed():
    broken = {"mod.py": "def oops(:\n"}
    fact = {"id": "x", "file": "mod.py", "kind": "function", "name": "oops"}

    problem = cpf.check(fact, broken)

    assert problem is not None and "does not parse" in problem


# ---------------------------------------------------------------------------
# The manifest cannot be malformed in a way that passes
#
# `validate()` takes the whole manifest, not one fact at a time (issue #167
# moved the `upstream` type check here, next to the shape check `facts`
# already had, rather than leaving it to whichever guard was nearest its
# point of use). Every case below wraps its fact of interest in an otherwise
# well-formed manifest via `manifest_with()`, so a failure can only be coming
# from the fact-shape check under test - not, vacuously, from the wrapper
# being malformed too. `test_a_malformed_entry_is_rejected` in particular
# would pass for the wrong reason without this: any manifest missing a valid
# `upstream` already returns non-None on its own, which would make "the fact
# is malformed" and "the wrapper is malformed" indistinguishable.
# ---------------------------------------------------------------------------


def manifest_with(facts: list[dict]) -> dict:
    return {"upstream": "example/upstream", "verified_against": "v1.0.0", "facts": facts}


def test_a_string_where_a_list_was_meant_is_rejected():
    """The vacuous pass this validation exists for.

    `"text": "requires-python"` iterates the characters of the string, and every
    one of them appears in any file, so the fact passes for ever while checking
    nothing. It has to be an error, not a green run.
    """
    fact = {"id": "x", "file": "a.py", "kind": "contains_all", "text": "requires-python",
            "why": "w"}

    problem = cpf.validate(manifest_with([fact]))

    assert problem is not None and "must be a" in problem and "list" in problem


@pytest.mark.parametrize(
    "fact",
    [
        {"id": "x", "file": "a.py", "kind": "nonsense", "why": "w"},
        {"id": "x", "kind": "contains_all", "text": ["a"], "why": "w"},
        {"id": "x", "file": "a.py", "kind": "function", "why": "w"},
        {"id": "x", "file": "a.py", "kind": "function_count", "name": "a", "why": "w"},
        {"id": "x", "file": "a.py", "kind": "methods", "class": "A", "names": "full_name",
         "why": "w"},
        {"id": "x", "file": "a.py", "kind": "contains_count", "text": ["a"], "count": 1,
         "why": "w"},
    ],
    ids=["unknown-kind", "no-file", "no-name", "no-count", "names-not-a-list",
         "count-text-not-a-string"],
)
def test_a_malformed_entry_is_rejected(fact):
    """Every fixture carries `why`, deliberately: `validate()` now requires it

    on every fact (`why` became mandatory alongside #150's fix, per SPEC.md
    11.3), and it is checked ahead of most of these kind-specific shapes.
    Without it, five of these six rows were actually being caught by "a fact
    is missing 'why'" and never exercising the defect their id names -
    "unknown-kind" passing for a reason that has nothing to do with the kind
    being unknown is exactly the kind of assertion that observes nothing.
    """
    assert cpf.validate(manifest_with([fact])) is not None


def test_a_well_formed_fact_in_an_otherwise_well_formed_manifest_passes():
    """The contrast case `manifest_with()` itself depends on: the wrapper this

    file adds around every fact-shape case above must not be malformed on its
    own, or those cases would all be passing vacuously for the wrapper's
    reason instead of the fact's.
    """
    fact = {"id": "x", "file": "a.py", "kind": "contains_all", "text": ["a"], "why": "w"}

    assert cpf.validate(manifest_with([fact])) is None


def test_every_entry_in_the_real_manifest_is_well_formed():
    """The validator is worth nothing if the file it guards was never run past it."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert cpf.validate(manifest) is None


def test_the_manifest_covers_every_fact_section_11_pins():
    """The two allowlists must not drift apart, which is this tool's own failure mode.

    SPEC section 11 is the human record and this manifest is the machine-checked
    subset. Someone editing section 11 has no reason to know a second copy
    exists, so the link is asserted here rather than hoped for.

    F12 and F29 live in another repository, and F22 and F34 describe the
    project this forks, none of which can be checked against upstream; the
    manifest's own note says so, and this test pins that list rather than
    letting it grow quietly.
    """
    not_checkable = {"F12", "F22", "F29", "F34"}
    spec = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    pinned = set(re.findall(r"^\| (F\d+) \|", spec, re.M))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    covered = {fact_number(fact["id"]) for fact in manifest["facts"]}

    missing = pinned - covered - not_checkable

    assert not missing, (
        f"SPEC section 11 pins {sorted(missing)} with no entry in pinned_symbols.json; "
        "add one, or add it to the not-checkable list here and say why in the manifest note"
    )


def test_the_manifest_invents_no_facts():
    """The reverse direction: an entry with no fact behind it in SPEC."""
    spec = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    pinned = set(re.findall(r"^\| (F\d+) \|", spec, re.M))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    invented = {fact_number(fact["id"]) for fact in manifest["facts"]} - pinned

    assert not invented, f"pinned_symbols.json checks {sorted(invented)}, not in SPEC section 11"


def spec_enumerations(spec: str) -> dict[str, set[str]]:
    """Every "F<n>... pin ..." sentence in SPEC.md, keyed by fact number.

    Two notations are accepted, because the manifest's own id scheme is not uniform:
    a contiguous letter range like "F28a-d pin ..." (F28's four entries are lettered
    a through d), and an explicit comma list like "F6, F6b pin ..." (F6's first entry
    keeps the bare fact id, with only the second one lettered). Renaming ids to force
    one notation would touch the manifest's facts, which is out of scope here, so the
    parser accepts both rather than the SPEC sentence quietly lying about what exists.

    Two things this does not do, deliberately left as a comment rather than closed,
    because neither weakens what the test below detects: it scans the whole of
    SPEC.md rather than the fact's own row, so a sentence counts wherever it sits in
    the document; and if a fact somehow ended up with two sentences (one of each
    notation, or two of the same), the second write to `enumerated[number]` silently
    wins over the first rather than being flagged as a conflict. Both would need a
    row-scoped parse to close, which the current regexes do not attempt.
    """
    enumerated: dict[str, set[str]] = {}

    for digits, start, end in re.findall(r"\bF(\d+)([a-z])-([a-z]) pin\b", spec):
        enumerated[f"F{digits}"] = {f"F{digits}{chr(c)}" for c in range(ord(start), ord(end) + 1)}

    for group in re.findall(r"\b((?:F\d+[a-z]?)(?:, F\d+[a-z]?)+) pin\b", spec):
        listed = group.split(", ")
        enumerated[fact_number(listed[0])] = set(listed)

    return enumerated


def test_every_multi_entry_fact_has_a_spec_sentence_naming_its_entries():
    """Entry-level granularity, for every fact that needs more than one manifest entry.

    The two tests above work at fact-number granularity: fact_number() folds F28a-d back
    to F28, so a fact with several entries reads as "covered" as long as at least one of
    them survives. That is the gap issue #63 describes - deleting F28d, the entry that
    asserts pwnagotchi/ui/display.py declares no get of its own, leaves F28 "covered" by
    its three siblings and every test above green, even though the absence those siblings
    depend on is no longer checked.

    Rather than hand this test a second, separately-maintained count, it makes the
    enumeration a rule instead of a coincidence: any fact with more than one manifest
    entry must be named, in full, by a "F<n>... pin ..." sentence in SPEC.md - the same
    phrasing F28 already used before this change, generalised to a comma list where the
    ids are not a clean letter range. A fact that gains or loses an entry without the
    sentence changing to match is exactly what this catches, for every such fact, not
    only F28.
    """
    spec = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ids = [fact["id"] for fact in manifest["facts"]]

    by_number = collections.defaultdict(set)
    for entry_id in ids:
        by_number[fact_number(entry_id)].add(entry_id)
    multi_entry = {number: entries for number, entries in by_number.items() if len(entries) > 1}

    enumerated = spec_enumerations(spec)

    unsentenced = sorted(number for number in multi_entry if number not in enumerated)
    assert not unsentenced, (
        f"facts {unsentenced} have more than one entry in pinned_symbols.json but no "
        "'F<n>... pin ...' sentence in SPEC.md naming them; add one to the fact's row, "
        "listing exactly the entries that cover it, e.g. "
        f"'{unsentenced[0]}, {unsentenced[0]}b pin ...'"
    )

    # Checked against every sentenced fact, not just multi_entry: a fact whose bare,
    # unlettered id is the one that survives a deletion (F6, F16, F23 all keep it) would
    # otherwise drop back to a single entry and quietly leave multi_entry, escaping the
    # check that is supposed to catch exactly that deletion.
    for number, sentenced in enumerated.items():
        entries = by_number.get(number, set())
        missing = sentenced - entries
        extra = entries - sentenced
        assert not missing and not extra, (
            f"SPEC.md's sentence for {number} names {sorted(sentenced)} but "
            f"pinned_symbols.json actually has {sorted(entries)} for it; keep them in sync"
        )


# ---------------------------------------------------------------------------
# The two questions (SPEC.md section 11.2): default, --latest, --ref, and the
# manifest defect that must not be mistaken for drift.
#
# No network: `fetch_tree` and `latest_tag` are the only two functions that
# reach out, and both are replaced with recording fakes before `main()` runs.
# A fake that raises when called stands in wherever a mode must not reach the
# network path it is not supposed to take.
# ---------------------------------------------------------------------------


def write_manifest(tmp_path: Path, **overrides) -> Path:
    """A minimal manifest with every key `main()` reads unconditionally.

    `facts` defaults to one well-formed entry, not empty: SPEC.md 11.3 (the
    bullet placed first, found by the security audit) makes `facts: []`
    itself malformed, so an empty default here would make every caller of
    this helper that does not override `facts` collide with that guard
    rather than exercise whatever it actually set out to test - which is
    exactly what happened to the fourteen tests in this file that fetch a
    tag, ask about `--latest`/`--ref`, or check the report's wording, none
    of which are about `facts` at all. Overriding `facts=[]` or any other
    shape still works exactly as before; only the unset case changed.
    """
    manifest = {
        "upstream": "example/upstream",
        "verified_against": "v1.0.0",
        "facts": [well_formed_fact()],
    }
    manifest.update(overrides)
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def install_manifest(monkeypatch, tmp_path: Path, **overrides) -> Path:
    path = write_manifest(tmp_path, **overrides)
    monkeypatch.setattr(cpf, "MANIFEST", path)
    return path


def recording_fetch_tree(monkeypatch, tree: dict | None = None) -> list[tuple[str, str]]:
    """Records which `(repo, ref)` `main()` fetched - not `wanted` too, on purpose.

    `fetch_tree` gained a third parameter, `wanted` (issue #165): `main()` now
    passes the set of paths the manifest names, so a filter-as-you-read
    archive reader can discard everything else without ever decoding it. A
    fake ignorant of that parameter breaks the moment `main()` calls it -
    every caller of this helper hit exactly that `TypeError` after the
    signature changed - so it has to accept the argument.

    It still only *records* `(repo, ref)`, in a 2-tuple, matching every
    existing caller's `calls == [(repo, ref)]` assertion unchanged: none of
    them are about which paths were asked for, only about which tag got
    fetched under `--latest`/`--ref`/the default, which is what this helper
    was built to pin and is the only thing that changed underneath it.

    The default `tree`, when the caller does not supply one, satisfies
    `write_manifest()`'s own default fact (`file="mod.py"`, `text=["x"]`)
    rather than being empty: SPEC.md 11.3 made `facts: []` itself malformed,
    so `write_manifest()` now defaults to one real fact, and an empty tree
    here would make that fact drift and turn every caller that does not care
    about drift status into an unexpected `1` instead of the `0` it expects.
    A caller that does care passes its own `tree` and is unaffected.
    """
    calls: list[tuple[str, str]] = []

    def fake(repo: str, ref: str, wanted: set[str]) -> dict[str, str]:
        calls.append((repo, ref))
        return tree if tree is not None else {"mod.py": "x"}

    monkeypatch.setattr(cpf, "fetch_tree", fake)
    return calls


def forbid(monkeypatch, name: str) -> None:
    """A network function that must not be called on this path.

    A regression that reaches it fails the test at the call site, with a
    clear reason, rather than surfacing later as a network error or a wrong
    tag silently accepted.
    """

    def fake(*args, **kwargs):
        raise AssertionError(f"{name} must not be called on this path")

    monkeypatch.setattr(cpf, name, fake)


def run_main(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["check_pinned_facts.py", *argv])
    return cpf.main()


def test_default_mode_fetches_the_tag_named_by_verified_against(monkeypatch, tmp_path):
    """The load-bearing case (issue #151): before this change the default mode

    ignored `verified_against` outright and always checked the latest release,
    so a test only watching the exit status would have passed against that
    code unchanged. Pinning the exact tag `fetch_tree` receives is what makes
    a fact edited without re-verifying actually fail the next run.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="v1.0.0")
    forbid(monkeypatch, "latest_tag")
    calls = recording_fetch_tree(monkeypatch)

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example/upstream", "v1.0.0")]


def test_latest_flag_fetches_the_newest_release_not_the_manifest_tag(monkeypatch, tmp_path):
    install_manifest(monkeypatch, tmp_path, verified_against="v1.0.0")
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: "v9.9.9")
    calls = recording_fetch_tree(monkeypatch)

    status = run_main(monkeypatch, ["--latest"])

    assert status == 0
    assert calls == [("example/upstream", "v9.9.9")]


def test_ref_flag_overrides_the_manifest_tag(monkeypatch, tmp_path):
    install_manifest(monkeypatch, tmp_path, verified_against="v1.0.0")
    forbid(monkeypatch, "latest_tag")
    calls = recording_fetch_tree(monkeypatch)

    status = run_main(monkeypatch, ["--ref", "candidate-branch"])

    assert status == 0
    assert calls == [("example/upstream", "candidate-branch")]


def test_ref_and_latest_together_is_a_usage_error_not_a_precedence_rule(monkeypatch, tmp_path):
    """Neither wins silently: the script must refuse to guess which question

    was meant, and must not reach the network to find out.
    """
    install_manifest(monkeypatch, tmp_path)
    forbid(monkeypatch, "latest_tag")
    forbid(monkeypatch, "fetch_tree")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, ["--ref", "v1.0.0", "--latest"])

    assert excinfo.value.code == 2


def test_an_empty_ref_does_not_silently_fall_back_to_the_manifest_tag(monkeypatch, tmp_path):
    """SPEC.md 11.3: an empty `--ref` names no tag, so it must not be treated

    as "no `--ref` was given" - the shape a truthiness check (`if args.ref:`)
    would produce, since `""` is falsy exactly like `None`. Both network
    functions are forbidden: a checker that fell through to the default
    would call `fetch_tree` with `verified_against`'s tag and exit 0 with
    nothing distinguishing this run from `run_main(monkeypatch, [])` above,
    which is precisely the indistinguishable-from-not-passing-the-flag shape
    the spec text warns about. `code == 2`, not merely `> 1`/`!= 1`: SPEC.md
    11.3 now states this is the same usage error as `--ref` and `--latest`
    together, exiting the same way, so the exact code is pinned rather than
    only the weaker "the check refused to run" idiom used elsewhere in this
    file for a malformed manifest.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="v1.0.0")
    forbid(monkeypatch, "latest_tag")
    forbid(monkeypatch, "fetch_tree")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, ["--ref", ""])

    assert excinfo.value.code == 2


def test_ref_empty_and_latest_together_is_still_a_usage_error(monkeypatch, tmp_path):
    """The interaction the spec text calls out by name: `--ref ""` is still

    "`--ref` was given" for the purpose of the mutual-exclusion check, so
    pairing it with `--latest` must land on the exact same usage error as
    `--ref v1.0.0 --latest` above (`code == 2`, not merely `> 1`) - not a
    weaker or different failure reached by falling through the empty-`--ref`
    guard first and never noticing `--latest` was also given.
    """
    install_manifest(monkeypatch, tmp_path)
    forbid(monkeypatch, "latest_tag")
    forbid(monkeypatch, "fetch_tree")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, ["--ref", "", "--latest"])

    assert excinfo.value.code == 2


def test_missing_verified_against_is_the_check_failing_not_drift(monkeypatch, tmp_path):
    """SPEC 11.2: this exits above 1, distinct from the exit 1 a real drifted

    fact produces, so `upstream-drift.yml` goes red instead of filing a
    ticket claiming upstream moved when nothing did. Neither network
    function may run: there is no tag to check anything against.

    `facts` carries one well-formed entry, not `[]`: SPEC.md 11.3 made an
    empty `facts` list malformed on its own account, so `[]` here would be
    caught by that guard before ever reaching the missing-`verified_against`
    branch this test names - exactly the recurrence already documented on
    `write_manifest()`'s default, one round earlier, for the same reason.
    """
    manifest = {"upstream": "example/upstream", "facts": [well_formed_fact()]}
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cpf, "MANIFEST", path)
    forbid(monkeypatch, "latest_tag")
    forbid(monkeypatch, "fetch_tree")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code == 2


def test_empty_verified_against_is_the_check_failing_not_drift(monkeypatch, tmp_path):
    """The empty-string case, distinct from missing: `.get()` returns it rather

    than the default, so only the explicit falsiness check catches it.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="")
    forbid(monkeypatch, "latest_tag")
    forbid(monkeypatch, "fetch_tree")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code == 2


def test_a_real_drifted_fact_still_exits_exactly_one(monkeypatch, tmp_path):
    """Contrast case for the two tests above: a manifest defect exits above 1,

    but an actual drifted fact must still exit exactly 1 - the two failure
    modes must stay distinguishable in both directions.
    """
    install_manifest(
        monkeypatch,
        tmp_path,
        facts=[{"id": "F1", "file": "mod.py", "kind": "function", "name": "gone", "why": "w"}],
    )
    recording_fetch_tree(monkeypatch, tree={"mod.py": "x = 1\n"})

    status = run_main(monkeypatch, [])

    assert status == 1


@pytest.mark.parametrize(
    "argv",
    [[], ["--latest"], ["--ref", "asked-ref"]],
    ids=["verified", "latest", "asked"],
)
def test_the_report_says_which_question_was_asked(monkeypatch, tmp_path, capsys, argv):
    """SPEC 11.2 point 6: the two failures mean opposite things and the ticket

    is read cold, so the printed report - not just `--json` - has to let a
    reader tell which question produced it. Checked here by requiring every
    mode's report to differ from the other two and to name the tag it used.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="v-verified")
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: "v-latest")
    recording_fetch_tree(monkeypatch)

    run_main(monkeypatch, argv)
    report = capsys.readouterr().out

    if argv == []:
        expected_ref = "v-verified"
    elif argv == ["--latest"]:
        expected_ref = "v-latest"
    else:
        expected_ref = "asked-ref"

    assert expected_ref in report


def test_the_three_reports_are_pairwise_distinct(monkeypatch, tmp_path, capsys):
    """The distinguishability claim itself, gathered in one place rather than

    left to be inferred from three separate runs above.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="v-verified")
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: "v-latest")

    reports = []
    for argv in ([], ["--latest"], ["--ref", "asked-ref"]):
        recording_fetch_tree(monkeypatch)
        run_main(monkeypatch, argv)
        reports.append(capsys.readouterr().out)

    assert len(set(reports)) == 3, reports


def test_the_reports_differ_by_wording_even_when_the_tag_is_the_same(
    monkeypatch, tmp_path, capsys
):
    """The same tag reached three different ways is exactly the case where the

    ref alone cannot tell a reader which question was asked: `--ref` naming
    the currently verified tag, or `--latest` resolving to it because
    upstream has not released since. The wording is the only signal left,
    so this pins that the wording itself - not just the tag - differs.
    """
    same_tag = "v-same"
    install_manifest(monkeypatch, tmp_path, verified_against=same_tag)
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: same_tag)

    reports = []
    for argv in ([], ["--latest"], ["--ref", same_tag]):
        recording_fetch_tree(monkeypatch)
        run_main(monkeypatch, argv)
        reports.append(capsys.readouterr().out)

    assert len(set(reports)) == 3, reports


EVERY_REF_MODE_ARGV = [[], ["--latest"], ["--ref", "candidate-3"]]

_LABEL_RE = re.compile(r"\(([^)]*)\):")


@pytest.mark.parametrize("argv", EVERY_REF_MODE_ARGV, ids=["verified", "latest", "asked"])
def test_the_plain_reports_label_matches_the_json_reports_question(
    monkeypatch, tmp_path, capsys, argv
):
    """Closes a gap the same-tag test above cannot: three pairwise-distinct

    strings still pass even if a mutant swaps which phrase belongs to which
    mode, since three mislabelled strings are still three distinct strings.
    This ties the human report's parenthetical label to `cpf.QUESTION_LABELS`,
    the same table the script itself uses to pick that wording and to build
    `--json`'s `question` field, so a mismatch between the two outputs can
    only mean the printed label and the assigned `question` disagree.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="candidate-1")
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: "candidate-2")

    recording_fetch_tree(monkeypatch)
    run_main(monkeypatch, [*argv, "--json"])
    question = json.loads(capsys.readouterr().out)["question"]

    recording_fetch_tree(monkeypatch)
    run_main(monkeypatch, argv)
    plain_report = capsys.readouterr().out
    match = _LABEL_RE.search(plain_report)
    assert match is not None, f"no parenthetical label found in report: {plain_report!r}"

    assert match.group(1) == cpf.QUESTION_LABELS[question]


def test_the_three_json_questions_are_pairwise_distinct(monkeypatch, tmp_path, capsys):
    """The opaque-token test above only checks a mode against itself; a mutant

    that collapses `question` to the same value for every mode (and collapses
    the text label lookup to match) would pass every one of those cases
    without ever being caught. This closes that, at the `--json` layer where
    the token actually lives.
    """
    install_manifest(monkeypatch, tmp_path, verified_against="candidate-1")
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: "candidate-2")

    questions = []
    for argv in EVERY_REF_MODE_ARGV:
        recording_fetch_tree(monkeypatch)
        run_main(monkeypatch, [*argv, "--json"])
        questions.append(json.loads(capsys.readouterr().out)["question"])

    assert len(set(questions)) == 3, questions


# ---------------------------------------------------------------------------
# `upstream` steers a network call and is now checked against the owner/name
# shape before that call happens (SPEC.md 11.2, "Putting the checker on a
# pull-request path changed who can steer it").
# ---------------------------------------------------------------------------


def test_a_well_formed_upstream_is_fetched_exactly_as_given(monkeypatch, tmp_path):
    install_manifest(
        monkeypatch, tmp_path, upstream="example-owner/example-repo", verified_against="v1.0.0"
    )
    calls = recording_fetch_tree(monkeypatch)

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example-owner/example-repo", "v1.0.0")]


@pytest.mark.parametrize(
    "upstream",
    [
        "https://evil.example/owner/repo",
        "example-owner/example-repo/extra",
        "../secret-org/private-repo",
        "",
        "example-owner/..",
        "../example-repo",
    ],
    ids=[
        "absolute-url",
        "second-slash",
        "dot-dot",
        "empty",
        "dot-dot-suffix-correct-slash-count",
        "dot-dot-prefix-correct-slash-count",
    ],
)
def test_a_malformed_upstream_is_refused_before_any_fetch(monkeypatch, tmp_path, upstream):
    """The seam assertions are the point, not the exit status alone: a check

    that refused only after already calling `fetch_tree` would satisfy a
    status-only test while still having made the diff-controlled request it
    exists to prevent. Exit 2 is above 1, not 1 itself: 1 means a fact
    drifted, and `upstream-drift.yml` branches on the two meaning opposite
    things, so a malformed `upstream` must not read as upstream having
    moved when the repository never asked it anything.

    The last two are the cases the first draft of this test deliberately
    left out: `owner/..` and `../owner` both have exactly one slash and two
    segments, so `UPSTREAM_RE` alone accepts them - `.` is an ordinary
    character inside a segment - and only a separate `".." in repo` check
    catches them. They are here now that that check exists.
    """
    install_manifest(monkeypatch, tmp_path, upstream=upstream, verified_against="v1.0.0")
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code == 2




# ---------------------------------------------------------------------------
# SPEC.md 11.3: the reason comes from the fact, never from the branch that
# caught it (issue #150).
#
# `absent` was written for section 11.1's forbidden-symbol guards, and its
# message used to say so unconditionally. Three of the four `absent` entries
# in the real manifest (F28d, F30b, F31b) are positive claims about
# upstream's shape, not forbidden-symbol guards, and section 11.1 says
# nothing about any of them. The rule is stated generally in section 11.3 -
# "every message a fact failure prints is built from that fact's own entry" -
# so it is tested generally here, across kinds, not only for `absent`.
#
# Driven through `main()`, not `check()` directly: `check(fact, files)` only
# ever describes what it observed - "mod.py now contains [...]" - and never
# carries `why` in its return value, in either the current checkout or any
# reading of section 11.3 that treats "what it observed" and "why it
# matters" as the two different things section 11.3 says they are. Section
# 11.3's rule is about the report a reader ends up looking at (the thing
# `upstream-drift.yml` posts into a ticket), which is what `main()` builds by
# combining `check()`'s observation with the fact's own `why` - so that is
# the layer these tests exercise, exactly as `upstream-drift.yml` captures
# both `stdout` and `stderr` into the same `report.txt` it posts.
# ---------------------------------------------------------------------------


def install_facts_manifest(monkeypatch, tmp_path: Path, facts: list[dict], tree: dict) -> None:
    install_manifest(monkeypatch, tmp_path, verified_against="v1.0.0", facts=facts)

    def fake_fetch_tree(repo: str, ref: str, wanted: set[str]) -> dict[str, str]:
        return tree

    monkeypatch.setattr(cpf, "fetch_tree", fake_fetch_tree)
    monkeypatch.setattr(cpf, "latest_tag", lambda repo: (_ for _ in ()).throw(
        AssertionError("latest_tag must not be called")
    ))


def drifted_entries(
    monkeypatch, tmp_path: Path, capsys, facts: list[dict], tree: dict
) -> list[dict]:
    """Runs the checker end to end and returns the `--json` `drifted` list -

    the same data `main()`'s plain-text path prints, in a form a test can
    compare structurally instead of scraping a sentence.
    """
    install_facts_manifest(monkeypatch, tmp_path, facts, tree)
    status = run_main(monkeypatch, ["--json"])
    assert status == 1, "the fixture was meant to drift; fix the fixture, not the assertion"
    return json.loads(capsys.readouterr().out)["drifted"]


def test_a_failing_facts_report_carries_its_own_why(monkeypatch, tmp_path, capsys):
    """F30b's own words, not a sentence fixed in the branch.

    The tree makes `sys.modules` present, which is exactly what F30b's real
    `why` says must never happen.
    """
    fact = {
        "id": "F30b",
        "file": "mod.py",
        "kind": "absent",
        "text": ["sys.modules"],
        "why": (
            "the loader never registers the plugin module, so during import "
            "sys.modules.get(__name__) is None"
        ),
    }
    tree = {"mod.py": "sys.modules[__name__] = None\n"}

    entries = drifted_entries(monkeypatch, tmp_path, capsys, [fact], tree)

    # `problem` is the discriminating assertion: `main()` copies `why` from
    # the fact onto the entry regardless of what `check()` returned, so a
    # `why`-only assertion here would pass unchanged against the code #150
    # replaced, which appended "which section 11.1 forbids relying on" to
    # exactly this `problem` string. `why` is still asserted below, but only
    # alongside the assertion that actually depends on the fix.
    assert entries[0]["id"] == "F30b"
    assert entries[0]["problem"] == "mod.py now contains ['sys.modules']"
    assert entries[0]["why"] == fact["why"]


def test_nothing_cites_11_1_for_a_why_that_does_not_mention_it(monkeypatch, tmp_path, capsys):
    """The negative half of the same rule, checked against the whole report

    a reader actually sees (both streams, matching what `upstream-drift.yml`
    captures into the ticket body), not only the JSON's `why` field: a `why`
    about `sys.modules` registration has nothing to do with section 11.1's
    forbidden-symbol list, so nothing anywhere in the output may send the
    reader there.
    """
    fact = {
        "id": "F31b",
        "file": "mod.py",
        "kind": "absent",
        "text": ["'ready'"],
        "why": (
            "neither entry point emits ready itself, which is what makes the "
            "manual path agent-less (issue #140)"
        ),
    }
    tree = {"mod.py": "plugins.on('ready', self)\n"}

    install_facts_manifest(monkeypatch, tmp_path, [fact], tree)
    status = run_main(monkeypatch, [])
    assert status == 1
    captured = capsys.readouterr()
    report = captured.out + captured.err

    assert "11.1" not in report
    assert "forbid" not in report.lower()


def test_an_absent_fact_that_really_is_a_forbidden_symbol_still_cites_11_1_via_its_why(
    monkeypatch, tmp_path, capsys
):
    """Contrast case: when a `why` itself names section 11.1, the reference

    is legitimate and must still come through - the rule is "never invent
    the citation", not "never print it". This is a control for the negative
    test above rather than a #150 defect-catcher on its own: a `why`
    round-trips regardless of what `check()` returns, so `"11.1" in
    entries[0]["why"]` is true whether or not the branch still appends its
    own copy. What is worth pinning here is that the *observation* stays
    exact even in the one case where the pre-#150 text and a legitimate
    `why` would have overlapped - a "fix" that kept the old suffix only when
    `why` happens to mention 11.1 would still pass a why-only check but fails
    this one.
    """
    fact = {
        "id": "Fx",
        "file": "mod.py",
        "kind": "absent",
        "text": ["agent.access_points"],
        "why": "a no-underscore form section 11.1 forbids relying on",
    }
    tree = {"mod.py": "x = agent.access_points\n"}

    entries = drifted_entries(monkeypatch, tmp_path, capsys, [fact], tree)

    assert entries[0]["problem"] == "mod.py now contains ['agent.access_points']"
    assert "11.1" in entries[0]["why"]


@pytest.mark.parametrize(
    "fact,expected_problem",
    [
        ({"id": "Fa", "file": "mod.py", "kind": "function", "name": "gone_now",
          "why": "REASON_A stats.gone_now comes from here"},
         "mod.py no longer defines gone_now()"),
        ({"id": "Fb", "file": "mod.py", "kind": "contains_all",
          "text": ["never_here_at_all"], "why": "REASON_B the config default"},
         "mod.py no longer contains ['never_here_at_all']"),
        ({"id": "Fc", "file": "mod.py", "kind": "methods", "class": "Peer",
          "names": ["vanished"], "why": "REASON_C every method normalise_peer calls"},
         "Peer no longer has vanished"),
        ({"id": "Fd", "file": "mod.py", "kind": "function_count", "name": "duplicated",
          "count": 99, "why": "REASON_D defined twice, second wins"},
         "mod.py defines duplicated() 2 time(s), expected 99"),
        ({"id": "Fe", "file": "mod.py", "kind": "contains_count",
          "text": "def duplicated", "count": 99, "why": "REASON_E fired from two sites"},
         "mod.py contains 'def duplicated' 2 time(s), expected 99"),
        ({"id": "Ff", "file": "mod.py", "kind": "absent", "text": ["Peer"],
          "why": "REASON_F an absence, not a forbidden symbol"},
         "mod.py now contains ['Peer']"),
    ],
    ids=["function", "contains_all", "methods", "function_count", "contains_count", "absent"],
)
def test_every_kind_of_failure_reports_only_the_observation(
    monkeypatch, tmp_path, capsys, fact, expected_problem
):
    """Section 11.3: "this generalises past the absent kind": every message a

    fact failure prints is built from that fact's own entry, not from a
    second reason the branch adds on top - the mistake #150 found in the
    `absent` branch specifically. `expected_problem` is hardcoded per kind
    rather than compared against a second call to `cpf.check()`: comparing
    the full pipeline's `problem` to `cpf.check(fact, TREE)` would be
    circular, since both sides call the very same (possibly still-buggy)
    function and would agree with each other before and after any fix. Only
    the `absent` case here actually discriminates #150's specific defect -
    its pre-fix message was this same string plus ", which section 11.1
    forbids relying on" - but the same exact-match discipline is applied to
    every kind so a future regression in any of them is caught the same way,
    which is what "this generalises" means. `why` is asserted too, as a
    documentation of the full entry shape rather than as the discriminator.
    """
    entries = drifted_entries(monkeypatch, tmp_path, capsys, [fact], TREE)

    assert entries[0]["problem"] == expected_problem
    assert entries[0]["why"] == fact["why"]


def test_two_facts_of_the_same_kind_differing_only_in_why_are_reported_with_their_own(
    monkeypatch, tmp_path, capsys
):
    """Proves the report is not a fixed per-kind sentence with the id swapped

    in, and pins it with an `absent`-kind pair specifically: `function`
    facts (the previous version of this test) were never touched by #150,
    so an equal-`problem` assertion there would hold regardless of the fix.
    Both facts here share the same observation, `mod.py now contains
    ['Peer']` exactly, no suffix - which only holds once the `absent` branch
    stops adding one of its own - and carry different `why`; a checker that
    lost track of which fact a `why` belonged to could report either one for
    both, or drop one silently.
    """
    fact_1 = {"id": "Fp", "file": "mod.py", "kind": "absent", "text": ["Peer"],
              "why": "REASON_ONE this is the first fact's own reason"}
    fact_2 = {"id": "Fq", "file": "mod.py", "kind": "absent", "text": ["Peer"],
              "why": "REASON_TWO this is the second fact's own, different reason"}

    entries = drifted_entries(monkeypatch, tmp_path, capsys, [fact_1, fact_2], TREE)

    by_id = {entry["id"]: entry for entry in entries}
    assert by_id["Fp"]["problem"] == "mod.py now contains ['Peer']"
    assert by_id["Fq"]["problem"] == "mod.py now contains ['Peer']"
    assert by_id["Fp"]["why"] == fact_1["why"]
    assert by_id["Fq"]["why"] == fact_2["why"]
    assert by_id["Fp"]["why"] != by_id["Fq"]["why"]


# ---------------------------------------------------------------------------
# SPEC.md 11.3: a malformed manifest is the check failing, never upstream
# drifting (issue #167). `manifest["upstream"]` used to be interpolated
# unchecked; a non-string value raised inside the guard itself
# (`TypeError`/`KeyError`), which Python turns into exit 1 - the status
# `upstream-drift.yml` reads as "upstream moved". Every case here must exit
# above 1, and never reach `fetch_tree` or `latest_tag` to find out.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "upstream",
    [None, 42, ["owner", "repo"], {"owner": "x", "repo": "y"}],
    ids=["none", "int", "list", "dict"],
)
def test_a_non_string_upstream_exits_above_one_never_exactly_one(monkeypatch, tmp_path, upstream):
    install_manifest(monkeypatch, tmp_path, upstream=upstream, verified_against="v1.0.0")
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_a_missing_upstream_key_exits_above_one_never_exactly_one(monkeypatch, tmp_path):
    """Distinct from `None`: `"upstream" not in manifest` is `KeyError`, a

    different failure than `manifest["upstream"] is None`, and both must
    land on the same non-drift status.

    `facts` carries one well-formed entry, not `[]`, for the same reason as
    the sibling `verified_against` test above: an empty list is itself
    malformed and would be caught before this test's own branch ever runs.
    """
    manifest = {"verified_against": "v1.0.0", "facts": [well_formed_fact()]}
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cpf, "MANIFEST", path)
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


@pytest.mark.parametrize(
    "upstream",
    [None, 42, ["owner", "repo"], {"owner": "x", "repo": "y"}],
    ids=["none", "int", "list", "dict"],
)
def test_the_message_says_malformed_not_that_upstream_moved(
    monkeypatch, tmp_path, capsys, upstream
):
    """SPEC 11.3 / issue #167 acceptance: the sentence must not read like the

    `--latest` drift report `upstream-drift.yml` turns into a ticket. Checked
    on both streams, so a future move between stdout and stderr is caught
    too rather than silently making this assertion vacuous.
    """
    install_manifest(monkeypatch, tmp_path, upstream=upstream, verified_against="v1.0.0")
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit):
        run_main(monkeypatch, [])

    captured = capsys.readouterr()
    output = captured.err + captured.out
    assert "malformed" in output
    assert "moved" not in output


def test_no_fetch_is_attempted_for_a_malformed_upstream(monkeypatch, tmp_path):
    """The seam assertion, not only the exit status: a checker that refused

    only after already calling `fetch_tree` would satisfy a status-only test
    while still making the diff-controlled request the guard exists to
    prevent. `forbid()` fails the test at the call site if either network
    function is reached.
    """
    install_manifest(monkeypatch, tmp_path, upstream=None, verified_against="v1.0.0")
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit):
        run_main(monkeypatch, [])


def test_a_well_formed_string_upstream_is_unaffected_by_the_type_check(monkeypatch, tmp_path):
    """Contrast case: the new type guard must not reject the ordinary shape."""
    install_manifest(
        monkeypatch, tmp_path, upstream="owner-name/repo-name", verified_against="v1.0.0"
    )
    calls = recording_fetch_tree(monkeypatch)

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("owner-name/repo-name", "v1.0.0")]


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "every value is type-checked before it is used ... every key
# any branch reaches - file, id, kind, why, and the per-kind fields." Review
# of this same section found four shapes that reached exit 1 with nothing
# guarding them: a `file` that is a list (unhashable, raises building the
# wanted set), a fact with no `id` (raises in the reporter), a `file` that is
# an integer (passes silently and reports a fabricated drift), and a `count`
# that is a string (compares unequal to an integer for ever, another
# fabricated drift). Each of those must instead be the check refusing to
# run, exactly like `upstream` above - and `verified_against`'s own type
# check, which predates this section, had no test of its own either.
#
# `why` is included here too: #150 made it the only source of "why a failure
# matters" for every kind, which is only safe if every fact is guaranteed to
# carry one (SPEC.md 11.3, "why is required, and that is a consequence of
# #150's subtraction rather than a tidiness rule").
# ---------------------------------------------------------------------------


def assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, **overrides) -> str:
    install_manifest(monkeypatch, tmp_path, **overrides)
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "malformed" in output
    return output


def well_formed_fact(**overrides) -> dict:
    fact = {"id": "F1", "file": "mod.py", "kind": "contains_all", "text": ["x"], "why": "w"}
    fact.update(overrides)
    return fact


def test_a_facts_file_that_is_a_list_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """`{fact["file"] for fact in facts}` raises `TypeError: unhashable type:

    'list'` for this shape, building the wanted set before any fact is even
    looked at - reported by review as reaching exit 1 uncaught.
    """
    assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[well_formed_fact(file=["mod.py"])]
    )


def test_a_fact_with_no_id_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """Before this fix, `main()`'s reporter builds `{"id": fact["id"], ...}`

    and only raises `KeyError` there, so a fact that never drifts would let a
    missing `id` through unnoticed - `validate()` must catch it up front
    instead, which is why `fetch_tree` is forbidden here as it is for every
    other case in this section: none of them may get far enough to need it.
    """
    fact = well_formed_fact()
    del fact["id"]
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_a_facts_file_that_is_an_integer_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """Passed silently today: `files.get(7)` is a legal (if pointless) dict

    lookup that returns `None`, so `check()` reports "7 no longer exists
    upstream" - a fabricated drift for a `file` value that was never a path
    to begin with, not the manifest being malformed.
    """
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[well_formed_fact(file=7)])


def test_a_count_that_is_a_string_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """`source.count(text) != expected` compares an `int` against a `str`,

    which is simply never equal, so every fact with this shape reports a
    permanent, fabricated drift regardless of what upstream does.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "contains_count", "text": "x",
            "count": "1", "why": "w"}
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


@pytest.mark.parametrize(
    "verified_against",
    [42, ["v1.0.0"], {"tag": "v1.0.0"}],
    ids=["int", "list", "dict"],
)
def test_a_verified_against_of_the_wrong_type_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys, verified_against
):
    """The falsiness check `main()` already had (`if not ref: fail(...)`)

    only catches `None` and `""`; nothing before this section's rewrite
    exercised a `verified_against` of a type that is simply never falsy, and
    the general "every manifest value is type-checked" rule names it
    explicitly alongside `upstream`.
    """
    assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, verified_against=verified_against
    )


def test_a_fact_with_no_why_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """SPEC.md 11.3: "`why` is required ... a fact with no `why` produces a

    report with no reason at all." Distinct from the missing-`id` case
    above: this one must be caught before any drift is even computed, since
    a fact that never drifts would otherwise let a missing `why` through
    unnoticed for ever.
    """
    fact = well_formed_fact()
    del fact["why"]
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


@pytest.mark.parametrize("why", [None, 7], ids=["none", "int"])
def test_a_why_of_the_wrong_type_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys, why
):
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[well_formed_fact(why=why)])


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "an unknown key on a fact is refused, not ignored." Found by
# the same audit that found the empty-`facts` fail-open: `required =
# set(fact.get("params", []))` then `if required:` means `params: ""` and
# `params: {}` are both falsy, so the signature half of a `function` fact is
# silently skipped and the run exits **0** - confirmed live against F2 and
# F9a. `params: "celsius"` iterates the string's characters instead, and
# reports that the function no longer takes `c`, `e`, `i`, `l`, `s`, `u`: a
# fabricated drift that would file a ticket about something that never
# happened. A misspelt key (`param:` for `params:`) is the same defect with
# no trace left behind at all, which is why an unknown key on a fact is
# refused outright rather than merely ignored - the argument SPEC.md 2.2.1
# already makes about a plugin's configuration table, applied here.
#
# The case that matters most is written first: a fact whose signature check
# is disabled must not exit 0. As with the empty-`facts` case, the exit
# status is asserted directly, not only the message, because the defect
# was a clean pass, not a wrong sentence.
# ---------------------------------------------------------------------------


def function_fact(**overrides) -> dict:
    fact = {"id": "F2", "file": "mod.py", "kind": "function", "name": "temperature",
            "why": "w"}
    fact.update(overrides)
    return fact


def test_a_params_that_would_silently_disable_the_signature_check_does_not_exit_zero(
    monkeypatch, tmp_path, capsys
):
    """`params: ""` is exactly F2's live shape (`params: ["celsius"]`) typed

    as an empty string instead - `set("")` is falsy, so `if required:` skips
    the check entirely and `check()` returns `None`, reporting the fact as
    holding regardless of what the real signature is. Refusing this manifest
    up front, before any fetch, is what turns that silent pass into
    something other than 0.
    """
    output = assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[function_fact(params="")]
    )
    assert "0 fact(s) checked" not in output


@pytest.mark.parametrize(
    "params",
    ["", {}, "celsius", ["celsius", 7]],
    ids=["empty-string", "dict", "string-of-characters", "list-with-non-string"],
)
def test_a_malformed_params_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys, params
):
    """The other three shapes review named, plus the empty string above,

    gathered under one exit-status contract: `params: "celsius"` in
    particular must not exit exactly 1, since `set("celsius")` is truthy and
    the unguarded code reports `temperature() no longer takes c, e, i, l, s,
    u` - a fabricated drift `upstream-drift.yml` would file as a real one.
    """
    assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[function_fact(params=params)]
    )


@pytest.mark.parametrize(
    "params",
    [[], [""]],
    ids=["empty-list", "list-with-only-empty-string"],
)
def test_a_vacuous_params_list_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys, params
):
    """The two shapes SPEC.md 11.3's general rule reaches that the earlier,

    narrower fix (`if required:`) never covered: `params: []` is well-typed
    - a list of strings, vacuously - and `set()` is exactly as falsy as
    `set("")`, so a guard that only rejects the string/dict shapes named in
    the historical incident (`params: ""`, `params: {}`) still lets this one
    disable the signature check the same way. `params: [""]` is the other
    half of the general rule stated for `text`/`names`: a *non-empty* list
    whose only element is itself empty is still vacuous, since `""` is never
    a real parameter name a function could have - a length-only check on the
    list would wave this one through even after the empty-list case above is
    fixed.
    """
    assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[function_fact(params=params)]
    )


def test_a_well_formed_params_list_is_unaffected_by_the_guard(monkeypatch, tmp_path):
    """Contrast case: the ordinary shape, a list of parameter-name strings,

    must not be rejected by whatever now validates it.
    """
    install_manifest(monkeypatch, tmp_path, facts=[function_fact(params=["celsius"])])
    calls = recording_fetch_tree(
        monkeypatch, tree={"mod.py": "def temperature(celsius=True):\n    return 40\n"}
    )

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example/upstream", "v1.0.0")]


def test_an_unknown_key_on_a_fact_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """`param:` for `params:` is the shape that made this worth a rule rather

    than a fix to one field: a misspelling leaves nothing behind for any
    per-field check to catch, since the field it was meant to populate is
    simply absent and reads as "no `params` set" - indistinguishable from a
    fact that never had one. Only refusing keys nobody asked for closes it.
    """
    fact = well_formed_fact()
    fact["param"] = ["celsius"]  # misspelling of "params"
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_a_count_of_true_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """`isinstance(True, int)` is `True` - `bool` subclasses `int` in Python -

    so a `count` of JSON `true` passes `isinstance(fact["count"], int)`
    unguarded, and `2 != True` compares fine (`False`) without ever raising,
    so this shape does not even reach exit 1 the way the string-`count` case
    did: it either silently disagrees with a real count of 1, or silently
    agrees with one, indistinguishable from a well-formed fact either way.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "function_count", "name": "duplicated",
            "count": True, "why": "w"}
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


@pytest.mark.parametrize(
    "kind",
    [7, ["contains_all"]],
    ids=["int", "list"],
)
def test_a_non_string_kind_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys, kind):
    """A `kind` that is not a string at all - int or list - is a different

    defect than an unrecognised string like `"nonsense"`: the unknown-kind
    branch compares `fact["kind"]` against a set of known strings, which for
    a non-string value either raises trying to format it into a message or
    (for a hashable non-string like an int) simply never matches any known
    kind and is reported the same as a typo, losing the distinction the next
    test below pins. Refusing the type up front, same as `upstream`,
    `verified_against`, and `count`, is what this file's own idiom - exit
    above 1, never exactly 1, no fetch attempted - already applies to every
    other manifest value; `kind` had no test of its own for this.
    """
    assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[well_formed_fact(kind=kind)]
    )


def test_an_unknown_string_kind_and_a_non_string_kind_are_different_refusals(
    monkeypatch, tmp_path, capsys
):
    """The distinction worth pinning, not just two passing tests side by side:

    an unrecognised *string* kind (`"nonsense"`) and a non-string kind (`7`)
    must produce messages that actually differ in what makes them different,
    not merely two strings that both happen to contain "malformed" - a test
    that only asserted the shared substring could not tell a checker that
    collapsed both into one generic "bad kind" message apart from one that
    kept them distinct. Checked here by requiring the non-string report to
    say the value is not a string (`"string"` appears) while the unknown
    string's report does not, and vice versa for naming the actual
    unrecognised kind (`"nonsense"` appears in its own report and not in the
    non-string one).
    """
    output_unknown_string = assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[well_formed_fact(kind="nonsense")]
    )
    output_non_string = assert_manifest_is_malformed(
        monkeypatch, tmp_path, capsys, facts=[well_formed_fact(kind=7)]
    )

    assert "string" in output_non_string
    assert "string" not in output_unknown_string
    assert "nonsense" in output_unknown_string
    assert "nonsense" not in output_non_string
    assert output_unknown_string != output_non_string


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "the vacuity rule reaches the fields a fact is written
# *about*, not only the ones it asserts *with*." An empty `id`, `file`,
# `name` or `class` does not make a check vacuous - it makes it permanently
# **false**, which is worse: the run exits 1 (the drift status) rather than
# 0, and `upstream-drift.yml` files a ticket saying upstream moved when
# nothing did. Observed before the fix: `name: ""` reported "m.py no longer
# defines ()", `file: ""` reported " no longer exists upstream", and an
# empty `id` printed a drift line with nothing identifying it. Every case
# here asserts the exit code discipline this file uses throughout for a
# malformed manifest (`> 1`, `!= 1`) *and* that the specific fabricated
# sentence the empty field would have produced is nowhere in the output -
# the status alone would also pass against a checker that refused the
# manifest for the wrong reason while still leaking the fabricated line
# through some other path (e.g. a `--json` branch left unguarded).
# ---------------------------------------------------------------------------


def test_an_empty_name_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """SPEC.md 11.3's own example: an unguarded `name: ""` on a `function`

    fact reports `mod.py no longer defines ()` - a fabricated drift for a
    function name that was never real to begin with, not the manifest being
    malformed.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "function", "name": "", "why": "w"}
    output = assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])

    assert "no longer defines ()" not in output


def test_an_empty_file_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """SPEC.md 11.3's own example: an unguarded `file: ""` reports " no

    longer exists upstream" - `files.get("")` is a legal, pointless lookup
    that returns `None`, fabricating a drift for a path that was never a
    path.
    """
    fact = well_formed_fact(file="")
    output = assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])

    assert "no longer exists upstream" not in output


def test_an_empty_id_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """SPEC.md 11.3: an empty `id` "prints a drift line with nothing

    identifying it" - distinct from the missing-`id` case already covered
    above (`KeyError` in the reporter): this one is present, is a string,
    and is still vacuous as an identifier. The fact is shaped to drift for
    real if the guard did not catch it (`never_here_at_all` is not in
    `TREE`), so a checker that let this through would fabricate a report
    that says nothing moved: the fabricated `contains_all` sentence itself
    must not appear either.
    """
    fact = well_formed_fact(id="", kind="contains_all", text=["never_here_at_all"])
    output = assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])

    assert "no longer contains" not in output


def test_an_empty_class_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """The fourth field the spec text names alongside `id`, `file` and

    `name`: an unguarded `class: ""` on a `methods` fact reports "mod.py no
    longer defines class " with nothing after it - the same shape this
    file's own passing fixture uses for a genuinely gone class ("Gone"),
    just with the name emptied out instead of misspelt.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "methods", "class": "",
            "names": ["full_name"], "why": "w"}
    output = assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])

    assert "no longer defines class" not in output


def test_non_empty_id_file_name_and_class_are_unaffected_by_these_guards(
    monkeypatch, tmp_path
):
    """Contrast case: ordinary, non-empty `id`, `file`, `name` and `class`

    values must still run cleanly through to a fetch, not be caught by an
    overzealous version of any of the four guards above.
    """
    install_manifest(
        monkeypatch,
        tmp_path,
        facts=[
            {"id": "F1", "file": "mod.py", "kind": "function", "name": "uptime",
             "why": "w"},
            {"id": "F2", "file": "mod.py", "kind": "methods", "class": "Peer",
             "names": ["full_name"], "why": "w"},
        ],
    )
    calls = recording_fetch_tree(monkeypatch, tree=TREE)

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example/upstream", "v1.0.0")]


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "the checker keeps what the manifest asked for, and refuses
# an archive that will not fit" / "Exit 1 means upstream moved, and nothing
# else may produce it." A download cut short, or a truncated or corrupt
# gzip stream, must not be allowed to exit through the interpreter's own
# uncaught-exception status - which is 1, indistinguishable from a real
# drifted fact to `upstream-drift.yml`. Built from a genuinely valid
# archive, cut short, rather than arbitrary garbage bytes: garbage might be
# refused earlier, by the gzip-header check alone, and never reach either
# path a real truncation can take.
#
# Two tests, deliberately, not one, and each is pinned to a specific cut
# fraction rather than "some truncation": a truncated stream can fail in
# two different places, guarded by two different `except` blocks, and a
# single fixture only ever exercises whichever one its own cut happens to
# land in. This was found live, not designed in - the first version of this
# section had one test, cut at 50% of a small single-member archive, and it
# failed with an uncaught `EOFError` for a real reason: `tarfile.open()`
# itself reads the gzip header and the first member's data before it
# returns, and at the time that call sat in a `try` guarding only
# `tarfile.TarError`, which `EOFError` is not - so a cut severe enough to
# land inside the very first member's compressed data escaped uncaught and
# exited 1, the exact false-ticket status this rule exists to prevent. The
# fix widened that `except` to cover `EOFError`/`zlib.error` too, alongside
# the member-loop's own guard for the same shapes one call further along -
# two separate `except` clauses around two separate calls, not one. Losing
# either test back down to a single cut fraction - "simplifying" the
# fixture - would silently stop testing whichever path the surviving
# fraction does not land in, which is the whole reason both stay.
#
# A 5-member, 2000-bytes-per-member archive is deliberately *not* reused for
# the 50% case: at that size gzip compresses "x" * 2000 so well that even a
# 50% cut of the whole archive still leaves `tarfile.open()` able to read
# every header cleanly (confirmed live), so the two cut fractions only land
# in the two different handlers when paired with fixtures of the right
# shape for each - a small single-member archive for the 50% cut, several
# larger members for the 80% one.
# ---------------------------------------------------------------------------


def test_a_severely_truncated_archive_fails_opening_the_tarball(monkeypatch, capsys):
    """The 50% cut: severe enough to land inside the first member's own

    compressed data, so `tarfile.open()` itself never returns - the failure
    the fix above added a guard for. Exit code asserted `> 1` and `!= 1`,
    not merely `!= 0`, since the historical defect exited exactly 1
    uncaught and a weaker assertion would still pass against it.
    """
    archive = build_archive({"mod.py": b"x = 1\n"})
    truncated = archive[: len(archive) // 2]
    install_archive(monkeypatch, truncated)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", {"mod.py"})

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    report = capsys.readouterr().err
    assert "could not be opened as a tarball" in report


def test_a_lightly_truncated_archive_fails_reading_a_later_member(monkeypatch, capsys):
    """The 80% cut: several members, each large enough that `tarfile.open()`

    itself succeeds - the first header is intact, well within the
    surviving 80% - so the failure instead comes from the member-loop's own
    guard, reading a later member's content past where the stream actually
    ends. The message is asserted to differ from the 50% case above, not
    only the exit code: the two are different failures with different next
    actions for whoever reads the report (a stream that never opened versus
    one that broke partway through), and a checker that collapsed both into
    one generic sentence would pass a status-only version of both tests
    while erasing the distinction the two `except` blocks exist to keep.
    """
    archive = build_archive({f"f{i}.py": b"x" * 2000 for i in range(5)})
    truncated = archive[: int(len(archive) * 0.8)]
    install_archive(monkeypatch, truncated)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree(
            "example/upstream", "v1.0.0", {f"f{i}.py" for i in range(5)}
        )

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    report = capsys.readouterr().err
    assert "archive ended unexpectedly while reading it" in report
    assert "could not be opened as a tarball" not in report


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "a wanted member that is not valid UTF-8 fails the run."
# Skipping an undecodable member with `continue` was right at `master`,
# where every text member of the whole tree was decoded and skipping
# binaries was the point; this branch's wanted-only filter means the skip
# can now fire on nothing but a file the manifest asked for **by name** -
# `defaults.toml` and similar are the real candidates - and the path then
# reads as absent from what was fetched, so `check()` reports it gone from
# upstream: exit 1, a ticket saying pwnagotchi moved, for a decode problem
# rather than a real change.
#
# The trap this rule is built to avoid, named explicitly because it is the
# reason the defect survived two reviews: a member the manifest does *not*
# name that fails to decode is still fine, and must stay fine - it is
# skipped before any read is attempted, for a reason that has nothing to do
# with this rule. A fixture with undecodable bytes in an arbitrary member
# proves nothing; the undecodable path has to be one the manifest actually
# asked for. Both shapes are tested below, side by side, so a fix that
# swings too far the other way - failing the run on *any* undecodable
# member, wanted or not - is caught by the second test rather than passing
# unnoticed because only the first was ever written.
# ---------------------------------------------------------------------------


def test_a_wanted_member_that_is_not_valid_utf8_fails_the_run(monkeypatch, capsys):
    """Observed live: `defaults.toml is not valid UTF-8: 'utf-8' codec can't

    decode byte 0xff in position 0: invalid start byte`, exit 2. Asserted
    with the exit-code discipline this file uses throughout for "the check
    refused to run" (`> 1`, `!= 1`, distinct from the drift status 1 a
    silently-skipped member would fabricate) and with the path name present
    in the report, since a message that named some other file or none at
    all would leave a reader unable to act on it.
    """
    archive = build_archive({"defaults.toml": b"\xff\xfe not valid utf-8"})
    install_archive(monkeypatch, archive)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", {"defaults.toml"})

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    report = capsys.readouterr().err
    assert "defaults.toml" in report
    assert "not valid UTF-8" in report


def test_an_unwanted_member_that_is_not_valid_utf8_is_still_fine(monkeypatch):
    """The trap itself, as a contrast case: the archive carries an

    undecodable member the manifest never asked for, alongside a clean one
    it did. The run must succeed, returning only the wanted, decodable
    path - the undecodable one is skipped before any read is attempted, the
    same way a binary asset in the real upstream tree always has been.
    """
    archive = build_archive({
        "clean.py": b"x = 1\n",
        "unwanted.bin": b"\xff\xfe also not valid utf-8",
    })
    install_archive(monkeypatch, archive)

    files = cpf.fetch_tree("example/upstream", "v1.0.0", {"clean.py"})

    assert files == {"clean.py": "x = 1\n"}


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "there are three handlers, not one, and the split is
# load-bearing ... they carry the same exception set and different
# messages, because an archive that would not open and one that broke
# while being read are different failures with different next actions." A
# fourth joins them here (the wanted-undecodable-member case above), and
# the risk named for the other three applies to it too: a future
# "simplification" that merges any two of the four into one message would
# pass every test above this one, each of which only ever compares its own
# fixture's report to a fixed substring. Gathered here, once, so a merge
# anywhere among the four is caught in one place rather than depended on to
# surface as a coincidental substring collision in an unrelated test.
# ---------------------------------------------------------------------------


def test_the_four_fetch_tree_failure_messages_are_pairwise_distinct(monkeypatch, capsys):
    """The download, `tarfile.open()`, the truncated-stream member loop, and

    the wanted-undecodable-member case: four different ways `fetch_tree`
    can refuse to run, exercised back to back against the same live
    function, with every report's *fixed* sentence collected and checked
    for four distinct strings.

    Comparing full reports (`report in reports`, `len(set(reports)) == 4`)
    does not pin this: every message ends `: {err}`, so two full reports
    can differ solely because the underlying exception's own text differs,
    while the checker's own fixed sentence - the part actually written by
    this script rather than quoted from the exception - has silently
    collapsed to the same wording as another case. That gap is real here,
    not hypothetical: cases 2 and 3 both raise an `EOFError` with the exact
    same text ("Compressed file ended before the end-of-stream marker was
    reached"), so a full-report comparison happens to still separate them
    only because their fixed sentences ("could not be opened as a tarball"
    vs "archive ended unexpectedly while reading it") differ - but a merge
    of the download's fixed sentence with either archive one would still
    leave four distinct *full* reports (the `err` text differs) and pass a
    full-report comparison regardless. Each `err` text is captured by
    triggering the same failure directly against the real `http.client`,
    `gzip`/`tarfile`, and `bytes.decode` machinery this test's fixtures
    exercise inside `fetch_tree` - not hardcoded - so the stripped-off tail
    is always exactly what this run actually produced, not a guess that
    could drift from it.
    """
    reports = []

    def run(build_response):
        monkeypatch.setattr(cpf.urllib.request, "urlopen", build_response)
        with pytest.raises(SystemExit):
            cpf.fetch_tree("example/upstream", "v1.0.0", {"mod.py", "defaults.toml"})
        reports.append(capsys.readouterr().err)

    class IncompleteReadResponse:
        def read(self, n=-1):
            raise http.client.IncompleteRead(b"partial")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    # 1. The download itself is cut short.
    incomplete_read_text = str(http.client.IncompleteRead(b"partial"))
    run(lambda *a, **kw: IncompleteReadResponse())

    # 2. `tarfile.open()` fails: a severe truncation of a small archive.
    small_archive = build_archive({"mod.py": b"x = 1\n"})
    small_truncated = small_archive[: len(small_archive) // 2]
    with pytest.raises(EOFError) as open_excinfo:
        tarfile.open(fileobj=io.BytesIO(small_truncated), mode="r:gz")
    tarfile_open_text = str(open_excinfo.value)
    run(lambda *a, data=small_truncated, **kw: FakeArchiveResponse(data))

    # 3. The member loop fails: a light truncation of a larger archive.
    large_archive = build_archive({f"f{i}.py": b"x" * 2000 for i in range(5)})
    large_truncated = large_archive[: int(len(large_archive) * 0.8)]
    with pytest.raises(EOFError) as loop_excinfo:
        with tarfile.open(fileobj=io.BytesIO(large_truncated), mode="r:gz") as tf:
            for member in tf:
                tf.extractfile(member).read()
    member_loop_text = str(loop_excinfo.value)
    run(lambda *a, data=large_truncated, **kw: FakeArchiveResponse(data))

    # 4. A wanted member is not valid UTF-8.
    undecodable_bytes = b"\xff\xfe not valid utf-8"
    with pytest.raises(UnicodeDecodeError) as decode_excinfo:
        undecodable_bytes.decode("utf-8")
    decode_text = str(decode_excinfo.value)
    undecodable_archive = build_archive({"defaults.toml": undecodable_bytes})
    run(lambda *a, data=undecodable_archive, **kw: FakeArchiveResponse(data))

    assert len(reports) == 4
    err_texts = [incomplete_read_text, tarfile_open_text, member_loop_text, decode_text]
    fixed_sentences = []
    for report, err_text in zip(reports, err_texts):
        tail = f": {err_text}"
        assert report.rstrip("\n").endswith(tail), (report, err_text)
        fixed_sentences.append(report.rstrip("\n")[: -len(tail)])

    assert len(set(fixed_sentences)) == 4, fixed_sentences


# ---------------------------------------------------------------------------
# SPEC.md 11.3: the manifest read handler catches `json.JSONDecodeError`,
# not every way a file can fail to be text at all. A manifest that is not
# valid UTF-8 raises `UnicodeDecodeError` - a `ValueError`, but not a
# `JSONDecodeError` - decoding the bytes before `json.loads` ever runs, so
# it escapes that handler and exits 1 uncaught, the same false-drift shape
# as every other case in this file's "malformed manifest" sections.
# ---------------------------------------------------------------------------


def test_a_non_utf8_manifest_exits_above_one_never_exactly_one(monkeypatch, tmp_path):
    """One invalid byte, mid-file, in an otherwise well-formed manifest -

    enough to make `.read_text(encoding="utf-8")` (or an equivalent decode)
    raise before any JSON parsing, structural validation, or fetch is even
    attempted.
    """
    import json as _json

    manifest = {
        "upstream": "example/upstream",
        "verified_against": "v1.0.0",
        "facts": [well_formed_fact()],
    }
    raw = _json.dumps(manifest).encode("utf-8")
    corrupted = raw[:10] + b"\xff" + raw[10:]
    path = tmp_path / "pinned_symbols.json"
    path.write_bytes(corrupted)
    monkeypatch.setattr(cpf, "MANIFEST", path)
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


# ---------------------------------------------------------------------------
# SPEC.md 11.3: `check()` raises `AssertionError` for a kind `validate()`
# should already have rejected - a defensive branch nothing in `main()`'s
# ordinary path can reach, since `validate()` always runs first and refuses
# an unrecognised `kind` there. Called directly, bypassing `validate()`
# entirely, since that is the only way to reach it at all.
# ---------------------------------------------------------------------------


def test_check_raises_assertion_error_for_a_kind_validate_would_have_rejected():
    fact = {"id": "x", "file": "mod.py", "kind": "not_a_real_kind", "why": "w"}

    with pytest.raises(AssertionError):
        cpf.check(fact, TREE)


def test_mains_except_exception_turns_the_unreachable_raise_into_exit_two(
    monkeypatch, tmp_path, capsys
):
    """The consequence of the raise above, not the raise itself: the test

    above calls `check()` directly and never touches `main()`'s own safety
    net, so nothing in this file pinned what a reader of a real run
    actually sees if that branch is ever reached in practice - a bug in
    this script (a `kind` added to `REQUIRED_KEYS` with no matching branch
    in `check()`) rather than a fact about upstream. `REQUIRED_KEYS` is
    widened with a kind `check()` cannot handle, which is the only way to
    reach this path through `main()` at all: `validate()` accepts any kind
    present in `REQUIRED_KEYS`, so a fact of this new kind passes validation
    and is handed to `check()`, which has no branch for it and raises. The
    `except Exception` in `main()` is what stands between that raise and an
    uncaught traceback exiting 1 - the drift status - for a mistake made in
    this script rather than upstream, so what is pinned here is exit 2 and
    the `AssertionError` naming itself in the report, not just "some
    non-zero code".
    """
    monkeypatch.setitem(cpf.REQUIRED_KEYS, "new_kind", ("text",))
    fact = {"id": "x", "file": "mod.py", "kind": "new_kind", "text": ["x"], "why": "w"}
    install_facts_manifest(monkeypatch, tmp_path, [fact], TREE)

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "AssertionError" in captured.out + captured.err


# ---------------------------------------------------------------------------
# SPEC.md 11.3: "nothing a fact asserts with may be empty, at any depth ...
# a value of the required type whose *empty* value makes the assertion
# vacuous." Named explicitly: `facts` (its own section below), `params`
# (above), and "then text and names, which between them carry 33 of the 42
# real facts: emptying one text array turns a red run green while the output
# still says forty-two facts were checked." And one level deeper: "emptiness
# is not the whole of it either: text: [""] is non-empty and still vacuous,
# because "" in source is true of every source there has ever been." Plus
# `why`, called out in its own sentence: "A why of "" is the same defect
# wearing #150's clothes - the key is present, the type is right, and the
# report prints no reason at all."
#
# Every case here is deliberately *not* an empty-facts, missing-key, or
# wrong-type case - those are covered elsewhere in this file - so a guard
# that only checks "is this a non-empty list of strings" without checking
# each string's own contents would still let `text: [""]` and `why: ""`
# through, which is exactly the gap SPEC.md draws out as a separate
# sentence ("emptiness is not the whole of it either").
# ---------------------------------------------------------------------------


def test_an_empty_text_list_on_contains_all_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """`text: []` makes `all(t in source for t in [])` vacuously true for any

    source at all, which is `contains_all`'s own vacuous-pass shape.
    """
    fact = well_formed_fact(kind="contains_all", text=[])
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_an_empty_text_list_on_absent_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """The same vacuity, on `absent`'s own list: `any(t in source for t in [])`

    is vacuously false, so an absence fact with nothing in `text` reports
    "still absent" against any source, for ever.
    """
    fact = well_formed_fact(kind="absent", text=[])
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_a_text_list_containing_only_an_empty_string_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """SPEC.md's own example: `text: [""]` is non-empty, so a length-only

    guard on the list waves it through, but `"" in source` holds for every
    source there has ever been - the assertion is vacuous even though the
    list itself is not.
    """
    fact = well_formed_fact(kind="contains_all", text=[""])
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_an_empty_names_list_on_methods_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """`names: []` is `methods`'s own vacuous-pass shape: no method name is

    ever checked against the class, so the fact holds regardless of what
    the class actually declares.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "methods", "class": "Peer",
            "names": [], "why": "w"}
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_a_names_list_containing_only_an_empty_string_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """The same non-empty-but-vacuous shape as `text: [""]`, for `names`: an

    empty method name is not a method any class ever declares, so this
    checks nothing while still being a non-empty list of strings.
    """
    fact = {"id": "F1", "file": "mod.py", "kind": "methods", "class": "Peer",
            "names": [""], "why": "w"}
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_an_empty_why_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """SPEC.md 11.3: "A why of "" is the same defect wearing #150's clothes -

    the key is present, the type is right, and the report prints no reason
    at all." Distinct from the missing-`why` and wrong-type-`why` cases
    already covered above: this one is present, is a string, and is still
    vacuous.
    """
    fact = well_formed_fact(why="")
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[fact])


def test_a_non_empty_text_and_names_and_why_are_unaffected_by_these_guards(
    monkeypatch, tmp_path
):
    """Contrast case: ordinary, non-empty `text`, `names`, and `why` values

    must still run cleanly through to a fetch, not be caught by an
    overzealous version of any of the five guards above.
    """
    install_manifest(
        monkeypatch,
        tmp_path,
        facts=[
            well_formed_fact(kind="contains_all", text=["x"], why="a real reason"),
            {"id": "F2", "file": "mod.py", "kind": "methods", "class": "Peer",
             "names": ["full_name"], "why": "a real reason"},
        ],
    )
    calls = recording_fetch_tree(
        monkeypatch,
        tree={"mod.py": "x\nclass Peer:\n    def full_name(self):\n        pass\n"},
    )

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example/upstream", "v1.0.0")]


# ---------------------------------------------------------------------------
# SPEC.md 11.3, the bullet placed first: "a manifest with no facts in it is
# the check failing, not the check passing." Found by the security audit as
# a fail-open this branch itself introduced: `manifest["facts"]` used to
# raise for a manifest that lacked the key, which went red; routing every
# read through a validated local with a default turned a missing or emptied
# `facts` into a clean exit reporting zero facts checked. `Pinned facts
# still hold` is a required check on every pull request, so this is the one
# way this section could make the checker worse than it found it - a diff
# that empties or drops `facts` would report success while checking
# nothing.
#
# The exit status is asserted explicitly and separately from `> 1`/`!= 1`
# for the empty-list case, because the defect here was never a wrong
# sentence - it was a green run. A test that only checked the message could
# still pass against a manifest that says "malformed" and then exits 0
# anyway; nothing here does that, but the status is what actually matters.
# ---------------------------------------------------------------------------


def test_facts_absent_exits_above_one_never_exactly_one_never_zero(monkeypatch, tmp_path, capsys):
    manifest = {"upstream": "example/upstream", "verified_against": "v1.0.0"}
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(cpf, "MANIFEST", path)
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    assert excinfo.value.code != 0


def test_an_empty_facts_list_does_not_exit_zero(monkeypatch, tmp_path, capsys):
    """The load-bearing case: `facts: []` is the shape a diff that empties

    the key actually produces, and it is a well-typed list, so anything
    checking only "is `facts` a list" would wave it through. Asserted
    directly against `0`, not only `> 1`, since a status-only assertion that
    happened to read `!= 1` would still miss a checker that regressed to
    exiting `0` for this shape specifically while every other malformed case
    in this file kept exiting above 1.
    """
    output = assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=[])

    assert "0 fact(s) checked" not in output


def test_facts_not_a_list_exits_above_one_never_exactly_one(monkeypatch, tmp_path, capsys):
    """An integer, not a string: a string is iterable, and every one of its

    characters then fails the next check down (`a fact must be an object`),
    so a string fixture here would still exit above 1 even with the
    `isinstance(facts, list)` guard itself deleted, discriminating nothing.
    An integer is not iterable at all, so only this specific guard stands
    between it and an uncaught `TypeError`.
    """
    assert_manifest_is_malformed(monkeypatch, tmp_path, capsys, facts=42)


def test_a_top_level_manifest_that_is_a_list_exits_above_one_never_exactly_one(
    monkeypatch, tmp_path, capsys
):
    """One level up from every other case in this section: `manifest.get(...)`

    itself raises `AttributeError` for a JSON document whose top level
    parses to a list rather than an object, which is exit 1 uncaught - the
    drift status - unless something checks the top level's own shape before
    calling `.get()` on it at all.
    """
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    monkeypatch.setattr(cpf, "MANIFEST", path)
    forbid(monkeypatch, "fetch_tree")
    forbid(monkeypatch, "latest_tag")

    with pytest.raises(SystemExit) as excinfo:
        run_main(monkeypatch, [])

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1
    captured = capsys.readouterr()
    assert "malformed" in captured.out + captured.err


def test_a_well_formed_facts_list_is_unaffected_by_these_guards(monkeypatch, tmp_path, capsys):
    """Contrast case: a real, present, non-empty `facts` list must still run

    cleanly through to a fetch, not be caught by an overzealous version of
    any of the four guards above.
    """
    install_manifest(monkeypatch, tmp_path, facts=[well_formed_fact()])
    calls = recording_fetch_tree(monkeypatch, tree={"mod.py": "x"})

    status = run_main(monkeypatch, [])

    assert status == 0
    assert calls == [("example/upstream", "v1.0.0")]


# ---------------------------------------------------------------------------
# SPEC.md 11.3: the checker keeps what the manifest asked for, and refuses an
# archive that will not fit (issue #165).
#
# `fetch_tree(repo, ref, wanted)` reaches the network exactly once, through
# `urllib.request.urlopen`; that single call is faked with a synthetic
# gzip-tar archive built by `tarfile`, so nothing here makes a real request.
# ---------------------------------------------------------------------------


def build_archive(members: dict[str, bytes], prefix: str = "upstream-vX/") -> bytes:
    """A gzip tarball shaped like the one `codeload.github.com` serves:

    every member's path starts with a single `<repo>-<ref>/` directory,
    which `fetch_tree` is known to strip (the existing tests elsewhere in
    this suite already exercise that path with real files; this only needs
    to produce the same shape, not re-verify the stripping).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as tf:
        for name, content in members.items():
            info = tarfile.TarInfo(name=f"{prefix}{name}")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class FakeArchiveResponse:
    """Stands in for the object `urllib.request.urlopen` returns.

    `fetch_tree` is known to bound the compressed download with a
    `read(MAX_ARCHIVE_BYTES + 1)`-shaped guard (issue #165's own report), so
    `read(n)` here mimics a real response by honouring `n` rather than always
    returning everything regardless of what was asked for.
    """

    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int = -1) -> bytes:
        return self._data if n is None or n < 0 else self._data[:n]

    def __enter__(self) -> "FakeArchiveResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def install_archive(monkeypatch, data: bytes) -> None:
    monkeypatch.setattr(cpf.urllib.request, "urlopen", lambda *a, **kw: FakeArchiveResponse(data))


def test_the_member_count_constant_is_the_real_one(monkeypatch):
    """The next test patches `MAX_MEMBERS` down to keep the archive it builds

    small - 100,001 real tar entries cost the better part of this file's
    runtime on its own, on the directory `CLAUDE.md` already names as
    dominating the local run, to prove a bound whose number does not
    otherwise matter. This is the guard that patch cannot hide behind: the
    constant the shipped code actually reads must still be the real one.
    """
    assert cpf.MAX_MEMBERS == 100_000


def test_an_archive_with_too_many_members_is_refused_above_one(monkeypatch):
    """`getmembers()` would materialise the whole index before any per-member

    cap could apply; the archive here has one member over `MAX_MEMBERS`,
    each a single byte, so the only way this can fail is the count itself,
    not either byte-size limit. `MAX_MEMBERS` is patched down to 5 so the
    archive itself only needs 6 members - the number is arbitrary, and the
    previous version of this test built one with the real 100,001, which
    alone cost 28 of this file's roughly 32 seconds.
    """
    monkeypatch.setattr(cpf, "MAX_MEMBERS", 5)
    archive = build_archive({f"f{i}.py": b"x" for i in range(cpf.MAX_MEMBERS + 1)})
    install_archive(monkeypatch, archive)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", {"f0.py"})

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_the_total_kept_byte_cap_constant_is_the_real_one(monkeypatch):
    """The next test patches `MAX_TOTAL_BYTES` down, for the same reason as

    the other three constants above: at the real value it was building
    several thousand tar entries to reach it. This is the guard that patch
    cannot hide behind.
    """
    assert cpf.MAX_TOTAL_BYTES == 20 * 1024 * 1024


def test_an_archive_whose_kept_total_is_too_large_is_refused_above_one(monkeypatch):
    """Individually small, collectively enormous - issue #165's own phrase.

    `MAX_TOTAL_BYTES` is patched down so the archive only needs a few
    hundred members to cross it, rather than several thousand at the real
    20 MiB. Each member stays nowhere near the (also real) `MAX_MEMBER_BYTES`
    and the member count stays a small fraction of `MAX_MEMBERS`; only their
    sum exceeds the patched total. Sized off the constants rather than a
    literal per-member size: a few members at just under the per-member cap
    would also cross a total, but that shape tests "one member near its own
    limit, a handful of times" more than it tests "many small members add
    up", which is the actual attack SPEC.md 11.3 describes and issue #165
    was opened about. All members are in `wanted`, so none of them are being
    discarded by the wanted-only filter either.
    """
    monkeypatch.setattr(cpf, "MAX_TOTAL_BYTES", 8192)
    member_size = 64
    count = (cpf.MAX_TOTAL_BYTES // member_size) + 5
    assert member_size < cpf.MAX_MEMBER_BYTES
    assert count < cpf.MAX_MEMBERS
    names = [f"f{i}.py" for i in range(count)]
    archive = build_archive({name: b"x" * member_size for name in names})
    install_archive(monkeypatch, archive)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", set(names))

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_a_member_the_manifest_does_not_name_is_never_kept(monkeypatch):
    """The rule that does the real work, in its own right and not only as a

    precondition for the total-bytes bound above: an unwanted member is
    absent from the result even though it exists in the archive.
    """
    archive = build_archive({"wanted.py": b"kept = 1", "unwanted.py": b"discarded = 1"})
    install_archive(monkeypatch, archive)

    files = cpf.fetch_tree("example/upstream", "v1.0.0", {"wanted.py"})

    assert files == {"wanted.py": "kept = 1"}
    assert "unwanted.py" not in files


def test_the_member_byte_cap_constant_is_the_real_one(monkeypatch):
    """The next test patches `MAX_MEMBER_BYTES` down to keep its 40-member

    archive small - at the real 4 MiB it was allocating roughly 160 MiB per
    run, on the same strained directory the member-count fix above already
    named. This is the guard that patch cannot hide behind.
    """
    assert cpf.MAX_MEMBER_BYTES == 4 * 1024 * 1024


def test_an_archive_full_of_unwanted_members_still_returns_only_what_was_wanted(monkeypatch):
    """The positive-control companion to the too-large-kept-total case above:

    a 40-member archive succeeds cleanly, with an empty result, once none of
    its members are in `wanted`. `MAX_MEMBER_BYTES` is patched down so each
    member only needs to be near-cap-sized relative to the *patched* value,
    not the real 4 MiB one - the property under test, many near-cap members
    that are never asked for, does not depend on what the cap's number is.

    Named for exactly what this asserts, not for the memory claim an earlier
    version of this test's name made: `files == {}` cannot tell a checker
    that discards unwanted content while reading it apart from one that
    decodes and keeps every member and only *counts* the wanted ones when
    deciding whether to raise - both return the same empty dict here, and
    only the second burns the memory the old name said neither did. Reaching
    that property needs an assertion on what the reader actually holds
    in-flight, not on its return value, which this file has no seam for
    without instrumenting `fetch_tree` itself - out of reach here since that
    would mean reading, and shaping this test around, the implementation the
    brief for this suite says not to read.
    """
    monkeypatch.setattr(cpf, "MAX_MEMBER_BYTES", 1024)
    member_size = cpf.MAX_MEMBER_BYTES - 1
    archive = build_archive({f"f{i}.py": b"x" * member_size for i in range(40)})
    install_archive(monkeypatch, archive)

    files = cpf.fetch_tree("example/upstream", "v1.0.0", set())

    assert files == {}


def test_an_oversized_member_is_refused_not_truncated(monkeypatch):
    """SPEC.md 11.3: "an oversized member is refused, not truncated" - the

    ambiguity this file's report originally flagged is now resolved in the
    spec, with the reason stated there: a silently truncated file is one
    this checker goes on to ask `contains_all`/`absent` questions of, so a
    pinned string sitting past the cut would report as gone from upstream
    when nothing moved, exactly the false-drift failure mode §11.3 as a
    whole exists to prevent. One byte over `MAX_MEMBER_BYTES`, well under
    the member-count and kept-total bounds, so only the per-member cap can
    be doing the refusing.
    """
    archive = build_archive({"huge.py": b"x" * (cpf.MAX_MEMBER_BYTES + 1)})
    install_archive(monkeypatch, archive)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", {"huge.py"})

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_a_member_exactly_at_the_cap_is_kept_whole_and_unmangled(monkeypatch):
    """The contrast case the refusal must not overreach into: a member sized

    exactly `MAX_MEMBER_BYTES`, one byte under the refused case above, is
    not itself oversized and must come back complete and byte-for-byte
    correct - not refused, and not truncated by an off-by-one in the new
    refusal either.
    """
    # Printable ASCII only, not repeated 'x': `fetch_tree` decodes as UTF-8, so
    # the pattern has to be valid UTF-8 on its own, and a varying pattern (not
    # a single repeated byte) is what would expose a truncation or an
    # off-by-one landing mid-content rather than exactly at the boundary.
    pattern = bytes(range(32, 127))
    content = (pattern * (cpf.MAX_MEMBER_BYTES // len(pattern) + 1))[: cpf.MAX_MEMBER_BYTES]
    assert len(content) == cpf.MAX_MEMBER_BYTES
    archive = build_archive({"exact.py": content})
    install_archive(monkeypatch, archive)

    files = cpf.fetch_tree("example/upstream", "v1.0.0", {"exact.py"})

    assert files["exact.py"].encode("ascii") == content


def test_the_archive_byte_cap_constant_is_the_real_one(monkeypatch):
    """The next test patches `MAX_ARCHIVE_BYTES` down rather than build a real

    64 MiB payload on every run, for the same reason the member-count and
    member-byte-cap fixtures above were rewritten this way. This is the
    guard that patch cannot hide behind.
    """
    assert cpf.MAX_ARCHIVE_BYTES == 64 * 1024 * 1024


def test_the_existing_archive_byte_cap_still_refuses_an_oversized_download(monkeypatch):
    """The other pre-existing limit (#165 acceptance, same sentence):

    `MAX_ARCHIVE_BYTES` bounds the compressed download itself. A payload one
    byte over it, with no members at all, must still be refused.
    """
    monkeypatch.setattr(cpf, "MAX_ARCHIVE_BYTES", 1024)
    oversized = b"\x00" * (cpf.MAX_ARCHIVE_BYTES + 1)
    install_archive(monkeypatch, oversized)

    with pytest.raises(SystemExit) as excinfo:
        cpf.fetch_tree("example/upstream", "v1.0.0", {"anything.py"})

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


# ---------------------------------------------------------------------------
# `latest_tag`'s return value is the only network-read value that reaches
# URL construction (`urllib.parse.quote(ref, ...)` in `fetch_tree`, once the
# resolved tag is passed along as `ref`): a non-string `tag_name` from the
# GitHub API raises `TypeError` there, uncaught, and exits 1 - the drift
# status - for a malformed response rather than any upstream fact actually
# moving. `urllib.request.urlopen` is the one function `latest_tag` itself
# calls, faked here the same way `install_archive()` fakes it for
# `fetch_tree`, so nothing in this section reaches the network either.
# ---------------------------------------------------------------------------


class FakeJSONResponse:
    def __init__(self, payload: object):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *args, **kwargs) -> bytes:
        return self._body

    def __enter__(self) -> "FakeJSONResponse":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


def install_release(monkeypatch, payload: object) -> None:
    monkeypatch.setattr(
        cpf.urllib.request, "urlopen", lambda *a, **kw: FakeJSONResponse(payload)
    )


@pytest.mark.parametrize(
    "tag_name",
    [None, 42, ["v1.0.0"], {"tag": "v1.0.0"}],
    ids=["none", "int", "list", "dict"],
)
def test_a_non_string_tag_name_exits_above_one_never_exactly_one(monkeypatch, tag_name):
    """Each of these reaches `urllib.parse.quote` in the unguarded version and

    raises `TypeError` there, which is exit 1 uncaught - the same class of
    defect #167 fixed for `upstream`, one call further along.
    """
    install_release(monkeypatch, {"tag_name": tag_name})

    with pytest.raises(SystemExit) as excinfo:
        cpf.latest_tag("example/upstream")

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_a_missing_tag_name_exits_above_one_never_exactly_one(monkeypatch):
    """A response with no `tag_name` key at all - the `KeyError` case,

    distinct from a present-but-wrong-typed one.
    """
    install_release(monkeypatch, {"name": "v1.0.0 release"})

    with pytest.raises(SystemExit) as excinfo:
        cpf.latest_tag("example/upstream")

    assert excinfo.value.code > 1
    assert excinfo.value.code != 1


def test_a_well_formed_tag_name_is_returned_unaffected_by_the_type_check(monkeypatch):
    """Contrast case: the ordinary shape, a string `tag_name`, must still

    come back from `latest_tag` exactly as given.
    """
    install_release(monkeypatch, {"tag_name": "v2.3.4"})

    assert cpf.latest_tag("example/upstream") == "v2.3.4"
