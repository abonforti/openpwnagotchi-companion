"""`.github/check_secrets.py`'s pattern for issue #211 (SPEC.md 5.1.2, "A
credential is not always at the start of a line"): a second, value-shaped
pattern alongside the existing column-0-anchored `npm registry credential`.

At the time this module was written, `PATTERNS` carried no such entry yet -
this is written from SPEC.md alone, against the interface the existing
`npm registry credential` / `credential assignment` pair already
establishes (label, `re.Pattern`, discovered through `PATTERNS` rather than
imported by name), not against a pattern that has landed. `_KNOWN_LABELS`
below is the label set `PATTERNS` carries *before* #211 lands, read off the
tracked script only to identify "what #211 added" without guessing a label
that has not been chosen yet - not to read or shape the new pattern itself,
which does not exist yet to read.

SPEC.md 5.1.2: "the second pattern is about the value, not the position...
It matches the key anywhere on the line and requires what follows to look
like a credential rather than a placeholder: a run of at least sixteen
characters from the alphabet real tokens use, with no spaces, no angle
brackets, and none of the words a document uses to mean 'put yours here'."
And: issue #211's own four measured escapes - a list literal, a bare
assignment, a comment, and a bare quoted line at column 0 - "now behave
correctly", while the anchored `npm registry credential` pattern is
unchanged and still fires on its own column-0, unquoted case, and while a
placeholder value (`<your token>`) still does not match either pattern.

Every matching probe below is assembled from separate pieces at runtime,
never written as one contiguous literal, for the same reason
`tests/test_check_secrets.py` gives its own probes: this module is tracked,
`test_scanner_is_clean_over_the_tracked_tree` in that module runs the real
scanner over the whole tree including this file, and a matching credential
shape written out whole here would be indistinguishable from a real one
pasted in by mistake.
"""
from __future__ import annotations

import importlib.util
import string
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "check_secrets.py"

# The label set PATTERNS carries before issue #211's new entry lands -
# read off the tracked script (not authored here) purely to compute the
# diff, not to read or copy the new pattern's own definition.
_KNOWN_LABELS = {
    "PEM private key",
    "GitHub token",
    "GitHub fine-grained PAT",
    "AWS access key id",
    "Slack token",
    "Google API key",
    "npm access token",
    "npm registry credential",
    "private key in a URL",
    "credential assignment",
    # Issue #7 landed after #211 and added two content patterns of its own.
    # They are part of the baseline this module subtracts, not the entry it
    # is looking for: the canary below finds #211's pattern by being the one
    # label left over, and with these unlisted it would find three.
    "pwnagotchi whitelist entry",
    "GPS coordinate pair",
}


def _load_check_secrets():
    spec = importlib.util.spec_from_file_location("check_secrets_shape", SCRIPT)
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


NPM_REGISTRY_CREDENTIAL = _pattern("npm registry credential")


def _new_patterns_beyond_known_labels():
    return [(label, pattern) for label, pattern in cs.PATTERNS if label not in _KNOWN_LABELS]


def test_exactly_one_new_pattern_entry_was_added_for_issue_211():
    """Canary for every test below: if this fails with zero entries, #211's
    pattern has not landed yet and every other test in this module is
    exercising nothing. If it fails with more than one, the discovery below
    is ambiguous and the tests that follow need a label, not a guess.
    """
    new_entries = _new_patterns_beyond_known_labels()
    labels = [label for label, _ in new_entries]
    assert len(new_entries) == 1, (
        f"expected exactly one PATTERNS entry beyond the pre-#211 set, got {labels!r} - "
        f"either issue #211 has not landed yet, or this module's _KNOWN_LABELS baseline "
        f"is stale and needs updating alongside it"
    )


_new_entries = _new_patterns_beyond_known_labels()
NEW_PATTERN_LABEL, NEW_PATTERN = _new_entries[0] if _new_entries else (None, None)


def _synthetic_credential_value(length=16):
    """A deterministic, obviously-synthetic run of `length` alphanumeric
    characters - real tokens' alphabet, no resemblance to one actually
    issued anywhere.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(alphabet[i % len(alphabet)] for i in range(length))


def _kv(key, value):
    """Assembles `key=value` from two pieces at runtime, so the shape never
    sits as one contiguous literal in this module's own tracked source.
    """
    return f"{key}" + "=" + f"{value}"


def _kv_colon(key, value):
    """Assembles `key:value` from two pieces at runtime - the separator
    SPEC.md 5.1.2 adds alongside `=` ("A colon is an assignment too"), for
    the same reason `_kv` above builds `=`.
    """
    return f"{key}" + ":" + f"{value}"


def _kv_json(key, value):
    """Assembles `{"key": "value"}` from separate pieces at runtime - the
    JSON-object shape SPEC.md 5.1.2 names directly ("the form a token most
    often arrives in ... from a config file or a paste out of a
    lockfile"), and the shape that most plainly needs the `:` separator
    rather than `=`.
    """
    return "{" + '"' + f"{key}" + '": "' + f"{value}" + '"}'


def _list_literal_line(key="_authToken", value=None):
    value = value if value is not None else _synthetic_credential_value()
    return "    " + '"' + _kv(key, value) + '"' + ","


def _assignment_line(key="_authToken", value=None):
    value = value if value is not None else _synthetic_credential_value()
    return "probe" + " = " + '"' + _kv(key, value) + '"'


def _comment_line(key="_authToken", value=None):
    value = value if value is not None else _synthetic_credential_value()
    return "#" + " " + _kv(key, value)


def _bare_quoted_line(key="_authToken", value=None):
    value = value if value is not None else _synthetic_credential_value()
    return '"' + _kv(key, value) + '"'


ESCAPING_SHAPES = [
    pytest.param(_list_literal_line, id="list-literal"),
    pytest.param(_assignment_line, id="assignment"),
    pytest.param(_comment_line, id="comment"),
    pytest.param(_bare_quoted_line, id="bare-quoted-column-0"),
]


# ---------------------------------------------------------------------------
# The four shapes issue #211 measured as escaping the anchored pattern
# today. SPEC.md 5.1.2 says each "now behave[s] correctly" - i.e. the new
# pattern fires on all four, and the old anchored pattern still does not
# (it is unchanged, and issue #211's whole premise is that it cannot see
# these shapes: a character sits before the key in every one of them).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("build_line", ESCAPING_SHAPES)
def test_new_pattern_fires_on_each_shape_issue_211_measured_as_escaping(build_line):
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = build_line()
    assert NEW_PATTERN.search(line), f"expected the new pattern to fire on {line!r}"


@pytest.mark.parametrize("build_line", ESCAPING_SHAPES)
def test_anchored_pattern_still_does_not_fire_on_any_of_the_four_shapes(build_line):
    """Two patterns with different failure modes is the design (SPEC.md
    5.1.2): a test asserting only that "something matched" cannot tell
    which one did. This pins that the anchored pattern's own limit is
    unchanged by the new pattern's addition - it is still exactly the
    shapes described in its own comment that escape it.
    """
    line = build_line()
    assert not NPM_REGISTRY_CREDENTIAL.search(line), (
        f"the anchored 'npm registry credential' pattern is not supposed to fire on "
        f"{line!r} - a character sits before the key in every one of these four shapes, "
        f"which is exactly what issue #211 found it missing"
    )


# ---------------------------------------------------------------------------
# The anchored pattern is unchanged and still fires on its own case: the
# plain, unquoted, unprefixed column-0 assignment its own comment says
# "still fire[s]" for.
# ---------------------------------------------------------------------------


def test_anchored_pattern_still_fires_on_its_own_unquoted_column_0_case():
    line = _kv("_authToken", _synthetic_credential_value())
    assert NPM_REGISTRY_CREDENTIAL.search(line), (
        f"expected the anchored 'npm registry credential' pattern to still fire on "
        f"its own unquoted, unprefixed column-0 case: {line!r}"
    )


# ---------------------------------------------------------------------------
# "A colon is an assignment too" (SPEC.md 5.1.2): the value-shaped pattern
# accepts `:` alongside `=`, because that is how JSON, YAML and a JavaScript
# object literal write the same assignment - `{"_authToken": "..."}`,
# `_authToken: Ab3x...` - and this is the form a token most often actually
# arrives in. The anchored pattern is unchanged and does not gain the colon:
# "it is a deliberate, documented concession and widening it is what §5.1.2
# already refused."
# ---------------------------------------------------------------------------


def test_new_pattern_fires_with_a_colon_separator_not_only_equals():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = _kv_colon("_authToken", _synthetic_credential_value())
    assert NEW_PATTERN.search(line), (
        f"expected the new pattern to fire on a colon-separated line: {line!r}"
    )


def test_new_pattern_fires_on_the_json_object_shape():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = _kv_json("_authToken", _synthetic_credential_value())
    assert NEW_PATTERN.search(line), (
        f"expected the new pattern to fire on the JSON-object shape: {line!r}"
    )


def test_anchored_pattern_does_not_fire_on_a_colon_separated_line_the_concession_is_unchanged():
    """Pins the stated asymmetry directly: the new pattern gained `:`, the
    anchored one did not. A line built exactly like the anchored pattern's
    own unquoted, unprefixed column-0 case above, with `:` swapped in for
    `=`, must still not match it - if it did, the anchored pattern would
    have silently widened alongside the new one, which SPEC.md 5.1.2 is
    explicit was never the intent.
    """
    line = _kv_colon("_authToken", _synthetic_credential_value())
    assert not NPM_REGISTRY_CREDENTIAL.search(line), (
        f"the anchored 'npm registry credential' pattern requires '=' and must not have "
        f"quietly gained ':' too: {line!r}"
    )


def test_anchored_pattern_does_not_fire_on_the_json_object_shape():
    line = _kv_json("_authToken", _synthetic_credential_value())
    assert not NPM_REGISTRY_CREDENTIAL.search(line), (
        f"the anchored 'npm registry credential' pattern requires the key at the start of "
        f"the line and must not fire on the JSON-object shape: {line!r}"
    )


# ---------------------------------------------------------------------------
# Placeholder shapes must not match the new pattern - a scanner that fires
# on documentation gets turned off (SPEC.md 5.1.2).
# ---------------------------------------------------------------------------


def test_new_pattern_does_not_fire_on_the_explicit_placeholder_spec_names():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    # SPEC.md 5.1.2's own example, verbatim: "_authToken=<your token> does
    # not match, because a placeholder announces itself".
    line = _kv("_authToken", "<your token>")
    assert not NEW_PATTERN.search(line), (
        f"expected no match on SPEC.md 5.1.2's own placeholder example: {line!r}"
    )


def test_new_pattern_does_not_fire_on_a_placeholder_value_containing_a_space():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = _kv("_authToken", "a placeholder value")
    assert not NEW_PATTERN.search(line), f"expected no match on a spaced value: {line!r}"


def test_new_pattern_does_not_fire_on_a_value_containing_angle_brackets():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    value = "<" + _synthetic_credential_value(14) + ">"
    line = _kv("_authToken", value)
    assert not NEW_PATTERN.search(line), f"expected no match on a bracketed value: {line!r}"


PLACEHOLDER_WORDS = ["your", "example", "changeme", "redacted", "placeholder"]


def _word_adjacent_via_underscore(word, filler_len=None):
    """`word` immediately followed by token-alphabet filler joined with an
    underscore, not a hyphen. SPEC.md 5.1.2, describing the defect this
    containment rule replaced: "The first version anchored the excluded
    words with \\b, which holds against a hyphen and not against an
    underscore - `_` is inside the token alphabet, so
    `YOUR_TOKEN_HERE_XXXX` fired while `your-token-here-xxxx` did not." A
    probe joined with a hyphen cannot exercise that failure mode at all - it
    passes whether the exclusion is genuinely boundary-free or still
    `\\b`-anchored underneath, which is exactly why the module's own first
    version of this test, built that way, could not tell the two apart. This
    one is built with the character that actually broke it.
    """
    default_len = max(16 - len(word) - 1, 4)
    filler = _synthetic_credential_value(filler_len if filler_len is not None else default_len)
    return word + "_" + filler


# SPEC.md 5.1.2: "Every excluded word gets its own probe, shaped so it
# passes the length and alphabet tests and is refused only by the
# exclusion: a probe that fails on shape first proves nothing about the
# word list, and a word list with one probe standing for all of it is a
# list where deleting eight entries is invisible." One parametrized test
# per word, not one probe standing for the set of five.
@pytest.mark.parametrize("word", PLACEHOLDER_WORDS, ids=PLACEHOLDER_WORDS)
def test_new_pattern_does_not_fire_on_each_placeholder_word_adjacent_via_underscore(word):
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    value = _word_adjacent_via_underscore(word)
    assert len(value) >= 16, f"probe itself must clear the length floor: {value!r}"
    line = _kv("_authToken", value)
    assert not NEW_PATTERN.search(line), (
        f"expected no match on a value containing the placeholder word {word!r}, joined to "
        f"filler with an underscore rather than a hyphen: {line!r}"
    )


# The other half of "anywhere in it, not only at its start" (SPEC.md
# 5.1.2): the word sits in the middle of the value, with filler on both
# sides, rather than adjacent to either edge.
@pytest.mark.parametrize("word", PLACEHOLDER_WORDS, ids=PLACEHOLDER_WORDS)
def test_new_pattern_does_not_fire_on_each_placeholder_word_anywhere_in_the_value(word):
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    value = _synthetic_credential_value(6) + word + _synthetic_credential_value(6)
    assert len(value) >= 16, f"probe itself must clear the length floor: {value!r}"
    line = _kv("_authToken", value)
    assert not NEW_PATTERN.search(line), (
        f"expected no match on a value containing the placeholder word {word!r} in its "
        f"middle, not only at an edge: {line!r}"
    )


# ---------------------------------------------------------------------------
# SPEC.md 5.1.2: "`none`, `null`, `true` and `false` come out of the list.
# They cannot be reached: the shortest is four characters against a
# sixteen-character floor, so as alternatives they were dead the moment the
# floor was written." This is a claim about *this* pattern's word list, and
# is not the same list the older, eight-character-floor `credential
# assignment` pattern still carries them on (that pattern's own probes are
# in tests/test_check_secrets.py). Pinned by the negative: a value
# containing one of the four must still match, proving none of them is
# quietly back in this pattern's own exclusion.
# ---------------------------------------------------------------------------

UNREACHABLE_WORDS = ["none", "null", "true", "false"]


@pytest.mark.parametrize("word", UNREACHABLE_WORDS, ids=UNREACHABLE_WORDS)
def test_new_pattern_still_fires_on_a_value_containing_a_word_the_list_dropped(word):
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    value = _synthetic_credential_value(6) + word + _synthetic_credential_value(6)
    assert len(value) >= 16, f"probe itself must clear the length floor: {value!r}"
    line = _kv("_authToken", value)
    assert NEW_PATTERN.search(line), (
        f"expected a match on a value containing {word!r} - none of none/null/true/false "
        f"is excluded by this pattern's own word list (SPEC.md 5.1.2): {line!r}"
    )


# ---------------------------------------------------------------------------
# Length boundary: "a run of at least sixteen characters" (SPEC.md 5.1.2).
# ---------------------------------------------------------------------------


def test_new_pattern_fires_on_a_sixteen_character_value_the_stated_floor():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = _kv("_authToken", _synthetic_credential_value(16))
    assert NEW_PATTERN.search(line), f"expected a match at the stated 16-character floor: {line!r}"


def test_new_pattern_does_not_fire_on_a_fifteen_character_value_one_below_the_floor():
    if NEW_PATTERN is None:
        pytest.skip("issue #211's pattern has not landed yet")
    line = _kv("_authToken", _synthetic_credential_value(15))
    assert not NEW_PATTERN.search(line), (
        f"expected no match one character below the stated 16-character floor: {line!r}"
    )


# ---------------------------------------------------------------------------
# The rule enforced elsewhere in this repository: a test for a pattern
# cannot spell the pattern's own probe out contiguously. This module's own
# probes are all built from separate pieces at runtime (see _kv and the
# *_line builders above), so nothing here needs registering the way
# tests/test_check_secrets.py registers PROBES for its own equivalent
# check - there is no module-level literal to enumerate. This test reads
# this module's own bytes to confirm that holds, the same technique
# tests/test_check_secrets.py uses for its module.
# ---------------------------------------------------------------------------


def test_no_matching_probe_value_appears_contiguously_in_this_modules_own_source():
    source = Path(__file__).read_bytes().decode("utf-8")
    candidates = [
        _kv("_authToken", _synthetic_credential_value(16)),
        _kv("_authToken", _synthetic_credential_value(15)),
        _kv_colon("_authToken", _synthetic_credential_value(16)),
        _kv_json("_authToken", _synthetic_credential_value(16)),
        _list_literal_line(),
        _assignment_line(),
        _comment_line(),
        _bare_quoted_line(),
    ]
    candidates.extend(_word_adjacent_via_underscore(word) for word in PLACEHOLDER_WORDS)
    candidates.extend(
        _kv("_authToken", _synthetic_credential_value(6) + word + _synthetic_credential_value(6))
        for word in PLACEHOLDER_WORDS
    )
    candidates.extend(
        _kv("_authToken", _synthetic_credential_value(6) + word + _synthetic_credential_value(6))
        for word in UNREACHABLE_WORDS
    )
    offenders = sorted({value for value in candidates if value in source})
    assert not offenders, (
        f"probe value(s) appear as one contiguous literal in this module's own tracked "
        f"source: {offenders!r}"
    )
