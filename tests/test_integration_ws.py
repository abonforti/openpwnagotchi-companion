"""The WebSocket contract end to end, over a real TLS socket.

SPEC 10.4. Everything else in this suite drives `Router` directly, which is the
right level for the mapping rules but says nothing about the transport. This
file is the only place where a real client speaks to a real listener over a real
certificate, so it is where three classes of failure are caught:

1. **`websockets` API drift (SPEC 2.3.2).** The image pins no version, and the
   server API changed incompatibly at 14 - handler signature and the location of
   `serve`. A unit test cannot see that; this one runs against whatever CI
   resolves.
2. **The TLS material actually working.** The certificate is generated here by
   `tools/gen-ca.sh` and `tools/gen-cert.sh`, so the scripts are exercised for
   real and the `iPAddress` SAN requirement is checked by a client that verifies
   it rather than by reading the certificate back.
3. **Connection lifecycle** (SPEC 2.3.3, 2.3.4) - the auth deadline, a parse
   failure that must not kill the handler, and a client vanishing mid-broadcast.

Every reply is validated against `docs/schemas/`, which D15 makes the authority.

Ports are ephemeral and every listener binds `127.0.0.1`, so a parallel run does
not collide. Nothing sleeps for a fixed interval: waiting is a poll with a
deadline, and the fixture stops the listeners so no socket or thread outlives
the test.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from conftest import DepsHarness
from plugin import companion

try:  # websockets >= 14 moved the asyncio client
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # only on websockets < 14
    from websockets import connect as ws_connect

from websockets.exceptions import ConnectionClosed

REPO_ROOT = Path(__file__).resolve().parent.parent
GEN_CA = REPO_ROOT / "tools" / "gen-ca.sh"
GEN_CERT = REPO_ROOT / "tools" / "gen-cert.sh"

HOST = "127.0.0.1"
TOKEN = "s3cr3t-shared-token"

# Wall-clock budget for a single frame. Generous, because CI on a shared runner
# is slow, but bounded so a hang fails rather than blocks.
RECV_TIMEOUT = 10.0
POLL_DEADLINE = 5.0

# The unprompted burst SPEC 2.4 requires on accept.
BURST = ["stats", "access_points", "face_status"]

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None,
    reason="openssl is not on PATH; the throwaway PKI cannot be generated",
)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def validator_for(schemas_dir):
    common = Resource.from_contents(
        json.loads((schemas_dir / "common.json").read_text(encoding="utf-8")),
        default_specification=DRAFT202012,
    )
    registry = Registry().with_resources(
        [
            ("common.json", common),
            ("incoming/common.json", common),
            ("outgoing/common.json", common),
        ]
    )

    def _validator(kind: str) -> Draft202012Validator:
        schema = json.loads(
            (schemas_dir / "outgoing" / f"{kind}.json").read_text(encoding="utf-8")
        )
        # The $id is relative, so dropping it leaves refs resolving against the
        # registry rather than against "outgoing/".
        schema.pop("$id", None)
        return Draft202012Validator(schema, registry=registry)

    return _validator


@pytest.fixture
def check(validator_for):
    """Validates one message against its outgoing schema and returns it."""

    def _check(message: dict) -> dict:
        assert "type" in message, message
        validator_for(message["type"]).validate(message)
        return message

    return _check


# ---------------------------------------------------------------------------
# The throwaway PKI
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pki(tmp_path_factory) -> dict[str, Path]:
    """A CA and a `127.0.0.1` server certificate, from the real scripts.

    Session-scoped because generating a 4096-bit CA is the slowest thing in the
    suite, and because reusing it is exactly what a user does.
    """
    for script in (GEN_CA, GEN_CERT):
        assert script.is_file(), f"{script} does not exist (SPEC section 3)"

    directory = tmp_path_factory.mktemp("pki")
    for argv in (
        ["sh", str(GEN_CA), "--out", str(directory)],
        ["sh", str(GEN_CERT), "--out", str(directory), HOST],
    ):
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, f"{argv[1]} failed: {result.stderr}"

    return {
        "ca": directory / "ca.crt",
        "cert": directory / "server.crt",
        "key": directory / "server.key",
    }


@pytest.fixture
def client_ssl(pki) -> ssl.SSLContext:
    """A client context that trusts only the throwaway CA.

    Verification stays on: the point of the iPAddress SAN requirement is that a
    verifying client accepts the certificate, and turning off the check would
    remove the only test that proves it.
    """
    return ssl.create_default_context(cafile=str(pki["ca"]))


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


def reserve_ports(count: int) -> list[int]:
    """`count` distinct loopback ports that were free a moment ago.

    All the probes are held open at once so the kernel cannot hand out the same
    port twice, which is what makes a parallel run safe.
    """
    probes = []
    try:
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind((HOST, 0))
            probes.append(probe)
        return [probe.getsockname()[1] for probe in probes]
    finally:
        for probe in probes:
            probe.close()


def wait_until(predicate, deadline: float = POLL_DEADLINE, interval: float = 0.02):
    """Polls until `predicate` is truthy or the deadline passes."""
    limit = time.monotonic() + deadline
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def is_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        return probe.connect_ex((HOST, port)) == 0


class RunningServer:
    def __init__(self, url, port, harness, agent, router, clients, listeners):
        self.url = url
        self.port = port
        self.harness = harness
        self.agent = agent
        self.router = router
        self.clients = clients
        self.listeners = listeners


@pytest.fixture
def wss_server(tmp_path, pki, agent_factory, options):
    """Starts the plugin's WSS listener on a loopback ephemeral port.

    `Listeners.reconcile()` is driven with a faked `resolve_interface_ip` so the
    tether interface resolves to `127.0.0.1`; nothing else about the binding path
    is bypassed, so this is the same code the device runs.
    """
    started: list = []

    def _start(*, token: str = "", overrides: dict | None = None, agent=None):
        ws_port, http_port = reserve_ports(2)
        web_root = tmp_path / f"web-{ws_port}"
        web_root.mkdir()
        (web_root / "index.html").write_text("<!doctype html><title>t</title>")

        resolved = dict(options)
        resolved.update(
            {
                "interfaces": ["bnep0"],
                "ws_port": ws_port,
                "http_port": http_port,
                "tls_cert": str(pki["cert"]),
                "tls_key": str(pki["key"]),
                "web_root": str(web_root),
                "token": token,
            }
        )
        resolved.update(overrides or {})

        harness = DepsHarness()
        harness.interface_ips = {"bnep0": HOST}
        deps = harness.deps
        subject = agent if agent is not None else agent_factory()

        # Assembled exactly as conftest's router_factory does, so the object
        # under the socket is the one the unit tests describe.
        session = companion.SessionCache(
            lambda: subject, deps, resolved["session_poll_interval"]
        )
        router = companion.Router(
            resolved,
            deps,
            lambda: subject,
            session,
            companion.GpsResolver(resolved, deps, session),
            companion.BatteryReader(resolved, deps),
            lambda: companion.HandshakeStore(
                subject._config["bettercap"]["handshakes"]
            ),
            companion.__version__,
        )

        ssl_context = companion.build_ssl_context(
            str(pki["cert"]), str(pki["key"])
        )
        assert ssl_context is not None, "the generated certificate must be usable"

        # The seam of SPEC 2.3.3, named and shaped exactly as pinned there.
        clients = companion.ClientSet()
        handler = companion.make_connection_handler(
            router, deps, resolved["auth_timeout"], clients
        )

        listeners = companion.Listeners(resolved, deps, ssl_context, handler)
        started.append(listeners)
        listeners.reconcile()
        assert wait_until(lambda: is_listening(ws_port)), (
            f"no WSS listener came up on {HOST}:{ws_port}"
        )

        return RunningServer(
            f"wss://{HOST}:{ws_port}",
            ws_port,
            harness,
            subject,
            router,
            clients,
            listeners,
        )

    yield _start

    for listeners in started:
        listeners.stop()


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

# `keepalive` arrives on the plugin's own schedule (PROTOCOL.md, lifecycle 5),
# so anything waiting for a reply has to step over it rather than mistake it for
# the answer.
UNSOLICITED = {"keepalive"}


async def recv_message(websocket, skip=UNSOLICITED) -> dict:
    while True:
        raw = await asyncio.wait_for(websocket.recv(), RECV_TIMEOUT)
        message = json.loads(raw)
        if message.get("type") not in skip:
            return message


async def recv_many(websocket, count: int, skip=UNSOLICITED) -> list[dict]:
    return [await recv_message(websocket, skip) for _ in range(count)]


async def authenticate(websocket, check, token=None) -> dict:
    """Sends the auth frame and consumes its acknowledgment.

    A successful auth is acknowledged even with no `message_id`, which is the
    one exception to the rule that an acknowledgment answers a `message_id`
    (SPEC 2.3.4): failure is an error and a close, so without it a client cannot
    tell success from a link that has simply gone quiet. The burst follows it.
    """
    await send(websocket, {"type": "auth", "token": TOKEN if token is None else token})
    acknowledgment = await recv_message(websocket)
    assert acknowledgment["type"] == "acknowledgment", acknowledgment
    assert acknowledgment["data"]["acknowledged"] == "auth"
    check(acknowledgment)
    return acknowledgment


async def expect_burst(websocket, check) -> list[dict]:
    messages = await recv_many(websocket, len(BURST))
    assert [message["type"] for message in messages] == BURST
    for message in messages:
        check(message)
        assert "message_id" not in message
    return messages


async def send(websocket, message: dict) -> None:
    await websocket.send(json.dumps(message))


def close_code_of(closed: ConnectionClosed, websocket) -> int | None:
    """The close code, wherever this `websockets` version keeps it."""
    received = getattr(closed, "rcvd", None)
    if received is not None:
        return received.code
    code = getattr(closed, "code", None)
    if code is not None:
        return code
    return websocket.close_code


def run(coroutine):
    """Runs one scenario on its own loop, which is then closed.

    `asyncio.run` rather than a session loop or pytest-asyncio: each test gets a
    fresh loop that is torn down with it, so no task or transport survives into
    the next one and `filterwarnings = error` stays quiet.
    """
    return asyncio.run(coroutine)


# ---------------------------------------------------------------------------
# Connect and the initial burst
# ---------------------------------------------------------------------------


def test_a_client_that_trusts_the_ca_connects_over_tls(wss_server, client_ssl, check):
    """The whole chain: generated CA, generated certificate, verifying client.

    A missing or `DNS:`-only SAN fails right here, which is the point of
    generating the material with the real scripts instead of a fixture.
    """
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)

    run(scenario())


def test_a_client_that_does_not_trust_the_ca_is_refused(wss_server):
    server = wss_server()

    async def scenario():
        # A default context knows nothing about the throwaway CA, and there is
        # no plaintext port to fall back to (D4).
        with pytest.raises(ssl.SSLError):
            async with ws_connect(server.url, ssl=ssl.create_default_context()):
                pass

    run(scenario())


def test_the_burst_arrives_before_any_request(wss_server, client_ssl, check):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            first = await recv_message(websocket)
            assert first["type"] == "stats"
            check(first)

    run(scenario())


# ---------------------------------------------------------------------------
# Every command in SPEC 2.4
# ---------------------------------------------------------------------------

# Requests whose reply is a substantive message. Driven with a `message_id`, so
# each one is `acknowledgment` then the reply (PROTOCOL.md, requests and
# replies).
REQUEST_REPLIES = [
    ({"type": "get_stats"}, "stats"),
    ({"type": "get_access_points"}, "access_points"),
    ({"type": "get_handshakes"}, "handshakes_list"),
    ({"type": "get_peers"}, "peers_list"),
    ({"type": "get_log", "lines": 5}, "log_lines"),
    ({"type": "get_face_status"}, "face_status"),
    ({"type": "get_screen"}, "screen_image"),
    ({"type": "get_gps_data"}, "gps_update"),
    ({"type": "ping"}, "pong"),
]


@pytest.mark.parametrize(
    "request_message,reply_type",
    REQUEST_REPLIES,
    ids=[message["type"] for message, _ in REQUEST_REPLIES],
)
def test_every_request_gets_its_reply_over_the_wire(
    wss_server, client_ssl, check, frame_file, request_message, reply_type
):
    server = wss_server()
    outgoing = dict(request_message, message_id="c7f1")

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, outgoing)
            replies = await recv_many(websocket, 2)

        assert [reply["type"] for reply in replies] == ["acknowledgment", reply_type]
        for reply in replies:
            check(reply)
            assert reply["message_id"] == "c7f1"
        assert replies[0]["data"]["acknowledged"] == request_message["type"]

    run(scenario())


def test_a_request_without_a_message_id_gets_no_acknowledgment(
    wss_server, client_ssl, check
):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "get_stats"})
            reply = await recv_message(websocket)

        assert reply["type"] == "stats"
        assert "message_id" not in reply
        check(reply)

    run(scenario())


def test_the_message_id_is_echoed_verbatim(wss_server, client_ssl, check):
    server = wss_server()
    identifier = "☃ not a uuid at all"

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "ping", "message_id": identifier})
            return await recv_many(websocket, 2)

    replies = run(scenario())

    assert [reply["message_id"] for reply in replies] == [identifier, identifier]


def test_gps_data_is_accepted_and_read_back(wss_server, client_ssl, check):
    """`gps_data` stores a browser reading; `get_gps_data` is how it is observed.

    SPEC 2.4 pins `gps_data` as acknowledgment-only: it is driven with a
    `message_id`, and a second frame arriving here would be the bug.
    """
    server = wss_server()
    reading = {"latitude": -33.512345, "longitude": -25.098765, "accuracy": 18.0}

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, dict(reading, type="gps_data", message_id="c7f1"))
            acknowledgment = await recv_message(websocket)
            await send(websocket, {"type": "get_gps_data"})
            return acknowledgment, await recv_message(websocket)

    acknowledgment, update = run(scenario())

    assert acknowledgment["type"] == "acknowledgment"
    check(acknowledgment)
    assert update["type"] == "gps_update"
    check(update)
    assert update["data"]["source"] == "browser"
    # Incoming and outgoing name the coordinates differently on purpose: the
    # command carries `latitude`/`longitude`, the Gps shape in common.json is
    # `lat`/`lon`, and the schema is the authority (D15).
    assert update["data"]["lat"] == pytest.approx(reading["latitude"])
    assert update["data"]["lon"] == pytest.approx(reading["longitude"])


def test_set_pasv_is_acknowledged_when_the_plugin_is_loaded(
    wss_server, client_ssl, check, load_pasv, agent_factory
):
    load_pasv()
    server = wss_server(agent=agent_factory(mode="auto"))

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "set_pasv", "on": True, "message_id": "c7f2"})
            return await recv_message(websocket)

    reply = run(scenario())

    assert reply["type"] == "acknowledgment"
    check(reply)


def test_set_pasv_without_the_plugin_is_an_error(wss_server, client_ssl, check):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "set_pasv", "on": True})
            return await recv_message(websocket)

    reply = run(scenario())

    assert reply["type"] == "error"
    assert reply["data"]["code"] == "pasv_unavailable"
    check(reply)


DISRUPTIVE = [
    ({"type": "set_mode", "mode": "manual"}, "restart_pwnagotchi"),
    ({"type": "reboot"}, "reboot_device"),
    ({"type": "shutdown"}, "shutdown_device"),
]


@pytest.mark.parametrize(
    "request_message,effect",
    DISRUPTIVE,
    ids=[message["type"] for message, _ in DISRUPTIVE],
)
def test_a_disruptive_command_broadcasts_restarting_before_acting(
    wss_server, client_ssl, check, request_message, effect
):
    """The ordering PROTOCOL.md fixes: acknowledgment, broadcast, flush, act.

    The effect itself is a recorded `spawn`, never a real restart - the harness
    seam is what makes it safe to drive these over a live socket at all.
    """
    server = wss_server()
    outgoing = dict(request_message, message_id="c7f3")
    already_spawned = len(server.harness.spawned)

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, outgoing)
            replies = await recv_many(websocket, 2)
            # SPEC 2.6.1: the plugin does not close the socket after
            # `restarting`. Closing first would replace an informative
            # "restarting" state in the app with a plain disconnect, which is
            # the ambiguity the broadcast exists to remove.
            await send(websocket, {"type": "ping"})
            return replies, await recv_message(websocket)

    replies, pong = run(scenario())

    assert [reply["type"] for reply in replies] == ["acknowledgment", "restarting"]
    for reply in replies:
        check(reply)
    assert pong["type"] == "pong"
    # The action is scheduled only after the frames are out, so give the server
    # thread a bounded moment rather than a fixed sleep.
    assert wait_until(
        lambda: len(server.harness.spawned) > already_spawned
    ), "the effect was never scheduled"
    for target in server.harness.spawned[already_spawned:]:
        target()

    assert effect in server.harness.names()
    # D12: the mode is fixed at process start, so writing the attribute changes
    # the reported mode and nothing else.
    assert server.agent.mode == "auto"


def test_set_mode_broadcasts_the_wire_vocabulary_not_the_restart_argument(
    wss_server, client_ssl, check
):
    """`pwnagotchi.restart` takes MANU/AUTO; the wire takes AUTO/PASV/MANUAL.

    Sending the restart argument to the client emits a value no schema accepts,
    which is exactly the prose bug SPEC 2.6.1 records having made once.
    """
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "set_mode", "mode": "manual"})
            return await recv_message(websocket)

    restarting = run(scenario())

    assert restarting["type"] == "restarting"
    check(restarting)
    assert restarting["data"]["mode"] == "MANUAL"
    assert restarting["data"]["reason"] == "mode_change"


# ---------------------------------------------------------------------------
# Bad input
# ---------------------------------------------------------------------------


def test_an_unknown_command_is_an_error_not_a_disconnect(wss_server, client_ssl, check):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "get_the_moon"})
            error = await recv_message(websocket)
            # Still usable afterwards: an unknown type is a client running a
            # newer version, not an attack.
            await send(websocket, {"type": "ping"})
            return error, await recv_message(websocket)

    error, pong = run(scenario())

    assert error["type"] == "error"
    assert error["data"]["code"] == "unknown_command"
    check(error)
    assert pong["type"] == "pong"


@pytest.mark.parametrize(
    "payload",
    ["not json at all", "{", '{"type": "ping",}', ""],
    ids=["garbage", "truncated", "trailing-comma", "empty"],
)
def test_malformed_json_never_kills_the_connection(
    wss_server, client_ssl, check, payload
):
    """SPEC 2.4: a parse failure must not kill the connection handler.

    The proof is not the error frame but the `pong` after it - a handler that
    logged and returned would look identical up to the first assertion.
    """
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await websocket.send(payload)
            error = await recv_message(websocket)
            await send(websocket, {"type": "ping"})
            return error, await recv_message(websocket)

    error, pong = run(scenario())

    assert error["type"] == "error"
    assert error["data"]["code"] == "bad_request"
    check(error)
    assert pong["type"] == "pong"


@pytest.mark.parametrize(
    "payload",
    ["[1, 2, 3]", "42", '"ping"', "null", "true"],
    ids=["array", "number", "string", "null", "boolean"],
)
def test_valid_json_that_is_not_an_object_is_a_bad_request(
    wss_server, client_ssl, check, payload
):
    """Parses cleanly and has no `type`, so neither branch is the obvious one.

    Every incoming schema declares `"type": "object"`, so a non-object cannot
    conform and `bad_request` is the only code in the closed enum that fits.
    """
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await websocket.send(payload)
            error = await recv_message(websocket)
            await send(websocket, {"type": "ping"})
            return error, await recv_message(websocket)

    error, pong = run(scenario())

    assert error["type"] == "error"
    assert error["data"]["code"] == "bad_request"
    check(error)
    assert pong["type"] == "pong"


def test_an_unexpected_key_is_a_bad_request(wss_server, client_ssl, check):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            await send(websocket, {"type": "get_stats", "unexpected": 1})
            return await recv_message(websocket)

    error = run(scenario())

    assert error["type"] == "error"
    assert error["data"]["code"] == "bad_request"
    check(error)


# ---------------------------------------------------------------------------
# Authentication (SPEC 2.3.4)
# ---------------------------------------------------------------------------


def test_no_token_configured_means_no_handshake(wss_server, client_ssl, check):
    server = wss_server(token="")

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            # The burst arrives without the client having said anything, which
            # is only true when auth is skipped entirely (D5).
            await expect_burst(websocket, check)
            await send(websocket, {"type": "ping"})
            return await recv_message(websocket)

    assert run(scenario())["type"] == "pong"


def test_the_right_token_opens_the_connection(wss_server, client_ssl, check):
    server = wss_server(token=TOKEN)

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await authenticate(websocket, check)
            await expect_burst(websocket, check)
            await send(websocket, {"type": "ping"})
            return await recv_message(websocket)

    assert run(scenario())["type"] == "pong"


def test_the_burst_does_not_precede_authentication(wss_server, client_ssl, check):
    """With a token configured, nothing goes out until the auth frame succeeds.

    The burst carries the unit's stats, its access points and its face. Sending
    it on accept would hand all of that to anyone able to open a socket, which
    makes the token worthless for reading (SPEC 2.4).
    """
    server = wss_server(token=TOKEN)

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            # Nothing at all until the client authenticates.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(websocket.recv(), 1.0)
            await authenticate(websocket, check)
            return await expect_burst(websocket, check)

    assert [message["type"] for message in run(scenario())] == BURST


def test_an_unauthenticated_client_is_not_in_the_broadcast_set(
    wss_server, client_ssl, check
):
    """Holding back the burst is worth nothing if the push channel still fans out.

    `Companion.broadcast` writes to every member of the `ClientSet` and to
    nothing else, so membership *is* the rule: a socket that joined on accept
    would receive `wifi_update`, `handshake`, `peer_detected` and
    `status_change` - the same data the burst was held back from - for the whole
    auth_timeout window, renewable by reconnecting (SPEC 2.4).

    The assertion is on the set rather than on what an eavesdropping socket
    receives, because this fixture drives `Listeners` directly and never builds
    a `Companion`: nothing here calls `broadcast()`, so a silent socket stays
    silent even with the rule removed, and such a test would pass while
    guarding nothing.
    """
    server = wss_server(token=TOKEN, overrides={"auth_timeout": 30})

    async def scenario():
        eavesdropper = await ws_connect(server.url, ssl=client_ssl)
        try:
            # Connected, TLS complete, not authenticated: not a broadcast
            # target. Polled rather than asserted once, so a socket admitted a
            # moment late still fails.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                assert len(server.clients) == 0, "admitted before authenticating"
                await asyncio.sleep(0.1)

            authenticated = await ws_connect(server.url, ssl=client_ssl)
            try:
                await authenticate(authenticated, check)
                await expect_burst(authenticated, check)
                # Exactly one: the socket that authenticated, and not the other.
                assert len(server.clients) == 1
            finally:
                await authenticated.close()
        finally:
            await eavesdropper.close()

    run(scenario())


def test_a_wrong_token_is_one_error_then_close_1008(wss_server, client_ssl, check):
    server = wss_server(token=TOKEN)

    async def scenario():
        frames = []
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await send(websocket, {"type": "auth", "token": "wrong"})
            try:
                while True:
                    frames.append(await recv_message(websocket))
            except ConnectionClosed as closed:
                return frames, close_code_of(closed, websocket)

    frames, code = run(scenario())

    assert [frame["type"] for frame in frames] == ["error"]
    assert frames[0]["data"]["code"] == "unauthorized"
    check(frames[0])
    assert code == 1008
    # Never say whether it was wrong, malformed or absent, and never echo it.
    assert TOKEN not in json.dumps(frames[0])
    assert "wrong" not in frames[0]["data"]["message"]


def test_a_command_before_auth_is_refused_and_the_socket_closes(
    wss_server, client_ssl, check
):
    """SPEC 2.3.4: *any* `unauthorized` closes with 1008, not only a bad `auth`.

    The contract is that the auth frame comes first, so a command arriving
    before it is a broken or probing client, and leaving the socket open serves
    neither.
    """
    server = wss_server(token=TOKEN, overrides={"auth_timeout": 1})

    async def scenario():
        frames = []
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await send(websocket, {"type": "get_stats"})
            try:
                while True:
                    frames.append(await recv_message(websocket))
            except ConnectionClosed as closed:
                return frames, close_code_of(closed, websocket)

    frames, code = run(scenario())

    assert [frame["type"] for frame in frames] == ["error"]
    assert frames[0]["data"]["code"] == "unauthorized"
    check(frames[0])
    assert code == 1008


def test_a_silent_client_is_closed_after_the_auth_timeout(wss_server, client_ssl):
    """The hard requirement of SPEC 2.3.4.

    Without the deadline an unauthenticated peer holds a socket open forever,
    and on a Pi Zero over Bluetooth that is the entire resource budget.

    The deadline is measured with `deps.now()`, so it is driven by advancing the
    fake clock rather than by waiting: the test is then about the rule firing,
    not about how long a shared CI runner took to get round to it.
    """
    server = wss_server(token=TOKEN, overrides={"auth_timeout": 30})

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            # Says nothing, ever. Time passes; the server must hang up on its own.
            server.harness.advance(31)
            with pytest.raises(ConnectionClosed) as raised:
                await asyncio.wait_for(websocket.recv(), RECV_TIMEOUT)
            return close_code_of(raised.value, websocket)

    assert run(scenario()) == 1008


def test_an_authenticated_client_is_not_closed_by_the_deadline(
    wss_server, client_ssl, check
):
    server = wss_server(token=TOKEN, overrides={"auth_timeout": 30})

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await authenticate(websocket, check)
            await expect_burst(websocket, check)
            # Well past the deadline: authenticating must cancel it, not defer it.
            server.harness.advance(300)
            await asyncio.sleep(0.2)
            await send(websocket, {"type": "ping"})
            return await recv_message(websocket)

    assert run(scenario())["type"] == "pong"


# ---------------------------------------------------------------------------
# Several clients, and one that leaves
# ---------------------------------------------------------------------------


def test_two_clients_are_served_independently(wss_server, client_ssl, check):
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as first:
            async with ws_connect(server.url, ssl=client_ssl) as second:
                await expect_burst(first, check)
                await expect_burst(second, check)
                await send(first, {"type": "ping", "message_id": "a"})
                await send(second, {"type": "ping", "message_id": "b"})
                return (
                    await recv_many(first, 2),
                    await recv_many(second, 2),
                )

    first_replies, second_replies = run(scenario())

    assert {reply["message_id"] for reply in first_replies} == {"a"}
    assert {reply["message_id"] for reply in second_replies} == {"b"}


def test_a_client_leaving_mid_broadcast_does_not_take_the_server_down(
    wss_server, client_ssl, check
):
    """A broadcast writes to every socket, and one of them is already gone.

    `restarting` is the broadcast that matters most - it is the one the client
    needs in order to distinguish an expected disconnect from a fault - and it
    is also the one most likely to hit a peer that has just dropped, because
    the whole device is on its way down. A write to a dead socket must be
    swallowed per client, not allowed to abort the fan-out or the listener.
    """
    server = wss_server()

    async def scenario():
        survivor = await ws_connect(server.url, ssl=client_ssl)
        leaver = await ws_connect(server.url, ssl=client_ssl)
        try:
            await expect_burst(survivor, check)
            await expect_burst(leaver, check)

            # Fire and forget: the close races the broadcast on purpose, which
            # is the situation a device restart actually produces.
            closing = asyncio.ensure_future(leaver.close())
            await send(survivor, {"type": "set_mode", "mode": "manual"})
            restarting = await recv_message(survivor)

            await send(survivor, {"type": "ping"})
            pong = await recv_message(survivor)
            await closing
        finally:
            await survivor.close()
            await leaver.close()

        # The listener is still accepting after all that.
        async with ws_connect(server.url, ssl=client_ssl) as latecomer:
            await expect_burst(latecomer, check)

        return restarting, pong

    restarting, pong = run(scenario())

    assert restarting["type"] == "restarting"
    check(restarting)
    assert pong["type"] == "pong"
    assert is_listening(server.port)


def test_a_client_that_vanishes_without_a_close_frame_is_survivable(
    wss_server, client_ssl, check
):
    """Bluetooth PAN drops do not send a close frame; they just stop.

    The connection is aborted at the transport so the server sees a reset rather
    than a handshake, which is the shape of every real tether failure.
    """
    server = wss_server()

    async def scenario():
        survivor = await ws_connect(server.url, ssl=client_ssl)
        vanisher = await ws_connect(server.url, ssl=client_ssl)
        try:
            await expect_burst(survivor, check)
            await expect_burst(vanisher, check)

            transport = getattr(vanisher, "transport", None)
            assert transport is not None, (
                "cannot reach the transport on this websockets version"
            )
            transport.abort()

            await send(survivor, {"type": "get_stats"})
            return await recv_message(survivor)
        finally:
            await survivor.close()

    stats = run(scenario())

    assert stats["type"] == "stats"
    check(stats)


# ---------------------------------------------------------------------------
# The rule the whole plugin exists to keep
# ---------------------------------------------------------------------------


def test_serving_a_client_never_calls_session_on_the_request_path(
    wss_server, client_ssl, check, frame_file
):
    """`agent.session()` is a 30-second-timeout HTTP GET (SPEC 2.5, F7).

    One call from inside the handler stalls every connected client for up to
    half a minute, so the count is checked after driving the request path over a
    real socket, not only through `Router` in isolation.
    """
    server = wss_server()

    async def scenario():
        async with ws_connect(server.url, ssl=client_ssl) as websocket:
            await expect_burst(websocket, check)
            for request in ({"type": "get_stats"}, {"type": "get_face_status"}):
                await send(websocket, request)
                check(await recv_message(websocket))

    run(scenario())

    assert server.agent.session_calls == 0
