"""A fake `smbus2`, so the direct PiSugar read in SPEC 2.11 can actually run.

`smbus2` and nothing else. The older `smbus` is a *different* package with a
smaller surface - no `i2c_msg`, no `force` keyword on a block read - and SPEC
2.11 names `smbus2` and states there is no fallback to it, so a stub answering
to the older name would stand in for an import the plugin never makes while
offering shapes the library of that name cannot produce.

Real `smbus2` is not a test dependency: it wants an I2C device node, the build
host has none, and installing an I2C library to test a battery reading would
make every contributor carry it. This package on `sys.path` is the same
mechanism `tests/fakes/pwnagotchi_stub/` uses, for the same reason.
"""

from _fake_i2c_device import SMBus, i2c_msg  # noqa: F401

__all__ = ["SMBus", "i2c_msg"]
