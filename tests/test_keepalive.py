"""The unsolicited heartbeat, SPEC 2.4.

`keepalive` exists for a failure that does not look like one: over BT PAN a
dropped tether does not close the socket, it stops delivering. Without a frame
arriving on a known cadence the app cannot tell a quiet plugin from a dead link,
and sits on a dead connection showing stale data.

These drive `Companion._background` directly with a real clock and a very short
interval. The alternative - a fake clock - does not work here, because the loop
sleeps on a `threading.Event` between passes and no amount of advancing a fake
`now()` makes that sleep return.
"""

from __future__ import annotations

import threading
import time

import pytest

import plugin.companion as companion


def run_background(plugin, seconds: float) -> None:
    """Runs the background loop for a while, then stops it and waits."""
    thread = threading.Thread(target=plugin._background, daemon=True)
    thread.start()
    try:
        time.sleep(seconds)
    finally:
        plugin._stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive(), "the background loop ignored the stop event"


@pytest.fixture
def plugin_with_recorded_broadcasts():
    """A `Companion` whose background loop does nothing slow and records its output.

    `_session`, `_gps` and `_listeners` stay None so the loop exercises only the
    keepalive branch: the refresh and rebind paths have their own tests, and
    leaving them live here would make this test depend on a bettercap fake.
    """
    plugin = companion.Companion()
    sent: list[dict] = []
    plugin.broadcast = sent.append
    return plugin, sent


def test_keepalive_goes_out_on_its_own(plugin_with_recorded_broadcasts):
    plugin, sent = plugin_with_recorded_broadcasts
    plugin.options = {
        **companion.DEFAULTS,
        "session_poll_interval": 5,
        "keepalive_interval": 0.05,
        "rebind_interval": 3600,
    }

    run_background(plugin, 0.3)

    keepalives = [message for message in sent if message["type"] == "keepalive"]
    assert keepalives, "no keepalive was broadcast"
    for message in keepalives:
        assert message["data"] == {}
        assert isinstance(message["timestamp"], float)
        assert "message_id" not in message


def test_the_loop_wakes_often_enough_for_the_interval(
    plugin_with_recorded_broadcasts,
):
    """The cadence is a bound, not a suggestion.

    A keepalive can only go out on a tick of the background loop. If the loop
    slept on `session_poll_interval` alone, a 0.05 s keepalive behind a 5 s poll
    would arrive once and then not again for five seconds, and a client whose
    timeout is set to the nominal interval would reconnect for no reason.
    """
    plugin, sent = plugin_with_recorded_broadcasts
    plugin.options = {
        **companion.DEFAULTS,
        "session_poll_interval": 5,
        "keepalive_interval": 0.05,
        "rebind_interval": 3600,
    }

    run_background(plugin, 0.3)

    keepalives = [message for message in sent if message["type"] == "keepalive"]
    assert len(keepalives) >= 3, f"only {len(keepalives)} in 0.3 s at a 0.05 s interval"


def test_the_first_keepalive_waits_a_full_interval(plugin_with_recorded_broadcasts):
    """The first pass runs before any client can have connected.

    Firing immediately would spend a broadcast on an empty set and then leave
    the first keepalive a client could actually receive one interval away.
    """
    plugin, sent = plugin_with_recorded_broadcasts
    plugin.options = {
        **companion.DEFAULTS,
        "session_poll_interval": 5,
        "keepalive_interval": 3600,
        "rebind_interval": 3600,
    }

    run_background(plugin, 0.2)

    assert [message for message in sent if message["type"] == "keepalive"] == []


def test_zero_disables_the_keepalive(plugin_with_recorded_broadcasts):
    plugin, sent = plugin_with_recorded_broadcasts
    plugin.options = {
        **companion.DEFAULTS,
        "session_poll_interval": 0.01,
        "keepalive_interval": 0,
        "rebind_interval": 3600,
    }

    run_background(plugin, 0.2)

    assert [message for message in sent if message["type"] == "keepalive"] == []


def test_a_broadcast_failure_does_not_kill_the_loop(plugin_with_recorded_broadcasts):
    """One bad pass must not stop the session refresh and the rebind for good.

    The rebind loop is what recovers the listeners when the tether comes back,
    so a background thread that dies takes the plugin off the air until the
    unit is restarted, with nothing in the log tying it to the keepalive.
    """
    plugin, _ = plugin_with_recorded_broadcasts
    calls: list[int] = []

    def explode(message):
        calls.append(1)
        raise RuntimeError("the socket went away mid-fan-out")

    plugin.broadcast = explode
    plugin.options = {
        **companion.DEFAULTS,
        "session_poll_interval": 5,
        "keepalive_interval": 0.05,
        "rebind_interval": 3600,
    }

    run_background(plugin, 0.3)

    assert len(calls) >= 3, "the loop stopped at the first failed broadcast"
