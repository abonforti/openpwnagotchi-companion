"""A fake pwnagotchi unit for the frontend end-to-end suite (SPEC 10.5, issue #185).

Every e2e assertion used to be about an empty shell: with no unit attached the
connection never left `connecting`, no view held data, and no request control
was ever enabled. This script gives Playwright something real to talk to
instead - the actual `Router` and `Listeners` from `plugin/companion.py`,
running against the pwnagotchi stub `tests/conftest.py` also uses and a
`Deps` whose seams answer with fixtures rather than touching hardware. It is
the contract in `docs/schemas/` spoken by the code that ships, not a fake of
it: nothing here reimplements a single reply, it only supplies the agent, the
addresses and the certificate the real components need to run standalone.

Run from the repository root, with tests/requirements.txt already installed in
the interpreter `python3` resolves to (CI's own `pip install -r
tests/requirements.txt` step, or an already-activated virtualenv locally):

    python3 -m tests.e2e_unit --ws-port 8082 --http-port 8443

That is exactly the command `playwright.config.ts`'s second `webServer` entry
runs, beside the built frontend's `vite preview`: nothing in the `e2e` job of
.github/workflows/ci.yml installs `uv`, so the command has to work with a
plain `python3` fed by that job's own pip install. For a one-off local run
with no environment already set up, `uv run --with-requirements
tests/requirements.txt python -m tests.e2e_unit ...` builds one on the fly.
It can also be run directly as `python tests/e2e_unit.py ...` from the
repository root - the `sys.path` handling below adds the repository root
either way, which is what lets `from plugin import companion` and `from
tests.fakes.harness import ...` resolve without a package install.

Binds `127.0.0.1` only, generates its own throwaway CA and server certificate
into a temporary directory with `tools/gen-ca.sh` and `tools/gen-cert.sh`
(SPEC D5, D4), and never touches a real pwnagotchi: `restart`, `reboot` and
`shutdown` are recorded by the harness and never call anything real (SPEC
2.3.3 D5 boundary - this process is not a pwnagotchi and must not act like
one reaches for `subprocess`, I2C or gpsd).

Nothing here plays `Companion._background`'s part: there is no periodic
`session.refresh()` or `gps.refresh_gpsd()` pass, so `stats.sessionAge` and
`stats.gps` stay at their unrefreshed defaults for the life of the process.
Dashboard, WiFi and Peers get real data straight from the fixtures
(`agent._access_points`, `agent._peers`, the handshake directory); Map and
any assertion on the staleness path do not, because both depend on a refresh
this script never runs.

Prints exactly one line, `e2e-unit: wss://127.0.0.1:<port>`, once `Listeners`
itself reports the WSS socket bound on the configured address - not once a
bare TCP probe happens to connect to *something* on that port, which cannot
tell this listener apart from a stale process left over on it - and runs
until SIGTERM or SIGINT, at which point it stops the listeners and exits.
Every other line - startup logging from `plugin/companion.py` itself - goes
to the plugin's own logger, never to stdout, so a `stdout: 'pipe'` webServer
entry sees the ready line and nothing else racing it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fakes.harness import (  # noqa: E402
    DepsHarness,
    FakeAgent,
    FIXTURES_DIR,
    default_peers,
    install_pwnagotchi_stub,
)

# Must happen before `plugin.companion` is imported, exactly as in
# `tests/conftest.py`: that module's own `import pwnagotchi` has to resolve to
# the stub, not fail outright for want of a real pwnagotchi installation.
install_pwnagotchi_stub()

from plugin import companion  # noqa: E402

log = logging.getLogger(__name__)

GEN_CA = REPO_ROOT / "tools" / "gen-ca.sh"
GEN_CERT = REPO_ROOT / "tools" / "gen-cert.sh"

#: How often `stats`/`keepalive` go out once someone is connected. The plugin
#: itself clamps `keepalive_interval` to [5, 20] seconds (SPEC 2.4); the floor
#: is used here so an e2e run does not wait longer than it has to for a second
#: update.
STATS_INTERVAL_S = 5.0

#: How often the main thread wakes to check whether a signal handler has set
#: `shutdown`. An untimed `Event.wait()` on the main thread is not reliably
#: interruptible by a signal on every platform - a run was observed still
#: alive 55 minutes after SIGTERM - so this loop polls instead of blocking
#: indefinitely.
SHUTDOWN_POLL_S = 0.5


def issue_certificate(directory: Path, host: str) -> dict[str, Path]:
    """Runs the real `gen-ca.sh` and `gen-cert.sh` for `host` into `directory`.

    Not a fixture certificate: SPEC 10.5 wants the scripts themselves
    exercised, the same way `tests/test_integration_ws.py`'s `pki` fixture
    does.
    """
    for argv in (
        ["sh", str(GEN_CA), "--out", str(directory)],
        ["sh", str(GEN_CERT), "--out", str(directory), host],
    ):
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"{argv[1]} failed: {result.stderr}")
    return {"cert": directory / "server.crt", "key": directory / "server.key"}


def build_agent(work_dir: Path) -> FakeAgent:
    """A `FakeAgent` over writable copies of the fixture handshake directory
    and log file, and the fixture bettercap session payload.

    Copied rather than used in place, the same reason `tests/conftest.py`'s
    `handshake_dir` fixture copies it: sidecar writing is part of what the
    running plugin does on a real `on_handshake`, so the directory has to be
    writable and disposable.
    """
    handshake_dir = work_dir / "handshakes"
    shutil.copytree(FIXTURES_DIR / "handshakes", handshake_dir)
    log_file = work_dir / "pwnagotchi.log"
    shutil.copyfile(FIXTURES_DIR / "pwnagotchi.log", log_file)
    session_payload = json.loads((FIXTURES_DIR / "bettercap_session.json").read_text())
    return FakeAgent(
        peers=default_peers(),
        session_payload=session_payload,
        config={
            "bettercap": {"handshakes": str(handshake_dir)},
            "main": {"log": {"path": str(log_file)}},
        },
    )


def broadcast(
    clients: "companion.ClientSet", listeners: "companion.Listeners", message: dict
) -> None:
    """Sends one message to every connected client. A dead client is dropped.

    The same technique `Companion.broadcast` uses, reading `listeners._loop`
    from outside the `Listeners` class the way that method already does in
    `plugin/companion.py` - this script stands in for `Companion` itself, not
    for a caller external to it.
    """
    if not message:
        return
    payload = json.dumps(message)
    loop = listeners._loop
    if loop is None:
        return
    for client in clients.snapshot():
        try:
            asyncio.run_coroutine_threadsafe(client.send(payload), loop)
        except Exception:
            clients.discard(client)


def stats_ticker(
    router: "companion.Router",
    clients: "companion.ClientSet",
    listeners: "companion.Listeners",
    deps: "companion.Deps",
    interval: float,
    stop: threading.Event,
) -> None:
    """Broadcasts `stats` and `keepalive` together, on the plugin's own cadence.

    `Companion._stats_ticker`'s own behaviour (SPEC 4.3.7, issue #65),
    reproduced here rather than reached through a `Companion` instance: SPEC
    10.5 names `Router` and `Listeners` as what this script runs for real,
    and the ticker is the small piece of glue between them that a plugin
    object would otherwise own. `router.initial_burst()` already puts `stats`
    on the wire the moment a client is admitted (SPEC 2.4); this loop is what
    keeps it arriving afterwards, so a client that stays connected sees the
    same cadence a real unit provides.
    """
    while not stop.is_set():
        if len(clients):
            now = deps.now()
            # Two separate try blocks, the same shape `Companion._stats_ticker`
            # uses: a repeatable stats failure must not also silence the
            # keepalive an idle client relies on to tell a quiet link from a
            # dead one. Logged by exception type only, never by message - a
            # message here could carry a fixture value onto a log a client can
            # read back (SPEC 2.4).
            try:
                broadcast(clients, listeners, companion.envelope("keepalive", {}, now))
            except Exception as err:
                log.warning("e2e-unit: keepalive broadcast failed: %s", type(err).__name__)
            try:
                broadcast(clients, listeners, companion.envelope("stats", router.stats(), now))
            except Exception as err:
                log.warning("e2e-unit: stats broadcast failed: %s", type(err).__name__)
        stop.wait(interval)


def build_options(
    host: str, ws_port: int, http_port: int, web_root: Path, cert: Path, key: Path
) -> dict[str, Any]:
    return {
        "bind_addresses": [host],
        "ws_port": ws_port,
        "http_port": http_port,
        "tls_cert": str(cert),
        "tls_key": str(key),
        "web_root": str(web_root),
        "token": "",
        "auth_timeout": 10,
        "pisugar": True,
        "gps_source": "auto",
        "gpsd_host": "127.0.0.1",
        "gpsd_port": 2947,
        "session_poll_interval": 5,
        "rebind_interval": 30,
        "keepalive_interval": 5,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ws-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    work_dir = Path(tempfile.mkdtemp(prefix="e2e-unit-"))
    try:
        web_root = work_dir / "web"
        web_root.mkdir()
        (web_root / "index.html").write_text(
            "<!doctype html><title>e2e fake unit</title><div id=app></div>"
        )

        pki_dir = work_dir / "pki"
        pki_dir.mkdir()
        material = issue_certificate(pki_dir, args.host)

        options = build_options(
            args.host, args.ws_port, args.http_port, web_root, material["cert"], material["key"]
        )

        harness = DepsHarness()
        harness.local_ipv4 = [args.host]
        deps = harness.deps

        agent = build_agent(work_dir)
        agent_getter = lambda: agent  # noqa: E731

        session = companion.SessionCache(agent_getter, deps)
        gps = companion.GpsResolver(options, deps, session)
        battery = companion.BatteryReader(options, deps)
        handshake_store = lambda: companion.HandshakeStore(  # noqa: E731
            agent._config["bettercap"]["handshakes"]
        )
        router = companion.Router(
            options,
            deps,
            agent_getter,
            session,
            gps,
            battery,
            handshake_store,
            companion.__version__,
        )

        ssl_context = companion.build_ssl_context(str(material["cert"]), str(material["key"]))
        if ssl_context is None:
            print("e2e-unit: the generated certificate could not be loaded", file=sys.stderr)
            return 1

        clients = companion.ClientSet()
        handler = companion.make_connection_handler(
            router, deps, float(options["auth_timeout"]), clients
        )
        listeners = companion.Listeners(options, deps, ssl_context, handler)

        # Registered before the listener is even started, not after the ready
        # line below: a signal that arrives in the gap between announcing
        # readiness and installing the handler would fall through to the
        # default disposition and kill the process without ever reaching
        # `listeners.stop()`. There is nothing yet for a caller to tear down
        # this early, so `shutdown.set()` here just means the wait below
        # returns immediately once the listener is up.
        shutdown = threading.Event()

        def _handle_signal(signum: int, frame: Any) -> None:
            shutdown.set()

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        # `reconcile()` is synchronous - it does not return until the sockets
        # it opens are actually bound and serving (SPEC: its own docstring
        # calls the cost "a real bound, not a best case") - so its return
        # value is the authority on what is actually listening, unlike a bare
        # TCP probe against the configured port: that would also succeed
        # against a stale process already sitting on it, or against a bind
        # `reconcile()` itself refused and only logged as a warning. The WSS
        # half specifically is checked, not just that `args.host` bound
        # something: `_bound[ip]` is `(ws_server, http_server)` and `_open`
        # can register an entry with either one `None` when only one of the
        # two ports was refused.
        bound = listeners.reconcile()
        entry = listeners._bound.get(args.host)
        if args.host not in bound or entry is None or entry[0] is None:
            print(
                f"e2e-unit: Listeners did not bind a WSS socket on {args.host}:{args.ws_port}",
                file=sys.stderr,
            )
            listeners.stop()
            return 1

        stop = threading.Event()
        ticker = threading.Thread(
            target=stats_ticker,
            args=(router, clients, listeners, deps, STATS_INTERVAL_S, stop),
            daemon=True,
        )
        ticker.start()

        # Exactly one line, once the listener is ready, and nothing else on
        # stdout after it: playwright.config.ts's webServer reads this to know
        # the server is up.
        print(f"e2e-unit: wss://{args.host}:{args.ws_port}", flush=True)

        while not shutdown.wait(SHUTDOWN_POLL_S):
            pass
        stop.set()
        listeners.stop()
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
