"""openpwnagotchi-companion - pwnagotchi side of a self-hosted PWA companion.

Serves the built PWA over HTTPS and speaks the WebSocket contract in
docs/schemas/ over WSS, bound only to the tether interfaces. See SPEC.md for the
full brief and, in particular, section 11 for the allowlist of pwnagotchi
symbols this file may use.

Derived from pwnios.py in https://github.com/BraedenP232/PwnIOS (GPL-3.0), used
and redistributed under the GPL with attribution preserved. That project
published the backend of a paid iOS app; this is a hardened fork of it.

Structure, and why it is shaped this way:

    pure helpers      parsing and mapping, no I/O, trivially testable
    Deps              every external effect as a replaceable callable
    components        SessionCache, GpsResolver, BatteryReader, HandshakeStore
    Router            the protocol core: a message in, messages out, no sockets
    transport         WSS and HTTPS servers, the only asyncio-aware layer
    Companion         the pwnagotchi plugin shell, deliberately thin

The Router split is what makes the coverage gate reachable: every command,
error path and edge case can be exercised without a socket, an event loop, or a
running pwnagotchi. The transport layer above it stays small enough to be
covered by the integration test.
"""

from __future__ import annotations

import base64
import dataclasses
import hmac
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

import pwnagotchi
import pwnagotchi.plugins as plugins

__author__ = "https://github.com/abonforti"
__version__ = "0.0.1"
__license__ = "GPL3"
__description__ = (
    "Self-hosted companion backend: serves an installable PWA over HTTPS and "
    "speaks a WebSocket protocol to it, bound only to tether interfaces, TLS only."
)
__name_of_plugin__ = "companion"

__help__ = """
Serves the openpwnagotchi-companion PWA from the unit itself and speaks its
WebSocket protocol. Both listeners are TLS only and bind exclusively to the
IPv4 addresses of the configured tether interfaces; the plugin will not start a
listener without a usable certificate, and never falls back to plaintext.

You need a certificate whose subjectAltName lists each Pi IP as an iPAddress
(not DNS), issued by a CA you install and fully trust on the phone. See
docs/CERTIFICATES.md - iOS is strict here and fails silently when it is not met.

    [main.plugins.companion]
    enabled    = true
    interfaces = ["bnep0", "usb0"]   # bind on each one's current IPv4, skip if absent
    ws_port    = 8082                # WSS
    http_port  = 8443                # HTTPS, serves the PWA
    tls_cert   = "/etc/pwnagotchi/companion/server.crt"
    tls_key    = "/etc/pwnagotchi/companion/server.key"
    web_root   = "/var/www/openpwn-companion"
    token      = ""                  # optional shared secret, empty disables auth

Full option reference and defaults: SPEC.md section 2.2.
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "interfaces": ["bnep0", "usb0"],
    "ws_port": 8082,
    "http_port": 8443,
    "tls_cert": "/etc/pwnagotchi/companion/server.crt",
    "tls_key": "/etc/pwnagotchi/companion/server.key",
    "web_root": "/var/www/openpwn-companion",
    "token": "",
    "auth_timeout": 10,
    "pisugar": True,
    "gps_source": "auto",
    "gpsd_host": "127.0.0.1",
    "gpsd_port": 2947,
    "session_poll_interval": 5,
    "rebind_interval": 30,
    "save_gps_log": False,
    "gps_log_path": "/var/tmp/pwnagotchi_gps.log",
    "mirror_auto_interval": 5,
}

HANDSHAKE_LIMIT = 500
LOG_LINES_DEFAULT = 200
LOG_LINES_MAX = 1000
BROWSER_GPS_TTL = 10.0

# pwnagotchi names captures SSID_BSSID.pcapng, the BSSID lowercased without
# separators. SSIDs legitimately contain underscores, so the split is anchored at
# the end of the name rather than the first separator.
HANDSHAKE_NAME = re.compile(r"^(?P<ssid>.*)_(?P<bssid>[0-9a-fA-F]{12})$")

log = logging.getLogger(__name__)


def option(options: Mapping[str, Any], key: str) -> Any:
    """Reads a configuration value, falling back to the DEFAULTS entry.

    A missing key must never raise: pwnagotchi merges user config over plugin
    defaults inconsistently across versions, and a KeyError at load time takes
    the whole plugin down.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def envelope(kind: str, data: Any, timestamp: float, message_id: str | None = None) -> dict:
    """Builds an outgoing message: payload under `data`, `timestamp` always present.

    `message_id` is included only when the request carried one. See
    docs/PROTOCOL.md for why incoming is flat while outgoing is enveloped.
    """
    raise NotImplementedError


def error_envelope(code: str, message: str, timestamp: float, message_id: str | None = None) -> dict:
    """Builds an `error` message. `code` must be one of common.json ErrorCode."""
    raise NotImplementedError


def mac_of(value: Any) -> str | None:
    """Extracts a MAC from either a bettercap dict or a bare string.

    on_handshake is fired from two code paths with different argument types
    (SPEC F20): MAC strings on one, bettercap dicts on the other. Returns None
    for anything else rather than guessing.
    """
    raise NotImplementedError


def hdop_to_metres(hdop: Any) -> float | None:
    """Converts a horizontal dilution of precision into a rough accuracy in metres.

    Uses a nominal 5 m user-equivalent range error, so accuracy is 5 * HDOP.
    Returns None when HDOP is absent, unparseable or non-positive, because a
    fabricated accuracy is worse than an honest absence.
    """
    raise NotImplementedError


def parse_handshake_filename(filename: str) -> tuple[str, str | None]:
    """Splits a capture filename into (ssid, bssid).

    Anchors on a trailing 12-hex-digit group, so an SSID containing underscores
    survives. Returns (whole basename, None) when the name does not match:
    the BSSID is reported as unknown rather than guessed.
    """
    raise NotImplementedError


def map_access_point(ap: Mapping[str, Any]) -> dict:
    """Maps one bettercap AP dict to the wire shape.

    The bettercap key is `mac`; there is no `bssid` (SPEC F4) - reading `bssid`
    is the upstream bug this fork exists partly to fix. `clients` may be absent
    or None, so its length is taken defensively.
    """
    raise NotImplementedError


def normalise_peer(peer: Any) -> dict:
    """Maps a pwnagotchi.mesh.peer.Peer object to the wire shape.

    Peers are objects, not dicts, and mix attributes with methods (SPEC F16).
    Note the timestamp asymmetry that this function exists to hide: `last_seen`
    is already an epoch float while `first_seen`, `prev_seen` and `first_met`
    are datetime objects - and can be plain strings when Peer's own constructor
    fell back after a parse failure. Everything leaves here as an epoch float or
    None. Every accessor is individually guarded; one bad field must not lose
    the whole peer.
    """
    raise NotImplementedError


def to_epoch(value: Any) -> float | None:
    """Coerces a datetime, epoch number or timestamp string into an epoch float."""
    raise NotImplementedError


def tail_lines(path: str, count: int) -> list[str]:
    """Returns the last `count` lines of a file, oldest first.

    Seeks backwards in chunks rather than reading the file: the pwnagotchi log
    is rotated but can still be megabytes, and this runs on a Pi Zero. Decodes
    with errors="replace" so a truncated multi-byte sequence at a chunk boundary
    cannot raise.
    """
    raise NotImplementedError


def gps_unavailable() -> dict:
    """The Gps shape with nothing in it: enabled false, every coordinate None.

    There is deliberately no alternative empty form - Gps is one uniform shape,
    never a union, so the client has a single thing to render.
    """
    raise NotImplementedError


def gps_from_bettercap(raw: Mapping[str, Any] | None, now: float) -> dict | None:
    """Builds a Gps from a bettercap session `gps` block, or None if there is no fix.

    Bettercap capitalises its keys (Latitude, Longitude, Altitude, FixQuality,
    NumSatellites, HDOP - SPEC F19) and reports 0.0/0.0 when it has no lock, so
    a zero or non-finite coordinate counts as no fix. Sets source "bettercap"
    and piFix true.
    """
    raise NotImplementedError


def gps_from_gpsd(tpv: Mapping[str, Any] | None, now: float) -> dict | None:
    """Builds a Gps from a gpsd TPV report, or None when there is no 2D fix.

    Requires mode >= 2 and finite lat/lon. Sets source "gpsd" and piFix true:
    gpsd is an on-Pi source and must outrank the browser just as bettercap does.
    """
    raise NotImplementedError


def gps_from_browser(latitude: float, longitude: float, accuracy: float | None, now: float) -> dict:
    """Builds a Gps from browser Geolocation coordinates. piFix is always false."""
    raise NotImplementedError


def sidecar_payload(gps: Mapping[str, Any], now: float) -> dict:
    """Renders a Gps into the on-disk .gps.json shape.

    Deliberately bettercap-shaped, with capitalised keys, so the stock gps.py
    plugin and webgpsmap keep reading these files (decision D13). A non-bettercap
    source adds a "Source" key, which those tools ignore. The disk format is
    never the wire format.
    """
    raise NotImplementedError


def normalise_sidecar(raw: Mapping[str, Any] | None) -> dict | None:
    """Reads an on-disk .gps.json back into the HandshakeGps wire shape.

    Returns None for a malformed or fix-less sidecar, so a bad file costs its
    own entry's coordinates and not the whole listing.
    """
    raise NotImplementedError


def sidecar_path_for(capture_path: str) -> str:
    """Returns the .gps.json path beside a capture.

    Uses splitext rather than the stock plugin's `.replace(".pcapng", ...)`,
    which silently produces a wrong name for a .pcap capture.
    """
    raise NotImplementedError


class ProtocolError(Exception):
    """A command that cannot be served, carrying an ErrorCode from common.json."""

    def __init__(self, code: str, message: str) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Deps:
    """Every external effect, as a replaceable callable.

    Constructor injection with real defaults. This is not architecture for its
    own sake: the coverage gate is 100% of branches, and the I2C, gpsd,
    subprocess and service-restart paths are unreachable in a test otherwise.
    Tests pass fakes; production passes nothing and gets the real thing.

    Keep these narrow. A seam is a callable someone can replace, not an
    abstraction layer with its own vocabulary.
    """

    now: Callable[[], float] = time.time
    restart_pwnagotchi: Callable[[str], None] = None  # type: ignore[assignment]
    reboot_device: Callable[[], None] = None  # type: ignore[assignment]
    shutdown_device: Callable[[], None] = None  # type: ignore[assignment]
    run_command: Callable[[Sequence[str]], int] = None  # type: ignore[assignment]
    read_gpsd: Callable[[str, int, float], dict | None] = None  # type: ignore[assignment]
    read_pisugar_i2c: Callable[[], tuple[float | None, bool | None]] = None  # type: ignore[assignment]
    resolve_interface_ip: Callable[[str], str | None] = None  # type: ignore[assignment]
    spawn: Callable[[Callable[[], None]], None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Fills unset seams with the real implementation.

        Bound late rather than as dataclass defaults so that importing this
        module does not require a working pwnagotchi, which is what lets the
        test suite run without one installed.
        """
        raise NotImplementedError


def default_restart(mode: str) -> None:
    """Restarts pwnagotchi into the given mode via pwnagotchi.restart (SPEC F9)."""
    raise NotImplementedError


def default_run_command(argv: Sequence[str]) -> int:
    """Runs a command, returning its exit status. Never raises."""
    raise NotImplementedError


def default_read_gpsd(host: str, port: int, timeout: float) -> dict | None:
    """Polls gpsd for one TPV report with a 2D fix or better.

    Sends ?WATCH={"enable":true,"json":true} and reads until a usable TPV
    arrives or the timeout expires. Bounded and non-blocking by construction:
    this must never be reachable from the request path.
    """
    raise NotImplementedError


def default_read_pisugar_i2c() -> tuple[float | None, bool | None]:
    """Reads battery percentage and charging state directly over I2C.

    PiSugar 3 answers on bus 1 at address 0x57, percentage in register 0x2A.
    Last resort, only when no PiSugar plugin is loaded. Returns (None, None) on
    any failure - a missing battery reading is not an error worth propagating.
    """
    raise NotImplementedError


def default_resolve_interface_ip(iface: str) -> str | None:
    """Returns the current IPv4 of an interface, or None if it has none.

    Uses netifaces when importable and falls back to parsing `ip -4 addr show`,
    so the plugin does not add a hard dependency for something this small.
    """
    raise NotImplementedError


def default_spawn(target: Callable[[], None]) -> None:
    """Runs a callable on a daemon thread."""
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------


class SessionCache:
    """Holds the most recent bettercap session snapshot.

    agent.session() is a synchronous HTTP GET with a 30 second timeout (SPEC F7).
    Calling it from a request handler stalls every connected client for as long
    as bettercap takes to answer, so it is called here, on a timer, on its own
    thread, and handlers read the cache.

    `age` is exposed on the wire so the client can show stale data as degraded
    rather than presenting it as current.
    """

    def __init__(self, agent_getter: Callable[[], Any], deps: Deps, interval: float) -> None:
        raise NotImplementedError

    def refresh(self) -> bool:
        """Fetches a new snapshot. Returns whether it succeeded. Never raises."""
        raise NotImplementedError

    @property
    def snapshot(self) -> dict:
        """The last successful snapshot, or an empty dict if there has never been one."""
        raise NotImplementedError

    @property
    def age(self) -> float | None:
        """Seconds since the last successful refresh, or None if never."""
        raise NotImplementedError


class GpsResolver:
    """Resolves a position from the best available source.

    Priority is bettercap, then gpsd, then the browser, honouring the
    `gps_source` option. The on-Pi sources both set piFix, which is the flag the
    client gates on: a browser reading must never overwrite a device fix, and
    "is it on the Pi" is not the same question as "is the source bettercap".
    """

    def __init__(self, options: Mapping[str, Any], deps: Deps, session: SessionCache) -> None:
        raise NotImplementedError

    def set_browser_position(self, latitude: float, longitude: float, accuracy: float | None) -> None:
        """Stores a client-pushed position. Expires after BROWSER_GPS_TTL seconds."""
        raise NotImplementedError

    def refresh_gpsd(self) -> None:
        """Polls gpsd and caches the result. Called from the background thread only."""
        raise NotImplementedError

    def current(self) -> dict:
        """Returns the current Gps, always in the uniform shape."""
        raise NotImplementedError


class BatteryReader:
    """Reads battery state from whichever PiSugar provider is available.

    Tries the pisugarx_ext plugin, then pisugarx, then a direct I2C read. The
    plugin attribute names are not pinned in SPEC section 11 because they belong
    to third-party plugins: they are probed with getattr over a candidate list
    and every miss is treated as "unavailable". Never imports them, never raises.
    """

    PERCENT_ATTRS: tuple[str, ...] = ("percentage", "percent", "battery", "level", "capacity")
    CHARGING_ATTRS: tuple[str, ...] = ("charging", "is_charging", "power_plugged", "plugged")

    def __init__(self, options: Mapping[str, Any], deps: Deps) -> None:
        raise NotImplementedError

    def read(self) -> dict:
        """Returns {"percent": float|None, "charging": bool|None}."""
        raise NotImplementedError


class HandshakeStore:
    """Enumerates and annotates the capture directory.

    The listing comes from the directory, never from agent._handshakes, which
    only holds the current session. Two counts are reported: `pcapng` matches
    what the e-ink display shows (the stock counter globs *.pcapng only, SPEC
    F15) and `total` is the honest figure including legacy .pcap captures.
    """

    def __init__(self, directory: str, limit: int = HANDSHAKE_LIMIT) -> None:
        raise NotImplementedError

    def counts(self) -> tuple[int, int]:
        """Returns (pcapng_count, total_count)."""
        raise NotImplementedError

    def entries(self) -> tuple[list[dict], bool, int]:
        """Returns (entries, truncated, total), newest first.

        Caps at `limit` and reports truncation rather than dropping silently. An
        unreadable file is skipped, not fatal.
        """
        raise NotImplementedError

    def newest(self) -> dict | None:
        """The most recent capture as a LastHandshake, or None if the directory is empty."""
        raise NotImplementedError

    def write_sidecar(self, capture_path: str, gps: Mapping[str, Any], now: float) -> str | None:
        """Writes a .gps.json beside a capture and returns its path.

        Does nothing and returns None when there is no fix - absent coordinates
        are not 0.0, 0.0 - or when a sidecar already exists.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Protocol core
# ---------------------------------------------------------------------------


class Router:
    """Turns one incoming message into the messages that answer it.

    Deliberately free of sockets, threads and the event loop: `handle` takes a
    parsed dict and returns a list of outgoing dicts. Everything the protocol
    does can therefore be tested directly, which is what makes 100% branch
    coverage reachable, and the transport above stays thin enough to be covered
    by the integration test alone.

    Commands that end the process - a mode change, a reboot, a shutdown - return
    their acknowledgment and a `restarting` broadcast, then schedule the effect
    through Deps.spawn. The caller flushes before the effect lands.
    """

    def __init__(
        self,
        options: Mapping[str, Any],
        deps: Deps,
        agent_getter: Callable[[], Any],
        session: SessionCache,
        gps: GpsResolver,
        battery: BatteryReader,
        handshakes: Callable[[], HandshakeStore],
        plugin_version: str = __version__,
    ) -> None:
        raise NotImplementedError

    # -- authentication ----------------------------------------------------

    @property
    def auth_required(self) -> bool:
        """Whether a token is configured. When false, `auth` is accepted but unnecessary."""
        raise NotImplementedError

    def check_token(self, candidate: Any) -> bool:
        """Constant-time token comparison. A non-string candidate is a failure, not a crash."""
        raise NotImplementedError

    # -- dispatch ----------------------------------------------------------

    def handle(self, message: Mapping[str, Any], authenticated: bool) -> list[dict]:
        """Dispatches one incoming message.

        Returns the messages to send, in order: the acknowledgment first when
        the request carried a message_id, then the reply or a broadcast. Raises
        nothing - a failure becomes an `error` message, because an exception
        here would take down the connection handler and, without the guard in
        the transport, the event loop with it.
        """
        raise NotImplementedError

    def initial_burst(self) -> list[dict]:
        """The messages pushed to a client on accept: stats, access_points, face_status.

        Sent unprompted so a freshly connected client has state without a round
        of polling, which matters on a link this slow.
        """
        raise NotImplementedError

    # -- individual replies ------------------------------------------------

    def stats(self) -> dict:
        """Builds the Stats payload. Must not touch agent.session() (SPEC F7)."""
        raise NotImplementedError

    def access_points(self) -> list[dict]:
        """Maps agent._access_points. Never calls the side-effecting getter (SPEC F3)."""
        raise NotImplementedError

    def peers(self) -> list[dict]:
        """Maps agent._peers values. grid.peers() is not used: different shape (SPEC 2.8)."""
        raise NotImplementedError

    def log_lines(self, count: int | None) -> dict:
        """Tails the configured log. Path comes from config, never hardcoded (SPEC F17)."""
        raise NotImplementedError

    def screen_image(self) -> dict:
        """Reads the e-ink frame under its lock (SPEC F18).

        Raises ProtocolError("no_frame") when the frame file does not exist yet,
        which is normal early in a boot. The lock is held for the read only,
        never across a send.
        """
        raise NotImplementedError

    def face_status(self) -> dict:
        """Current face and status as text. Faces are unicode, never images (D11)."""
        raise NotImplementedError

    def capabilities(self) -> dict:
        """What this instance can do: pasv, pisugar, configured gps source, version.

        The client gates its controls on this and must not infer availability any
        other way.
        """
        raise NotImplementedError

    # -- controls ----------------------------------------------------------

    def set_mode(self, mode: str) -> list[dict]:
        """Switches between auto and manual by restarting the services (D12).

        Assigning agent.mode changes a label and nothing else: the mode is fixed
        at process start from the --manual flag (SPEC F10). The real switch is
        pwnagotchi.restart, which takes this plugin down with it - so the
        acknowledgment and the `restarting` broadcast are produced first, and
        the restart is scheduled afterwards.

        A request for the mode already in effect is acknowledged and ignored.
        """
        raise NotImplementedError

    def set_pasv(self, on: bool) -> list[dict]:
        """Toggles passive mode through the pasv_mode plugin (SPEC F12).

        Raises ProtocolError("pasv_unavailable") when the plugin is not loaded
        and ("pasv_requires_auto") outside auto. Emits plugins.on and returns
        only an acknowledgment: dispatch is asynchronous (SPEC F8), so the new
        state cannot be read back here and reaches the client with the next
        stats.
        """
        raise NotImplementedError

    def reboot(self) -> list[dict]:
        """Broadcasts `restarting` then schedules pwnagotchi.reboot (SPEC F9)."""
        raise NotImplementedError

    def shutdown(self) -> list[dict]:
        """Broadcasts `restarting` then schedules pwnagotchi.shutdown (SPEC F9)."""
        raise NotImplementedError

    # -- pushes ------------------------------------------------------------

    def on_handshake_message(self, filename: str, access_point: Any, station: Any) -> dict:
        """Builds the `handshake` push, normalising both hook signatures (SPEC F20)."""
        raise NotImplementedError

    def push(self, kind: str, data: Any) -> dict:
        """Wraps a push payload in an envelope with the current timestamp."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def build_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext | None:
    """Loads the certificate chain, or returns None if it cannot.

    Returning None is the whole point: the caller starts no listener at all.
    There is no plaintext fallback, because a silent one is a worse outcome than
    being down - iOS requires TLS, so a plaintext server would not serve the app
    and would quietly expose the socket instead (D4).
    """
    raise NotImplementedError


def make_http_handler(web_root: str) -> type:
    """Builds the request handler class serving `web_root` over HTTPS.

    Two behaviours that are easy to omit and break the PWA silently (D14):

    MIME types are declared explicitly rather than inherited from the system
    database. A minimal Raspberry Pi OS image does not know .webmanifest, and a
    manifest served as application/octet-stream makes iOS refuse to install the
    app with no visible error. A service worker needs text/javascript for the
    same reason.

    A GET that resolves to no file and does not look like an asset serves
    index.html, so deep links and refreshes work in a single-page app. Paths
    containing .. are rejected with 400 before any resolution, directory
    listings are disabled, and index.html and the service worker are sent
    no-cache while hashed assets are not.
    """
    raise NotImplementedError


class Listeners:
    """Owns the bound sockets and reconciles them as interfaces come and go.

    bnep0 usually has no address until the tether is up, so binding is a
    continuous reconciliation rather than a one-off at load. The state is keyed
    on the resolved IP:

        has an IP, nothing bound      bind both listeners
        lost its IP                   close both, drop the clients
        IP changed                    close the old, bind the new
        absent entirely               skip quietly after the first log
        bind raises OSError           log, leave unbound, retry next pass

    Never binds 0.0.0.0, :: or a hostname. A failure here is logged and retried,
    never fatal: an unbindable interface must not take down the ones that work.
    """

    def __init__(self, options: Mapping[str, Any], deps: Deps, ssl_context: ssl.SSLContext) -> None:
        raise NotImplementedError

    def reconcile(self) -> dict[str, str | None]:
        """Runs one reconciliation pass. Returns the interface-to-IP map after it."""
        raise NotImplementedError

    def stop(self) -> None:
        """Closes every listener. Idempotent."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class Companion(plugins.Plugin):
    """The pwnagotchi plugin shell.

    Deliberately thin. Its job is to receive hooks, hand them to the Router, and
    own the background threads; the behaviour worth testing lives in the
    components above, where it can be reached without a running pwnagotchi.
    """

    __author__ = __author__
    __version__ = __version__
    __license__ = __license__
    __description__ = __description__
    __help__ = __help__

    def __init__(self) -> None:
        raise NotImplementedError

    # -- lifecycle ---------------------------------------------------------

    def on_loaded(self) -> None:
        """Reads configuration and starts the transport, or refuses to start.

        Without a usable certificate this logs an explicit error and starts
        nothing at all.
        """
        raise NotImplementedError

    def on_ready(self, agent: Any) -> None:
        """Captures the agent and starts the background refresh and rebind threads."""
        raise NotImplementedError

    def on_unload(self, ui: Any = None) -> None:
        """Stops the threads and closes every listener."""
        raise NotImplementedError

    # -- pushes ------------------------------------------------------------

    def on_handshake(self, agent: Any, filename: str, access_point: Any, client_station: Any) -> None:
        """Writes the GPS sidecar and pushes the `handshake` message.

        Both argument shapes are accepted: this hook is fired from two paths with
        different types (SPEC F20).
        """
        raise NotImplementedError

    def on_peer_detected(self, agent: Any, peer: Any) -> None:
        """Pushes `peer_detected`."""
        raise NotImplementedError

    def on_wifi_update(self, agent: Any, access_points: Iterable[Mapping[str, Any]]) -> None:
        """Pushes `wifi_update`."""
        raise NotImplementedError

    def on_channel_hop(self, agent: Any, channel: int) -> None:
        """Pushes `channel_hop`."""
        raise NotImplementedError

    def on_ui_update(self, ui: Any) -> None:
        """Pushes `face_status` only when the face or status text actually changed."""
        raise NotImplementedError

    def on_bored(self, agent: Any) -> None:
        """Pushes `status_change` with mood "bored"."""
        raise NotImplementedError

    def on_excited(self, agent: Any) -> None:
        """Pushes `status_change` with mood "excited"."""
        raise NotImplementedError

    def on_lonely(self, agent: Any) -> None:
        """Pushes `status_change` with mood "lonely"."""
        raise NotImplementedError

    def on_sad(self, agent: Any) -> None:
        """Pushes `status_change` with mood "sad"."""
        raise NotImplementedError
