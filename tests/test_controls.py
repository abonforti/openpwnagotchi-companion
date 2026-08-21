"""The four controls.

`set_mode` is the one this file exists for. Upstream `pwnios.py` assigns
`agent.mode` and assumes the main loop reacts. It does not: the mode is chosen at
process start from the `--manual` flag, and `agent.mode` is only a label set
inside `do_manual_mode()` / `do_auto_mode()` (SPEC F10, D12). Writing it changes
the number the app displays and nothing about what the unit does - a bug that
looks like it works.

The real switch is `pwnagotchi.restart("AUTO"|"MANU")`, which restarts both
services and takes this plugin down with them. So the ordering is part of the
contract, not an implementation detail: acknowledgment, then the `restarting`
broadcast to every client, then a flush, and only then the effect.
"""

from __future__ import annotations

import pytest

from conftest import FakeAgent
from plugin import companion


class ModeGuard(FakeAgent):
    """A FakeAgent that refuses to have `mode` assigned after construction.

    The prohibition is the point of D12: an implementation that assigns here
    would pass a test asserting on `stats.mode` while doing nothing at all on
    the device.
    """

    _sealed = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sealed = True

    def __setattr__(self, name, value):
        if name == "mode" and getattr(self, "_sealed", False):
            raise AssertionError(
                "set_mode must call pwnagotchi.restart (SPEC D12/F10), not assign agent.mode"
            )
        super().__setattr__(name, value)


class CountingPasv:
    """A pasv_mode stand-in that counts reads of `passive`.

    `plugins.on()` dispatch is asynchronous (SPEC F8): the handler has not run
    when the call returns, so reading `passive` back would report the old value
    and the client would be told the toggle failed.
    """

    def __init__(self, passive=False):
        self._passive = passive
        self.reads = 0

    @property
    def passive(self):
        self.reads += 1
        return self._passive


def types_of(messages):
    return [message["type"] for message in messages]


def only(messages, kind):
    matches = [message for message in messages if message["type"] == kind]
    assert len(matches) == 1, f"expected exactly one {kind}, got {types_of(messages)}"
    return matches[0]


# ---------------------------------------------------------------------------
# set_mode
# ---------------------------------------------------------------------------


def test_switching_to_manual_restarts_into_MANU(router_factory, agent_factory, harness):
    agent = agent_factory(mode="auto")
    router = router_factory(agent)

    router.set_mode("manual")
    harness.run_spawned()

    assert harness.args_for("restart_pwnagotchi") == [("MANU",)]


def test_switching_to_auto_restarts_into_AUTO(router_factory, agent_factory, harness):
    agent = agent_factory(mode="manual")
    router = router_factory(agent)

    router.set_mode("auto")
    harness.run_spawned()

    assert harness.args_for("restart_pwnagotchi") == [("AUTO",)]


def test_the_agent_mode_attribute_is_never_assigned(
    router_factory, harness, handshake_dir, log_file
):
    agent = ModeGuard(
        mode="auto",
        config={
            "bettercap": {"handshakes": str(handshake_dir)},
            "main": {"log": {"path": str(log_file)}},
        },
    )
    router = router_factory(agent)

    router.handle({"type": "set_mode", "mode": "manual", "message_id": "c7f1"}, authenticated=True)
    harness.run_spawned()

    assert agent.mode == "auto"


def test_the_restarting_broadcast_precedes_the_restart(router_factory, agent_factory, harness):
    agent = agent_factory(mode="auto")
    router = router_factory(agent)

    messages = router.handle(
        {"type": "set_mode", "mode": "manual", "message_id": "c7f1"}, authenticated=True
    )

    assert types_of(messages) == ["acknowledgment", "restarting"]
    # Scheduled, not performed: the caller still has to flush the socket.
    assert "spawn" in harness.names()
    assert "restart_pwnagotchi" not in harness.names()

    harness.run_spawned()

    assert "restart_pwnagotchi" in harness.names()


def test_the_acknowledgment_echoes_the_message_id(router_factory, agent_factory):
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle(
        {"type": "set_mode", "mode": "manual", "message_id": "c7f1"}, authenticated=True
    )

    acknowledgment = only(messages, "acknowledgment")
    assert acknowledgment["data"] == {"acknowledged": "set_mode"}
    assert acknowledgment["message_id"] == "c7f1"


def test_the_restarting_payload_names_the_reason_and_the_target_mode(
    router_factory, agent_factory
):
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle({"type": "set_mode", "mode": "manual"}, authenticated=True)

    # The wire value is the Mode enum from common.json - AUTO, PASV or MANUAL.
    # "MANU" is what pwnagotchi.restart() takes, and SPEC 2.6.1 quotes it in the
    # broadcast by mistake; D15 makes the schema authoritative on disagreement.
    assert only(messages, "restarting")["data"] == {"reason": "mode_change", "mode": "MANUAL"}


def test_switching_to_auto_broadcasts_the_auto_mode(router_factory, agent_factory):
    router = router_factory(agent_factory(mode="manual"))

    messages = router.handle({"type": "set_mode", "mode": "auto"}, authenticated=True)

    assert only(messages, "restarting")["data"] == {"reason": "mode_change", "mode": "AUTO"}


def test_asking_for_the_current_mode_is_acknowledged_and_ignored(
    router_factory, agent_factory, harness
):
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle(
        {"type": "set_mode", "mode": "auto", "message_id": "c7f1"}, authenticated=True
    )

    assert types_of(messages) == ["acknowledgment"]
    assert "spawn" not in harness.names()
    harness.run_spawned()
    assert "restart_pwnagotchi" not in harness.names()


def test_asking_for_the_current_mode_while_passive_is_still_a_no_op(
    router_factory, agent_factory, harness, load_pasv
):
    # The unit reports PASV, but the pwnagotchi mode underneath is still auto.
    load_pasv(passive=True)
    router = router_factory(agent_factory(mode="auto"))

    router.handle({"type": "set_mode", "mode": "auto"}, authenticated=True)

    assert "restart_pwnagotchi" not in harness.names()


@pytest.mark.parametrize("mode", ["AUTO", "MANU", "passive", "", None, 1])
def test_an_unknown_mode_is_a_bad_request(router_factory, agent_factory, harness, mode):
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle({"type": "set_mode", "mode": mode}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "bad_request"
    assert "restart_pwnagotchi" not in harness.names()


def test_set_mode_without_a_mode_is_a_bad_request(router, harness):
    messages = router.handle({"type": "set_mode"}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "bad_request"
    assert "spawn" not in harness.names()


# ---------------------------------------------------------------------------
# set_pasv
# ---------------------------------------------------------------------------


def test_pasv_on_emits_the_event(router_factory, agent_factory, stub_plugins, load_pasv):
    load_pasv(passive=False)
    router = router_factory(agent_factory(mode="auto"))

    router.set_pasv(True)

    assert stub_plugins._events() == ["pasv_on"]


def test_pasv_off_emits_the_event(router_factory, agent_factory, stub_plugins, load_pasv):
    load_pasv(passive=True)
    router = router_factory(agent_factory(mode="auto"))

    router.set_pasv(False)

    assert stub_plugins._events() == ["pasv_off"]


def test_pasv_replies_only_with_an_acknowledgment(router_factory, agent_factory, load_pasv):
    load_pasv()
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle(
        {"type": "set_pasv", "on": True, "message_id": "c7f1"}, authenticated=True
    )

    assert types_of(messages) == ["acknowledgment"]
    assert messages[0]["data"] == {"acknowledged": "set_pasv"}


def test_the_new_state_is_not_read_back(router_factory, agent_factory, stub_plugins):
    counter = CountingPasv(passive=False)
    stub_plugins.loaded["pasv_mode"] = counter
    router = router_factory(agent_factory(mode="auto"))

    router.set_pasv(True)

    # plugins.on() is asynchronous; the handler has not run yet, so reading
    # `passive` here could only report the old value.
    assert counter.reads == 0


def test_pasv_without_the_plugin_is_an_error(router_factory, agent_factory, stub_plugins):
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle({"type": "set_pasv", "on": True}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "pasv_unavailable"
    assert stub_plugins._events() == []


@pytest.mark.parametrize("mode", ["manual", "custom"])
def test_pasv_outside_auto_is_an_error(
    router_factory, agent_factory, stub_plugins, load_pasv, mode
):
    load_pasv()
    router = router_factory(agent_factory(mode=mode))

    messages = router.handle({"type": "set_pasv", "on": True}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "pasv_requires_auto"
    assert stub_plugins._events() == []


def test_a_missing_plugin_is_reported_before_the_mode_check(
    router_factory, agent_factory
):
    # Both conditions are wrong at once. The client hides the control entirely on
    # pasv_unavailable and only greys it out on pasv_requires_auto, so which one
    # comes back decides what the user sees.
    router = router_factory(agent_factory(mode="manual"))

    messages = router.handle({"type": "set_pasv", "on": True}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "pasv_unavailable"


def test_set_pasv_without_the_flag_is_a_bad_request(router_factory, agent_factory, load_pasv):
    load_pasv()
    router = router_factory(agent_factory(mode="auto"))

    messages = router.handle({"type": "set_pasv"}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "bad_request"


def test_pasv_mode_is_never_imported():
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("pasv_mode")


# ---------------------------------------------------------------------------
# With no agent (SPEC 2.6.0, issue #147)
#
# A unit in manual mode never hands the plugin an agent at all (F31): `on_ready`
# never fires on that path. The first implementation guarded all three of these
# controls with the same `_require_agent()` check and answered `internal_error`
# with "agent is not ready yet" forever - unavailable exactly on the unit that
# needed them, and `set_mode` is the one that could have rescued it.
#
# `router_factory(None)` is the established stand-in for "no agent" (see
# test_stats.py's issue #140 section and the router_factory docstring); it is
# not a hand-rolled substitute.
# ---------------------------------------------------------------------------


def test_set_mode_to_manual_with_no_agent_is_the_no_op_it_always_was(
    router_factory, harness
):
    # SPEC 2.6.0: with no agent the current mode "is" manual (F31), so a
    # request for manual is a no-op exactly as it is when an agent reports
    # mode == "manual" already.
    router = router_factory(None)

    messages = router.handle(
        {"type": "set_mode", "mode": "manual", "message_id": "c7f1"}, authenticated=True
    )

    assert types_of(messages) == ["acknowledgment"]
    assert "spawn" not in harness.names()
    harness.run_spawned()
    assert "restart_pwnagotchi" not in harness.names()


def test_set_mode_to_auto_with_no_agent_restarts_the_unit_back_to_auto(
    router_factory, harness
):
    # This is the point of issue #147: a unit stuck in manual, with no agent,
    # can be sent back to auto from the app. Before the fix this path answered
    # internal_error and the restart never happened.
    router = router_factory(None)

    messages = router.handle(
        {"type": "set_mode", "mode": "auto", "message_id": "c7f1"}, authenticated=True
    )

    assert types_of(messages) == ["acknowledgment", "restarting"]
    assert only(messages, "restarting")["data"] == {"reason": "mode_change", "mode": "AUTO"}
    # Scheduled, not performed, before the caller flushes the socket - same
    # ordering guarantee as the with-agent case (SPEC 2.6.1), and it must
    # survive with no agent since that is exactly the state a client needs to
    # hear about a restart from.
    assert "spawn" in harness.names()
    assert "restart_pwnagotchi" not in harness.names()

    harness.run_spawned()

    assert harness.args_for("restart_pwnagotchi") == [("AUTO",)]


def test_set_mode_to_manual_with_no_agent_never_restarts_either_direction(
    router_factory, harness
):
    # Guards against a mutant that restarts for both values rather than only
    # for auto: manual must stay a no-op even once anything spawn() recorded
    # has been run.
    router = router_factory(None)

    router.handle({"type": "set_mode", "mode": "manual"}, authenticated=True)
    harness.run_spawned()

    assert "restart_pwnagotchi" not in harness.names()


def test_pasv_with_no_agent_and_the_plugin_loaded_requires_auto(
    router_factory, stub_plugins, load_pasv
):
    # SPEC 2.6.0: with no agent, set_pasv used the agent only to check the mode
    # is auto. With no agent it is not auto, so the contract's existing code
    # applies rather than a new one - and rather than internal_error.
    load_pasv()
    router = router_factory(None)

    messages = router.handle({"type": "set_pasv", "on": True}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "pasv_requires_auto"
    assert stub_plugins._events() == []


def test_pasv_precedence_holds_with_no_agent(router_factory, stub_plugins):
    # SPEC 2.6.2's ordering (missing plugin wins over wrong mode) must not
    # depend on having an agent to consult for the mode.
    router = router_factory(None)

    messages = router.handle({"type": "set_pasv", "on": True}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "pasv_unavailable"


def test_get_log_with_no_agent_is_log_unavailable_not_internal_error(router_factory):
    # SPEC 2.6.0: log_lines is genuinely unavailable with no agent. This pins
    # the observable behaviour, not the guard that names the case: the
    # pre-existing broad except around agent._config already produces this
    # same code and message when agent is None, so a deleted guard would not
    # make this fail - the point is that the answer stays right regardless of
    # which line is responsible for it.
    router = router_factory(None)

    messages = router.handle({"type": "get_log"}, authenticated=True)

    assert only(messages, "error")["data"]["code"] == "log_unavailable"


def test_get_log_with_no_agent_does_not_promise_a_wait(router_factory):
    # Same caveat as above: this pins that the reply never implies a wait,
    # not which code path produces that reply. Nothing about a missing
    # agent's config is going to resolve itself if the client retries.
    router = router_factory(None)

    messages = router.handle({"type": "get_log"}, authenticated=True)

    message = only(messages, "error")["data"]["message"].lower()
    assert "ready" not in message
    assert "wait" not in message


# ---------------------------------------------------------------------------
# reboot and shutdown
# ---------------------------------------------------------------------------


def test_reboot_broadcasts_then_schedules(router, harness):
    messages = router.handle({"type": "reboot", "message_id": "c7f1"}, authenticated=True)

    assert types_of(messages) == ["acknowledgment", "restarting"]
    assert only(messages, "restarting")["data"] == {"reason": "reboot", "mode": None}
    assert "reboot_device" not in harness.names()

    harness.run_spawned()

    assert harness.args_for("reboot_device") == [()]


def test_shutdown_broadcasts_then_schedules(router, harness):
    messages = router.handle({"type": "shutdown", "message_id": "c7f1"}, authenticated=True)

    assert types_of(messages) == ["acknowledgment", "restarting"]
    assert only(messages, "restarting")["data"] == {"reason": "shutdown", "mode": None}
    assert "shutdown_device" not in harness.names()

    harness.run_spawned()

    assert harness.args_for("shutdown_device") == [()]


def test_reboot_and_shutdown_do_not_restart_pwnagotchi(router, harness):
    router.handle({"type": "reboot"}, authenticated=True)
    router.handle({"type": "shutdown"}, authenticated=True)
    harness.run_spawned()

    assert "restart_pwnagotchi" not in harness.names()


def test_no_control_touches_the_agent_mode(router, harness, agent):
    router.handle({"type": "reboot"}, authenticated=True)
    router.handle({"type": "shutdown"}, authenticated=True)
    harness.run_spawned()

    assert agent.mode == "auto"


# ---------------------------------------------------------------------------
# The default seams reach the module-level functions
# ---------------------------------------------------------------------------


def test_the_default_restart_seam_calls_the_module_function(stub_pwnagotchi):
    companion.default_restart("MANU")

    assert stub_pwnagotchi._calls_to("restart") == [("restart", ("MANU",), {})]


def test_the_default_deps_bind_reboot_and_shutdown_to_the_module(stub_pwnagotchi):
    # There is no agent.reboot() or agent.shutdown() (SPEC 11.1); the graceful
    # path lives at module level (F9).
    deps = companion.Deps()

    deps.reboot_device()
    deps.shutdown_device()

    assert [name for name, _, _ in stub_pwnagotchi._CALLS] == ["reboot", "shutdown"]


def test_the_default_deps_keep_an_explicitly_passed_seam(stub_pwnagotchi):
    recorded = []
    deps = companion.Deps(restart_pwnagotchi=recorded.append)

    deps.restart_pwnagotchi("AUTO")

    assert recorded == ["AUTO"]
    assert stub_pwnagotchi._calls_to("restart") == []


def test_the_default_clock_is_the_wall_clock():
    import time

    assert companion.Deps().now() == pytest.approx(time.time(), abs=5)
