"""Stand-in for `pwnagotchi.plugins`.

Pinned by SPEC.md section 11:

    F8   plugins.on(event_name, *args) enqueues work and returns nothing.
         Dispatch is asynchronous: a plugin that emits an event must not read the
         resulting state back on the next line.
    F12  A loaded plugin is reachable as plugins.loaded['<key>'].

`Plugin` is the base class every pwnagotchi plugin subclasses.
"""

from __future__ import annotations

from typing import Any

# -- test scaffolding -------------------------------------------------------

#: Every plugins.on() / toggle_plugin() call, as (name, args, kwargs).
_CALLS: list[tuple[str, tuple, dict]] = []


def _reset() -> None:
    """Clears the recorded calls and empties the loaded-plugin registry."""
    _CALLS.clear()
    loaded.clear()


def _calls_to(name: str) -> list[tuple[str, tuple, dict]]:
    return [call for call in _CALLS if call[0] == name]


def _events() -> list[str]:
    """The event names passed to plugins.on(), in order."""
    return [call[1][0] for call in _CALLS if call[0] == "on" and call[1]]


# -- the pinned surface -----------------------------------------------------

#: Plugin key -> plugin instance. Empty unless a test puts something in it.
loaded: dict[str, Any] = {}


class Plugin:
    """Base class for a pwnagotchi plugin.

    The real one carries a metaclass that registers subclasses; nothing in the
    contract depends on that, so it is left out rather than approximated.
    """


def on(event_name: str, *args: Any) -> None:
    """F8: queues `event_name` onto every loaded plugin's own thread.

    Returns nothing, and the handlers have not run by the time it returns. The
    stub deliberately does not invoke any handler: a test that passes only
    because the stub dispatched synchronously would be asserting the opposite of
    the pinned behaviour.
    """
    _CALLS.append(("on", (event_name,) + args, {}))


def toggle_plugin(name: str, enable: bool = True) -> bool:
    """Enables or disables a plugin by name. Recorded, never performed."""
    _CALLS.append(("toggle_plugin", (name, enable), {}))
    return True
