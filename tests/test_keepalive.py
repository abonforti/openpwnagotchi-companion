"""The unsolicited heartbeat, SPEC 2.4.

`keepalive` exists for a failure that does not look like one: over BT PAN a
dropped tether does not close the socket, it stops delivering. Without a frame
arriving on a known cadence the app cannot tell a quiet plugin from a dead
link, and sits on a dead connection showing stale data.

Issue #65 moved this broadcast off `Companion._background` onto a ticker of
its own, `Companion._stats_ticker`, which carries `stats` alongside it on
every tick (SPEC 4.3.7). `_background` no longer broadcasts anything - see
`test_broadcast_cadence.py::test_the_background_loop_broadcasts_nothing` -
and the cadence, the 5-20 s clamp and the stats/keepalive pairing all belong
to that file now. What is left here is specific to the `keepalive` frame
itself: its shape, and that one broadcast failure does not take the ticker
down with it.

Driven with `SteppingStop`, a `threading.Event` double whose `wait(timeout)`
records the timeout and returns immediately: the clamped floor is 5 s, so a
real-clock test would have to sleep for it, which this suite cannot afford
(SPEC 10.7, the development host is small). `test_broadcast_cadence.py` uses
the same double; it is duplicated here rather than imported so this file does
not depend on another test module's internals.
"""

from __future__ import annotations

import pytest

import plugin.companion as companion


class SteppingStop:
    """A `threading.Event` double that ends the ticker loop after `passes`
    loop bodies, recording the timeout each `wait()` call was given."""

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
    stop = SteppingStop(passes)
    plugin._stop = stop
    plugin._stats_ticker()
    return stop


class DummyRouter:
    """Just enough for `_stats_ticker` to build a `stats` envelope.

    The payload's own shape - what `Router.stats()` puts inside `data` - is
    `test_broadcast_cadence.py`'s concern, exercised there against the real
    `Router` and the schema. This file only needs the ticker to have
    something to broadcast.
    """

    def stats(self) -> dict:
        return {}


@pytest.fixture
def ticking_plugin():
    """A bare `Companion`, with one client admitted so the ticker has
    something to send to, and a `DummyRouter` standing in for the real one."""
    plugin = companion.Companion()
    plugin._router = DummyRouter()
    plugin._clients.add(object())
    return plugin


def test_keepalive_carries_no_payload_and_no_message_id(ticking_plugin):
    plugin = ticking_plugin
    sent: list[dict] = []
    plugin.broadcast = sent.append

    run_ticker(plugin, passes=1)

    keepalive = next(m for m in sent if m["type"] == "keepalive")
    assert keepalive["data"] == {}
    assert isinstance(keepalive["timestamp"], float)
    assert "message_id" not in keepalive


def test_a_broadcast_failure_does_not_kill_the_ticker(ticking_plugin):
    """One bad pass must not stop the ticker for good, and must not cost the
    pass its other frame either.

    Both `keepalive` and `stats` must be attempted on every tick, even though
    every send raises here - which is only true if each has its own `try`
    (SPEC 10.2, test_broadcast_cadence.py's row). A single `try` wrapping
    both would let the first failure skip the second send, and this test
    would keep passing with `len(calls) >= 3` alone regardless: what makes it
    bite is checking *what* was attempted on every pass, not just that
    something was.

    It also matters for its own sake: a ticker that dies on the first failed
    send leaves a client reading a `sessionAge` that never gets refreshed
    again - the exact failure `degraded` (SPEC 4.3.1) exists to report, made
    permanent by the reporting mechanism itself.
    """
    plugin = ticking_plugin
    calls: list[str] = []

    def explode(message):
        calls.append(message["type"])
        raise RuntimeError("the socket went away mid-fan-out")

    plugin.broadcast = explode

    stop = run_ticker(plugin, passes=3)

    assert len(stop.waits) == 3, "the ticker stopped before completing every pass"
    assert calls == ["keepalive", "stats"] * 3, (
        f"expected both keepalive and stats attempted on every pass, got {calls}"
    )

