"""The pwnagotchi hook surface (SPEC 2.13).

These are the methods the agent calls on the device, on its own threads, at a
rate nothing here controls. Three properties matter and none of them is visible
from a router test:

1. **The pushes carry the wire shapes.** Every hook broadcast is validated
   against `docs/schemas/outgoing/<type>.json`, which D15 makes the authority.
2. **`on_ui_update` diffs.** The hook fires on every display refresh, so pushing
   unconditionally would turn a Bluetooth link into a face-status firehose
   (SPEC 9, pitfall 17: keep payloads small).
3. **No hook ever raises.** SPEC 2.14 requires that no unhandled exception can
   kill the loop. A hook that throws does not merely lose a message; it
   propagates into `plugins.on()` and takes the agent's thread with it, which on
   a unit in a pocket means the pwnagotchi stops working and nobody finds out
   until the display stops changing.

The plugin is driven through its documented lifecycle - `on_loaded`, `on_ready`,
the hooks, `on_unload` - rather than by assembling its internals, because the
lifecycle is what the agent will do and the wiring is not part of the contract.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from plugin import companion
from pwnagotchi.mesh.peer import Peer
from pwnagotchi.ui.view import View

# Synthetic throughout: a documentation MAC, a made-up SSID and a position in
# the South Atlantic. Fixtures are published, and a real BSSID with a real
# coordinate is location data about a person (SPEC 13).
AP_MAC = "aa:bb:cc:dd:ee:ff"
STA_MAC = "11:22:33:44:55:66"
STRING_SHAPE = (AP_MAC, STA_MAC)
DICT_SHAPE = (
    {"mac": AP_MAC, "hostname": "TestNet_001", "channel": 6},
    {"mac": STA_MAC, "vendor": "Synthetic Devices"},
)

LAT = -33.512345
LON = -25.098765
ACCURACY = 18.0

MOODS = ["bored", "excited", "lonely", "sad"]


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def outgoing_validator(schemas_dir):
    """A validator per outgoing message type, refs resolved against common.json."""
    common = Resource.from_contents(
        json.loads((schemas_dir / "common.json").read_text(encoding="utf-8")),
        default_specification=DRAFT202012,
    )
    registry = Registry().with_resources(
        [
            ("common.json", common),
            ("outgoing/common.json", common),
        ]
    )

    def _for(kind: str) -> Draft202012Validator:
        schema = json.loads(
            (schemas_dir / "outgoing" / f"{kind}.json").read_text(encoding="utf-8")
        )
        # The $id is relative, so dropping it leaves the refs to resolve against
        # the registry rather than against "outgoing/".
        schema.pop("$id", None)
        return Draft202012Validator(schema, registry=registry)

    return _for


@pytest.fixture
def check_message(outgoing_validator):
    def _check(message: dict, kind: str) -> dict:
        assert message["type"] == kind
        outgoing_validator(kind).validate(message)
        return message["data"]

    return _check


# ---------------------------------------------------------------------------
# The plugin under its own lifecycle
# ---------------------------------------------------------------------------


class Hooked:
    """A loaded plugin plus everything a hook test needs to look at."""

    def __init__(self, plugin, agent, sent, harness, handshake_dir) -> None:
        self.plugin = plugin
        self.agent = agent
        self.sent = sent
        self.harness = harness
        self.handshake_dir = handshake_dir

    def types(self) -> list[str]:
        return [message["type"] for message in self.sent]

    def only(self, kind: str) -> dict:
        matching = [message for message in self.sent if message["type"] == kind]
        assert len(matching) == 1, f"expected exactly one {kind}, got {self.types()}"
        return matching[0]

    def capture(self, name: str) -> Path:
        """An empty capture file that has no sidecar yet."""
        path = self.handshake_dir / name
        path.write_bytes(b"")
        return path

    def with_a_browser_fix(self) -> None:
        """Gives the plugin a fix through the documented `gps_data` command.

        The browser source is used because it is the only one a test can supply
        without reaching past the protocol: bettercap's would need the cached
        session and gpsd's a socket. SPEC 2.12.1 requires the same sidecar shape
        whichever source produced the fix, plus a `Source` key, so the assertion
        is about the sidecar contract rather than about the browser.
        """
        self.plugin._router.handle(
            {
                "type": "gps_data",
                "latitude": LAT,
                "longitude": LON,
                "accuracy": ACCURACY,
            },
            True,
        )


@pytest.fixture
def hooked(options, harness, agent, tls_material, handshake_dir, tmp_path):
    """A plugin taken through `on_loaded` and `on_ready`, with pushes captured.

    Real TLS material, because without it SPEC D4 requires the plugin to refuse
    to start - and a plugin that refused to start is the router-absent case,
    which is tested separately rather than being the default here.

    No local address matches `bind_addresses`, so no socket is opened: the hook
    surface is what is under test, not the listeners.
    """
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **options,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }
    plugin.on_loaded()
    assert plugin._router is not None, "the fixture did not produce a wired plugin"
    plugin.on_ready(agent)

    sent: list[dict] = []
    plugin.broadcast = sent.append

    yield Hooked(plugin, agent, sent, harness, handshake_dir)

    plugin.on_unload()


def a_view(face: str, status: str) -> View:
    """The UI view a display refresh presents, holding the two keys the initial
    state always carries (F28).

    `get` returns the value and never a widget: `State.get` unwraps the widget
    itself, so no caller on a device can ever observe one. A fake that offered
    the widget shape would make a widget-unwrapping branch look tested while
    being unreachable on hardware.
    """
    return View({"face": face, "status": status})


def show(hooked: Hooked, view) -> None:
    """Presents a face/status to the plugin the way a display refresh does.

    SPEC 2.13 says `on_ui_update` "diffs face/status" and does not say where it
    reads them from; the hook is handed the view, and F28 pins `agent.view()` as
    a source the reply to `get_face_status` can reach on its own. The view is
    therefore offered through both channels, so these tests state the diffing
    contract and nothing about which of the two the plugin chose.
    """
    hooked.agent._ui_view = view
    hooked.plugin.on_ui_update(view)


# ---------------------------------------------------------------------------
# on_handshake
# ---------------------------------------------------------------------------


def test_a_captured_handshake_is_pushed(hooked, check_message):
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)

    data = check_message(hooked.only("handshake"), "handshake")
    assert data["filename"] == "TestNet_002_aabbccddee01.pcapng"
    assert data["ap"] == AP_MAC
    assert "station" not in data


def test_both_argument_shapes_reach_the_wire_identically(hooked):
    """F20: the agent fires this hook with MAC strings from one path and
    bettercap dicts from the other. A handler that assumes either shape drops
    every handshake the other path produces, and only on a real unit."""
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)
    hooked.plugin.on_handshake(hooked.agent, str(capture), *DICT_SHAPE)

    from_strings, from_dicts = hooked.sent
    assert from_strings["data"] == from_dicts["data"]
    assert from_strings["data"]["ap"] == AP_MAC


def test_the_station_mac_is_absent_from_the_wire_in_both_shapes(hooked):
    """SPEC 2.13, issue #33: the hook is handed a client station beside the
    access point, and the push is `{filename, ap, gps}` - the station is
    dropped after normalising nothing from it. Both argument shapes (F20)
    are checked, since a fix scoped to only one would still leak a client
    MAC through the other."""
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)
    hooked.plugin.on_handshake(hooked.agent, str(capture), *DICT_SHAPE)

    from_strings, from_dicts = hooked.sent
    assert "station" not in from_strings["data"]
    assert "station" not in from_dicts["data"]


def test_a_fix_writes_the_sidecar_next_to_the_capture(hooked, check_message):
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")
    hooked.with_a_browser_fix()

    hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)

    sidecar = capture.with_suffix(".gps.json")
    assert sidecar.exists(), "a fix was available and no sidecar was written (D13)"
    written = json.loads(sidecar.read_text())
    # D13: the disk format is bettercap's, capitalised, so the stock gps.py
    # plugin and webgpsmap keep reading what this writes.
    assert written["Latitude"] == pytest.approx(LAT)
    assert written["Longitude"] == pytest.approx(LON)
    assert written["Source"] == "browser"

    data = check_message(hooked.only("handshake"), "handshake")
    assert data["gps"] is not None
    assert set(data["gps"]) == {"lat", "lon", "accuracy", "source"}
    assert data["gps"]["lat"] == pytest.approx(LAT)
    assert data["gps"]["lon"] == pytest.approx(LON)


def test_no_fix_writes_no_sidecar_and_still_pushes(hooked, check_message):
    """No coordinates is not `0.0, 0.0` (SPEC 2.12.1), and losing the push as
    well would mean a unit without a GPS never reports a capture at all."""
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)

    assert not capture.with_suffix(".gps.json").exists()
    data = check_message(hooked.only("handshake"), "handshake")
    assert data["gps"] is None


def test_a_sidecar_failure_does_not_cost_the_push(hooked, check_message, tmp_path):
    """The two halves are independent: a read-only capture directory, or a full
    disk, must not swallow the notification the client is waiting for.

    The failure is produced by the filesystem rather than by patching the store,
    because "the sidecar could not be written" is a real condition on a unit
    whose SD card has gone read-only, which is how these cards fail."""
    locked = tmp_path / "locked"
    locked.mkdir()
    capture = locked / "TestNet_002_aabbccddee01.pcapng"
    capture.write_bytes(b"")
    hooked.with_a_browser_fix()
    locked.chmod(0o555)

    try:
        hooked.plugin.on_handshake(hooked.agent, str(capture), *STRING_SHAPE)
    finally:
        locked.chmod(0o755)

    assert not capture.with_suffix(".gps.json").exists()
    data = check_message(hooked.only("handshake"), "handshake")
    assert data["filename"] == capture.name


def test_a_filename_the_filesystem_refuses_outright_costs_only_the_sidecar(
    hooked, check_message, tmp_path
):
    """The capture name is built from the SSID, and an SSID is whatever the
    access point broadcast. A name the OS will not accept at all must cost the
    sidecar and nothing else - not the push, and certainly not the hook."""
    hooked.with_a_browser_fix()
    hostile = str(tmp_path / "TestNet\x00_aabbccddee01.pcapng")

    hooked.plugin.on_handshake(hooked.agent, hostile, *STRING_SHAPE)

    check_message(hooked.only("handshake"), "handshake")


def test_an_unreadable_mac_becomes_null_rather_than_a_dropped_push(hooked, check_message):
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    # The unusable dict sits in the access point slot: the station slot is
    # no longer read (SPEC 2.13, issue #33), so a value there exercises nothing.
    hooked.plugin.on_handshake(
        hooked.agent, str(capture), {"hostname": "no mac here"}, None
    )

    data = check_message(hooked.only("handshake"), "handshake")
    assert data["ap"] is None
    assert "station" not in data


# ---------------------------------------------------------------------------
# on_peer_detected
# ---------------------------------------------------------------------------


def test_a_detected_peer_is_pushed_in_the_peer_shape(hooked, check_message):
    peer = Peer(
        {
            "name": "TestPeer_001",
            "identity": "aabbccddeeff00112233445566778899",
            "version": "2.9.5.6",
            "face": "(⌐■_■)",
            "uptime": 4242,
            "epoch": 17,
            "pwnd_run": ["TestNet_001"],
            "pwnd_tot": 91,
        },
        first_met=datetime(2025, 8, 1, 12, 0, 0, tzinfo=timezone.utc),
        first_seen=datetime(2025, 8, 15, 9, 0, 0, tzinfo=timezone.utc),
        prev_seen=datetime(2025, 8, 15, 9, 30, 0, tzinfo=timezone.utc),
        last_seen=1_755_250_000.0,
        encounters=3,
        last_channel=11,
        rssi=-61,
    )

    hooked.plugin.on_peer_detected(hooked.agent, peer)

    data = check_message(hooked.only("peer_detected"), "peer_detected")
    assert data["name"] == "TestPeer_001"
    assert data["channel"] == 11, "channel is peer.last_channel, not peer.channel (F16)"
    # last_seen is already an epoch float where its three siblings are datetime;
    # converting it twice is the silent bug SPEC 2.8 names.
    assert data["lastSeen"] == pytest.approx(1_755_250_000.0)
    assert data["pwndTotal"] == 91


def test_a_peer_missing_its_advertisement_is_still_pushed(hooked, check_message):
    """The accessors raise on a missing key rather than returning a default, so
    an incomplete advertisement must cost the field and not the message."""
    hooked.plugin.on_peer_detected(hooked.agent, Peer({}))

    data = check_message(hooked.only("peer_detected"), "peer_detected")
    assert data["name"] == "???"
    assert data["version"] is None
    assert data["face"] is None


# ---------------------------------------------------------------------------
# on_wifi_update
# ---------------------------------------------------------------------------


def test_wifi_update_maps_every_access_point(hooked, check_message):
    hooked.plugin.on_wifi_update(
        hooked.agent,
        [
            {
                "mac": AP_MAC,
                "hostname": "TestNet_001",
                "channel": 6,
                "rssi": -52,
                "encryption": "WPA2",
                "vendor": "Synthetic Devices",
                "clients": [{"mac": STA_MAC}],
            },
            {"mac": STA_MAC, "hostname": "", "clients": None},
        ],
    )

    data = check_message(hooked.only("wifi_update"), "wifi_update")
    assert len(data) == 2
    # F4: bettercap has no `bssid` key. Upstream reads one and always gets "".
    assert data[0]["bssid"] == AP_MAC
    assert data[0]["clients"] == 1
    assert data[1]["hostname"] == STA_MAC, "an empty hostname falls back to the MAC"
    assert data[1]["clients"] == 0, "`clients` may be null as well as absent"
    assert data[1]["channel"] == 0


def test_wifi_update_skips_what_is_not_an_access_point(hooked, check_message):
    """The hook's argument comes straight off bettercap. One malformed entry
    must cost that entry, not the whole list."""
    hooked.plugin.on_wifi_update(
        hooked.agent, [{"mac": AP_MAC}, "not a mapping", None, 42, {"mac": STA_MAC}]
    )

    data = check_message(hooked.only("wifi_update"), "wifi_update")
    assert [entry["bssid"] for entry in data] == [AP_MAC, STA_MAC]


def test_wifi_update_with_nothing_found_is_an_empty_list(hooked, check_message):
    hooked.plugin.on_wifi_update(hooked.agent, [])

    assert check_message(hooked.only("wifi_update"), "wifi_update") == []


def test_wifi_update_never_asks_the_agent_for_the_list(hooked):
    """F3: `get_access_points()` fires `plugins.on('wifi_update')` itself, so
    reading it from here is a loop, and it advances the epoch besides. The hook
    is handed the list; `FakeAgent.get_access_points` fails the test on sight.

    Measured as a delta: the plugin is allowed one refresh while it is being
    wired, which is off the request path. What it may not do is spend a
    30-second blocking GET (F7) inside a hook that fires on every scan."""
    before = hooked.agent.session_calls

    hooked.plugin.on_wifi_update(hooked.agent, [{"mac": AP_MAC}])

    assert hooked.agent.session_calls == before


# ---------------------------------------------------------------------------
# on_channel_hop
# ---------------------------------------------------------------------------


def test_a_channel_hop_is_pushed(hooked, check_message):
    hooked.plugin.on_channel_hop(hooked.agent, 11)

    assert check_message(hooked.only("channel_hop"), "channel_hop") == {"channel": 11}


def test_a_channel_hop_does_not_round_trip_through_the_session(hooked):
    """F6/F7: the channel is an attribute. A `session()` here would be a
    30-second blocking GET on a hook that fires constantly."""
    before = hooked.agent.session_calls

    hooked.plugin.on_channel_hop(hooked.agent, 11)

    assert hooked.agent.session_calls == before


# ---------------------------------------------------------------------------
# on_ui_update
# ---------------------------------------------------------------------------


def test_the_first_display_refresh_pushes_the_face(hooked, check_message):
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    data = check_message(hooked.only("face_status"), "face_status")
    assert data["face"] == "(⌐■_■)"
    assert data["status"] == "Hi, I'm TestUnit"
    assert data["mode"] == "AUTO"


def test_a_router_that_cannot_report_the_face_costs_no_push_and_no_crash(
    hooked, caplog
):
    """The last seam upstream of face_status() itself: `on_ui_update` reaches
    the router through `self._router`, and that call used to be wrapped in a
    bare `except Exception: return` - a swallow with no trace at all. SPEC
    2.14 still forbids the exception escaping and killing the display thread,
    but a swallow that leaves nothing behind is indistinguishable from the
    hook working correctly and choosing not to push, so something has to be
    logged. The log *text* is not the contract here (only #86-style presence
    is pinned) - only that a record exists.

    `face_status()` itself is proven never to raise elsewhere in this file and
    in test_face_status.py, so reaching this branch needs a double standing in
    for the router rather than any input this suite can otherwise construct -
    the branch is real (SPEC 2.14 demands it exists) even though nothing
    upstream can currently trigger it.
    """

    class RaisingRouter:
        def face_status(self):
            raise RuntimeError("the router could not be read")

    hooked.plugin._router = RaisingRouter()

    with caplog.at_level("ERROR"):
        hooked.plugin.on_ui_update(a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    assert hooked.sent == [], "there is nothing to push when the router itself could not answer"
    assert [r for r in caplog.records if r.levelname == "ERROR"], (
        "a swallowed exception here must still leave a trace, not vanish silently"
    )


def test_an_unchanged_face_is_not_pushed_again(hooked):
    """The hook fires on every display refresh. Pushing each one would put a
    face on the wire several times a second over a Bluetooth PAN link that SPEC
    9 already asks to keep quiet, and none of them would be news."""
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    assert hooked.types() == ["face_status"]


@pytest.mark.parametrize(
    "second,changed",
    [
        (("(o_o)", "Hi, I'm TestUnit"), "face"),
        (("(⌐■_■)", "Hack the planet"), "status"),
        (("(o_o)", "Hack the planet"), "both"),
    ],
    ids=["face", "status", "both"],
)
def test_a_changed_face_or_status_is_pushed(hooked, check_message, second, changed):
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))
    show(hooked, a_view(*second))

    assert hooked.types() == ["face_status", "face_status"], (
        f"the {changed} changed and no second face_status went out"
    )
    data = check_message(hooked.sent[-1], "face_status")
    assert (data["face"], data["status"]) == second


def test_a_key_the_view_never_set_is_pushed_as_empty_text(hooked, check_message):
    """F28: `State.get` answers `None` for an absent key, and the schema requires
    a string. The key that *is* present must still make it out."""
    show(hooked, View({"face": "(⌐■_■)"}))

    data = check_message(hooked.only("face_status"), "face_status")
    assert data["face"] == "(⌐■_■)"
    assert data["status"] == ""


def test_a_face_the_view_cannot_supply_is_empty_text_not_an_image(hooked, check_message):
    """D11: faces are unicode text. Whatever the view does or does not hold, no
    PNG is ever transported, and the fields stay strings."""

    class Uncooperative:
        def get(self, key):
            raise RuntimeError("no such element")

    show(hooked, Uncooperative())

    data = check_message(hooked.only("face_status"), "face_status")
    assert data["face"] == ""
    assert data["status"] == ""


def test_a_face_that_cannot_be_read_at_all_still_pushes_a_degraded_face_status(
    hooked, check_message
):
    """The regression this issue exists for (2026-08-18). Before the fix, an
    unreadable view degraded to empty text and still went out, but an
    unreadable *mode* made the whole payload-building call raise, and this
    hook swallowed that exception and pushed nothing. Silence is exactly the
    outcome that is not allowed here: a client cannot tell "nothing changed"
    from "the plugin stopped talking", and this one fires on every display
    refresh, so a throw here is a throw several times a second (SPEC 2.14).

    This is the test to watch: reverting to the old "drop the push" behaviour
    makes `hooked.only("face_status")` fail because nothing arrived at all,
    not merely because an exception escaped.
    """

    class Collapsing:
        @property
        def mode(self):
            raise RuntimeError("the agent is going away")

        def view(self):
            raise RuntimeError("the agent is going away")

    hooked.plugin.on_ready(Collapsing())
    hooked.plugin.on_ui_update(a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    data = check_message(hooked.only("face_status"), "face_status")
    assert (data["face"], data["status"]) == ("", "")
    assert data["mode"] == "AUTO", (
        "mode degrades to AUTO, not to an empty string, because the wire "
        "enum has no empty member (docs/schemas/common.json)"
    )


def test_a_readable_face_still_pushes_when_only_the_mode_cannot_be_read(
    hooked, check_message
):
    """The third of the four view/mode combinations: the view answers fine,
    only the mode read fails. Before the fix this one also lost the whole
    push, indistinguishably from the view also having failed - this test is
    what tells the two apart."""

    class ModeBroken:
        def __init__(self, view) -> None:
            self._view = view

        def view(self):
            return self._view

        @property
        def mode(self):
            raise RuntimeError("mode is unreadable")

    view = a_view("(⌐■_■)", "Hi, I'm TestUnit")
    hooked.plugin.on_ready(ModeBroken(view))
    hooked.plugin.on_ui_update(view)

    data = check_message(hooked.only("face_status"), "face_status")
    assert data["face"] == "(⌐■_■)"
    assert data["status"] == "Hi, I'm TestUnit"
    assert data["mode"] == "AUTO"


# ---------------------------------------------------------------------------
# on_ui_update with no agent at all (issue #140)
# ---------------------------------------------------------------------------
#
# Distinct from the two tests above: `Collapsing`/`ModeBroken` both hand
# `on_ready` an agent whose `mode` *raises* when read, and SPEC 2.13 (left
# unedited by issue #140) says that degrades to `"AUTO"`. This section is the
# other case entirely - no agent has ever arrived, because `on_ready` was
# never called at all, the manual-mode shape #140 is about - and SPEC 2.5
# says that one is `null`, not a guess. The two must not be conflated: an
# implementation that reused the "unreadable" fallback for "absent" would
# reintroduce the exact regression this issue closed.


@pytest.fixture
def loaded_without_ready(options, harness, tls_material, handshake_dir, tmp_path):
    """A plugin taken through `on_loaded` only - `on_ready` is never called.

    The manual-mode shape of issue #140: pwnagotchi never supplies an agent
    at all. Mirrors `hooked` except for that one call.
    """
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **options,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }
    plugin.on_loaded()
    assert plugin._router is not None, "the fixture did not produce a wired plugin"

    sent: list[dict] = []
    plugin.broadcast = sent.append

    yield plugin, sent

    plugin.on_unload()


def test_a_display_refresh_with_no_agent_still_pushes_a_null_mode(
    loaded_without_ready, check_message
):
    """The regression issue #140 closed. `on_ready` never having fired must
    not cost the display refresh its push - SPEC 2.13 already requires a
    hook that cannot build part of the payload to still build the rest - and
    the `mode` it carries must be `null`, not the `"AUTO"` the first
    implementation guessed."""
    plugin, sent = loaded_without_ready

    plugin.on_ui_update(a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    matching = [m for m in sent if m["type"] == "face_status"]
    assert len(matching) == 1, f"expected exactly one face_status, got {[m['type'] for m in sent]}"
    data = check_message(matching[0], "face_status")
    assert data["mode"] is None, (
        "mode is null with no agent, not AUTO (SPEC 2.5, issue #140) - "
        "AUTO is reserved for an agent that answered once and then went "
        "unreadable (SPEC 2.13), a different case"
    )


class FlakyBroadcast:
    """A push that fails a set number of times and then works.

    The permanently exploding broadcast used for failure containment below pins
    that nothing escapes, and nothing else: with every push failing, a diff that
    remembered the face it never managed to send is indistinguishable from one
    that did not. A transient failure is what tells the two apart, and transient
    is the realistic case - a client that went away between the refresh and the
    write.
    """

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.sent: list[dict] = []

    def __call__(self, message: dict) -> None:
        if self.failures:
            self.failures -= 1
            raise RuntimeError("the client went away mid-push")
        self.sent.append(message)


def test_a_face_lost_to_a_failed_push_goes_out_on_the_next_refresh(hooked, check_message):
    """The diff records what was *delivered*, not what was attempted.

    The display refreshes several times a second with the same face, so a
    signature burned by a push that raised means that face is never sent again -
    the next refresh compares equal and returns early - and the client's face
    stays wrong until the unit's mood happens to change. Nothing retries on this
    path but the next refresh.
    """
    flaky = FlakyBroadcast(failures=1)
    hooked.plugin.broadcast = flaky

    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))
    assert flaky.sent == [], "the fixture did not fail the first push"

    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    assert len(flaky.sent) == 1, "the face that failed to send was never retried"
    data = check_message(flaky.sent[0], "face_status")
    assert (data["face"], data["status"]) == ("(⌐■_■)", "Hi, I'm TestUnit")


def test_a_face_that_was_delivered_is_not_sent_twice(hooked):
    """The complement, through the same broadcast, so the pair means something.

    Without this, "retry after a failure" is satisfied by a plugin that pushes
    on every refresh and diffs nothing at all.
    """
    steady = FlakyBroadcast(failures=0)
    hooked.plugin.broadcast = steady

    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))
    show(hooked, a_view("(⌐■_■)", "Hi, I'm TestUnit"))

    assert len(steady.sent) == 1, "an unchanged face went out twice"


# ---------------------------------------------------------------------------
# The mood hooks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mood", MOODS)
def test_each_mood_hook_pushes_its_own_mood(hooked, check_message, mood):
    """The `status` half is the text the unit is showing right now (F28): the
    mood names the event, the status is what the display says about it, and a
    client renders the two together."""
    hooked.agent._ui_view = View({"face": "(-__-)", "status": "I'm bored ..."})

    getattr(hooked.plugin, f"on_{mood}")(hooked.agent)

    data = check_message(hooked.only("status_change"), "status_change")
    assert data["mood"] == mood
    assert data["status"] == "I'm bored ..."


def test_a_mood_carries_the_status_the_view_is_showing(hooked, check_message):
    """The complement of the parametrised case: a status that is the same string
    every time would satisfy it, and would tell the user nothing."""
    hooked.agent._ui_view = View({"face": "(⇀‿‿↼)", "status": "Hack the planet"})

    hooked.plugin.on_excited(hooked.agent)

    assert check_message(hooked.only("status_change"), "status_change")["status"] == (
        "Hack the planet"
    )


def test_the_mood_hooks_are_distinguishable(hooked):
    """One handler wired to the wrong constant is invisible in a single-mood
    test and turns the client's mood display into a lie."""
    for mood in MOODS:
        getattr(hooked.plugin, f"on_{mood}")(hooked.agent)

    assert [message["data"]["mood"] for message in hooked.sent] == MOODS


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------


def hook_calls(plugin, agent, capture):
    """Every hook, with arguments the agent would really pass."""
    return {
        "on_handshake": lambda: plugin.on_handshake(agent, str(capture), *STRING_SHAPE),
        "on_peer_detected": lambda: plugin.on_peer_detected(agent, Peer({})),
        "on_wifi_update": lambda: plugin.on_wifi_update(agent, [{"mac": AP_MAC}]),
        "on_channel_hop": lambda: plugin.on_channel_hop(agent, 11),
        "on_ui_update": lambda: plugin.on_ui_update(a_view("(⌐■_■)", "status")),
        "on_bored": lambda: plugin.on_bored(agent),
        "on_excited": lambda: plugin.on_excited(agent),
        "on_lonely": lambda: plugin.on_lonely(agent),
        "on_sad": lambda: plugin.on_sad(agent),
    }


HOOK_NAMES = [
    "on_handshake",
    "on_peer_detected",
    "on_wifi_update",
    "on_channel_hop",
    "on_ui_update",
    "on_bored",
    "on_excited",
    "on_lonely",
    "on_sad",
]


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_a_failing_push_never_reaches_the_agent(hooked, hook):
    """SPEC 2.14: no unhandled exception can kill the loop.

    `plugins.on()` runs the hook on the agent's own thread (F8). An exception
    escaping here is not a lost message, it is a pwnagotchi that stops working
    in a pocket, and the only symptom is a display that stopped changing."""

    def explode(message):
        raise RuntimeError("the client set is on fire")

    hooked.plugin.broadcast = explode
    capture = hooked.capture("TestNet_002_aabbccddee01.pcapng")

    hook_calls(hooked.plugin, hooked.agent, capture)[hook]()


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_a_hook_before_the_plugin_is_wired_pushes_nothing(tmp_path, hook):
    """A plugin whose TLS material was unusable has no router at all (D4, no
    plaintext fallback), and the agent still calls its hooks. Nothing may be
    pushed, and nothing may be raised."""
    plugin = companion.Companion()
    sent: list[dict] = []
    plugin.broadcast = sent.append
    assert plugin._router is None

    capture = tmp_path / "TestNet_002_aabbccddee01.pcapng"
    capture.write_bytes(b"")

    hook_calls(plugin, None, capture)[hook]()

    assert sent == []


@pytest.mark.parametrize("hook", HOOK_NAMES)
def test_a_hook_before_on_ready_pushes_nothing_and_does_not_raise(
    options, harness, tls_material, tmp_path, hook
):
    """`on_loaded` runs before the agent exists: the plugin is loaded at boot and
    only handed the agent later. Every hook is reachable in that window."""
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **options,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }
    plugin.on_loaded()
    sent: list[dict] = []
    plugin.broadcast = sent.append

    capture = tmp_path / "TestNet_002_aabbccddee01.pcapng"
    capture.write_bytes(b"")

    try:
        hook_calls(plugin, None, capture)[hook]()
    finally:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_on_ready_is_what_makes_the_agent_readable(hooked, check_message):
    """`stats` needs the agent, and `on_ready` is the only place it arrives."""
    stats = hooked.plugin._router.stats()

    assert stats["mode"] == "AUTO"
    assert stats["channel"] == hooked.agent._current_channel


def test_on_ready_on_a_plugin_that_refused_to_start_does_not_raise(agent):
    """The agent calls `on_ready` on every loaded plugin, including one that
    logged a TLS failure and set nothing up. That must be uneventful."""
    plugin = companion.Companion()

    plugin.on_ready(agent)

    assert plugin._router is None


def test_the_session_is_refreshed_from_a_background_thread(
    options, agent, tls_material, tmp_path
):
    """SPEC 2.5: `session()` is a blocking HTTP GET with a 30-second timeout and
    must never be on the request path, so something has to be calling it off it.

    This is the one test that lets the real thread run - everywhere else `spawn`
    is recorded and the loop is not started - because "a background thread does
    the polling" is a claim about threading, and a recorded callable cannot show
    that the thread was ever started or that unloading stops it.
    """
    plugin = companion.Companion()
    plugin.deps = companion.Deps(list_local_ipv4=lambda: [])
    plugin.options = {
        **options,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
        "session_poll_interval": 0.05,
        "rebind_interval": 0.05,
        "keepalive_interval": 0.05,
        "gps_source": "none",
    }
    plugin.broadcast = lambda message: None
    plugin.on_loaded()

    try:
        assert agent.session_calls == 0, "polling started before the agent existed"
        plugin.on_ready(agent)

        deadline = time.monotonic() + 20
        while agent.session_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert agent.session_calls > 0, "the background loop never refreshed the session"
    finally:
        plugin.on_unload()

    settled = agent.session_calls
    time.sleep(0.5)
    assert agent.session_calls == settled, (
        "the background loop kept polling after on_unload; on a real unit that "
        "is a thread holding a bettercap connection open across a plugin reload"
    )


def test_a_push_with_nobody_connected_is_not_an_error(hooked, check_message):
    """The hooks fire whether or not anyone is listening - a unit spends most of
    its life with no client at all - so the broadcast path has to be a no-op
    rather than a failure when the set is empty."""
    hooked.plugin.broadcast = companion.Companion.broadcast.__get__(hooked.plugin)

    hooked.plugin.on_channel_hop(hooked.agent, 11)

    assert len(hooked.plugin._clients) == 0


def test_on_unload_is_idempotent(hooked):
    """The agent unloads plugins on shutdown, and a second call - or a call
    after a failed load - must not be the thing that raises during shutdown."""
    hooked.plugin.on_unload()
    hooked.plugin.on_unload()

    assert hooked.plugin._listeners is None


def test_on_unload_before_on_loaded_does_not_raise():
    companion.Companion().on_unload()


def test_on_unload_accepts_the_ui_argument():
    """The agent passes the UI to `on_unload`; a handler declared without it
    raises a TypeError during shutdown, on the one path nobody watches."""
    companion.Companion().on_unload(object())
