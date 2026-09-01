"""A bound on unfinished HTTPS handshakes (SPEC 2.15.2, issue #102).

SPEC 2.15 (issue #100) moved the TLS handshake off the accept loop and onto a
per-connection handler thread, so a silent peer no longer blocks anyone else's
request or `stop()`. What that fix does not bound is the aggregate: one thread
and one file descriptor per silent peer, held for the life of the process,
since `daemon_threads = True` is what makes the thread unjoinable rather than
immortal - it never bounds how many can pile up.

SPEC 2.15.2 has now been through two designs for what closes a slot at the
cap. The first was plain oldest-first with no deadline, where sixteen silent
sockets lock the owner out permanently. The second tried to be fair by source
address; an audit found that a fairness rule keyed on source address cannot
work on this port, because addresses are free here (a host on the USB gadget
interface can add IPv4 aliases at will, and the BT PAN /28 holds fourteen) -
any metric an attacker can minimise by spreading out eventually names the
legitimate client instead, whose honest behaviour is concentrated. Both
designs, and every test this file once had for the second one, are gone.
What replaces them is smaller and asserted below through real sockets against
a real `Listeners` instance, never through its internals:

1. At most `MAX_PENDING_HANDSHAKES = 16` connections may be in the handshake
   state - accepted but not yet TLS-handshake-complete or closed - at once,
   per bound address.
2. An unfinished handshake also expires, after `HANDSHAKE_DEADLINE = 30`
   seconds, releasing its slot with nobody having to reconnect. This is the
   property everything else rests on: a slot in the registry is never held
   forever, which is what turns "sixteen sockets opened once and abandoned"
   into "a continuous stream of connections" as the cost of denying the
   owner access - and what lets the registry drain back to empty on its own.
3. At the cap, the connection closed is simply the oldest unfinished
   handshake. No source accounting, no tie-break.
4. A completed handshake frees its slot immediately and counts against
   neither the aggregate cap nor `HANDSHAKE_DEADLINE`, so an ordinary client
   is never refused because other peers hold every pending slot - not once,
   but repeatedly, for as long as the registry stays full.
5. `stop()` closes every socket still in the handshake state and returns
   promptly, without joining the handler threads.
6. `stop()`'s drain runs after its call to `shutdown()` returns, not before -
   a connection accepted in the window between the two is still closed, not
   leaked past `stop()` returning.
7. An eviction at the aggregate cap produces no log record naming the
   connection, at any level - the plugin's own doing, same as an eviction
   always was. A handshake that fails on its own (not TLS, a rejected
   certificate) still logs at debug, because that one is news. A request
   deadline (`HTTP_REQUEST_DEADLINE = 30`, after a completed handshake) that
   expires is silent for the same reason, even though
   `BaseHTTPRequestHandler`'s own default would log a timed-out read.
8. A blanket silence on `HANDSHAKE_DEADLINE` expiries would hide a real
   symptom - a tether so degraded that no handshake ever completes would
   produce nothing at all in the log - so expiries are *counted* instead,
   and a report line is written summarising how many there have been, but
   at most once every `EXPIRY_REPORT_INTERVAL = 60` seconds, however many
   connections a peer abandons. A live-connection eviction (rule 9, below)
   shares the same counter and the same once-a-minute gate, for the same
   reason: the connection being closed may be the owner's, and silence
   about it is the worst diagnostic outcome this section can produce.
9. The number of live HTTP connections - pending or completed - is capped
   too, at `MAX_HTTP_CONNECTIONS = 48` per bound address, independent of the
   handshake-state registry above (a pending connection holds one of these
   forty-eight slots too). Past it, the *least recently active* live
   connection is closed and the newcomer is served. Deliberately not the
   oldest: the owner's own connection, serving happily for a while, is the
   oldest by construction, while a flooder's connection that arrived a
   second ago and went quiet is the youngest - an oldest-first rule would
   therefore evict the owner first. Every read and every write marks a
   connection as used; eviction targets whichever has gone longest without
   one. This replaced an earlier version of the rule that refused the
   newcomer instead: an audit priced the two lockouts against each other and
   found refusing the newcomer fifty to a hundred times cheaper to trigger
   than beating the eviction rule (one to two connections a second,
   sustained, from a single address, deterministically - against eighty to a
   hundred and sixty a second, and only a probabilistic win). Unlike every
   other bound in this section, nothing expires this counter on its own - a
   slot is only ever released by a matching decrement when a connection is
   torn down - so it is the one place a single unbalanced path could leave
   the listener refusing every connection forever rather than for thirty
   seconds.

Not tested here, and named rather than guessed at:

- "A slot is released on every path out, not only the normal one - including
  when the handler thread fails to start" has no black-box trigger this file
  can reach without either guessing which stdlib hook of `ThreadingHTTPServer`
  the fix attaches to, or patching `threading.Thread.start` broadly enough
  that its blast radius covers threads this file does not intend to touch.
- **The release of a `MAX_HTTP_CONNECTIONS` slot is not observable from
  outside the process, under this eviction policy.** A registry left full of
  connections that were closed but never released their slot evicts one of
  those zombies on every new arrival - the same as a correctly releasing one
  would - so an ordinary request succeeds either way, and no black-box test,
  however many requests it makes or in whatever order, tells the two apart.
  `test_listener_recovers_after_the_live_connection_cap_empties` proves the
  listener stays *available*, which is real and worth having; it does not
  and cannot prove the counter is exact. What actually guards against the
  leak is that there is exactly one release site in the implementation for
  mutation to target, not a test that could catch a second one appearing.
  SPEC 2.15.2 itself now says this in as many words: "the two paths are
  observationally equivalent for that defect, which is why the suite
  records it as untested rather than pretending otherwise."
- **Whether the socket timeout is restored to its post-header value after
  the header-reading phase ends.** Every successful request in this file
  exercises that code path, so it reads as covered by line count while
  proving nothing: nothing here asserts what the restored value is or
  observes a difference a wrong one would cause, and SPEC does not name an
  externally observable consequence of getting it wrong that this file could
  assert on without guessing at an implementation detail.
- A flood spread across many source addresses. SPEC is explicit that nothing
  at this layer stops it - "a sustained flood still denies service" - so
  nothing here asserts that it is stopped; the only address diversity below
  is a handful of loopback IPs used as convenient labels for the logging
  test, never as an input to any policy the server applies.
- **Which specific connection is evicted from MAX_HTTP_CONNECTIONS - least
  recently active, not oldest - when the two differ.** Tried more than
  once: a partial request (enough for a genuine read, per SPEC's own
  language) does not appear to register as activity in the current
  implementation, and a completed request/response closes its connection
  outright, because the handler is HTTP/1.0 - so no black-box test from
  this file can hold a connection that is both still live and demonstrably
  recently used at the moment it forces the cap. That is a fact about the
  handler, not a gap in this file's patience, and SPEC 2.15.2 now says so.
  What guards the rule instead is not a black-box test but two things a
  reader can check directly: a probe that drives the server object itself
  rather than a client socket, and the fact that both marking sites still
  exist in the source - the read path and the write path each mark a
  connection as used on their own, independently, since either one alone
  going missing would make the rule silently regress to first-in-first-out
  again, the exact failure it replaced. What is tested here instead -
  `test_max_http_connections_evicts_exactly_one_connection_and_serves_the_newcomer`
  - is the structural half open to this file: exactly one connection is
  evicted and the newcomer is served, without claiming to discriminate
  which one.
- Whether `stop()`'s own drain flushes a pending expiry/eviction count
  through the report mechanism (rules 8-9) before the process count is lost.
  Not written here because SPEC 2.15.2 does not yet say it, though the
  implementation may be growing it in parallel; adding a test for behaviour
  SPEC has not stated would be testing the implementation instead of the
  specification, which is the one thing this file is not allowed to do.

Every wait below is a deadline-bounded poll, not a sleep-and-hope. SPEC
states plainly that every deadline in this section, including
`HTTP_REQUEST_DEADLINE`, is "measured on a monotonic clock, not on the wall
clock" - so `Deps.now`, the injectable seam this suite uses elsewhere (and
the one SPEC itself names as the right source for a `timestamp` on the
wire), is not a lever on any of them. `HANDSHAKE_DEADLINE`,
`HTTP_REQUEST_DEADLINE` and `EXPIRY_REPORT_INTERVAL` are instead shortened
directly, by monkeypatching the module constant for the duration of one test
(`short_handshake_deadline`, `short_http_request_deadline`,
`fast_expiry_reporting`) and restoring it through `monkeypatch`'s own
teardown - never by editing the source file. Each such test requests its
patch fixture *before* `running_listeners` in its parameter list: fixtures
with no interdependency are resolved in request order, and the constant has
to be small before the server, and any per-connection code that reads it,
is ever constructed.

A peer's own socket is the only thing most tests here inspect. Whether the
server has closed its end is read from the client side, with a short-timeout
`recv`: a clean EOF (`b""`) or an outright reset means the server closed it,
and a timeout with nothing to read means it is still open and silent. Nothing
here reaches into `Listeners`, a thread name, or a file descriptor table,
except `caplog` for the logging and reporting rules, which is the observable
those rules are about - and there, the check is on `record.name` (the
logger, `"plugin.companion"`) or on a message's own text, never on a source
address in a message whose formatting does not carry one; see the
request-deadline silence test for why that distinction is load-bearing.

Mutations were run against the working implementation, each a single line
patched in place and restored immediately after - named here in words
someone could re-run rather than claimed and left unchecked:

- `request.settimeout(HANDSHAKE_DEADLINE)` widened to
  `request.settimeout(HANDSHAKE_DEADLINE * 100000)` -
  `test_handshake_deadline_drains_the_registry_with_no_reconnection` fails,
  because nothing closes the peers it opens.
- `if len(self._pending) > MAX_PENDING_HANDSHAKES:` widened to
  `> MAX_PENDING_HANDSHAKES * 1000` -
  `test_at_the_cap_the_oldest_handshake_is_closed_not_the_newest` and
  `test_bound_holds_across_many_silent_connections` both fail, because the
  open count never comes back down to the cap.
- `if len(self._live_order) > MAX_HTTP_CONNECTIONS:` widened to
  `> MAX_HTTP_CONNECTIONS * 1000` -
  `test_max_http_connections_evicts_exactly_one_connection_and_serves_the_newcomer`
  and `test_max_http_connections_counts_a_pending_handshake_toward_the_same_cap`
  both fail, because nothing is evicted to make room for the newcomer.
- `self._live_order.pop(request, None)` (the release site every torn-down
  connection passes through) changed to `.get(request, None)`, a read that
  does not remove the entry - every test in this file still passes. This is
  not a failure to fix; it is the finding the "not tested" list above
  describes in words: under oldest-first eviction, a missing release here is
  unobservable from outside the process, because a registry full of
  unreleased zombies evicts a zombie on every arrival exactly as a correctly
  releasing one would.
- The gate `if now - self._report_window < EXPIRY_REPORT_INTERVAL: return`
  widened to always return (`if True: return`) -
  `test_expiry_report_line_is_eventually_written` fails, because no report
  line ever appears.
- The same gate narrowed to never hold
  (`if now - self._report_window < 0: return`, which a monotonic clock can
  never satisfy) - `test_expiry_report_rate_limits_to_at_most_one_line_per_
  interval` fails, producing one report line per expiry instead of one per
  interval.
- `if args and isinstance(args[0], _RequestDeadlineExceeded): return`
  (the suppression check in `log_error` that keeps a request-deadline
  timeout silent) short-circuited to never match
  (`if False and args and ...`) -
  `test_silence_after_a_completed_handshake_is_eventually_closed` fails,
  and the positive control inside it independently confirms the same
  malformed-request path still logs, which is what makes the failure mean
  something rather than a needle that stopped matching anything at all.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
from typing import Any, Callable

import pytest

from plugin import companion

# Inside tls_material's SAN (IP:127.0.0.1, IP:127.0.0.2); loopback keeps the
# test independent of any tether interface actually being present.
ADDRESS = "127.0.0.1"

# Distinct loopback addresses used only as labels for the logging test, so a
# log record can be matched to the specific connection that produced it. Not
# an input to any policy the server applies - SPEC withdrew source-address
# accounting entirely (see the module docstring).
EVICTION_LOG_SOURCE = "127.0.0.85"
EXPIRY_LOG_SOURCE = "127.0.0.86"
GARBAGE_LOG_SOURCE = "127.0.0.90"


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
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
    for the same double. The WSS side is not under test here, but `Listeners`
    still needs a connection handler to construct."""
    await websocket.wait_closed()


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(str(tls_material["cert"]), str(tls_material["key"]))
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def make_listeners(tmp_path, tls_material, free_ports, deps, ssl_context):
    """Mirrors the fixture of the same name in test_https_handshake_off_accept.py,
    test_listeners_stop_race.py and test_binding.py. Duplicated rather than
    shared, on purpose and for the same reason those three give: this file's
    socket-heavy tests should stay free to change its own fixture without
    touching a file another issue's regression test also depends on.
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
        "the HTTPS listener never came up - this run proves nothing about issue #102"
    )
    return listeners


@pytest.fixture
def short_handshake_deadline(monkeypatch):
    """Patches `companion.HANDSHAKE_DEADLINE` down to 1 second for the test.

    Listed before `running_listeners` in every test that needs it: fixtures
    with no interdependency are resolved in the order they are requested, so
    the constant is small before `running_listeners` constructs the server
    and before any per-connection code that reads it ever runs. Reverted
    automatically by `monkeypatch` at teardown - this never touches the
    source file.
    """
    monkeypatch.setattr(companion, "HANDSHAKE_DEADLINE", 1)
    return 1


@pytest.fixture
def short_http_request_deadline(monkeypatch):
    """Patches `companion.HTTP_REQUEST_DEADLINE` down to 1 second for the test.

    SPEC 2.15.2 states every deadline in this section, this one included, is
    "measured on a monotonic clock, not on the wall clock" - so `Deps.now`
    (the injectable seam used elsewhere in this suite, and the one SPEC
    names as the right source for a `timestamp` on the wire) is not the
    clock this deadline reads, and advancing it buys nothing here. The
    constant itself is the only lever left, and it has to be small before
    `running_listeners` constructs the server, for the same reason
    `short_handshake_deadline` is listed first: fixtures with no
    interdependency are resolved in request order.
    """
    monkeypatch.setattr(companion, "HTTP_REQUEST_DEADLINE", 1)
    return 1


@pytest.fixture
def long_handshake_deadline(monkeypatch):
    """Patches `companion.HANDSHAKE_DEADLINE` *up* to 120 seconds.

    For a test that establishes many real, sequential TLS handshakes as
    setup and needs one genuinely unrelated pending connection to survive
    untouched for the whole of it: at the real 30s default, a slow run (this
    file opens dozens of sockets and threads across many tests, and
    `daemon_threads = True` means none of them are ever joined, so load
    accumulates over the file's own run) can push that control peer past its
    own deadline before the test's actual assertion ever runs, which would
    look exactly like the ordering bug under test without being it -
    ruled out the same way once already this round: make the suspected
    confound impossible and see whether the symptom survives. Widening the
    deadline removes it; nothing about `HANDSHAKE_DEADLINE`'s own value is
    what these tests check.
    """
    monkeypatch.setattr(companion, "HANDSHAKE_DEADLINE", 120)
    return 120


@pytest.fixture
def fast_expiry_reporting(monkeypatch):
    """Patches `companion.HANDSHAKE_DEADLINE` and
    `companion.EXPIRY_REPORT_INTERVAL` down together, sized so every
    handshake in a test expires with time to spare before the interval
    itself elapses: the deadline at a fifth of the interval. Both are read
    from the module global at call time, the same as every other constant
    this file monkeypatches - see the module docstring for why that is the
    only lever available for a deadline SPEC says is measured on a
    monotonic clock, not the wall clock (or an injected one).

    Returns `(deadline, interval)`.
    """
    monkeypatch.setattr(companion, "HANDSHAKE_DEADLINE", 0.2)
    monkeypatch.setattr(companion, "EXPIRY_REPORT_INTERVAL", 1.0)
    return 0.2, 1.0


def _silent_peer(https_port: int, source_ip: str | None = None) -> socket.socket:
    """A TCP connection that never speaks: no TLS, no ClientHello, nothing -
    the peer SPEC 2.15.2 bounds the count of.

    `source_ip`, when given, binds the client side of the connection to a
    specific loopback address before connecting, purely so the peer can be
    identified by address afterwards (the logging test's own purpose) - it
    is never treated specially by the server, which no longer accounts for
    source address at all.
    """
    peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if source_ip is not None:
        peer.bind((source_ip, 0))
    peer.settimeout(5.0)
    peer.connect((ADDRESS, https_port))
    return peer


def _peer_is_open(peer: socket.socket) -> bool:
    """Whether the server side of `peer` is still open, read from the client.

    A clean EOF (`recv` returning `b""`) or a reset means the server closed
    its end - the outcome SPEC 2.15.2 promises for an evicted, expired or
    torn-down handshake. A timeout with nothing arrived means the server has
    not touched this connection: it is still parked in the handshake state.
    """
    original_timeout = peer.gettimeout()
    peer.settimeout(0.05)
    try:
        data = peer.recv(1)
        return len(data) != 0
    except (socket.timeout, TimeoutError):
        return True
    except OSError:
        return False
    finally:
        try:
            peer.settimeout(original_timeout)
        except OSError:
            pass


def _count_open(peers: list[socket.socket]) -> int:
    return sum(1 for peer in peers if _peer_is_open(peer))


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
# The constants themselves
# ---------------------------------------------------------------------------


def test_max_pending_handshakes_is_sixteen():
    """SPEC 2.15.2 pins the exact number, not just "some bound" - it is
    derived from a browser's six-connections-per-host limit times two
    legitimate clients plus headroom, and a different number would change
    that reasoning's own conclusion silently.
    """
    assert companion.MAX_PENDING_HANDSHAKES == 16


def test_handshake_deadline_is_thirty_seconds():
    """SPEC 2.15.2 pins this exactly: "three orders of magnitude above any
    handshake that will ever complete... it only has to be safely larger
    than [the link], and it is" - an argument about this specific number,
    not about "some timeout".
    """
    assert companion.HANDSHAKE_DEADLINE == 30


def test_http_request_deadline_is_thirty_seconds():
    """SPEC 2.15.2 pins the number itself for the post-handshake request
    deadline too - "generous by orders of magnitude for a link whose round
    trip is measured in milliseconds" is an argument about this specific
    number. A distinct constant from HANDSHAKE_DEADLINE, even though both
    happen to be 30 today: one bounds the handshake itself, the other bounds
    how long a client that already finished it may take to ask for something.
    """
    assert companion.HTTP_REQUEST_DEADLINE == 30


def test_max_http_connections_is_forty_eight():
    """SPEC 2.15.2: "three times what SPEC 2.15.2's own reasoning gives a
    legitimate client" - three times MAX_PENDING_HANDSHAKES.
    """
    assert companion.MAX_HTTP_CONNECTIONS == 48


def test_expiry_report_interval_is_sixty_seconds():
    """SPEC 2.15.2 pins the number: "one line is written at most once a
    minute" - sixty seconds, not some unspecified rate limit.
    """
    assert companion.EXPIRY_REPORT_INTERVAL == 60


# ---------------------------------------------------------------------------
# Rules 1 and 3: the aggregate cap, and plain oldest-first eviction
# ---------------------------------------------------------------------------


def test_bound_holds_across_many_silent_connections(running_listeners, ports):
    """SPEC 2.15.2, rule 1: at most MAX_PENDING_HANDSHAKES may be in the
    handshake state at once, regardless of how many silent peers arrive.

    Three times the cap connect and never speak. Catches a mutant that
    removes the aggregate cap entirely - the open count would settle at 3x
    the cap instead of exactly the cap.
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    overflow = cap * 3

    peers = [_silent_peer(ports[1]) for _ in range(overflow)]
    try:
        settled = wait_until(lambda: _count_open(peers) <= cap, timeout=15.0)
        assert settled, (
            f"{overflow} silent peers connected and the open count never dropped to the "
            f"cap ({cap}) within the deadline - issue #102's bound is not holding"
        )
        open_count = _count_open(peers)
        assert open_count == cap, (
            f"expected exactly {cap} silent peers to remain open, found {open_count} - "
            "the cap on unfinished handshakes is not exact"
        )
    finally:
        for peer in peers:
            peer.close()


def test_at_the_cap_the_oldest_handshake_is_closed_not_the_newest(running_listeners, ports):
    """SPEC 2.15.2, rule 3 - the plain rule that replaced source-address
    accounting: "At the cap, the connection closed is simply the oldest
    unfinished handshake." A test that only checks "the count stays at the
    cap" would also pass an implementation that refuses the newest
    connection instead, which SPEC explicitly rejects: that hands anyone who
    can reach the port a way to keep the owner out for as long as the flood
    lasts. This test asserts which specific connection dies.

    No fixed sleep is used to establish ordering: MAX_PENDING_HANDSHAKES
    silent peers are opened with blocking, sequential `connect()` calls from
    a single thread, so the kernel's accept queue - and therefore the order
    `accept()` sees them in - matches connection order. Settling is polled
    for with a deadline; only the assertion on *which* peer is closed
    depends on FIFO ordering being preserved, and that ordering is a kernel
    guarantee for a single client issuing sequential blocking connects to
    one listener, not an assumption about server-side scheduling.
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    peers = [_silent_peer(ports[1]) for _ in range(cap)]
    newest = _silent_peer(ports[1])
    peers.append(newest)
    try:
        settled = wait_until(lambda: _count_open(peers) <= cap, timeout=15.0)
        assert settled, (
            f"connecting one peer past the cap of {cap} never brought the open count "
            "back down within the deadline"
        )
        assert _count_open(peers) == cap

        assert not _peer_is_open(peers[0]), (
            "the oldest unfinished handshake was still open after a new connection "
            "pushed the count past the cap - SPEC 2.15.2 requires the oldest to be "
            "the one closed"
        )
        for index, peer in enumerate(peers[1:], start=1):
            assert _peer_is_open(peer), (
                f"peer #{index} (not the oldest) was closed at the cap - SPEC 2.15.2 "
                "requires the oldest unfinished handshake to be closed, not some other one"
            )
        assert _peer_is_open(newest), (
            "the newest connection was the one closed at the cap - SPEC 2.15.2 explicitly "
            "rejects refusing the newest connection instead of evicting the oldest"
        )
    finally:
        for peer in peers:
            peer.close()


# ---------------------------------------------------------------------------
# Rule 2: HANDSHAKE_DEADLINE - a slot is never held forever
# ---------------------------------------------------------------------------


def test_handshake_deadline_drains_the_registry_with_no_reconnection(
    short_handshake_deadline, running_listeners, ports
):
    """SPEC 2.15.2, rule 2 - "a slot in this registry is never held forever"
    - and the property the section says this design has that its two
    predecessors did not: "the registry returns to empty after the deadline
    with no reconnection". A single burst of silent peers connects once and
    is never touched again by this test; the registry must still drain back
    to zero on its own, purely from `HANDSHAKE_DEADLINE` expiring.

    A mutant that removes the deadline (or never actually applies it to an
    accepted connection) leaves every one of these peers open forever - this
    test's own bounded poll would then time out rather than pass, so a
    regression here fails loudly rather than the test quietly proving
    nothing.

    Uses `short_handshake_deadline` (a monkeypatched constant, not real 30s
    or an injected clock) - see the module docstring for why a plain socket
    timeout needs the constant shortened before it is ever handed to the
    kernel.

    Openness is asserted at connect time, one peer at a time, rather than by
    a single batch check of "all sixteen open at once" after every peer has
    been created. With the deadline patched to one second, that batch
    precondition is structurally unreachable under load: `_count_open` costs
    up to `cap` times 50ms of that same second, and `wait_until` retries a
    predicate that can only ever decrease as peers start expiring, so once
    the first one does, "all sixteen open" can never become true again for
    the rest of the poll - a flake with nothing to do with the drain this
    test is actually about. The property under test does not need every
    peer alive at one instant; it only needs each one confirmed open when it
    was created, and all of them eventually gone.
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    peers: list[socket.socket] = []
    for index in range(cap):
        peer = _silent_peer(ports[1])
        assert _peer_is_open(peer), (
            f"peer #{index} was already closed immediately after connecting - this run "
            "proves nothing about the handshake deadline"
        )
        peers.append(peer)
    try:
        drained = wait_until(lambda: _count_open(peers) == 0, timeout=15.0)
        assert drained, (
            f"{_count_open(peers)} of {cap} silent peers were still open well after "
            f"HANDSHAKE_DEADLINE ({short_handshake_deadline}s, patched) should have "
            "expired every one of them - the registry never drained on its own"
        )
    finally:
        for peer in peers:
            peer.close()


# ---------------------------------------------------------------------------
# Rule 4: a completed handshake is never refused, repeatedly
# ---------------------------------------------------------------------------


def test_owner_gets_in_repeatedly_while_the_registry_is_full(
    running_listeners, ports, tls_material
):
    """SPEC 2.15.2, rule 4 - and specifically the claim that matters more
    than "one request got through": the owner gets in *repeatedly* while the
    cap is full of abandoned connections, not just once. The implementation's
    own probe is cited as five consecutive successful requests against a full
    registry; a test asserting a single success proves less than one that
    asserts it keeps working, so this one makes five in a row, with the
    registry re-filled to the cap by the silent peers already holding it
    full throughout (each real request may evict the then-oldest pending
    connection to get its own slot, exactly as SPEC 2.15.2 rule 3 specifies,
    but that must never be *this* client's own request).
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    silent_peers = [_silent_peer(ports[1]) for _ in range(cap)]
    try:
        assert wait_until(lambda: _count_open(silent_peers) == cap), (
            f"could not even establish {cap} concurrent silent peers - this run proves "
            "nothing about rule 4"
        )

        for attempt in range(5):
            try:
                status, body = _get_index(ports[1], tls_material)
            except OSError as exc:
                pytest.fail(
                    f"a real HTTPS request (attempt {attempt + 1} of 5) was refused or "
                    f"timed out while the handshake-state cap was held full: {exc!r}"
                )
            assert status == 200, f"attempt {attempt + 1} of 5 returned status {status}"
            assert b"<!doctype html>" in body
    finally:
        for peer in silent_peers:
            peer.close()


# ---------------------------------------------------------------------------
# Rule 5: stop() closes unfinished handshakes and returns promptly
# ---------------------------------------------------------------------------


def test_stop_closes_unfinished_handshakes_and_returns_promptly(running_listeners, ports):
    """SPEC 2.15.2, rule 5: `stop()` closes every socket still in the
    handshake state and does not join their threads. "Does not join" is
    observed as "returns promptly" - a join here would block for the life of
    a daemon thread that never exits on its own, which is exactly what #90
    and #100 both depend on `stop()` never doing.

    More than the cap connect and never speak, so this exercises both a
    peer that was already evicted before `stop()` ran and one that was still
    parked in the handshake state when it was called - both must end up
    closed. `stop()` itself runs on a background thread with a bounded join,
    the same pattern `test_https_handshake_off_accept.py` uses for the same
    reason: a regression that reintroduces a wait must fail this test, not
    hang the job.
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    peers = [_silent_peer(ports[1]) for _ in range(cap * 2)]
    try:
        assert wait_until(lambda: _count_open(peers) <= cap, timeout=15.0), (
            "the open count never settled at the cap before stop() was even called - "
            "this run proves nothing about stop()'s own behaviour"
        )

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
            "Listeners.stop() never returned with unfinished handshakes still parked - "
            "a join on their daemon threads would explain a hang like this, and SPEC "
            "2.15.2 forbids it"
        )
        if "error" in stop_result:
            raise stop_result["error"]

        all_closed = wait_until(lambda: _count_open(peers) == 0, timeout=5.0)
        assert all_closed, (
            "at least one peer that never completed a handshake still held an open "
            "descriptor after stop() returned - SPEC 2.15.2's acceptance criterion for "
            "stop() is that none do"
        )
    finally:
        for peer in peers:
            peer.close()


# ---------------------------------------------------------------------------
# Rule 6: stop() drains after shutdown() returns, not before it
# ---------------------------------------------------------------------------


def test_stop_drains_connections_accepted_during_its_own_shutdown_window(running_listeners, ports):
    """SPEC 2.15.2, "The drain in stop() happens after shutdown() returns,
    not before it": draining first races the loop it is draining -
    `shutdown()` only sets a flag the accept loop notices up to its own poll
    interval later, so a connection accepted in that window is registered
    into the pending set only after a drain-first implementation already
    took its snapshot and cleared it. Nothing ever closes that connection
    afterwards: `server_close()` releases only the listening socket. That
    leak is real, not merely delayed - it survives `stop()` returning and
    accumulates across every reload, which is what this section exists to
    prevent.

    A background thread keeps opening new silent connections for as long as
    `stop()` is in flight, so at least one has a real chance of landing
    inside whatever window `shutdown()` leaves between setting its flag and
    the accept loop noticing it. This file has no way to aim at that window
    directly without reading the implementation, so it is raced against
    instead: every connection this thread manages to open (a failed connect,
    once the listening socket is actually gone, is caught and discarded, not
    recorded - nothing was accepted to leak) must end up closed within a
    bounded poll after `stop()` returns.
    """
    connected: list[socket.socket] = []
    connected_lock = threading.Lock()
    keep_connecting = threading.Event()
    keep_connecting.set()

    def _connector() -> None:
        while keep_connecting.is_set():
            peer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                peer.settimeout(1.0)
                peer.connect((ADDRESS, ports[1]))
            except OSError:
                peer.close()
                continue
            with connected_lock:
                connected.append(peer)
            time.sleep(0.005)

    connector_thread = threading.Thread(target=_connector, daemon=True)
    connector_thread.start()

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
        "stop() never returned while a connector kept opening new connections against it"
    )

    # A short grace period so a connection landing exactly as stop() returns
    # still has a chance to be recorded, before the connector is told to
    # stop - fixed and short on purpose, since it only widens the window the
    # assertion below covers rather than deciding its outcome.
    time.sleep(0.2)
    keep_connecting.clear()
    connector_thread.join(timeout=5.0)

    if "error" in stop_result:
        raise stop_result["error"]

    with connected_lock:
        peers_snapshot = list(connected)

    assert peers_snapshot, (
        "the connector thread never managed to open a single connection while stop() was "
        "in flight - this run proves nothing about the race"
    )

    try:
        all_closed = wait_until(lambda: _count_open(peers_snapshot) == 0, timeout=10.0)
        assert all_closed, (
            f"{_count_open(peers_snapshot)} of {len(peers_snapshot)} connections opened "
            "during stop()'s own teardown window were still open afterwards - draining "
            "before shutdown() leaves a connection accepted in that window unclosed"
        )
    finally:
        for peer in peers_snapshot:
            peer.close()


# ---------------------------------------------------------------------------
# Rule 7: every deadline in this section is silent
# ---------------------------------------------------------------------------


def test_eviction_and_handshake_expiry_produce_no_log_record_naming_them(
    short_handshake_deadline, running_listeners, ports, caplog
):
    """SPEC 2.15.2: "Every deadline this section sets is silent... the
    handshake one included" - an eviction at the cap and a handshake that
    expires after `HANDSHAKE_DEADLINE` must both produce no log record
    naming the connection, because in both cases the plugin set the clock
    (or the cap) and the plugin closed the socket - its own doing, the same
    as an eviction. A handshake that fails on its own still logs at debug,
    because that one is news, not the plugin's doing.

    Three phases inside one capture window:

    1. Positive control (`GARBAGE_LOG_SOURCE`): plaintext sent instead of a
       ClientHello is a genuine, non-plugin-caused handshake failure and
       must still log. This proves the address-matching technique below
       actually finds a real logged failure before the other two phases are
       trusted to find nothing - without it, a needle that stopped matching
       anything, ever, would make this test pass for the wrong reason.
    2. Eviction (`EVICTION_LOG_SOURCE`): enough silent peers to overflow the
       aggregate cap, so several are evicted. None may log.
    3. Expiry (`EXPIRY_LOG_SOURCE`): one silent peer, left alone past the
       (patched, short) `HANDSHAKE_DEADLINE`. It must not log either.

    The needle is the source host alone (`getsockname()[0]`), not the full
    `(host, port)` tuple: coupling this assertion to the exact tuple-repr
    `%s`-formatting a log line happens to use today would make it silently
    vacuous the day that formatting changes.
    """
    cap = companion.MAX_PENDING_HANDSHAKES
    records_snapshot: list[str] = []
    eviction_peers: list[socket.socket] = []
    expiry_peer: socket.socket | None = None

    with caplog.at_level(0):
        try:
            # Phase 1: positive control.
            garbage_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            garbage_socket.bind((GARBAGE_LOG_SOURCE, 0))
            garbage_socket.settimeout(5.0)
            garbage_socket.connect((ADDRESS, ports[1]))
            try:
                garbage_socket.sendall(b"not a tls client hello, just garbage bytes\r\n\r\n")
            finally:
                garbage_socket.close()

            control_found = wait_until(
                lambda: any(
                    GARBAGE_LOG_SOURCE in record.getMessage() for record in caplog.records
                ),
                timeout=5.0,
            )
            assert control_found, (
                "a genuine handshake failure (plaintext sent to the HTTPS port) never "
                "produced a log record naming its source - this run proves nothing about "
                "the silence checks below, which rely on the same matching technique"
            )

            # Phase 2: eviction at the cap. The peers still open afterwards
            # (the ones that survived to the cap) are deliberately left open
            # here - closing a live, mid-handshake connection from the
            # client side is itself a failed handshake and legitimately
            # logs (SPEC 2.15), which would misattribute to this rule if it
            # happened before the snapshot below.
            eviction_peers = [
                _silent_peer(ports[1], source_ip=EVICTION_LOG_SOURCE) for _ in range(cap + 10)
            ]
            settled = wait_until(lambda: _count_open(eviction_peers) <= cap, timeout=15.0)
            assert settled, (
                "the open count never settled at the cap - this run proves nothing about "
                "eviction logging"
            )
            evicted = any(not _peer_is_open(peer) for peer in eviction_peers)
            assert evicted, (
                "none of the overflow peers were ever evicted - this run proves nothing "
                "about eviction logging"
            )

            # Phase 3: handshake-deadline expiry. Already closed server-side
            # by the time `expired` is true, so closing our own end after
            # the snapshot is just tidiness, not a source of a stray log.
            expiry_peer = _silent_peer(ports[1], source_ip=EXPIRY_LOG_SOURCE)
            expired = wait_until(lambda: not _peer_is_open(expiry_peer), timeout=15.0)
            assert expired, (
                f"a silent peer was never closed by HANDSHAKE_DEADLINE "
                f"({short_handshake_deadline}s, patched) - this run proves nothing about "
                "expiry logging"
            )

            # A short, fixed pause for any log call made by a handler thread
            # that has already exited to actually land, before the snapshot
            # below is taken.
            time.sleep(0.3)
            records_snapshot = [record.getMessage() for record in caplog.records]
        finally:
            for peer in eviction_peers:
                peer.close()
            if expiry_peer is not None:
                expiry_peer.close()

    for source in (EVICTION_LOG_SOURCE, EXPIRY_LOG_SOURCE):
        offending = [message for message in records_snapshot if source in message]
        assert not offending, (
            f"a log record named {source!r} - SPEC 2.15.2 says every deadline this "
            f"section sets is silent, eviction and handshake expiry both: {offending!r}"
        )


# ---------------------------------------------------------------------------
# Rule 8: EXPIRY_REPORT_INTERVAL - abandoned handshakes are counted and
# reported, at most once per interval
# ---------------------------------------------------------------------------


def test_expiry_report_line_is_eventually_written(
    fast_expiry_reporting, running_listeners, ports, caplog
):
    """SPEC 2.15.2: "a tether so degraded that no handshake ever completes
    produces, under [blanket silence], nothing at all. That is the owner's
    own symptom, and it is the one this listener is best placed to report.
    So expiries are counted, and one line is written at most once a minute
    saying how many there have been since the last one." This proves the
    line exists at all - the next test proves it is rate-limited, which is
    a different property neither test can stand in for.

    Both `HANDSHAKE_DEADLINE` and `EXPIRY_REPORT_INTERVAL` are patched down
    together (`fast_expiry_reporting`), and a continuous trickle of newly
    connected, immediately-abandoned peers is kept up for several report
    intervals' worth of real time - long enough that at least one
    expiry-and-gate-check is guaranteed to land after the interval has
    elapsed, regardless of exactly when the implementation's own internal
    window starts counting from. A mutant that removes the report path
    entirely leaves no matching record for the whole of this window; this
    test's own bounded wait then times out rather than passing.

    The needle is `"expired without completing"`, observed from a real run
    against the working implementation rather than read from its source or
    guessed from SPEC's prose - SPEC states only the content ("how many
    there have been"), not a string. A plain "any record from
    plugin.companion" check is not used here: this scenario's rapid,
    high-churn silent connections also produce an unrelated, occasional
    `handle_error`-logged `SSLEOFError` this file did not set out to
    explain, and a needle that matched it too would still pass while
    proving nothing about the report line specifically.
    """
    handshake_deadline, interval = fast_expiry_reporting
    peers: list[socket.socket] = []
    with caplog.at_level(0):
        try:
            trickle_until = time.monotonic() + interval * 6
            while time.monotonic() < trickle_until:
                peers.append(_silent_peer(ports[1]))
                time.sleep(handshake_deadline * 1.5)

            wrote_report = wait_until(
                lambda: any(
                    "expired without completing" in record.getMessage()
                    for record in caplog.records
                    if record.name == "plugin.companion"
                ),
                timeout=interval * 3,
            )
        finally:
            for peer in peers:
                peer.close()

    assert wrote_report, (
        f"no report line ever appeared from the plugin.companion logger over several "
        f"EXPIRY_REPORT_INTERVAL ({interval}s, patched) windows' worth of abandoned, "
        "expiring connections - SPEC 2.15.2 says a degraded tether where no handshake "
        "ever completes must still produce a report, not total silence"
    )


def test_expiry_report_rate_limits_to_at_most_one_line_per_interval(
    fast_expiry_reporting, running_listeners, ports, caplog
):
    """SPEC 2.15.2: "A peer can force that line, but not more than one a
    minute however many connections it opens, which is the property that
    made the per-connection line unacceptable." This is the rate limit
    itself, and it is the point of the whole mechanism, not a detail of it.

    `MAX_PENDING_HANDSHAKES` silent peers - enough to be "many", chosen to
    stay under the aggregate cap so every one of them expires via
    `HANDSHAKE_DEADLINE` rather than being evicted by it, which would be a
    different, already-silent event this test has no business counting -
    connect in a tight burst and are left to expire, all comfortably inside
    one patched `EXPIRY_REPORT_INTERVAL` window. However many of them
    expire, at most one report line may appear.

    Counted by matching `"expired without completing"` (observed from a
    real run, not read from the source or guessed from SPEC's prose - see
    the previous test) rather than by counting every `plugin.companion`
    record: this scenario's rapid, high-churn silent connections also
    produce an occasional, unrelated `SSLEOFError` this file did not set
    out to explain, and counting it here would risk failing this test for a
    reason that has nothing to do with the rate limit.
    """
    handshake_deadline, interval = fast_expiry_reporting
    peers = [_silent_peer(ports[1]) for _ in range(companion.MAX_PENDING_HANDSHAKES)]
    try:
        with caplog.at_level(0):
            all_expired = wait_until(
                lambda: _count_open(peers) == 0,
                timeout=handshake_deadline + interval / 2,
            )
            assert all_expired, (
                "not every abandoned peer had expired well before the patched "
                "EXPIRY_REPORT_INTERVAL elapsed - this run proves nothing about the "
                "rate limit"
            )
            report_count = sum(
                1
                for record in caplog.records
                if record.name == "plugin.companion"
                and "expired without completing" in record.getMessage()
            )
        assert report_count <= 1, (
            f"{report_count} report lines were produced from {len(peers)} abandoned "
            "connections expiring within a single EXPIRY_REPORT_INTERVAL window - SPEC "
            "2.15.2 says at most one report line per interval, however many "
            "connections a peer opens"
        )
    finally:
        for peer in peers:
            peer.close()


def test_silence_after_a_completed_handshake_is_eventually_closed(
    short_http_request_deadline, running_listeners, ports, tls_material, caplog
):
    """SPEC 2.15.2: a completed handshake has `HTTP_REQUEST_DEADLINE` seconds
    to finish sending its request headers, and "a request deadline that
    expires is silent too... a rule that forbids the eviction line while
    permitting [BaseHTTPRequestHandler's default timed-out-read log] is a
    rule that has not been read carefully."

    The rule this pins is enforced by suppressing one specific exception in
    the handler's `log_error` override, which - like the stdlib
    `log_message` it eventually reaches for an unsuppressed error - never
    includes the peer's address in what it logs; only `handle_error` (a
    different override, covering TLS-level failures per SPEC 2.15) formats
    an address. An earlier version of this test asserted on the peer's
    address not appearing in any record, which cannot fail regardless of
    what the implementation does, since that text can never appear on this
    path at all - review ran it against an implementation that was in fact
    logging "Request timed out" for exactly this case, and the assertion
    stayed green through it. This version asserts on there being no record
    at all from the `plugin.companion` logger during the silent window.

    A positive control on the *same* path is what makes that assertion mean
    something: a malformed request line on a separate connection is a
    genuine, non-deadline failure that must still reach `log_error` ("every
    other error this method reports... still logs, unchanged"), proving a
    record from this logger is observable at all on this exact method chain
    before the silent case is trusted to produce none. A control that
    exercises `handle_error` instead - as this file's eviction-logging test
    does, correctly, for the path *it* is about - would not validate this
    needle: that path formats an address into what it logs, and this one
    never does, so a control has to travel with the needle it is meant to
    validate rather than merely live in the same file.

    Uses `short_http_request_deadline` (a monkeypatched constant) rather than
    real 30 seconds or an injected clock - see the module docstring for why
    a deadline SPEC says is "measured on a monotonic clock, not on the wall
    clock" cannot be sped up any other way from outside.
    """
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))

    with caplog.at_level(0):
        # Positive control: a malformed request line is a genuine failure
        # this handler still logs, on the exact log_error/log_message path
        # the deadline-suppression check lives on - not `handle_error`.
        control_raw = socket.create_connection((ADDRESS, ports[1]), timeout=10.0)
        control = client_context.wrap_socket(control_raw, server_hostname=ADDRESS)
        try:
            control.sendall(b"NOT A VALID REQUEST LINE AT ALL\r\n\r\n")
        finally:
            control.close()

        control_found = wait_until(
            lambda: any(record.name == "plugin.companion" for record in caplog.records),
            timeout=5.0,
        )
        assert control_found, (
            "a malformed request line never produced a log record from the "
            "plugin.companion logger - this run proves nothing about the silence "
            "check below, which relies on that same logger producing nothing at all"
        )
        caplog.clear()

        # The actual case: a completed handshake, then silence.
        raw = socket.create_connection((ADDRESS, ports[1]), timeout=10.0)
        wrapped = client_context.wrap_socket(raw, server_hostname=ADDRESS)
        try:
            assert _peer_is_open(wrapped), (
                "the connection was already closed immediately after its TLS handshake "
                "completed - this run proves nothing about the request deadline"
            )

            closed = wait_until(
                lambda: not _peer_is_open(wrapped), timeout=short_http_request_deadline + 5.0
            )
            assert closed, (
                f"a silent connection was still open more than "
                f"{short_http_request_deadline + 5}s after its handshake completed "
                f"(HTTP_REQUEST_DEADLINE patched to {short_http_request_deadline}s) - the "
                "request deadline is not firing"
            )
            time.sleep(0.3)
        finally:
            wrapped.close()

    offending = [
        (record.name, record.getMessage())
        for record in caplog.records
        if record.name == "plugin.companion"
    ]
    assert not offending, (
        "a log record was produced from the plugin.companion logger while a completed "
        f"handshake's request deadline expired - SPEC 2.15.2 says that expiry is "
        f"silent, the same as an eviction: {offending!r}"
    )


def test_a_trickle_does_not_indefinitely_extend_the_request_deadline(
    short_http_request_deadline, running_listeners, ports, tls_material
):
    """SPEC 2.15.2: "The remaining time is therefore recomputed before each
    read, so a trickle runs the deadline down like silence does" - the whole
    point of making the bound a deadline rather than `socketserver`'s
    per-read `timeout` attribute, which "a peer resets... by sending one
    byte every twenty-nine seconds."

    Uses `short_http_request_deadline` (a monkeypatched constant, not an
    injected clock - see the module docstring for why) and a trickle
    interval deliberately much shorter than the patched deadline: each line
    arrives well inside what a *resettable* per-read timeout of the same
    duration would consider "in time", so only a genuinely absolute deadline
    can still close this connection within the real-time budget below.
    Trickled a complete header line at a time (`X-Trickle: N\\r\\n`), never
    the blank line that would end the headers, rather than one raw byte: a
    header parser built on buffered `readline()` calls only returns from the
    *current* call once a line terminator arrives, so a lone byte with no
    terminator would sit inside one buffered read this test cannot observe
    or influence.

    A regression back to a plain per-read timeout would never close this
    connection within the loop's real-time budget: every trickled line
    arrives at roughly a tenth of the patched deadline apart, so each one
    would reset such a timeout well before it could ever fire.
    """
    raw = socket.create_connection((ADDRESS, ports[1]), timeout=10.0)
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    wrapped = client_context.wrap_socket(raw, server_hostname=ADDRESS)
    try:
        assert _peer_is_open(wrapped), (
            "the connection was already closed immediately after its TLS handshake "
            "completed - this run proves nothing about the request deadline"
        )

        # A well-formed request line first, so the header-line trickle below
        # is read as headers rather than rejected as a malformed request.
        wrapped.sendall(b"GET / HTTP/1.1\r\nHost: example.invalid\r\n")

        trickle_interval = short_http_request_deadline / 10
        real_budget = time.monotonic() + max(short_http_request_deadline * 5, 10.0)
        closed = False
        line_number = 0
        while time.monotonic() < real_budget:
            line_number += 1
            try:
                wrapped.sendall(f"X-Trickle: {line_number}\r\n".encode("ascii"))
            except OSError:
                closed = True
                break
            time.sleep(trickle_interval)
            if not _peer_is_open(wrapped):
                closed = True
                break

        assert closed, (
            f"a peer trickling one header line every {trickle_interval:.2f}s - well "
            f"inside the patched {short_http_request_deadline}s request deadline - was "
            "never closed within this test's real-time budget: SPEC 2.15.2's request "
            "deadline must not be reset by a trickle of data"
        )
        assert line_number >= 3, (
            f"the connection closed after only {line_number} trickled line(s), too soon "
            "to be evidence of the deadline itself rather than some unrelated failure"
        )
    finally:
        wrapped.close()


# ---------------------------------------------------------------------------
# Rule 9: MAX_HTTP_CONNECTIONS - the least recently active connection is
# evicted, not the oldest; the newcomer is served
# ---------------------------------------------------------------------------


def test_max_http_connections_evicts_exactly_one_connection_and_serves_the_newcomer(
    running_listeners, ports, tls_material
):
    """SPEC 2.15.2: "Past the cap the least recently active live connection
    is closed and the new one is served." This test pins the structural
    half of that rule that a black-box test in this file can reliably
    reach: filling the cap and then connecting once more must evict exactly
    one existing connection and serve the newcomer, never refuse it and
    never evict more than one.

    What this test deliberately does *not* attempt is proving *which*
    connection is evicted is decided by activity rather than plain age -
    see the "not tested" note in the module docstring for why constructing
    that scenario black-box against this handler was tried, more than once,
    and abandoned rather than shipped fragile or currently failing.

    Fills the cap with `MAX_HTTP_CONNECTIONS` real, completed TLS handshakes
    - not silent/pending ones, which is exactly what this cap counts that
    the handshake registry does not - then makes one more. A mutant that
    reverts to refusing the newcomer would fail the "newcomer is served"
    assertion; a mutant that evicts more than one, or none at all, would
    fail the count assertion.
    """
    cap = companion.MAX_HTTP_CONNECTIONS
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    completed: list[ssl.SSLSocket] = []
    try:
        for attempt in range(cap):
            raw = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
            try:
                wrapped = client_context.wrap_socket(raw, server_hostname=ADDRESS)
            except OSError as exc:
                pytest.fail(
                    f"completing TLS handshake #{attempt} of {cap} failed before the "
                    f"MAX_HTTP_CONNECTIONS cap should have been reached: {exc!r}"
                )
            completed.append(wrapped)

        assert all(_peer_is_open(peer) for peer in completed), (
            f"not all {cap} completed connections were still open once the cap was "
            "reached - MAX_HTTP_CONNECTIONS is refusing connections below its own limit"
        )

        newest_raw = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
        try:
            newest = client_context.wrap_socket(newest_raw, server_hostname=ADDRESS)
        except OSError as exc:
            pytest.fail(
                f"a connection made after {cap} live connections already held the "
                "MAX_HTTP_CONNECTIONS cap was refused rather than served - SPEC 2.15.2 "
                f"says an existing connection is evicted instead: {exc!r}"
            )
        try:
            evicted = wait_until(
                lambda: sum(1 for peer in completed if not _peer_is_open(peer)) == 1,
                timeout=20.0,
            )
            still_open = sum(1 for peer in completed if _peer_is_open(peer))
            assert evicted, (
                f"expected exactly one of the {cap} pre-existing live connections to be "
                f"closed once a new one arrived at a full MAX_HTTP_CONNECTIONS cap, "
                f"found {cap - still_open} closed - SPEC 2.15.2 says exactly one "
                "connection pays for a newcomer, not zero and not several"
            )
            assert _peer_is_open(newest), (
                "the newly arrived connection was the one closed - SPEC 2.15.2 says the "
                "newcomer is served, not refused, once the cap is full"
            )
        finally:
            newest.close()
    finally:
        for peer in completed:
            peer.close()


def test_max_http_connections_counts_a_pending_handshake_toward_the_same_cap(
    long_handshake_deadline, running_listeners, ports, tls_material
):
    """SPEC 2.15.2: "A connection still in the handshake state holds one of
    these forty-eight [MAX_HTTP_CONNECTIONS] too" - a pending, unfinished
    handshake shares the same live-connection cap as a completed one, and
    the same eviction rule. Age and inactivity happen to coincide in this
    scenario, since nothing here - the pending connection or any of the
    completed ones - ever does a read or a write beyond its own handshake,
    so the least-recently-active connection is also, here, simply the
    oldest: the pending one, established first and never touched again.

    One genuinely pending connection is established first, then the
    remainder of the cap is filled with completed handshakes that never do
    anything beyond completing, then one more connection is made. The
    pending one must be the one evicted - not a completed connection that
    arrived later - and the newcomer must be served. A mutant that only
    ever evicts from among *completed* connections (treating the two
    registries as separate rather than sharing one live-connection count)
    would leave the pending one untouched instead.

    Uses `long_handshake_deadline` (widened, not shortened) so the pending
    control peer cannot be mistaken for evicted by `HANDSHAKE_DEADLINE`
    itself expiring during the slow part of this test - establishing up to
    47 real, sequential TLS handshakes - which is not what this test is
    about.
    """
    cap = companion.MAX_HTTP_CONNECTIONS
    oldest_pending = _silent_peer(ports[1])
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    completed: list[ssl.SSLSocket] = []
    try:
        assert wait_until(lambda: _peer_is_open(oldest_pending)), (
            "could not even establish the oldest pending peer - this run proves nothing "
            "about mixed pending/completed ordering"
        )

        completed_needed = cap - 1
        for attempt in range(completed_needed):
            raw = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
            try:
                wrapped = client_context.wrap_socket(raw, server_hostname=ADDRESS)
            except OSError as exc:
                pytest.fail(
                    f"completing TLS handshake #{attempt} of {completed_needed} failed "
                    f"before MAX_HTTP_CONNECTIONS should have been reached: {exc!r}"
                )
            completed.append(wrapped)

        newest_raw = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
        try:
            newest = client_context.wrap_socket(newest_raw, server_hostname=ADDRESS)
        except OSError as exc:
            pytest.fail(
                "a connection made after MAX_HTTP_CONNECTIONS was reached (via one "
                f"pending and {completed_needed} completed connections) was refused "
                f"rather than served: {exc!r}"
            )
        try:
            evicted = wait_until(lambda: not _peer_is_open(oldest_pending), timeout=20.0)
            assert evicted, (
                "the pending handshake - the only connection here that has gone longer "
                "without activity than any other - was never closed once a new "
                "connection pushed the live-connection count past MAX_HTTP_CONNECTIONS "
                "- SPEC 2.15.2 says a pending connection holds one of these slots too"
            )
            for index, peer in enumerate(completed):
                assert _peer_is_open(peer), (
                    f"completed connection #{index} was closed instead of the pending "
                    "one, even though the pending connection had gone longer without "
                    "any activity"
                )
            assert _peer_is_open(newest), (
                "the newly arrived connection was refused rather than served"
            )
        finally:
            newest.close()
    finally:
        oldest_pending.close()
        for peer in completed:
            peer.close()


def test_listener_recovers_after_the_live_connection_cap_empties(
    running_listeners, ports, tls_material
):
    """SPEC 2.15.2: "the listener is fully available again within thirty
    seconds of the flood stopping". The counter behind `MAX_HTTP_CONNECTIONS`
    is the one bound in this section that does not expire on its own the way
    a pending handshake or a request deadline does - it is only ever
    released by a matching decrement when a connection is torn down.

    This test proves *availability*, not the decrement itself, and that
    distinction matters: under the current oldest-first eviction policy
    (rule 8 above), a registry left full of connections that were closed but
    never released their slot still evicts one of those zombies on every new
    arrival, the same as a correctly releasing implementation would - so an
    ordinary request succeeding here is consistent with the counter being
    exactly right, and equally consistent with it being permanently wrong by
    exactly the number of zombies accumulated so far. No sequence of
    black-box requests tells the two apart; see the module docstring's "not
    tested" list for why closing that gap is not a test problem. What this
    test does establish, and it is still worth having: the listener does not
    lock up after a full cap of ordinary connections closes, which is the
    plain, directly-stated claim SPEC makes about recovery.

    Fills `MAX_HTTP_CONNECTIONS` with real completed handshakes, closes
    every one of them from the client side - which hands the server an
    immediate EOF on each, rather than waiting for a deadline to expire -
    and then makes one ordinary request. That request must succeed within a
    bounded poll.
    """
    cap = companion.MAX_HTTP_CONNECTIONS
    client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
    completed: list[ssl.SSLSocket] = []
    try:
        for attempt in range(cap):
            raw = socket.create_connection((ADDRESS, ports[1]), timeout=5.0)
            try:
                wrapped = client_context.wrap_socket(raw, server_hostname=ADDRESS)
            except OSError as exc:
                pytest.fail(
                    f"completing TLS handshake #{attempt} of {cap} failed before the "
                    f"MAX_HTTP_CONNECTIONS cap should have been reached: {exc!r}"
                )
            completed.append(wrapped)
    finally:
        for peer in completed:
            peer.close()

    def _ordinary_request_succeeds() -> bool:
        try:
            status, body = _get_index(ports[1], tls_material)
        except OSError:
            return False
        return status == 200 and b"<!doctype html>" in body

    recovered = wait_until(_ordinary_request_succeeds, timeout=10.0)
    assert recovered, (
        f"an ordinary HTTPS request was still refused after every one of the {cap} "
        "connections holding MAX_HTTP_CONNECTIONS slots was closed - the live-connection "
        "count never released, and the listener is refusing every connection "
        "permanently rather than for the duration of a flood"
    )
