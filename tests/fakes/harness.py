"""Shared fake-unit scaffolding for `tests/conftest.py` and `tests/e2e_unit.py`.

Both need the same two things before they can touch `plugin.companion`: the
`pwnagotchi` stub on `sys.path` so `import pwnagotchi` resolves to a stand-in
rather than failing outright, and a `FakeAgent`/`Deps` pair whose seams never
touch real hardware. `tests/e2e_unit.py` cannot import `tests/conftest.py`
directly - that module is pytest scaffolding, registers autouse fixtures, and
importing it outside a pytest session pulls in `pytest` for no reason - so the
pieces both sides need live here instead, deliberately smaller than
`conftest.py`'s own versions: no `pytest.fixture` decorators, no autouse
hygiene hooks, nothing conftest.py itself does not already own for the suite.

This module is not imported by `tests/conftest.py` today. It exists so that
`tests/e2e_unit.py` is not left copying stub-injection and fake-agent code
that already exists once; keeping conftest.py's own copy untouched is
deliberate, since a test author working from SPEC.md and the schemas owns
that file and its fixtures independently of this one.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

HARNESS_DIR = Path(__file__).resolve().parent
TESTS_DIR = HARNESS_DIR.parent
REPO_ROOT = TESTS_DIR.parent
STUB_DIR = HARNESS_DIR / "pwnagotchi_stub"
FIXTURES_DIR = HARNESS_DIR / "fixtures"


def install_pwnagotchi_stub() -> Any:
    """Puts the pwnagotchi stub on `sys.path` and imports it, before any import
    of `plugin.companion` - which is what makes `import pwnagotchi` inside that
    module resolve to the stand-in rather than to nothing at all.

    Returns the `pwnagotchi` module, mostly so a caller can assert the guard
    below actually held.
    """
    for path in (str(STUB_DIR), str(REPO_ROOT)):
        if path not in sys.path:
            sys.path.insert(0, path)
    import pwnagotchi
    import pwnagotchi.mesh.peer  # noqa: F401
    import pwnagotchi.plugins  # noqa: F401
    import pwnagotchi.ui.web  # noqa: F401

    assert Path(pwnagotchi.__file__).is_relative_to(STUB_DIR), (
        "the real pwnagotchi package is shadowing the stub; nothing here may "
        "run against a real pwnagotchi installation"
    )
    return pwnagotchi


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------

#: Synthetic and obviously fictional, the same convention `tests/conftest.py`
#: follows for its own access-point fixture.
DEFAULT_ACCESS_POINTS: list[dict[str, Any]] = [
    {
        "mac": "aa:bb:cc:dd:ee:ff",
        "hostname": "E2E_Fake_Unit_Net",
        "channel": 6,
        "rssi": -52,
        "encryption": "WPA2",
        "vendor": "Synthetic Devices",
        "clients": [{"mac": "11:22:33:44:55:66"}],
    },
    {
        "mac": "11:22:33:44:55:66",
        "hostname": "E2E_Legacy_Net",
        "channel": 11,
        "rssi": -78,
        "encryption": "WPA",
        "vendor": "",
        "clients": None,
    },
]

DEFAULT_FACE = "(⌐■_■)"
DEFAULT_STATUS = "Hi, I'm the e2e fake unit"


class FakeAgent:
    """The pwnagotchi agent, reduced to what SPEC section 11 pins.

    Identical in shape to `tests/conftest.py`'s own `FakeAgent`, kept as a
    second definition rather than an import for the reason given at the top
    of this module: pulling in `conftest.py` outside pytest is the wrong
    dependency direction. Any drift between the two is a defect in this file,
    since SPEC section 11 is the single source both are reduced from.
    """

    def __init__(
        self,
        *,
        mode: str = "auto",
        face: str = DEFAULT_FACE,
        status: str = DEFAULT_STATUS,
        access_points: list[dict[str, Any]] | None = None,
        handshakes: dict[str, Any] | None = None,
        peers: dict[str, Any] | None = None,
        current_channel: int = 6,
        config: dict[str, Any] | None = None,
        session_payload: dict[str, Any] | None = None,
    ) -> None:
        from pwnagotchi.ui.view import View

        self.mode = mode
        self._access_points = DEFAULT_ACCESS_POINTS if access_points is None else access_points
        self._handshakes = {} if handshakes is None else handshakes
        self._peers = {} if peers is None else peers
        self._current_channel = current_channel
        self._config = (
            config
            if config is not None
            else {
                "bettercap": {"handshakes": "/etc/pwnagotchi/handshakes"},
                "main": {"log": {"path": "/etc/pwnagotchi/log/pwnagotchi.log"}},
            }
        )
        self._session_payload = {} if session_payload is None else session_payload
        self.session_calls = 0
        self._ui_view = View({"face": face, "status": status})

    def session(self) -> dict[str, Any]:
        self.session_calls += 1
        return self._session_payload

    def view(self):
        return self._ui_view

    def get_access_points(self):
        """Forbidden by SPEC 11.1 - side-effecting; must never be called."""
        raise AssertionError(
            "agent.get_access_points() is side-effecting (SPEC F3) and must never "
            "be called; read agent._access_points"
        )


def default_peers() -> dict[str, Any]:
    """One synthetic peer, so `stats.lastPeer` and the `peers` view have
    something to show. Built directly rather than through `Peer(**kwargs)`
    defaults, so the values on the wire are recognisably fixture data.
    """
    from pwnagotchi.mesh.peer import Peer

    advertisement = {
        "name": "E2E_Peer",
        "identity": "aabbccddeeff00112233445566778899",
        "version": "2.9.5.6",
        "face": "(-_-)",
        "uptime": 4242,
        "epoch": 17,
        "pwnd_run": ["E2E_Fake_Unit_Net"],
        "pwnd_tot": 3,
    }
    peer = Peer(
        advertisement,
        last_seen=time.time(),
        encounters=2,
        session_id="aa:bb:cc:dd:ee:01",
        last_channel=6,
        rssi=-55,
    )
    return {peer.session_id: peer}


# ---------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------


class DepsHarness:
    """A `companion.Deps` with every seam answering from a fixture, never from
    real hardware or a real service restart.

    Shaped like `tests/conftest.py`'s own `DepsHarness`, with one deliberate
    difference: `spawn` here starts a real daemon thread instead of only
    recording its target. `tests/conftest.py`'s version exists to let a test
    assert an ordering and then run the effect on purpose with
    `run_spawned()`; this one drives a live process with nothing to run it on
    the harness's behalf, so a seam that only recorded would mean nothing
    the plugin spawns - the stats ticker in `tests/e2e_unit.py` included -
    ever actually runs.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.gpsd_reply: dict | None = None
        self.pisugar_reply: tuple[float | None, bool | None] = (None, None)
        self.local_ipv4: list[str] = []
        self.command_status = 0
        import plugin.companion as companion

        self.deps = companion.Deps(
            now=self._now,
            restart_pwnagotchi=self._restart,
            reboot_device=self._reboot,
            shutdown_device=self._shutdown,
            run_command=self._run_command,
            read_gpsd=self._read_gpsd,
            read_pisugar_i2c=self._read_pisugar_i2c,
            list_local_ipv4=self._list_local_ipv4,
            spawn=self._spawn,
        )

    def _now(self) -> float:
        # Real wall-clock time: this harness runs a live process a real
        # client connects to, not a scenario stepped by a fake clock.
        return time.time()

    def _restart(self, mode: str) -> None:
        # Never a real pwnagotchi.restart(): nothing in this process is a
        # pwnagotchi, and this is a mode switch reply on a fixture, not an
        # instruction to a real service.
        self.calls.append(("restart_pwnagotchi", (mode,)))

    def _reboot(self) -> None:
        self.calls.append(("reboot_device", ()))

    def _shutdown(self) -> None:
        self.calls.append(("shutdown_device", ()))

    def _run_command(self, argv: Sequence[str]) -> int:
        self.calls.append(("run_command", (tuple(argv),)))
        return self.command_status

    def _read_gpsd(self, host: str, port: int, timeout: float) -> dict | None:
        self.calls.append(("read_gpsd", (host, port, timeout)))
        return self.gpsd_reply

    def _read_pisugar_i2c(self) -> tuple[float | None, bool | None]:
        self.calls.append(("read_pisugar_i2c", ()))
        return self.pisugar_reply

    def _list_local_ipv4(self) -> list[str]:
        self.calls.append(("list_local_ipv4", ()))
        return list(self.local_ipv4)

    def _spawn(self, target: Callable[[], None]) -> None:
        self.calls.append(("spawn", (target,)))
        threading.Thread(target=target, daemon=True).start()
