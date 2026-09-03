"""Peer mapping, and the timestamp asymmetry that hides inside it.

`agent._peers` holds `Peer` objects, not dicts. Three of their four timestamps
are `datetime` objects and the fourth, `last_seen`, is already a `time.time()`
float - and `Peer.__init__` falls back to leaving a plain `str` in place when it
cannot parse one (SPEC F16). All four leave the plugin as epoch floats.

SPEC 2.8 calls this "the single most likely place for a silent bug", which is
why it gets its own tests rather than being folded into the field table.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from plugin import companion
from pwnagotchi.mesh.peer import Peer

FULL_ADVERTISEMENT = {
    "name": "TestPeer_001",
    "identity": "aabbccddeeff00112233445566778899",
    "version": "2.9.5.6",
    "face": "(⌐■_■)",
    "uptime": 4242,
    "epoch": 17,
    "pwnd_run": ["TestNet_001", "TestNet_002"],
    "pwnd_tot": 91,
}

FIRST_MET = datetime(2025, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
FIRST_SEEN = datetime(2025, 8, 15, 9, 0, 0, tzinfo=timezone.utc)
PREV_SEEN = datetime(2025, 8, 15, 9, 30, 0, tzinfo=timezone.utc)
LAST_SEEN = 1_755_250_000.0


def make_peer(**overrides):
    kwargs = {
        "first_met": FIRST_MET,
        "first_seen": FIRST_SEEN,
        "prev_seen": PREV_SEEN,
        "last_seen": LAST_SEEN,
        "encounters": 3,
        "last_channel": 11,
        "rssi": -61,
    }
    kwargs.update(overrides)
    advertisement = kwargs.pop("adv", FULL_ADVERTISEMENT)
    return Peer(advertisement, **kwargs)


# ---------------------------------------------------------------------------
# The field table of SPEC 2.8
# ---------------------------------------------------------------------------


def test_every_field_maps():
    mapped = companion.normalise_peer(make_peer())

    assert mapped["name"] == "TestPeer_001"
    assert mapped["fingerprint"] == "aabbccddeeff00112233445566778899"
    assert mapped["fullName"] == "TestPeer_001@aabbccddeeff00112233445566778899"
    assert mapped["rssi"] == -61
    assert mapped["channel"] == 11
    assert mapped["encounters"] == 3
    assert mapped["pwndRun"] == 2
    assert mapped["pwndTotal"] == 91
    assert mapped["version"] == "2.9.5.6"
    assert mapped["uptime"] == 4242
    assert mapped["face"] == "(⌐■_■)"


def test_the_wire_shape_has_exactly_the_schema_fields():
    expected = {
        "name", "fingerprint", "fullName", "rssi", "channel", "firstSeen",
        "prevSeen", "firstMet", "lastSeen", "encounters", "pwndRun",
        "pwndTotal", "version", "uptime", "face",
    }

    assert set(companion.normalise_peer(make_peer())) == expected


def test_the_channel_comes_from_last_channel():
    peer = make_peer(last_channel=6)
    # peer.channel does not exist (SPEC 11.1); reading it must not be how this
    # value is obtained.
    assert not hasattr(peer, "channel")

    assert companion.normalise_peer(peer)["channel"] == 6


def test_the_pwnd_counters_come_from_the_methods():
    peer = make_peer()
    assert not hasattr(peer, "pwnd_tot")

    mapped = companion.normalise_peer(peer)

    assert mapped["pwndRun"] == 2
    assert mapped["pwndTotal"] == 91


# ---------------------------------------------------------------------------
# The timestamp asymmetry
# ---------------------------------------------------------------------------


def test_last_seen_is_already_an_epoch_float_and_is_not_converted_again():
    mapped = companion.normalise_peer(make_peer())

    assert mapped["lastSeen"] == pytest.approx(LAST_SEEN)


def test_the_datetime_siblings_become_epoch_floats():
    mapped = companion.normalise_peer(make_peer())

    assert mapped["firstMet"] == pytest.approx(FIRST_MET.timestamp())
    assert mapped["firstSeen"] == pytest.approx(FIRST_SEEN.timestamp())
    assert mapped["prevSeen"] == pytest.approx(PREV_SEEN.timestamp())


def test_all_four_timestamps_leave_as_the_same_type():
    mapped = companion.normalise_peer(make_peer())

    types = {type(mapped[key]) for key in ("firstMet", "firstSeen", "prevSeen", "lastSeen")}
    assert types == {float}


def test_a_timestamp_left_as_a_string_by_peers_own_parse_fallback():
    # Peer.__init__ keeps the raw value when strptime fails, so first_met can be
    # a str on a real unit (SPEC F16).
    peer = make_peer(first_met="2025-08-01T12:00:00Z")

    mapped = companion.normalise_peer(peer)

    assert mapped["firstMet"] == pytest.approx(FIRST_MET.timestamp())


def test_an_unparseable_timestamp_string_becomes_null_not_a_crash():
    peer = make_peer(prev_seen="not a timestamp at all")

    mapped = companion.normalise_peer(peer)

    assert mapped["prevSeen"] is None
    assert mapped["lastSeen"] == pytest.approx(LAST_SEEN)


@pytest.mark.parametrize(
    "value,expected",
    [
        (FIRST_MET, FIRST_MET.timestamp()),
        (1_755_250_000.0, 1_755_250_000.0),
        (1_755_250_000, 1_755_250_000.0),
        ("2025-08-01T12:00:00+00:00", FIRST_MET.timestamp()),
        (None, None),
        ("", None),
        ("nonsense", None),
        ([], None),
    ],
)
def test_to_epoch_coerces_or_gives_up(value, expected):
    result = companion.to_epoch(value)

    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_to_epoch_refuses_a_bool_even_though_bool_is_an_int():
    # isinstance(True, int) is True in Python, so a bool would otherwise sail
    # through the int/float branch as 1.0/0.0 - a timestamp nobody wrote.
    assert companion.to_epoch(True) is None
    assert companion.to_epoch(False) is None


def test_to_epoch_survives_a_timestamp_that_raises():
    # datetime(1, 1, 1).timestamp() raises ValueError on every platform this
    # runs on - year 0 is before what mktime/gmtime can represent - which is
    # exactly the "timestamp() explodes" case, reached without faking a clock.
    assert companion.to_epoch(datetime(1, 1, 1)) is None


def test_to_epoch_treats_a_naive_iso_string_as_utc_not_local_time():
    # A string with no offset is what Peer's own fallback formatting produces.
    # The answer must not depend on the host's timezone, so this compares
    # against an explicitly UTC-tagged datetime rather than datetime.timestamp()
    # (which is local-time-based) - the two disagree on any host not set to UTC.
    naive = "2025-08-01T12:00:00"
    expected = datetime(2025, 8, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()

    assert companion.to_epoch(naive) == pytest.approx(expected)


def test_normalise_peer_of_something_with_no_peer_attributes_at_all_is_the_placeholder():
    # Every field reader is `getattr(peer, name)` wrapped in its own
    # try/except (SPEC 2.8): an object with none of Peer's attributes - not a
    # Peer at all - must degrade field by field rather than raise once and
    # lose the whole entry.
    mapped = companion.normalise_peer(42)

    assert mapped["name"] == "???"
    assert mapped["fingerprint"] == "???"
    assert mapped["rssi"] is None
    assert mapped["channel"] is None


# ---------------------------------------------------------------------------
# Missing advertisement keys
# ---------------------------------------------------------------------------


def test_missing_name_and_identity_fall_back_to_the_placeholder():
    mapped = companion.normalise_peer(Peer({}))

    assert mapped["name"] == "???"
    assert mapped["fingerprint"] == "???"
    assert mapped["fullName"] == "???@???"


def test_an_empty_advertisement_loses_fields_but_not_the_peer():
    # The accessors backed by adv keys raise when the key is absent, exactly as
    # the real Peer does. Every one is guarded individually, so a missing face
    # costs the face and nothing else.
    mapped = companion.normalise_peer(Peer({}, rssi=-70, last_channel=1))

    assert mapped["face"] is None
    assert mapped["version"] is None
    assert mapped["uptime"] is None
    assert mapped["pwndRun"] is None
    assert mapped["pwndTotal"] is None
    assert mapped["rssi"] == -70
    assert mapped["channel"] == 1


def test_one_exploding_accessor_does_not_lose_the_others():
    class Hostile(Peer):
        def face(self):
            raise RuntimeError("the advertisement is a lie")

    mapped = companion.normalise_peer(Hostile(FULL_ADVERTISEMENT, rssi=-55))

    assert mapped["face"] is None
    assert mapped["name"] == "TestPeer_001"
    assert mapped["rssi"] == -55


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


def test_peers_are_read_from_the_underscore_dict(router_factory, agent_factory):
    agent = agent_factory(peers={"one": make_peer(), "two": Peer({"name": "other"})})

    entries = router_factory(agent).peers()

    assert {entry["name"] for entry in entries} == {"TestPeer_001", "other"}


def test_no_peers_is_an_empty_list_not_a_null(router):
    assert router.peers() == []


def test_get_peers_replies_with_the_listing(router_factory, agent_factory):
    agent = agent_factory(peers={"one": make_peer()})

    reply = router_factory(agent).handle({"type": "get_peers"}, authenticated=True)[-1]

    assert reply["type"] == "peers_list"
    assert len(reply["data"]["entries"]) == 1


def test_peer_detected_carries_the_same_shape(router):
    message = router.push("peer_detected", companion.normalise_peer(make_peer()))

    assert message["type"] == "peer_detected"
    assert message["data"]["fullName"] == "TestPeer_001@aabbccddeeff00112233445566778899"


# ---------------------------------------------------------------------------
# An infinite rssi or pwnd_tot: OverflowError past a narrower except (issue #96)
# ---------------------------------------------------------------------------


def _peers_list_validator(schemas_dir):
    import json

    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    def load(path):
        return json.loads(path.read_text(encoding="utf-8"))

    common = Resource.from_contents(
        load(schemas_dir / "common.json"), default_specification=DRAFT202012
    )
    registry = Registry().with_resources(
        [("common.json", common), ("outgoing/common.json", common)]
    )
    schema = load(schemas_dir / "outgoing" / "peers_list.json")
    schema.pop("$id", None)
    return Draft202012Validator(schema, registry=registry)


def test_an_infinite_rssi_or_pwnd_total_costs_only_that_field_not_the_reply(
    router_factory, agent_factory, schemas_dir
):
    """A pwngrid advertisement is attacker-controlled JSON on a point-to-point
    link (SPEC 2.8): `json.loads` happily produces `float('inf')` from an
    `Infinity` literal in the wire text, and `int(float('inf'))` raises
    `OverflowError` - not `TypeError` or `ValueError`. A narrower except
    around the integer coercion lets that one raise past the per-field
    guard and, from there, past `Router.peers()`'s own per-entry guard too -
    one hostile advertisement anywhere in the mesh would cost the whole
    `get_peers` reply, not just the one field it actually broke.

    `rssi` and `pwndTotal` (from `pwnd_tot`) are both integer-coerced
    fields (SPEC 2.8); both are exercised here, beside an unaffected peer,
    so the guard is proven for each rather than for only whichever one
    happened to be fixed first.
    """
    good = make_peer(adv={**FULL_ADVERTISEMENT, "identity": "1111", "name": "Good"})
    bad_rssi = make_peer(
        adv={**FULL_ADVERTISEMENT, "identity": "2222", "name": "BadRssi"},
        rssi=float("inf"),
    )
    bad_pwnd = make_peer(
        adv={
            **FULL_ADVERTISEMENT,
            "identity": "3333",
            "name": "BadPwnd",
            "pwnd_tot": float("inf"),
        }
    )
    agent = agent_factory(
        peers={"good": good, "bad_rssi": bad_rssi, "bad_pwnd": bad_pwnd}
    )

    replies = router_factory(agent).handle({"type": "get_peers"}, authenticated=True)

    # Never an internal_error, and never a reply dropped in favour of one:
    # exactly the peers_list a well-formed mesh would have produced.
    assert [reply["type"] for reply in replies] == ["peers_list"]
    reply = replies[0]
    _peers_list_validator(schemas_dir).validate(reply)

    entries = {entry["fingerprint"]: entry for entry in reply["data"]["entries"]}
    assert set(entries) == {"1111", "2222", "3333"}, (
        "an inf field must cost only itself, never the whole peer entry"
    )

    assert entries["1111"]["rssi"] == -61
    assert entries["1111"]["pwndTotal"] == 91

    assert entries["2222"]["rssi"] is None
    assert entries["2222"]["pwndTotal"] == 91

    assert entries["3333"]["rssi"] == -61
    assert entries["3333"]["pwndTotal"] is None
