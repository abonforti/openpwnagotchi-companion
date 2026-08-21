"""Battery detection.

Three tiers, each one guarded: the `pisugarx_ext` plugin, then `pisugarx`, then a
direct I2C read. The plugin attribute names are deliberately **not** pinned in
SPEC section 11 - they belong to third-party plugins nobody here controls - so
they are probed with `getattr` over a candidate list and every miss is treated as
"unavailable". They are never imported (SPEC 11.1).

The binding rule is the last one: no tier may raise. A missing battery reading is
a null on the wire, never an exception into the asyncio loop.
"""

from __future__ import annotations

import pytest

from plugin import companion

NO_BATTERY = {"percent": None, "charging": None}


# The nine seams of SPEC 10.7, in the order conftest injects them. A `Deps` is
# built by constructor injection - SPEC 10.7 forbids monkeypatching module
# internals - so replacing one seam means naming the other eight. Mirrors
# tests/test_binding.py's DEPS_SEAMS/deps_with_enumeration, which is where this
# pattern is proven; every use here only ever overrides read_pisugar_i2c.
DEPS_SEAMS = (
    "now",
    "restart_pwnagotchi",
    "reboot_device",
    "shutdown_device",
    "run_command",
    "read_gpsd",
    "read_pisugar_i2c",
    "list_local_ipv4",
    "spawn",
)


def deps_with_pisugar(base, read_pisugar_i2c):
    """`base` with its I2C read replaced and every other seam kept."""
    seams = {name: getattr(base, name) for name in DEPS_SEAMS}
    seams["read_pisugar_i2c"] = read_pisugar_i2c
    return companion.Deps(**seams)


class FakeProvider:
    """A stand-in for a PiSugar plugin instance in `plugins.loaded`."""

    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


class HostileProvider:
    """A provider whose attributes raise. Third-party code does this."""

    @property
    def percentage(self):
        raise RuntimeError("the I2C bus is on fire")

    @property
    def charging(self):
        raise RuntimeError("still on fire")


def read(options, harness):
    return companion.BatteryReader(options, harness.deps).read()


# ---------------------------------------------------------------------------
# Tier one and two: the plugins
# ---------------------------------------------------------------------------


def test_pisugarx_ext_is_read_first(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(percentage=87.5, charging=True)
    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage=12.0, charging=False)

    assert read(options, harness) == {"percent": 87.5, "charging": True}


def test_pisugarx_is_read_when_the_ext_plugin_is_absent(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage=41.0, charging=False)

    assert read(options, harness) == {"percent": 41.0, "charging": False}


@pytest.mark.parametrize("attribute", companion.BatteryReader.PERCENT_ATTRS)
def test_every_percentage_candidate_is_probed(options, harness, stub_plugins, attribute):
    stub_plugins.loaded["pisugarx"] = FakeProvider(**{attribute: 55.0})

    assert read(options, harness)["percent"] == 55.0


@pytest.mark.parametrize("attribute", companion.BatteryReader.CHARGING_ATTRS)
def test_every_charging_candidate_is_probed(options, harness, stub_plugins, attribute):
    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage=50.0, **{attribute: True})

    assert read(options, harness)["charging"] is True


def test_a_provider_with_no_recognisable_attribute_falls_through(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx"] = FakeProvider(some_other_name=99)
    harness.pisugar_reply = (33.0, False)

    assert read(options, harness) == {"percent": 33.0, "charging": False}


def test_a_provider_whose_attributes_raise_falls_through(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx_ext"] = HostileProvider()
    harness.pisugar_reply = (33.0, False)

    assert read(options, harness) == {"percent": 33.0, "charging": False}


def test_a_non_numeric_percentage_is_not_reported(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage="not a number")

    assert read(options, harness) == NO_BATTERY


def test_the_providers_are_never_imported(options, harness, stub_plugins):
    # There is no importable pisugarx module in the stub; if the implementation
    # imported one instead of going through plugins.loaded, this would fail on a
    # real unit only when the plugin happened to be absent.
    import importlib

    for name in ("pisugarx", "pisugarx_ext", "pasv_mode"):
        with pytest.raises(ImportError):
            importlib.import_module(name)

    assert read(options, harness) == NO_BATTERY


# ---------------------------------------------------------------------------
# Tier three: I2C
# ---------------------------------------------------------------------------


def test_the_i2c_read_is_the_last_resort(options, harness):
    harness.pisugar_reply = (76.0, True)

    assert read(options, harness) == {"percent": 76.0, "charging": True}
    assert "read_pisugar_i2c" in harness.names()


def test_the_i2c_read_is_skipped_when_pisugar_is_disabled(options, harness):
    options["pisugar"] = False
    harness.pisugar_reply = (76.0, True)

    assert read(options, harness) == NO_BATTERY
    assert "read_pisugar_i2c" not in harness.names()


def test_a_loaded_provider_means_no_i2c_read(options, harness, stub_plugins):
    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage=50.0, charging=False)

    read(options, harness)

    assert "read_pisugar_i2c" not in harness.names()


def test_an_i2c_read_that_answers_nothing_yields_nulls(options, harness):
    harness.pisugar_reply = (None, None)

    assert read(options, harness) == NO_BATTERY


def test_an_i2c_read_that_raises_yields_nulls(options, harness):
    def exploding():
        raise OSError("no such device on bus 1")

    deps = deps_with_pisugar(harness.deps, exploding)

    assert companion.BatteryReader(options, deps).read() == NO_BATTERY


def test_a_partial_i2c_answer_is_reported_as_far_as_it_goes(options, harness):
    harness.pisugar_reply = (64.0, None)

    assert companion.BatteryReader(options, harness.deps).read() == {
        "percent": 64.0,
        "charging": None,
    }


# ---------------------------------------------------------------------------
# Every tier failing
# ---------------------------------------------------------------------------


def test_no_provider_at_all_is_nulls_and_no_exception(options, harness):
    assert read(options, harness) == NO_BATTERY


def test_the_reader_is_reusable(options, harness, stub_plugins):
    reader = companion.BatteryReader(options, harness.deps)

    assert reader.read() == NO_BATTERY

    stub_plugins.loaded["pisugarx"] = FakeProvider(percentage=20.0, charging=True)

    assert reader.read() == {"percent": 20.0, "charging": True}


def test_battery_reaches_the_stats_payload(router, harness):
    harness.pisugar_reply = (76.0, True)

    assert router.stats()["battery"] == {"percent": 76.0, "charging": True}


# ---------------------------------------------------------------------------
# capabilities.pisugar: a provider has answered when either field arrives
# ---------------------------------------------------------------------------
#
# SPEC 2.11.2: "A provider has answered when either field arrives." The flag
# records whether any tier ever produced a reading, and since 2.11.2 a direct
# read can produce a charging bit with no percentage: a byte that cannot be a
# percentage is a successful read of a bad value, and the charging bit came off
# a different register. That reading is a PiSugar, however poorly it is
# behaving.
#
# Until this section existed, no test anywhere supplied a reading with a null
# percentage and a non-null charging bit: every fixture gave both or neither.
# So the `or` in the condition short-circuited on every input the suite had,
# the rule was fully covered and entirely unverified, and reverting it to the
# old `percent is not None` left the gate green. These are the inputs that
# separate the two.


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        ((None, True), True),
        ((None, False), True),
        ((76.0, None), True),
        ((76.0, False), True),
        ((0.0, None), True),
        ((None, None), False),
    ],
    ids=[
        "charging-only-true",
        "charging-only-false",
        "percent-only",
        "both",
        "flat-battery-only",
        "nothing",
    ],
)
def test_a_provider_has_answered_when_either_field_arrives(
    options, harness, reading, expected
):
    """SPEC 2.11.2, at the I2C tier. `available` is null-ness, not truthiness.

    `charging-only-false` is the row that does the work twice over. An
    implementation testing `percent is not None` passes `charging-only-true` on
    nothing but luck and fails here for the same reason; an implementation
    testing `if percent or charging` passes `charging-only-true` and fails here,
    because `False` is an answer and `0.0` percent is a reading. The cable being
    out is something the PiSugar told us.
    """
    harness.pisugar_reply = reading
    reader = companion.BatteryReader(options, harness.deps)

    assert reader.available is False, "nothing has been read yet"

    percent, charging = reading
    assert reader.read() == {"percent": percent, "charging": charging}
    assert reader.available is expected, (
        f"a reading of {reading!r} means a provider {'answered' if expected else 'did not'}; "
        f"capabilities.pisugar would be {reader.available!r}"
    )


def test_a_provider_that_answered_once_stays_available_afterwards(options, harness):
    """`available` is "has ever answered", not "answered last time".

    A PiSugar that reports its charging bit and then drops off the bus - the
    unit going into a firmware update, or a loose header - must not make the
    client's battery panel appear and disappear. The first reading here has no
    percentage at all, which is the case 2.11.2 added and the one an
    implementation is most likely to forget to latch.
    """
    reader = companion.BatteryReader(options, harness.deps)

    harness.pisugar_reply = (None, True)
    reader.read()
    assert reader.available is True

    harness.pisugar_reply = (None, None)
    assert reader.read() == NO_BATTERY
    assert reader.available is True, "the provider answered once; that does not un-happen"


def test_a_disabled_pisugar_never_becomes_available(options, harness):
    """`pisugar: false` skips the I2C tier entirely (SPEC 2.11), so there is no
    reading to have answered - even though the seam, if it were called, would
    hand back a charging bit."""
    options["pisugar"] = False
    harness.pisugar_reply = (None, True)
    reader = companion.BatteryReader(options, harness.deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False
    assert "read_pisugar_i2c" not in harness.names()


def test_an_i2c_read_that_raises_leaves_the_provider_unanswered(options, harness):
    """SPEC 2.11.2's transport rule reaching the capability flag: a seam that
    raises is not a provider that answered, and the guard that swallows the
    exception must not fall through into marking one."""

    def exploding():
        raise OSError("no such device on bus 1")

    deps = deps_with_pisugar(harness.deps, exploding)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False


@pytest.mark.parametrize(
    ("attributes", "expected_reading", "expected_available"),
    [
        ({"percentage": 41.0, "charging": False}, {"percent": 41.0, "charging": False}, True),
        ({"charging": True}, {"percent": None, "charging": True}, True),
        ({"charging": False}, {"percent": None, "charging": False}, True),
        ({"percentage": 41.0}, {"percent": 41.0, "charging": None}, True),
        ({"some_other_name": 99}, {"percent": None, "charging": None}, False),
    ],
    ids=["both", "charging-only-true", "charging-only-false", "percent-only", "unrecognisable"],
)
def test_the_plugin_tier_answers_on_either_field_too(
    options, harness, stub_plugins, attributes, expected_reading, expected_available
):
    """SPEC 2.11.2 says the direct read "had no reason to be stricter" than the
    test tier 1 already applies, so this pins the tier-1 side of that sentence.

    A third-party plugin exposing a charge state and no level is exactly why the
    rule is worded around either field, and `charging-only-false` is again the
    row that separates null-ness from truthiness. The `unrecognisable` row is
    the boundary: a provider object is present, nothing usable came off it, and
    that is not an answer - it falls through to the I2C tier, which here has
    nothing to say either.
    """
    stub_plugins.loaded["pisugarx"] = FakeProvider(**attributes)
    reader = companion.BatteryReader(options, harness.deps)

    assert reader.read() == expected_reading
    assert reader.available is expected_available


@pytest.mark.parametrize(
    ("reading", "expected"),
    [((None, True), True), ((None, False), True), ((None, None), False)],
    ids=["charging-only-true", "charging-only-false", "nothing"],
)
def test_capabilities_pisugar_follows_the_same_rule_on_the_wire(
    router_factory, agent, harness, reading, expected
):
    """The rule as the client sees it. `capabilities.pisugar` is what decides
    whether the PWA shows a battery panel at all, so a reading with a charging
    bit and no percentage must not hide the panel that would have shown the
    charge icon - which is the only thing that reading has to say.
    """
    harness.pisugar_reply = reading
    router = router_factory(agent)
    stats = router.stats()

    percent, charging = reading
    assert stats["battery"] == {"percent": percent, "charging": charging}
    assert router.capabilities()["pisugar"] is expected


# ---------------------------------------------------------------------------
# Per-field resolution (SPEC 2.11, "Resolution is per field")
# ---------------------------------------------------------------------------
#
# The rule these pin replaced a defect: resolution used to stop at the first
# tier that answered *either* field, so a tier-1 plugin exposing a charge state
# and no level made the level permanently unreachable even though the I2C tier
# below could read it off register 0x2A. SPEC now resolves each field on its
# own, with a constraint of equal weight: a tier is never consulted for a field
# already in hand, so the bus is touched only when there is something to gain.
#
# Every fake below is built from attribute names written out here, never from
# `BatteryReader.PERCENT_ATTRS` / `CHARGING_ATTRS`. A fake derived from the
# probe's own list can only ever assert that the probe finds what it was told to
# look for, which is why the defect above survived the rest of this file.


class CountingSeam:
    """A `read_pisugar_i2c` seam that counts calls and answers a fixed pair."""

    def __init__(self, reply=(None, None)):
        self.reply = reply
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.reply


class ForbiddenSeam:
    """A `read_pisugar_i2c` seam that must never be reached."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError(
            "the I2C bus was touched for a reading that was already in hand"
        )


class SpyProvider:
    """A provider that records every candidate attribute read off it."""

    def __init__(self, **attributes):
        object.__setattr__(self, "reads", [])
        for name, value in attributes.items():
            object.__setattr__(self, name, value)

    def __getattribute__(self, name):
        if not name.startswith("_") and name != "reads":
            object.__getattribute__(self, "reads").append(name)
        return object.__getattribute__(self, name)


def read_with(options, deps):
    return companion.BatteryReader(options, deps).read()


def test_a_pisugarx_ext_shaped_provider_answers_both_fields(options, harness, stub_plugins):
    """The names `pisugarx_ext` actually exposes, spelled out rather than taken
    from the candidate list (SPEC 2.11: "The candidate lists must contain what
    the plugins actually expose, which for `pisugarx_ext` is `battery_level` and
    `power_plugged`").

    This provider has neither `percentage` nor `charging` on it. On the
    reference unit that is the whole of tier 1, and if either name is missing
    from its list the device reports a null for a field the plugin is holding.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=64.0, power_plugged=True
    )
    reader = companion.BatteryReader(options, harness.deps)

    assert reader.read() == {"percent": 64.0, "charging": True}
    assert reader.available is True


def test_a_charge_state_alone_no_longer_hides_the_level(options, harness, stub_plugins):
    """The defect itself. Tier 1 has a charge state and no level; the I2C tier
    can read a level. The level must arrive, and the charge state must be the
    one tier 1 gave - not the one the same I2C transaction returned alongside
    the percentage.

    The two charging values differ on purpose. With them equal the test cannot
    tell a tier-1 value that survived from an I2C value that overwrote it.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(charging=True)
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}
    assert seam.calls == 1, "the missing percentage is worth exactly one bus transaction"


def test_a_level_alone_takes_the_charge_state_from_the_tier_below(
    options, harness, stub_plugins
):
    """The mirror image, and the one that catches an implementation that made
    only the percentage per-field. The percentages differ so a tier-1 level
    overwritten by the I2C one is visible."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=88.0)
    seam = CountingSeam((11.0, True))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 88.0, "charging": True}
    assert seam.calls == 1


def test_a_complete_tier_one_never_touches_the_i2c_bus(options, harness, stub_plugins):
    """SPEC 2.11: "A tier is never consulted for a field already in hand", so on
    a unit whose tier-1 plugin answers both fields the bus is not touched at
    all. Asserted on the seam, not inferred from the returned values: an
    implementation that reads the bus and then discards the answer returns the
    same dictionary and pays for an I2C transaction on every `get_stats`.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=64.0, power_plugged=False
    )
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": 64.0, "charging": False}
    assert seam.calls == 0
    assert reader.available is True


def test_the_second_plugin_supplies_only_what_the_first_left_missing(
    options, harness, stub_plugins
):
    """Per-field resolution runs between the two plugin tiers as well, not only
    between the plugins and the bus: `pisugarx_ext` holds the charge state,
    `pisugarx` holds the level, and neither tier is asked for the field the
    other already answered. Nothing reaches I2C.

    Asserted on what was read off the second provider and not only on the
    returned reading: it carries a `charging` of its own that disagrees with the
    first plugin's, and an implementation missing this guard reads it, discards
    it, and returns the same dictionary."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(charging=True)
    second = SpyProvider(battery_level=30.0, charging=False)
    stub_plugins.loaded["pisugarx"] = second
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 30.0, "charging": True}
    assert seam.calls == 0
    assert "battery_level" in second.reads, "the missing level is what it was consulted for"
    assert "charging" not in second.reads, (
        f"the charge state was already in hand and the second plugin was probed for it "
        f"anyway: {second.reads!r}"
    )


def test_a_percentage_that_is_not_a_number_still_descends_for_the_level(
    options, harness, stub_plugins
):
    """A field is "in hand" when a usable value came off the provider, not when
    an attribute of that name existed. A plugin holding a string where the level
    should be has not answered the level, so the tier below is consulted for it
    and the charge state it also answered is kept."""
    stub_plugins.loaded["pisugarx"] = FakeProvider(
        percentage="not a number", charging=True
    )
    seam = CountingSeam((50.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 50.0, "charging": True}


def test_a_bus_that_raises_leaves_the_tier_one_field_standing(
    options, harness, stub_plugins
):
    """SPEC 2.11.2's transport rule discards *the I2C reading*, both halves of
    it, and says nothing about a value another tier already supplied. A charge
    state from tier 1 survives a bus that will not answer."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(power_plugged=True)

    def exploding():
        raise OSError("no such device on bus 1")

    deps = deps_with_pisugar(harness.deps, exploding)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": None, "charging": True}
    assert reader.available is True, "tier 1 answered a field; that is a provider"


def test_a_field_no_tier_supplies_is_null(options, harness, stub_plugins):
    """SPEC 2.11: "A field that no tier supplies is `null`, which is the same
    answer it was before." Tier 1 has the charge state, the bus has nothing, and
    `available` is still true because a charging-only reading is a reading."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(charging=False)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": None, "charging": False}
    assert reader.available is True
    assert seam.calls == 1


def test_nothing_anywhere_is_two_nulls_and_no_capability(options, harness):
    """Tier 3 of SPEC 2.11, reached with every tier above it empty."""
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False


def test_pisugar_false_consults_no_tier_at_all(options, harness, stub_plugins):
    """`pisugar: false` short-circuits ahead of every tier: no attribute is read
    off a loaded plugin and the bus is not touched. The provider here would
    answer both fields, so a probe that ran would be visible in `reads`.
    """
    provider = SpyProvider(battery_level=64.0, power_plugged=True, charging=True)
    stub_plugins.loaded["pisugarx_ext"] = provider
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)
    options["pisugar"] = False
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False
    assert seam.calls == 0
    assert provider.reads == [], f"the disabled probe still read {provider.reads!r}"


# ---------------------------------------------------------------------------
# Candidate-list order (SPEC 2.11)
# ---------------------------------------------------------------------------
#
# "Within each list the order is by how precisely a name answers the question,
# not by which one was observed: a plugin exposing both `charging` and
# `power_plugged` means the two differently, and the first is the field this
# reports."


def test_charging_outranks_power_plugged(options, harness, stub_plugins):
    """The pair SPEC names outright. A plugin carrying both is saying that
    current is flowing and that a cable is present, which are different claims;
    the field this reports is `charging`. The values differ so the losing one is
    detectable."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=64.0, charging=False, power_plugged=True
    )

    assert read_with(options, harness.deps) == {"percent": 64.0, "charging": False}


def test_charging_outranks_plugged(options, harness, stub_plugins):
    """The same distinction under the shorter spelling."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=64.0, charging=True, plugged=False
    )

    assert read_with(options, harness.deps) == {"percent": 64.0, "charging": True}


@pytest.mark.parametrize("vaguer", ["battery", "level", "capacity"], ids=str)
def test_battery_level_outranks_the_vaguer_percentage_names(
    options, harness, stub_plugins, vaguer
):
    """`battery_level` names both the subject and the quantity; `battery`,
    `level` and `capacity` each name one of the two and leave the other to be
    guessed, so a plugin exposing `battery_level` alongside one of them is
    answered by `battery_level`. Test values differ so the loser is visible.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        **{"battery_level": 64.0, vaguer: 7.0}
    )

    assert read_with(options, harness.deps)["percent"] == 64.0


# ---------------------------------------------------------------------------
# What counts as a value, at any tier (SPEC 2.11)
# ---------------------------------------------------------------------------
#
# "An attribute that is absent, or present and `None`, or whose value will not
# convert to the field's type, is a miss and never an error. A candidate that is
# callable is called [...] a call that raises is a miss like any other. And a
# percentage outside 0-100 is not a percentage regardless of which tier produced
# it."
#
# The range rule is the one that changes behaviour: it used to live in the
# direct read alone, so the same byte was discarded off register 0x2A and
# rendered by the client when a plugin handed it over. A miss, though - a value
# rejected for range does not poison the reading, and resolution carries on to
# the candidate below it and then to the tier below that.


def raising_callable(exception=RuntimeError("the gauge is not talking")):
    def call():
        raise exception

    return call


def as_callable(value):
    return lambda: value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0.0),
        (100.0, 100.0),
        (64.0, 64.0),
        (100.1, None),
        (101.0, None),
        (255.0, None),
        (-0.1, None),
        (-1.0, None),
    ],
    ids=["flat", "full", "typical", "just-over", "over", "a-raw-byte", "just-under", "under"],
)
@pytest.mark.parametrize("wrap", [lambda v: v, as_callable], ids=["attribute", "callable"])
def test_a_tier_one_percentage_is_bounded_to_0_100(
    options, harness, stub_plugins, value, expected, wrap
):
    """The schema caps the field at 100 and 0 is a flat battery, not a missing
    one, so both ends are inclusive and both are values a real gauge produces.
    `a-raw-byte` is 255, the reading SPEC names: discarded off the register
    already, and now discarded off a plugin that hands over the same number.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=wrap(value))
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == expected


def test_an_out_of_range_percentage_does_not_stop_the_tier_below(
    options, harness, stub_plugins
):
    """The half of the rule an implementation that simply returns null would
    satisfy without: rejecting for range is a *miss*, so the level is still
    missing and the I2C tier is still consulted for it. The charge state tier 1
    did supply survives, and the one the same transaction returned does not.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=255.0, charging=True)
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}
    assert seam.calls == 1


def test_an_out_of_range_percentage_does_not_stop_the_candidate_below(
    options, harness, stub_plugins
):
    """A miss inside the candidate list is a miss like any other, so probing
    carries on to the next name on the same provider rather than settling the
    field. `battery_level` outranks `percentage` and here yields nothing usable,
    which leaves `percentage` to answer."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=255.0, percentage=64.0)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == 64.0


def test_an_out_of_range_percentage_with_nothing_below_is_null(
    options, harness, stub_plugins
):
    """And when no tier can supply a level, the client is told there is none
    rather than shown a plausible-looking lie."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=255.0, charging=True)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": None, "charging": True}


def test_a_callable_candidate_is_called(options, harness, stub_plugins):
    """SPEC 2.11: "A candidate that is callable is called, because some PiSugar
    plugins expose `get_battery_level()` rather than a plain attribute."

    Both fields come off callables, so an implementation that hands the bound
    method through as a value reports a function object where a float belongs -
    and would reach the wire, since neither field is then null.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=as_callable(64.0), power_plugged=as_callable(True)
    )
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": 64.0, "charging": True}
    assert seam.calls == 0
    assert reader.available is True


def test_a_callable_candidate_that_raises_is_a_miss(options, harness, stub_plugins):
    """"A call that raises is a miss like any other" - not an error, and not the
    end of resolution. The level is still missing, so the tier below supplies
    it, and the charge state tier 1 answered is kept."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=raising_callable(), charging=True
    )
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}


def test_a_callable_charging_candidate_that_raises_is_a_miss(
    options, harness, stub_plugins
):
    """The same on the other field, which has its own probe and its own
    conversion."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=88.0, power_plugged=raising_callable(OSError("bus gone"))
    )
    seam = CountingSeam((11.0, True))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 88.0, "charging": True}


def test_battery_level_outranks_percentage(options, harness, stub_plugins):
    """SPEC 2.11: precision does not separate these two, since both name the
    quantity exactly; the tie-break is that `battery_level` is what
    `pisugarx_ext` exposes, and that is the tier the reference unit reaches.
    The values differ so the loser is visible."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=64.0, percentage=7.0)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == 64.0


# ---------------------------------------------------------------------------
# The three edges of "what counts as a value" (SPEC 2.11)
# ---------------------------------------------------------------------------
#
# "Finite is load bearing: NaN and the infinities are not caught by asking
# whether a value is below zero or above one hundred, since every comparison
# against NaN is false, and a NaN that survives reaches json.dumps and is
# serialised as the literal NaN, which is not JSON and which a conformant client
# rejects. [...] A boolean is not a percentage, though True converts to 1.0 and
# sits happily in range [...] And a value refused for any of these reasons is a
# miss, so the next candidate and then the tier below still get their turn."
#
# "A refusal is not an answer": a provider whose only usable output was refused
# has supplied nothing, and capabilities.pisugar stays false on its account.

REFUSED_PERCENTAGES = [
    (float("nan"), "nan"),
    (float("inf"), "positive-infinity"),
    (float("-inf"), "negative-infinity"),
    (255.0, "a-raw-byte"),
    (-1.0, "under"),
    (101.0, "over"),
    (True, "true"),
    (False, "false"),
    ("255", "a-numeric-string-out-of-range"),
    ("nan", "a-string-that-converts-to-nan"),
    ("inf", "a-string-that-converts-to-infinity"),
    (10 ** 400, "an-integer-too-large-for-a-float"),
]
# The last three rows are where the two rules of SPEC 2.11 compose. A numeric
# string is accepted *as a way of arriving* - "a miss is a value that will not
# convert" - and then faces the rules about the quantity itself, which are
# finiteness and the range. `float("nan")` converts and is still not a
# percentage. Getting the first rule right and the second one only for values
# that arrived as numbers puts `NaN` on the wire by the longer route.

_refused = pytest.mark.parametrize(
    "value",
    [pytest.param(value, id=name) for value, name in REFUSED_PERCENTAGES],
)
_pathway = pytest.mark.parametrize(
    "wrap", [lambda v: v, as_callable], ids=["attribute", "callable"]
)


@_pathway
@_refused
def test_a_refused_percentage_is_never_reported(options, harness, stub_plugins, value, wrap):
    """`nan` is the row this exists for. Every comparison against it is false,
    so a bound written as "below zero or above one hundred" lets it through, and
    `json.dumps` renders it as the bare literal `NaN` - not JSON, and rejected
    by a client that validates against `common.json`. The infinities go the same
    way through `Infinity`.

    `true` and `false` are the other careless pass: `True` converts to `1.0` and
    sits inside any range check, so a charge flag read into the level field
    arrives at the client as a battery at 1%.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=wrap(value))
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] is None


@_pathway
@_refused
def test_a_refused_percentage_still_lets_the_tier_below_answer(
    options, harness, stub_plugins, value, wrap
):
    """A refusal is a miss, not a reading that is later nulled. If it ended
    resolution the level would be unreachable on a unit whose plugin reports
    nonsense, which is the defect the per-field rule removes."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=wrap(value), charging=True
    )
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}
    assert seam.calls == 1


@_pathway
@_refused
def test_a_refused_percentage_still_lets_the_next_candidate_answer(
    options, harness, stub_plugins, value, wrap
):
    """And the next name on the same provider gets its turn before the tier
    below does."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=wrap(value), percentage=64.0
    )
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == 64.0


@_refused
def test_a_refusal_is_not_an_answer(options, harness, stub_plugins, value):
    """SPEC 2.11: "A tier-1 provider whose only usable output was refused has
    supplied nothing, so `capabilities.pisugar` stays false on its account."

    The provider is present and the attribute is there, which is exactly the
    shape an implementation latches `available` on before it has decided whether
    the value survives. Nothing below answers either, so a true here would put a
    battery panel in front of the user with two nulls behind it.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=value)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False


@_refused
def test_a_refusal_alongside_a_charge_state_is_still_an_answer(
    options, harness, stub_plugins, value
):
    """The other side of the same sentence, and the one that keeps it from
    contradicting 2.11.2: there the charging bit is the answer, and it is here
    too. The refused level does not take it down with it."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=value, power_plugged=False
    )
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": None, "charging": False}
    assert reader.available is True


@_refused
def test_a_refusal_rescued_by_the_tier_below_is_an_answer(
    options, harness, stub_plugins, value
):
    """`available` follows what was supplied, not where the refusal happened:
    tier 1 supplied nothing, the bus supplied a level, so a provider answered."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=value)
    seam = CountingSeam((42.0, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": 42.0, "charging": None}
    assert reader.available is True


def test_a_refused_percentage_never_reaches_the_wire(router_factory, agent, stub_plugins):
    """The consequence, at the boundary that matters. `json.dumps` writes `NaN`
    as a bare literal, so a level that survived the probe would leave the plugin
    as a document no conformant client can parse - and the client would have
    nothing to fall back on, because the same message carries the capability
    flag that would have hidden the panel."""
    import json

    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=float("nan"))
    router = router_factory(agent)
    stats = router.stats()

    assert stats["battery"] == NO_BATTERY
    assert router.capabilities()["pisugar"] is False

    encoded = json.dumps(stats)
    assert "NaN" not in encoded and "Infinity" not in encoded
    assert json.loads(encoded)["battery"] == NO_BATTERY


# ---------------------------------------------------------------------------
# A value that converts is accepted (SPEC 2.11)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("87", 87.0), ("87.5", 87.5), ("0", 0.0), ("100", 100.0)],
    ids=["integral", "fractional", "flat", "full"],
)
@_pathway
def test_a_numeric_string_percentage_is_accepted(
    options, harness, stub_plugins, value, expected, wrap
):
    """SPEC 2.11: ""87" from a third-party plugin is 87, and the existing clause
    above already decides it: a miss is a value that *will not* convert."

    The tier is deliberately forgiving about shapes nobody here controls, and a
    plugin reporting its level as text is careless rather than wrong. This is
    not in tension with the bool refusal: a bool converts to a number *inside*
    the range and is then indistinguishable from a real reading, where a numeric
    string converts to the right number.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=wrap(value))
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read()["percent"] == expected
    assert reader.available is True, "a value that converts is a provider answering"


def test_a_numeric_string_settles_the_field_and_stops_the_descent(
    options, harness, stub_plugins
):
    """Accepted means accepted: the level is in hand, so the candidate below it
    does not answer and neither does the tier below that."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level="87", percentage=7.0, power_plugged=True
    )
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 87.0, "charging": True}
    assert seam.calls == 0


def test_a_string_that_will_not_convert_is_still_a_miss(options, harness, stub_plugins):
    """The other half of the same clause, and the boundary of the leniency: the
    tier is forgiving about the shape a number arrives in, not about whether one
    arrived. The level is still missing, so the tier below supplies it."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level="eighty-seven", charging=True
    )
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}


# ---------------------------------------------------------------------------
# `available` latches (SPEC 2.11)
# ---------------------------------------------------------------------------


@_refused
def test_available_does_not_un_latch_when_a_later_read_is_refused(
    options, harness, stub_plugins, value
):
    """"Has ever answered" does not un-happen. A plugin that reported a level
    and then starts reporting nonsense - a firmware update mid-flight, a gauge
    that browns out - must not make the battery panel disappear from the
    client, because the capability flag is about whether this unit has a PiSugar
    and the unit still does.

    The refusal is the interesting trigger rather than a plain absence: an
    implementation that recomputes the flag from the reading it is about to
    return gets the plain absence right by accident, since `available` and the
    reading agree there, and gets this wrong.
    """
    provider = FakeProvider(battery_level=64.0)
    stub_plugins.loaded["pisugarx_ext"] = provider
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read()["percent"] == 64.0
    assert reader.available is True

    provider.battery_level = value

    assert reader.read() == NO_BATTERY
    assert reader.available is True, "the provider answered once; that does not un-happen"


def test_the_capability_flag_stays_true_on_the_wire_after_a_refusal(
    router_factory, agent, stub_plugins
):
    """The same latch as the client sees it, since `capabilities.pisugar` is
    what decides whether the panel exists at all."""
    provider = FakeProvider(battery_level=64.0)
    stub_plugins.loaded["pisugarx_ext"] = provider
    router = router_factory(agent)

    assert router.stats()["battery"]["percent"] == 64.0
    assert router.capabilities()["pisugar"] is True

    provider.battery_level = float("nan")

    assert router.stats()["battery"] == NO_BATTERY
    assert router.capabilities()["pisugar"] is True


def test_the_second_plugin_is_not_probed_for_the_level_the_first_supplied(
    options, harness, stub_plugins
):
    """The mirror of test_the_second_plugin_supplies_only_what_the_first_left_missing.

    There the first plugin leaves the percentage missing; here it leaves the
    charge state missing, so the guard being exercised with a value already in
    hand is the other one of the pair. SPEC 2.11's constraint is symmetric - "a
    tier is never consulted for a field already in hand" names no field - and an
    implementation with a guard per field can easily have one of them and not
    the other.

    Asserted on what was read off the second provider, not on the returned
    dictionary: an implementation that probes both fields and then discards the
    redundant answer returns exactly the same reading and is doing the thing the
    constraint forbids. Here that costs only wasted attribute reads, but on the
    tier below the same mistake is an I2C transaction on every `get_stats`.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=88.0)
    second = SpyProvider(battery_level=7.0, charging=True)
    stub_plugins.loaded["pisugarx"] = second
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 88.0, "charging": True}
    assert seam.calls == 0
    assert "charging" in second.reads, "the missing charge state is what it was consulted for"
    assert "battery_level" not in second.reads, (
        f"the level was already in hand and the second plugin was probed for it anyway: "
        f"{second.reads!r}"
    )


# ---------------------------------------------------------------------------
# The shape of the real plugin object (SPEC 2.11 tier 1, F29)
# ---------------------------------------------------------------------------
#
# "The values are not on the plugin object. `plugins.loaded['pisugarx_ext']` is
# a `PiSugar(plugins.Plugin)` that holds its hardware client at `self.ps`, and
# the battery state lives on that client (F29). So each candidate is looked for
# on the plugin and then on `plugin.ps`, and on the reference unit it is found
# in the second place, as the methods `get_battery_level()` and
# `get_battery_power_plugged()`."
#
# Every other fake in this file is a flat object, which is why a probe reading
# only the plugin object passed all of them and would still have read nothing on
# the reference unit. The shapes below are written out from F29 rather than
# derived from anything in the module.


class FakeClient:
    """A stand-in for `PiSugarServer`, the object at `plugin.ps`.

    `ready` is never defaulted here: `PiSugarServer.__init__` starts it `False`
    and nothing sets it back once it is `True` (F29), so a fake standing in for
    a connected device must say so explicitly, exactly as a fake standing in for
    one that never found its hardware must leave it unset or say `False` itself.
    """

    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


class FakePluginWithClient:
    """A stand-in for `PiSugar(plugins.Plugin)`: a client and its own attributes.

    With no attributes of its own this is the reference unit exactly - the
    plugin object carries no battery state at all, defines no `__getattr__` and
    no properties, and everything worth reading is one hop away at `.ps`.
    """

    def __init__(self, client, **attributes):
        self.ps = client
        for name, value in attributes.items():
            setattr(self, name, value)


class HostileClient:
    """A client whose every attribute access raises."""

    def __getattr__(self, name):
        raise RuntimeError("the PiSugar server is not answering")


class ClientWhoseReadyRaises:
    """A client whose `ready` flag itself blows up when read.

    SPEC 2.11: "Reading `ready` is third-party attribute access like every
    other: absent, `None`, or a property that raises all mean not ready, and
    none of them is an error."
    """

    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)

    @property
    def ready(self):
        raise RuntimeError("the PiSugar server is not answering")


# ---------------------------------------------------------------------------
# The client is traversed only when it reports itself ready (SPEC 2.11, F29)
# ---------------------------------------------------------------------------
#
# "So a client whose ready is not true is treated as absent, exactly as if the
# plugin held no client at all, and both fields fall through to the tier
# below." Before this rule the constructor's stand-in values - 0 and False -
# were confident-looking answers from a unit that never found its hardware.


@pytest.mark.parametrize(
    "make_client",
    [
        lambda: FakeClient(battery_level=64.0, power_plugged=True),
        lambda: FakeClient(ready=None, battery_level=64.0, power_plugged=True),
        lambda: FakeClient(ready=False, battery_level=64.0, power_plugged=True),
        lambda: FakeClient(ready=0, battery_level=64.0, power_plugged=True),
        lambda: FakeClient(ready="", battery_level=64.0, power_plugged=True),
        lambda: ClientWhoseReadyRaises(battery_level=64.0, power_plugged=True),
    ],
    ids=["absent", "none", "false", "falsy-int", "falsy-string", "raises"],
)
def test_a_client_that_is_not_ready_is_not_traversed(
    options, harness, stub_plugins, make_client
):
    """Every one of these is a client the constructor left in its stand-in
    state, or one whose flag cannot be read at all - both are "not ready" and
    both must fall through, not answer confidently with the constructor's `0`
    and `False`.

    Distinguished by the values themselves: the client carries 64.0/True and
    the I2C tier carries 42.0/False, so only a real traversal could produce the
    client's numbers, and this asserts the tier below answered instead.
    """
    client = make_client()
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": False}
    assert seam.calls == 1


def test_the_plugin_itself_is_still_probed_when_its_client_is_not_ready(
    options, harness, stub_plugins
):
    """SPEC 2.11: "The gate is on the client, not on the plugin." A plugin that
    keeps its own reading on itself answers on its own account regardless of
    what its client says, because another plugin may hold no client at all and
    that shape is why the plugin is a probe target in the first place."""
    client = FakeClient(ready=False, battery_level=7.0, power_plugged=False)
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(
        client, battery_level=88.0, power_plugged=True
    )
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 88.0, "charging": True}
    assert seam.calls == 0


def test_the_reference_unit_shape_is_read_through_the_nested_client(
    options, harness, stub_plugins
):
    """The unit this project is built against, as F29 describes it: a plugin
    object with no battery attributes, a client at `.ps`, and the state reachable
    only as methods on that client.

    Both fields arrive and the bus is never touched, which is the whole claim of
    "on a unit whose tier-1 plugin answers both fields it is not touched at
    all". Before F29 was written down this shape read nothing at all and the
    reference unit fell through to I2C in silence.
    """
    client = FakeClient(
        ready=True,
        get_battery_level=lambda: 64.0,
        get_battery_power_plugged=lambda: True,
    )
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == {"percent": 64.0, "charging": True}
    assert seam.calls == 0
    assert reader.available is True


def test_the_nested_client_is_read_as_plain_attributes_too(options, harness, stub_plugins):
    """`PiSugarServer.__init__` sets `battery_level` and `power_plugged` as
    plain attributes as well as defining accessors for them (F29), so the
    traversal must find them whichever form the client is in when it is read."""
    client = FakeClient(ready=True, battery_level=64.0, power_plugged=True)
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 64.0, "charging": True}
    assert seam.calls == 0


def test_upstream_pisugarx_has_the_same_shape(options, harness, stub_plugins):
    """F29: "Upstream `jayofelony` `pisugarx.py` has the identical shape, so the
    same traversal reaches both tiers." A traversal wired into the `pisugarx_ext`
    branch alone would leave every unit running the stock plugin unread."""
    client = FakeClient(
        ready=True,
        get_battery_level=lambda: 41.0,
        get_battery_power_plugged=lambda: False,
    )
    stub_plugins.loaded["pisugarx"] = FakePluginWithClient(client)
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 41.0, "charging": False}
    assert seam.calls == 0


def test_an_attribute_on_the_plugin_wins_over_the_same_one_on_the_client(
    options, harness, stub_plugins
):
    """"Each candidate is looked for on the plugin and then on `plugin.ps`" - in
    that order, so a plugin that does hold the value itself is not overtaken by
    a stale copy on its client. The values differ so the loser is visible."""
    client = FakeClient(battery_level=7.0, power_plugged=False)
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(
        client, battery_level=88.0, power_plugged=True
    )
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 88.0, "charging": True}
    assert seam.calls == 0


def test_a_flat_plugin_with_no_client_still_works(options, harness, stub_plugins):
    """The traversal is an addition, not a replacement: a plugin holding its own
    values and no `.ps` at all is read exactly as before."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=64.0, power_plugged=True)
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 64.0, "charging": True}
    assert seam.calls == 0


@pytest.mark.parametrize(
    "client",
    [None, object(), HostileClient()],
    ids=["ps-is-none", "ps-is-an-unrelated-object", "ps-raises-on-every-attribute"],
)
def test_a_client_that_answers_nothing_is_a_miss_and_not_an_error(
    options, harness, stub_plugins, client
):
    """A plugin whose client is absent, is something else entirely, or refuses
    every attribute is a miss like any other, so the tier below answers and
    nothing raises into the asyncio loop."""
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)
    seam = CountingSeam((42.0, True))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}


def test_a_plugin_whose_client_attribute_raises_is_a_miss(options, harness, stub_plugins):
    """`.ps` itself blowing up - a property that touches the hardware, a plugin
    still initialising - is a miss too, not an exception."""

    class PluginWithExplodingClient:
        @property
        def ps(self):
            raise RuntimeError("still initialising")

    stub_plugins.loaded["pisugarx_ext"] = PluginWithExplodingClient()
    seam = CountingSeam((42.0, True))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}


# ---------------------------------------------------------------------------
# `battery_charging` is dead and must not be probed (SPEC 2.11, F29)
# ---------------------------------------------------------------------------


def test_battery_charging_is_not_probed_on_the_client(options, harness, stub_plugins):
    """F29: `battery_charging` is assigned `0` in the client's constructor and
    nowhere else, and `get_battery_charging()` has a body of `pass`.

    `bool(0)` is `False`, and a `False` is a value rather than a miss, so a probe
    that still carries the name reports "not charging" confidently and forever
    while `power_plugged` - the only charge-related field the plugin maintains -
    is never consulted. The two answers here disagree, which is the only way to
    tell which name was read.
    """
    client = FakeClient(
        ready=True,
        battery_level=64.0,
        battery_charging=0,
        get_battery_charging=lambda: None,
        get_battery_power_plugged=lambda: True,
    )
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)

    assert read_with(options, harness.deps) == {"percent": 64.0, "charging": True}


def test_battery_charging_is_not_probed_on_the_plugin_either(
    options, harness, stub_plugins
):
    """The name is dead wherever it is found, not only one hop away."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(
        battery_level=64.0, battery_charging=0, power_plugged=True
    )

    assert read_with(options, harness.deps) == {"percent": 64.0, "charging": True}


def test_a_dead_battery_charging_alone_is_not_an_answer(options, harness, stub_plugins):
    """And with nothing else to read, the plugin has supplied nothing: a name
    that is never maintained is not a charge state, so the field is null and the
    tier below gets its turn rather than being pre-empted by a confident
    `False`."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_charging=0)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)
    reader = companion.BatteryReader(options, deps)

    assert reader.read() == NO_BATTERY
    assert reader.available is False
    assert seam.calls == 1


# ---------------------------------------------------------------------------
# Two more ways a candidate misses
# ---------------------------------------------------------------------------


def test_an_integer_too_large_for_a_float_does_not_escape(options, harness, stub_plugins):
    """`float(10**400)` raises `OverflowError`, which is an `ArithmeticError`
    and neither a `TypeError` nor a `ValueError`, so a cast guarded by those two
    lets it out - into the asyncio loop, past "never hard-fail, never raise"
    (SPEC 2.11). It is a miss, so the tier below still supplies the level.
    """
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=10 ** 400, charging=True)
    seam = CountingSeam((42.0, False))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 42.0, "charging": True}


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        ({"battery_level": None, "percentage": 64.0}, 64.0),
        ({"battery_level": None}, 42.0),
    ],
    ids=["next-candidate-answers", "tier-below-answers"],
)
def test_a_candidate_present_and_none_is_a_miss(
    options, harness, stub_plugins, attributes, expected
):
    """SPEC 2.11: "An attribute that is absent, or present and `None`" is a miss.
    The two are distinct events and only the absent one had a test: a plugin
    that initialises its level to `None` and fills it in later is the ordinary
    case, and `None` reaching the conversion is where a careless probe stops
    looking."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(**attributes)
    seam = CountingSeam((42.0, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == expected


def test_a_client_attribute_present_and_none_is_a_miss(options, harness, stub_plugins):
    """The same one hop away, where a client constructed but not yet polled is
    exactly the object that holds `None`."""
    client = FakeClient(ready=True, battery_level=None, get_battery_level=lambda: 64.0)
    stub_plugins.loaded["pisugarx_ext"] = FakePluginWithClient(client)
    seam = CountingSeam((None, None))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps)["percent"] == 64.0


# ---------------------------------------------------------------------------
# The guarded charging conversion, `BatteryReader._as_charging` (SPEC 2.11)
# ---------------------------------------------------------------------------
#
# "Charging is converted with the same rigor as percent, for the opposite
# reason. Percent guards a range; charging guards a two-valued fact, and a
# value that is not unambiguously one of the two is a miss rather than a
# guess. A bare `bool()` cast has no content on this field: `""` is `False`,
# `"false"` is `True`, and `0.5` is `True`, so a plugin that returns any
# non-empty string or any non-zero number would be read as charging regardless
# of what it meant. The guarded conversion accepts a `bool` as itself; an `int`
# only when it is exactly `0` or `1`; a `str` only from a fixed set of
# spellings read after `strip().lower()` - `"true"`/`"false"`, `"1"`/`"0"`,
# `"yes"`/`"no"`, `"on"`/`"off"`. Everything else, including a float and an
# unrecognised string, is refused like a failed cast."
#
# `_as_charging` takes the signature the implementation exposes it under - a
# staticmethod of one argument, returning `bool` or raising to refuse - and
# nothing else about the implementation is assumed. The exception type is not
# pinned by SPEC, so every refusal here is asserted only as "raises", not as a
# particular class.


def test_as_charging_accepts_a_bool_as_itself():
    assert companion.BatteryReader._as_charging(True) is True
    assert companion.BatteryReader._as_charging(False) is False


def test_as_charging_accepts_only_0_and_1_as_an_int():
    assert companion.BatteryReader._as_charging(0) is False
    assert companion.BatteryReader._as_charging(1) is True


@pytest.mark.parametrize("value", [2, -1, 100])
def test_as_charging_refuses_every_other_int(value):
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("TRUE", True),
        ("  false  ", False),
        ("Yes", True),
    ],
    ids=[
        "true",
        "false",
        "one",
        "zero",
        "yes",
        "no",
        "on",
        "off",
        "upper-case",
        "padded",
        "mixed-case",
    ],
)
def test_as_charging_accepts_the_enumerated_spellings_after_strip_and_lower(value, expected):
    assert companion.BatteryReader._as_charging(value) is expected


@pytest.mark.parametrize(
    "value", ["maybe", "yep", "nope", "2", "-1", "TrueFalse", "onoff", " "]
)
def test_as_charging_refuses_every_unrecognised_string(value):
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging(value)


def test_as_charging_refuses_the_empty_string_though_bool_would_accept_it_as_false():
    """The first of the three counterexamples SPEC names: `bool("")` is
    `False`, agreeing with the guard here only by coincidence and only in the
    direction that looks safe. The guard refuses it outright rather than
    supply a `False` that was never read off anything."""
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging("")


def test_as_charging_reads_the_string_false_as_false_though_bool_would_read_it_as_true():
    """The second counterexample: `bool("false")` is `True`, because the
    string is non-empty. The guard instead reads the word and answers `False`,
    which is the entire reason this conversion exists rather than a cast."""
    assert companion.BatteryReader._as_charging("false") is False


def test_as_charging_refuses_a_float_though_bool_would_accept_it_as_true():
    """The third counterexample: `bool(0.5)` is `True`, with no relation to
    charging at all. A float carries no unambiguous two-valued fact under this
    guard and is refused outright, never rounded or truncated into a bool."""
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging(0.5)


@pytest.mark.parametrize("value", [0.0, 1.0, -0.5])
def test_as_charging_refuses_every_float_including_the_ones_in_range(value):
    """`0.0` and `1.0` are exactly the two accepted `int` values in float
    clothing, and the guard still refuses them: the accepted type for a whole
    number is `int`, not "anything that equals 0 or 1"."""
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging(value)


def test_as_charging_refuses_none():
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging(None)


def test_as_charging_refuses_an_unrelated_type():
    with pytest.raises(Exception):
        companion.BatteryReader._as_charging([])


def test_a_charging_value_from_the_enumerated_spellings_is_accepted_end_to_end(
    options, harness, stub_plugins
):
    """The unit-level guard, reached through the actual probe pipeline: a
    plugin that reports charging as a word rather than a bool is still read,
    and the I2C tier is never touched because the field is already in hand."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=64.0, charging="yes")
    seam = ForbiddenSeam()
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 64.0, "charging": True}
    assert seam.calls == 0


def test_an_unrecognised_charging_string_is_a_miss_end_to_end(
    options, harness, stub_plugins
):
    """The refusal, reached the same way: a miss like any other, so the tier
    below still gets to answer the charging field."""
    stub_plugins.loaded["pisugarx_ext"] = FakeProvider(battery_level=64.0, charging="maybe")
    seam = CountingSeam((None, True))
    deps = deps_with_pisugar(harness.deps, seam)

    assert read_with(options, deps) == {"percent": 64.0, "charging": True}
    assert seam.calls == 1
