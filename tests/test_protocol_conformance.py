"""Every message on the wire, against `docs/schemas`.

D15 makes `docs/schemas/*.json` the authority: the prose in SPEC 2.4 is a
summary, the TypeScript types are generated from the schemas, and both ends are
tested against them. That only means something if the plugin is actually checked
against the files rather than against a reading of them.

Two directions, and the parity between them:

- every outgoing message the plugin can produce validates against
  `outgoing/<type>.json`;
- every incoming schema is exercised with a valid and an invalid sample;
- a type in one place and not the other fails. Drift between the plugin and the
  schemas is the exact failure the schema-first design exists to prevent, so it
  cannot be allowed to be quiet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from plugin import companion


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def registry(schemas_dir) -> Registry:
    common = Resource.from_contents(
        load(schemas_dir / "common.json"), default_specification=DRAFT202012
    )
    return Registry().with_resources(
        [
            ("common.json", common),
            ("incoming/common.json", common),
            ("outgoing/common.json", common),
        ]
    )


@pytest.fixture(scope="session")
def validator_for(schemas_dir, registry):
    def _validator(direction: str, kind: str) -> Draft202012Validator:
        schema = load(schemas_dir / direction / f"{kind}.json")
        # The $id is relative, so dropping it leaves refs to resolve against the
        # registry rather than against "outgoing/".
        schema.pop("$id", None)
        return Draft202012Validator(schema, registry=registry)

    return _validator


def names_in(directory: Path) -> set[str]:
    return {path.stem for path in directory.glob("*.json")}


@pytest.fixture(scope="session")
def outgoing_types(schemas_dir) -> set[str]:
    return names_in(schemas_dir / "outgoing")


@pytest.fixture(scope="session")
def incoming_types(schemas_dir) -> set[str]:
    return names_in(schemas_dir / "incoming")


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------

VALID_INCOMING: dict[str, dict] = {
    "auth": {"type": "auth", "token": "s3cr3t-shared-token"},
    "get_stats": {"type": "get_stats"},
    "get_access_points": {"type": "get_access_points"},
    "get_handshakes": {"type": "get_handshakes"},
    "get_peers": {"type": "get_peers"},
    "get_log": {"type": "get_log", "lines": 50},
    "get_face_status": {"type": "get_face_status"},
    "get_screen": {"type": "get_screen"},
    "get_gps_data": {"type": "get_gps_data"},
    "set_mode": {"type": "set_mode", "mode": "manual", "message_id": "c7f1"},
    "set_pasv": {"type": "set_pasv", "on": True},
    "reboot": {"type": "reboot"},
    "shutdown": {"type": "shutdown"},
    "ping": {"type": "ping", "message_id": "c7f1"},
    "gps_data": {
        "type": "gps_data",
        "latitude": -33.512345,
        "longitude": -25.098765,
        "accuracy": 18.0,
    },
}

INVALID_INCOMING: dict[str, dict] = {
    "auth": {"type": "auth"},
    "get_stats": {"type": "get_stats", "unexpected": 1},
    "get_access_points": {"type": "get_access_points", "lines": 10},
    "get_handshakes": {"type": "get_handshakes", "limit": 10},
    "get_peers": {"type": "get_peers", "since": 0},
    "get_log": {"type": "get_log", "lines": 5000},
    "get_face_status": {"type": "get_face_status", "face": "(⌐■_■)"},
    "get_screen": {"type": "get_screen", "scale": 2},
    "get_gps_data": {"type": "get_gps_data", "source": "gpsd"},
    "set_mode": {"type": "set_mode", "mode": "MANU"},
    "set_pasv": {"type": "set_pasv", "on": "yes"},
    "reboot": {"type": "reboot", "force": True},
    "shutdown": {"type": "shutdown", "delay": 5},
    "ping": {"type": "ping", "message_id": 7},
    "gps_data": {"type": "gps_data", "latitude": -33.5},
}


# ---------------------------------------------------------------------------
# Incoming
# ---------------------------------------------------------------------------


def test_every_incoming_schema_has_samples(incoming_types):
    assert set(VALID_INCOMING) == incoming_types
    assert set(INVALID_INCOMING) == incoming_types


@pytest.mark.parametrize("kind", sorted(VALID_INCOMING))
def test_the_valid_sample_validates(validator_for, kind):
    validator_for("incoming", kind).validate(VALID_INCOMING[kind])


@pytest.mark.parametrize("kind", sorted(INVALID_INCOMING))
def test_the_invalid_sample_does_not_validate(validator_for, kind):
    errors = list(validator_for("incoming", kind).iter_errors(INVALID_INCOMING[kind]))

    assert errors, f"{kind}: the invalid sample was accepted"


@pytest.mark.parametrize("kind", sorted(VALID_INCOMING))
def test_the_plugin_routes_every_incoming_type(
    router_factory, agent_factory, load_pasv, frame_file, kind
):
    load_pasv()
    router = router_factory(agent_factory(mode="auto"), overrides={"token": ""})

    replies = router.handle(dict(VALID_INCOMING[kind]), authenticated=True)

    codes = [
        reply["data"].get("code") for reply in replies if reply["type"] == "error"
    ]
    assert "unknown_command" not in codes


def test_a_type_nobody_implements_is_an_unknown_command(router):
    replies = router.handle({"type": "get_the_moon"}, authenticated=True)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["data"]["code"] == "unknown_command"


def test_a_message_without_a_type_is_a_bad_request(router):
    replies = router.handle({"mode": "manual"}, authenticated=True)

    assert replies[-1]["data"]["code"] == "bad_request"


def test_an_unexpected_key_is_a_bad_request(router):
    # additionalProperties is false throughout: an extra field is an error, not
    # something to ignore on one side of the link.
    replies = router.handle({"type": "get_stats", "unexpected": 1}, authenticated=True)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["data"]["code"] == "bad_request"


# ---------------------------------------------------------------------------
# Outgoing
# ---------------------------------------------------------------------------


@pytest.fixture
def produced(router_factory, agent_factory, load_pasv, frame_file, harness):
    """Every outgoing message type, produced by driving the router.

    Collected in one place so the parity check below can compare what the plugin
    emits against what the schema directory declares.
    """
    from test_peers import make_peer

    load_pasv(passive=False)
    agent = agent_factory(mode="auto", peers={"one": make_peer()})
    router = router_factory(agent)

    messages: list[dict] = []
    for request in (
        {"type": "get_stats", "message_id": "c7f1"},
        {"type": "get_access_points"},
        {"type": "get_handshakes"},
        {"type": "get_peers"},
        {"type": "get_log", "lines": 5},
        {"type": "get_screen"},
        {"type": "get_face_status"},
        {"type": "get_gps_data"},
        {"type": "ping"},
        {"type": "set_pasv", "on": True, "message_id": "c7f2"},
        {"type": "get_the_moon"},
        {"type": "set_mode", "mode": "manual", "message_id": "c7f3"},
        {"type": "reboot"},
        {"type": "shutdown"},
    ):
        messages.extend(router.handle(request, authenticated=True))

    messages.extend(router.initial_burst())
    messages.append(
        router.on_handshake_message(
            "TestNet_001_aabbccddeeff.pcapng",
            "aa:bb:cc:dd:ee:ff",
            "11:22:33:44:55:66",
        )
    )
    messages.append(router.push("peer_detected", router.peers()[0]))
    messages.append(router.push("wifi_update", router.access_points()))
    messages.append(router.push("channel_hop", {"channel": 6}))
    messages.append(router.push("status_change", {"status": "bored...", "mood": "bored"}))
    messages.append(router.push("keepalive", {}))
    return messages


def test_every_outgoing_message_validates(produced, validator_for, outgoing_types):
    for message in produced:
        assert message["type"] in outgoing_types, (
            f"{message['type']} has no schema in docs/schemas/outgoing"
        )
        validator_for("outgoing", message["type"]).validate(message)


def test_the_plugin_produces_every_declared_outgoing_type(produced, outgoing_types):
    emitted = {message["type"] for message in produced}

    assert emitted == outgoing_types


def test_every_outgoing_message_carries_the_envelope(produced):
    for message in produced:
        assert set(message) <= {"type", "data", "timestamp", "message_id"}
        assert isinstance(message["timestamp"], float)
        assert "data" in message


def test_the_timestamp_comes_from_the_injected_clock(produced, harness):
    for message in produced:
        assert message["timestamp"] == pytest.approx(harness.clock)


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def test_a_message_id_is_echoed_on_the_acknowledgment_and_the_reply(router):
    replies = router.handle({"type": "get_stats", "message_id": "c7f1"}, authenticated=True)

    assert [reply["type"] for reply in replies] == ["acknowledgment", "stats"]
    assert all(reply["message_id"] == "c7f1" for reply in replies)


def test_no_message_id_means_no_acknowledgment_and_no_echo(router):
    replies = router.handle({"type": "get_stats"}, authenticated=True)

    assert [reply["type"] for reply in replies] == ["stats"]
    assert "message_id" not in replies[0]


def test_an_error_echoes_the_message_id_too(router):
    replies = router.handle({"type": "get_the_moon", "message_id": "c7f1"}, authenticated=True)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["message_id"] == "c7f1"


def test_the_message_id_is_opaque(router):
    identifier = "☃ not a uuid at all"

    replies = router.handle(
        {"type": "ping", "message_id": identifier}, authenticated=True
    )

    assert replies[-1]["message_id"] == identifier


# ---------------------------------------------------------------------------
# The envelope helpers
# ---------------------------------------------------------------------------


def test_the_envelope_omits_the_message_id_when_there_was_none():
    assert companion.envelope("pong", {}, 1.0) == {
        "type": "pong",
        "data": {},
        "timestamp": 1.0,
    }


def test_the_error_envelope_carries_a_closed_enum_code(schemas_dir):
    codes = load(schemas_dir / "common.json")["$defs"]["ErrorCode"]["enum"]
    message = companion.error_envelope("bad_request", "unexpected key", 1.0, "c7f1")

    assert message["data"]["code"] in codes
    assert message["message_id"] == "c7f1"


def test_the_initial_burst_is_stats_then_access_points_then_face_status(router):
    burst = [message["type"] for message in router.initial_burst()]

    assert burst == ["stats", "access_points", "face_status"]


def test_the_burst_carries_no_message_id(router):
    for message in router.initial_burst():
        assert "message_id" not in message


# ---------------------------------------------------------------------------
# Screen and face
# ---------------------------------------------------------------------------


def test_the_frame_is_base64_with_no_data_prefix(router, frame_file):
    import base64

    reply = router.handle({"type": "get_screen"}, authenticated=True)[-1]

    assert reply["type"] == "screen_image"
    assert not reply["data"]["png"].startswith("data:")
    assert base64.b64decode(reply["data"]["png"]) == frame_file.read_bytes()
    assert reply["data"]["mtime"] == pytest.approx(frame_file.stat().st_mtime)


def test_a_missing_frame_is_an_error_not_an_empty_image(router, stub_ui_web, tmp_path):
    stub_ui_web.frame_path = str(tmp_path / "never-rendered.png")

    reply = router.handle({"type": "get_screen"}, authenticated=True)[-1]

    assert reply["type"] == "error"
    assert reply["data"]["code"] == "no_frame"


def test_the_frame_is_read_under_the_lock(router, frame_file, stub_ui_web):
    stub_ui_web.frame_lock.acquire()
    try:
        # The lock is held for the read only, never across a send, so a plugin
        # holding it must not be able to serve the frame right now.
        assert stub_ui_web.frame_lock.locked()
    finally:
        stub_ui_web.frame_lock.release()

    reply = router.handle({"type": "get_screen"}, authenticated=True)[-1]

    assert reply["type"] == "screen_image"
    assert not stub_ui_web.frame_lock.locked()


def test_the_face_is_text_and_never_an_image(router):
    face = router.face_status()

    assert set(face) == {"face", "status", "mode"}
    assert isinstance(face["face"], str)
    assert "png" not in repr(face).lower()
