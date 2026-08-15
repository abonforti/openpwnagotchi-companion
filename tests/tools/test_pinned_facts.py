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

import importlib.util
import json
import re
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
    nested = {"mod.py": "if True:\n    class Peer:\n        def full_name(self):\n            pass\n"}
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

    F12 lives in another repository and F22 describes the project this forks,
    neither of which can be checked against upstream; the manifest's own note
    says so, and this test pins that list rather than letting it grow quietly.
    """
    not_checkable = {"F12", "F22"}
    spec = (REPO_ROOT / "SPEC.md").read_text(encoding="utf-8")
    pinned = set(re.findall(r"^\| (F\d+) \|", spec, re.M))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    # F9 is split into F9a/F9b/F9c, F16 into F16/F16b: strip the suffix.
    covered = {fact["id"].rstrip("abc") for fact in manifest["facts"]}

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

    invented = {fact["id"].rstrip("abc") for fact in manifest["facts"]} - pinned

    assert not invented, f"pinned_symbols.json checks {sorted(invented)}, not in SPEC section 11"
