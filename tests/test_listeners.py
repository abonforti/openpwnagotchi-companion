"""`Listeners`' teardown and error-guard paths (SPEC 2.3.1, issue #18).

`reconcile`, `_open` and the bind selection itself are exercised end to end in
`tests/test_binding.py` against real sockets. What is not exercised there is the
half of `Listeners` that only runs once something has already gone wrong or is
already closed: a crashing server thread, a shutdown call on a socket that was
never opened, a websockets server object without `wait_closed`, and the three
`except Exception` guards inside `stop()`. Each of those exists for one reason
stated in the class's own docstring - "A failure here is logged and retried,
never fatal" for the bind/teardown guards, and "Idempotent" for `stop()` - and
each test below drives the specific failure the guard is there to survive
rather than merely touching the line.

None of the doubles here are real sockets or a real websockets server: the
seam under test is entirely internal state (`_bound`, `_loop`, `_loop_thread`)
and the private methods that read it, so a hand-built double that behaves like
the one real object each guard expects is more direct than standing up a
second real listener next to the ones in test_binding.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import pytest

from plugin import companion


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def listeners_factory(deps):
    """Builds a bare `Listeners` with no bound address and no real TLS material.

    None of the tests here reach `_open`/`_serve_http`, which are the only
    places `self._ssl` and `self._options` matter beyond their defaults, so a
    minimal options mapping and a `None` ssl context are enough.
    """
    created: list[companion.Listeners] = []

    def _make(client_handler: Any = None) -> companion.Listeners:
        instance = companion.Listeners({}, deps, None, client_handler)
        created.append(instance)
        return instance

    yield _make

    for instance in created:
        instance.stop()


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class CrashingHTTPServer:
    """`serve_forever` raises immediately, as a bound socket rejecting a
    request malformed enough to reach into http.server's internals might."""

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        raise RuntimeError("serve_forever exploded")


class FailingShutdownHTTPServer:
    def shutdown(self) -> None:
        raise RuntimeError("shutdown exploded")

    def server_close(self) -> None:
        raise AssertionError("must not be reached once shutdown() has raised")


class ServerWithoutWaitClosed:
    """A websockets server object lacking `wait_closed`, which `_shutdown_ws`
    must tolerate via `getattr(server, "wait_closed", None)`."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FailingCloseWsServer:
    def close(self) -> None:
        raise RuntimeError("close exploded")


class FailingCallSoonLoop:
    def __init__(self) -> None:
        self.close_called = False

    def stop(self) -> None:
        pass

    def call_soon_threadsafe(self, callback: Callable[[], Any]) -> None:
        raise RuntimeError("call_soon_threadsafe exploded")

    def close(self) -> None:
        self.close_called = True


class FailingCloseLoop:
    def __init__(self) -> None:
        self.call_soon_threadsafe_called = False

    def stop(self) -> None:
        pass

    def call_soon_threadsafe(self, callback: Callable[[], Any]) -> None:
        self.call_soon_threadsafe_called = True
        callback()

    def close(self) -> None:
        raise RuntimeError("close exploded")


# ---------------------------------------------------------------------------
# _start_http: the HTTP server crash path
# ---------------------------------------------------------------------------


def test_start_http_survives_a_crashing_serve_forever(caplog):
    """The serving thread's own guard, not the caller, is what stands between a
    `serve_forever` failure and an unhandled exception on a daemon thread."""
    server = CrashingHTTPServer()

    def logged() -> bool:
        return any(
            r.name == "plugin.companion" and r.levelname == "DEBUG" for r in caplog.records
        )

    with caplog.at_level(logging.DEBUG, logger="plugin.companion"):
        companion.Listeners._start_http(server, "203.0.113.10")
        emitted = wait_until(logged)

    assert emitted, "a crashing serve_forever must leave a record that it failed"


# ---------------------------------------------------------------------------
# _serve_ws: no client handler configured
# ---------------------------------------------------------------------------


def test_serve_ws_without_a_handler_returns_none_and_starts_no_loop(listeners_factory):
    """A `Listeners` built with no `client_handler` is HTTPS-only by design
    (used by test_static_server.py); WSS must not be attempted at all."""
    listeners = listeners_factory(client_handler=None)

    result = listeners._serve_ws("203.0.113.10")

    assert result is None
    assert listeners._loop is None, "no client handler means no ws loop is ever started"


# ---------------------------------------------------------------------------
# _close: an address that was never bound
# ---------------------------------------------------------------------------


def test_close_of_a_never_bound_address_is_a_noop(listeners_factory):
    """Closing an address `_open` never touched must not raise or mutate state:
    the reconciliation loop calls `_close` for every address that drops out of
    `selected`, not only ones it remembers binding."""
    listeners = listeners_factory()

    listeners._close("203.0.113.10")

    assert listeners._bound == {}


# ---------------------------------------------------------------------------
# _shutdown_http: the exception guard
# ---------------------------------------------------------------------------


def test_shutdown_http_survives_a_failing_shutdown(caplog):
    with caplog.at_level(logging.DEBUG):
        companion.Listeners._shutdown_http(FailingShutdownHTTPServer())

    assert any(r.levelname == "DEBUG" for r in caplog.records), (
        "a failing http shutdown must leave a record that it failed"
    )


def test_shutdown_http_of_nothing_is_a_noop(caplog):
    """`_open` passes `None` here when `_serve_http` itself never ran (SPEC
    2.3.1's OSError row); that must be silent, not a failure the guard below
    happens to catch. `FailingShutdownHTTPServer.server_close` would raise
    `AssertionError` if the guard were removed and execution fell through to
    call `.shutdown()` on `None` and then reach past it - so, as with
    `_shutdown_ws`, the only thing that tells the early return apart from the
    guard swallowing the same `AttributeError` is silence."""
    with caplog.at_level(logging.DEBUG):
        companion.Listeners._shutdown_http(None)  # must not raise

    assert caplog.records == [], "closing nothing must not fall through to the guard"


# ---------------------------------------------------------------------------
# _shutdown_ws: early return when server or loop is None
# ---------------------------------------------------------------------------


def test_shutdown_ws_with_no_server_is_a_noop(listeners_factory, caplog):
    listeners = listeners_factory()
    listeners._loop = object()  # any non-None sentinel; must never be touched

    with caplog.at_level(logging.DEBUG):
        listeners._shutdown_ws(None)  # must not raise despite the bogus loop

    # The bogus loop has none of asyncio's API, so anything past the early
    # return trips the exception guard below it and leaves a DEBUG record.
    # Its absence is what shows the early return, not the guard, is what ran.
    assert caplog.records == [], "a shutdown of nothing must not fall through to the guard"


def test_shutdown_ws_with_no_loop_is_a_noop(listeners_factory, caplog):
    listeners = listeners_factory()
    server = ServerWithoutWaitClosed()

    with caplog.at_level(logging.DEBUG):
        listeners._shutdown_ws(server)  # listeners._loop is None by construction

    assert server.closed is False, "a server on a loop that was never started must be untouched"
    # Scheduling on a `None` loop raises inside `asyncio.run_coroutine_threadsafe`,
    # which the guard below would catch and log - so silence here shows the
    # early return fired before that call was ever attempted.
    assert caplog.records == [], "a shutdown with no loop must not fall through to the guard"


# ---------------------------------------------------------------------------
# _shutdown_ws: a server with no wait_closed
# ---------------------------------------------------------------------------


def test_shutdown_ws_without_wait_closed_still_closes(listeners_factory, caplog):
    listeners = listeners_factory()
    listeners._ensure_loop()
    server = ServerWithoutWaitClosed()

    with caplog.at_level(logging.DEBUG):
        listeners._shutdown_ws(server)

    assert server.closed is True
    # A server double with no `wait_closed` must be a fully successful close,
    # not one the guard below happens to paper over: calling `None()` for a
    # waiter that does not exist would raise and be logged there, so silence
    # here shows the `waiter is not None` check, not the guard, decided this.
    assert caplog.records == [], "closing a server with no wait_closed must not need the guard"


# ---------------------------------------------------------------------------
# _shutdown_ws: the exception guard
# ---------------------------------------------------------------------------


def test_shutdown_ws_survives_a_failing_close(listeners_factory, caplog):
    listeners = listeners_factory()
    listeners._ensure_loop()

    with caplog.at_level(logging.DEBUG):
        listeners._shutdown_ws(FailingCloseWsServer())

    assert any(r.levelname == "DEBUG" for r in caplog.records), (
        "a failing ws close must leave a record that it failed"
    )
    # Teardown (listeners_factory) closes the loop; nothing to do here even if
    # the assert above fails, which is the point of going through the factory.


# ---------------------------------------------------------------------------
# stop(): the exception guard around loop.call_soon_threadsafe
# ---------------------------------------------------------------------------


def test_stop_survives_a_failing_call_soon_threadsafe(listeners_factory, caplog):
    listeners = listeners_factory()
    loop = FailingCallSoonLoop()
    listeners._loop = loop
    listeners._loop_thread = None

    with caplog.at_level(logging.DEBUG):
        listeners.stop()  # must return normally despite the raise

    assert any(r.levelname == "DEBUG" for r in caplog.records), (
        "a failing call_soon_threadsafe must leave a record that it failed"
    )
    assert loop.close_called is True, "stop() must still attempt to close the loop afterwards"


# ---------------------------------------------------------------------------
# stop(): the "if thread is not None" false branch
# ---------------------------------------------------------------------------


def test_stop_with_no_loop_thread_does_not_join_anything(listeners_factory):
    """`_ensure_loop` was never called (or its thread reference was already
    cleared) - `stop()` must still tear the loop down without touching a join
    on something that does not exist."""
    listeners = listeners_factory()
    listeners._loop = FailingCallSoonLoop()  # any loop double works here
    listeners._loop_thread = None

    listeners.stop()  # no AttributeError from calling .join() on None

    assert listeners._loop is None
    assert listeners._loop_thread is None


# ---------------------------------------------------------------------------
# stop(): the exception guard around loop.close()
# ---------------------------------------------------------------------------


def test_stop_survives_a_failing_loop_close(listeners_factory, caplog):
    listeners = listeners_factory()
    loop = FailingCloseLoop()
    listeners._loop = loop
    listeners._loop_thread = None

    with caplog.at_level(logging.DEBUG):
        listeners.stop()

    assert loop.call_soon_threadsafe_called is True
    assert any(r.levelname == "DEBUG" for r in caplog.records), (
        "a failing loop.close() must leave a record that it failed"
    )


# ---------------------------------------------------------------------------
# stop(): idempotence, per its own docstring
# ---------------------------------------------------------------------------


def test_stop_is_idempotent(listeners_factory, caplog):
    listeners = listeners_factory()
    listeners._ensure_loop()
    listeners.stop()

    with caplog.at_level(logging.DEBUG):
        listeners.stop()  # a second call on an already-torn-down instance

    assert listeners._loop is None
    assert listeners._loop_thread is None
    # `_loop` is already `None` after the first `stop()`, so a second call
    # must take the early `if loop is None: return` and touch nothing else -
    # in particular it must not reach either exception guard further down,
    # which would swallow a `None.call_soon_threadsafe`/`None.close()` and
    # leave the asserts above satisfied for the wrong reason.
    assert caplog.records == [], (
        "a second stop() must be a true no-op, not a guard swallowing one"
    )
