"""Interface binding and the reconciliation loop.

`bnep0` usually has no address until the tether is up, so binding cannot be a
one-off at load: it is a reconciliation that runs on a timer and converges as
the link flaps. The table in SPEC 2.3.1 is what these tests walk.

Underneath all of it sits one rule with no exceptions: never `0.0.0.0`, never
`::`, never a hostname (D5, SPEC 11.1). Upstream `pwnios.py` binds
`0.0.0.0:8082` (F22), which on a tethered Pi publishes the socket to every
network it can see. The check watches `socket.bind` itself, so an implementation
cannot satisfy it by reporting the right address while passing another one.
"""

from __future__ import annotations

import socket

import pytest

from plugin import companion

LOOPBACK = "127.0.0.1"
SECOND_LOOPBACK = "127.0.0.2"


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(
        str(tls_material["cert"]), str(tls_material["key"])
    )
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def bind_options(tmp_path, tls_material, free_port):
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    return {
        "interfaces": ["bnep0", "usb0"],
        "ws_port": free_port,
        "http_port": free_port + 1,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(web_root),
        "token": "",
        "rebind_interval": 30,
    }


def is_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


@pytest.fixture
def listeners(bind_options, deps, ssl_context):
    instance = companion.Listeners(bind_options, deps, ssl_context)
    yield instance
    instance.stop()


# ---------------------------------------------------------------------------
# The reconciliation table
# ---------------------------------------------------------------------------


def test_an_interface_that_gains_an_ip_is_bound(
    listeners, harness, bind_options, bind_recorder
):
    harness.interface_ips = {"bnep0": LOOPBACK}

    state = listeners.reconcile()

    assert state["bnep0"] == LOOPBACK
    assert state["usb0"] is None
    assert is_listening(LOOPBACK, bind_options["http_port"])
    bind_recorder.assert_no_wildcard()


def test_an_interface_gets_both_listeners_not_just_the_static_one(
    bind_options, deps, ssl_context, harness
):
    """A resolved interface must carry WSS as well as HTTPS (SPEC 2.3).

    The rest of this file exercises the reconciliation table through the HTTPS
    listener, because it binds synchronously. This is the one test that insists
    the WebSocket listener exists too - the app is useless without it, and an
    HTTPS-only unit looks like it started correctly.
    """

    async def handler(websocket, *_):
        await websocket.wait_closed()

    instance = companion.Listeners(bind_options, deps, ssl_context, handler)
    harness.interface_ips = {"bnep0": LOOPBACK}
    try:
        instance.reconcile()

        assert is_listening(LOOPBACK, bind_options["ws_port"])
    finally:
        instance.stop()


def test_an_interface_that_loses_its_ip_is_closed(listeners, harness, bind_options):
    harness.interface_ips = {"bnep0": LOOPBACK}
    listeners.reconcile()

    harness.interface_ips = {"bnep0": None}
    state = listeners.reconcile()

    assert state["bnep0"] is None
    assert not is_listening(LOOPBACK, bind_options["http_port"])


def test_a_changed_ip_moves_the_listeners(listeners, harness, bind_options):
    harness.interface_ips = {"bnep0": LOOPBACK}
    listeners.reconcile()

    harness.interface_ips = {"bnep0": SECOND_LOOPBACK}
    state = listeners.reconcile()

    assert state["bnep0"] == SECOND_LOOPBACK
    assert is_listening(SECOND_LOOPBACK, bind_options["http_port"])
    assert not is_listening(LOOPBACK, bind_options["http_port"])


def test_an_absent_interface_is_skipped_without_raising(listeners, harness, bind_recorder):
    harness.interface_ips = {}

    assert listeners.reconcile() == {"bnep0": None, "usb0": None}
    assert bind_recorder.addresses == []


def test_an_absent_interface_is_not_logged_on_every_pass(listeners, harness, caplog):
    harness.interface_ips = {}

    with caplog.at_level("DEBUG"):
        listeners.reconcile()
        first = len(caplog.records)
        listeners.reconcile()
        listeners.reconcile()

    assert len(caplog.records) == first, "a missing tether must not spam the log"


def test_a_stable_interface_is_not_rebound_every_pass(listeners, harness, bind_recorder):
    harness.interface_ips = {"bnep0": LOOPBACK}
    listeners.reconcile()
    bound_once = len(bind_recorder.addresses)

    listeners.reconcile()
    listeners.reconcile()

    assert len(bind_recorder.addresses) == bound_once


def test_an_address_already_in_use_is_survivable(listeners, harness, bind_options, caplog):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind((LOOPBACK, bind_options["http_port"]))
    blocker.listen(1)
    harness.interface_ips = {"bnep0": LOOPBACK}

    try:
        with caplog.at_level("WARNING"):
            listeners.reconcile()

        assert caplog.records, "a failed bind must be logged"
    finally:
        blocker.close()

    # The next pass retries rather than giving up on the interface for good.
    listeners.reconcile()

    assert is_listening(LOOPBACK, bind_options["http_port"])


def test_a_failing_interface_does_not_take_down_a_working_one(
    listeners, harness, bind_options
):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind((SECOND_LOOPBACK, bind_options["http_port"]))
    blocker.listen(1)
    harness.interface_ips = {"bnep0": LOOPBACK, "usb0": SECOND_LOOPBACK}

    try:
        listeners.reconcile()

        assert is_listening(LOOPBACK, bind_options["http_port"])
    finally:
        blocker.close()


def test_stop_closes_everything_and_is_idempotent(listeners, harness, bind_options):
    harness.interface_ips = {"bnep0": LOOPBACK}
    listeners.reconcile()

    listeners.stop()
    listeners.stop()

    assert not is_listening(LOOPBACK, bind_options["http_port"])


# ---------------------------------------------------------------------------
# The rule with no exceptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resolved", [LOOPBACK, SECOND_LOOPBACK])
def test_only_the_literal_interface_address_is_ever_bound(
    listeners, harness, bind_recorder, resolved
):
    harness.interface_ips = {"bnep0": resolved}

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()
    assert set(bind_recorder.hosts) == {resolved}


def test_a_wildcard_resolution_is_not_bound(listeners, harness, bind_recorder):
    # If interface resolution ever hands back a wildcard, binding it anyway is
    # the upstream bug (F22), not a convenience.
    harness.interface_ips = {"bnep0": "0.0.0.0"}

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()


def test_a_hostname_is_not_bound(listeners, harness, bind_recorder):
    harness.interface_ips = {"bnep0": "localhost"}

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()


def test_the_interfaces_come_from_the_configuration(
    bind_options, deps, ssl_context, harness
):
    bind_options["interfaces"] = ["wlan9"]
    instance = companion.Listeners(bind_options, deps, ssl_context)
    try:
        instance.reconcile()
    finally:
        instance.stop()

    assert harness.args_for("resolve_interface_ip") == [("wlan9",)]
