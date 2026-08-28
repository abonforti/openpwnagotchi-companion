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
import importlib.util
import json
import re
import sys
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
        ({"id": "h", "file": "mod.py", "kind": "absent", "text": ["Peer"]},
         "section 11.1 forbids"),
        ({"id": "i", "file": "gone.py", "kind": "contains_all", "text": ["x"]},
         "no longer exists upstream"),
    ],
    ids=["renamed", "signature", "duplicate-cleaned-up", "forbidden-method",
         "class-gone", "text-gone", "wrong-count", "forbidden-name-returned",
         "file-gone"],
)
def test_a_fact_that_no_longer_holds_is_reported(fact, expected):
    """The half that matters. Each of these is a drift that reached a device once."""
    problem = cpf.check(fact, TREE)
    assert problem is not None, "drift went unreported"
    assert expected in problem, problem


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
# ---------------------------------------------------------------------------


def test_a_string_where_a_list_was_meant_is_rejected():
    """The vacuous pass this validation exists for.

    `"text": "requires-python"` iterates the characters of the string, and every
    one of them appears in any file, so the fact passes for ever while checking
    nothing. It has to be an error, not a green run.
    """
    problem = cpf.validate({"id": "x", "file": "a.py", "kind": "contains_all",
                            "text": "requires-python"})

    assert problem is not None and "must be a list" in problem


@pytest.mark.parametrize(
    "fact",
    [
        {"id": "x", "file": "a.py", "kind": "nonsense"},
        {"id": "x", "kind": "contains_all", "text": ["a"]},
        {"id": "x", "file": "a.py", "kind": "function"},
        {"id": "x", "file": "a.py", "kind": "function_count", "name": "a"},
        {"id": "x", "file": "a.py", "kind": "methods", "class": "A", "names": "full_name"},
        {"id": "x", "file": "a.py", "kind": "contains_count", "text": ["a"], "count": 1},
    ],
    ids=["unknown-kind", "no-file", "no-name", "no-count", "names-not-a-list",
         "count-text-not-a-string"],
)
def test_a_malformed_entry_is_rejected(fact):
    assert cpf.validate(fact) is not None


def test_every_entry_in_the_real_manifest_is_well_formed():
    """The validator is worth nothing if the file it guards was never run past it."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    problems = [cpf.validate(fact) for fact in manifest["facts"]]

    assert [p for p in problems if p] == []


def test_the_manifest_covers_every_fact_section_11_pins():
    """The two allowlists must not drift apart, which is this tool's own failure mode.

    SPEC section 11 is the human record and this manifest is the machine-checked
    subset. Someone editing section 11 has no reason to know a second copy
    exists, so the link is asserted here rather than hoped for.

    F12 and F29 live in another repository and F22 describes the project this
    forks, none of which can be checked against upstream; the manifest's own
    note says so, and this test pins that list rather than letting it grow
    quietly.
    """
    not_checkable = {"F12", "F22", "F29"}
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

    `facts` defaults to empty: the modes under test care about which tag gets
    fetched and which exit status comes back, not about drift detection,
    which the rest of this file already covers via `check()` directly.
    """
    manifest = {"upstream": "example/upstream", "verified_against": "v1.0.0", "facts": []}
    manifest.update(overrides)
    path = tmp_path / "pinned_symbols.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def install_manifest(monkeypatch, tmp_path: Path, **overrides) -> Path:
    path = write_manifest(tmp_path, **overrides)
    monkeypatch.setattr(cpf, "MANIFEST", path)
    return path


def recording_fetch_tree(monkeypatch, tree: dict | None = None) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(repo: str, ref: str) -> dict[str, str]:
        calls.append((repo, ref))
        return tree if tree is not None else {}

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


def test_missing_verified_against_is_the_check_failing_not_drift(monkeypatch, tmp_path):
    """SPEC 11.2: this exits above 1, distinct from the exit 1 a real drifted

    fact produces, so `upstream-drift.yml` goes red instead of filing a
    ticket claiming upstream moved when nothing did. Neither network
    function may run: there is no tag to check anything against.
    """
    manifest = {"upstream": "example/upstream", "facts": []}
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
        facts=[{"id": "F1", "file": "mod.py", "kind": "function", "name": "gone"}],
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


TOKEN_PROBE_ARGV = [[], ["--latest"], ["--ref", "candidate-3"]]

_LABEL_RE = re.compile(r"\(([^)]*)\):")


@pytest.mark.parametrize("argv", TOKEN_PROBE_ARGV, ids=["verified", "latest", "asked"])
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
    for argv in TOKEN_PROBE_ARGV:
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


