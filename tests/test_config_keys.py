"""SPEC 2.2.1: an unknown configuration key is named, not ignored (issue #137).

Before this rule, a misspelled key was invisible: `option()`/`self.options.get`
never found it, fell back to the hardcoded default, and the unit ran on a
value its owner never wrote - the SPEC's own example is `bind_address`,
singular, silently replacing `bind_addresses` and leaving an address the
owner had deliberately added unbound, with nothing in the log to point at the
letter that was missing.

The rule (SPEC 2.2.1): at `on_loaded`, every key present under
`[main.plugins.companion]` that the plugin does not accept is logged **once**
at WARNING, naming the key and the accepted set. The accepted set is
`DEFAULTS` plus `enabled` - F32 pins that `plugin.options` is the
`config.toml` table assigned wholesale, with `enabled` the single exception
because `toggle_plugin` writes and persists it, so it is the only key
pwnagotchi itself injects. `interfaces` is excluded from this warning
because it already has a more specific one of its own (SPEC 2.3.1, naming
`bind_addresses` as the replacement); saying it twice would read as two
problems instead of one. The plugin never refuses to start over this - it
always comes up, on its defaults.

Every test here drives `Companion` through its real `on_loaded`/`on_ready`
lifecycle rather than reaching into a helper directly, the same shape
`tests/test_hooks.py`'s `hooked` fixture and `tests/test_broadcast_cadence.py`'s
`wired_plugin_factory` use, because the rule is stated in terms of what
`on_loaded` does and nothing here is about the broadcast cadence or the hook
surface those two files already own.
"""

from __future__ import annotations

import re

import pytest

from plugin import companion


def warnings_mentioning(caplog: pytest.LogCaptureFixture, key: str) -> list:
    """WARNING records whose message names `key` as a whole word.

    Word-bounded on purpose: `bind_address` is a literal prefix of the real
    key `bind_addresses`, so a plain substring search could not tell "the
    typo was named" from "the real key merely appeared in the same message"
    (SPEC 2.2.1 says the accepted set is named in the warning too).
    """
    pattern = re.compile(rf"\b{re.escape(key)}\b")
    return [
        record
        for record in caplog.records
        if record.levelname == "WARNING" and pattern.search(record.getMessage())
    ]


@pytest.fixture
def full_options(tls_material, tmp_path) -> dict:
    """The complete, valid SPEC 2.2 configuration block, `enabled` included.

    Built from `companion.DEFAULTS` itself, not from the conftest `options`
    fixture: that fixture is a hand-maintained copy of the SPEC 2.2 table for
    other files' convenience, and a key added to `DEFAULTS` without a
    matching edit there would leave it out of this dict too - which would
    make `test_a_fully_valid_configuration_logs_no_warning`, the strictest
    assertion in this file, pass without ever exercising that key. Reading
    `companion.DEFAULTS` is reading a data table, not the implementation.

    Real TLS material: without it SPEC D4 refuses every listener, which would
    make "the plugin still comes up" untestable for the wrong reason. Callers
    copy this dict and add, remove or rename keys to build the scenario under
    test; it is never mutated in place, since it is a fixture shared across
    the whole module.
    """
    return {
        **companion.DEFAULTS,
        "enabled": True,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }


@pytest.fixture
def load_plugin(harness, agent):
    """Takes a `Companion` through `on_loaded`/`on_ready` on a given options dict.

    Reduced from `wired_plugin_factory`/`hooked` to what this file needs: the
    caller supplies the *complete* options dict (there is nothing here to
    merge overrides onto, since every test in this file is specifically about
    what is and is not in that dict).
    """
    built: list[companion.Companion] = []

    def _load(config_options: dict) -> companion.Companion:
        plugin = companion.Companion()
        plugin.deps = harness.deps
        plugin.options = dict(config_options)
        plugin.on_loaded()
        assert plugin._router is not None, (
            "the plugin did not come up - SPEC 2.2.1 requires it to start "
            "normally on its defaults, never refusing over a bad key"
        )
        plugin.on_ready(agent)
        built.append(plugin)
        return plugin

    yield _load

    for plugin in built:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# The rule itself
# ---------------------------------------------------------------------------


def test_unknown_key_is_named_once_at_warning(load_plugin, full_options, caplog):
    config = dict(full_options)
    config["frobnicate_level"] = "high"

    with caplog.at_level("WARNING"):
        plugin = load_plugin(config)

    matches = warnings_mentioning(caplog, "frobnicate_level")
    assert len(matches) == 1, (
        f"expected exactly one WARNING naming the unknown key, got {matches}"
    )
    assert plugin._router is not None


def test_two_unknown_keys_are_both_named_in_the_same_record(
    load_plugin, full_options, caplog
):
    """A single unknown key is not enough to catch an implementation that
    reports only the first offender and silently drops the rest. Two unknown
    keys must both be named, and SPEC 2.2.1's "every key ... is logged once"
    is satisfied here by one shared record naming both, the same shape the
    plugin uses for one key - not two separate records, one per key.
    """
    config = dict(full_options)
    config["frobnicate_level"] = "high"
    config["another_bogus_key"] = "low"

    with caplog.at_level("WARNING"):
        load_plugin(config)

    first = warnings_mentioning(caplog, "frobnicate_level")
    second = warnings_mentioning(caplog, "another_bogus_key")
    assert len(first) == 1, f"expected 'frobnicate_level' named once, got {first!r}"
    assert len(second) == 1, f"expected 'another_bogus_key' named once, got {second!r}"
    assert first == second, (
        "both unknown keys must be named in the same warning record, not "
        f"two separate ones ({first!r} vs {second!r})"
    )


def test_the_accepted_set_is_named_in_the_warning(load_plugin, full_options, caplog):
    """SPEC 2.2.1: "The warning names the unknown key and the accepted set,
    and lets the reader do the matching" - deliberately no near-match
    suggestion, because a wrong guess sends the reader to fix a key that was
    never the problem. The accepted set is `DEFAULTS` plus `enabled` (F32);
    every one of those names must appear in the same record as the unknown
    key.
    """
    config = dict(full_options)
    config["frobnicate_level"] = "high"

    with caplog.at_level("WARNING"):
        load_plugin(config)

    matches = warnings_mentioning(caplog, "frobnicate_level")
    assert len(matches) == 1, f"expected one warning record, got {matches!r}"
    message = matches[0].getMessage()

    accepted = set(companion.DEFAULTS) | {"enabled"}
    missing = sorted(
        name for name in accepted if not re.search(rf"\b{re.escape(name)}\b", message)
    )
    assert not missing, (
        f"the warning must name the full accepted set; missing {missing} "
        f"from: {message!r}"
    )


def test_unknown_key_warning_is_not_repeated_by_the_rebind_loop(
    load_plugin, full_options, harness, caplog
):
    """A regression guard, not evidence of the "once" property itself - that
    evidence is `test_unknown_key_is_named_once_at_warning`. This test
    cannot fail against any implementation that keeps the scan in
    `on_loaded` and never gives `Listeners.reconcile()` a path to it, which
    is the only shape SPEC 2.2.1 currently describes. It exists so that if
    the scan is ever moved into, or duplicated into, the `rebind_interval`
    loop, something here notices before a unit logs the same warning on
    every pass.
    """
    config = dict(full_options)
    config["frobnicate_level"] = "high"
    harness.local_ipv4 = []

    with caplog.at_level("WARNING"):
        plugin = load_plugin(config)
        after_load = len(warnings_mentioning(caplog, "frobnicate_level"))
        plugin._listeners.reconcile()
        plugin._listeners.reconcile()
        plugin._listeners.reconcile()

    final = len(warnings_mentioning(caplog, "frobnicate_level"))
    assert after_load == 1
    assert final == 1, (
        "the unknown-key warning must fire once, at on_loaded, not once per "
        f"reconcile pass (count after 3 extra passes: {final})"
    )


def test_enabled_never_warns(load_plugin, full_options, caplog):
    """`enabled` is the one key pwnagotchi itself injects: F32 pins that
    `plugin.options` is `config.toml`'s table assigned wholesale, and that
    `toggle_plugin` is what writes and persists `enabled`. SPEC 2.2.1 is
    explicit that warning on it would be a false positive on every unit, at
    every boot."""
    with caplog.at_level("WARNING"):
        plugin = load_plugin(full_options)  # already carries enabled=True

    assert warnings_mentioning(caplog, "enabled") == []
    assert plugin._router is not None


def test_interfaces_is_not_also_reported_as_unknown(load_plugin, full_options, caplog):
    """`interfaces` already has its own, more specific warning (SPEC 2.3.1,
    naming `bind_addresses` as the replacement). It must not also collect the
    generic unknown-key warning - one problem, one message."""
    config = dict(full_options)
    config["interfaces"] = ["wlan0"]

    with caplog.at_level("WARNING"):
        plugin = load_plugin(config)

    matches = warnings_mentioning(caplog, "interfaces")
    assert len(matches) == 1, (
        "interfaces must produce exactly one warning (its own §2.3.1 one), "
        f"not this generic one as well; got {matches}"
    )
    assert plugin._router is not None


def test_a_fully_valid_configuration_logs_no_warning(load_plugin, full_options, caplog):
    """The assertion that stops the rule becoming noise: a block with nothing
    but accepted keys must log nothing at WARNING at all. A rule that warns on
    a correct configuration is worse than the silence it replaces."""
    with caplog.at_level("WARNING"):
        plugin = load_plugin(full_options)

    warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warning_records == [], (
        "a configuration block with only accepted keys must log nothing at "
        f"WARNING; got {[r.getMessage() for r in warning_records]}"
    )
    assert plugin._router is not None


# ---------------------------------------------------------------------------
# Three keys withdrawn rather than implemented (issue #156)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("save_gps_log", False),
        ("gps_log_path", "/tmp/gps.log"),
        ("mirror_auto_interval", 5),
    ],
)
def test_a_withdrawn_key_is_named_as_unknown(load_plugin, full_options, caplog, key, value):
    """SPEC 2.2's own words: a config carrying one of these three keys "is a
    config asking for something this plugin does not do", and §2.2.1 now
    names it as unknown - the same generic warning a misspelling gets, not a
    silent no-op and not a dedicated message of its own (unlike `interfaces`,
    which SPEC 2.2.1 explicitly keeps out of this path because it already has
    a more specific warning). `full_options` is built from `companion.DEFAULTS`
    itself (see its docstring above), so this key is genuinely absent from it
    before being added here - the same guarantee that lets
    `test_a_fully_valid_configuration_logs_no_warning` assert silence.
    """
    config = dict(full_options)
    config[key] = value

    with caplog.at_level("WARNING"):
        plugin = load_plugin(config)

    matches = warnings_mentioning(caplog, key)
    assert len(matches) == 1, (
        f"a config carrying the withdrawn key {key!r} must be named as an "
        f"unknown key exactly once (SPEC 2.2.1); got {matches}"
    )
    assert plugin._router is not None


@pytest.mark.parametrize(
    "key", ["save_gps_log", "gps_log_path", "mirror_auto_interval"]
)
def test_a_withdrawn_key_is_absent_from_the_accepted_set(key):
    """Checking `DEFAULTS` alone would pass for the wrong reason: an
    implementation that deleted the key from `DEFAULTS` but left it (or a
    private copy of it) in the accepted set used by the §2.2.1 scan would
    still warn nobody and this suite would never notice. Rebuilding
    `set(companion.DEFAULTS) | {"enabled"}` here is the same mistake by
    another name: given the `key not in companion.DEFAULTS` assertion above,
    a set built from `DEFAULTS` itself cannot contain `key` either, so that
    second assertion could not fail for the reason this test names - it is
    checked against `companion.ACCEPTED_CONFIG_KEYS`, the set the §2.2.1
    scan actually consults, so a private copy of the withdrawn key kept
    there (and nowhere near `DEFAULTS`) is exactly what this test catches.
    """
    assert key not in companion.DEFAULTS
    assert key not in companion.ACCEPTED_CONFIG_KEYS


# ---------------------------------------------------------------------------
# The scan runs even when the plugin then refuses to start
# ---------------------------------------------------------------------------


def test_unknown_key_is_still_named_when_tls_material_is_unusable(
    full_options, harness, bind_recorder, caplog, tmp_path
):
    """SPEC 2.6/2.15: unusable TLS material makes `on_loaded` start no
    listener at all and return early. SPEC 2.2.1's unknown-key scan is
    unconditional at load, so it must still fire in that case - a compound
    failure the rule exists for, since the misspelling is often exactly what
    made the TLS material look wrong (a typo'd `tls_cert`/`tls_key` would
    produce this same shape). This does not use `load_plugin`: that fixture
    asserts the plugin came up, which is the opposite of what this test
    needs, so `Companion` is driven directly, the way
    `tests/test_tls_startup.py` does for the same refusal.
    """
    config = dict(full_options)
    config["frobnicate_level"] = "high"
    config["tls_cert"] = str(tmp_path / "absent.crt")

    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = config
    try:
        with caplog.at_level("WARNING"):
            plugin.on_loaded()

        assert plugin._router is None, (
            "unusable TLS material must make on_loaded refuse to start "
            "(SPEC 2.6/2.15), which is the premise of this test"
        )
        assert bind_recorder.addresses == [], (
            "no plaintext fallback, no listener at all"
        )

        matches = warnings_mentioning(caplog, "frobnicate_level")
        assert len(matches) == 1, (
            "the unknown-key warning (SPEC 2.2.1) must still fire even when "
            f"the plugin then refuses to start over unusable TLS material; "
            f"got {matches}"
        )
    finally:
        plugin.on_unload()


# ---------------------------------------------------------------------------
# The motivating misspelling, by name (issue #137)
# ---------------------------------------------------------------------------


def test_bind_address_typo_is_named_and_the_real_default_still_governs_binding(
    load_plugin, full_options, harness, bind_recorder, caplog
):
    """`bind_address`, singular, in place of `bind_addresses` is the exact
    case SPEC 2.2.1 calls out by name: §2.3 makes `bind_addresses` the whole
    security model, so a typo there is silently a different security posture,
    in both directions.

    This asserts both halves of the ticket at once: the typo is named, and -
    the second acceptance criterion, not just "no exception" - the real
    default `bind_addresses` (SPEC 2.2's `["172.20.10.0/28", "10.0.0.2"]`)
    still governs which address gets bound, with the bogus `bind_address` key
    sitting unused in the same block. `harness.local_ipv4` is set to
    `10.0.0.2`, one of the two real defaults; `bind_recorder` watches the
    actual `socket.bind` call, so this cannot be satisfied by bookkeeping
    that merely reports the right address. The bind itself is expected to
    fail - `10.0.0.2` is not actually configured on the machine running this
    suite - which is the OSError row of SPEC 2.3.1's reconciliation table:
    logged and retried, never a crash, so `reconcile()` running to completion
    here is itself part of the assertion.
    """
    config = dict(full_options)
    del config["bind_addresses"]
    # RFC 5737 documentation range: unrelated to any real network, and
    # unmistakably so. Must never be consulted.
    config["bind_address"] = ["198.51.100.0/24"]
    harness.local_ipv4 = ["10.0.0.2"]

    with caplog.at_level("WARNING"):
        plugin = load_plugin(config)
        plugin._listeners.reconcile()

    matches = warnings_mentioning(caplog, "bind_address")
    assert len(matches) == 1, (
        f"expected exactly one warning naming the typo'd key, got {matches}"
    )

    assert "10.0.0.2" in bind_recorder.hosts, (
        "the real bind_addresses default (SPEC 2.2) must govern binding even "
        "with an unrelated, unknown 'bind_address' key present in the same "
        "block; the typo'd key must never be consulted for anything"
    )
    bind_recorder.assert_no_wildcard()
