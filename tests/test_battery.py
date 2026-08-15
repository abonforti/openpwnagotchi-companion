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
