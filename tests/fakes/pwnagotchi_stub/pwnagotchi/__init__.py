"""Stand-in for the `pwnagotchi` top-level module.

Pinned by SPEC.md section 11:

    F1  pwnagotchi.uptime()                  -> int seconds
    F2  pwnagotchi.temperature(celsius=True)  -> int celsius
    F9  pwnagotchi.shutdown(), pwnagotchi.reboot(mode=None), pwnagotchi.restart(mode)

Nothing else exists here, deliberately. The underscore-prefixed names are test scaffolding.
"""

from __future__ import annotations

from typing import Any

# -- test scaffolding -------------------------------------------------------

#: Every call the plugin makes into this module, as (name, args, kwargs).
_CALLS: list[tuple[str, tuple, dict]] = []

#: Readings the two sensor functions return. Tests set these directly.
_UPTIME = 3600
_TEMPERATURE = 42


def _reset() -> None:
    """Clears the recorded calls and restores the default readings."""
    global _UPTIME, _TEMPERATURE
    _CALLS.clear()
    _UPTIME = 3600
    _TEMPERATURE = 42


def _record(name: str, *args: Any, **kwargs: Any) -> None:
    _CALLS.append((name, args, kwargs))


def _calls_to(name: str) -> list[tuple[str, tuple, dict]]:
    return [call for call in _CALLS if call[0] == name]


# -- the pinned surface -----------------------------------------------------


def uptime() -> int:
    """F1: seconds since boot, read from /proc/uptime on a real unit."""
    _record("uptime")
    return _UPTIME


def temperature(celsius: bool = True) -> int:
    """F2: SoC temperature in degrees celsius."""
    _record("temperature", celsius)
    return _TEMPERATURE


def restart(mode: str) -> None:
    """F9: touches the mode flag file and restarts bettercap and pwnagotchi.

    On a real unit this takes the plugin down with it. Here it only records the
    call, which is what lets a test assert the mode switch went through
    `restart` and not through an assignment to `agent.mode`.
    """
    _record("restart", mode)


def reboot(mode: str | None = None) -> None:
    """F9: graceful reboot."""
    _record("reboot", mode)


def shutdown() -> None:
    """F9: graceful power off."""
    _record("shutdown")
