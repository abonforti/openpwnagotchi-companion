"""The periodic `stats`/`keepalive` broadcast, SPEC 2.4 and 4.3.7 (issue #65).

Before #65 the periodic broadcast rode the bettercap-refresh loop, so its
spacing was the loop's rather than `keepalive_interval`'s, and it carried only
an empty `keepalive` - a client had no way to notice a bettercap that had died
short of polling `get_stats` itself. `_background` now wakes on
`session_poll_interval` alone and sends nothing; a ticker of its own
(`Companion._stats_ticker`) broadcasts `stats` and `keepalive` together, every
`keepalive_interval`, without ever touching `agent.session()` - a synchronous
HTTP call with a 30 second timeout that would stall every connected client if
it ran on this path (SPEC 2.5, F7).

Both `keepalive_interval` (5-20 s) and `session_poll_interval` (1-5 s) are
clamped through the shared `clamp_interval()`, and the clamp is logged,
because both are load-bearing for how a client reads staleness (SPEC 2.4).
`keepalive_interval = 0`, which used to disable the broadcast outright, now
takes the default of 20 rather than the floor of 5. A value that cannot be
turned into a number at all - a stray string, or the bare `nan`/`inf`
literals TOML allows - falls back to the default and is reported at WARNING
rather than reaching `Event.wait()`, where a NaN spins the loop at full CPU
instead of ever sleeping.

Both intervals are clamped exactly once, in `on_loaded`, and stored on
`Companion._session_poll_interval` / `Companion._keepalive_interval`;
neither loop re-clamps `self.options` itself, so mutating options after
`on_loaded` must be inert, and a value poked into `plugin.options` has to
reach the plugin through `wired_plugin_factory`'s override *before*
`on_loaded` runs rather than afterwards. `keepalive_interval` is clamped
there rather than inside `_stats_ticker` because `on_loaded` owns configuration:
one place decides each interval, and neither loop can drift from it.

`rebind_interval` (5-300 s) goes through the same `clamp_interval()` and is
stored on `Companion._rebind_interval`, clamped once in `on_loaded` exactly
like the other two (issue #105). It differs from `keepalive_interval` in one
deliberate way: a configured `0` lands on the **floor** of 5 rather than the
default of 30, because `0` never meant "disable" for this key, only
"reconcile on every pass" - 5 s is the nearest the plugin will honour that. A
value that is not a usable number still takes the default and is reported at
WARNING, and - this is the actual defect #105 fixes - the background thread
must start and keep running regardless: before the fix `_background` read
`rebind_interval` with a bare `float()` at thread entry, outside the loop's
own `try`, so a non-numeric value raised before the first pass and killed the
thread that also refreshes the session and polls gpsd, for the life of the
process.

`_stats_ticker`'s loop body has an outer `try/except` around both inner
sends, the same shape `_background` already had, because `len(self._clients)`
and `deps.now()` sit outside either inner `try` and a fault there would
otherwise end the thread silently.

Driven synchronously throughout with `SteppingStop`, a `threading.Event`
double whose `wait(timeout)` records the timeout and returns immediately: the
clamped floor is 5 s and a real-clock test would have to sleep for it on every
pass, which this suite cannot afford (SPEC 10.7, the development host is
small). One test near the bottom uses a real thread and a real `Event`
instead, because `SteppingStop` is exactly the double the shutdown path
depends on and cannot also be what proves that path works.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from plugin import companion


# ---------------------------------------------------------------------------
# Schema validation, mirroring test_hooks.py local fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def outgoing_validator(schemas_dir):
    common = Resource.from_contents(
        json.loads((schemas_dir / "common.json").read_text(encoding="utf-8")),
        default_specification=DRAFT202012,
    )
    registry = Registry().with_resources(
        [
            ("common.json", common),
            ("outgoing/common.json", common),
        ]
    )

    def _for(kind: str) -> Draft202012Validator:
        schema = json.loads(
            (schemas_dir / "outgoing" / f"{kind}.json").read_text(encoding="utf-8")
        )
        schema.pop("$id", None)
        return Draft202012Validator(schema, registry=registry)

    return _for


@pytest.fixture
def check_message(outgoing_validator):
    def _check(message: dict, kind: str) -> dict:
        assert message["type"] == kind
        outgoing_validator(kind).validate(message)
        return message["data"]

    return _check


# ---------------------------------------------------------------------------
# A stop event that never sleeps
# ---------------------------------------------------------------------------


class SteppingStop:
    """A `threading.Event` double that lets a ticker loop run to completion
    without a real clock.

    `wait(timeout)` records the timeout it was given - which is the spacing
    the loop asked for - and reports itself set after `passes` calls, which is
    what ends the `while not self._stop.is_set()` loop. No thread and no sleep
    are involved: the loop body runs synchronously, `passes` times, in the
    calling thread.
    """

    def __init__(self, passes: int) -> None:
        self.waits: list[float] = []
        self._remaining = passes
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        self._remaining -= 1
        if self._remaining <= 0:
            self._set = True
        return self._set


def run_ticker(plugin, passes: int) -> SteppingStop:
    """Runs `Companion._stats_ticker` for exactly `passes` loop bodies."""
    stop = SteppingStop(passes)
    plugin._stop = stop
    plugin._stats_ticker()
    return stop


def run_background(plugin, passes: int) -> SteppingStop:
    """Runs `Companion._background` for exactly `passes` loop bodies."""
    stop = SteppingStop(passes)
    plugin._stop = stop
    plugin._background()
    return stop


# ---------------------------------------------------------------------------
# A fully wired plugin, driven by hand rather than by its own threads
# ---------------------------------------------------------------------------


@pytest.fixture
def wired_plugin_factory(options, harness, agent, tls_material, tmp_path):
    """Builds `Companion` instances taken through `on_loaded`/`on_ready`.

    A factory rather than a single object because some tests (the clamp ones)
    need to set `options` *before* `on_loaded` runs, since `on_loaded` is the
    only place `session_poll_interval` is ever clamped now - a value poked
    into `plugin.options` afterwards has nothing left to read it.

    `harness.deps.spawn` only records its target and never runs it (see
    `DepsHarness` in conftest.py), so neither `on_loaded` - which is what
    actually spawns them now (SPEC 2.5, issue #140) - nor `on_ready` starts
    `_background` or `_stats_ticker` as threads here; each test drives the
    one it is about directly, with `SteppingStop` standing in for
    `plugin._stop`.

    `plugin.broadcast` is replaced with a list-recording function by default,
    the same pattern `tests/test_keepalive.py` and `tests/test_hooks.py` use;
    a test after the real fan-out rebinds it to the genuine method.
    """
    built: list[companion.Companion] = []

    def _make(overrides: dict | None = None):
        plugin = companion.Companion()
        plugin.deps = harness.deps
        plugin.options = {
            **options,
            "tls_cert": str(tls_material["cert"]),
            "tls_key": str(tls_material["key"]),
            "web_root": str(tmp_path),
            **(overrides or {}),
        }
        plugin.on_loaded()
        assert plugin._router is not None, "the fixture did not produce a wired plugin"
        plugin.on_ready(agent)

        sent: list[dict] = []
        plugin.broadcast = sent.append

        built.append(plugin)
        return plugin, agent, sent

    yield _make

    for plugin in built:
        plugin.on_unload()


@pytest.fixture
def wired_plugin(wired_plugin_factory):
    return wired_plugin_factory()


def admit_a_client(plugin) -> None:
    """Adds a member to `_clients`, the way a successful `auth` does.

    The ticker only has something to send to once a socket has joined this
    set (SPEC 2.4: a connection joins the broadcast set at the same moment
    authentication succeeds). Which sockets get admitted - a token accepted,
    rejected, or not required - belongs to the auth test file; here the set
    is populated directly so the cadence tests do not also have to open a
    real connection.
    """
    plugin._clients.add(object())


class RecordingClient:
    """The minimum a real WebSocket connection offers `Companion.broadcast()`:
    an object with an async `send()`. Used only by the fan-out test below,
    which routes through the genuine `broadcast()` rather than the
    list-recording double every other test in this file installs.

    `broadcast()` schedules one coroutine per message via
    `run_coroutine_threadsafe` and does not wait on either, so the two sends
    from one tick can land in either order and are not simultaneous; a test
    reading `received` must wait for both rather than for the first.
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self._lock = threading.Lock()
        self._new_arrival = threading.Event()

    async def send(self, payload: str) -> None:
        with self._lock:
            self.received.append(payload)
        self._new_arrival.set()

    def wait_for_count(self, count: int, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if len(self.received) >= count:
                    return
            remaining = deadline - time.monotonic()
            assert remaining > 0, (
                f"only {len(self.received)} of {count} messages delivered "
                f"within {timeout}s"
            )
            self._new_arrival.wait(remaining)
            self._new_arrival.clear()


# ---------------------------------------------------------------------------
# stats + keepalive ride the same tick
# ---------------------------------------------------------------------------


def test_stats_and_keepalive_go_out_on_the_same_tick(wired_plugin, check_message):
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)

    run_ticker(plugin, passes=1)

    types = [message["type"] for message in sent]
    # Both, not one: SPEC 4.3.7 is explicit that keepalive is redundant
    # information once stats is broadcast, and dropping it anyway is a
    # deliberate decision this suite must not make for the plugin.
    assert sorted(types) == ["keepalive", "stats"], sent
    stats_message = next(m for m in sent if m["type"] == "stats")
    keepalive_message = next(m for m in sent if m["type"] == "keepalive")
    check_message(stats_message, "stats")
    check_message(keepalive_message, "keepalive")


def test_keepalive_data_is_empty(wired_plugin):
    """The `keepalive` schema fixes this too, but the ticker builds the
    envelope itself rather than going through a schema at runtime, so a stray
    key added there would not otherwise be caught here."""
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)

    run_ticker(plugin, passes=1)

    keepalive_message = next(m for m in sent if m["type"] == "keepalive")
    assert keepalive_message["data"] == {}


def test_empty_client_set_means_nothing_broadcast(wired_plugin):
    """With nobody in `_clients` the tick has nobody to send to, so it sends
    nothing at all - not a `stats` with an empty recipient list, not a
    `keepalive` either. This is the plugin-side mechanism; which sockets end
    up in `_clients` in the first place (only authenticated ones) is
    `tests/test_integration_ws.py`'s contract, not exercised here."""
    plugin, agent, sent = wired_plugin

    run_ticker(plugin, passes=3)

    assert sent == []


def test_broadcast_reaches_every_admitted_client(wired_plugin):
    """Fan-out through the real `Companion.broadcast()`, not the
    list-recording double every other test here installs: two admitted
    clients, both must receive both frames from the same tick.
    """
    plugin, agent, sent = wired_plugin
    # Route through the genuine method: the recording override the fixture
    # installs is only good for reading "was this built", never "did the
    # real fan-out deliver it".
    plugin.broadcast = companion.Companion.broadcast.__get__(plugin)
    plugin._listeners._ensure_loop()
    clients = [RecordingClient(), RecordingClient()]
    for client in clients:
        plugin._clients.add(client)

    run_ticker(plugin, passes=1)

    for client in clients:
        client.wait_for_count(2)
        types = {json.loads(payload)["type"] for payload in client.received}
        assert types == {"stats", "keepalive"}, (
            f"client received {types}, expected both frames from the tick"
        )


# ---------------------------------------------------------------------------
# A stats failure must not cost that tick's keepalive (SPEC 10.2 test row)
# ---------------------------------------------------------------------------


class ExplodingRouter:
    """A `Router` double whose `stats()` always raises."""

    def stats(self) -> dict:
        raise RuntimeError("bettercap payload could not be built")


def test_a_stats_failure_does_not_cost_the_ticks_keepalive(wired_plugin, caplog):
    """`Router.stats()` is not total - `int(pwnagotchi.uptime())` and
    `SessionCache.age` are unguarded - so a broken payload must not also
    silence the one frame an idle client relies on to tell a quiet plugin
    from a dead link.
    """
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)
    plugin._router = ExplodingRouter()

    with caplog.at_level("ERROR"):
        run_ticker(plugin, passes=1)

    types = [message["type"] for message in sent]
    assert types == ["keepalive"], (
        "a stats failure must not also cost that tick's keepalive "
        f"(sent: {types})"
    )
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "a failed stats build must be logged, not swallowed in silence"
    )


def test_a_keepalive_failure_does_not_cost_the_ticks_stats(wired_plugin, check_message):
    """The other direction of the same rule: `keepalive` and `stats` are sent
    from two independent `try` blocks, so a keepalive send failing must not
    also take that tick's stats down with it."""
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)
    record = plugin.broadcast  # the sent.append double the fixture installed

    def flaky(message):
        if message["type"] == "keepalive":
            raise RuntimeError("the socket went away mid keepalive")
        record(message)

    plugin.broadcast = flaky

    run_ticker(plugin, passes=1)

    types = [message["type"] for message in sent]
    assert types == ["stats"], (
        f"a keepalive failure must not cost that tick's stats (sent: {types})"
    )
    check_message(sent[0], "stats")


# ---------------------------------------------------------------------------
# The blocking-call rule (SPEC 2.5, F7)
# ---------------------------------------------------------------------------


def test_the_ticker_never_calls_agent_session(wired_plugin):
    """`agent.session()` is a synchronous HTTP GET to bettercap with a 30 s
    timeout; calling it from the ticker would stall every connected client for
    up to 30 seconds on every tick. `stats` must come entirely from the
    cache."""
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)
    # Baseline rather than an assumed zero: on_loaded's first background pass
    # (SPEC 2.5, issue #140) may already have refreshed the cache once by the
    # time this runs, and the claim under test is that the ticker itself
    # adds none on top of whatever that baseline is.
    before = agent.session_calls

    run_ticker(plugin, passes=5)

    assert len(sent) == 10, "expected 5 stats and 5 keepalive frames"
    assert agent.session_calls == before, (
        "the stats ticker called agent.session(), which blocks the request "
        "path for up to 30 seconds (SPEC 2.5, F7)"
    )


# ---------------------------------------------------------------------------
# Spacing is keepalive_interval, and nothing else
# ---------------------------------------------------------------------------


def test_the_spacing_is_exactly_keepalive_interval(wired_plugin_factory):
    """Before #65 the broadcast rode a loop waking at
    min(session_poll_interval, keepalive_interval), so a 5 s poll turned a
    nominal 20 s cadence into 20-25 s. On its own ticker the wait between
    passes must be keepalive_interval and nothing else - not the poll
    interval, not a minimum of the two.

    `keepalive_interval` is clamped in `on_loaded` now, exactly like
    `session_poll_interval`, so the override has to be given to the factory
    before it runs rather than poked into `plugin.options` afterwards."""
    plugin, agent, sent = wired_plugin_factory(
        {"keepalive_interval": 9, "session_poll_interval": 2}
    )
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=3)

    assert stop.waits == [9.0, 9.0, 9.0]
    assert len(sent) == 6


# ---------------------------------------------------------------------------
# keepalive_interval clamp: 5-20, logged, 0 -> default rather than floor
# ---------------------------------------------------------------------------


def test_keepalive_interval_below_the_floor_is_clamped_to_5(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": 2})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [5.0]
    assert any(
        "keepalive_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    ), "the clamp must be logged, not applied silently"


def test_keepalive_interval_above_the_ceiling_is_clamped_to_20(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": 3600})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [20.0]
    assert any(
        "keepalive_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )


def test_keepalive_interval_zero_takes_the_default_not_the_floor(wired_plugin_factory, caplog):
    """The one case three drafts of SPEC 2.4 got wrong on paper: `0` used to
    disable the broadcast outright, and the floor of the new clamp is 5, but
    `0` must land on the default of 20 - not on 5, which would turn a
    deliberately silenced unit into one broadcasting a full payload every five
    seconds over BT PAN."""
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": 0})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [float(companion.DEFAULTS["keepalive_interval"])]
    assert stop.waits == [20.0]


def test_keepalive_interval_inside_the_range_is_left_alone(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": 12})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [12.0]
    assert not any(
        "keepalive_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    ), "a value already inside 5-20 must not be reported as clamped"


def test_keepalive_interval_non_numeric_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog
):
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": "20s"})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [float(companion.DEFAULTS["keepalive_interval"])]
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "keepalive_interval" in r.getMessage() and "20s" in r.getMessage()
        for r in warnings
    ), "a non-numeric value must be reported at WARNING, distinct from the INFO clamp lines"


@pytest.mark.parametrize(
    "bad_value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
def test_keepalive_interval_non_finite_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog, bad_value
):
    """The worst bug the review found: `min(max(nan, low), high)` is `nan`,
    and `threading.Event.wait(nan)` returns immediately, so a bare `nan` -
    which TOML allows as a literal - used to spin the ticker at full CPU
    instead of ever sleeping. `inf`/`-inf` are rejected for the same reason
    `_finite()` already rejects them elsewhere on the wire."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": bad_value})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [float(companion.DEFAULTS["keepalive_interval"])]
    assert any(
        r.levelname == "WARNING" and "keepalive_interval" in r.getMessage()
        for r in caplog.records
    )


def test_keepalive_interval_true_falls_back_to_default_and_warns(wired_plugin_factory, caplog):
    """`float(True)` is `1.0`, a value `clamp_interval` would otherwise accept
    and clamp to the floor of 5, reporting it at INFO as an out-of-range
    number rather than at WARNING as the type mistake it is. `bool` is
    rejected before it ever reaches `float()` for exactly this reason."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"keepalive_interval": True})
    admit_a_client(plugin)

    stop = run_ticker(plugin, passes=1)

    assert stop.waits == [float(companion.DEFAULTS["keepalive_interval"])]
    assert any(
        r.levelname == "WARNING" and "keepalive_interval" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# session_poll_interval: clamped once in on_loaded, read by the loop that uses it
# ---------------------------------------------------------------------------


def test_session_poll_interval_below_the_floor_is_clamped_once_and_read_from_self(
    wired_plugin_factory, caplog
):
    """`session_poll_interval` is clamped exactly once, in `on_loaded`, and
    stored on `Companion._session_poll_interval`. Mutating `options` after
    `on_loaded` must be inert: `_background` reads the stored attribute,
    never `self.options`, again.

    There is one consumer of the clamped value, not two. `SessionCache` used
    to be handed it as well, and stored it without ever reading it, so an
    assertion about it showed two variables holding the same float rather
    than one clamped value reaching two places. The field and the argument
    are gone (#106) and the cache has no cadence of its own: `refresh()`
    fetches whenever it is called. What remains here is the observable half,
    the loop period `_background` actually waits on.
    """
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": 0.1, "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == 1.0
    assert any(
        "session_poll_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )

    plugin.options = {**plugin.options, "session_poll_interval": 4}
    stop = run_background(plugin, passes=1)
    assert stop.waits == [1.0], (
        "on_loaded already clamped this; options changed afterwards must not matter"
    )


def test_session_poll_interval_above_the_ceiling_is_clamped_once_and_read_from_self(
    wired_plugin_factory, caplog
):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": 3600, "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == 5.0
    assert any(
        "session_poll_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )


def test_session_poll_interval_inside_the_range_is_left_alone(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": 3, "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == 3.0
    assert not any(
        "session_poll_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )


def test_session_poll_interval_non_numeric_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog
):
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": "5s", "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == float(companion.DEFAULTS["session_poll_interval"])
    assert any(
        r.levelname == "WARNING" and "session_poll_interval" in r.getMessage()
        for r in caplog.records
    )


def test_session_poll_interval_nan_falls_back_to_default_and_warns(wired_plugin_factory, caplog):
    """The same spin risk as `keepalive_interval`'s: `_background` waits on
    `self._session_poll_interval` too, so a `nan` here is just as dangerous."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": float("nan"), "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == float(companion.DEFAULTS["session_poll_interval"])
    stop = run_background(plugin, passes=1)
    assert stop.waits == [float(companion.DEFAULTS["session_poll_interval"])]
    assert any(
        r.levelname == "WARNING" and "session_poll_interval" in r.getMessage()
        for r in caplog.records
    )


def test_session_poll_interval_true_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog
):
    """The same `float(True) == 1.0` trap as `keepalive_interval`'s: `1.0` is
    inside 1-5 and would otherwise be accepted outright, silently taking a
    boolean typo as a deliberate one-second poll."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory(
            {"session_poll_interval": True, "rebind_interval": 300}
        )

    assert plugin._session_poll_interval == float(companion.DEFAULTS["session_poll_interval"])
    stop = run_background(plugin, passes=1)
    assert stop.waits == [float(companion.DEFAULTS["session_poll_interval"])]
    assert any(
        r.levelname == "WARNING" and "session_poll_interval" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# rebind_interval: clamped 5-300, 0 -> floor (not default), thread survives
# a non-numeric value (issue #105)
# ---------------------------------------------------------------------------


def test_rebind_interval_below_the_floor_is_clamped_to_5(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": 2})

    assert plugin._rebind_interval == 5.0
    assert any(
        "rebind_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    ), "the clamp must be logged, not applied silently"


def test_rebind_interval_above_the_ceiling_is_clamped_to_300(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": 5000})

    assert plugin._rebind_interval == 300.0
    assert any(
        "rebind_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )


def test_rebind_interval_zero_takes_the_floor_not_the_default(wired_plugin_factory, caplog):
    """The deliberate opposite of `keepalive_interval = 0`
    (`test_keepalive_interval_zero_takes_the_default_not_the_floor` above):
    that key's `0` used to mean "disable" and now lands on the default of 20.
    This key's `0` never meant disable, only "reconcile on every pass", so it
    lands on the floor of 5 instead - a test that only checked the value
    differs from `0` would pass just as well against a plugin that mistakenly
    fell back to the default 30, which is why the floor is asserted by its
    exact number and the default is ruled out explicitly."""
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": 0})

    assert plugin._rebind_interval == 5.0
    assert plugin._rebind_interval != float(companion.DEFAULTS["rebind_interval"])
    assert any(
        "rebind_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    )


def test_rebind_interval_inside_the_range_is_left_alone(wired_plugin_factory, caplog):
    with caplog.at_level("INFO"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": 45})

    assert plugin._rebind_interval == 45.0
    assert not any(
        "rebind_interval" in r.getMessage() and "clamp" in r.getMessage()
        for r in caplog.records
    ), "a value already inside 5-300 must not be reported as clamped"


def test_rebind_interval_non_numeric_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog
):
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": "30s"})

    assert plugin._rebind_interval == float(companion.DEFAULTS["rebind_interval"])
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "rebind_interval" in r.getMessage() and "30s" in r.getMessage() for r in warnings
    ), "a non-numeric value must be reported at WARNING, distinct from the INFO clamp lines"


@pytest.mark.parametrize(
    "bad_value", [float("nan"), float("inf"), float("-inf")], ids=["nan", "inf", "-inf"]
)
def test_rebind_interval_non_finite_falls_back_to_default_and_warns(
    wired_plugin_factory, caplog, bad_value
):
    """`nan` is the case the issue names specifically: `now >= next_rebind`
    is `False` for every comparison against `nan`, so a bare `nan` - a legal
    TOML float literal - used to pin the reconcile silently after the first
    pass and never run it again."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": bad_value})

    assert plugin._rebind_interval == float(companion.DEFAULTS["rebind_interval"])
    assert any(
        r.levelname == "WARNING" and "rebind_interval" in r.getMessage()
        for r in caplog.records
    )


def test_rebind_interval_true_falls_back_to_default_and_warns(wired_plugin_factory, caplog):
    """`float(True)` is `1.0`, which is out of range and would otherwise be
    clamped to the floor and logged at INFO as a number, rather than at
    WARNING as the type mistake it is."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory({"rebind_interval": True})

    assert plugin._rebind_interval == float(companion.DEFAULTS["rebind_interval"])
    assert any(
        r.levelname == "WARNING" and "rebind_interval" in r.getMessage()
        for r in caplog.records
    )


class ClockAdvancingStop(SteppingStop):
    """A `SteppingStop` that also advances `harness`'s fake clock by every
    wait it records.

    `next_rebind` inside `_background` is a local, reset to `0.0` on every
    call, so two separate `run_background` calls cannot show spacing - each
    one's first pass always reconciles regardless of the interval. Only a
    double that lets the clock move *between* passes of one call can show
    whether the second reconcile lands where the clamped interval says it
    should, which is what proves `_background` is reading the clamped
    attribute and not `self.options` on every pass.
    """

    def __init__(self, passes: int, harness) -> None:
        super().__init__(passes)
        self._harness = harness

    def wait(self, timeout: float | None = None) -> bool:
        result = super().wait(timeout)
        self._harness.advance(timeout)
        return result


def test_rebind_interval_clamped_once_and_read_from_self(wired_plugin_factory, harness):
    """Mirrors the `session_poll_interval` shape (test above), the honest
    way: it drives `_background` and asserts on when the reconcile actually
    runs, not on an attribute nothing in the test writes to.

    `rebind_interval` does not govern `stop.waits` the way
    `session_poll_interval` does - it governs *when the reconcile fires*,
    so the observable here is the number of `list_local_ipv4` calls across
    two passes with the clock advanced by `session_poll_interval` (5 s,
    the fixture default) between them. Pass 1 always reconciles (`next_rebind`
    starts at `0.0`), scheduling the next one at `+5` if `_background` reads
    the clamped `self._rebind_interval`, or at `+250` if it read
    `self.options["rebind_interval"]` instead - the value mutated below,
    which on_loaded already clamped away and must stay inert. At exactly
    5 s elapsed, only the clamped reading brings the second pass due.
    """
    plugin, agent, sent = wired_plugin_factory({"rebind_interval": 2})
    assert plugin._rebind_interval == 5.0

    plugin.options = {**plugin.options, "rebind_interval": 250}

    # on_loaded already performed one eager reconcile of its own before
    # spawning anything, so the count below is measured as a delta across
    # the two manual passes rather than as an absolute total.
    before = harness.names().count("list_local_ipv4")
    stop = ClockAdvancingStop(passes=2, harness=harness)
    plugin._stop = stop
    plugin._background()
    after = harness.names().count("list_local_ipv4")

    assert after - before == 2, (
        "the second pass must reconcile at the clamped 5 s mark, not sit "
        "waiting for the mutated 250 s that on_loaded already discarded"
    )


def test_rebind_interval_non_numeric_does_not_kill_the_background_thread(
    wired_plugin_factory, harness, caplog
):
    """The actual defect #105 fixes, and the one assertion in this file that
    would have failed against the code the issue describes. Before the fix,
    `float("30s")` ran at thread entry, outside the loop's own `try`, and
    raised before the first pass ever completed - killing the thread that
    also refreshes the session and polls gpsd, for the life of the process.
    A test that only checks `plugin._rebind_interval == 30.0` passes whether
    or not the thread that reads it is still alive, since the clamp itself
    runs in `on_loaded`, not in `_background`. Only driving the loop and
    observing all three of its jobs - the wait completing, the session
    refreshed, gpsd polled, the address list enumerated - proves survival."""
    with caplog.at_level("WARNING"):
        plugin, agent, sent = wired_plugin_factory(
            {"rebind_interval": "30s", "gps_source": "gpsd"}
        )

    stop = run_background(plugin, passes=2)

    assert len(stop.waits) == 2, "the background thread died instead of completing both passes"
    assert agent.session_calls > 0, "the session refresh must still run"
    assert "read_gpsd" in harness.names(), "the gpsd poll must still run"
    assert "list_local_ipv4" in harness.names(), "the listener reconcile must still run"


# ---------------------------------------------------------------------------
# _background no longer carries the broadcast (the other half of #65)
# ---------------------------------------------------------------------------


def test_the_background_loop_broadcasts_nothing(wired_plugin):
    """The point of #65: the periodic stats/keepalive broadcast moves off
    this loop entirely. Even with a client admitted and a short poll
    interval, `_background` must produce no broadcast of any kind - that is
    now `_stats_ticker`'s job alone."""
    plugin, agent, sent = wired_plugin
    admit_a_client(plugin)
    plugin.options = {
        **plugin.options,
        "session_poll_interval": 1,
        "rebind_interval": 300,
    }

    run_background(plugin, passes=5)

    assert sent == []


# ---------------------------------------------------------------------------
# The outer guard: a fault outside either inner send must not end the thread
# ---------------------------------------------------------------------------


class ExplodingClientSet:
    """A `ClientSet` double whose `__len__` raises.

    `len(self._clients)` is the first thing `_stats_ticker`'s loop body does
    and sits outside both inner `try` blocks, which makes it the honest place
    to prove the outer guard: without it, a fault here - or in `deps.now()`,
    reached the same way - ends the thread silently and the ticker never
    broadcasts again, on a device nobody is watching.
    """

    def __len__(self) -> int:
        raise RuntimeError("client set is corrupted")


def test_a_fault_outside_the_inner_sends_does_not_kill_the_ticker(wired_plugin, caplog):
    plugin, agent, sent = wired_plugin
    plugin._clients = ExplodingClientSet()

    with caplog.at_level("ERROR"):
        stop = run_ticker(plugin, passes=3)

    assert len(stop.waits) == 3, "the ticker stopped instead of completing every pass"
    assert sent == []
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "a fault outside the inner sends must be logged, not swallowed in silence"
    )


# ---------------------------------------------------------------------------
# Shutdown is real, not just SteppingStop-shaped
# ---------------------------------------------------------------------------


def test_the_ticker_exits_promptly_when_stopped(wired_plugin_factory):
    """Every other test in this file drives `_stats_ticker` with
    `SteppingStop`, which has the exact semantics the shutdown path depends
    on - `Event.wait(timeout)` returning as soon as `set()` is called, not
    only once the timeout elapses. That double is not itself proof the real
    `threading.Event` behaves the same way, so this one test runs the ticker
    on a real thread against a real `Event`, with the actual 5 s floor in
    play, and closes it immediately rather than waiting out a tick."""
    plugin, agent, sent = wired_plugin_factory({"keepalive_interval": 5})
    plugin._stop = threading.Event()

    thread = threading.Thread(target=plugin._stats_ticker, daemon=True)
    thread.start()
    plugin._stop.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "the ticker ignored the stop event"


# ---------------------------------------------------------------------------
# on_loaded starts the loops; on_ready only captures the agent (issue #140)
# ---------------------------------------------------------------------------
#
# In manual mode pwnagotchi never calls `on_ready` at all (SPEC 2.5). Before
# the fix, both `_background` and `_stats_ticker` were spawned from
# `on_ready`, so a unit that booted in manual sat inert for its whole
# session: no session refresh, no gpsd poll, and - the part that matters
# most, because it is silent rather than merely absent - no reconcile pass,
# so a tether that came up after boot was never noticed and never bound.
# These tests never call `on_ready` at all, the manual-mode shape exactly.


def is_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((host, port)) == 0


@pytest.fixture
def agentless_plugin_factory(options, harness, tls_material, tmp_path):
    """Like `wired_plugin_factory`, but `on_ready` is never called.

    No agent is ever supplied, at any point - the manual-mode shape issue
    #140 is about, not merely the brief pre-`on_ready` window auto mode also
    has. `harness.deps.spawn` still only records; nothing here starts a real
    thread unless a test drives one by hand.
    """
    built: list[companion.Companion] = []

    def _make(overrides: dict | None = None):
        plugin = companion.Companion()
        plugin.deps = harness.deps
        plugin.options = {
            **options,
            "tls_cert": str(tls_material["cert"]),
            "tls_key": str(tls_material["key"]),
            "web_root": str(tmp_path),
            **(overrides or {}),
        }
        plugin.on_loaded()
        assert plugin._router is not None, "the fixture did not produce a wired plugin"

        sent: list[dict] = []
        plugin.broadcast = sent.append

        built.append(plugin)
        return plugin, sent

    yield _make

    for plugin in built:
        plugin.on_unload()


def test_on_loaded_alone_spawns_both_loops_with_no_agent_ever_supplied(
    agentless_plugin_factory, harness
):
    """`deps.spawn` is the only seam a background loop can start through
    (SPEC 10.7), and `DepsHarness` is built expressly to let a test assert
    the ordering a protocol requires (conftest.py). Both loops spawned with
    `on_ready` never having been called at all is that ordering claim: SPEC
    2.5 says both the background pass and the stats ticker now start in
    `on_loaded`, and `on_ready` captures the agent and nothing else.

    Asserted by identity, not by count: a bare `len(...) >= 2` would still
    pass if `on_loaded` spawned two copies of one loop and never the other,
    or would stop meaning anything the day a third, unrelated spawn joins
    it."""
    plugin, sent = agentless_plugin_factory()

    assert plugin._background in harness.spawned, (
        "on_loaded must spawn the background pass without on_ready ever "
        "being called"
    )
    assert plugin._stats_ticker in harness.spawned, (
        "on_loaded must spawn the stats ticker without on_ready ever "
        "being called"
    )


def test_a_driven_background_pass_binds_a_tether_with_no_agent(
    options, harness, tls_material, tmp_path, free_ports
):
    """`_background`'s own tolerance of a missing agent, in isolation:
    driven by hand with `SteppingStop` (the fast, deterministic technique
    every other test in this file uses), on a plugin `on_ready` was never
    called on. This says the loop body itself does not need an agent to
    reconcile; it says nothing about who schedules that body, which is a
    separate claim the next test makes with a real thread, because
    `run_background` calls `_background()` directly and would pass exactly
    the same way if `on_loaded` never spawned it at all.
    """
    ws_port, http_port = free_ports
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **options,
        "bind_addresses": ["127.0.0.1"],
        "ws_port": ws_port,
        "http_port": http_port,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }
    harness.local_ipv4 = []

    plugin.on_loaded()

    try:
        assert not is_listening("127.0.0.1", ws_port), (
            "nothing local matched bind_addresses yet; a bind here is the "
            "control failing, not the tether test"
        )
        assert not is_listening("127.0.0.1", http_port)

        harness.local_ipv4 = ["127.0.0.1"]
        run_background(plugin, passes=1)

        assert is_listening("127.0.0.1", ws_port)
        assert is_listening("127.0.0.1", http_port)
    finally:
        plugin.on_unload()


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_on_loaded_itself_schedules_the_reconcile_that_binds_a_tether(
    options, tls_material, tmp_path, free_ports
):
    """The claim the isolated test above cannot make: that `on_loaded` is
    what starts the loop doing this, with `on_ready` never called at all -
    the manual-mode shape issue #140 is about. `run_background`/`SteppingStop`
    bypass `deps.spawn` entirely and would pass this same way even if
    `on_loaded` scheduled nothing, so this one uses a real `Deps()` and a
    real background thread instead: `on_loaded` is the only call made, the
    tether address is made to "come up" by mutating what `list_local_ipv4`
    returns after the fact, and the assertion waits on the real kernel
    socket rather than driving the loop body by hand.

    This is the test the mutation described in the test-author's report -
    moving the two `deps.spawn` calls from `on_loaded` back into `on_ready` -
    is expected to kill: with no `on_ready` call anywhere in this test,
    nothing would ever spawn `_background`, and the socket would never open
    inside the timeout.

    Before issue #105, `rebind_interval = 0` forced a reconcile on every
    pass here, which this test relied on to make the second pass (the one
    that actually finds the address) arrive promptly. The clamp now refuses
    `0` outright - it lands on the floor of 5, not on "every pass" - so
    SPEC 2.4's own prescribed fix is used instead: the reconcile is driven
    by `self.deps.now()`, so `now()` is replaced with a hand-controlled
    clock and advanced past the floor once the address is in place, rather
    than asking for an interval the plugin would no longer honour.
    """
    ws_port, http_port = free_ports
    box = {"addrs": []}
    clock = {"t": 1_755_264_000.0}
    clock_lock = threading.Lock()

    def controlled_now() -> float:
        with clock_lock:
            return clock["t"]

    real_deps = companion.Deps(
        now=controlled_now, list_local_ipv4=lambda: list(box["addrs"])
    )
    plugin = companion.Companion()
    plugin.deps = real_deps
    plugin.options = {
        **options,
        "bind_addresses": ["127.0.0.1"],
        "ws_port": ws_port,
        "http_port": http_port,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
        # Short enough that the test does not have to wait out the 1-5 s
        # clamp floor several times over, long enough that the first pass
        # (still finding nothing) is clearly over before the address
        # appears. rebind_interval is left at the 5 s floor, in range, so
        # nothing here depends on the clamp correcting it.
        "session_poll_interval": 1,
        "rebind_interval": 5,
        # This test is about the reconcile, and everything else in it being
        # real is deliberate - except gpsd. With the option default of
        # "auto", every background pass would open a real TCP connection to
        # gpsd_host:gpsd_port (127.0.0.1:2947 by default), which is refused
        # at once only because nothing is listening on this host; on a host
        # running gpsd, or a runner that blackholes rather than refuses, that
        # 2 s connect timeout eats the 5 s wait_until budget below. "none"
        # keeps this a test of the tether, not of the network it happens to
        # run on.
        "gps_source": "none",
    }

    plugin.on_loaded()  # on_ready is never called anywhere in this test.

    try:
        assert not is_listening("127.0.0.1", ws_port)
        assert not is_listening("127.0.0.1", http_port)

        box["addrs"] = ["127.0.0.1"]
        with clock_lock:
            clock["t"] += 10.0  # past the 5 s floor: the next wake reconciles

        assert wait_until(lambda: is_listening("127.0.0.1", ws_port), timeout=5.0), (
            "on_loaded must itself schedule the pass that reconciles a "
            "tether coming up, with no on_ready call anywhere in sight "
            "(SPEC 2.5, issue #140)"
        )
        assert wait_until(lambda: is_listening("127.0.0.1", http_port), timeout=5.0)
    finally:
        plugin.on_unload()


def test_the_stats_ticker_still_broadcasts_with_no_agent_and_mode_is_null(
    agentless_plugin_factory, check_message
):
    """The second loop SPEC 2.5 names: `_stats_ticker` must not sit waiting
    for an agent either. A `stats` still goes out on the tick, and its
    `mode` is `None` rather than the plugin refusing to build the payload at
    all - the same "say so rather than guessing" rule `get_stats` follows."""
    plugin, sent = agentless_plugin_factory()
    admit_a_client(plugin)

    run_ticker(plugin, passes=1)

    types = sorted(message["type"] for message in sent)
    assert types == ["keepalive", "stats"], sent
    stats_message = next(m for m in sent if m["type"] == "stats")
    data = check_message(stats_message, "stats")
    assert data["mode"] is None
