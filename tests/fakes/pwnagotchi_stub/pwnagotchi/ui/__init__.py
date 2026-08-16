"""Stand-in for the `pwnagotchi.ui` package.

Holds nothing of its own. The frame constants the plugin needs live in the
`pwnagotchi.ui.web` package `__init__` (SPEC.md F18), not in a `server` module,
and the `View` a face status is read from lives in `pwnagotchi.ui.view` (F28).

Neither submodule is re-exported here, because the real package does not
re-export them either.
"""
