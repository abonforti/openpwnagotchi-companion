"""`ws_port` and `http_port` are a number the owner chose (SPEC 2.3.0, issue #16).

Before this rule `ws_port`/`http_port` were read and used unvalidated, so
`ws_port = 0` was accepted and the operating system assigned an arbitrary free
port - a unit listening on a port nobody chose and nothing announces, which is
the same shape SPEC 2.3 already refuses for addresses.

**Zero is refused, and so is anything outside 1-65535 or not a whole number.**
The listener that would have used the bad value is not started, the refusal is
logged at error naming the key, and the plugin carries on: the *other*
listener still comes up (SPEC 2.2.1's rule that a misconfiguration must
degrade, never take the unit down), and the process itself never raises.

Both halves are exercised at two levels: `Listeners` directly, the same shape
`tests/test_binding.py` uses for `bind_addresses` entry validation, and
`Companion` end to end, the same shape `tests/test_tls_startup.py` uses for
"a refusal to start is not a crash" - because a rule stated only against
`Listeners` could pass while `Companion.on_loaded` still let a bad value
reach it some other way.
"""

from __future__ import annotations

import http.client
import re
import socket
import ssl
import time
from typing import Any, Callable, Iterable

import pytest

from plugin import companion

LOOPBACK = "127.0.0.1"


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
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


def error_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]


def mentions(messages: list[str], key: str) -> bool:
    """Whether any message names `key` as a whole word.

    Word-bounded for the same reason `tests/test_config_keys.py`'s
    `warnings_mentioning` is: `ws_port` and `http_port` share no substring
    relationship today, but a plain substring search is a property that holds
    only until a third port-like key is added, and this file has no reason to
    be the one place that regresses first.
    """
    pattern = re.compile(rf"\b{re.escape(key)}\b")
    return any(pattern.search(message) for message in messages)


# SPEC 2.3.0: a port is an integer in 1-65535 and nothing else. `True` is
# refused explicitly - Python's `isinstance(True, int)` and `1 <= True <=
# 65535` would otherwise accept `ws_port = true` as port 1, an accident of the
# language rather than a value the owner wrote - and `8082.0` is refused even
# though it is numerically whole, because TOML keeps floats and integers
# distinct and a key holding one is a type mismatch, not a value to coerce.
INVALID_PORTS: list[Any] = [
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(-8082, id="very-negative"),
    pytest.param(65536, id="one-above-max"),
    pytest.param(1_000_000, id="way-too-large"),
    pytest.param(8082.5, id="fractional"),
    pytest.param(8082.0, id="whole-valued-float"),
    pytest.param(True, id="boolean-true"),
    pytest.param(False, id="boolean-false"),
    pytest.param(None, id="none"),
    pytest.param("8082", id="numeric-string"),
    pytest.param("", id="empty-string"),
    pytest.param([], id="list"),
]


# ---------------------------------------------------------------------------
# Listeners level - mirrors test_binding.py's entry-validation shape
# ---------------------------------------------------------------------------


async def idle_handler(websocket, *_):
    await websocket.wait_closed()


@pytest.fixture
def ssl_context(tls_material):
    context = companion.build_ssl_context(
        str(tls_material["cert"]), str(tls_material["key"])
    )
    assert context is not None, "the fixture certificate must be usable"
    return context


@pytest.fixture
def make_listeners(tmp_path, tls_material, free_ports, deps, ssl_context):
    """Builds a `Listeners` over a configuration whose only variable is the ports."""
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    started: list[Any] = []

    def _make(omit: Iterable[str] = (), **overrides: Any):
        options: dict[str, Any] = {
            "bind_addresses": [LOOPBACK],
            "ws_port": free_ports[0],
            "http_port": free_ports[1],
            "tls_cert": str(tls_material["cert"]),
            "tls_key": str(tls_material["key"]),
            "web_root": str(web_root),
            "token": "",
            "rebind_interval": 30,
        }
        options.update(overrides)
        for key in omit:
            options.pop(key, None)
        instance = companion.Listeners(options, deps, ssl_context, idle_handler)
        started.append(instance)
        return instance

    yield _make

    for instance in started:
        instance.stop()


@pytest.fixture
def ports(free_ports) -> tuple[int, int]:
    return free_ports


@pytest.mark.parametrize("bad_port", INVALID_PORTS)
def test_a_bad_ws_port_starts_no_wss_listener_but_https_still_comes_up(
    make_listeners, harness, ports, bind_recorder, caplog, bad_port
):
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(ws_port=bad_port)
        listeners.reconcile()

        assert wait_until(lambda: is_listening(LOOPBACK, ports[1])), (
            "a bad ws_port must not stop the HTTPS listener from coming up"
        )

    # `ports[0]` was never handed to `make_listeners` as `ws_port` in this run
    # (it was overridden by `bad_port`), so `not is_listening(LOOPBACK,
    # ports[0])` would hold for every implementation, including one that never
    # refused anything at all - it says nothing about what the plugin did with
    # the bad value. What it actually did is what `bind_recorder` saw reach
    # `socket.bind`: exactly one loopback bind, the HTTPS one, the same shape
    # `test_a_bad_ws_port_still_starts_the_plugin_and_serves_https` below uses
    # at the Companion level.
    loopback_binds = [a for a in bind_recorder.addresses if a[0] == LOOPBACK]
    assert len(loopback_binds) == 1, (
        f"only the HTTPS listener may have bound; got {loopback_binds}"
    )

    messages = error_messages(caplog)
    assert mentions(messages, "ws_port"), (
        f"the refusal must name the key; got {messages}"
    )


@pytest.mark.parametrize("bad_port", INVALID_PORTS)
def test_a_bad_http_port_starts_no_https_listener_but_wss_still_comes_up(
    make_listeners, harness, ports, bind_recorder, caplog, bad_port
):
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(http_port=bad_port)
        listeners.reconcile()

        assert wait_until(lambda: is_listening(LOOPBACK, ports[0])), (
            "a bad http_port must not stop the WSS listener from coming up"
        )

    # See the comment in the ws_port test above: `ports[1]` was never handed
    # to `make_listeners` as `http_port` in this run, so a probe against it
    # cannot fail. `bind_recorder` is the assertion that actually watches what
    # reached the kernel.
    loopback_binds = [a for a in bind_recorder.addresses if a[0] == LOOPBACK]
    assert len(loopback_binds) == 1, (
        f"only the WSS listener may have bound; got {loopback_binds}"
    )

    messages = error_messages(caplog)
    assert mentions(messages, "http_port"), (
        f"the refusal must name the key; got {messages}"
    )


def test_both_ports_bad_binds_nothing_and_does_not_raise(
    make_listeners, harness, bind_recorder, caplog
):
    """The degrade-not-crash floor: nothing to serve, but no exception either."""
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(ws_port=0, http_port=0)
        listeners.reconcile()

    assert bind_recorder.addresses == [], "neither refused port may contribute a listener"
    messages = error_messages(caplog)
    assert mentions(messages, "ws_port")
    assert mentions(messages, "http_port")


def test_the_upper_boundary_port_is_accepted(
    make_listeners, harness, bind_recorder, caplog
):
    """65535 is the top of the accepted range and must not be refused.

    Pinned through construction and the bind attempt `bind_recorder` observes,
    not through a successful client connection to a hardcoded port: the
    earlier version of this test bound 65535 on loopback and then connected
    to it, which fails on any host where something else already holds that
    port and proves nothing about the validation rule itself. Mirrors
    `test_the_lower_boundary_port_is_not_refused_as_out_of_range` below.
    """
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(ws_port=65535)
        listeners.reconcile()
        wait_until(lambda: any(a == (LOOPBACK, 65535) for a in bind_recorder.addresses))

    assert (LOOPBACK, 65535) in bind_recorder.addresses, (
        "65535 must not be refused as out of range"
    )
    assert not mentions(error_messages(caplog), "ws_port")


def test_one_above_the_upper_boundary_is_refused(
    make_listeners, harness, bind_recorder, caplog
):
    """65536 is one past the accepted range and must be refused exactly like
    any other out-of-range value (it is already covered generically by
    `INVALID_PORTS`' "one-above-max" case above); this test pins it at the
    exact boundary and needs no socket at all, since the refusal happens at
    construction (SPEC 2.3.0), before `reconcile()` - and therefore before
    any bind - ever runs.
    """
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        make_listeners(ws_port=65536)

    assert bind_recorder.addresses == [], "a refused port must never reach socket.bind"
    assert mentions(error_messages(caplog), "ws_port")


def test_the_lower_boundary_port_is_not_refused_as_out_of_range(
    make_listeners, harness, bind_recorder, caplog
):
    """1 is the bottom of the accepted range.

    Binding it may still fail with `EADDRNOTAVAIL`/`EACCES` on a host without
    the right privilege - that is SPEC 2.3.1's OSError row (logged at warning,
    retried) and is not this rule's concern. What this test pins is narrower:
    1 must never be refused *as an invalid port*, which is an ERROR naming
    `ws_port`. `bind_recorder` sees the attempt regardless of whether the
    underlying syscall then succeeds, because it records the address handed to
    `socket.bind` before calling the real one.
    """
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(ws_port=1)
        listeners.reconcile()
        wait_until(lambda: any(a == (LOOPBACK, 1) for a in bind_recorder.addresses))

    assert (LOOPBACK, 1) in bind_recorder.addresses, "1 must not be refused as out of range"
    assert not mentions(error_messages(caplog), "ws_port")


def test_the_refusal_happens_at_construction_not_at_the_first_reconcile_pass(
    make_listeners, harness, bind_recorder, caplog
):
    """SPEC 2.3.0: "The check happens when `Listeners` is constructed, before
    any pass runs" - the same point `Listeners` already validates
    `bind_addresses` entries at (SPEC 2.3.1). A check deferred to the first
    `reconcile()` would still satisfy every other assertion in this file,
    since all of them call `reconcile()` immediately after construction; this
    is the one test that puts a gap between the two calls, so a refusal that
    only happens during `reconcile()` has somewhere to be absent from.
    """
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        make_listeners(ws_port=0)

        assert mentions(error_messages(caplog), "ws_port"), (
            "constructing a Listeners over a bad port must log the refusal "
            "immediately, before reconcile() is ever called"
        )

    assert bind_recorder.addresses == [], (
        "nothing may have been bound before reconcile() ran at all"
    )


def test_reconciling_a_constructed_listeners_repeatedly_does_not_repeat_the_refusal(
    make_listeners, harness, caplog
):
    """A companion to the construction-time test above: if the refusal lived
    in `reconcile()` after all, a naive fix might log it on every pass. This
    pins that once the value was refused at construction, three subsequent
    `reconcile()` calls add nothing - the same "once, not once per pass"
    shape `tests/test_config_keys.py` pins for the unknown-key warning.
    """
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(ws_port=0)
        after_construction = len(
            [m for m in error_messages(caplog) if "ws_port" in m]
        )
        listeners.reconcile()
        listeners.reconcile()
        listeners.reconcile()

    final = len([m for m in error_messages(caplog) if "ws_port" in m])
    assert after_construction == 1
    assert final == 1, (
        "the bad-port refusal must be logged once, at construction, not once "
        f"per reconcile() pass (count after 3 extra passes: {final})"
    )


# ---------------------------------------------------------------------------
# A key that is simply absent, distinguished from one present holding `None`
# ---------------------------------------------------------------------------
#
# Every test above supplies both `ws_port` and `http_port` explicitly - the
# "none" case in INVALID_PORTS covers a key *present* and holding the literal
# value `None`, which is refused exactly like any other invalid port. SPEC
# 2.3.0 treats an absent key differently: falling back to the default is not
# the same rule as refusing an invalid value, and nothing in this file has
# ever put the two cases in front of an implementation that might conflate
# them - a check that refused an absent key exactly like an explicit `None`
# would still pass every test above and only fail here.


def test_a_missing_ws_port_key_falls_back_to_the_default_rather_than_being_refused(
    make_listeners, harness, caplog
):
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(omit={"ws_port"})
        listeners.reconcile()

    assert not mentions(error_messages(caplog), "ws_port"), (
        "an absent ws_port key must fall back to the default, not be refused "
        "as an invalid value"
    )


def test_a_missing_http_port_key_falls_back_to_the_default_rather_than_being_refused(
    make_listeners, harness, caplog
):
    harness.local_ipv4 = [LOOPBACK]

    with caplog.at_level("ERROR"):
        listeners = make_listeners(omit={"http_port"})
        listeners.reconcile()

    assert not mentions(error_messages(caplog), "http_port"), (
        "an absent http_port key must fall back to the default, not be "
        "refused as an invalid value"
    )


# ---------------------------------------------------------------------------
# Companion level - "the plugin still starts", the shape test_tls_startup.py
# uses for the equivalent TLS-material rule
# ---------------------------------------------------------------------------


@pytest.fixture
def full_options(tls_material, tmp_path, free_ports) -> dict:
    web_root = tmp_path / "web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    return {
        **companion.DEFAULTS,
        "bind_addresses": [LOOPBACK],
        "ws_port": free_ports[0],
        "http_port": free_ports[1],
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(web_root),
        "token": "",
    }


def test_a_bad_ws_port_still_starts_the_plugin_and_serves_https(
    full_options, harness, bind_recorder, caplog
):
    harness.local_ipv4 = [LOOPBACK]
    config = dict(full_options)
    config["ws_port"] = 0
    good_http_port = config["http_port"]

    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = config
    try:
        with caplog.at_level("ERROR"):
            plugin.on_loaded()

            assert plugin._router is not None, (
                "a bad ws_port must never make on_loaded refuse to start "
                "(SPEC 2.2.1: degrade, never take the unit down)"
            )
            assert wait_until(lambda: is_listening(LOOPBACK, good_http_port)), (
                "the HTTPS listener must still come up despite the bad ws_port"
            )

        loopback_binds = [a for a in bind_recorder.addresses if a[0] == LOOPBACK]
        assert len(loopback_binds) == 1, (
            f"only the HTTPS listener may have bound; got {loopback_binds}"
        )
        assert mentions(error_messages(caplog), "ws_port")
    finally:
        plugin.on_unload()


def test_both_ports_bad_still_starts_the_plugin_with_nothing_to_serve(
    full_options, harness, bind_recorder, caplog
):
    harness.local_ipv4 = [LOOPBACK]
    config = dict(full_options)
    config["ws_port"] = 0
    config["http_port"] = 0

    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = config
    try:
        with caplog.at_level("ERROR"):
            plugin.on_loaded()

            assert plugin._router is not None, (
                "both ports bad must still leave the plugin started"
            )

        assert bind_recorder.addresses == [], "neither refused port may bind"
        messages = error_messages(caplog)
        assert mentions(messages, "ws_port")
        assert mentions(messages, "http_port")
    finally:
        # Confirms the "does not raise" half at the level a real unit exercises:
        # on_unload of a plugin that never bound anything must still be harmless.
        plugin.on_unload()


# ---------------------------------------------------------------------------
# A refused ws_port leaves the *other* listener running, and the CSP that
# listener hands out has to be honest about it (SPEC 2.3.0 / 2.15.1)
# ---------------------------------------------------------------------------


def parse_csp(header: str) -> dict[str, list[str]]:
    """Splits a CSP header into {directive: [tokens...]}, tolerant of trailing
    ';' and spacing. A copy of tests/test_csp.py's own helper - this file does
    not import from that one so each stays runnable and readable on its own.
    """
    directives: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, *tokens = chunk.split()
        directives[name] = tokens
    return directives


def test_a_refused_ws_port_leaves_the_csp_with_no_wss_origin_and_https_still_serves(
    full_options, harness, tls_material, caplog
):
    """SPEC 2.3.0: refusing a bad port leaves the other listener running.
    SPEC 2.15.1: connect-src is built per request from the addresses the
    plugin is bound to on the WSS port, `'self'` plus one wss:// origin per
    address. With ws_port refused the WSS listener never binds anywhere, so
    there is nothing to list - connect-src must be `'self'` alone, not a
    wss:// origin built from whatever the OS made of the bad value and not a
    silently-omitted directive either. Nothing else in this suite drives the
    real HTTPS listener with a refused ws_port and reads its CSP back; every
    CSP assertion in tests/test_csp.py runs against a `make_http_handler`
    built directly over addresses the test supplies, not against a Companion
    whose WSS listener failed to come up.
    """
    harness.local_ipv4 = [LOOPBACK]
    config = dict(full_options)
    config["ws_port"] = 0
    good_http_port = config["http_port"]

    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = config
    try:
        with caplog.at_level("ERROR"):
            plugin.on_loaded()

            assert wait_until(lambda: is_listening(LOOPBACK, good_http_port)), (
                "the HTTPS listener must still come up despite the refused ws_port"
            )

        client_context = ssl.create_default_context(cafile=str(tls_material["cert"]))
        connection = http.client.HTTPSConnection(
            LOOPBACK, good_http_port, timeout=5.0, context=client_context
        )
        try:
            connection.request("GET", "/index.html")
            response = connection.getresponse()
            status = response.status
            headers = {name.lower(): value for name, value in response.getheaders()}
            response.read()
        finally:
            connection.close()

        assert status == 200, "the HTTPS listener must actually serve the PWA"
        csp = headers["content-security-policy"]
        directives = parse_csp(csp)
        assert directives["connect-src"] == ["'self'"], (
            f"a refused ws_port must leave connect-src with no wss:// origin at "
            f"all; got {csp!r}"
        )
    finally:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# gpsd_port is a port too (SPEC 2.3.0, "gpsd_port is a port too")
# ---------------------------------------------------------------------------
#
# Unlike ws_port/http_port, gpsd_port is not checked at Listeners construction:
# it is read inside GpsResolver.refresh_gpsd() and used to open a socket to
# gpsd. SPEC 2.3.0/2.12 place the check "at the poll, on the value the poll
# will use" - the key can be edited between passes, so a value validated once
# and cached could disagree with the value actually dispatched. A refused
# value must never reach the log, including through another library's own
# exception text: `int("anything")` raises `invalid literal for int() with
# base 10: 'anything'`, which carries the owner's raw string without this
# file ever interpolating it itself - the exact route the `gpsd_host`/
# `ipaddress.ValueError` case was fixed for.


def make_gps_resolver(options, harness, agent, session_payload=None):
    agent._session_payload = session_payload or {}
    cache = companion.SessionCache(lambda: agent, harness.deps)
    cache.refresh()
    return companion.GpsResolver(options, harness.deps, cache)


# `None` is deliberately excluded here, unlike the ws_port/http_port batch
# above: those two are read with a plain `.get(key, DEFAULTS[key])`
# specifically so an explicit `null` is refused rather than folded into the
# default (see the comment beside `raw_ws_port` in `Listeners.__init__`).
# `gpsd_port` is read through the ordinary `option()` helper instead, whose
# own documented contract folds a `None` value into the default exactly as
# it does for every other key read that way (`gpsd_host` included) - so
# `gpsd_port = None` reaching the poll with the default port is the
# documented behaviour of the shared helper, not a hole in this rule. TOML
# has no null literal to begin with, so the case this excludes is one an
# owner's file could never produce anyway.
GPSD_PORT_INVALID = [p for p in INVALID_PORTS if p.values[0] is not None]


@pytest.mark.parametrize("bad_port", GPSD_PORT_INVALID)
def test_a_bad_gpsd_port_is_refused_before_the_poll(
    options, harness, agent, caplog, bad_port
):
    options["gpsd_port"] = bad_port
    harness.gpsd_reply = {"class": "TPV", "mode": 3, "lat": -33.5, "lon": -25.0}
    resolver = make_gps_resolver(options, harness, agent)

    with caplog.at_level("ERROR"):
        resolver.refresh_gpsd()

    assert "read_gpsd" not in harness.names(), (
        "a bad gpsd_port must never reach the poll"
    )
    assert resolver.current() == companion.gps_unavailable(), (
        "with gpsd refused and no other source configured, GPS must report "
        "unavailable rather than fail"
    )
    assert mentions(error_messages(caplog), "gpsd_port"), (
        f"the refusal must name the key; got {error_messages(caplog)}"
    )


def test_a_non_integer_gpsd_port_never_leaks_through_ints_own_exception_text(
    options, harness, agent, caplog
):
    """The specific route SPEC 2.3.0 names: `int("anything")` raises `invalid
    literal for int() with base 10: 'anything'`, carrying the owner's raw
    value even though this file never wrote it into a log call itself.

    Two things the original version of this test got wrong, both needed to
    actually pin the route rather than pass against the leak: the pre-fix
    code logged the exception's text at DEBUG, not ERROR, so a check that
    only captures and scans ERROR records - `caplog.at_level("ERROR")` raises
    the capture threshold, and `error_messages()` filters on `levelname` -
    never even sees a leaking DEBUG record; and "`read_gpsd` was never
    called" holds under the old code too, since `int("anything")` raises
    before the poll is reached, so that assertion alone cannot tell the fix
    from the defect. This captures at DEBUG and scans every record at every
    level for the raw value, which is the only way to see a leak logged
    below ERROR.
    """
    options["gpsd_port"] = "anything"
    resolver = make_gps_resolver(options, harness, agent)

    with caplog.at_level("DEBUG"):
        resolver.refresh_gpsd()
        resolver.refresh_gpsd()

    messages = [record.getMessage() for record in caplog.records]
    assert not any("anything" in message for message in messages), (
        f"the refused gpsd_port value leaked into the log; got {messages}"
    )
    assert "read_gpsd" not in harness.names()


def test_repeated_refreshes_over_a_bad_gpsd_port_do_not_poll(options, harness, agent):
    options["gpsd_port"] = 0
    resolver = make_gps_resolver(options, harness, agent)

    resolver.refresh_gpsd()
    resolver.refresh_gpsd()
    resolver.refresh_gpsd()

    assert "read_gpsd" not in harness.names()


def test_the_gpsd_port_refusal_is_logged_once_and_again_after_a_recovery(
    options, harness, agent, caplog
):
    """SPEC 2.3.0 states this in bold for the port refusals generally: logged
    once, not once per pass. Two different defects would leave that half
    alone true: one that logs on every pass (spam), and one that logs once
    and then latches a flag that never clears - the flag SPEC's own fix
    resets on a successful pass. A flag that never clears is the one that
    matters most: it makes a *second*, unrelated misconfiguration go
    completely silent, because the code believes it already reported gpsd_port
    trouble once and never will again. So this pins both halves: refuse
    twice (exactly one message), then a valid value that actually polls, then
    refuse again (a second message, not silence).
    """
    options["gpsd_port"] = 0
    harness.gpsd_reply = {"class": "TPV", "mode": 3, "lat": -33.5, "lon": -25.0}
    resolver = make_gps_resolver(options, harness, agent)

    with caplog.at_level("ERROR"):
        resolver.refresh_gpsd()
        resolver.refresh_gpsd()

    first_batch = [m for m in error_messages(caplog) if "gpsd_port" in m]
    assert len(first_batch) == 1, (
        f"the refusal must be logged once, not once per pass; got {first_batch}"
    )

    caplog.clear()
    options["gpsd_port"] = 2947
    with caplog.at_level("ERROR"):
        resolver.refresh_gpsd()

    assert "read_gpsd" in harness.names(), (
        "a valid gpsd_port must actually be polled on the recovery pass, or "
        "this proves nothing about the flag clearing"
    )
    assert not mentions(error_messages(caplog), "gpsd_port"), (
        "a successful pass must not still be carrying the earlier refusal"
    )

    caplog.clear()
    options["gpsd_port"] = 0
    with caplog.at_level("ERROR"):
        resolver.refresh_gpsd()

    second_batch = [m for m in error_messages(caplog) if "gpsd_port" in m]
    assert len(second_batch) == 1, (
        "a fresh refusal after a successful pass must be logged again - a "
        f"flag that never clears would leave this silent; got {second_batch}"
    )


def test_a_bad_gpsd_port_does_not_disable_other_gps_sources(options, harness, agent):
    """The refusal costs only gpsd - the same rule SPEC 2.3.0/2.12 state for
    a refused `gpsd_host` (issue #163): with `gps_source` left at its default
    `auto`, a bettercap fix must still surface exactly as it would with no
    gpsd configured at all.
    """
    options["gpsd_port"] = 0
    bettercap_fix = {"Latitude": -33.512345, "Longitude": -25.098765, "HDOP": 1.4}
    resolver = make_gps_resolver(options, harness, agent, {"gps": bettercap_fix})

    resolver.refresh_gpsd()

    assert resolver.current()["source"] == "bettercap"


def test_a_bad_gpsd_port_does_not_stop_the_plugin_from_starting(full_options, harness):
    """SPEC 2.2.1's standing rule, restated for this specific refusal: a
    misconfigured gpsd_port must degrade the gpsd source, never take the
    plugin down.
    """
    harness.local_ipv4 = [LOOPBACK]
    config = dict(full_options)
    config["gpsd_port"] = "anything"

    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = config
    try:
        plugin.on_loaded()

        assert plugin._router is not None
        plugin._listeners.reconcile()
    finally:
        plugin.on_unload()
