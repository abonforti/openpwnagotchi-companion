"""`Listeners.stop()` racing `Listeners.reconcile()` (SPEC 2.3.1, issue #90).

`Companion.on_unload` calls `stop()` on whatever thread pwnagotchi calls it on,
while the background thread keeps calling `reconcile()` in a loop. Nothing in
either method's own docstring serialises them, and `_bound` is mutated by both.
The three failure modes the ticket names - a double close, a listener opened a
moment after teardown began and then leaked, and a `KeyError` on an entry that
vanished between a check and a use - all need the two calls actually driven
into an overlap to show up; calling them from two threads and hoping is not
enough; see below for how the overlap is made deterministic.

Four rules, asserted through the public surface only (real sockets and the
seam `Deps.list_local_ipv4`), never through `_bound`, `_lock` or `_stopped`:

1. A pass and a teardown cannot interleave.
2. The teardown wins: nothing survives `stop()`, whatever `reconcile()` was
   doing when it was called.
3. A pass that arrives after a teardown does nothing - no re-opening what
   teardown just closed.
4. Neither call raises, on any ordering.

Rule 1 has no assertion of its own below: nothing on the public surface
distinguishes "the two calls never interleaved" from "they interleaved and it
happened not to matter this run". What *is* observable is its consequence -
a half-updated pass is exactly what would leave a listener bound that `stop()`
never saw, or raise from a `_bound` mutated out from under one of the two
calls - so the assertions for rule 2 and rule 4 are what would catch rule 1
breaking, and are the only place it is described here.
"""

from __future__ import annotations

import concurrent.futures
import http.client
import socket
import ssl
import threading
import time
from typing import Any, Callable

import pytest

from plugin import companion

# Distinct from the loopback literals used elsewhere in the suite so a failure
# here is never confused with one in test_binding.py.
RACE_ADDRESS_A = "127.0.0.41"
RACE_ADDRESS_B = "127.0.0.42"

# Inside tls_material's own SAN (IP:127.0.0.1, IP:127.0.0.2), unlike the two
# addresses above - needed only where a test verifies a real TLS handshake.
TLS_COVERED_ADDRESS = "127.0.0.2"


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
    """Holds the WSS connection open; see test_binding.py for the same double."""
    await websocket.wait_closed()


class BlockingListLocalIPv4:
    """A `list_local_ipv4` double that pauses a `reconcile()` pass mid-flight.

    `reconcile()` calls this first (per the `Listeners` class docstring), so
    blocking here reliably puts one pass "in flight" at a controllable point,
    without this file reasoning about, or reading, where inside `reconcile()`
    any lock is actually taken. `entered` tells the test the pass has reached
    this point; `release` is what lets it continue.
    """

    def __init__(self, addresses: list[str]) -> None:
        self._addresses = addresses
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self) -> list[str]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5.0):
            # A regression that deadlocks here must fail the test, not hang
            # the suite: the double itself times out rather than blocking
            # forever if the test never gets to release it.
            raise AssertionError("test never released the blocked list_local_ipv4 call")
        return list(self._addresses)


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(str(tls_material["cert"]), str(tls_material["key"]))
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def make_listeners(tmp_path, tls_material, free_ports, deps, ssl_context):
    """Builds a `Listeners` over a configuration whose only variable is binding.

    Mirrors `tests/test_binding.py`'s fixture of the same name; duplicated
    rather than imported, since the two files' `deps`/`ssl_context` instances
    must not be shared across a threaded test and a socket-heavy one.
    """
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    started: list[Any] = []

    def _make(bind_addresses: list[str]) -> companion.Listeners:
        options: dict[str, Any] = {
            "bind_addresses": bind_addresses,
            "ws_port": free_ports[0],
            "http_port": free_ports[1],
            "tls_cert": str(tls_material["cert"]),
            "tls_key": str(tls_material["key"]),
            "web_root": str(web_root),
            "token": "",
            "rebind_interval": 30,
        }
        instance = companion.Listeners(options, deps, ssl_context, idle_handler)
        started.append(instance)
        return instance

    yield _make

    # Every instance is stopped before any failure is reported: a hung or
    # raising stop() on one must not leave a later instance's listeners open
    # because teardown gave up early.
    stuck: list[int] = []
    errors: list[BaseException] = []
    for index, instance in enumerate(started):
        result: dict[str, BaseException] = {}

        def _stop(instance: companion.Listeners = instance, result: dict = result) -> None:
            try:
                instance.stop()
            except BaseException as exc:  # noqa: BLE001 - reported below, not swallowed
                result["error"] = exc

        thread = threading.Thread(target=_stop, daemon=True)
        thread.start()
        thread.join(timeout=10.0)
        if thread.is_alive():
            stuck.append(index)
        elif "error" in result:
            errors.append(result["error"])

    if stuck:
        pytest.fail(
            f"Listeners.stop() never returned during fixture teardown for {len(stuck)} "
            "instance(s)"
        )
    if errors:
        raise errors[0]


@pytest.fixture
def ports(free_ports) -> tuple[int, int]:
    return free_ports


# ---------------------------------------------------------------------------
# Rules 1, 2 and 4: a teardown that starts mid-pass
# ---------------------------------------------------------------------------


def test_a_teardown_started_mid_pass_wins_and_leaves_nothing_bound(
    make_listeners, deps, ports, bind_recorder
):
    """Drives `stop()` into a `reconcile()` pass that is already in flight.

    `reconcile()` starts on its own thread and is parked inside
    `list_local_ipv4` by `BlockingListLocalIPv4`, which is confirmed via
    `entered` before anything else happens - that is what makes this an
    actual overlap rather than two calls that merely happened not to collide.
    `stop()` then starts on a second thread while the pass is still parked;
    only once both calls are in motion does the test release the pass, so
    from that point on there is a real `reconcile()` and a real `stop()`
    running concurrently and racing for `_bound`, for as long as either
    implementation lets them.

    `future.result(timeout=...)` both re-raises anything either thread threw
    (rule 4) and converts a deadlock into a test failure instead of a hang
    (the ticket's own failure mode on the device).

    `bind_recorder` guards against passing vacuously: `double.entered` only
    proves the pass reached enumeration, not that it went on to open a socket
    before `stop()` caught up with it.
    """
    double = BlockingListLocalIPv4([RACE_ADDRESS_A, RACE_ADDRESS_B])
    deps.list_local_ipv4 = double
    listeners = make_listeners([RACE_ADDRESS_A, RACE_ADDRESS_B])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        reconcile_future = executor.submit(listeners.reconcile)
        assert wait_until(double.entered.is_set), "reconcile() never reached list_local_ipv4"

        stop_future = executor.submit(listeners.stop)
        double.release.set()

        reconcile_future.result(timeout=10.0)
        stop_future.result(timeout=10.0)

    assert bind_recorder.addresses, (
        "the parked pass never bound anything, so stop() had nothing to race"
    )
    for address in (RACE_ADDRESS_A, RACE_ADDRESS_B):
        assert not is_listening(address, ports[0]), (
            f"a WSS listener on {address} survived a teardown that started mid-pass"
        )
        assert not is_listening(address, ports[1]), (
            f"an HTTPS listener on {address} survived a teardown that started mid-pass"
        )


# ---------------------------------------------------------------------------
# Rule 3: a pass that arrives after teardown does nothing
# ---------------------------------------------------------------------------


def test_reconcile_after_stop_binds_nothing(make_listeners, deps, ports, bind_recorder):
    """The leak the ticket describes, and the case a lock alone does not fix.

    `stop()` here runs to completion, uncontested, before `reconcile()` is
    called at all - "a listener opened a moment after teardown began" reduced
    to its simplest form: the moment is zero and the pass still must not bind.
    A lock alone recovers exclusivity once `stop()` releases it; only a
    teardown that is remembered past that point, not just a mutex that is
    free again, stops this second, later call from opening a socket.

    `bind_recorder` proves the negative directly, against the syscall rather
    than only against the two ports staying silent.
    """
    deps.list_local_ipv4 = lambda: [RACE_ADDRESS_A]
    listeners = make_listeners([RACE_ADDRESS_A])

    listeners.stop()
    listeners.reconcile()

    assert bind_recorder.addresses == [], "reconcile() after stop() must bind nothing"
    assert not wait_until(lambda: is_listening(RACE_ADDRESS_A, ports[0]), timeout=1.0), (
        "a WSS listener was opened by a reconcile() that ran after stop()"
    )
    assert not is_listening(RACE_ADDRESS_A, ports[1]), (
        "an HTTPS listener was opened by a reconcile() that ran after stop()"
    )


# ---------------------------------------------------------------------------
# The OSError cleanup hang: a bind failure on one listener must not strand
# stop() behind a server whose serve_forever never started
# ---------------------------------------------------------------------------


def test_a_bind_failure_on_one_listener_still_lets_stop_return(
    make_listeners, deps, ports, bind_recorder
):
    """SPEC 2.3.1's OSError row, from the one ordering test_binding.py's own
    EADDRINUSE case never reaches. `test_an_address_already_in_use_is_survivable`
    there occupies the *HTTP* port, so the static server's own constructor
    raises immediately and there is nothing yet to clean up. Occupying the
    *WSS* port instead lets the HTTP server bind and get built first, so the
    side that fails is WS, and `_open`'s `except OSError` branch is left to
    tear down an HTTP server whose `serve_forever` was never started -
    `BaseServer.shutdown()` then waits forever on an event only
    `serve_forever` sets.

    Before the lock this hung only the background thread that calls
    `reconcile()`; under it, `stop()` - which is `on_unload` - waits on the
    same lock that thread never releases, so a unit that hits this once at
    shutdown never comes back. `reconcile()` therefore runs on its own daemon
    thread here and is never joined: whether or when it ever returns is not
    this test's rule and this test makes no claim about it either way. The
    rule under test is that `stop()`, called afterwards, still does - so
    `stop()` runs on a second thread with a timeout that turns a regression
    into a failure instead of a hang, which is what CI needs from it.

    `bind_recorder` is what proves the run was not vacuous, by showing the
    HTTP side actually reached a real `socket.bind`, and it is also what
    tells the test when it is safe to stop waiting: rather than a fixed
    sleep, `wait_until` polls for that same HTTP bind to appear before
    starting `stop()`, so the test does not depend on wall-clock time to
    reach the point it is actually trying to test.
    """
    occupied_ws_port = ports[0]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupier:
        occupier.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupier.bind((RACE_ADDRESS_A, occupied_ws_port))
        occupier.listen(1)

        deps.list_local_ipv4 = lambda: [RACE_ADDRESS_A]
        listeners = make_listeners([RACE_ADDRESS_A])

        reconcile_thread = threading.Thread(target=listeners.reconcile, daemon=True)
        reconcile_thread.start()
        assert wait_until(lambda: (RACE_ADDRESS_A, ports[1]) in bind_recorder.addresses), (
            "the HTTP side never reached a real bind, so this run proves nothing"
        )

        stop_result: dict[str, BaseException] = {}

        def _stop() -> None:
            try:
                listeners.stop()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread below
                stop_result["error"] = exc

        stop_thread = threading.Thread(target=_stop, daemon=True)
        stop_thread.start()
        stop_thread.join(timeout=10.0)

    assert not stop_thread.is_alive(), (
        "stop() never returned after a bind failure on one listener - the OSError-cleanup hang"
    )
    if "error" in stop_result:
        raise stop_result["error"]

    assert not is_listening(RACE_ADDRESS_A, ports[1]), (
        "an HTTP server created before the WS bind failed was left listening after stop()"
    )


# ---------------------------------------------------------------------------
# The serving-thread unwind: self._bound[ip] must not survive a thread that
# never started
# ---------------------------------------------------------------------------


def _targets_start_http(target) -> bool:
    """True for anything that would run `Listeners._start_http` or the
    thread it launches internally to call `serve_forever` - discovered by
    recording every `threading.Thread` construction one `reconcile()` pass
    makes and reading back what each target's `__qualname__` actually is,
    not by reading `companion.py`. The direct-reference and `functools.partial`
    cases are kept as well, in case a future version calls it more plainly.
    """
    if target is None:
        return False
    if target is companion.Listeners._start_http:
        return True
    inner = getattr(target, "func", None)
    if inner is not None and _targets_start_http(inner):
        return True
    qualname = getattr(target, "__qualname__", "") or ""
    return qualname == "Listeners._start_http" or qualname.startswith("Listeners._start_http.")


def test_a_serving_thread_that_cannot_start_leaves_nothing_bound(
    make_listeners, deps, ports, monkeypatch
):
    """SPEC 2.3.1: `_open` records `self._bound[ip]` before the HTTPS serving
    thread is started. If starting that thread fails - `RuntimeError: can't
    start new thread`, the realistic failure on a Pi Zero 2 W under memory
    pressure - the entry must not survive it, or the next `stop()` calls
    `shutdown()` on a server whose `serve_forever` never ran and hangs
    forever, exactly as in `test_a_bind_failure_on_one_listener_still_lets_stop_return`
    above but reached from the other side: nothing else failed to bind here,
    the HTTPS listener bound cleanly and then could not be served.

    The failure is injected by target, not by address or call count:
    `threading.Thread` is replaced with a factory that only fails the thread
    whose target is `_start_http`, so the websockets loop thread - started
    the same way, in the same pass, for the same address - is left alone.
    Failing that one too would prove nothing about this rule specifically.
    """
    real_thread = threading.Thread

    def selective_factory(*args, target=None, **kwargs):
        thread = real_thread(*args, target=target, **kwargs)
        if _targets_start_http(target):

            def _cannot_start() -> None:
                raise RuntimeError("can't start new thread")

            thread.start = _cannot_start
        return thread

    deps.list_local_ipv4 = lambda: [RACE_ADDRESS_A]
    listeners = make_listeners([RACE_ADDRESS_A])

    with monkeypatch.context() as ctx:
        ctx.setattr(threading, "Thread", selective_factory)
        bound = listeners.reconcile()

    assert bound == [], "a pass whose HTTPS thread could not start must bind nothing"
    assert not is_listening(RACE_ADDRESS_A, ports[0]), (
        "a WSS listener survived its pair's HTTPS thread-start failure"
    )
    assert not is_listening(RACE_ADDRESS_A, ports[1]), (
        "an HTTPS listener survived its own thread-start failure"
    )

    # Real threading.Thread is back once the `with` block above exited, so a
    # later pass - the OS able to create threads again - must bind cleanly:
    # the address must not have been left poisoned by the failed attempt.
    #
    # This runs before stop() below, not after: stop() is a one-way teardown
    # (test_reconcile_after_stop_binds_nothing, above), so calling it first
    # would make every later reconcile() a no-op for a reason that has
    # nothing to do with this rule and this test would not be checking what
    # it says it checks.
    retried = listeners.reconcile()
    assert retried == [RACE_ADDRESS_A], "a later pass must recover the address, not stay poisoned"
    assert is_listening(RACE_ADDRESS_A, ports[1])
    assert wait_until(lambda: is_listening(RACE_ADDRESS_A, ports[0]))

    # stop() is checked last and against a real, successfully bound pair: if
    # the rollback above left any stale reference to the failed attempt's
    # unstarted server behind its bookkeeping, a real listener now sharing
    # that address is exactly what would surface it as a hang here.
    stop_result: dict[str, BaseException] = {}

    def _stop() -> None:
        try:
            listeners.stop()
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread below
            stop_result["error"] = exc

    stop_thread = threading.Thread(target=_stop, daemon=True)
    stop_thread.start()
    stop_thread.join(timeout=10.0)

    assert not stop_thread.is_alive(), (
        "stop() never returned after an HTTPS serving thread had earlier failed to start"
    )
    if "error" in stop_result:
        raise stop_result["error"]


# ---------------------------------------------------------------------------
# The server_bind override: no reverse DNS lookup, and nothing a client can
# see changes because of it
# ---------------------------------------------------------------------------


def test_server_bind_avoids_a_reverse_dns_lookup_and_still_serves(
    make_listeners, deps, ports, tls_material, monkeypatch
):
    """SPEC 2.3.1: `_serve_http`'s server class overrides `server_bind`
    rather than falling through to `HTTPServer.server_bind`, whose
    `socket.getfqdn` on the bind address is a reverse lookup that would
    otherwise run against whatever resolver the phone's Personal Hotspot
    happens to offer over BT PAN - synchronously, inside the reconciliation
    lock (issue #90).

    Both halves are pinned, not either alone: that the lookup never happens,
    and that what the override does instead - set `server_name`/`server_port`
    from the literal bind address, not a resolved one - is right. A
    `getfqdn` spy that raises the moment it is called proves the first half
    loudly; `server_name`/`server_port` are read directly off the object
    `_serve_http` returns, the same pattern the earlier tests in this file
    use for `_start_http`/`_shutdown_http`.

    That object is closed with `_close_unstarted_http`, not `_shutdown_http`:
    its `serve_forever` was never started by this direct call, and
    `_shutdown_http` calling `.shutdown()` on it is exactly the hang the two
    tests above this one exist to rule out.

    The "still works" half is checked separately, through a full
    `reconcile()` pass on the same address: the CSP header's `connect-src`
    is built from `self._bound`, which the direct `_serve_http` call above
    never populates. The point of the override is that a client sees nothing
    different, so the response is read over a real wrapped socket and the
    `getfqdn` guard stays armed for it too.
    """
    getfqdn_calls: list[Any] = []

    def _forbidden_getfqdn(*args, **kwargs):
        getfqdn_calls.append((args, kwargs))
        raise AssertionError(
            "socket.getfqdn must never run during a bind - a reverse lookup over "
            "BT PAN hits whatever resolver the phone's hotspot happens to run, "
            "synchronously, inside the reconciliation lock"
        )

    monkeypatch.setattr(socket, "getfqdn", _forbidden_getfqdn)

    listeners = make_listeners([TLS_COVERED_ADDRESS])

    server = listeners._serve_http(TLS_COVERED_ADDRESS)
    try:
        assert getfqdn_calls == [], "server_bind must not resolve the bind address"
        assert server.server_name == TLS_COVERED_ADDRESS, (
            "the override must record the literal bind address, not a DNS name"
        )
        assert server.server_port == ports[1]
    finally:
        companion.Listeners._close_unstarted_http(server)

    deps.list_local_ipv4 = lambda: [TLS_COVERED_ADDRESS]
    bound = listeners.reconcile()
    assert bound == [TLS_COVERED_ADDRESS]
    assert wait_until(lambda: is_listening(TLS_COVERED_ADDRESS, ports[1]))

    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    connection = http.client.HTTPSConnection(
        TLS_COVERED_ADDRESS, ports[1], timeout=5, context=client_context
    )
    try:
        connection.request("GET", "/index.html")
        response = connection.getresponse()
        headers = {name.lower(): value for name, value in response.getheaders()}
        response.read()
    finally:
        connection.close()

    assert response.status == 200
    assert getfqdn_calls == [], "serving the request must not trigger a reverse lookup either"
    csp = headers["content-security-policy"]
    assert f"wss://{TLS_COVERED_ADDRESS}:{ports[0]}" in csp, (
        "the override must change nothing a client can see: the CSP still names "
        "the address the client actually connected to"
    )
