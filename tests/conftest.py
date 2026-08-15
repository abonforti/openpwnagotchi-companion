"""Shared test scaffolding.

Two jobs, in this order and no other:

1. Put `tests/fakes/pwnagotchi_stub/` on `sys.path` and import the stub package,
   so that `import pwnagotchi` inside `plugin/companion.py` resolves to the
   stand-in. This must happen before the plugin is imported, which is why it is
   done at module scope here rather than in a fixture.
2. Provide the fakes every test shares: a `FakeAgent` that counts `session()`
   calls, a `Deps` harness whose seams are all recorded, and the fixture files.

The stub exposes exactly the symbols pinned in SPEC.md section 11. If the plugin
reaches for something else the import or the attribute lookup fails, loudly, and
that failure is the point - it is the guard against an invented pwnagotchi API
reaching a real unit.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

import pytest

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
STUB_DIR = TESTS_DIR / "fakes" / "pwnagotchi_stub"
FIXTURES_DIR = TESTS_DIR / "fakes" / "fixtures"
SCHEMAS_DIR = REPO_ROOT / "docs" / "schemas"

for path in (str(STUB_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Imported for the side effect of registering the stub in sys.modules under the
# `pwnagotchi` names, before anything imports the plugin.
import pwnagotchi  # noqa: E402
import pwnagotchi.mesh.peer  # noqa: E402
import pwnagotchi.plugins  # noqa: E402
import pwnagotchi.ui.web  # noqa: E402

assert Path(pwnagotchi.__file__).is_relative_to(STUB_DIR), (
    "the real pwnagotchi package is shadowing the stub; the suite must run "
    "without a pwnagotchi installation"
)

from plugin import companion  # noqa: E402


# ---------------------------------------------------------------------------
# Stub hygiene
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stub():
    """Clears recorded calls and the loaded-plugin registry around every test.

    Autouse because a leaked `plugins.loaded` entry silently changes what
    `capabilities.pasv` reports, and a test that passes for that reason is worse
    than one that fails.
    """
    pwnagotchi._reset()
    pwnagotchi.plugins._reset()
    pwnagotchi.ui.web._reset()
    yield
    pwnagotchi._reset()
    pwnagotchi.plugins._reset()
    pwnagotchi.ui.web._reset()


@pytest.fixture
def stub_pwnagotchi():
    """The `pwnagotchi` stub module, for asserting on restart/reboot/shutdown."""
    return pwnagotchi


@pytest.fixture
def stub_plugins():
    """The `pwnagotchi.plugins` stub, for `loaded` and the recorded `on()` calls."""
    return pwnagotchi.plugins


@pytest.fixture
def stub_ui_web():
    """The `pwnagotchi.ui.web` stub, for redirecting `frame_path`."""
    return pwnagotchi.ui.web


# ---------------------------------------------------------------------------
# Fake pasv_mode
# ---------------------------------------------------------------------------


class FakePasvMode:
    """Stands in for the `pasv_mode` plugin instance in `plugins.loaded`.

    The plugin may only reach it through `plugins.loaded` and may never import
    it (SPEC 2.6.2), so this deliberately lives nowhere importable.
    """

    def __init__(self, passive: bool = False) -> None:
        self.passive = passive


@pytest.fixture
def load_pasv(stub_plugins):
    """Registers a fake `pasv_mode` and hands it back."""

    def _load(passive: bool = False) -> FakePasvMode:
        instance = FakePasvMode(passive)
        stub_plugins.loaded["pasv_mode"] = instance
        return instance

    return _load


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


DEFAULT_ACCESS_POINTS: list[dict[str, Any]] = [
    {
        "mac": "aa:bb:cc:dd:ee:ff",
        "hostname": "TestNet_001",
        "channel": 6,
        "rssi": -52,
        "encryption": "WPA2",
        "vendor": "Synthetic Devices",
        "clients": [{"mac": "11:22:33:44:55:66"}],
    },
    {
        "mac": "11:22:33:44:55:66",
        "hostname": "Legacy_Net",
        "channel": 11,
        "rssi": -78,
        "encryption": "WPA",
        "vendor": "",
        "clients": None,
    },
]


class FakeAgent:
    """The pwnagotchi agent, reduced to what SPEC section 11 pins.

    Only the underscore-prefixed collections exist: the no-underscore forms
    (`access_points`, `handshakes`, `peers`) are false on this fork and reading
    one must raise `AttributeError` here rather than quietly returning something
    on a real unit.

    `session()` counts its calls. That counter is the machine-checked form of the
    rule in SPEC 2.5: the call is a 30-second-timeout HTTP GET, so a single one
    on the request path stalls every connected client, and `get_stats` must make
    exactly zero of them.
    """

    def __init__(
        self,
        *,
        mode: str = "auto",
        access_points: list[dict[str, Any]] | None = None,
        handshakes: dict[str, Any] | None = None,
        peers: dict[str, Any] | None = None,
        current_channel: int = 6,
        config: dict[str, Any] | None = None,
        session_payload: dict[str, Any] | None = None,
    ) -> None:
        self.mode = mode
        self._access_points = DEFAULT_ACCESS_POINTS if access_points is None else access_points
        self._handshakes = {} if handshakes is None else handshakes
        self._peers = {} if peers is None else peers
        self._current_channel = current_channel
        self._config = config if config is not None else {
            "bettercap": {"handshakes": "/etc/pwnagotchi/handshakes"},
            "main": {"log": {"path": "/etc/pwnagotchi/log/pwnagotchi.log"}},
        }
        self._session_payload = {} if session_payload is None else session_payload
        self.session_calls = 0

    def session(self) -> dict[str, Any]:
        self.session_calls += 1
        return self._session_payload

    def get_access_points(self):
        """Forbidden by SPEC 11.1 - side-effecting.

        On a real unit this fires `plugins.on('wifi_update')`, drives channel
        selection and advances the epoch. Calling it from a read path is a bug
        that would only show up as strange behaviour on hardware, so it is a
        hard failure here.
        """
        raise AssertionError(
            "agent.get_access_points() is side-effecting (SPEC F3) and must never "
            "be called; read agent._access_points"
        )


@pytest.fixture
def agent_factory(handshake_dir, log_file):
    """Builds a FakeAgent whose config points at the fixture directories."""

    def _make(**kwargs: Any) -> FakeAgent:
        kwargs.setdefault(
            "config",
            {
                "bettercap": {"handshakes": str(handshake_dir)},
                "main": {"log": {"path": str(log_file)}},
            },
        )
        return FakeAgent(**kwargs)

    return _make


@pytest.fixture
def agent(agent_factory) -> FakeAgent:
    return agent_factory()


# ---------------------------------------------------------------------------
# The seams
# ---------------------------------------------------------------------------


class DepsHarness:
    """A `Deps` with every seam faked, plus the recordings they produce.

    SPEC 10.7 makes this a design requirement rather than a convenience: without
    injectable seams the I2C, gpsd, subprocess and service-restart branches are
    unreachable from a test, and the branch-coverage gate would have to be
    weakened to accommodate that.

    `spawn` records its target and does **not** run it. That is what lets a test
    assert the ordering the protocol requires - acknowledgment and `restarting`
    produced first, effect scheduled afterwards - and then run the effect on
    purpose with `run_spawned()`.
    """

    def __init__(self, *, clock: float = 1_755_264_000.0) -> None:
        self.clock = clock
        self.calls: list[tuple[str, tuple]] = []
        self.spawned: list[Callable[[], None]] = []
        self.gpsd_reply: dict | None = None
        self.pisugar_reply: tuple[float | None, bool | None] = (None, None)
        self.interface_ips: dict[str, str | None] = {}
        self.command_status = 0
        self.deps = companion.Deps(
            now=self._now,
            restart_pwnagotchi=self._restart,
            reboot_device=self._reboot,
            shutdown_device=self._shutdown,
            run_command=self._run_command,
            read_gpsd=self._read_gpsd,
            read_pisugar_i2c=self._read_pisugar_i2c,
            resolve_interface_ip=self._resolve_interface_ip,
            spawn=self._spawn,
        )

    # -- the seams ---------------------------------------------------------

    def _now(self) -> float:
        return self.clock

    def _restart(self, mode: str) -> None:
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

    def _resolve_interface_ip(self, iface: str) -> str | None:
        self.calls.append(("resolve_interface_ip", (iface,)))
        return self.interface_ips.get(iface)

    def _spawn(self, target: Callable[[], None]) -> None:
        self.calls.append(("spawn", (target,)))
        self.spawned.append(target)

    # -- assertions helpers ------------------------------------------------

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def args_for(self, name: str) -> list[tuple]:
        return [args for called, args in self.calls if called == name]

    def run_spawned(self) -> None:
        """Runs everything `spawn` was handed, in order."""
        for target in list(self.spawned):
            target()

    def advance(self, seconds: float) -> None:
        self.clock += seconds


@pytest.fixture
def harness() -> DepsHarness:
    return DepsHarness()


@pytest.fixture
def deps(harness) -> Any:
    return harness.deps


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@pytest.fixture
def options(handshake_dir, tmp_path) -> dict[str, Any]:
    """A configuration with the defaults, pointed at throwaway paths."""
    return {
        "interfaces": ["bnep0", "usb0"],
        "ws_port": 8082,
        "http_port": 8443,
        "tls_cert": str(tmp_path / "server.crt"),
        "tls_key": str(tmp_path / "server.key"),
        "web_root": str(tmp_path / "web"),
        "token": "",
        "auth_timeout": 10,
        "pisugar": True,
        "gps_source": "auto",
        "gpsd_host": "127.0.0.1",
        "gpsd_port": 2947,
        "session_poll_interval": 5,
        "rebind_interval": 30,
        "save_gps_log": False,
        "gps_log_path": str(tmp_path / "gps.log"),
        "mirror_auto_interval": 5,
    }


# ---------------------------------------------------------------------------
# The router under test
# ---------------------------------------------------------------------------


@pytest.fixture
def router_factory(options, deps):
    """Assembles a Router over a FakeAgent with every component wired to fakes.

    The handshake store is built from the agent's own config so that a test can
    move the capture directory without reaching past the agent, which is the
    only place the plugin is allowed to learn the path from (SPEC F14).
    """

    def _make(agent, *, overrides: dict[str, Any] | None = None, plugin_version: str | None = None):
        resolved = dict(options)
        resolved.update(overrides or {})
        agent_getter = lambda: agent  # noqa: E731
        session = companion.SessionCache(
            agent_getter, deps, resolved["session_poll_interval"]
        )
        gps = companion.GpsResolver(resolved, deps, session)
        battery = companion.BatteryReader(resolved, deps)
        store = lambda: companion.HandshakeStore(  # noqa: E731
            agent._config["bettercap"]["handshakes"]
        )
        return companion.Router(
            resolved,
            deps,
            agent_getter,
            session,
            gps,
            battery,
            store,
            plugin_version if plugin_version is not None else companion.__version__,
        )

    return _make


@pytest.fixture
def router(router_factory, agent):
    return router_factory(agent)


# ---------------------------------------------------------------------------
# Fixture files
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def schemas_dir() -> Path:
    return SCHEMAS_DIR


@pytest.fixture(scope="session")
def bettercap_session() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "bettercap_session.json").read_text())


@pytest.fixture
def handshake_dir(tmp_path) -> Path:
    """A writable copy of the fixture handshake directory.

    Copied rather than used in place: sidecar writing is part of what is under
    test, and a test must not be able to add a file to the repository.
    """
    destination = tmp_path / "handshakes"
    shutil.copytree(FIXTURES_DIR / "handshakes", destination)
    return destination


@pytest.fixture
def log_file(tmp_path) -> Path:
    destination = tmp_path / "pwnagotchi.log"
    shutil.copyfile(FIXTURES_DIR / "pwnagotchi.log", destination)
    return destination


@pytest.fixture
def frame_file(tmp_path, stub_ui_web) -> Path:
    """A one-pixel PNG standing in for the e-ink frame, with frame_path pointed at it."""
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
        "00000049454e44ae426082"
    )
    destination = tmp_path / "pwnagotchi.png"
    destination.write_bytes(png)
    stub_ui_web.frame_path = str(destination)
    return destination


# ---------------------------------------------------------------------------
# TLS material
# ---------------------------------------------------------------------------


def _issue_self_signed(directory: Path) -> dict[str, Path]:
    key = directory / "server.key"
    cert = directory / "server.crt"
    config = directory / "openssl.cnf"
    config.write_text(
        "[req]\n"
        "distinguished_name = dn\n"
        "x509_extensions = ext\n"
        "prompt = no\n"
        "[dn]\n"
        "CN = openpwnagotchi-companion-test\n"
        "[ext]\n"
        "basicConstraints = CA:FALSE\n"
        "extendedKeyUsage = serverAuth\n"
        "subjectAltName = IP:127.0.0.1, IP:127.0.0.2\n"
    )
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-days", "1", "-config", str(config),
        ],
        check=True,
        capture_output=True,
    )
    return {"cert": cert, "key": key, "dir": directory}


@pytest.fixture(scope="session")
def tls_material_second(tmp_path_factory) -> dict[str, Path]:
    """A second, unrelated pair, for the certificate/key mismatch case."""
    return _issue_self_signed(tmp_path_factory.mktemp("tls-other"))


@pytest.fixture(scope="session")
def tls_material(tmp_path_factory) -> dict[str, Path]:
    """A throwaway self-signed certificate with an iPAddress SAN for 127.0.0.1.

    Generated with openssl rather than committed: a private key in a public
    repository is a leak even when it is a toy, and `.gitignore` blocks the
    filenames outright.
    """
    return _issue_self_signed(tmp_path_factory.mktemp("tls"))


# ---------------------------------------------------------------------------
# Bind guard
# ---------------------------------------------------------------------------


class BindRecorder:
    """Records every address any socket is bound to during a test.

    SPEC D5 and 11.1 forbid `0.0.0.0`, `::` and hostnames in a bind call, and
    upstream `pwnios.py` binds `0.0.0.0:8082` (F22). Watching the syscall rather
    than the plugin's own bookkeeping is the only check that cannot be satisfied
    by a listener that merely *reports* the right address.
    """

    def __init__(self) -> None:
        self.addresses: list[Any] = []

    def record(self, address: Any) -> None:
        # A bind to port 0 asks the kernel for an ephemeral port. The plugin
        # never does that - its ports come from the configuration - so those are
        # the test harness probing for a free port, not a listener.
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == 0:
            return
        self.addresses.append(address)

    @property
    def hosts(self) -> list[Any]:
        return [address[0] for address in self.addresses if isinstance(address, tuple) and address]

    def assert_no_wildcard(self) -> None:
        forbidden = {"0.0.0.0", "::", "", "localhost"}
        offending = [host for host in self.hosts if host in forbidden]
        assert not offending, f"bound to a wildcard or a hostname: {offending}"


@pytest.fixture
def bind_recorder(monkeypatch) -> BindRecorder:
    """Wraps socket.bind for the duration of a test.

    This patches the standard library, not the module under test: the point is
    to observe what actually reaches the kernel, without the plugin having to
    expose a seam for it.
    """
    recorder = BindRecorder()
    original = socket.socket.bind

    def recording_bind(self, address):
        recorder.record(address)
        return original(self, address)

    monkeypatch.setattr(socket.socket, "bind", recording_bind)
    return recorder


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


@pytest.fixture
def free_port() -> int:
    """A TCP port that was free a moment ago on the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
