"""Stand-in for `pwnagotchi.ui.view`.

Pinned by SPEC.md section 11:

    F28  `Agent.view()` returns the `View` the agent was built with, `View.get(key)`
         delegates to `State.get`, and `State.get` returns **the value**, never the
         widget, or `None` when the key is absent. The initial state of a running
         unit always carries `'face'` and `'status'`, so on hardware both keys
         resolve to strings.

The single most important thing about this stub is what it does **not** do: `get`
never hands back an object with a `.value`. The real `State.get` unwraps the widget
itself, so a caller can never observe one, and a stub that offered that shape would
let a widget-unwrapping branch in the plugin look tested while being dead code on a
device.

That is enforced in code and not merely stated here: the state is a
`Mapping[str, str]`, and the constructor refuses any other value shape, so a test
cannot construct the widget form at all. See `_reject_widget_shape`.

Nothing else about the view is pinned. There is no `set`, no `update`, no `on_render`
and no `State` class here, because the plugin reads two keys and nothing more.
"""

from __future__ import annotations

from typing import Mapping


def _reject_widget_shape(key: str, value: object) -> str:
    """Guards the one claim this stub makes: values are strings, never widgets.

    If you arrived here because `View({'face': something_with_a_value})` failed:
    that is deliberate. `State.get` unwraps the widget and hands back
    `widget.value`, so no caller of `View.get` has ever observed a widget on a
    device (F28). A widget-unwrapping branch in the plugin is therefore dead
    code, and a stub that accepted this shape would let such a branch be written
    and look tested.
    """
    if not isinstance(value, str):
        raise TypeError(
            f"View state {key!r} was given a {type(value).__name__}; F28 pins "
            "View.get as returning the already-unwrapped value, so str is the "
            "only shape a caller can observe on a device. A widget - anything "
            "carrying a .value - is a shape the fork cannot produce, and this "
            "stub will not fake it."
        )
    return value


class View:
    """The UI view, reduced to the read the face status needs.

    The constructor takes the values directly rather than widgets: widgets are an
    implementation detail of `State`, invisible through `get`, and reproducing them
    here would only make it possible to write a test the fork cannot fail.
    """

    def __init__(self, state: Mapping[str, str] | None = None) -> None:
        self._state: dict[str, str] = {
            key: _reject_widget_shape(key, value)
            for key, value in dict(state or {}).items()
        }

    def get(self, key: str) -> str | None:
        """F28: the stored value, or `None` when the key was never set."""
        return self._state[key] if key in self._state else None
