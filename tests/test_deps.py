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

import contextlib
import importlib
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

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
def no_smbus2(monkeypatch):
    """Makes `import smbus2` fail, whatever the host has.

    `None` in `sys.modules` is the documented way to make an import raise
    `ImportError` without touching the filesystem.

    `smbus2` alone, because SPEC 2.11 names it and says there is no `smbus`
    fallback. Blocking the older spelling as well would be the friendlier
    fixture and the useless one: it would make the tests below pass against a
    plugin that imported `smbus`, which is the dependency the section removed.
    """
    monkeypatch.setitem(sys.modules, "smbus2", None)


@pytest.fixture
def broken_smbus2(monkeypatch):
    """A `smbus2` that is importable and whose bus refuses to open.

    An I2C bus that is present but answers with `Remote I/O error` is the state
    of a Pi with the bus enabled and nothing at 0x57 on it, and it is the case
    that reaches the plugin's `except` rather than skipping past it.

    Only `smbus2` is faked (SPEC 2.11: no `smbus` fallback), so a plugin that
    reached for the older name would find nothing importable on a build host,
    open no bus at all, and fail the assertion on which bus was opened rather
    than quietly passing.
    """
    opened: list[tuple] = []

    class RefusingBus:
        def __init__(self, *args, **kwargs) -> None:
            opened.append(args)
            raise OSError(121, "Remote I/O error")

    module = types.ModuleType("smbus2")
    module.SMBus = RefusingBus
    monkeypatch.setitem(sys.modules, "smbus2", module)

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
    real_deps, no_smbus2
):
    """The precondition is established here rather than assumed of the host: a
    unit with no `smbus2` installed reports null, it does not fail the poll."""
    assert real_deps.read_pisugar_i2c() == (None, None)


def test_the_battery_read_is_nulls_when_the_bus_refuses_to_open(
    real_deps, broken_smbus2
):
    """SPEC 2.11 wraps the direct read in try/except, and SPEC 2.14 forbids an
    exception escaping. `OSError` from the bus is the common failure and the one
    that would otherwise surface several times a minute."""
    assert real_deps.read_pisugar_i2c() == (None, None)
    assert broken_smbus2 == [(1,)], (
        f"SPEC 2.11 pins the PiSugar to I2C bus 1; the bus opened was {broken_smbus2}"
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


class _FailingCloseSocket:
    """Wraps one real socket so only *its* `close()` raises.

    Patching `socket.socket.close` at the class level, as an earlier version
    of this test did, makes every socket in the process raise on close for
    the duration of the call - including the fake gpsd server's own accepted
    connection, closed inside its `_serve` thread's `with connection:` block.
    That exception has nowhere to go but "Exception in thread", which
    pytest's thread-exception plugin turns into a test error unrelated to
    the guard this test is actually about. Wrapping only the one socket
    `default_read_gpsd` opens - the object `socket.create_connection`
    hands back to it - keeps the failure on the client side alone, which is
    what the guard under test (`default_read_gpsd`'s `finally: sock.close()`)
    is there for in the first place.

    Delegates everything else to the real socket unchanged: `default_read_gpsd`
    also calls `settimeout`, `sendall` and `recv` on the object it gets back.
    """

    def __init__(self, real: socket.socket) -> None:
        self._real = real

    def close(self) -> None:
        self._real.close()
        raise OSError("close exploded")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_failing_socket_close_does_not_cost_the_reading(real_deps, gpsd, monkeypatch):
    """The `finally: sock.close()` around the whole poll is itself guarded: a
    `close()` that raises `OSError` - a socket already torn down under it by
    the peer - must not take the reading it already parsed down with it.

    Only `socket.create_connection` - the one call `default_read_gpsd` makes
    to open its own socket - is patched, and only the object it returns has
    its `close()` made to fail (`_FailingCloseSocket` above). The fake gpsd
    server's listening and accepted sockets, opened directly with
    `socket.socket(...)` in the `gpsd` fixture, are never touched.
    """
    server = gpsd([VERSION_LINE, FIX])
    original_create_connection = socket.create_connection

    def failing_close_create_connection(*args, **kwargs):
        return _FailingCloseSocket(original_create_connection(*args, **kwargs))

    monkeypatch.setattr(socket, "create_connection", failing_close_create_connection)

    reading = real_deps.read_gpsd("127.0.0.1", server.port, 2.0)

    assert reading is not None
    assert reading["lat"] == pytest.approx(-33.512345)


# ---------------------------------------------------------------------------
# The libraries that are only there on a unit
# ---------------------------------------------------------------------------
#
# Everything above runs the default seams as this host finds them, which means
# the halves of `read_pisugar_i2c` and `list_local_ipv4` that need `smbus2` or
# `netifaces` never run: neither is a test dependency, `smbus2` because it wants
# an I2C device node no build host has and `netifaces` because SPEC 2.3 forbids
# a hard dependency on it outright. Injection cannot reach them either - the
# whole point of an injectable seam is to run something else instead.
#
# So the libraries are supplied, as importable packages under `tests/fakes/`,
# installed on `sys.path` for the duration of a test and removed afterwards.
# `tests/fakes/README.md` explains why a package on disk rather than an object
# in `sys.modules`, and why these two stubs are permissive where the pwnagotchi
# stub is exact.


FAKES_DIR = Path(__file__).resolve().parent / "fakes"


@contextlib.contextmanager
def installed_stub(directory: str, *names: str):
    """Imports `names` out of `tests/fakes/<directory>`, then puts it all back.

    Scoped to a `with` block rather than to the session because the *absent*
    library is the state the suite runs in and the *present* one is the
    exception. A stub left on `sys.path` would also shadow the real library on a
    machine that has one, which is the failure a stub must never cause.
    """
    path = str(FAKES_DIR / directory)
    missing = object()
    displaced = {name: sys.modules.pop(name, missing) for name in names}
    sys.path.insert(0, path)
    try:
        modules = [importlib.import_module(name) for name in names]
        for module in modules:
            assert Path(module.__file__).is_relative_to(FAKES_DIR), (
                f"{module.__name__} resolved to {module.__file__}, not to the stub; "
                "a real installation is shadowing it and the test proves nothing"
            )
        yield tuple(modules)
    finally:
        for name in names:
            sys.modules.pop(name, None)
        if path in sys.path:
            sys.path.remove(path)
        for name, module in displaced.items():
            if module is not missing:
                sys.modules[name] = module


@contextlib.contextmanager
def i2c_stub():
    """A working I2C bus, and the device on it.

    Under one name, `smbus2`, because that is the one SPEC names (section 2.11),
    which says outright that there is no `smbus` fallback. A second package
    answering to that name would be unreachable from the code under test, and
    it would advertise `i2c_msg` and a `force` keyword that the real library of
    that name does not have.
    """
    with installed_stub("i2c_stub", "_fake_i2c_device", "smbus2") as modules:
        yield modules[0].device


@contextlib.contextmanager
def netifaces_stub(table=None):
    """An importable `netifaces` reporting `table`, or the reference unit's."""
    with installed_stub("netifaces_stub", "netifaces") as (netifaces,):
        netifaces._reset()
        netifaces._set_table(reference_interfaces(netifaces) if table is None else table)
        yield netifaces


# ---------------------------------------------------------------------------
# read_pisugar_i2c, on a unit that has the bus
# ---------------------------------------------------------------------------


#: SPEC 2.11 and 2.11.1, tier 2: bus 1, address 0x57, register 0x2A for the
#: percentage and bit 7 of register 0x02 for charging, both read as a byte.
PISUGAR_BUS = 1
PISUGAR_ADDRESS = 0x57
PERCENT_REGISTER = 0x2A
CHARGE_REGISTER = 0x02
CHARGE_BIT = 0x80

#: The two bytes SPEC 2.11.1 records from the reference unit at firmware v1.3.8:
#: 0xEC with the cable in, 0x6C with it out. They differ in bit 7 and nowhere
#: else, which is the whole evidence for the bit.
CHARGE_REGISTER_ON_EXTERNAL_POWER = 0xEC
CHARGE_REGISTER_ON_BATTERY = 0x6C


def test_the_percentage_is_read_from_the_pinned_bus_address_and_register():
    """SPEC 2.11 names all three, and each of them can be wrong on its own.

    A test that stubbed the bus and only checked the number that came back would
    pass against a plugin reading register `0x2A` of whatever device answers at
    `0x2B`, or bus 0 on a Pi where bus 0 is the camera connector. The stub
    records the bus it was opened with and every `(address, register)` pair read,
    so all three are assertions rather than assumptions.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 77
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert percent == 77
    assert charging is False, (
        "SPEC 2.11.1 puts charging in bit 7 of 0x02; the stub answers 0x00 there, "
        f"which is not charging, and the read said {charging!r}"
    )
    assert device.opened == [PISUGAR_BUS], (
        f"SPEC 2.11 pins I2C bus 1; the buses opened were {device.opened}"
    )
    assert device.read_addresses() == {PISUGAR_ADDRESS}, (
        f"SPEC 2.11 pins address 0x57; the addresses read were "
        f"{[hex(address) for address in sorted(device.read_addresses())]}"
    )
    assert PERCENT_REGISTER in device.read_registers(PISUGAR_ADDRESS), (
        f"SPEC 2.11 pins register 0x2A for the percentage; the registers read at "
        f"0x57 were {[hex(register) for register in device.read_registers(PISUGAR_ADDRESS)]}"
    )


def test_a_flat_battery_is_a_reading_and_not_an_absent_one():
    """Zero percent is falsy and is also the reading that matters most.

    Reported as null it looks like a unit without a PiSugar, so the app hides
    the battery instead of showing the one number that would have explained why
    the unit is about to stop.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 0
        percent, _ = companion.Deps().read_pisugar_i2c()

    assert percent == 0


@given(level=st.integers(min_value=0, max_value=100))
@settings(max_examples=60, deadline=None)
def test_every_in_range_level_survives_the_read_unchanged(level):
    """The register holds the percentage, so the read is a passthrough.

    Property rather than a handful of cases because the plausible bugs are at
    the edges and in the middle in different ways: a byte masked to 7 bits, a
    value halved from a 0-200 scale, an off-by-one at 100.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = level
        percent, _ = companion.Deps().read_pisugar_i2c()

    assert percent == level


@given(level=st.integers(min_value=101, max_value=255))
@settings(max_examples=60, deadline=None)
def test_a_byte_outside_the_percentage_range_is_not_a_reading(level):
    """A byte that cannot be a percentage must be absent rather than clamped.

    Why a gauge would produce one is not asserted here; nothing observed says,
    and SPEC 2.11.1 declines to guess. What matters is the consequence: passed
    through, 255 becomes "255% battery" on the client, and the schema caps the
    field at 100, so clamping would turn a broken reading into a plausible-
    looking lie."""
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = level
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert percent is None, f"register value {level} was reported as {percent}%"
    assert charging is False, (
        "SPEC 2.11.2: an unusable percentage discards the percentage alone, and 0x02 read "
        f"0x00 here, so charging is False; the read returned {charging!r}"
    )


@pytest.mark.parametrize("filler", [0x00, 0xFF])
def test_the_percentage_comes_from_its_own_register_and_no_other(filler):
    """SPEC 2.11 puts the percentage in `0x2A` alone.

    Every other register is set to the same filler and the answer must not
    move. This is what separates reading the pinned byte from reading a word or
    a block that happens to start there: the neighbouring byte belongs to the
    charging state, not to the percentage, and folding it in produces a number
    that is right at 0x00 and nonsense at 0xFF.
    """
    with i2c_stub() as device:
        device.default_byte = filler
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 42
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert percent == 42
    assert charging is bool(filler & CHARGE_BIT), (
        f"with every unpinned register at {filler:#04x}, charging follows bit 7 of 0x02 "
        f"and nothing else; the read said {charging!r}"
    )


def test_the_battery_read_is_nulls_when_the_bus_opens_and_will_not_answer():
    """A bus that opens and then errors on the transfer is a PiSugar that is
    absent, asleep or on the wrong address - the ordinary case on a unit with
    I2C enabled and nothing attached. SPEC 2.11 and 2.14: nulls, never an
    exception, because this runs on the session poll thread."""
    with i2c_stub() as device:
        device.read_error = OSError(121, "Remote I/O error")
        result = companion.Deps().read_pisugar_i2c()

    assert result == (None, None)
    assert device.opened == [PISUGAR_BUS]
    assert device.reads, "the bus was opened but never read"


# ---------------------------------------------------------------------------
# read_pisugar_i2c: charging, SPEC 2.11.1
# ---------------------------------------------------------------------------
#
# Charging used to be null here because no register was known to carry it. One
# is now: bit 7 of 0x02, read off the reference unit at firmware v1.3.8 and
# recorded in SPEC 2.11.1 with the two bytes it showed. Those two bytes are the
# evidence, so they appear below, but they are never the whole test: a plugin
# that compared the byte against 0xEC would satisfy them and would report a
# discharging unit as charging on any firmware that moves an unrelated bit.


@pytest.mark.parametrize(
    ("register_value", "expected"),
    [
        (CHARGE_REGISTER_ON_EXTERNAL_POWER, True),
        (CHARGE_REGISTER_ON_BATTERY, False),
    ],
    ids=["external-power-0xEC", "battery-0x6C"],
)
def test_charging_follows_the_bytes_observed_on_the_reference_unit(register_value, expected):
    """SPEC 2.11.1: 0xEC with the cable in, 0x6C without it, nothing else moved.

    Identity against `True`/`False` rather than equality, because the wire
    schema types `charging` as a boolean (`common.json#/$defs/Battery`) and an
    `int` of 1 - the natural result of returning the masked bit unshifted -
    serialises as `1`, which that schema rejects.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = register_value
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert charging is expected, (
        f"register 0x02 = {register_value:#04x} has bit 7 "
        f"{'set' if register_value & CHARGE_BIT else 'clear'}, so charging is {expected}; "
        f"the read said {charging!r}"
    )
    assert percent == 89


@given(register_value=st.integers(min_value=0x00, max_value=0xFF))
@settings(max_examples=256, deadline=None)
def test_charging_is_bit_seven_of_the_charge_register_for_every_byte(register_value):
    """The invariant, over the whole byte rather than the two bytes that were
    observed: charging is exactly `bit 7 is set`.

    This is what the two literals above cannot establish on their own. It fails
    an implementation that compares against 0xEC, one that reads a different
    bit, one that inverts the sense, and one that reports "any non-zero byte
    means charging" - which 0x6C, a non-zero byte on battery, already disproves
    on the hardware.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = register_value
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 50
        _, charging = companion.Deps().read_pisugar_i2c()

    assert charging is bool(register_value & CHARGE_BIT), (
        f"register 0x02 = {register_value:#04x} -> charging {charging!r}, "
        f"expected {bool(register_value & CHARGE_BIT)}"
    )


@pytest.mark.parametrize("base", [CHARGE_REGISTER_ON_EXTERNAL_POWER, CHARGE_REGISTER_ON_BATTERY])
@pytest.mark.parametrize("other_bit", [0, 1, 2, 3, 4, 5, 6])
def test_no_other_bit_of_the_charge_register_changes_the_answer(base, other_bit):
    """SPEC 2.11.1: "One bit moves and no other bit of the byte does."

    The other seven bits carry something - a model, a fault flag, a firmware
    state machine - and whatever it is, it is free to change under the plugin
    without the charge icon flickering. Each bit is flipped on its own so a
    failure names the bit that leaked into the answer.
    """
    expected = bool(base & CHARGE_BIT)
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = base ^ (1 << other_bit)
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        _, charging = companion.Deps().read_pisugar_i2c()

    assert charging is expected, (
        f"flipping bit {other_bit} of {base:#04x} changed charging to {charging!r}"
    )


def test_charging_comes_from_its_own_register_and_not_a_neighbour():
    """0x01 and 0x03 are other registers, and on a PiSugar they answer.

    Both are held at 0xFF - bit 7 set - while 0x02 says not charging. An
    off-by-one in the register number, or a word read at 0x02 that folds 0x03
    into the high byte and then tests the wrong bit of the result, reports
    charging on a unit running off its battery.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, 0x01)] = 0xFF
        device.registers[(PISUGAR_ADDRESS, 0x03)] = 0xFF
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_BATTERY
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert charging is False
    assert percent == 89


def test_both_readings_are_byte_reads_of_exactly_their_own_registers():
    """SPEC 2.11.1 settles the byte-versus-word question, and settles it against
    the word read *even where the word read agrees*: 0x2A read as a word is
    right only while 0x2B happens to be zero, and 0x2B is a separate register
    that promises nothing.

    The stub records one `(address, register)` per byte transferred, so a word
    read at 0x2A shows up as a read of 0x2B and a block read of the map shows up
    as a read of everything. The set is asserted exactly, which is the only form
    of this assertion a returned value cannot fake.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert (percent, charging) == (89, True)
    assert set(device.read_registers(PISUGAR_ADDRESS)) == {CHARGE_REGISTER, PERCENT_REGISTER}, (
        "SPEC 2.11.1 pins two byte reads, 0x02 and 0x2A; the registers read at 0x57 were "
        f"{[hex(register) for register in device.read_registers(PISUGAR_ADDRESS)]}"
    )
    assert device.opened == [PISUGAR_BUS]
    assert device.read_addresses() == {PISUGAR_ADDRESS}


def test_a_nonzero_neighbouring_byte_does_not_reach_either_reading():
    """The same point as above, made through the returned values instead of the
    recording, so that neither assertion depends on the other being kept.

    0x2B and 0x03 both hold 0xFF, which is what makes a word read visible: as a
    word, 0x2A becomes 0xFF59 = 65369, far outside a percentage, and 0x02
    becomes 0xFF6C, whose bit 7 is still clear but whose bit 15 is not.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER + 1)] = 0xFF
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_BATTERY
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER + 1)] = 0xFF
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert percent == 89, (
        "0x2A is a byte read (SPEC 2.11.1); folding 0x2B in gives 65369, not 89, "
        f"and the read returned {percent!r}"
    )
    assert charging is False


def test_charging_is_null_and_never_false_when_the_bus_cannot_be_opened(broken_smbus2):
    """SPEC 2.11 tier 3 is `{percent: null, charging: null}`, and the difference
    between null and `False` is the whole point of the field: null hides the
    icon, `False` claims the cable is out. A unit with no bus knows neither.
    """
    percent, charging = companion.Deps().read_pisugar_i2c()

    assert charging is None, f"no bus, and charging was reported as {charging!r}"
    assert percent is None


def test_a_bus_that_will_not_answer_reports_neither_reading():
    """SPEC 2.11 and 2.14: the direct read is wrapped, and nothing escapes it
    onto the session poll thread. Both registers are populated so that a failure
    here is a failure to notice the transfer error, not an empty device."""
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        device.read_error = OSError(121, "Remote I/O error")
        result = companion.Deps().read_pisugar_i2c()

    assert result == (None, None)


def test_a_bus_that_will_not_close_discards_both_readings():
    """SPEC 2.11.2: a close that raises is a transport fault, so the answer is
    `(None, None)` even though both reads had already succeeded and both values
    were in range.

    This is the one case the transport/content line does not decide by itself,
    because the readings were in hand before anything went wrong. SPEC puts it
    with transport: a handle that will not close is evidence about the channel,
    and the alternative is a fourth rule for an event nobody has observed.

    The registers are asserted to have been read and the values are the good
    ones on purpose. Without that, the test would pass against a plugin that
    never got as far as the reads, and the rule it is here to pin - that
    perfectly good values are dropped - would not be the reason it was green.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.close_error = OSError(5, "Input/output error")
        result = companion.Deps().read_pisugar_i2c()

    assert set(device.read_registers(PISUGAR_ADDRESS)) == {CHARGE_REGISTER, PERCENT_REGISTER}, (
        "both registers must have been read and answered, or the close is not what "
        f"discarded them; the registers read were {device.read_registers(PISUGAR_ADDRESS)}"
    )
    assert device.closed, "the bus was never closed, so nothing raised on the way out"
    assert result == (None, None), (
        "SPEC 2.11.2: a bus that will not close is a transport fault, and 89% with the "
        f"cable in is not reported anyway; the read returned {result!r}"
    )


@pytest.mark.parametrize(
    "failing_register",
    [CHARGE_REGISTER, PERCENT_REGISTER],
    ids=["charge-register-naks", "percent-register-naks"],
)
def test_a_register_that_will_not_answer_discards_both_readings(failing_register):
    """SPEC 2.11.2: if *either* read raises, the answer is `(None, None)`.

    Never the half-populated tuple, and the reason is not squeamishness about
    the surviving byte. `(percent, None)` is already the *meaningful* answer of
    tier 1 - a PiSugar plugin that exposes a level and no charge state - so
    reusing that shape for "the bus is misbehaving" would make two unrelated
    situations indistinguishable to `battery_info` one layer up.

    Both directions, because they are separate code paths and a plugin that
    reads the percentage first has an obvious way to keep it and no obvious way
    to keep the charge bit.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 89
        device.read_errors[(PISUGAR_ADDRESS, failing_register)] = OSError(121, "Remote I/O error")
        result = companion.Deps().read_pisugar_i2c()

    assert result == (None, None), (
        f"register {failing_register:#04x} did not answer, so SPEC 2.11.2 discards both "
        f"readings; the read returned {result!r}"
    )
    assert result[1] is not False, "a bus that failed cannot report that the cable is out"


@pytest.mark.parametrize(
    ("charge_value", "expected_charging"),
    [
        (CHARGE_REGISTER_ON_EXTERNAL_POWER, True),
        (CHARGE_REGISTER_ON_BATTERY, False),
    ],
    ids=["external-power-0xEC", "battery-0x6C"],
)
def test_a_percentage_outside_the_range_discards_only_the_percentage(
    charge_value, expected_charging
):
    """SPEC 2.11.2: `0xFF` at `0x2A` is a successful read of a bad value, not a
    failed read. The gauge is nonsense; the charge bit came off another
    register, is one bit wide, and is unaffected. Result: `(None, <the bit>)`.

    Both bit states, and the `False` one is the case that matters: an
    implementation that discards charging whenever the percentage is
    implausible still answers `(None, False)` by accident when the cable is out,
    so the `0x6C` row alone cannot distinguish "kept the bit" from "gave up".
    The `0xEC` row is what forces the bit to be real.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 0xFF
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = charge_value
        percent, charging = companion.Deps().read_pisugar_i2c()

    assert percent is None, f"0xFF is not a percentage; the read returned {percent!r}"
    assert charging is expected_charging, (
        f"0x02 answered {charge_value:#04x} and answered it successfully, so charging is "
        f"{expected_charging} however unusable 0x2A was; the read returned {charging!r}"
    )


def test_transport_failure_and_bad_content_are_told_apart():
    """The line SPEC 2.11.2 draws, asserted as a line rather than as two cases.

    The two runs differ in exactly one thing: whether `0x2A` raised or answered
    a byte that is not a percentage. Everything else - the bus, the address, the
    charge register and its value - is identical, and the percentage is
    unusable either way. So any difference in the answer is attributable to the
    distinction the section is about: a read that did not happen says nothing
    about the register it did not touch, because the fault is in the channel
    both share, while a read that happened and returned nonsense says something
    only about itself.

    An implementation that collapses the two - one `except` that also swallows
    the range check, or a range check that raises - passes each of the tests
    above in one direction and fails here.
    """
    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.read_errors[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = OSError(121, "Remote I/O error")
        transport_fault = companion.Deps().read_pisugar_i2c()

    with i2c_stub() as device:
        device.registers[(PISUGAR_ADDRESS, CHARGE_REGISTER)] = CHARGE_REGISTER_ON_EXTERNAL_POWER
        device.registers[(PISUGAR_ADDRESS, PERCENT_REGISTER)] = 0xFF
        content_fault = companion.Deps().read_pisugar_i2c()

    assert transport_fault == (None, None), transport_fault
    assert content_fault == (None, True), content_fault
    assert transport_fault != content_fault, (
        "a bus that would not answer and a gauge that answered nonsense are different "
        "events (SPEC 2.11.2) and must not produce the same battery reading"
    )


# ---------------------------------------------------------------------------
# list_local_ipv4, on a unit that has netifaces
# ---------------------------------------------------------------------------


#: SPEC D7's two documented hosts, plus a loopback. Synthetic addresses on
#: synthetic interfaces: nothing here describes a real machine.
LOOPBACK_ADDRESS = "127.0.0.1"
TETHER_ADDRESS = "172.20.10.3"
GADGET_ADDRESS = "10.0.0.2"


def reference_interfaces(netifaces) -> dict:
    """What `netifaces` reports on a unit shaped like the reference hardware.

    A loopback, a BT PAN tether, the USB gadget, and a monitor-mode interface
    with a link address and no IPv4 at all - the last one because an
    enumeration that returned one entry per interface rather than one per
    address would otherwise look correct.
    """
    return {
        "lo": {
            netifaces.AF_LINK: [{"addr": "00:00:00:00:00:00"}],
            netifaces.AF_INET: [
                {"addr": LOOPBACK_ADDRESS, "netmask": "255.0.0.0", "peer": "127.0.0.1"}
            ],
        },
        "bnep0": {
            netifaces.AF_LINK: [{"addr": "aa:bb:cc:dd:ee:ff"}],
            netifaces.AF_INET: [
                {
                    "addr": TETHER_ADDRESS,
                    "netmask": "255.255.255.240",
                    "broadcast": "172.20.10.15",
                }
            ],
            netifaces.AF_INET6: [
                {"addr": "fe80::1%bnep0", "netmask": "ffff:ffff:ffff:ffff::/64"}
            ],
        },
        "usb0": {
            netifaces.AF_INET: [{"addr": GADGET_ADDRESS, "netmask": "255.255.255.0"}]
        },
        "wlan0mon": {netifaces.AF_LINK: [{"addr": "11:22:33:44:55:66"}]},
    }


def test_netifaces_is_used_when_it_is_importable():
    """SPEC 2.3: `netifaces` if importable, `ip -4 addr show` otherwise.

    The tether and gadget addresses asserted here are on no interface of the
    machine running the test, so they can only have come from the library. That
    is the whole check: a plugin that ignored `netifaces` and shelled out
    regardless would return this host's own addresses and fail here.
    """
    with netifaces_stub() as netifaces:
        addresses = companion.Deps().list_local_ipv4()
        assert netifaces._queried, "netifaces was importable and was never asked"

    assert set(addresses) == {LOOPBACK_ADDRESS, TETHER_ADDRESS, GADGET_ADDRESS}


def test_an_interface_without_an_ipv4_address_contributes_nothing():
    """`wlan0mon` in the reference table has a link address and no IPv4. An
    enumeration that reported it - as a name, as an empty string, or as its MAC -
    would put something into the bind decision that is not an address."""
    with netifaces_stub():
        addresses = companion.Deps().list_local_ipv4()

    assert "wlan0mon" not in addresses
    for entry in addresses:
        ipaddress.IPv4Address(entry)


def test_the_interface_name_never_reaches_the_result_from_netifaces():
    """D5.1: the enumeration returns addresses, and which interface carries one
    is not consulted and must not reach a bind decision. The names are known
    here, so their absence is checkable rather than merely plausible."""
    with netifaces_stub() as netifaces:
        names = set(reference_interfaces(netifaces))
        addresses = companion.Deps().list_local_ipv4()

    assert not names & set(addresses)


def test_the_wildcard_is_dropped_even_when_netifaces_reports_it():
    """SPEC 2.3.1: the refusal of `0.0.0.0` applies to a wildcard arriving from
    the address enumeration, not only to a configured entry. An unconfigured
    point-to-point interface really does report it, and D5 and section 11.1
    forbid it reaching any bind call unconditionally."""
    with netifaces_stub() as netifaces:
        table = reference_interfaces(netifaces)
        table["ppp0"] = {netifaces.AF_INET: [{"addr": "0.0.0.0", "netmask": "0.0.0.0"}]}
        netifaces._set_table(table)
        addresses = companion.Deps().list_local_ipv4()

    assert "0.0.0.0" not in addresses
    assert TETHER_ADDRESS in addresses, "the rest of the enumeration was lost with it"


def test_a_host_with_no_addresses_at_all_enumerates_to_nothing():
    """Not the same case as a failed enumeration below: here the library
    answered, and the answer is that there is nothing to bind. SPEC 2.3.1 has
    the reconciler skip silently, which it can only do if it is told."""
    with netifaces_stub({}):
        assert companion.Deps().list_local_ipv4() == []


@pytest.mark.parametrize(
    "failure",
    [
        "interfaces",
        "ifaddresses",
    ],
    ids=["interfaces() raises", "every interface raises"],
)
def test_a_netifaces_that_raises_is_a_failure_and_not_an_empty_host(failure):
    """SPEC 2.3.1: "A failure raises rather than returning an empty list."

    `[]` means "this host holds no addresses", which makes the reconciler close
    every listener and drop every client; a failure means "nothing was
    observed", which must leave the bound set alone. A `list[str]` signature has
    no other honest channel, so raising is the signal - and the section has the
    seam let the backend's own exception out rather than wrap it, which is what
    the identity check below pins.

    The exception *type* is deliberately unpinned by that same section, so
    nothing here asserts on `OSError` as such; `test_binding.py` covers what
    `reconcile` does with whatever arrives.
    """
    error = OSError(19, "No such device")
    with netifaces_stub() as netifaces:
        if failure == "interfaces":
            netifaces._fail_interfaces(error)
        else:
            netifaces._fail_ifaddresses(error)

        with pytest.raises(Exception) as raised:
            companion.Deps().list_local_ipv4()

    assert raised.value is error, (
        "SPEC 2.3.1 has the seam let the backend's own exception out; what escaped "
        f"was {raised.value!r}"
    )


def test_one_interface_that_fails_to_enumerate_is_skipped_and_the_rest_stand():
    """SPEC 2.3.1: "One interface failing is not a failure."

    `netifaces` raises for an interface that disappeared between `interfaces()`
    and `ifaddresses()`, and on a pwnagotchi that is routine rather than exotic:
    `wlan0mon` is created and torn down as a matter of course. Collapsing it into
    a failed enumeration costs the whole pass - every address on every other
    interface with it - each time monitor mode is cycled, so the reconciler
    learns nothing precisely while the unit is doing what it exists to do.

    The interface armed to fail carries an address of its own, which is what
    separates a skip from a pass that never reached it: everything except that
    address must come back.
    """
    error = OSError(19, "No such device")

    with netifaces_stub() as netifaces:
        netifaces._fail_ifaddresses(error, ["usb0"])
        addresses = companion.Deps().list_local_ipv4()

    assert set(addresses) == {LOOPBACK_ADDRESS, TETHER_ADDRESS}, (
        "one interface that raised must cost that interface and no more"
    )
    assert GADGET_ADDRESS not in addresses, (
        "the failing interface was never observed, so nothing of it can be reported"
    )


def test_an_enumeration_whose_only_interface_fails_is_a_failure():
    """SPEC 2.3.1: the exception to the rule above is when *every* interface
    enumerated failed, because the pass then observed nothing at all.

    One interface, and it raises. Skipping it would leave the pass with an empty
    list, which is the reconciler's "bound address no longer present locally"
    row: both listeners closed and every client dropped, on a unit whose
    addresses never changed. That is the same defect the whole-enumeration case
    above guards against, arriving one level down where a per-interface `except`
    hides it.
    """
    error = OSError(19, "No such device")

    with netifaces_stub() as netifaces:
        netifaces._set_table(
            {"bnep0": {netifaces.AF_INET: [{"addr": TETHER_ADDRESS}]}}
        )
        netifaces._fail_ifaddresses(error, ["bnep0"])

        with pytest.raises(Exception) as raised:
            companion.Deps().list_local_ipv4()

    assert raised.value is error, (
        "SPEC 2.3.1 has the seam let the backend's own exception out; what escaped "
        f"was {raised.value!r}"
    )


# ---------------------------------------------------------------------------
# list_local_ipv4, on a unit without netifaces
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ip(tmp_path, monkeypatch):
    """Puts an `ip` on `PATH` that prints canned output.

    Not a monkeypatch of the plugin: SPEC 10.7 forbids reaching into module
    internals from a test, and nothing is reached into here. `PATH` is the
    process environment and the fallback genuinely runs whichever `ip` it finds
    there, so this is the same kind of substitution as the stub packages above -
    the environment the code runs in, not the code.

    It buys the cases this host cannot produce on demand: a secondary address, a
    point-to-point peer, an interface with no IPv4 at all, and a command that
    fails.
    """
    directory = tmp_path / "bin"
    directory.mkdir()

    def _install(output: str | None, *, status: int = 0):
        if output is None:
            # An empty PATH is how "there is no ip on this system" is arranged;
            # every other case prepends, so that the script itself still has a
            # shell to run in.
            monkeypatch.setenv("PATH", str(directory))
            return None
        monkeypatch.setenv("PATH", str(directory) + os.pathsep + os.environ["PATH"])
        script = directory / "ip"
        script.write_text(
            "#!/bin/sh\ncat <<'IP_OUTPUT'\n" + output + "\nIP_OUTPUT\nexit " + str(status) + "\n"
        )
        script.chmod(0o755)
        return script

    return _install


#: Output in the shape `ip -4 addr show` produces on a unit, with the parts that
#: separate a parser from a regular expression over the whole file: a secondary
#: address on an interface that already has one, a point-to-point interface
#: whose peer address is *not* an address this host holds, an IPv6 line, and an
#: interface carrying only a link-layer address.
IP_ADDR_OUTPUT = """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
2: bnep0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    inet 172.20.10.3/28 brd 172.20.10.15 scope global dynamic noprefixroute bnep0
       valid_lft 3595sec preferred_lft 3595sec
    inet 172.20.10.9/28 brd 172.20.10.15 scope global secondary bnep0:1
       valid_lft forever preferred_lft forever
    inet6 fe80::1/64 scope link
       valid_lft forever preferred_lft forever
3: usb0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP group default qlen 1000
    inet 10.0.0.2 peer 192.0.2.1/32 scope global usb0
       valid_lft forever preferred_lft forever
4: wlan0mon: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP group default qlen 1000
    link/ieee802.11/radiotap aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff\
"""


def test_the_fallback_reads_every_address_and_only_addresses(fake_ip):
    """The `ip -4 addr show` parse, against output chosen to be hostile.

    Each of the four expected entries fails differently under a lazy parse: the
    secondary is lost by a parser that keeps one address per interface, the peer
    line yields `192.0.2.1` - an address this host does *not* hold, and binding it
    fails - to a parser that takes the last literal on the line, and `bnep0:1`
    and `fe80::1` are what a parser that scans for anything address-shaped picks
    up.
    """
    fake_ip(IP_ADDR_OUTPUT)

    addresses = companion.Deps().list_local_ipv4()

    assert set(addresses) == {"127.0.0.1", "172.20.10.3", "172.20.10.9", "10.0.0.2"}


def test_the_wildcard_is_dropped_by_the_fallback_too(fake_ip):
    """The same rule the `netifaces` path is held to, on the other path.

    An unconfigured point-to-point interface reports `0.0.0.0`, and SPEC 2.3.1
    refuses a wildcard arriving from the address enumeration and not only from
    the configuration. Two implementations of one question must not differ about
    it, and this is the case where the difference would be invisible on a build
    host: no machine running the suite happens to have a `0.0.0.0` to report.
    """
    fake_ip(
        IP_ADDR_OUTPUT
        + "\n5: ppp0: <POINTOPOINT,MULTICAST,NOARP,UP> mtu 1500 qdisc pfifo_fast state UNKNOWN"
        + "\n    inet 0.0.0.0 peer 0.0.0.0/32 scope global ppp0"
    )

    addresses = companion.Deps().list_local_ipv4()

    assert "0.0.0.0" not in addresses
    assert "172.20.10.3" in addresses, "the rest of the enumeration was lost with it"


def test_an_ip_command_that_says_nothing_is_a_host_with_no_addresses(fake_ip):
    """Distinct from the command *failing*, below: `ip` ran, exited zero and
    reported nothing. SPEC 2.3.1's reconciler is entitled to act on that."""
    fake_ip("")

    assert companion.Deps().list_local_ipv4() == []


@pytest.mark.parametrize(
    ("output", "status"),
    [
        (None, 0),
        ("Cannot open netlink socket: Operation not permitted", 1),
    ],
    ids=["no ip binary on PATH at all", "ip exits non-zero"],
)
def test_a_failing_ip_command_is_a_failure_and_not_a_host_with_no_addresses(
    fake_ip, output, status
):
    """SPEC 2.3.1: "A failure raises rather than returning an empty list."

    The same contract as the `netifaces` case, on the other backend, and the
    distinction is the whole point of the reconciliation table's last row. `[]`
    means "this host holds no addresses", which makes the reconciler close every
    listener and drop every client; a failure means "nothing was observed",
    which must leave the bound set alone.

    The type is not pinned - the section says so outright, because nothing
    downstream distinguishes one backend failure from another - so this asserts
    that something escapes and not what. Returning anything at all fails the
    test, `[]` being the return that would do the damage. `test_binding.py` pins
    what `reconcile` then does with what escaped.
    """
    fake_ip(output, status=status)

    with pytest.raises(Exception):
        companion.Deps().list_local_ipv4()


def test_the_fallback_is_not_consulted_when_netifaces_answers(fake_ip):
    """SPEC 2.3 orders the two: `netifaces` if importable, the command
    otherwise. Both are available here and the command's addresses are not in the
    answer, so the order is observed rather than inferred from a call count."""
    fake_ip(IP_ADDR_OUTPUT)

    with netifaces_stub() as netifaces:
        netifaces._set_table({"eth0": {netifaces.AF_INET: [{"addr": "198.51.100.4"}]}})
        addresses = companion.Deps().list_local_ipv4()

    assert addresses == ["198.51.100.4"]


# ---------------------------------------------------------------------------
# The two paths have to agree
# ---------------------------------------------------------------------------


def host_ip_addr_output() -> str:
    """This host's own `ip -4 addr show`, read once.

    Read once and then replayed through `fake_ip`, so that the plugin parses the
    exact bytes the expectation is derived from. Comparing against a *second*
    live invocation is the obvious shape and the wrong one: on a machine with
    container or veth churn the two readings disagree for reasons that have
    nothing to do with the parser, and the test goes red irreproducibly. It also
    wanted `ip -4 -json`, which an older iproute2 does not have.

    The call is live, which is the point - the sample is the one nobody here
    chose - so the two ways it can be unavailable are skips rather than errors.
    A host without `iproute2`, or one where netlink is not permitted, has
    nothing to say about the parser, and a red test there is noise that teaches
    the reader to ignore this file.
    """
    binary = shutil.which("ip")
    if binary is None:
        pytest.skip("this host has no `ip`, so it cannot describe itself")
    result = subprocess.run(
        [binary, "-4", "addr", "show"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        pytest.skip(f"`ip -4 addr show` failed here: {result.stderr.strip()!r}")
    return result.stdout


def addresses_in(output: str) -> set[str]:
    """The IPv4 addresses in `ip -4 addr show` output, parsed here.

    Not circular: a second implementation of the same read, written from SPEC
    2.3.1's description of the format rather than from the plugin, so that the
    comparisons below are against something other than the code under test.

    The `peer` guard is the detail SPEC 2.3.1 singles out and the one this gets
    right by construction: `inet 10.0.0.2 peer 192.0.2.1/32` carries one local
    address and one belonging to the far end of the link, and only the token
    immediately after `inet` is ours. The wildcard is dropped because the same
    section drops it from the enumeration.
    """
    found: set[str] = set()
    for line in output.splitlines():
        tokens = line.split()
        if not tokens or tokens[0] != "inet":
            continue
        candidate = tokens[1].split("/")[0]
        if candidate == "0.0.0.0":
            continue
        try:
            found.add(str(ipaddress.IPv4Address(candidate)))
        except ValueError:
            continue
    return found


def test_the_fallback_finds_exactly_the_addresses_this_host_reports(fake_ip):
    """The `ip -4 addr show` parse, against this host's own output.

    The canned cases above cover the shapes no build machine can produce on
    demand; this one covers whatever the machine running the suite happens to
    look like, which is the only sample nobody here chose. It is what a parser
    that drops a secondary address or mangles a `/32` gets wrong while still
    returning perfectly well-formed literals.

    Live and still deterministic: the output is captured once and replayed, so
    the plugin and the expectation see identical bytes and there is nothing for
    a passing veth to race with.
    """
    output = host_ip_addr_output()
    fake_ip(output)

    assert set(companion.Deps().list_local_ipv4()) == addresses_in(output)


def ip_addr_output_as_netifaces(netifaces) -> dict:
    """`IP_ADDR_OUTPUT` restated as the table `netifaces` reports for that host.

    Written from the *interface structure* of that output, interface by
    interface, and deliberately not from `addresses_in()`: a table built out of
    the reference parser's answer hands the second path the first path's result,
    and the comparison below collapses into "the stub returns what it was
    given". The disagreement this exists to catch would survive that untouched.

    Each of the awkward parts keeps the shape the library reports it in, because
    each is where the two backends can differ: the secondary on `bnep0` is a
    second `AF_INET` entry on the same interface rather than an interface of its
    own, `usb0` carries the far end of the link in `peer` under a `/32` netmask,
    the link-local is `AF_INET6`, and `wlan0mon` has a link address and no IPv4
    at all.
    """
    return {
        "lo": {
            netifaces.AF_INET: [
                {"addr": "127.0.0.1", "netmask": "255.0.0.0", "peer": "127.0.0.1"}
            ]
        },
        "bnep0": {
            netifaces.AF_INET: [
                {
                    "addr": "172.20.10.3",
                    "netmask": "255.255.255.240",
                    "broadcast": "172.20.10.15",
                },
                {
                    "addr": "172.20.10.9",
                    "netmask": "255.255.255.240",
                    "broadcast": "172.20.10.15",
                },
            ],
            netifaces.AF_INET6: [
                {"addr": "fe80::1%bnep0", "netmask": "ffff:ffff:ffff:ffff::/64"}
            ],
        },
        "usb0": {
            netifaces.AF_INET: [
                {
                    "addr": "10.0.0.2",
                    "netmask": "255.255.255.255",
                    "peer": "192.0.2.1",
                }
            ]
        },
        "wlan0mon": {netifaces.AF_LINK: [{"addr": "aa:bb:cc:dd:ee:ff"}]},
    }


def test_both_enumeration_paths_answer_the_same_for_the_same_host(fake_ip):
    """SPEC 2.3 offers two implementations of one question, and a unit runs
    whichever its image happens to have. A disagreement between them is a bug
    that appears only on the machines without the library - or only on the
    machines with it - and is invisible to any test that exercises one path.

    So one host goes through both: the canned `ip` output above, and the same
    host restated as the table `netifaces` would report for it. Compared as
    sets, because SPEC 2.3.1 leaves de-duplication to selection, so an address
    reported twice is not a difference worth failing on while an address present
    in one answer and missing from the other is.

    The two descriptions are written independently of each other and of the
    plugin, so each of the three answers here - the reference parse, the
    fallback, the library path - can move without the others.
    """
    expected = addresses_in(IP_ADDR_OUTPUT)

    fake_ip(IP_ADDR_OUTPUT)
    fallback = set(companion.Deps().list_local_ipv4())

    with netifaces_stub() as netifaces:
        netifaces._set_table(ip_addr_output_as_netifaces(netifaces))
        through_netifaces = set(companion.Deps().list_local_ipv4())

    assert fallback == expected, "the canned host was not read back as written"
    assert through_netifaces == fallback, (
        "one backend sees an address the other does not, for the same host: "
        f"only in the command parse {sorted(fallback - through_netifaces)}, "
        f"only through the library {sorted(through_netifaces - fallback)}"
    )


# ---------------------------------------------------------------------------
# What an enumeration is allowed to hand on
# ---------------------------------------------------------------------------
#
# D5.1 is one sentence long and both paths have to satisfy it: the enumeration
# returns addresses. The tests above establish that it finds the right ones on a
# well-behaved host; these establish that a badly-behaved one cannot push
# anything else through, which is the half no build machine can demonstrate
# because no build machine has an interface in a strange state.


def test_an_entry_without_a_usable_address_is_skipped_and_the_rest_survive():
    """SPEC 2.3.1 requires the enumeration to validate what the backend hands
    it, so this hands it entries a healthy host would not produce: an `AF_INET`
    entry with a `peer` and no `addr`, and one whose `addr` is empty.

    **UNVERIFIED:** whether real `netifaces` ever reports an `AF_INET` entry
    lacking `addr` could not be established - an unnumbered point-to-point
    interface is the plausible source, and nobody has confirmed it. That is why
    the entries are injected by this test rather than volunteered by the stub
    (see the closing rule of `tests/fakes/README.md`): the assertion is about
    what the boundary refuses, and it holds whether or not the backend can
    actually produce this.

    Two ways to get it wrong, failing in opposite directions - raising
    `KeyError` turns one odd interface into a failed enumeration, and appending
    whatever `get('addr')` returned puts `None` or an empty string into the bind
    decision. The address that *is* there must come back, alone.
    """
    with netifaces_stub() as netifaces:
        netifaces._set_table(
            {
                "ppp0": {
                    netifaces.AF_INET: [
                        {"peer": "192.0.2.1", "netmask": "255.255.255.255"},
                        {"addr": ""},
                        {"addr": TETHER_ADDRESS},
                    ]
                }
            }
        )
        addresses = companion.Deps().list_local_ipv4()

    assert addresses == [TETHER_ADDRESS]


@pytest.mark.parametrize(
    "reported",
    [
        "192.0.2.6/24",
        "fe80::1%bnep0",
        "not-an-address",
        "192.0.2",
    ],
    ids=["with a prefix", "an IPv6 literal", "a name", "a truncated literal"],
)
def test_netifaces_cannot_push_a_non_address_through_the_enumeration(reported):
    """D5.1: what comes out is addresses, and an interface name must not reach a
    bind decision. `netifaces` is a C extension reading netlink, so what it
    reports is not the plugin's choice - the enumeration is where a value that
    is not an IPv4 literal has to stop.

    Each of these is something a bind call must never see (SPEC 11.1 forbids a
    hostname outright), and each of them survives a check for "non-empty
    string" or for "contains a dot".
    """
    with netifaces_stub() as netifaces:
        netifaces._set_table(
            {
                "eth0": {
                    netifaces.AF_INET: [{"addr": reported}, {"addr": TETHER_ADDRESS}]
                }
            }
        )
        addresses = companion.Deps().list_local_ipv4()

    assert reported not in addresses
    assert addresses == [TETHER_ADDRESS], "the well-formed address went with it"


def test_the_fallback_cannot_push_a_non_address_through_the_enumeration(fake_ip):
    """The same rule on the other path, against an `inet` line that is shaped
    like an address and is not one. `999.1.1.1` passes any check made of digits
    and dots and fails the only one that matters, which is whether it parses."""
    fake_ip(
        "2: eth0: <BROADCAST,UP> mtu 1500\n"
        "    inet 999.1.1.1/24 scope global eth0\n"
        "3: bnep0: <BROADCAST,UP> mtu 1500\n"
        "    inet 172.20.10.3/28 scope global bnep0"
    )

    addresses = companion.Deps().list_local_ipv4()

    assert addresses == [TETHER_ADDRESS]


# ---------------------------------------------------------------------------
# refresh_gpsd: the poll itself failing (as opposed to a refused connection,
# which read_gpsd already turns into None rather than raising)
# ---------------------------------------------------------------------------


def test_refresh_gpsd_survives_the_seam_raising():
    """`read_gpsd` is trusted to return `None` on a refused connection
    (test_a_gpsd_that_is_not_listening_is_not_an_exception above), but
    `GpsResolver.refresh_gpsd` does not get to assume that of whatever seam it
    was handed - an injected one, or a future default, may raise outright -
    so the background thread that calls this every pass must not die on it."""

    def exploding_read_gpsd(host, port, timeout):
        raise OSError("gpsd connection reset")

    deps = companion.Deps(now=lambda: 1.0, read_gpsd=exploding_read_gpsd)
    options = {"gps_source": "auto", "gpsd_host": "127.0.0.1", "gpsd_port": 2947}
    session = companion.SessionCache(lambda: None, deps)
    resolver = companion.GpsResolver(options, deps, session)

    resolver.refresh_gpsd()  # must not raise

    assert resolver.current() == companion.gps_unavailable()


# ---------------------------------------------------------------------------
# websockets_version: the websockets import shim
# ---------------------------------------------------------------------------
#
# resolve_ws_serve()'s own <14 fallback (`from websockets import serve`) is
# NOT exercised here: under the websockets release pinned in
# tests/requirements.txt, that name is itself a lazy alias into
# `websockets.asyncio.server` (websockets/imports.py), so blocking that one
# submodule to stand in for "an old installation" breaks the fallback import
# too, rather than reaching a genuine pre-14 code path - blocking only
# `websockets.asyncio.server` and asserting the resolver still returns
# something callable raised ModuleNotFoundError instead of passing, which
# would have meant the test was passing for the wrong reason. A faithful
# test needs an actual pre-14 websockets on sys.path, the way
# tests/fakes/i2c_stub and tests/fakes/netifaces_stub stand in for smbus2 and
# netifaces (SPEC 10.7) - left to the implementer as a gap rather than
# guessed at here with a technique already shown not to prove anything.


def test_websockets_version_is_unavailable_when_the_package_cannot_be_imported(monkeypatch):
    monkeypatch.setitem(sys.modules, "websockets", None)

    assert companion.websockets_version() == "unavailable"
