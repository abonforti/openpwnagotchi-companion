"""`stats`: the field mapping, the four mode states, and the blocking-call rule.

The rule that matters most here is not a field: `agent.session()` is a
synchronous HTTP GET with a 30-second timeout (SPEC F7), so one call from a
request handler stalls every connected client for as long as bettercap takes to
answer. `get_stats` must make exactly zero of them, and the fake agent counts.
"""

from __future__ import annotations

import pytest

from conftest import FakeAgent
from plugin import companion


# ---------------------------------------------------------------------------
# The blocking-call rule
# ---------------------------------------------------------------------------


def test_get_stats_never_calls_session(router, agent):
    router.handle({"type": "get_stats"}, authenticated=True)

    assert agent.session_calls == 0


def test_stats_never_calls_session(router, agent):
    router.stats()

    assert agent.session_calls == 0


def test_initial_burst_never_calls_session(router, agent):
    router.initial_burst()

    assert agent.session_calls == 0


def test_session_cache_is_what_calls_session(agent, deps):
    cache = companion.SessionCache(lambda: agent, deps, 5)

    assert cache.refresh() is True
    assert agent.session_calls == 1


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def test_stats_reports_uptime_and_temperature_from_the_module(router, stub_pwnagotchi):
    stub_pwnagotchi._UPTIME = 7331
    stub_pwnagotchi._TEMPERATURE = 51

    data = router.stats()

    assert data["uptime"] == 7331
    assert data["temperature"] == 51


def test_stats_reads_the_channel_attribute_not_the_session(router_factory, agent_factory):
    agent = agent_factory(current_channel=11)

    data = router_factory(agent).stats()

    assert data["channel"] == 11
    assert agent.session_calls == 0


def test_stats_counts_peers_and_access_points_from_the_underscore_collections(
    router_factory, agent_factory
):
    from pwnagotchi.mesh.peer import Peer

    agent = agent_factory(peers={"one": Peer({"name": "alpha"}), "two": Peer({"name": "beta"})})

    data = router_factory(agent).stats()

    assert data["peers"] == 2
    assert data["accessPoints"] == len(agent._access_points)


def test_handshake_counts_split_pcapng_from_the_honest_total(router):
    data = router.stats()

    # Three .pcapng and one legacy .pcap in the fixture directory. The first
    # number is what the e-ink display shows, because the stock counter globs
    # *.pcapng only (SPEC F15); the second is the truth.
    assert data["handshakes"] == 3
    assert data["handshakesTotal"] == 4
    assert data["handshakesTotal"] >= data["handshakes"]


def test_handshake_counts_ignore_the_session_only_collection(router_factory, agent_factory):
    agent = agent_factory(handshakes={"a": {}, "b": {}, "c": {}, "d": {}, "e": {}})

    data = router_factory(agent).stats()

    # len(agent._handshakes) is the current session only and must not leak in.
    assert data["handshakes"] == 3
    assert data["handshakesTotal"] == 4


def test_last_handshake_is_the_newest_directory_entry(router, handshake_dir):
    newest = handshake_dir / "TestNet_001_aabbccddeeff.pcapng"
    import os

    os.utime(newest, (2_000_000_000, 2_000_000_000))

    last = router.stats()["lastHandshake"]

    assert last["filename"] == "TestNet_001_aabbccddeeff.pcapng"
    assert last["ssid"] == "TestNet_001"
    assert last["bssid"] == "aabbccddeeff"
    assert last["mtime"] == pytest.approx(2_000_000_000)


def test_last_handshake_is_null_for_an_empty_directory(router_factory, agent_factory, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    agent = agent_factory(
        config={
            "bettercap": {"handshakes": str(empty)},
            "main": {"log": {"path": str(tmp_path / "nothing.log")}},
        }
    )

    assert router_factory(agent).stats()["lastHandshake"] is None


def test_last_peer_is_the_most_recently_seen(router_factory, agent_factory):
    from pwnagotchi.mesh.peer import Peer

    older = Peer({"name": "older", "identity": "1111"}, last_seen=1000.0)
    newer = Peer({"name": "newer", "identity": "2222"}, last_seen=2000.0)
    agent = agent_factory(peers={"a": older, "b": newer})

    last = router_factory(agent).stats()["lastPeer"]

    assert last["name"] == "newer"
    assert last["lastSeen"] == 2000.0


def test_last_peer_is_null_without_peers(router):
    assert router.stats()["lastPeer"] is None


def test_session_age_is_null_until_a_refresh_succeeds(router):
    assert router.stats()["sessionAge"] is None


def test_session_age_counts_from_the_last_successful_refresh(
    agent_factory, harness, bettercap_session
):
    agent = agent_factory(session_payload=bettercap_session)
    cache = companion.SessionCache(lambda: agent, harness.deps, 5)
    cache.refresh()
    harness.advance(12.5)

    assert cache.age == pytest.approx(12.5)
    assert cache.snapshot["gps"]["Latitude"] == pytest.approx(-33.512345)


def test_session_cache_survives_a_failing_session_call(harness):
    class Exploding(FakeAgent):
        def session(self):
            self.session_calls += 1
            raise OSError("bettercap is not answering")

    agent = Exploding()
    cache = companion.SessionCache(lambda: agent, harness.deps, 5)

    assert cache.refresh() is False
    assert cache.snapshot == {}
    assert cache.age is None


# ---------------------------------------------------------------------------
# Mode: the four states
# ---------------------------------------------------------------------------


def test_mode_is_manual_when_the_agent_is_in_manual(router_factory, agent_factory):
    agent = agent_factory(mode="manual")

    assert router_factory(agent).stats()["mode"] == "MANUAL"


def test_mode_is_manual_even_when_pasv_reports_passive(router_factory, agent_factory, load_pasv):
    load_pasv(passive=True)
    agent = agent_factory(mode="manual")

    assert router_factory(agent).stats()["mode"] == "MANUAL"


def test_mode_is_auto_without_the_pasv_plugin(router_factory, agent_factory):
    agent = agent_factory(mode="auto")

    assert router_factory(agent).stats()["mode"] == "AUTO"


def test_mode_is_auto_when_pasv_is_loaded_but_not_passive(
    router_factory, agent_factory, load_pasv
):
    load_pasv(passive=False)
    agent = agent_factory(mode="auto")

    assert router_factory(agent).stats()["mode"] == "AUTO"


def test_mode_is_pasv_in_auto_while_the_plugin_reports_passive(
    router_factory, agent_factory, load_pasv
):
    load_pasv(passive=True)
    agent = agent_factory(mode="auto")

    assert router_factory(agent).stats()["mode"] == "PASV"


def test_mode_survives_a_pasv_plugin_without_the_attribute(
    router_factory, agent_factory, stub_plugins
):
    class Bare:
        pass

    stub_plugins.loaded["pasv_mode"] = Bare()
    agent = agent_factory(mode="auto")

    assert router_factory(agent).stats()["mode"] == "AUTO"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_capabilities_pasv_follows_the_loaded_registry(router, load_pasv):
    assert router.capabilities()["pasv"] is False

    load_pasv()

    assert router.capabilities()["pasv"] is True


def test_capabilities_echo_the_configured_gps_source_not_the_active_one(
    router_factory, agent, harness
):
    harness.gpsd_reply = {"class": "TPV", "mode": 3, "lat": -33.5, "lon": -25.0}
    router = router_factory(agent, overrides={"gps_source": "auto"})

    assert router.capabilities()["gpsSource"] == "auto"


def test_capabilities_carry_the_plugin_version(router_factory, agent):
    router = router_factory(agent, plugin_version="9.9.9")

    assert router.capabilities()["pluginVersion"] == "9.9.9"


def test_capabilities_pisugar_is_false_when_no_provider_answers(router, harness):
    harness.pisugar_reply = (None, None)

    assert router.capabilities()["pisugar"] is False


def test_capabilities_pisugar_is_true_once_a_provider_answers(router_factory, agent, harness):
    harness.pisugar_reply = (87.0, True)
    router = router_factory(agent)
    router.stats()

    assert router.capabilities()["pisugar"] is True


def test_stats_embeds_capabilities_and_battery_and_gps(router):
    data = router.stats()

    assert set(data["capabilities"]) == {"pasv", "pisugar", "gpsSource", "pluginVersion"}
    assert set(data["battery"]) == {"percent", "charging"}
    assert data["gps"]["enabled"] in (True, False)


def test_stats_has_exactly_the_schema_fields(router):
    expected = {
        "uptime", "mode", "channel", "battery", "temperature", "handshakes",
        "handshakesTotal", "peers", "accessPoints", "lastHandshake", "lastPeer",
        "gps", "sessionAge", "capabilities",
    }

    assert set(router.stats()) == expected
