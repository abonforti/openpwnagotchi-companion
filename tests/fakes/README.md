# Hardware library stubs

Two importable stand-ins for libraries the plugin uses **when they happen to be installed on a
unit**, so that the code which runs in that case can run here too.

| Directory | Stands in for | Reaches |
|---|---|---|
| `i2c_stub/` | `smbus2` | the direct PiSugar read, SPEC 2.11: bus 1, address `0x57`, register `0x2A` |
| `netifaces_stub/` | `netifaces` | the primary branch of the address enumeration, SPEC 2.3 and 2.3.1 |

## Why a package on `sys.path` rather than a test dependency

Neither library belongs in `tests/requirements.txt`. `smbus2` wants an I2C device node that no
build host has, and SPEC 2.3 forbids a hard dependency on `netifaces` outright. Both are
optional on the device by design, which is exactly why the plugin has two paths through each
call and exactly why nothing else in the suite can reach the first one.

This is the mechanism `pwnagotchi_stub/` already establishes, with one difference in how it is
switched on. The pwnagotchi stub goes on `sys.path` once, in `conftest.py`, before the plugin is
imported, because `import pwnagotchi` must resolve to it in every test. These two are installed
per test by `installed_stub()` in `tests/test_deps.py`, which prepends the directory, imports the
module and removes it again afterwards. The absent-library case is the default state of the
suite and needs no help; it is the present-library case that is the exception, and an exception
that lasted for the whole session would silently shadow a real `netifaces` on a machine that has
one.

## Why `sys.path` and not `sys.modules`

A module object assigned into `sys.modules` is the cheaper trick and the suite still uses it for
the *absent* case, where `None` is the documented way to make an import raise. For the
present-and-working case it is the weaker one:

- it never runs the import system, so an implementation that imports a submodule, re-imports
  under a second name or imports from a worker thread is not actually exercised;
- the fake has no source file, so nobody reviews it, and the shapes it returns - the thing that
  has to match the real library or the test proves nothing - live inline in whichever test wrote
  them, in as many versions as there are tests.

## The stubs are not equally strict, on purpose

`pwnagotchi_stub/` exposes exactly the symbols pinned in SPEC section 11 and nothing more,
because an invented pwnagotchi API is a crash on somebody's unit and the missing attribute is
the guard. Nothing widens it except a new pinned fact.

`netifaces_stub/` and `i2c_stub/` are permissive instead. They stand in for third-party
libraries with published APIs that SPEC does not enumerate, so a stub trimmed to what one
implementation happens to call would fail correct code that called something else. Their job is
to answer the way the real library answers - the same shapes, the same exception types, the same
family constants - and to record what was asked, so a test can pin the parts SPEC does pin: the
bus number, the I2C address, the register, and that an interface name never reaches a result.

Never let a stub answer in a shape the real library cannot produce. That turns green here into
red on the device, which is the one failure mode a stub can have that is worse than no stub.

That rule is about what a stub **volunteers**, not about what a test may **inject**. SPEC 2.3.1
requires the address enumeration to validate whatever the backend hands it, and a boundary that
refuses bad input is only tested by feeding it bad input. So `tests/test_deps.py` does pass
`netifaces_stub._set_table()` `AF_INET` entries a healthy host would not produce - an entry with
a `peer` and no `addr`, an empty `addr`, an `addr` carrying a prefix, an IPv6 literal, a name, a
truncated literal - and asserts that none of them reaches the result. Those live in the test that
needs them, next to the assertion that justifies them. What the stub returns *unasked*, and every
default it ships with, stays inside the shapes the real library can produce.

Whether real `netifaces` ever reports an `AF_INET` entry lacking `addr` is **unverified**. The
test that injects one says so in its docstring; do not turn it into a claim about the library.
