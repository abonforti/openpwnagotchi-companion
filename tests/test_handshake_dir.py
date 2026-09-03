"""Where the handshake capture directory comes from (SPEC 2.2, 2.5, issue #146).

The bug this closes: `handshakes`/`handshakesTotal` used to read
`agent._config['bettercap']['handshakes']` (F14) and nowhere else, so a unit
with no agent - which is a unit in manual mode, exactly the one that may have
hundreds of captures on disk - reported a confident zero. The fix is two
pieces read together: both counts became nullable, and the plugin gained its
own `handshake_dir` config key (SPEC 2.2) so an operator can tell a unit with
no agent where its captures are instead of the plugin only being able to say
it does not know.

Precedence, in order (SPEC 2.5): `handshake_dir` when it is a non-empty
string; otherwise `agent._config['bettercap']['handshakes']` (F14); otherwise
the directory is unknown. Unknown is not the same as empty: a known directory
that is missing or unreadable counts a real zero, and only "nowhere to look"
is null.

Every test here goes through a real `Companion` instance and its own
`_handshake_store()` - not a hand-built `HandshakeStore` over a directory the
test chose, and not a fixture that reimplements the precedence rule. Resolving
the directory is production's job; this file proves production does it right,
which a test that only ever hands a `HandshakeStore` the directory it expects
back cannot do.
"""

from __future__ import annotations

import os
import stat

import pytest

from conftest import FakeAgent
from plugin import companion


# ---------------------------------------------------------------------------
# A Companion instance, driven by hand, agent optional
# ---------------------------------------------------------------------------


@pytest.fixture
def plugin_factory(options, harness, tls_material, tmp_path):
    """Builds `Companion` instances taken through `on_loaded`, agent optional.

    Mirrors `wired_plugin_factory` in `test_broadcast_cadence.py`: real TLS
    material because SPEC D4 has the plugin refuse to start without it.
    `on_loaded` still calls `reconcile()` synchronously against the real
    tether selectors in `options` (`bind_addresses`, SPEC 2.3.1) - what stops
    a real bind is `DepsHarness.local_ipv4` (`harness`, see conftest.py)
    starting empty, not the selectors matching nothing local; a host that
    genuinely holds a `172.20.10.x` address would still bind nothing here,
    because `harness` never reports one. `harness.deps.spawn` only records
    its target and never runs it, so no background thread starts either.

    `agent=None` (the default) leaves `plugin.agent` exactly as `Companion()`
    constructs it - the no-agent state issue #140 documents and SPEC 2.5
    requires the plugin to tolerate for the whole life of the process, not
    only in the window before `on_ready`. Passing an `agent` calls
    `on_ready(agent)`, which SPEC 2.5 states does nothing else: "`on_ready`
    now captures the agent and nothing else".
    """

    built: list[companion.Companion] = []

    def _make(*, agent=None, overrides: dict | None = None) -> companion.Companion:
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
        if agent is not None:
            plugin.on_ready(agent)
        built.append(plugin)
        return plugin

    yield _make

    for plugin in built:
        plugin.on_unload()


def _make_captures(directory, names: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b"")


def _agent_pointing_at(agent_factory, tmp_path, directory) -> FakeAgent:
    return agent_factory(
        config={
            "bettercap": {"handshakes": str(directory)},
            "main": {"log": {"path": str(tmp_path / "unused.log")}},
        }
    )


class ConfigExplodes:
    """An agent that is present but whose config read raises.

    The same broken-agent shape `test_face_status.py` uses to prove a
    production read degrades rather than crashing - here reused to prove
    resolution treats "the agent answered with garbage" the same as "there is
    no agent": both leave the directory unknown, never a guessed path.
    """

    @property
    def _config(self):
        raise RuntimeError("bettercap config unavailable")


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


def test_handshake_dir_wins_over_the_agents_config_when_they_differ(
    plugin_factory, agent_factory, tmp_path
):
    configured = tmp_path / "configured"
    _make_captures(configured, ["Configured_aabbccddeeff.pcapng"])

    agents_own = tmp_path / "agents-own"
    _make_captures(
        agents_own,
        ["One_aabbccddeeff.pcapng", "Two_112233445566.pcapng", "Three_998877665544.pcap"],
    )

    agent = _agent_pointing_at(agent_factory, tmp_path, agents_own)
    plugin = plugin_factory(agent=agent, overrides={"handshake_dir": str(configured)})

    # If the agent's directory (3 files) had won this would read (2, 3);
    # `handshake_dir`'s directory has exactly one .pcapng and nothing else.
    assert plugin._handshake_store().counts() == (1, 1)


def test_agent_directory_used_when_handshake_dir_is_unset(
    plugin_factory, agent_factory, tmp_path
):
    directory = tmp_path / "agents-own"
    _make_captures(directory, ["One_aabbccddeeff.pcapng", "Two_112233445566.pcap"])
    agent = _agent_pointing_at(agent_factory, tmp_path, directory)

    # `options` (conftest.py) never sets `handshake_dir` at all - this is the
    # "key absent" half of "unset", not merely an empty string.
    plugin = plugin_factory(agent=agent)

    assert plugin._handshake_store().counts() == (1, 2)


def test_agent_directory_used_when_handshake_dir_is_the_empty_string(
    plugin_factory, agent_factory, tmp_path
):
    directory = tmp_path / "agents-own"
    _make_captures(directory, ["One_aabbccddeeff.pcapng"])
    agent = _agent_pointing_at(agent_factory, tmp_path, directory)

    plugin = plugin_factory(agent=agent, overrides={"handshake_dir": ""})

    assert plugin._handshake_store().counts() == (1, 1)


def test_a_non_string_handshake_dir_falls_through_to_the_agent(
    plugin_factory, agent_factory, tmp_path
):
    """The symmetric half of SPEC 2.5's guard: `handshake_dir` must be a
    non-empty *string* to win, not merely truthy. A stray non-string value -
    a config value that came out of a parser as an int, say - must fall
    through to the agent's own directory rather than becoming
    `HandshakeStore(42)`, a store built over a value nothing on disk could
    ever be a path to. Production applies the same `isinstance(..., str)`
    check to both `handshake_dir` and the agent's own config value; this is
    the config-key side, `test_agent_directory_empty_or_non_string_...`
    below is the agent-config side.
    """
    directory = tmp_path / "agents-own"
    _make_captures(directory, ["One_aabbccddeeff.pcapng"])
    agent = _agent_pointing_at(agent_factory, tmp_path, directory)

    plugin = plugin_factory(agent=agent, overrides={"handshake_dir": 42})

    assert plugin._handshake_store().counts() == (1, 1)


# ---------------------------------------------------------------------------
# Unknown
# ---------------------------------------------------------------------------


def test_directory_is_unknown_with_no_agent_and_no_handshake_dir(plugin_factory):
    plugin = plugin_factory()

    assert plugin._handshake_store().counts() == (None, None)


def test_directory_is_unknown_when_the_agents_config_read_raises(plugin_factory):
    plugin = plugin_factory(agent=ConfigExplodes())

    assert plugin._handshake_store().counts() == (None, None)


def test_directory_is_unknown_when_the_agents_config_has_no_bettercap_key(
    plugin_factory, agent_factory, tmp_path
):
    """A `KeyError` from a real, present-but-incomplete config dict, rather
    than `ConfigExplodes`'s deliberately raising property: the same "nowhere
    to look" answer, reached through the plain `except Exception` around the
    `agent._config['bettercap']['handshakes']` read rather than through a
    fixture that raises on purpose."""
    agent = agent_factory(config={"main": {"log": {"path": str(tmp_path / "unused.log")}}})
    plugin = plugin_factory(agent=agent)

    assert plugin._handshake_store().counts() == (None, None)


@pytest.mark.parametrize("value", ["", 42], ids=["empty-string", "non-string"])
def test_agent_directory_empty_or_non_string_is_unknown_not_a_real_zero(
    plugin_factory, agent_factory, tmp_path, value
):
    """SPEC 2.5 (amended): "Neither source may contribute an empty path: an
    empty path is nowhere to look, and a store over one scans nothing and
    reports zero, which is this ticket's own defect written a second time."

    An agent whose own `bettercap.handshakes` is an empty string - or, since
    it costs nothing extra to check, some other non-string junk - must leave
    the directory unknown, not fall through to a store over `""` that
    quietly reports a real-looking `(0, 0)`. This is the one case where the
    old defect's answer and the correct one are both plausible-looking
    integers-or-not, so the assertion is on the value explicitly (`is None`)
    rather than through `== (None, None)` alone, which a mutant returning
    `(0, 0)` would not trip if the tuple identity check were loosened.
    """
    agent = agent_factory(
        config={
            "bettercap": {"handshakes": value},
            "main": {"log": {"path": str(tmp_path / "unused.log")}},
        }
    )
    plugin = plugin_factory(agent=agent)

    handshakes, handshakes_total = plugin._handshake_store().counts()

    assert handshakes is None
    assert handshakes_total is None


# ---------------------------------------------------------------------------
# Known, but missing or unreadable: a real zero, not unknown
# ---------------------------------------------------------------------------


def test_a_known_but_missing_directory_is_a_real_zero(plugin_factory, agent_factory, tmp_path):
    missing = tmp_path / "does-not-exist"
    agent = _agent_pointing_at(agent_factory, tmp_path, missing)

    plugin = plugin_factory(agent=agent)

    assert plugin._handshake_store().counts() == (0, 0)


def test_handshake_dir_pointing_nowhere_is_also_a_real_zero(plugin_factory, tmp_path):
    # `handshake_dir` is set - the plugin was told exactly where to look - so
    # a directory that does not exist there is a real zero, the same as it is
    # via the agent's own config, never unknown: the operator said which
    # directory they meant.
    missing = tmp_path / "nowhere"
    plugin = plugin_factory(overrides={"handshake_dir": str(missing)})

    assert plugin._handshake_store().counts() == (0, 0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a 000 directory")
def test_a_known_but_unreadable_directory_is_a_real_zero(plugin_factory, agent_factory, tmp_path):
    locked = tmp_path / "locked"
    _make_captures(locked, ["One_aabbccddeeff.pcapng"])
    agent = _agent_pointing_at(agent_factory, tmp_path, locked)
    plugin = plugin_factory(agent=agent)

    locked.chmod(0)
    try:
        assert plugin._handshake_store().counts() == (0, 0)
    finally:
        locked.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------------------
# Known, with captures: the real counts
# ---------------------------------------------------------------------------


def test_a_known_directory_with_captures_reports_the_real_counts(
    plugin_factory, handshake_dir
):
    # The shared fixture directory: three .pcapng and one legacy .pcap
    # (SPEC F15, same fixture test_handshakes.py and test_stats.py rely on).
    plugin = plugin_factory(overrides={"handshake_dir": str(handshake_dir)})

    assert plugin._handshake_store().counts() == (3, 4)
