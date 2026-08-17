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

import dataclasses

import pytest

from plugin import companion

NO_BATTERY = {"percent": None, "charging": None}


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

    deps = dataclasses.replace(harness.deps, read_pisugar_i2c=exploding)

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

    deps = dataclasses.replace(harness.deps, read_pisugar_i2c=exploding)
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
