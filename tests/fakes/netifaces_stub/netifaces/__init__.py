"""A fake `netifaces`, so the primary address-enumeration path can run.

SPEC 2.3 says the enumeration uses `netifaces` when it is importable and parses
`ip -4 addr show` otherwise, and explicitly forbids a hard dependency on it. A
build host therefore does not have it, which leaves the primary path - the one
that runs on a unit where somebody did install it - as code no test reaches.
This package on `sys.path`, installed for the duration of a test, is that path's
only way in.

The surface here is the real library's, not a pinned subset. That is the one
deliberate difference from `tests/fakes/pwnagotchi_stub/`, and the reason is
that the two stubs guard different things: the pwnagotchi stub exists to make an
invented pwnagotchi API fail loudly, because SPEC section 11 pins every symbol
the plugin may use and an invention there takes a real unit down. `netifaces`
has no such list - it is a third-party library with a published API - so a stub
narrowed to guesses would fail on correct code that happened to call
`gateways()`. What it must not do is answer differently from the real library,
so the shapes below are the documented ones: `ifaddresses()` keyed by address
family, each family a list of dicts with `addr`, and `ValueError` for an
interface that does not exist.

Everything underscore-prefixed is test scaffolding. The plugin must never touch
it.
"""

from __future__ import annotations

#: The address-family constants, with the values the real library exposes on
#: Linux. The plugin is expected to select on `AF_INET`; a stub that used
#: arbitrary numbers would let a plugin keyed to the wrong family pass.
AF_UNSPEC = 0
AF_INET = 2
AF_INET6 = 10
AF_PACKET = 17
AF_LINK = 17

version = "0.11.0"

#: `{interface_name: {family: [addr_dict, ...]}}`
_table: dict[str, dict[int, list[dict]]] = {}
_interfaces_error: BaseException | None = None
#: Armed `ifaddresses()` failures, keyed by interface name, with `None` as the
#: key meaning "every interface". Per-interface rather than global because SPEC
#: 2.3.1 draws its line exactly there: one interface that raises is skipped and
#: the pass stands, while every interface raising is a failed enumeration. A
#: single global error can only express the second, which leaves the first
#: unpinnable and the difference between the two invisible.
_ifaddresses_errors: dict[str | None, BaseException] = {}
#: Every `ifaddresses()` argument, in order, so a test can show the enumeration
#: walked the interfaces rather than inventing them.
_queried: list[str] = []


def _reset() -> None:
    global _interfaces_error
    _table.clear()
    _queried.clear()
    _ifaddresses_errors.clear()
    _interfaces_error = None


def _set_table(table: dict[str, dict[int, list[dict]]]) -> None:
    _table.clear()
    _table.update(table)


def _fail_interfaces(error: BaseException) -> None:
    global _interfaces_error
    _interfaces_error = error


def _fail_ifaddresses(
    error: BaseException, interfaces: "list[str] | None" = None
) -> None:
    """Arms `ifaddresses()` to raise `error`, for `interfaces` or for all of them.

    The real library raises for an interface that went away between
    `interfaces()` and `ifaddresses()`, which is a property of that one
    interface and not of the host: `wlan0mon` is created and torn down as a
    matter of course while the tether stays up. Naming the interfaces is
    therefore the honest shape, and it is the only one in which "one of five
    failed" can be told apart from "all five failed".
    """
    if interfaces is None:
        _ifaddresses_errors[None] = error
        return
    for name in interfaces:
        _ifaddresses_errors[name] = error


# ---------------------------------------------------------------------------
# The public API
# ---------------------------------------------------------------------------


def interfaces() -> list[str]:
    if _interfaces_error is not None:
        raise _interfaces_error
    return list(_table)


def ifaddresses(interface: str) -> dict[int, list[dict]]:
    _queried.append(interface)
    error = _ifaddresses_errors.get(interface, _ifaddresses_errors.get(None))
    if error is not None:
        raise error
    if interface not in _table:
        # The real library's message and type for an unknown interface.
        raise ValueError("You must specify a valid interface name.")
    return {family: [dict(entry) for entry in entries]
            for family, entries in _table[interface].items()}


def gateways() -> dict:
    """The real library reports the default routes here.

    Present because the real API has it, empty because SPEC 2.3.1 makes the
    bind decision from addresses alone and a route must not reach it.
    """
    return {"default": {}}
