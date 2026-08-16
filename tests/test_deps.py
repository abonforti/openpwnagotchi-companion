"""The real seams behind `Deps`, exercised rather than substituted.

SPEC 10.7 makes every external effect an injectable seam, and every other file
in this suite injects a fake. That leaves the default implementations - the ones
that actually run on the device - as the only code in the plugin nothing drives,
which is precisely backwards: they are the parts that touch a socket, a
subprocess and an I2C bus, and SPEC 2.14 requires each of them to be guarded so
that no failure can kill the loop.

So this file constructs a bare `Deps()` and uses it. Nothing here needs a
pwnagotchi, a GPS receiver or a PiSugar: a refused connection, an absent module
and a command that does not exist are all first-class cases the spec has an
answer for, and they are the cases a unit in the field spends most of its time
in.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import sys
import threading
import types

import pytest

from plugin import companion


@pytest.fixture
def real_deps():
    """The defaults, with nothing injected."""
    return companion.Deps()


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------


def test_spawn_runs_the_target(real_deps):
    ran = threading.Event()

    real_deps.spawn(ran.set)

    assert ran.wait(5), "the spawned target never ran"


def test_spawn_does_not_run_the_target_on_the_caller_thread(real_deps):
    """The session poll and the rebind loop are spawned from `on_loaded`. If
    `spawn` ran them inline, loading the plugin would never return and the agent
    would not finish booting."""
    caller = threading.get_ident()
    seen: list[int] = []
    done = threading.Event()

    def target() -> None:
        seen.append(threading.get_ident())
        done.set()

    real_deps.spawn(target)

    assert done.wait(5)
    assert seen[0] != caller


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [0, 1, 3])
def test_run_command_returns_the_exit_status(real_deps, status):
    assert real_deps.run_command(["/bin/sh", "-c", f"exit {status}"]) == status


def test_a_command_that_does_not_exist_is_a_failure_not_an_exception(real_deps):
    """SPEC 2.14: nothing here may raise into the caller. A missing binary is
    the normal case on an image that does not ship the tool being probed."""
    status = real_deps.run_command(["/nonexistent/companion-test-binary", "--help"])

    assert status != 0


# ---------------------------------------------------------------------------
# list_local_ipv4
# ---------------------------------------------------------------------------


def test_local_addresses_are_ipv4_literals_and_nothing_else(real_deps):
    """D5.1: the enumeration returns addresses. An interface name must not
    survive into what a bind decision is made from, so anything that is not a
    plain IPv4 literal here is a name leaking through."""
    addresses = real_deps.list_local_ipv4()

    assert isinstance(addresses, list)
    for entry in addresses:
        assert isinstance(entry, str)
        # Raises for a name, a CIDR suffix, a scope id or an IPv6 literal.
        ipaddress.IPv4Address(entry)


def test_the_wildcard_is_never_offered_as_a_local_address(real_deps):
    """SPEC 11.1 forbids `0.0.0.0` in a bind call, and upstream binds it (F22).
    The cheapest way for that to come back is for the enumeration to report it
    and for selection to match it honestly."""
    assert "0.0.0.0" not in real_deps.list_local_ipv4()


def test_a_host_running_the_suite_has_at_least_the_loopback(real_deps):
    """Not a tautology: an enumeration that silently returned nothing would pass
    every shape check above while making the plugin bind nothing at all, which
    on a unit looks exactly like a tether that never came up.

    The loopback is named rather than merely counted, because a non-empty list
    is also what a one-address stub returns. Every host that can run this suite
    has 127.0.0.1, and the enumeration is not allowed to filter addresses on its
    own initiative: which addresses are eligible is decided by `bind_addresses`
    (D5.1), one layer up.
    """
    addresses = real_deps.list_local_ipv4()

    assert "127.0.0.1" in addresses, "the loopback is missing from the enumerated addresses"


# ---------------------------------------------------------------------------
# read_pisugar_i2c
# ---------------------------------------------------------------------------


@pytest.fixture
def no_smbus(monkeypatch):
    """Makes `import smbus2` (and `smbus`) fail, whatever the host has.

    `None` in `sys.modules` is the documented way to make an import raise
    `ImportError` without touching the filesystem. Both spellings are blocked so
    that the precondition holds regardless of which the plugin reaches for.
    """
    for name in ("smbus", "smbus2"):
        monkeypatch.setitem(sys.modules, name, None)


@pytest.fixture
def broken_smbus(monkeypatch):
    """A bus library that is importable and whose bus refuses to open.

    An I2C bus that is present but answers with `Remote I/O error` is the state
    of a Pi with the bus enabled and nothing at 0x57 on it, and it is the case
    that reaches the plugin's `except` rather than skipping past it.
    """
    opened: list[tuple] = []

    class RefusingBus:
        def __init__(self, *args, **kwargs) -> None:
            opened.append(args)
            raise OSError(121, "Remote I/O error")

    for name in ("smbus", "smbus2"):
        module = types.ModuleType(name)
        module.SMBus = RefusingBus
        monkeypatch.setitem(sys.modules, name, module)

    return opened


def test_the_battery_read_answers_in_two_parts_and_never_raises(real_deps):
    """SPEC 2.11: the battery never fails.

    Deliberately says nothing about whether this host has a bus. The reference
    hardware is a Pi Zero 2 W with a PiSugar 3 answering at 0x57, so a test that
    demanded nulls would assert a property of the build machine and fail on the
    device it was written for. What holds everywhere is the shape: two values,
    each either absent or of the type the `battery` field carries on the wire,
    and no exception on the way out - this runs on the session poll thread.
    """
    result = real_deps.read_pisugar_i2c()

    assert isinstance(result, tuple) and len(result) == 2, result
    percent, charging = result
    if percent is not None:
        assert isinstance(percent, (int, float)) and not isinstance(percent, bool), percent
        assert 0 <= percent <= 100, percent
    assert charging is None or isinstance(charging, bool), charging


def test_the_battery_read_is_nulls_when_no_bus_library_can_be_imported(
    real_deps, no_smbus
):
    """The precondition is established here rather than assumed of the host: a
    unit with no `smbus2` installed reports null, it does not fail the poll."""
    assert real_deps.read_pisugar_i2c() == (None, None)


def test_the_battery_read_is_nulls_when_the_bus_refuses_to_open(
    real_deps, broken_smbus
):
    """SPEC 2.11 wraps the direct read in try/except, and SPEC 2.14 forbids an
    exception escaping. `OSError` from the bus is the common failure and the one
    that would otherwise surface several times a minute."""
    assert real_deps.read_pisugar_i2c() == (None, None)
    assert broken_smbus == [(1,)], (
        f"SPEC 2.11 pins the PiSugar to I2C bus 1; the bus opened was {broken_smbus}"
    )


# ---------------------------------------------------------------------------
# read_gpsd
# ---------------------------------------------------------------------------


class FakeGpsd:
    """A socket that speaks just enough of the gpsd JSON protocol.

    One connection, one canned script of lines, and the client's first request
    recorded so that the `?WATCH` handshake SPEC 2.12 requires can be asserted
    rather than assumed.
    """

    def __init__(self, lines: list[str], *, answer: bool = True) -> None:
        self.lines = lines
        self.answer = answer
        self.request = b""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._socket.accept()
        except OSError:
            return
        with connection:
            connection.settimeout(2.0)
            try:
                self.request = connection.recv(4096)
                if self.answer:
                    for line in self.lines:
                        connection.sendall((line + "\r\n").encode())
                        # gpsd emits one object per line; sending them in one
                        # write would let a reader that only ever looks at the
                        # first chunk pass by accident.
                    connection.shutdown(socket.SHUT_WR)
                else:
                    # An accepted connection that says nothing: a gpsd that is
                    # up but has no device. The read must time out, not hang.
                    connection.recv(1)
            except OSError:
                pass

    def close(self) -> None:
        self._socket.close()
        self._thread.join(timeout=5)


@pytest.fixture
def gpsd():
    servers: list[FakeGpsd] = []

    def _start(lines: list[str], *, answer: bool = True) -> FakeGpsd:
        server = FakeGpsd(lines, answer=answer)
        servers.append(server)
        return server

    yield _start

    for server in servers:
        server.close()


VERSION_LINE = json.dumps({"class": "VERSION", "release": "3.22"})
DEVICES_LINE = json.dumps({"class": "DEVICES", "devices": []})
NO_FIX = json.dumps({"class": "TPV", "mode": 1, "lat": None, "lon": None})
FIX = json.dumps(
    {
        "class": "TPV",
        "mode": 3,
        "lat": -33.512345,
        "lon": -25.098765,
        "alt": 12.5,
        "eph": 7.5,
        "time": "2025-08-15T09:41:10.000Z",
    }
)


def test_a_tpv_is_read_past_the_lines_that_are_not_one(real_deps, gpsd):
    """gpsd answers the handshake with `VERSION` and `DEVICES` before any
    position. A poll that read one line and gave up would never see a fix."""
    server = gpsd([VERSION_LINE, DEVICES_LINE, FIX])

    reading = real_deps.read_gpsd("127.0.0.1", server.port, 2.0)

    assert reading is not None
    assert reading["class"] == "TPV"
    assert reading["lat"] == pytest.approx(-33.512345)
    assert reading["lon"] == pytest.approx(-25.098765)
    assert reading["mode"] == 3


def test_the_watch_handshake_is_sent(real_deps, gpsd):
    """SPEC 2.12: `?WATCH={"enable":true,"json":true}`. Without it gpsd never
    starts streaming and the poll returns nothing, on a unit that has a fix."""
    server = gpsd([VERSION_LINE, FIX])

    real_deps.read_gpsd("127.0.0.1", server.port, 2.0)

    request = server.request.decode()
    assert "?WATCH=" in request
    payload = json.loads(request.split("?WATCH=", 1)[1].strip().rstrip(";"))
    assert payload["enable"] is True
    assert payload["json"] is True


def test_a_reading_without_a_fix_never_becomes_a_position(real_deps, gpsd):
    """`mode` 1 is "no fix". SPEC 2.12 requires `mode >= 2` and finite lat/lon,
    and does not say which layer drops the rest, so this asserts the end of the
    chain: whatever the socket hands back, no fix comes out of it. Treating a
    mode-1 report as a position puts a null island on the map and into a
    handshake sidecar (D13)."""
    server = gpsd([VERSION_LINE, NO_FIX])

    reading = real_deps.read_gpsd("127.0.0.1", server.port, 2.0)

    assert companion.gps_from_gpsd(reading, 1_755_264_000.0) is None


def test_a_gpsd_that_is_not_listening_is_not_an_exception(real_deps):
    """`gps_source: auto` probes gpsd on a unit that has none. A refused
    connection is the normal answer, not a failure to report."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()

    assert real_deps.read_gpsd("127.0.0.1", closed_port, 1.0) is None


def test_a_silent_gpsd_is_bounded_by_the_timeout(real_deps, gpsd):
    """SPEC 2.12: socket timeout <= 2 s. This runs on the background thread, so
    a hang here stops the session cache refreshing and every client's stats go
    stale without an error anywhere."""
    server = gpsd([], answer=False)

    assert real_deps.read_gpsd("127.0.0.1", server.port, 1.0) is None


def test_a_line_that_is_not_json_does_not_stop_the_poll(real_deps, gpsd):
    """The socket carries whatever is on it. One damaged line must cost that
    line."""
    server = gpsd(["{not json at all", VERSION_LINE, FIX])

    reading = real_deps.read_gpsd("127.0.0.1", server.port, 2.0)

    assert reading is not None
    assert reading["lat"] == pytest.approx(-33.512345)
