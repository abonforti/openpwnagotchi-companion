"""GPS: source priority, the uniform shape, and the sidecar contract.

Two rules carry the weight here.

`piFix` is what the client gates on, not the source name: bettercap and gpsd are
both on-Pi sources and both outrank the browser, whose reading must never
overwrite a device fix.

Sidecars go to disk in bettercap's own shape with capitalised keys (D13), so the
stock `gps.py` plugin and `webgpsmap` keep reading them. The wire shape is the
normalised read of that file and is never what gets written. A sidecar is never
written without a fix - no coordinates is not `0.0, 0.0` - and an existing one is
never overwritten.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from plugin import companion

GPS_FIELDS = {
    "enabled", "source", "piFix", "fix", "lat", "lon", "altitude", "accuracy", "updated",
}

NOW = 1_755_264_000.0

BETTERCAP_FIX = {
    "Updated": "2025-08-15T09:41:10.000000000Z",
    "Latitude": -33.512345,
    "Longitude": -25.098765,
    "Altitude": 12.5,
    "FixQuality": "1",
    "NumSatellites": 9,
    "HDOP": 1.4,
}

GPSD_FIX = {
    "class": "TPV",
    "mode": 3,
    "lat": -33.512345,
    "lon": -25.098765,
    "alt": 12.5,
    "eph": 7.5,
    "time": "2025-08-15T09:41:10.000Z",
}


# ---------------------------------------------------------------------------
# The uniform shape
# ---------------------------------------------------------------------------


def test_nothing_available_is_still_the_same_shape():
    empty = companion.gps_unavailable()

    assert set(empty) == GPS_FIELDS
    assert empty["enabled"] is False
    assert empty["fix"] is False
    assert empty["piFix"] is False
    assert empty["source"] is None
    assert all(
        empty[key] is None for key in ("lat", "lon", "altitude", "accuracy", "updated")
    )


@pytest.mark.parametrize(
    "builder",
    [
        lambda: companion.gps_from_bettercap(BETTERCAP_FIX, NOW),
        lambda: companion.gps_from_gpsd(GPSD_FIX, NOW),
        lambda: companion.gps_from_browser(-33.5, -25.0, 18.0, NOW),
    ],
)
def test_every_source_produces_the_same_field_set(builder):
    assert set(builder()) == GPS_FIELDS


# ---------------------------------------------------------------------------
# bettercap
# ---------------------------------------------------------------------------


def test_bettercap_capitalised_keys_are_read():
    gps = companion.gps_from_bettercap(BETTERCAP_FIX, NOW)

    assert gps["lat"] == pytest.approx(-33.512345)
    assert gps["lon"] == pytest.approx(-25.098765)
    assert gps["altitude"] == pytest.approx(12.5)
    assert gps["source"] == "bettercap"
    assert gps["fix"] is True
    assert gps["piFix"] is True
    assert gps["enabled"] is True


def test_bettercap_accuracy_comes_from_hdop():
    gps = companion.gps_from_bettercap(BETTERCAP_FIX, NOW)

    assert gps["accuracy"] == pytest.approx(7.0)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"Latitude": 0.0, "Longitude": 0.0},
        {"Latitude": -33.5, "Longitude": 0.0},
        {"Latitude": 0.0, "Longitude": -25.0},
        {"Latitude": None, "Longitude": -25.0},
        {"Longitude": -25.0},
        {"Latitude": float("nan"), "Longitude": -25.0},
        {"Latitude": float("inf"), "Longitude": -25.0},
        {"Latitude": "not a number", "Longitude": -25.0},
    ],
)
def test_bettercap_without_a_lock_is_not_a_fix(raw):
    # bettercap reports 0.0/0.0 when it has no lock, which is a point off the
    # coast of Ghana rather than a position.
    assert companion.gps_from_bettercap(raw, NOW) is None


def test_bettercap_without_hdop_reports_no_accuracy_rather_than_a_guess():
    gps = companion.gps_from_bettercap(
        {"Latitude": -33.5, "Longitude": -25.0}, NOW
    )

    assert gps["accuracy"] is None
    assert gps["altitude"] is None


# ---------------------------------------------------------------------------
# HDOP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hdop,expected",
    [(1.0, 5.0), (1.4, 7.0), (2.0, 10.0), ("1.4", 7.0), (0.5, 2.5)],
)
def test_hdop_converts_to_metres(hdop, expected):
    assert companion.hdop_to_metres(hdop) == pytest.approx(expected)


@pytest.mark.parametrize(
    "hdop", [None, "", "abc", 0, 0.0, -1.0, float("nan"), float("inf"), [], {}]
)
def test_an_unusable_hdop_is_an_honest_absence(hdop):
    assert companion.hdop_to_metres(hdop) is None


@given(hdop=st.floats(min_value=0.01, max_value=99.0, allow_nan=False, allow_infinity=False))
@settings(max_examples=200, deadline=None)
def test_hdop_is_monotonic_and_positive(hdop):
    metres = companion.hdop_to_metres(hdop)

    assert metres is not None
    assert metres > 0
    assert companion.hdop_to_metres(hdop * 2) >= metres


@given(hdop=st.one_of(st.floats(), st.text(), st.none(), st.integers(), st.booleans()))
@settings(max_examples=300, deadline=None)
def test_hdop_never_raises_and_never_returns_a_non_finite_number(hdop):
    metres = companion.hdop_to_metres(hdop)

    assert metres is None or (isinstance(metres, float) and math.isfinite(metres) and metres > 0)


# ---------------------------------------------------------------------------
# gpsd
# ---------------------------------------------------------------------------


def test_gpsd_tpv_with_a_3d_fix():
    gps = companion.gps_from_gpsd(GPSD_FIX, NOW)

    assert gps["source"] == "gpsd"
    assert gps["piFix"] is True
    assert gps["fix"] is True
    assert gps["lat"] == pytest.approx(-33.512345)
    assert gps["altitude"] == pytest.approx(12.5)


def test_gpsd_a_non_numeric_mode_is_not_a_fix():
    tpv = {"class": "TPV", "mode": "not-a-number", "lat": -33.5, "lon": -25.0}

    assert companion.gps_from_gpsd(tpv, NOW) is None


@pytest.mark.parametrize(
    "tpv",
    [
        {"class": "TPV", "mode": 3, "lat": 0.0, "lon": -25.0},
        {"class": "TPV", "mode": 3, "lat": -33.5, "lon": 0.0},
    ],
    ids=["lat-zero", "lon-zero"],
)
def test_gpsd_an_axis_at_exactly_zero_is_not_a_fix(tpv):
    # Mirrors the bettercap rule: 0.0 on either axis alone is "no lock", not
    # a real reading that happens to sit off the coast of Ghana.
    assert companion.gps_from_gpsd(tpv, NOW) is None


def test_gpsd_two_dimensional_fix_is_still_a_fix():
    gps = companion.gps_from_gpsd(
        {"class": "TPV", "mode": 2, "lat": -33.5, "lon": -25.0}, NOW
    )

    assert gps["fix"] is True
    assert gps["altitude"] is None


@pytest.mark.parametrize(
    "tpv",
    [
        None,
        {},
        {"class": "TPV", "mode": 0, "lat": -33.5, "lon": -25.0},
        {"class": "TPV", "mode": 1, "lat": -33.5, "lon": -25.0},
        {"class": "TPV", "mode": 3, "lon": -25.0},
        {"class": "TPV", "mode": 3, "lat": None, "lon": -25.0},
        {"class": "TPV", "mode": 3, "lat": float("nan"), "lon": -25.0},
        {"class": "TPV", "mode": 3, "lat": float("inf"), "lon": -25.0},
        {"class": "TPV", "mode": "3", "lat": "here", "lon": "there"},
    ],
)
def test_gpsd_without_a_usable_fix_is_none(tpv):
    assert companion.gps_from_gpsd(tpv, NOW) is None


@given(
    lat=st.floats(min_value=-90, max_value=90, allow_nan=False, allow_infinity=False),
    lon=st.floats(min_value=-180, max_value=180, allow_nan=False, allow_infinity=False),
    mode=st.integers(min_value=-2, max_value=5),
    extra=st.dictionaries(st.text(max_size=6), st.integers(), max_size=4),
)
@settings(max_examples=300, deadline=None)
def test_gpsd_parsing_never_raises_and_only_claims_a_fix_when_it_has_one(lat, lon, mode, extra):
    # SPEC 2.12 now states the rule the suite deliberately left unpinned: as with
    # bettercap, EITHER coordinate being exactly zero means the receiver has not
    # locked, whatever mode it reports. Excluding only the origin was the honest
    # placeholder while that was undecided.
    assume(lat != 0 and lon != 0)
    tpv = {"class": "TPV", "mode": mode, "lat": lat, "lon": lon}
    tpv.update({key: value for key, value in extra.items() if key not in tpv})

    gps = companion.gps_from_gpsd(tpv, NOW)

    if mode is not None and mode >= 2:
        assert gps is not None
        assert gps["fix"] is True
        assert gps["piFix"] is True
        assert gps["source"] == "gpsd"
        assert set(gps) == GPS_FIELDS
    else:
        assert gps is None


# ---------------------------------------------------------------------------
# browser
# ---------------------------------------------------------------------------


def test_the_browser_never_claims_a_pi_fix():
    gps = companion.gps_from_browser(-33.5, -25.0, 18.0, NOW)

    assert gps["source"] == "browser"
    assert gps["piFix"] is False
    assert gps["fix"] is True
    assert gps["accuracy"] == pytest.approx(18.0)
    assert gps["updated"] == pytest.approx(NOW)


def test_the_browser_may_omit_its_accuracy():
    assert companion.gps_from_browser(-33.5, -25.0, None, NOW)["accuracy"] is None


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------


def make_resolver(options, harness, agent, session_payload=None):
    from plugin.companion import GpsResolver, SessionCache

    agent._session_payload = session_payload or {}
    cache = SessionCache(lambda: agent, harness.deps)
    cache.refresh()
    return GpsResolver(options, harness.deps, cache)


def test_bettercap_outranks_gpsd_and_the_browser(options, harness, agent):
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": BETTERCAP_FIX})
    resolver.refresh_gpsd()
    resolver.set_browser_position(1.0, 2.0, 5.0)

    assert resolver.current()["source"] == "bettercap"


def test_gpsd_outranks_the_browser(options, harness, agent):
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": {"Latitude": 0.0, "Longitude": 0.0}})
    resolver.refresh_gpsd()
    resolver.set_browser_position(1.0, 2.0, 5.0)

    current = resolver.current()

    assert current["source"] == "gpsd"
    assert current["piFix"] is True


def test_the_browser_is_used_when_the_pi_has_nothing(options, harness, agent):
    harness.gpsd_reply = None
    resolver = make_resolver(options, harness, agent, {})
    resolver.refresh_gpsd()
    resolver.set_browser_position(-33.5, -25.0, 18.0)

    current = resolver.current()

    assert current["source"] == "browser"
    assert current["piFix"] is False


def test_no_source_at_all_is_the_empty_shape(options, harness, agent):
    resolver = make_resolver(options, harness, agent, {})

    assert resolver.current() == companion.gps_unavailable()


def test_gps_source_none_disables_every_source(options, harness, agent):
    options["gps_source"] = "none"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": BETTERCAP_FIX})
    resolver.refresh_gpsd()
    resolver.set_browser_position(-33.5, -25.0, 18.0)

    assert resolver.current()["enabled"] is False
    assert resolver.current()["source"] is None


def test_gps_source_bettercap_ignores_gpsd(options, harness, agent):
    options["gps_source"] = "bettercap"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": {"Latitude": 0.0, "Longitude": 0.0}})
    resolver.refresh_gpsd()

    assert resolver.current()["source"] is None
    assert ("read_gpsd" in harness.names()) is False


def test_gps_source_gpsd_ignores_bettercap(options, harness, agent):
    options["gps_source"] = "gpsd"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": BETTERCAP_FIX})
    resolver.refresh_gpsd()

    assert resolver.current()["source"] == "gpsd"


def test_a_browser_position_expires(options, harness, agent):
    resolver = make_resolver(options, harness, agent, {})
    resolver.set_browser_position(-33.5, -25.0, 18.0)
    harness.advance(companion.BROWSER_GPS_TTL + 0.5)

    assert resolver.current()["source"] is None
    assert resolver.current()["enabled"] is False


def test_a_browser_position_inside_the_window_is_kept(options, harness, agent):
    resolver = make_resolver(options, harness, agent, {})
    resolver.set_browser_position(-33.5, -25.0, 18.0)
    harness.advance(companion.BROWSER_GPS_TTL - 0.5)

    assert resolver.current()["source"] == "browser"


def test_gpsd_is_polled_from_the_background_pass_not_from_current(options, harness, agent):
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {})

    resolver.current()

    assert "read_gpsd" not in harness.names()

    resolver.refresh_gpsd()

    assert "read_gpsd" in harness.names()


def test_gpsd_is_polled_with_the_configured_endpoint_and_a_bounded_timeout(
    options, harness, agent
):
    options["gpsd_host"] = "192.0.2.10"
    options["gpsd_port"] = 2948
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent)

    resolver.refresh_gpsd()

    host, port, timeout = harness.args_for("read_gpsd")[0]
    assert (host, port) == ("192.0.2.10", 2948)
    assert 0 < timeout <= 2


# ---------------------------------------------------------------------------
# `gpsd_host` must be an address literal (SPEC 2.4/§gpsd source, issue #163)
# ---------------------------------------------------------------------------
#
# `getaddrinfo` runs before the connect and honours no timeout the caller can
# set, so a hostname behind an unresponsive resolver would block the poll -
# in the same background thread that also refreshes the session cache - for
# as long as the resolver takes. Refusing a hostname when the config is read
# is the bounded answer; the plugin must still come up, and every other gps
# source must keep working, which is the "degrades rather than takes the
# unit down" rule SPEC 2.2.1 states as standing policy.


def test_an_ipv6_literal_gpsd_host_is_polled_normally(options, harness, agent):
    # 2001:db8::/32 is the IETF documentation range, RFC 3849: unrelated to
    # any real network, unmistakably so.
    options["gpsd_host"] = "2001:db8::1"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent)

    resolver.refresh_gpsd()

    host, _port, _timeout = harness.args_for("read_gpsd")[0]
    assert host == "2001:db8::1"


def test_a_hostname_gpsd_host_never_reaches_read_gpsd(options, harness, agent):
    """The refusal itself: `read_gpsd` stands in for the connect *and* the
    name resolution that must precede it (`getaddrinfo`), so "never called"
    is the only observable proxy this suite has for "no resolver lookup was
    attempted". A literal host, `192.0.2.10`, is polled freely
    (`test_gpsd_is_polled_with_the_configured_endpoint_and_a_bounded_timeout`
    above); a hostname must never get that far, on any refresh pass.
    """
    options["gpsd_host"] = "gpsd.example.invalid"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent)

    resolver.refresh_gpsd()
    resolver.refresh_gpsd()

    assert "read_gpsd" not in harness.names()


def test_constructing_the_resolver_over_a_hostname_does_not_raise(options, harness, agent):
    """The refusal must not be an exception that could take the plugin down
    with it - SPEC 2.2.1's standing rule, restated here for this specific
    refusal. Building the resolver, and asking it for a reading, must both
    complete normally.
    """
    options["gpsd_host"] = "gpsd.example.invalid"

    resolver = make_resolver(options, harness, agent, {})
    resolver.refresh_gpsd()

    assert resolver.current() == companion.gps_unavailable()


def test_a_hostname_gpsd_host_does_not_disable_other_gps_sources(options, harness, agent):
    """The refusal costs only gpsd: with `gps_source` left at its default
    `auto`, a bettercap fix must still surface exactly as it would with no
    gpsd configured at all - one bad source degrades, the resolver as a
    whole does not.
    """
    options["gpsd_host"] = "gpsd.example.invalid"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": BETTERCAP_FIX})

    resolver.refresh_gpsd()
    resolver.set_browser_position(1.0, 2.0, 5.0)

    assert resolver.current()["source"] == "bettercap"
    assert "read_gpsd" not in harness.names()


def test_gps_source_forced_to_gpsd_with_a_hostname_is_unavailable_not_a_crash(
    options, harness, agent
):
    """`gps_source = "gpsd"` restricts the resolver to gpsd alone
    (`test_gps_source_gpsd_ignores_bettercap` above); paired with a refused
    hostname there is nothing left to report, and the honest answer is the
    same uniform "nothing available" shape every other empty case uses, not
    an internal error.
    """
    options["gps_source"] = "gpsd"
    options["gpsd_host"] = "gpsd.example.invalid"
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent, {"gps": BETTERCAP_FIX})

    resolver.refresh_gpsd()

    assert resolver.current() == companion.gps_unavailable()
    assert "read_gpsd" not in harness.names()


def test_a_hostname_gpsd_host_does_not_stop_the_plugin_from_starting(
    harness, tls_material, tmp_path
):
    """The whole-plugin half of the refusal: SPEC 2.2.1's standing rule is
    that a bad value degrades the plugin, never takes it down. A unit whose
    `config.toml` was handed a hostname for `gpsd_host` must still come up
    with a working router and its listeners reconciled - the same "plugin
    did not come up" failure `test_config_keys.py` guards against for an
    unusable TLS pair must not happen here either.
    """
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **companion.DEFAULTS,
        "enabled": True,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
        "gpsd_host": "gpsd.example.invalid",
    }
    try:
        plugin.on_loaded()

        assert plugin._router is not None
        plugin._listeners.reconcile()
    finally:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# The values a person actually types by hand, rather than the hostname every
# #163 test above uses. "gpsd.example.invalid" is a string, and reaches the
# validator's ValueError branch inside address-literal parsing; an empty
# string and a bare int both have to be turned away before that parse is
# even attempted, so neither exercises the same code path the hostname
# tests do.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_host",
    ["", 2947],
    ids=["empty-string", "int-host-port-transposition"],
)
def test_an_unparsable_gpsd_host_never_reaches_read_gpsd(options, harness, agent, bad_host):
    """An empty string is what clearing the field by hand produces; a bare
    int (`gpsd_host = 2947`) is what typing the port number into the host
    key produces. Both must be refused exactly as a hostname is
    (`test_a_hostname_gpsd_host_never_reaches_read_gpsd` above) - `read_gpsd`
    stands in for the resolver lookup and the connect together, so this is
    the same "never called" proxy that test uses, for the two shapes it
    does not cover.
    """
    options["gpsd_host"] = bad_host
    harness.gpsd_reply = GPSD_FIX
    resolver = make_resolver(options, harness, agent)

    resolver.refresh_gpsd()
    resolver.refresh_gpsd()

    assert "read_gpsd" not in harness.names()


@pytest.mark.parametrize(
    "bad_host",
    ["", 2947],
    ids=["empty-string", "int-host-port-transposition"],
)
def test_constructing_the_resolver_over_an_unparsable_gpsd_host_does_not_raise(
    options, harness, agent, bad_host
):
    """SPEC 2.2.1's standing rule again, for an empty string and a bare int
    rather than for a hostname: neither may raise while the resolver is
    built or while it is asked for a reading, and the honest answer for
    both is the same uniform "nothing available" shape.
    """
    options["gpsd_host"] = bad_host

    resolver = make_resolver(options, harness, agent, {})
    resolver.refresh_gpsd()

    assert resolver.current() == companion.gps_unavailable()


@pytest.mark.parametrize(
    "bad_host",
    ["", 2947],
    ids=["empty-string", "int-host-port-transposition"],
)
def test_an_unparsable_gpsd_host_does_not_stop_the_plugin_from_starting(
    harness, tls_material, tmp_path, bad_host
):
    """The whole-plugin half of the refusal, for an empty string and a bare
    int rather than for a hostname - mirrors
    test_a_hostname_gpsd_host_does_not_stop_the_plugin_from_starting above.
    """
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **companion.DEFAULTS,
        "enabled": True,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
        "gpsd_host": bad_host,
    }
    try:
        plugin.on_loaded()

        assert plugin._router is not None
        plugin._listeners.reconcile()
    finally:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# Sidecars on disk
# ---------------------------------------------------------------------------


def test_a_sidecar_is_written_in_bettercap_shape(handshake_dir):
    capture = handshake_dir / "no-bssid-here.pcapng"
    store = companion.HandshakeStore(str(handshake_dir))
    gps = companion.gps_from_bettercap(BETTERCAP_FIX, NOW)

    path = store.write_sidecar(str(capture), gps, NOW)

    written = json.loads(Path(path).read_text())
    assert written["Latitude"] == pytest.approx(-33.512345)
    assert written["Longitude"] == pytest.approx(-25.098765)
    assert "Updated" in written
    assert "lat" not in written and "lon" not in written


def test_a_non_bettercap_source_is_labelled_without_breaking_the_shape(handshake_dir):
    capture = handshake_dir / "no-bssid-here.pcapng"
    store = companion.HandshakeStore(str(handshake_dir))
    gps = companion.gps_from_browser(-33.5, -25.0, 18.0, NOW)

    path = store.write_sidecar(str(capture), gps, NOW)

    written = json.loads(Path(path).read_text())
    assert written["Source"] == "browser"
    assert written["Latitude"] == pytest.approx(-33.5)
    assert written["Longitude"] == pytest.approx(-25.0)


def test_a_pcap_capture_gets_the_right_sidecar_name(handshake_dir):
    capture = handshake_dir / "Legacy_Net_112233445566.pcap"
    (handshake_dir / "Legacy_Net_112233445566.gps.json").unlink()
    store = companion.HandshakeStore(str(handshake_dir))

    path = store.write_sidecar(
        str(capture), companion.gps_from_bettercap(BETTERCAP_FIX, NOW), NOW
    )

    assert path.endswith("Legacy_Net_112233445566.gps.json")
    assert not (handshake_dir / "Legacy_Net_112233445566.pcap.gps.json").exists()


def test_no_sidecar_is_written_without_a_fix(handshake_dir):
    capture = handshake_dir / "no-bssid-here.pcapng"
    store = companion.HandshakeStore(str(handshake_dir))

    assert store.write_sidecar(str(capture), companion.gps_unavailable(), NOW) is None
    assert not (handshake_dir / "no-bssid-here.gps.json").exists()


def test_an_existing_sidecar_is_never_overwritten(handshake_dir):
    capture = handshake_dir / "TestNet_001_aabbccddeeff.pcapng"
    sidecar = handshake_dir / "TestNet_001_aabbccddeeff.gps.json"
    before = sidecar.read_text()
    store = companion.HandshakeStore(str(handshake_dir))

    result = store.write_sidecar(
        str(capture),
        companion.gps_from_browser(1.0, 2.0, 3.0, NOW),
        NOW,
    )

    assert sidecar.read_text() == before
    assert result is None


def test_sidecar_payload_omits_accuracy_when_there_is_none():
    # No HDOP means gps_from_bettercap reports accuracy: None; the on-disk
    # shape must then drop the key rather than write "Accuracy": null.
    gps = companion.gps_from_bettercap({"Latitude": -33.5, "Longitude": -25.0}, NOW)
    assert gps["accuracy"] is None

    payload = companion.sidecar_payload(gps, NOW)

    assert "Accuracy" not in payload


def test_sidecar_payload_is_the_disk_shape_not_the_wire_shape():
    payload = companion.sidecar_payload(
        companion.gps_from_bettercap(BETTERCAP_FIX, NOW), NOW
    )

    assert "Latitude" in payload and "Longitude" in payload and "Updated" in payload
    assert set(payload).isdisjoint({"lat", "lon", "accuracy", "source", "piFix"})


# ---------------------------------------------------------------------------
# Sidecars read back
# ---------------------------------------------------------------------------


def test_a_sidecar_is_normalised_on_read():
    normalised = companion.normalise_sidecar(BETTERCAP_FIX)

    assert set(normalised) == {"lat", "lon", "accuracy", "source"}
    assert normalised["lat"] == pytest.approx(-33.512345)
    assert normalised["lon"] == pytest.approx(-25.098765)
    assert normalised["accuracy"] == pytest.approx(7.0)


def test_a_sidecar_records_the_source_it_was_written_from():
    raw = dict(BETTERCAP_FIX)
    raw["Source"] = "gpsd"

    assert companion.normalise_sidecar(raw)["source"] == "gpsd"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"Latitude": 0.0, "Longitude": 0.0},
        {"Latitude": -33.5},
        {"Latitude": "x", "Longitude": "y"},
    ],
)
def test_a_malformed_or_fixless_sidecar_reads_as_nothing(raw):
    assert companion.normalise_sidecar(raw) is None


def test_a_sidecar_source_outside_the_three_known_ones_reads_as_null_source():
    # "Source" is free text on disk (nothing else writes this file), so a
    # value that is not one of the three the plugin itself ever writes must
    # not be echoed back to a client uncritically.
    raw = dict(BETTERCAP_FIX)
    raw["Source"] = "some-other-tool"

    normalised = companion.normalise_sidecar(raw)

    assert normalised["source"] is None


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------


def test_get_gps_data_replies_with_a_gps_update(router):
    reply = router.handle({"type": "get_gps_data"}, authenticated=True)[-1]

    assert reply["type"] == "gps_update"
    assert set(reply["data"]) == GPS_FIELDS


def test_gps_data_stores_the_browser_position(router):
    router.handle(
        {"type": "gps_data", "latitude": -33.5, "longitude": -25.0, "accuracy": 18.0},
        authenticated=True,
    )

    reply = router.handle({"type": "get_gps_data"}, authenticated=True)[-1]

    assert reply["data"]["source"] == "browser"
    assert reply["data"]["lat"] == pytest.approx(-33.5)
