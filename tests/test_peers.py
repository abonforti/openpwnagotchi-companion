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
    "name": "sn0wtest",
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

    assert mapped["name"] == "sn0wtest"
    assert mapped["fingerprint"] == "aabbccddeeff00112233445566778899"
    assert mapped["fullName"] == "sn0wtest@aabbccddeeff00112233445566778899"
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
    assert mapped["name"] == "sn0wtest"
    assert mapped["rssi"] == -55


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


def test_peers_are_read_from_the_underscore_dict(router_factory, agent_factory):
    agent = agent_factory(peers={"one": make_peer(), "two": Peer({"name": "other"})})

    entries = router_factory(agent).peers()

    assert {entry["name"] for entry in entries} == {"sn0wtest", "other"}


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
    assert message["data"]["fullName"] == "sn0wtest@aabbccddeeff00112233445566778899"
