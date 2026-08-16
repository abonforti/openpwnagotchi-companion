"""`face_status`: the one thing a companion app exists to show (SPEC 2.4, D11).

The payload is `{face, status, mode}` and every field is required to be present
and to be a string (`docs/schemas/common.json`, `$defs/FaceStatus`). Three
properties are worth stating separately, because each is broken by a different
mistake:

1. **The text comes from the agent's view.** `Agent.view()` returns the `View`
   the agent was built with, and `View.get(key)` returns the stored **value** -
   never the widget, `None` for an absent key (F28). A reply that ignores the
   view is a dashboard that shows an empty card on a working unit, which is
   exactly what the suite could not see before the stub knew about the view.
2. **The face is unicode text and stays byte-for-byte what the view held**
   (D11). No PNG is transported, and nothing on this path may normalise,
   transliterate or ASCII-escape a face: the faces the fork ships are built out
   of characters that any of those would destroy.
3. **A view that cannot answer costs the text, never the message.** The schema
   makes all three fields required strings, so the only conforming answer to an
   unreadable view is empty text - not a missing key, not `None`, and not an
   exception on a path the display refresh drives.

Reading a face must also cost nothing: `agent.session()` is a 30-second blocking
GET (F7) and `get_face_status` is a request path (SPEC 2.5).
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from pwnagotchi.ui.view import View

# Synthetic throughout. The first nine are the shape upstream
# `pwnagotchi/ui/faces.py` ships - ASCII brackets around box-drawing and
# miscellaneous-symbol code points - and are the characters an encoding slip
# mangles first. The last two are stress cases that no stock unit renders: a
# grapheme cluster and a zero-width-joiner sequence, included because a face is
# operator-configurable text and either one is what breaks a length-based or a
# normalising transport.
FACE = "(⌐■_■)"
STATUS = "Hi, I'm TestUnit"

UNICODE_FACES = [
    "(⌐■_■)",
    "(☓‿‿☓)",
    "(°▃▃°)",
    "( ✜‿‿✜)",
    "(#__#)",
    "(ᵔ◡◡ᵔ)",
    "( ⚆_⚆)",
    "(╥☁╥ )",
    "(♥‿‿♥)",
    "निर्भय",  # synthetic: a grapheme cluster with combining marks
    "🐱‍💻",  # synthetic: a zero-width joiner sequence
]


# ---------------------------------------------------------------------------
# The stub's own guard
# ---------------------------------------------------------------------------


class Widget:
    """The shape F28 says a caller of `View.get` can never observe.

    `State.get` unwraps the widget and returns `widget.value`, so this object
    exists on a device only inside `State`, never at the boundary the plugin
    reads. It is defined here purely to prove the stub refuses it.
    """

    def __init__(self, value: str) -> None:
        self.value = value


def test_the_view_stub_refuses_a_widget_outright():
    """The claim the stub is built on has to be a failure, not a docstring.

    Without this, a later test could hand `View` the widget shape, a
    widget-unwrapping branch in the plugin would light up as covered, and the
    branch would still be dead code on hardware.
    """
    with pytest.raises(TypeError) as raised:
        View({"face": Widget(FACE)})

    message = str(raised.value)
    assert "'face'" in message
    assert "Widget" in message
    assert "F28" in message


@pytest.mark.parametrize("value", [None, 42, b"(o_o)", ["(o_o)"], {"value": "(o_o)"}])
def test_the_view_stub_refuses_any_value_that_is_not_text(value):
    """F28 resolves both pinned keys to strings on a running unit. Every other
    shape is a fake the fork cannot back, whatever it happens to be."""
    with pytest.raises(TypeError):
        View({"status": value})


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def face_status_validator(schemas_dir):
    """The `face_status` schema, refs resolved against `common.json` (D15)."""
    common = Resource.from_contents(
        json.loads((schemas_dir / "common.json").read_text(encoding="utf-8")),
        default_specification=DRAFT202012,
    )
    registry = Registry().with_resources(
        [("common.json", common), ("outgoing/common.json", common)]
    )
    schema = json.loads(
        (schemas_dir / "outgoing" / "face_status.json").read_text(encoding="utf-8")
    )
    schema.pop("$id", None)
    return Draft202012Validator(schema, registry=registry)


@pytest.fixture
def reply(face_status_validator):
    """`get_face_status` driven through a router, schema-checked, unwrapped."""

    def _reply(the_router) -> dict:
        messages = the_router.handle({"type": "get_face_status"}, authenticated=True)
        assert [message["type"] for message in messages] == ["face_status"]
        face_status_validator.validate(messages[0])
        return messages[0]["data"]

    return _reply


# ---------------------------------------------------------------------------
# The view is the source
# ---------------------------------------------------------------------------


def test_both_fields_come_back_as_the_view_holds_them(router_factory, agent_factory, reply):
    agent = agent_factory(face=FACE, status=STATUS)

    data = reply(router_factory(agent))

    assert data["face"] == FACE
    assert data["status"] == STATUS


def test_the_direct_call_and_the_reply_agree(router_factory, agent_factory, reply):
    """The burst and the reply are the same payload through two doors (SPEC 2.4).

    If they could disagree, a client that never polls would show one face and a
    client that polls would show another, and only one of them would be right.
    """
    agent = agent_factory(face="(°▃▃°)", status="Hack the planet")
    the_router = router_factory(agent)

    assert reply(the_router) == the_router.face_status()


def test_the_view_is_read_on_every_call_rather_than_at_startup(
    router_factory, agent_factory, reply
):
    """The face changes several times a minute. A value captured when the router
    was built would leave the app showing the boot face for the whole session."""
    agent = agent_factory(face=FACE, status=STATUS)
    the_router = router_factory(agent)
    assert reply(the_router)["face"] == FACE

    agent._ui_view = View({"face": "(o_o)", "status": "ZzzZz"})

    data = reply(the_router)
    assert data["face"] == "(o_o)"
    assert data["status"] == "ZzzZz"


def test_the_burst_carries_the_face_the_view_holds(router_factory, agent_factory, face_status_validator):
    """The initial burst exists so a fresh client has state without polling
    (SPEC 2.4). A burst carrying an empty face defeats its own purpose."""
    agent = agent_factory(face=FACE, status=STATUS)

    burst = router_factory(agent).initial_burst()

    face_status = [message for message in burst if message["type"] == "face_status"]
    assert len(face_status) == 1
    face_status_validator.validate(face_status[0])
    assert face_status[0]["data"]["face"] == FACE
    assert face_status[0]["data"]["status"] == STATUS


def test_reading_the_face_never_touches_bettercap(router_factory, agent_factory, reply):
    """F7: `session()` is a synchronous GET with a 30-second timeout. One on this
    path stalls every connected client for as long as bettercap takes to answer."""
    agent = agent_factory(face=FACE, status=STATUS)
    the_router = router_factory(agent)

    reply(the_router)

    assert agent.session_calls == 0


# ---------------------------------------------------------------------------
# The face is text (D11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("face", UNICODE_FACES, ids=range(len(UNICODE_FACES)))
def test_a_unicode_face_survives_the_round_trip_unchanged(
    router_factory, agent_factory, reply, face
):
    """D11: faces are unicode text, rendered natively by the PWA. Anything that
    re-encodes, strips or normalises here shows the user a different face than
    the e-ink does, and the two are meant to be the same picture."""
    agent = agent_factory(face=face, status=STATUS)

    data = reply(router_factory(agent))

    assert data["face"] == face
    # The wire is JSON, and `ensure_ascii` is the switch that quietly turns a
    # face into escapes. Either encoding must decode back to the same string.
    assert json.loads(json.dumps(data["face"], ensure_ascii=False)) == face
    assert json.loads(json.dumps(data["face"], ensure_ascii=True)) == face


def test_no_image_is_ever_transported_with_the_face(router_factory, agent_factory, reply):
    """D11 again, from the other side: there is no PNG face transport at all, and
    the frame has its own message type (`screen_image`)."""
    agent = agent_factory(face=FACE, status=STATUS)

    data = reply(router_factory(agent))

    # The payload has room for exactly three text fields. No image key can be
    # added without this failing, and nothing here is base64 or bytes.
    assert set(data) == {"face", "status", "mode"}
    assert all(isinstance(value, str) for value in data.values())


# ---------------------------------------------------------------------------
# A view that cannot supply the text
# ---------------------------------------------------------------------------


def test_a_key_the_view_never_set_becomes_empty_text(router_factory, agent_factory, reply):
    """F28: `State.get` returns `None` for an absent key. `None` is not a string,
    and the schema requires one, so the missing key costs the text and nothing
    else - the other field still has to arrive."""
    agent = agent_factory()
    agent._ui_view = View({"face": FACE})

    data = reply(router_factory(agent))

    assert data["face"] == FACE
    assert data["status"] == ""


def test_a_view_holding_nothing_at_all_still_produces_the_payload(
    router_factory, agent_factory, reply
):
    agent = agent_factory()
    agent._ui_view = View({})

    data = reply(router_factory(agent))

    assert (data["face"], data["status"]) == ("", "")
    assert data["mode"] == "AUTO", "the mode does not come from the view"


def test_a_view_that_refuses_the_lookup_costs_only_the_text(
    router_factory, agent_factory, reply
):
    class Uncooperative:
        def get(self, key):
            raise RuntimeError("no such element")

    agent = agent_factory()
    agent._ui_view = Uncooperative()

    data = reply(router_factory(agent))

    assert (data["face"], data["status"]) == ("", "")
    assert data["mode"] == "AUTO"


def test_an_agent_that_cannot_hand_over_its_view_costs_only_the_text(
    router_factory, agent_factory, reply
):
    """The hooks and the request path both run while the agent is being torn
    down. An exception here reaches the connection handler, and SPEC 2.14 says
    nothing may kill the loop."""
    agent = agent_factory()

    def exploding_view():
        raise RuntimeError("the agent is going away")

    agent.view = exploding_view

    data = reply(router_factory(agent))

    assert (data["face"], data["status"]) == ("", "")
    assert data["mode"] == "AUTO"


# ---------------------------------------------------------------------------
# The mode field
# ---------------------------------------------------------------------------


def test_mode_is_auto_by_default(router_factory, agent_factory, reply):
    assert reply(router_factory(agent_factory(mode="auto")))["mode"] == "AUTO"


def test_mode_is_manual_when_the_agent_is_in_manual(router_factory, agent_factory, reply):
    """F11: `agent.mode == "manual"` is the fork's own comparison. The wire
    vocabulary is the `Mode` enum, whose member is `MANUAL` and not `MANU` -
    `MANU` is what `pwnagotchi.restart` takes (SPEC 2.6.1)."""
    assert reply(router_factory(agent_factory(mode="manual")))["mode"] == "MANUAL"


def test_mode_is_pasv_in_auto_while_the_plugin_reports_passive(
    router_factory, agent_factory, reply, load_pasv
):
    load_pasv(passive=True)

    assert reply(router_factory(agent_factory(mode="auto")))["mode"] == "PASV"


def test_the_mode_here_is_the_one_stats_reports(router_factory, agent_factory, load_pasv):
    """The dashboard shows one mode badge and reads it from whichever message
    arrived last. Two mappings that can disagree make the badge flicker between
    two truths."""
    load_pasv(passive=True)
    the_router = router_factory(agent_factory(mode="auto"))

    assert the_router.face_status()["mode"] == the_router.stats()["mode"]
