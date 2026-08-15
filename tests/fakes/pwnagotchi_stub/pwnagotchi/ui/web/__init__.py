"""Stand-in for `pwnagotchi.ui.web`.

Pinned by SPEC.md section 11:

    F18  pwnagotchi.ui.web is a package whose __init__ defines frame_path,
         frame_format, frame_ctype and frame_lock.

`from pwnagotchi.ui.web import server` is explicitly forbidden (section 11.1) and
there is no `server` module here, so that import fails loudly.

Tests point `frame_path` at a file under tmp_path; the plugin must read whatever
the module currently holds rather than caching it at import time.
"""

from __future__ import annotations

import threading

frame_path = "/var/tmp/pwnagotchi/pwnagotchi.png"
frame_format = "PNG"
frame_ctype = "image/png"
frame_lock = threading.Lock()

#: The default, so a fixture can restore it after redirecting frame_path.
_DEFAULT_FRAME_PATH = frame_path


def _reset() -> None:
    global frame_path
    frame_path = _DEFAULT_FRAME_PATH
