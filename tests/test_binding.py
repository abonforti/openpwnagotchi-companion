"""Address binding and the reconciliation loop.

Listeners are chosen by `bind_addresses`, a list of literal IPv4 addresses and
CIDR blocks (D5.1, SPEC 2.3.1). An interface name identifies a device, not a
network - `bnep0` is whichever Bluetooth peer connected first - so no bind
decision may be taken from one, and the only seam these tests drive is
`Deps.list_local_ipv4() -> list[str]`, which returns addresses and nothing else.

Two tables in SPEC 2.3.1 are the contract, and both are walked here: entry
validation at load, and the reconciliation the `rebind_interval` timer runs.

Underneath all of it sits one rule with no exceptions: never `0.0.0.0`, never
`::`, never a hostname (D5, SPEC 11.1). Upstream `pwnios.py` binds
`0.0.0.0:8082` (F22), which on a tethered Pi publishes the socket to every
network it can see. The check watches `socket.bind` itself, so an implementation
cannot satisfy it by reporting the right address while passing another one.

The plugin's own state is only ever observed through what reached the kernel:
which sockets accept a connection, and which addresses `socket.bind` saw. SPEC
does not pin a return value for `reconcile()`, so nothing here asserts one.
"""

from __future__ import annotations

import socket
import time
from typing import Any, Callable

import pytest

from plugin import companion

# All of these are bindable on Linux, which is what lets a selection rule be
# checked against a real listener rather than against bookkeeping.
LOOPBACK = "127.0.0.1"
SECOND_LOOPBACK = "127.0.0.2"
THIRD_LOOPBACK = "127.0.0.3"
UNMATCHED_LOOPBACK = "127.0.0.9"

# TEST-NET-1 (RFC 5737). Never configured on a host, so a bind attempt raises
# EADDRNOTAVAIL - which is the OSError row of the reconciliation table.
ABSENT_ADDRESS = "192.0.2.7"


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def is_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


async def idle_handler(websocket, *_):
    """The connection handler seam of SPEC 2.3.3, reduced to holding the socket.

    Every `Listeners` here is built with one so that the WSS listener exists in
    every test: a unit that came up with HTTPS only looks healthy and is useless.
    """
    await websocket.wait_closed()


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(
        str(tls_material["cert"]), str(tls_material["key"])
    )
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def make_listeners(tmp_path, tls_material, free_ports, deps, ssl_context):
    """Builds a `Listeners` over a configuration whose only variable is binding."""
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    started: list[Any] = []

    def _make(bind_addresses: Any = ..., over_deps: Any = None, **overrides: Any):
        options: dict[str, Any] = {
            "ws_port": free_ports[0],
            "http_port": free_ports[1],
            "tls_cert": str(tls_material["cert"]),
            "tls_key": str(tls_material["key"]),
            "web_root": str(web_root),
            "token": "",
            "rebind_interval": 30,
        }
        if bind_addresses is not ...:
            options["bind_addresses"] = bind_addresses
        options.update(overrides)
        instance = companion.Listeners(
            options, over_deps or deps, ssl_context, idle_handler
        )
        started.append(instance)
        return instance

    yield _make

    for instance in started:
        instance.stop()


@pytest.fixture
def ports(free_ports) -> tuple[int, int]:
    return free_ports


# ---------------------------------------------------------------------------
# Selection: literal, block, and no match at all
# ---------------------------------------------------------------------------


def test_a_literal_entry_binds_the_matching_local_address(
    make_listeners, harness, ports, bind_recorder
):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])
    assert set(bind_recorder.hosts) == {LOOPBACK}
    bind_recorder.assert_no_wildcard()


def test_a_local_address_inside_a_block_is_bound(make_listeners, harness, ports):
    """The block form exists because the host part comes from the phone's DHCP.

    `127.0.0.3` appears in no entry literally; it is selected only by falling
    inside `127.0.0.0/28`.
    """
    harness.local_ipv4 = [THIRD_LOOPBACK]
    listeners = make_listeners(["127.0.0.0/28"])

    listeners.reconcile()

    assert is_listening(THIRD_LOOPBACK, ports[1])


def bind_count(recorder, host: str, port: int) -> int:
    """How many times `socket.bind` saw exactly this endpoint."""
    return recorder.addresses.count((host, port))


@pytest.mark.parametrize(
    "entries",
    [[LOOPBACK, "127.0.0.0/28"], ["127.0.0.0/28", LOOPBACK], [LOOPBACK, "127.0.0.0/28", LOOPBACK]],
    ids=["literal-then-block", "block-then-literal", "literal-twice-and-block"],
)
def test_an_address_matching_several_entries_is_bound_once(
    make_listeners, harness, ports, bind_recorder, entries
):
    """SPEC 2.3.1: "An address that matches more than one entry is bound once."

    A literal entry and a block containing it are the configuration an owner
    ends up with after adding their current DHCP lease to the shipped subnet.
    Selection driven per entry rather than per address would try to bind the
    same endpoint twice: the second attempt fails with `EADDRINUSE` and, under
    the OSError row of the same section, gets logged and retried forever - a
    healthy unit that reports a permanent bind failure once every pass.

    Counting reaches `socket.bind` itself, so it also catches the variant where
    the duplicate attempt is made and swallowed.
    """
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners(entries)

    listeners.reconcile()

    assert wait_until(lambda: is_listening(LOOPBACK, ports[0])), "no WSS listener"
    assert is_listening(LOOPBACK, ports[1]), "no HTTPS listener"
    assert bind_count(bind_recorder, LOOPBACK, ports[1]) == 1, (
        "the HTTPS endpoint was bound more than once for one address"
    )
    assert bind_count(bind_recorder, LOOPBACK, ports[0]) == 1, (
        "the WSS endpoint was bound more than once for one address"
    )
    assert len(bind_recorder.addresses) == 2, (
        "one selected address means exactly two listeners and nothing else"
    )


@pytest.mark.parametrize(
    "entries, reported, selected",
    [
        (["127.0.0.0/28"], [LOOPBACK, LOOPBACK], [LOOPBACK]),
        (
            [LOOPBACK, SECOND_LOOPBACK],
            [LOOPBACK, SECOND_LOOPBACK, LOOPBACK],
            [LOOPBACK, SECOND_LOOPBACK],
        ),
        ([LOOPBACK], [LOOPBACK, UNMATCHED_LOOPBACK, LOOPBACK], [LOOPBACK]),
        ([LOOPBACK], [LOOPBACK, LOOPBACK, LOOPBACK], [LOOPBACK]),
    ],
    ids=["adjacent", "split-by-another-selected", "split-by-an-unselected", "three-times"],
)
def test_an_address_the_enumeration_repeats_is_bound_once(
    make_listeners, harness, ports, bind_recorder, entries, reported, selected
):
    """SPEC 2.3.1: "and so is an address the enumeration itself reports more than once".

    The same IPv4 can be configured on two interfaces, and a backend may list it
    under both a primary and a secondary label, so the enumeration is not a set.
    A pass driven per reported address rather than per distinct address binds the
    same endpoint twice; the second attempt fails with `EADDRINUSE` and is then
    retried on every pass, which reads as a permanent fault on a healthy unit.

    The repeat is checked adjacent and separated, because a de-duplication that
    only compares an address against the previous one survives the first shape.
    """
    harness.local_ipv4 = list(reported)
    listeners = make_listeners(entries)

    listeners.reconcile()

    for address in selected:
        assert wait_until(lambda a=address: is_listening(a, ports[0])), (
            f"no WSS listener on {address}"
        )
        assert is_listening(address, ports[1]), f"no HTTPS listener on {address}"
        assert bind_count(bind_recorder, address, ports[1]) == 1, (
            f"the HTTPS endpoint of {address} was bound more than once"
        )
        assert bind_count(bind_recorder, address, ports[0]) == 1, (
            f"the WSS endpoint of {address} was bound more than once"
        )
    assert len(bind_recorder.addresses) == 2 * len(selected), (
        "a repeated address must add no listener beyond the two it already has"
    )
    assert set(bind_recorder.hosts) == set(selected)


def test_the_bind_count_guard_is_live(bind_recorder, ports):
    """The guard-of-the-guard, as for the wildcard check above.

    "Bound once" passes trivially against a recorder that cannot see a second
    bind at all. This binds the same endpoint twice - the second attempt fails
    with `EADDRINUSE`, which is exactly the shape a duplicated selection would
    produce - and requires the count used above to report 2.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as first:
        first.bind((LOOPBACK, ports[1]))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as second:
            with pytest.raises(OSError):
                second.bind((LOOPBACK, ports[1]))

    assert bind_count(bind_recorder, LOOPBACK, ports[1]) == 2


def test_a_local_address_matching_no_entry_gets_no_listener(
    make_listeners, harness, ports, bind_recorder
):
    """The security property of the ticket: presence on the host is not consent."""
    harness.local_ipv4 = [UNMATCHED_LOOPBACK]
    listeners = make_listeners([LOOPBACK, "10.0.0.2"])

    listeners.reconcile()

    assert bind_recorder.addresses == []
    assert not is_listening(UNMATCHED_LOOPBACK, ports[1])
    assert not is_listening(UNMATCHED_LOOPBACK, ports[0])


def test_only_the_matching_addresses_of_several_are_bound(
    make_listeners, harness, ports
):
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK, UNMATCHED_LOOPBACK]
    listeners = make_listeners([LOOPBACK, SECOND_LOOPBACK])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])
    assert is_listening(SECOND_LOOPBACK, ports[1])
    assert not is_listening(UNMATCHED_LOOPBACK, ports[1])


def test_a_selected_address_gets_both_listeners_not_just_the_static_one(
    make_listeners, harness, ports
):
    """WSS as well as HTTPS (SPEC 2.3). The app is useless without both."""
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    listeners.reconcile()

    assert wait_until(lambda: is_listening(LOOPBACK, ports[0])), "no WSS listener"
    assert is_listening(LOOPBACK, ports[1]), "no HTTPS listener"


# ---------------------------------------------------------------------------
# Entry validation, the first table of SPEC 2.3.1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", [LOOPBACK, "127.0.0.0/24", "127.0.0.0/28", "127.0.0.1/32"])
def test_an_accepted_entry_produces_a_listener(make_listeners, harness, ports, entry):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([entry])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])


@pytest.mark.parametrize("entry", ["127.0.0.0/23", "127.0.0.0/16", "127.0.0.0/8", "0.0.0.0/0"])
def test_a_block_wider_than_a_slash_24_is_refused(
    make_listeners, harness, ports, bind_recorder, caplog, entry
):
    """The `/24` floor is what stops the block syntax spelling "everything"."""
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners([entry])
        listeners.reconcile()

    assert bind_recorder.addresses == [], "a refused block must contribute no listener"
    assert not is_listening(LOOPBACK, ports[1])
    assert [r for r in caplog.records if r.levelname == "ERROR"], (
        "a refused entry must say so at error level"
    )


def test_a_block_with_host_bits_set_is_normalised_to_its_network(
    make_listeners, harness, ports
):
    """`127.0.0.5/28` means the network, not the host: `127.0.0.1` is inside it.

    Treated as the literal `127.0.0.5` instead, this address would get nothing.
    """
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners(["127.0.0.5/28"])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])


@pytest.mark.parametrize(
    "entry",
    [
        "bnep0",
        "localhost",
        "example.test",
        "::1",
        "2001:db8::/32",
        "999.1.1.1",
        "127.0.0.1.5",
        "127.0.0.1/",
        "127.0.0.1/33",
        "127.0.0.1-127.0.0.9",
        "",
        "  ",
    ],
)
def test_an_unparseable_entry_is_rejected_and_the_rest_still_bind(
    make_listeners, harness, ports, caplog, entry
):
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners([entry, LOOPBACK])
        listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1]), "a valid neighbour entry must still bind"
    assert not is_listening(SECOND_LOOPBACK, ports[1]), (
        "a rejected entry must not select anything"
    )
    assert [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize(
    "value",
    [LOOPBACK, "127.0.0.0/24", 8082, None, True, {"addr": LOOPBACK}, ("127.0.0.1",)],
    ids=["string", "cidr-string", "int", "none", "bool", "dict", "tuple"],
)
def test_a_bind_addresses_that_is_not_a_list_binds_nothing(
    make_listeners, harness, ports, bind_recorder, caplog, value
):
    """A bare string is iterable; iterating it would turn a typo into garbage."""
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(value)
        listeners.reconcile()

    assert bind_recorder.addresses == []
    assert not is_listening(LOOPBACK, ports[1])
    assert [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize("value", [None, "172.20.10.0/28", 8082], ids=["none", "string", "int"])
def test_a_non_list_does_not_fall_back_to_the_default_list(
    make_listeners, harness, bind_recorder, value
):
    """"Treated as empty" is not "treated as absent".

    A configuration the plugin cannot read must bind nothing; quietly reverting
    to the shipped defaults would bind a network the operator did not choose,
    which is exactly the "never fall back to a default that binds more" rule.
    """
    harness.local_ipv4 = ["172.20.10.9", "10.0.0.2"]
    listeners = make_listeners(value)

    listeners.reconcile()

    assert bind_recorder.addresses == []


def test_an_empty_list_binds_nothing(
    make_listeners, harness, ports, bind_recorder, caplog
):
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners([])
        listeners.reconcile()

    assert bind_recorder.addresses == []
    assert [r for r in caplog.records if r.levelname == "ERROR"]


def test_every_entry_rejected_means_zero_listeners_and_no_wider_fallback(
    make_listeners, harness, ports, bind_recorder, caplog
):
    """"Nothing configured" must never degrade into "bind more"."""
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK, UNMATCHED_LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(["0.0.0.0/0", "not-an-address", "::1"])
        listeners.reconcile()

    assert bind_recorder.addresses == []
    bind_recorder.assert_no_wildcard()
    for address in (LOOPBACK, SECOND_LOOPBACK, UNMATCHED_LOOPBACK):
        assert not is_listening(address, ports[1])
    assert [r for r in caplog.records if r.levelname == "ERROR"]


@pytest.mark.parametrize("address", ["172.20.10.9", "10.0.0.2"])
def test_the_default_is_the_two_tether_networks(
    make_listeners, harness, bind_recorder, address
):
    """A missing key falls back to the SPEC 2.2 default, not to something wider.

    Neither address exists on the machine running this, so the bind attempt is
    expected to fail; that it was *attempted* is what proves the default block
    and the default literal are both honoured, and the failure exercises the
    OSError row without needing a privileged setup.
    """
    harness.local_ipv4 = [address]
    listeners = make_listeners()

    listeners.reconcile()

    assert address in bind_recorder.hosts, "the documented default must select this"
    bind_recorder.assert_no_wildcard()


def test_the_default_does_not_reach_an_address_outside_it(make_listeners, harness, ports):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners()

    listeners.reconcile()

    assert not is_listening(LOOPBACK, ports[1])


# ---------------------------------------------------------------------------
# The legacy key
# ---------------------------------------------------------------------------


def test_a_legacy_interfaces_key_binds_nothing(
    make_listeners, harness, ports, bind_recorder, caplog
):
    """Honouring `interfaces` silently would preserve the exposure D5.1 closes."""
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK]

    with caplog.at_level("WARNING"):
        listeners = make_listeners([], interfaces=["lo", "bnep0"])
        listeners.reconcile()

    assert bind_recorder.addresses == []
    assert not is_listening(LOOPBACK, ports[1])
    assert any("bind_addresses" in r.getMessage() for r in caplog.records), (
        "the warning must name the key that replaced it"
    )


def test_a_legacy_interfaces_key_does_not_widen_a_valid_configuration(
    make_listeners, harness, ports
):
    harness.local_ipv4 = [LOOPBACK, SECOND_LOOPBACK]
    listeners = make_listeners([LOOPBACK], interfaces=["lo"])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])
    assert not is_listening(SECOND_LOOPBACK, ports[1]), (
        "`lo` carries both loopback addresses; only the configured one may bind"
    )


# ---------------------------------------------------------------------------
# The reconciliation table
# ---------------------------------------------------------------------------


def test_an_address_that_appears_later_is_bound(make_listeners, harness, ports, caplog):
    """The tether has no address at load; the timer is what brings it up."""
    harness.local_ipv4 = []
    listeners = make_listeners(["127.0.0.0/28"])
    listeners.reconcile()
    assert not is_listening(LOOPBACK, ports[1])

    harness.local_ipv4 = [LOOPBACK]
    with caplog.at_level("INFO"):
        listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])
    assert wait_until(lambda: is_listening(LOOPBACK, ports[0]))
    assert [r for r in caplog.records if r.levelname == "INFO"], "a new bind is logged"


def test_an_address_that_disappears_closes_both_listeners(
    make_listeners, harness, ports
):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners(["127.0.0.0/28"])
    listeners.reconcile()
    assert wait_until(lambda: is_listening(LOOPBACK, ports[0]))

    harness.local_ipv4 = []
    listeners.reconcile()

    assert not is_listening(LOOPBACK, ports[1]), "the HTTPS listener must close"
    assert wait_until(lambda: not is_listening(LOOPBACK, ports[0])), (
        "the WSS listener must close too"
    )


def test_a_new_address_in_the_same_block_moves_the_listeners(
    make_listeners, harness, ports
):
    """A fresh DHCP lease inside the hotspot subnet converges without a reload."""
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners(["127.0.0.0/28"])
    listeners.reconcile()

    harness.local_ipv4 = [SECOND_LOOPBACK]
    listeners.reconcile()

    assert is_listening(SECOND_LOOPBACK, ports[1])
    assert not is_listening(LOOPBACK, ports[1])


def test_no_matching_address_is_skipped_without_raising(
    make_listeners, harness, bind_recorder
):
    harness.local_ipv4 = [UNMATCHED_LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    listeners.reconcile()
    listeners.reconcile()

    assert bind_recorder.addresses == []


def test_no_matching_address_is_not_logged_on_every_pass(make_listeners, harness, caplog):
    harness.local_ipv4 = []

    with caplog.at_level("DEBUG"):
        listeners = make_listeners([LOOPBACK])
        listeners.reconcile()
        first = len(caplog.records)
        listeners.reconcile()
        listeners.reconcile()

    assert len(caplog.records) == first, "a missing tether must not spam the log"


def test_a_stable_address_is_not_rebound_every_pass(
    make_listeners, harness, bind_recorder
):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])
    listeners.reconcile()
    bound_once = len(bind_recorder.addresses)

    listeners.reconcile()
    listeners.reconcile()

    assert len(bind_recorder.addresses) == bound_once


def test_an_address_already_in_use_is_survivable(make_listeners, harness, ports, caplog):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind((LOOPBACK, ports[1]))
    blocker.listen(1)
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    try:
        with caplog.at_level("WARNING"):
            listeners.reconcile()

        assert [r for r in caplog.records if r.levelname == "WARNING"], (
            "a failed bind must be logged at warning"
        )
    finally:
        blocker.close()

    # The next pass retries rather than giving up on the address for good.
    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])


def test_an_address_that_cannot_be_bound_does_not_take_down_a_working_one(
    make_listeners, harness, ports, caplog
):
    harness.local_ipv4 = [ABSENT_ADDRESS, LOOPBACK]

    with caplog.at_level("WARNING"):
        listeners = make_listeners([LOOPBACK, "192.0.2.0/24"])
        listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])
    assert [r for r in caplog.records if r.levelname == "WARNING"]


def test_an_unbindable_address_is_retried_and_never_substituted(
    make_listeners, harness, bind_recorder, caplog
):
    """`EADDRNOTAVAIL` is transient, so it is retried - on the same address only.

    The failure mode this guards against is a loop that, unable to bind what was
    configured, binds something else.
    """
    harness.local_ipv4 = [ABSENT_ADDRESS, UNMATCHED_LOOPBACK]

    with caplog.at_level("WARNING"):
        listeners = make_listeners(["192.0.2.0/24"])
        listeners.reconcile()
        listeners.reconcile()

    assert bind_recorder.hosts.count(ABSENT_ADDRESS) >= 2, "the next pass retries"
    assert set(bind_recorder.hosts) == {ABSENT_ADDRESS}
    bind_recorder.assert_no_wildcard()


# The nine seams of SPEC 10.7, in the order conftest injects them. A `Deps` is
# built by constructor injection - SPEC 10.7 forbids monkeypatching module
# internals - so replacing one seam means naming the other eight.
DEPS_SEAMS = (
    "now",
    "restart_pwnagotchi",
    "reboot_device",
    "shutdown_device",
    "run_command",
    "read_gpsd",
    "read_pisugar_i2c",
    "list_local_ipv4",
    "spawn",
)


class FailingEnumeration:
    """`list_local_ipv4` that raises for its first `failures` calls, then works.

    The failure is not hypothetical: the enumeration shells out to `ip -4 addr
    show` when `netifaces` is absent (SPEC 2.3), and a subprocess can fail for
    reasons that have nothing to do with the tether.
    """

    def __init__(self, delegate, error: BaseException, failures: int = 1) -> None:
        self._delegate = delegate
        self._error = error
        self._failures = failures
        self.calls = 0
        self.raised = 0

    def fail_next(self, error: BaseException, failures: int = 1) -> None:
        """Arms the next `failures` calls to raise, after some have succeeded.

        This is how a raise is placed *after* listeners are bound, which is the
        only ordering that can distinguish "made no change to the bound set"
        from "treated the failure as an empty address list".
        """
        self._error = error
        self._failures = self.calls + failures

    def __call__(self) -> list[str]:
        self.calls += 1
        if self.calls <= self._failures:
            self.raised += 1
            raise self._error
        return self._delegate()


def deps_with_enumeration(base: Any, enumeration: Any) -> Any:
    """`base` with its address enumeration replaced and every other seam kept."""
    seams = {name: getattr(base, name) for name in DEPS_SEAMS}
    seams["list_local_ipv4"] = enumeration
    return companion.Deps(**seams)


@pytest.mark.parametrize(
    "error",
    [OSError("ip: command not found"), RuntimeError("enumeration backend exploded")],
    ids=["oserror", "unexpected"],
)
def test_an_enumeration_that_raises_leaves_the_loop_able_to_bind_later(
    make_listeners, deps, harness, ports, bind_recorder, error
):
    """SPEC 2.3.1: the rebinding loop never crashes, and retries the next pass.

    The table states that for a failed bind; the enumeration that feeds it is
    the step before, and a pass that dies there produces the same outcome the
    rule forbids - a unit whose timer stopped reconciling and whose tether
    therefore never comes up, with no symptom beyond silence.

    The second half is the half that matters. Swallowing the exception is easy;
    swallowing it and leaving the object wedged - a half-updated selection set,
    a "we already logged this" latch that now suppresses the real bind - looks
    identical until the enumeration recovers and nothing happens. So the pass
    after the failure must bind exactly as if the failure had never occurred.

    Both a plausible `OSError` and an unexpected `RuntimeError` are driven: a
    guard narrowed to `OSError` would let the second one out.
    """
    harness.local_ipv4 = [LOOPBACK]
    enumeration = FailingEnumeration(deps.list_local_ipv4, error)
    listeners = make_listeners([LOOPBACK], over_deps=deps_with_enumeration(deps, enumeration))

    listeners.reconcile()

    assert enumeration.calls == 1, "the pass must actually have asked for addresses"
    assert bind_recorder.addresses == [], "a pass that could not enumerate binds nothing"
    assert not is_listening(LOOPBACK, ports[1])

    listeners.reconcile()

    assert enumeration.calls == 2, "the loop must still be asking after a failure"
    assert is_listening(LOOPBACK, ports[1]), "the recovered pass must bind HTTPS"
    assert wait_until(lambda: is_listening(LOOPBACK, ports[0])), (
        "the recovered pass must bind WSS too"
    )
    assert set(bind_recorder.hosts) == {LOOPBACK}
    bind_recorder.assert_no_wildcard()


@pytest.mark.parametrize(
    "error",
    [OSError("ip: command not found"), RuntimeError("enumeration backend exploded")],
    ids=["oserror", "unexpected"],
)
def test_an_enumeration_that_raises_does_not_tear_down_bound_listeners(
    make_listeners, deps, harness, ports, bind_recorder, error
):
    """SPEC 2.3.1: a failed enumeration makes no change to the bound set.

    The rows above it in that table are about what the enumeration *observed*,
    and one that raised observed nothing. Folding the failure into an empty
    address list would reach the "bound address no longer present locally" row
    and close both listeners for every bound address, dropping their clients -
    so a transient hiccup in `ip -4 addr show` would drop the tether of a unit
    whose addresses never changed.

    Bound first, then raised: that ordering is the whole test. A pass that
    raises before anything is bound cannot tell the two readings apart.
    """
    harness.local_ipv4 = [LOOPBACK]
    enumeration = FailingEnumeration(deps.list_local_ipv4, error, failures=0)
    listeners = make_listeners([LOOPBACK], over_deps=deps_with_enumeration(deps, enumeration))
    listeners.reconcile()
    assert is_listening(LOOPBACK, ports[1]), "the fixture must start from a bound unit"
    assert wait_until(lambda: is_listening(LOOPBACK, ports[0]))
    bound = list(bind_recorder.addresses)
    assert len(bound) == 2, "one address means exactly a WSS and an HTTPS listener"

    enumeration.fail_next(error)
    listeners.reconcile()

    assert enumeration.raised == 1, "the pass must actually have hit the failure"
    assert is_listening(LOOPBACK, ports[1]), (
        "a failed enumeration observed nothing and must not close HTTPS"
    )
    assert is_listening(LOOPBACK, ports[0]), (
        "a failed enumeration observed nothing and must not close WSS"
    )
    assert bind_recorder.addresses == bound, "the failed pass must bind nothing new"

    # The enumeration recovers and reports what it always did. There is nothing
    # to rebind, because nothing was ever closed: a listener that reappears here
    # means the failed pass tore it down and this pass quietly put it back, with
    # every client of the old one dropped in between.
    listeners.reconcile()

    assert bind_recorder.addresses == bound, (
        "a recovered pass must not rebind an address that was never unbound"
    )
    assert is_listening(LOOPBACK, ports[1])
    assert is_listening(LOOPBACK, ports[0])
    bind_recorder.assert_no_wildcard()


# ---------------------------------------------------------------------------
# What a failed enumeration is logged at
# ---------------------------------------------------------------------------
#
# SPEC 2.3.1 gives the last row of the reconciliation table two levels: warning
# normally, and "at `error` rather than `warning` when nothing has ever been
# bound since load", because a unit with no `ip` binary and no `netifaces` fails
# every pass forever and at warning that reads exactly like a netlink hiccup on
# a working unit, so nobody looks.
#
# The two tests above execute both arcs of that branch without asserting on
# either level, which is the case SPEC 10.7 warns about: a branch that runs with
# nothing behind it reports as covered and pins nothing. These say which level.

#: One plausible backend failure. The *type* is unpinned by SPEC 2.3.1 and the
#: tests above already drive both an `OSError` and a `RuntimeError`; what is at
#: stake here is the level, which the type has nothing to do with.
ENUMERATION_ERROR = OSError("ip: command not found")


def levels_of(caplog) -> set[str]:
    return {record.levelname for record in caplog.records}


def test_a_failed_enumeration_is_an_error_while_nothing_has_ever_bound(
    make_listeners, deps, harness, caplog
):
    """SPEC 2.3.1: `error` when no enumeration has ever succeeded since load.

    Never *enumerated*, not never *bound*: the two differ on a unit whose
    enumeration works every pass and matches no configured entry, where a
    transient failure must not be announced as a permanent fault.

    This is the unreachable unit. Every pass fails, no listener has ever
    existed, and the only thing the owner can see is that the app does not
    connect. Logged at warning it is indistinguishable from the routine case
    below - which happens on a perfectly healthy unit whenever netlink stutters -
    and the message that says the companion is unreachable is filtered out along
    with it.
    """
    harness.local_ipv4 = [LOOPBACK]
    enumeration = FailingEnumeration(deps.list_local_ipv4, ENUMERATION_ERROR)
    listeners = make_listeners([LOOPBACK], over_deps=deps_with_enumeration(deps, enumeration))

    with caplog.at_level("DEBUG"):
        caplog.clear()
        listeners.reconcile()

    assert enumeration.raised == 1, "the pass must actually have hit the failure"
    assert "ERROR" in levels_of(caplog), (
        "a unit that has never bound anything and cannot enumerate is unreachable; "
        f"the levels logged were {sorted(levels_of(caplog))}"
    )
    assert "WARNING" not in levels_of(caplog), (
        "SPEC 2.3.1 says error *rather than* warning here, so a warning as well "
        "leaves the two cases as hard to tell apart as before"
    )


def test_a_failed_enumeration_is_a_warning_once_something_has_bound(
    make_listeners, deps, harness, ports, caplog
):
    """SPEC 2.3.1: warning is the normal level for a failed pass.

    A unit whose tether is up and whose enumeration hiccups once has lost one
    pass and nothing else - the bound set is untouched and the next pass
    reconciles. Reporting that at error trains the owner to ignore the level
    that the case above depends on being read.
    """
    harness.local_ipv4 = [LOOPBACK]
    enumeration = FailingEnumeration(deps.list_local_ipv4, ENUMERATION_ERROR, failures=0)
    listeners = make_listeners([LOOPBACK], over_deps=deps_with_enumeration(deps, enumeration))
    listeners.reconcile()
    assert is_listening(LOOPBACK, ports[1]), "the fixture must start from a bound unit"

    enumeration.fail_next(ENUMERATION_ERROR)
    with caplog.at_level("DEBUG"):
        caplog.clear()
        listeners.reconcile()

    assert enumeration.raised == 1, "the pass must actually have hit the failure"
    assert "WARNING" in levels_of(caplog), (
        f"a lost pass on a bound unit is a warning; the levels logged were "
        f"{sorted(levels_of(caplog))}"
    )
    assert "ERROR" not in levels_of(caplog), (
        "the unit is reachable: something is bound and nothing was torn down"
    )


def test_a_failed_enumeration_is_still_a_warning_after_the_address_went_away(
    make_listeners, deps, harness, ports, caplog
):
    """SPEC 2.3.1 measures it "since load", not "right now".

    A tether that drops closes its listeners, and the next enumeration failure
    then finds nothing bound. That unit is not the one the error level exists
    for: it bound once, so its configuration and its `ip` binary both work, and
    what it is doing is flapping. Gated on the current bound set instead of on
    the whole life of the plugin, every unit that loses its tether starts
    reporting the unreachable-unit error, which is how the level stops carrying
    the meaning the row gives it.
    """
    harness.local_ipv4 = [LOOPBACK]
    enumeration = FailingEnumeration(deps.list_local_ipv4, ENUMERATION_ERROR, failures=0)
    listeners = make_listeners([LOOPBACK], over_deps=deps_with_enumeration(deps, enumeration))
    listeners.reconcile()
    assert is_listening(LOOPBACK, ports[1]), "the fixture must start from a bound unit"

    harness.local_ipv4 = []
    listeners.reconcile()
    assert not is_listening(LOOPBACK, ports[1]), (
        "the address is gone, so its listeners must be closed before the failure"
    )

    enumeration.fail_next(ENUMERATION_ERROR)
    with caplog.at_level("DEBUG"):
        caplog.clear()
        listeners.reconcile()

    assert enumeration.raised == 1, "the pass must actually have hit the failure"
    assert "WARNING" in levels_of(caplog), (
        f"something has been bound since load, so this is the ordinary lost pass; "
        f"the levels logged were {sorted(levels_of(caplog))}"
    )
    assert "ERROR" not in levels_of(caplog), (
        "SPEC 2.3.1 gates the error on nothing having been bound *since load*; "
        "this unit bound a listener and then lost the address under it"
    )


def test_stop_closes_everything_and_is_idempotent(make_listeners, harness, ports):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])
    listeners.reconcile()
    assert wait_until(lambda: is_listening(LOOPBACK, ports[0]))

    listeners.stop()
    listeners.stop()

    assert not is_listening(LOOPBACK, ports[1])
    assert wait_until(lambda: not is_listening(LOOPBACK, ports[0]))


# ---------------------------------------------------------------------------
# The rule with no exceptions
# ---------------------------------------------------------------------------


def test_a_wildcard_entry_never_reaches_a_bind_call(
    make_listeners, harness, bind_recorder
):
    """`0.0.0.0` is the wildcard D5 exists to keep out, in any spelling.

    It parses as a valid IPv4 address, so an implementation that only checks
    parseability would accept it and bind every network the unit can see.
    """
    harness.local_ipv4 = ["0.0.0.0", LOOPBACK]
    listeners = make_listeners(["0.0.0.0"])

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()
    assert "0.0.0.0" not in bind_recorder.hosts
    assert LOOPBACK not in bind_recorder.hosts, "a non-matching address must not bind"


def test_a_wildcard_local_address_inside_an_accepted_block_is_not_bound(
    make_listeners, harness, bind_recorder
):
    harness.local_ipv4 = ["0.0.0.0"]
    listeners = make_listeners(["0.0.0.0/24"])

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()
    assert bind_recorder.addresses == []


def test_the_wildcard_guard_is_live(bind_recorder, free_ports):
    """The guard-of-the-guard.

    Every assertion above about `0.0.0.0` can only fail if a wildcard bind is
    actually noticed, and no test can make the plugin attempt one on purpose.
    This one binds the wildcard itself and requires the recorder to object, so
    the two tests above are known to be watching rather than merely silent.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as offender:
        offender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        offender.bind(("0.0.0.0", free_ports[0]))

        with pytest.raises(AssertionError):
            bind_recorder.assert_no_wildcard()

    assert ("0.0.0.0", free_ports[0]) in bind_recorder.addresses


@pytest.mark.parametrize("reported", ["0.0.0.0", "::", "::1", "localhost", ""])
def test_a_junk_local_address_is_never_bound(
    make_listeners, harness, bind_recorder, reported
):
    """The enumeration is not trusted to be well-formed either."""
    harness.local_ipv4 = [reported]
    listeners = make_listeners([LOOPBACK, "127.0.0.0/24"])

    listeners.reconcile()

    bind_recorder.assert_no_wildcard()
    assert bind_recorder.addresses == []


def test_an_ipv6_local_address_does_not_disturb_the_ipv4_selection(
    make_listeners, harness, ports
):
    harness.local_ipv4 = ["fe80::1", "::1", LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    listeners.reconcile()

    assert is_listening(LOOPBACK, ports[1])


# ---------------------------------------------------------------------------
# No bind decision may read an interface name
# ---------------------------------------------------------------------------


def test_the_interface_name_seam_no_longer_exists(deps):
    assert hasattr(deps, "list_local_ipv4"), "SPEC 10.7 pins this seam"
    assert not hasattr(deps, "resolve_interface_ip"), (
        "an interface-name seam is what D5.1 removed; its presence means a name "
        "can still reach a bind decision"
    )


def test_the_enumeration_is_asked_for_addresses_and_nothing_else(
    make_listeners, harness
):
    harness.local_ipv4 = [LOOPBACK]
    listeners = make_listeners([LOOPBACK])

    listeners.reconcile()
    listeners.reconcile()

    assert harness.args_for("list_local_ipv4") == [(), ()], (
        "the enumeration takes no argument, so no name can select an address"
    )
