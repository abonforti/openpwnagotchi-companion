"""The HTTPS accept loop must not run the TLS handshake itself (SPEC 2.15, issue #100).

The server used to wrap its listening socket, so `SSLSocket.accept()` negotiated TLS
on the one thread that accepts connections, with no timeout. A peer that completed
the TCP connect and then sent nothing parked that thread for good: every later
connection queued behind a handshake that would never finish, and `stop()` could not
return, because it waits on the same loop. No token gates any of this - the token is
checked on the WebSocket once a connection exists, and this parks the connection
before anything is read.

SPEC 2.3.1 used to carry that as the one wait `stop()` could not bound, naming this
issue. That paragraph is gone, because this is the fix it pointed at: the handshake
now happens on the per-connection handler thread instead, so a silent peer no longer
blocks the loop that serves everybody else. What a silent peer still costs after this
fix is a separate question this file makes no claim about; see issue #102.

Four rules, asserted through real sockets against a real `Listeners` instance,
never through its internals:

1. A peer that completes the TCP connect and then sends nothing does not stop a
   normal HTTPS request to the same server getting its response.
2. The same peer does not stop `Listeners.stop()` returning.
3. After `stop()`, nothing is left listening - closing tears the socket down even
   though the silent peer's own connection was never resolved one way or the other.
4. A failed handshake produces no raw traceback anywhere it can be observed - not
   in the log and not on stderr, since the stdlib default `handle_error` writes
   straight to stderr and a log-only check would go blind the moment SPEC 2.15's
   override is removed rather than merely weakened. A separate change from the
   accept-loop fix above, and mutation-checked against that change specifically,
   not against rules 1-3.

Every wait below carries an explicit timeout: this file exists because a wait had
none, and a test that can itself hang while proving that is worse than no test -
CI would report a dead job with no failing test name.
"""

from __future__ import annotations

import http.client
import logging
import socket
import ssl
import threading
import time
from typing import Any, Callable

import pytest

from plugin import companion

# Inside tls_material's SAN (IP:127.0.0.1, IP:127.0.0.2); loopback keeps the test
# independent of any tether interface actually being present on the build host.
ADDRESS = "127.0.0.1"


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
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
    """Holds the WSS connection open; see test_binding.py / test_listeners_stop_race.py
    for the same double. The WSS side is not under test here, but `Listeners` still
    needs a connection handler to construct."""
    await websocket.wait_closed()


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(str(tls_material["cert"]), str(tls_material["key"]))
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def make_listeners(tmp_path, tls_material, free_ports, deps, ssl_context):
    """Mirrors the fixture of the same name in test_listeners_stop_race.py and
    test_binding.py, function-scoped there exactly as here, so hoisting it to
    conftest.py would lose nothing about isolation - each test would still get
    its own `deps`, `ssl_context` and `Listeners`. Duplicated anyway, to keep
    this file free to change its own fixture without touching a file the #90
    race test also depends on; that is a choice about blast radius, not a
    requirement, and there are now three copies that can drift."""
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


@pytest.fixture
def running_listeners(make_listeners, deps, ports):
    """A `Listeners` bound on ADDRESS, confirmed listening on both ports."""
    deps.list_local_ipv4 = lambda: [ADDRESS]
    listeners = make_listeners([ADDRESS])
    listeners.reconcile()
    assert wait_until(lambda: is_listening(ADDRESS, ports[1])), (
        "the HTTPS listener never came up - this run proves nothing about issue #100"
    )
    return listeners


def _silent_peer(https_port: int) -> socket.socket:
    """A TCP connection that never speaks: no TLS, no ClientHello, nothing.

    This is the peer SPEC 2.3.1 describes as parking the accept loop's TLS
    handshake indefinitely.
    """
    peer = socket.create_connection((ADDRESS, https_port), timeout=5.0)
    return peer


def _get_index(https_port: int, tls_material, timeout: float = 5.0):
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    connection = http.client.HTTPSConnection(
        ADDRESS, https_port, timeout=timeout, context=client_context
    )
    try:
        connection.request("GET", "/index.html")
        response = connection.getresponse()
        body = response.read()
        return response.status, body
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Rule 1: a silent peer does not block a normal client
# ---------------------------------------------------------------------------


def test_a_silent_peer_does_not_block_a_normal_https_request(
    running_listeners, ports, tls_material
):
    """SPEC 2.3.1 / issue #100, rule 1.

    A peer connects and sends nothing. With the handshake still running inside
    the single accept loop, that peer's connection never resolves and the loop
    never calls `accept()` again, so a second, well-behaved client queues behind
    it forever. With the handshake moved to the per-connection thread, `accept()`
    returns immediately for both, and the second client is served on its own
    thread regardless of what the first one ever does.

    The short sleep between the two connections is a wall-clock wait this file
    would rather not have, but there is nothing observable from outside that
    marks "the accept loop has reached this connection" without reading the
    implementation, and program order plus the kernel's FIFO accept backlog
    turned out not to be enough on their own: run head-to-head against the
    unfixed tree with the sleep removed, this test still caught the regression
    every time, but the sibling tests below it (rule 2 and rule 3, which share
    the same "connect, then act" shape) missed it on most runs, catching it on
    roughly one run in five. That result is reported in full rather than
    smoothed over - see the report for the counts.

    The real request below carries its own socket timeout (`_get_index`'s
    default 5s), so a regression here fails with a clear timeout/connection
    error rather than hanging the test.
    """
    silent = _silent_peer(ports[1])
    try:
        # See the docstring above: a fixed, short sleep, kept after measuring
        # that removing it made the mutation run unreliable rather than
        # assuming it would be.
        time.sleep(0.2)

        try:
            status, body = _get_index(ports[1], tls_material)
        except OSError as exc:
            pytest.fail(
                "a normal HTTPS request never got a response while a silent peer "
                f"was connected - the accept loop is parked on its TLS handshake "
                f"(issue #100): {exc!r}"
            )

        assert status == 200
        assert b"<!doctype html>" in body
    finally:
        silent.close()


# ---------------------------------------------------------------------------
# Rule 2: a silent peer does not block stop()
# ---------------------------------------------------------------------------


def test_a_silent_peer_does_not_block_stop(running_listeners, ports, tls_material):
    """SPEC 2.3.1 / issue #100, rule 2.

    `stop()` closing an address whose HTTPS server is mid-`accept()` calls
    `BaseServer.shutdown()`, which waits with no timeout for `serve_forever`'s
    loop to notice - the exact wait SPEC 2.3.1 says is "not the plugin's to
    bound" while the handshake still runs there. Once the handshake is on the
    per-connection thread instead, the accept loop is never inside it and
    `shutdown()` has nothing to wait on.

    Ordered by rule 1's own outcome rather than a fixed sleep: a completed
    HTTPS GET after the silent peer connects is exactly what
    `test_a_silent_peer_does_not_block_a_normal_https_request` uses to prove
    itself, and it is already bounded at 5s and fails fast against the broken
    tree, so it doubles here as the barrier proving the accept loop got past
    the silent peer before `stop()` is asked to close it. That couples this
    test to rule 1's own behaviour - if rule 1 ever stops holding, this one's
    ordering guarantee goes with it - which measurement made worth it anyway:
    with a fixed 0.2s sleep in its place instead, this test missed the
    still-present bug on most runs rather than on an unlucky few.
    """
    silent = _silent_peer(ports[1])
    try:
        try:
            barrier_status, _ = _get_index(ports[1], tls_material)
        except OSError as exc:
            pytest.fail(
                "the ordering barrier itself failed - a normal HTTPS request never "
                f"got a response while a silent peer was connected: {exc!r}"
            )
        assert barrier_status == 200

        stop_result: dict[str, BaseException] = {}

        def _stop() -> None:
            try:
                running_listeners.stop()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread below
                stop_result["error"] = exc

        stop_thread = threading.Thread(target=_stop, daemon=True)
        stop_thread.start()
        stop_thread.join(timeout=10.0)

        assert not stop_thread.is_alive(), (
            "stop() never returned while a silent peer's TLS handshake was still "
            "outstanding - the accept-loop hang issue #100 exists to fix"
        )
        if "error" in stop_result:
            raise stop_result["error"]
    finally:
        silent.close()


# ---------------------------------------------------------------------------
# Rule 3: nothing is left listening afterwards
# ---------------------------------------------------------------------------


def test_stop_with_a_silent_peer_still_present_leaves_nothing_listening(
    running_listeners, ports, tls_material
):
    """SPEC 2.3.1 / issue #100, rule 3.

    Closing must tear the listening socket down even though the silent peer's
    own connection was never resolved one way or the other - the peer's
    unfinished handshake must not leave the port answering new connections.

    Same barrier as `test_a_silent_peer_does_not_block_stop`, and the same
    coupling to rule 1's own outcome instead of a fixed sleep - see that
    test's docstring for why.
    """
    silent = _silent_peer(ports[1])
    try:
        try:
            barrier_status, _ = _get_index(ports[1], tls_material)
        except OSError as exc:
            pytest.fail(
                "the ordering barrier itself failed - a normal HTTPS request never "
                f"got a response while a silent peer was connected: {exc!r}"
            )
        assert barrier_status == 200

        stop_thread = threading.Thread(target=running_listeners.stop, daemon=True)
        stop_thread.start()
        stop_thread.join(timeout=10.0)
        assert not stop_thread.is_alive(), "stop() never returned; see the sibling test"

        assert not is_listening(ADDRESS, ports[1]), (
            "the HTTPS port was still accepting connections after stop() returned, "
            "with a silent peer's handshake still outstanding"
        )
        assert not is_listening(ADDRESS, ports[0]), (
            "the WSS port was still accepting connections after stop() returned"
        )
    finally:
        silent.close()


# ---------------------------------------------------------------------------
# A failed handshake must not block the next client either
# ---------------------------------------------------------------------------


def test_a_garbage_handshake_does_not_block_other_clients(running_listeners, ports, tls_material):
    """A neighbouring property, not one of the four rules issue #100 is about,
    and not evidence for the accept-loop fix specifically: it passes against
    both mutants tried for this file (the full revert and the `handle_error`-only
    removal), so nothing here discriminates that fix. It earns its place anyway,
    because it pins a related but distinct guarantee: a peer that sends garbage
    instead of a ClientHello meets `ssl.SSLError` on the per-connection handler
    thread rather than on the accept loop, and that failure - wherever and however
    it is handled - must not stop the next, well-behaved client from being served.
    What that failure does to the log and to stderr is the separate, actually
    #100-specific rule pinned in `test_a_failed_handshake_produces_no_traceback_anywhere`
    below.
    """
    garbage_peer = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
    try:
        garbage_peer.sendall(b"not a tls client hello, just garbage bytes\r\n\r\n")
    finally:
        garbage_peer.close()

    # The failed handshake happens on its own background thread; give it a
    # bounded moment to run before the next request, so a regression that
    # only shows up under contention is not masked by favourable timing.
    # Unlike rules 1-3, there is no ordering guarantee available here to lean
    # on instead: nothing about this rule depends on which of two connections
    # the server reaches first, so there is no kernel-level order to invoke,
    # and no outcome to poll for from outside short of the request below
    # itself timing out.
    time.sleep(0.5)

    try:
        status, body = _get_index(ports[1], tls_material)
    except OSError as exc:
        pytest.fail(
            f"a normal HTTPS request failed after a peer sent a garbage "
            f"handshake to another connection: {exc!r}"
        )
    assert status == 200
    assert b"<!doctype html>" in body


# ---------------------------------------------------------------------------
# Rule 4: a failed handshake is logged as one line, never as a raw traceback
# ---------------------------------------------------------------------------


def test_a_failed_handshake_produces_no_traceback_anywhere(
    running_listeners, ports, caplog, capsys
):
    """SPEC 2.15: the HTTPS server overrides `handle_error` so a failed
    handshake is logged as one line instead of the `socketserver` default,
    which prints a full stack trace straight to stderr. This is a separate
    change from the accept-loop fix rules 1-3 pin, and is checked against its
    own mutant: the override removed while the accept-loop fix stays in
    place. Not this file's business to pin the text of that line - only the
    absence of frames, the one part of "a raw traceback reached the output"
    that survives a rephrasing of the message itself.

    Two assertions, on two different outputs, because they catch two
    different mutants and neither stands in for the other: the log-content
    check below catches an override that logs too much (frames attached to
    the record), and the stderr check catches the override being removed
    outright, since the stdlib default writes straight to stderr rather than
    through `logging` and a caplog-only check would then see no record at
    all and never reach the content it means to test. Deleting either
    half leaves the other blind to its own mutant.

    Waits on either `caplog.records` or captured stderr growing past empty,
    polled together, rather than a fixed sleep: the failed handshake runs on
    its own background thread, and output is the one thing about its outcome
    this test can observe from outside without reading `handle_error`'s own
    body - so it is polled for, the same way `is_listening` is polled for
    elsewhere in this file, instead of assumed to have happened after some
    fixed wall-clock delay. `capsys.readouterr()` drains what it returns, so
    it is called from inside the predicate itself and every fragment is kept
    rather than read once and discarded.
    """
    captured_stderr: list[str] = []

    def _drain_and_check() -> bool:
        captured = capsys.readouterr()
        if captured.err:
            captured_stderr.append(captured.err)
        return len(caplog.records) >= 1 or bool(captured_stderr)

    with caplog.at_level(logging.DEBUG):
        peer = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
        try:
            peer.sendall(b"GET / HTTP/1.1\r\nHost: example.invalid\r\n\r\n")
        finally:
            peer.close()

        assert wait_until(_drain_and_check, timeout=5.0), (
            "a peer sending plaintext HTTP to the HTTPS port produced neither a log "
            "record nor anything on stderr within 5s - this run proves nothing about "
            "the handle_error rule"
        )
        # A single handle_error call's print()s or log call land in one tight
        # burst; this catches whatever trailed the first fragment observed above.
        time.sleep(0.05)
        _drain_and_check()

    stderr_text = "".join(captured_stderr)
    assert "Traceback (most recent call last)" not in stderr_text, (
        "a raw Python traceback from a failed TLS handshake reached stderr - "
        "an unauthenticated peer controls this input"
    )

    for record in caplog.records:
        assert record.exc_info is None, (
            "a failed TLS handshake was logged with exc_info attached, which formats "
            "as a full traceback - an unauthenticated peer controls this input"
        )
        text = record.getMessage()
        if record.exc_text:
            text = f"{text}\n{record.exc_text}"
        assert "Traceback (most recent call last)" not in text, (
            "a raw Python traceback from a failed TLS handshake reached the log - "
            "an unauthenticated peer controls this input"
        )
