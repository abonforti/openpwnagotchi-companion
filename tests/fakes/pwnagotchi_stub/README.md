# `pwnagotchi` stub

A minimal importable stand-in for the real `pwnagotchi` package, so the plugin can be tested
without a pwnagotchi installation.

It exposes **exactly** the symbols pinned in `SPEC.md` section 11 and nothing else. If the
plugin reaches for a symbol that is not here, the test fails with an `AttributeError` or an
`ImportError` — that failure is the point. It is the guard against an invented API reaching a
real device, where the same call would take the whole unit down.

Never widen this stub to make a test pass. Widen it only when section 11 gains a genuinely new
pinned fact, and cite the fact ID in the commit message.

Everything prefixed with an underscore is test scaffolding (call recorders, tunable readings)
and is not part of the pinned surface. The plugin must never touch it.

`conftest.py` puts this directory on `sys.path` and imports the package before
`plugin.companion` is imported, so `import pwnagotchi` inside the plugin resolves here.
