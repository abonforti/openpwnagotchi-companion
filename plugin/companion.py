"""openpwnagotchi-companion - pwnagotchi side of a self-hosted PWA companion.

Serves the built PWA over HTTPS and speaks the WebSocket contract in
docs/schemas/ over WSS, bound only to the tether addresses. See SPEC.md for the
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
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

import pwnagotchi
import pwnagotchi.plugins as plugins

__author__ = "https://github.com/abonforti"
__version__ = "0.0.1"
__license__ = "GPL3"
__description__ = (
    "Self-hosted companion backend: serves an installable PWA over HTTPS and "
    "speaks a WebSocket protocol to it, bound only to tether addresses, TLS only."
)
__name_of_plugin__ = "companion"

__help__ = """
Serves the openpwnagotchi-companion PWA from the unit itself and speaks its
WebSocket protocol. Both listeners are TLS only and bind exclusively to the
local IPv4 addresses that match `bind_addresses`; the plugin will not start a
listener without a usable certificate, and never falls back to plaintext.

You need a certificate whose subjectAltName lists each Pi IP as an iPAddress
(not DNS), issued by a CA you install and fully trust on the phone. See
docs/CERTIFICATES.md - iOS is strict here and fails silently when it is not met.

    [main.plugins.companion]
    enabled        = true
    bind_addresses = ["172.20.10.0/28", "10.0.0.2"]  # IPv4 addresses or CIDR blocks
    ws_port        = 8082            # WSS
    http_port      = 8443            # HTTPS, serves the PWA
    tls_cert       = "/etc/pwnagotchi/companion/server.crt"
    tls_key        = "/etc/pwnagotchi/companion/server.key"
    web_root       = "/var/www/openpwn-companion"
    token          = ""              # optional shared secret, empty disables auth

A local address is bound when it equals one of those entries or falls inside a
block; a block shorter than /24 is refused, and so is anything that is not an
IPv4 address or block. Nothing is bound if no entry survives that check. The
older `interfaces` key is ignored: an interface name identifies a device, not a
network, and `bnep0` is only whichever Bluetooth peer connected first.

Full option reference and defaults: SPEC.md section 2.2.
"""

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "bind_addresses": ["172.20.10.0/28", "10.0.0.2"],
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
    "keepalive_interval": 20,
}

HANDSHAKE_LIMIT = 500
# How often an unauthenticated connection wakes up to re-check its deadline
# (SPEC 2.3.4). It has to be a poll rather than a check on the next frame,
# because the peer the deadline exists to stop is the one that never sends one.
AUTH_POLL_INTERVAL = 0.25
# Shortest prefix a `bind_addresses` block may carry. Without a floor the block
# syntax becomes a way back to binding everything: 0.0.0.0/0 matches every
# address the host holds, which is D5 defeated in one line of config.
BIND_MIN_PREFIX = 24
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
    if options is None:
        return DEFAULTS.get(key)
    value = options.get(key, None)
    if value is None:
        return DEFAULTS.get(key)
    return value


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    """Coerces to float, rejecting None, NaN and the infinities.

    Every coordinate on the wire passes through here. A NaN latitude serialises
    to invalid JSON and a fabricated one is worse than an absent one.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def is_bindable_address(value: Any) -> bool:
    """Whether a value is a literal IPv4 address this plugin is willing to bind.

    Decision D5 is "bind only the tether addresses", and two things defeat it:
    a wildcard, which binds every interface at once, and a hostname, which
    resolves at bind time to whatever DNS happens to answer - possibly a
    wildcard, possibly an address on a network the unit should not be reachable
    from. Only a literal address is accepted, and never an unspecified one.
    """
    if not isinstance(value, str) or not value:
        return False
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return False
    return not address.is_unspecified


def parse_bind_entry(value: Any) -> ipaddress.IPv4Address | ipaddress.IPv4Network:
    """Parses one `bind_addresses` entry, raising ValueError with the reason.

    An entry is either a literal IPv4 address or an IPv4 block. Blocks exist
    because the literal address is less certain than it looks: over a Personal
    Hotspot the subnet is always 172.20.10.0/28 but the host part comes from
    the phone's DHCP server, so a literal-only configuration leaves some units
    with no listener and the unhelpful symptom "nothing started".
    """
    if not isinstance(value, str) or not value:
        raise ValueError("not a string")
    if "/" in value:
        try:
            block = ipaddress.IPv4Network(value, strict=False)
        except ValueError as err:
            raise ValueError(str(err)) from err
        if block.network_address.is_unspecified:
            # The network address is the lowest address of a block, so a block
            # holds 0.0.0.0 exactly when its network address is the wildcard -
            # true at every prefix length, independently of the floor checked
            # below. D5 forbids the wildcard in any bind call unconditionally,
            # and that outranks "it parses".
            raise ValueError("block contains the wildcard address")
        if block.prefixlen < BIND_MIN_PREFIX:
            raise ValueError(f"prefix shorter than /{BIND_MIN_PREFIX}")
        return block
    if not is_bindable_address(value):
        raise ValueError("not a bindable IPv4 address")
    return ipaddress.IPv4Address(value)


def parse_bind_addresses(entries: Any) -> list[ipaddress.IPv4Address | ipaddress.IPv4Network]:
    """Validates `bind_addresses` once, logging and dropping each bad entry.

    A rejected entry never contributes a listener and never takes the good
    entries down with it. When nothing survives, the plugin binds nothing at
    all: falling back to a default here would bind more than the owner asked
    for, which is the exposure D5.1 exists to close.
    """
    if not isinstance(entries, list):
        log.error("[companion] bind_addresses is not a list: %r", entries)
        return []
    selectors: list[ipaddress.IPv4Address | ipaddress.IPv4Network] = []
    for entry in entries:
        try:
            selectors.append(parse_bind_entry(entry))
        except ValueError as err:
            log.error("[companion] ignoring bind_addresses entry %r: %s", entry, err)
    if not selectors:
        log.error("[companion] no usable entry in bind_addresses: nothing will be bound")
    return selectors


def bind_addresses_option(options: Mapping[str, Any]) -> Any:
    """Reads `bind_addresses`, keeping a nulled key distinct from a missing one.

    `option()` cannot tell them apart, and here they must: the shipped default
    binds a whole subnet, so a key the owner wrote and left unusable has to
    resolve to nothing rather than to more. Emptying the key is a plausible way
    to try to stop the plugin listening, and it must not do the opposite.
    """
    if options is None or "bind_addresses" not in options:
        return DEFAULTS.get("bind_addresses")
    return options.get("bind_addresses")


def select_bind_addresses(
    local: Iterable[Any],
    selectors: Sequence[ipaddress.IPv4Address | ipaddress.IPv4Network],
) -> list[str]:
    """Picks the local addresses to bind: equal to a literal, or inside a block.

    The interface carrying an address is not consulted and never reaches this
    function (D5.1). Anything the host reports that is not a bindable literal
    IPv4 is dropped here, so 0.0.0.0 stays out of a bind call even if a block
    were to contain it. The enumeration refuses the wildcard as well, and the
    duplication is deliberate (SPEC 2.3.1): the enumeration is an injectable
    seam, so a rule kept only there is one a test double walks past, and a rule
    kept only here is one a replacement seam walks past. This is the
    load-bearing side of the two, because every address reaches it; both call
    `is_bindable_address`, so neither can drift from the other.
    """
    selected: list[str] = []
    for raw in local:
        if not is_bindable_address(raw):
            continue
        address = ipaddress.IPv4Address(raw)
        text = str(address)
        if text in selected:
            continue
        for selector in selectors:
            matched = (
                address in selector
                if isinstance(selector, ipaddress.IPv4Network)
                else address == selector
            )
            if matched:
                selected.append(text)
                break
    return selected


def envelope(kind: str, data: Any, timestamp: float, message_id: str | None = None) -> dict:
    """Builds an outgoing message: payload under `data`, `timestamp` always present.

    `message_id` is included only when the request carried one. See
    docs/PROTOCOL.md for why incoming is flat while outgoing is enveloped.
    """
    message: dict[str, Any] = {"type": kind, "data": data, "timestamp": timestamp}
    if message_id is not None:
        message["message_id"] = message_id
    return message


def error_envelope(
    code: str, message: str, timestamp: float, message_id: str | None = None
) -> dict:
    """Builds an `error` message. `code` must be one of common.json ErrorCode."""
    return envelope("error", {"code": code, "message": message}, timestamp, message_id)


def mac_of(value: Any) -> str | None:
    """Extracts a MAC from either a bettercap dict or a bare string.

    on_handshake is fired from two code paths with different argument types
    (SPEC F20): MAC strings on one, bettercap dicts on the other. Returns None
    for anything else rather than guessing.
    """
    if isinstance(value, Mapping):
        found = value.get("mac") or value.get("bssid")
        return found if isinstance(found, str) and found else None
    if isinstance(value, str) and value:
        return value
    return None


def hdop_to_metres(hdop: Any) -> float | None:
    """Converts a horizontal dilution of precision into a rough accuracy in metres.

    Uses a nominal 5 m user-equivalent range error, so accuracy is 5 * HDOP.
    Returns None when HDOP is absent, unparseable or non-positive, because a
    fabricated accuracy is worse than an honest absence.
    """
    try:
        value = float(hdop)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # Guard the product, not just the input: a large but finite HDOP can still
    # overflow to inf here, and inf is not valid JSON. Deliberately unrounded -
    # rounding to one decimal turns a very small HDOP into 0.0 metres, which
    # claims perfect precision instead of reporting high precision.
    return _finite(value * 5.0)


def parse_handshake_filename(filename: str) -> tuple[str, str | None]:
    """Splits a capture filename into (ssid, bssid).

    Anchors on a trailing 12-hex-digit group, so an SSID containing underscores
    survives. Returns (whole basename, None) when the name does not match:
    the BSSID is reported as unknown rather than guessed.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    match = HANDSHAKE_NAME.match(stem)
    if match is None or not match.group("ssid"):
        return stem, None
    return match.group("ssid"), match.group("bssid").lower()


def map_access_point(ap: Mapping[str, Any]) -> dict:
    """Maps one bettercap AP dict to the wire shape.

    The bettercap key is `mac`; there is no `bssid` (SPEC F4) - reading `bssid`
    is the upstream bug this fork exists partly to fix. `clients` may be absent
    or None, so its length is taken defensively.
    """
    mac = ap.get("mac") or ""
    hostname = ap.get("hostname") or mac
    return {
        "bssid": mac,
        "hostname": hostname,
        "channel": int(ap.get("channel") or 0),
        "rssi": int(ap.get("rssi") or 0),
        "encryption": ap.get("encryption") or "",
        "vendor": ap.get("vendor") or "",
        "clients": len(ap.get("clients") or []),
    }


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
    def call(name: str, default: Any = None) -> Any:
        try:
            member = getattr(peer, name)
        except Exception:
            return default
        try:
            return member() if callable(member) else member
        except Exception:
            return default

    def as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {
        "name": call("name", "???") or "???",
        "fingerprint": call("identity", "???") or "???",
        "fullName": call("full_name", "") or "",
        "rssi": as_int(call("rssi")),
        # last_channel, not channel: Peer has no attribute of the latter name.
        "channel": as_int(call("last_channel")),
        "firstSeen": to_epoch(call("first_seen")),
        "prevSeen": to_epoch(call("prev_seen")),
        "firstMet": to_epoch(call("first_met")),
        # Already an epoch float, unlike its three siblings above.
        "lastSeen": to_epoch(call("last_seen")),
        "encounters": as_int(call("encounters")),
        "pwndRun": as_int(call("pwnd_run")),
        # pwnd_total(), not the pwnd_tot key it reads from internally.
        "pwndTotal": as_int(call("pwnd_total")),
        "version": call("version"),
        "uptime": as_int(call("uptime")),
        "face": call("face"),
    }


def to_epoch(value: Any) -> float | None:
    """Coerces a datetime, epoch number or timestamp string into an epoch float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        try:
            return float(timestamp())
        except Exception:
            return None
    if isinstance(value, str):
        # Peer.__init__ falls back to a formatted string when it cannot parse a
        # timestamp, so this shape genuinely reaches us - and losing it here
        # would silently null three of the four peer timestamps.
        import datetime

        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed.timestamp()
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return time.mktime(time.strptime(value.split(".")[0], fmt))
            except (ValueError, OverflowError):
                continue
    return None


def tail_lines(path: str, count: int) -> list[str]:
    """Returns the last `count` lines of a file, oldest first.

    Seeks backwards in chunks rather than reading the file: the pwnagotchi log
    is rotated but can still be megabytes, and this runs on a Pi Zero. Decodes
    with errors="replace" so a truncated multi-byte sequence at a chunk boundary
    cannot raise.
    """
    if count <= 0:
        return []
    chunk_size = 8192
    data = b""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        while position > 0 and data.count(b"\n") <= count:
            step = min(chunk_size, position)
            position -= step
            handle.seek(position)
            data = handle.read(step) + data
    text = data.decode("utf-8", errors="replace")
    # split("\n"), not splitlines(): the latter also breaks on \x0b, \x1e and
    # U+2028, inventing lines a log never contained and making `count` count
    # something other than lines.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines[-count:]


def gps_unavailable() -> dict:
    """The Gps shape with nothing in it: enabled false, every coordinate None.

    There is deliberately no alternative empty form - Gps is one uniform shape,
    never a union, so the client has a single thing to render.
    """
    return {
        "enabled": False,
        "source": None,
        "piFix": False,
        "fix": False,
        "lat": None,
        "lon": None,
        "altitude": None,
        "accuracy": None,
        "updated": None,
    }


def gps_from_bettercap(raw: Mapping[str, Any] | None, now: float) -> dict | None:
    """Builds a Gps from a bettercap session `gps` block, or None if there is no fix.

    Bettercap capitalises its keys (Latitude, Longitude, Altitude, FixQuality,
    NumSatellites, HDOP - SPEC F19) and reports 0.0/0.0 when it has no lock, so
    a zero or non-finite coordinate counts as no fix. Sets source "bettercap"
    and piFix true.
    """
    if not raw:
        return None
    latitude = _finite(raw.get("Latitude"))
    longitude = _finite(raw.get("Longitude"))
    if latitude is None or longitude is None:
        return None
    # The stock gps.py plugin writes a sidecar only when both coordinates are
    # truthy, so a single zero is "no lock" too, not a position on the equator.
    if latitude == 0.0 or longitude == 0.0:
        return None
    return {
        "enabled": True,
        "source": "bettercap",
        "piFix": True,
        "fix": True,
        "lat": latitude,
        "lon": longitude,
        "altitude": _finite(raw.get("Altitude")),
        "accuracy": hdop_to_metres(raw.get("HDOP")),
        "updated": now,
    }


def gps_from_gpsd(tpv: Mapping[str, Any] | None, now: float) -> dict | None:
    """Builds a Gps from a gpsd TPV report, or None when there is no 2D fix.

    Requires mode >= 2 and finite lat/lon. Sets source "gpsd" and piFix true:
    gpsd is an on-Pi source and must outrank the browser just as bettercap does.
    """
    if not tpv:
        return None
    try:
        mode = int(tpv.get("mode") or 0)
    except (TypeError, ValueError):
        return None
    if mode < 2:
        return None
    latitude = _finite(tpv.get("lat"))
    longitude = _finite(tpv.get("lon"))
    if latitude is None or longitude is None:
        return None
    # Same rule as bettercap: a receiver reporting exactly 0.0 on either axis
    # has not locked, whatever it claims about its mode.
    if latitude == 0.0 or longitude == 0.0:
        return None
    return {
        "enabled": True,
        # gpsd is an on-Pi source too, which is why the client gates on piFix
        # rather than comparing the source against "bettercap".
        "source": "gpsd",
        "piFix": True,
        "fix": True,
        "lat": latitude,
        "lon": longitude,
        "altitude": _finite(tpv.get("alt")),
        "accuracy": _finite(tpv.get("eph")),
        "updated": now,
    }


def gps_from_browser(latitude: float, longitude: float, accuracy: float | None, now: float) -> dict:
    """Builds a Gps from browser Geolocation coordinates. piFix is always false."""
    return {
        "enabled": True,
        "source": "browser",
        "piFix": False,
        "fix": True,
        "lat": float(latitude),
        "lon": float(longitude),
        "altitude": None,
        "accuracy": _finite(accuracy),
        "updated": now,
    }


def sidecar_payload(gps: Mapping[str, Any], now: float) -> dict:
    """Renders a Gps into the on-disk .gps.json shape.

    Deliberately bettercap-shaped, with capitalised keys, so the stock gps.py
    plugin and webgpsmap keep reading these files (decision D13). A non-bettercap
    source adds a "Source" key, which those tools ignore. The disk format is
    never the wire format.
    """
    payload: dict[str, Any] = {
        "Latitude": gps.get("lat"),
        "Longitude": gps.get("lon"),
        "Altitude": gps.get("altitude") if gps.get("altitude") is not None else 0.0,
        "Updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(gps.get("updated") or now)),
    }
    source = gps.get("source")
    if source and source != "bettercap":
        # An extra key the stock tools ignore, so provenance survives without
        # breaking the shape they expect.
        payload["Source"] = source
    accuracy = gps.get("accuracy")
    if accuracy is not None:
        payload["Accuracy"] = accuracy
    return payload


def normalise_sidecar(raw: Mapping[str, Any] | None) -> dict | None:
    """Reads an on-disk .gps.json back into the HandshakeGps wire shape.

    Returns None for a malformed or fix-less sidecar, so a bad file costs its
    own entry's coordinates and not the whole listing.
    """
    if not raw:
        return None
    latitude = _finite(raw.get("Latitude", raw.get("lat")))
    longitude = _finite(raw.get("Longitude", raw.get("lon")))
    if latitude is None or longitude is None:
        return None
    if latitude == 0.0 and longitude == 0.0:
        return None
    source = raw.get("Source") or "bettercap"
    if source not in ("bettercap", "gpsd", "browser"):
        source = None
    accuracy = _finite(raw.get("Accuracy", raw.get("accuracy")))
    if accuracy is None:
        # Same derivation gps_from_bettercap uses, so a sidecar written by the
        # stock plugin - which stores HDOP and no accuracy - keeps its precision.
        accuracy = hdop_to_metres(raw.get("HDOP"))
    return {
        "lat": latitude,
        "lon": longitude,
        "accuracy": accuracy,
        "source": source,
    }


def sidecar_path_for(capture_path: str) -> str:
    """Returns the .gps.json path beside a capture.

    Uses splitext rather than the stock plugin's `.replace(".pcapng", ...)`,
    which silently produces a wrong name for a .pcap capture.
    """
    return os.path.splitext(capture_path)[0] + ".gps.json"


class ProtocolError(Exception):
    """A command that cannot be served, carrying an ErrorCode from common.json."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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
    list_local_ipv4: Callable[[], list[str]] = None  # type: ignore[assignment]
    spawn: Callable[[Callable[[], None]], None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """Fills unset seams with the real implementation.

        Bound late rather than as dataclass defaults so that importing this
        module does not require a working pwnagotchi, which is what lets the
        test suite run without one installed.
        """
        if self.restart_pwnagotchi is None:
            self.restart_pwnagotchi = default_restart
        if self.reboot_device is None:
            self.reboot_device = lambda: pwnagotchi.reboot()
        if self.shutdown_device is None:
            self.shutdown_device = lambda: pwnagotchi.shutdown()
        if self.run_command is None:
            self.run_command = default_run_command
        if self.read_gpsd is None:
            self.read_gpsd = default_read_gpsd
        if self.read_pisugar_i2c is None:
            self.read_pisugar_i2c = default_read_pisugar_i2c
        if self.list_local_ipv4 is None:
            self.list_local_ipv4 = default_list_local_ipv4
        if self.spawn is None:
            self.spawn = default_spawn


def default_restart(mode: str) -> None:
    """Restarts pwnagotchi into the given mode via pwnagotchi.restart (SPEC F9)."""
    pwnagotchi.restart(mode)


def default_run_command(argv: Sequence[str]) -> int:
    """Runs a command, returning its exit status. Never raises."""
    try:
        return subprocess.run(list(argv), check=False).returncode
    except Exception as err:
        log.error("[companion] command %s failed: %s", argv, err)
        return -1


def default_read_gpsd(host: str, port: int, timeout: float) -> dict | None:
    """Polls gpsd for one TPV report with a 2D fix or better.

    Sends ?WATCH={"enable":true,"json":true} and reads until a usable TPV
    arrives or the timeout expires. Bounded and non-blocking by construction:
    this must never be reachable from the request path.
    """
    deadline = time.time() + timeout
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.settimeout(timeout)
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        buffer = b""
        while time.time() < deadline:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                try:
                    report = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if report.get("class") == "TPV":
                    return report
    except (OSError, socket.timeout):
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return None


def default_read_pisugar_i2c() -> tuple[float | None, bool | None]:
    """Reads the battery percentage and the charging flag directly over I2C.

    PiSugar 3 answers on bus 1 at address 0x57. Register 0x2A carries the
    percentage and bit 7 of register 0x02 carries charging; both are read as a
    byte, never as a word. Last resort, only when no PiSugar plugin is loaded.
    Never raises - a missing battery reading is not an error worth propagating.

    Half an answer arrives in three ways and SPEC 2.11.2 answers them along the
    line between transport and content:

    - A read that raises discards both readings, whichever register it was on:
      the result is (None, None). The fault is in the channel the two reads
      share, so neither answer is worth reporting. (percent, None) would also
      collide with tier 1's meaningful answer - a PiSugar plugin that exposes a
      level and no charge state - and make two unrelated situations look alike
      one layer up in battery_info.
    - A percentage outside 0..100 discards only the percentage: the result is
      (None, charging). A byte that cannot be a percentage is a successful read
      of a bad value rather than a failed read, and the charging bit came off a
      different register and is one bit wide, so a nonsensical gauge reading does
      not make it suspect.
    - A bus that will not close discards both readings even when both were in
      range, because the close sits inside the guarded block. That placement is
      deliberate: a handle that fails to close is evidence about the channel, so
      it goes with transport rather than earning a fourth rule of its own.

    What charging means here is decided by the upstream plugin, not by the dumps.
    The dumps were taken at 89%, where a connected cable is also charging, so they
    fit "external power present" and "current flowing into the battery" equally
    well. pisugarx_ext reads this same register, names bit 7 power_plugged and
    puts a separate allow_charging at bit 6, which is why this field is taken to
    mean external power present. It follows - by inference, never yet observed -
    that a full battery on a connected cable reports charging while nothing is
    being charged. SPEC 2.11.1 has the evidence and why that is accepted.

    All four constants - bus, address, 0x2A and bit 7 of 0x02 - were established
    against the reference unit on PiSugar firmware v1.3.8; SPEC 2.11.1 records the
    dumps and how each one was confirmed.
    """
    try:
        import smbus2  # type: ignore[import-not-found]

        bus = smbus2.SMBus(1)
        try:
            percent = float(bus.read_byte_data(0x57, 0x2A))
            charging = bool(bus.read_byte_data(0x57, 0x02) & 0x80)
        finally:
            bus.close()
    except Exception:
        # Transport: the channel both reads share failed, so neither survives.
        return None, None
    if not 0 <= percent <= 100:
        # Content: this register returned nonsense and the other one did not.
        return None, charging
    return percent, charging


def default_list_local_ipv4() -> list[str]:
    """Returns every IPv4 address the host currently holds.

    Uses netifaces when importable and falls back to parsing `ip -4 addr show`,
    so the plugin does not add a hard dependency for something this small. An
    interface name is walked over on the way to the addresses and dropped here:
    it is a device identifier, not a network, and it must not reach a bind
    decision (D5.1). Everything that survives is a literal IPv4 address the
    plugin is willing to bind, so a value that is not one - a CIDR suffix, an
    IPv6 literal, a name, or the wildcard an unconfigured point-to-point
    interface reports - stops here rather than one layer up (SPEC 2.3.1).

    A failure of the enumeration itself is raised, never folded into an empty
    list: the two are indistinguishable to the caller and only one of them says
    the host holds no addresses. The rebinding loop catches it and leaves the
    bound set alone, so a hiccup of `ip -4 addr show` cannot tear down a healthy
    tether (SPEC 2.3.1, last row). One interface failing is not that case and is
    not raised: the pass learned everything except that interface, and on a
    pwnagotchi it is routine rather than exotic, because `wlan0mon` is created
    and torn down as a matter of course and `netifaces` raises for a name that
    disappeared between `interfaces()` and `ifaddresses()`. It is one case
    however it fails, so reading the interface and reading its entries are
    guarded together: a shape the library does not document is that interface
    not working out, not the enumeration failing. Losing every interface is
    that case again, and is raised.
    """
    try:
        import netifaces  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        found: list[str] = []
        names = list(netifaces.interfaces())
        failure: Exception | None = None
        observed = 0
        for iface in names:
            try:
                entries = netifaces.ifaddresses(iface).get(netifaces.AF_INET) or []
                here = [entry.get("addr") for entry in entries]
            except Exception as err:
                # Debug, not warning: an interface going away mid-pass is what
                # a working unit does routinely, and a warning per pass for
                # normal behaviour is how a log stops being read.
                log.debug("[companion] skipping interface %s: %s", iface, err)
                failure = err
                continue
            # Only now, because an interface counts as observed once it has
            # been read whole. Appending after the guard keeps a half-read
            # interface out of the result instead of leaving part of it in.
            observed += 1
            found.extend(address for address in here if is_bindable_address(address))
        if failure is not None and observed == 0:
            # Every interface there was failed, so the pass observed nothing at
            # all, which is the case the docstring above raises for. A host with
            # no interfaces is not this: it observed, and the answer was empty.
            raise failure
        return found

    # netifaces is not a hard dependency for something this small.
    output = subprocess.run(
        ["ip", "-4", "addr", "show"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout
    addresses: list[str] = []
    # The first token after `inet` is the local address; what follows it is a
    # peer, a broadcast or a label, and none of those is an address this host
    # holds. `inet6` does not match, and the prefix length is not part of the
    # address. The token is taken whole rather than matched as four dot-
    # separated digit runs, because that shape accepts `999.1.1.1`; deciding
    # what is an address is `is_bindable_address`'s job, not the pattern's.
    for token in re.findall(r"^\s*inet\s+(\S+)", output, re.MULTILINE):
        candidate = token.split("/", 1)[0]
        if is_bindable_address(candidate):
            addresses.append(candidate)
    return addresses


def default_spawn(target: Callable[[], None]) -> None:
    """Runs a callable on a daemon thread."""
    threading.Thread(target=target, daemon=True).start()


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
        self._agent_getter = agent_getter
        self._deps = deps
        self._interval = float(interval)
        self._lock = threading.Lock()
        self._snapshot: dict = {}
        self._fetched_at: float | None = None

    def refresh(self) -> bool:
        """Fetches a new snapshot. Returns whether it succeeded. Never raises."""
        agent = self._agent_getter()
        if agent is None:
            return False
        try:
            snapshot = agent.session()
        except Exception as err:
            log.debug("[companion] bettercap session refresh failed: %s", err)
            return False
        if not isinstance(snapshot, Mapping):
            return False
        with self._lock:
            self._snapshot = dict(snapshot)
            self._fetched_at = self._deps.now()
        return True

    @property
    def snapshot(self) -> dict:
        """The last successful snapshot, or an empty dict if there has never been one."""
        with self._lock:
            return dict(self._snapshot)

    @property
    def age(self) -> float | None:
        """Seconds since the last successful refresh, or None if never."""
        with self._lock:
            if self._fetched_at is None:
                return None
            return max(0.0, self._deps.now() - self._fetched_at)


class GpsResolver:
    """Resolves a position from the best available source.

    Priority is bettercap, then gpsd, then the browser, honouring the
    `gps_source` option. The on-Pi sources both set piFix, which is the flag the
    client gates on: a browser reading must never overwrite a device fix, and
    "is it on the Pi" is not the same question as "is the source bettercap".
    """

    def __init__(self, options: Mapping[str, Any], deps: Deps, session: SessionCache) -> None:
        self._options = options
        self._deps = deps
        self._session = session
        self._browser: dict | None = None
        self._browser_at: float = 0.0
        self._gpsd: dict | None = None

    def set_browser_position(
        self, latitude: float, longitude: float, accuracy: float | None
    ) -> None:
        """Stores a client-pushed position. Expires after BROWSER_GPS_TTL seconds."""
        now = self._deps.now()
        self._browser = gps_from_browser(latitude, longitude, accuracy, now)
        self._browser_at = now

    def refresh_gpsd(self) -> None:
        """Polls gpsd and caches the result. Called from the background thread only."""
        if option(self._options, "gps_source") not in ("auto", "gpsd"):
            self._gpsd = None
            return
        tpv = None
        try:
            tpv = self._deps.read_gpsd(
                option(self._options, "gpsd_host"),
                int(option(self._options, "gpsd_port")),
                2.0,
            )
        except Exception as err:
            log.debug("[companion] gpsd poll failed: %s", err)
        self._gpsd = gps_from_gpsd(tpv, self._deps.now())

    def current(self) -> dict:
        """Returns the current Gps, always in the uniform shape."""
        source = option(self._options, "gps_source")
        if source == "none":
            return gps_unavailable()
        now = self._deps.now()

        if source in ("auto", "bettercap"):
            fix = gps_from_bettercap(self._session.snapshot.get("gps"), now)
            if fix is not None:
                return fix

        if source in ("auto", "gpsd") and self._gpsd is not None:
            return dict(self._gpsd)

        # Lowest priority, and only while fresh: a stale browser position is
        # worse than none, because the phone may have moved without the Pi.
        if self._browser is not None:
            if now - self._browser_at <= BROWSER_GPS_TTL:
                return dict(self._browser)
            self._browser = None

        return gps_unavailable()


class BatteryReader:
    """Reads battery state from whichever PiSugar provider is available.

    Tries the pisugarx_ext plugin, then pisugarx, then a direct I2C read. The
    plugin attribute names are not pinned in SPEC section 11 because they belong
    to third-party plugins: they are probed with getattr over a candidate list
    and every miss is treated as "unavailable". Never imports them, never raises.
    """

    # In both lists every plain attribute comes before every accessor, and only then is
    # the order by how precisely a name answers the question, with ties going to what the
    # reference unit exposes: `battery_level` and `percentage` name the quantity equally
    # well, and the first is what that unit's PiSugar client carries (F29). The accessors
    # sit last despite naming the question more precisely than `capacity` or `plugged` do,
    # because reading an attribute cannot block or touch hardware while calling
    # third-party code may do either, on the request path. That these two are one-line
    # wrappers today is a fact about one plugin at one version, not about the shape being
    # probed, and the probe cannot tell a wrapper from a bus transaction before calling.
    PERCENT_ATTRS: tuple[str, ...] = (
        "battery_level", "percentage", "percent", "battery", "level", "capacity",
        "get_battery_level",
    )
    # A plugin exposing both `charging` and `power_plugged` means the two differently, and
    # the first is the field this reports. `battery_charging` is deliberately absent: on
    # the reference unit's client it is assigned 0 in the constructor and never again, and
    # its accessor has a body of `pass` (F29). `bool(0)` is a value rather than a miss, so
    # probing it would answer "not charging" forever and `power_plugged` would never be
    # reached. A dead field that answers is worse than no field.
    CHARGING_ATTRS: tuple[str, ...] = (
        "charging", "is_charging", "power_plugged", "plugged", "get_battery_power_plugged",
    )

    def __init__(self, options: Mapping[str, Any], deps: Deps) -> None:
        self._options = options
        self._deps = deps
        self._seen_provider = False

    def read(self) -> dict:
        """Returns {"percent": float|None, "charging": bool|None}.

        Resolution is per field: a tier that supplies one field does not settle
        the other, and descending continues for whatever is still missing. A
        tier is never consulted for a field already in hand, so the I2C bus is
        touched only when there is something left to gain by touching it.
        """
        if not option(self._options, "pisugar"):
            return {"percent": None, "charging": None}

        percent: float | None = None
        charging: bool | None = None

        for name in ("pisugarx_ext", "pisugarx"):
            if percent is not None and charging is not None:
                break
            plugin = self._loaded(name)
            if plugin is None:
                continue
            # Resolved once per plugin per read: `ps` is third-party and may be a
            # property, so the traversal is not repeated for the second field.
            client = self._client(plugin)
            if percent is None:
                percent = self._probe(plugin, client, self.PERCENT_ATTRS, self._as_percent)
            if charging is None:
                charging = self._probe(plugin, client, self.CHARGING_ATTRS, self._as_charging)

        if percent is None or charging is None:
            # One bus transaction returns both fields, so reaching it for one
            # missing field also yields the other. A value already in hand wins:
            # the one that came along for the ride is discarded here explicitly.
            try:
                i2c_percent, i2c_charging = self._deps.read_pisugar_i2c()
            except Exception:
                i2c_percent, i2c_charging = None, None
            if percent is None:
                percent = i2c_percent
            if charging is None:
                charging = i2c_charging

        if percent is not None or charging is not None:
            self._seen_provider = True
        return {"percent": percent, "charging": charging}

    @property
    def available(self) -> bool:
        """Whether any provider has ever answered. Reported as capabilities.pisugar."""
        return self._seen_provider

    @staticmethod
    def _loaded(name: str) -> Any:
        """Looks a plugin up by name without importing it.

        `plugins.loaded` is the registry plugins register themselves into (F12),
        and a `.get` on a string key cannot raise, so no guard is needed here.
        """
        return plugins.loaded.get(name)

    @staticmethod
    def _as_percent(value: Any) -> float:
        """Converts a candidate value to a percentage, refusing what cannot be one.

        A percentage is a finite number in 0-100 whichever tier produced it, so
        anything else is refused like a failed cast and the caller treats it as
        a miss: the next candidate, and then the tier below, still get their
        turn. A bool is refused before it can convert, since `True` is an `int`
        that lands on 1.0 and would report a charge flag as a 1% battery. The
        bound is written as a range the value must fall inside so that NaN and
        the infinities are refused too: NaN fails every comparison, and one that
        survived would reach `json.dumps` and serialise as the literal `NaN`,
        which is not JSON.
        """
        if isinstance(value, bool):
            raise TypeError("a boolean is not a percentage")
        percent = float(value)
        if not 0.0 <= percent <= 100.0:
            raise ValueError("a percentage is a finite number in 0-100")
        return percent

    @staticmethod
    def _as_charging(value: Any) -> bool:
        """Converts a candidate value to a charging flag, refusing what cannot be one.

        Charging is a two-valued fact, so a value is accepted only when it is
        unambiguously one of the two: a `bool` as itself; an `int` when it is
        exactly `0` or `1`, nothing else; a `str` from a fixed set of spellings
        after `strip().lower()` (`"true"`/`"false"`, `"1"`/`"0"`, `"yes"`/`"no"`,
        `"on"`/`"off"`). Everything else is refused like a failed cast, including
        floats and unrecognised strings, and the caller treats a refusal as a
        miss: the next candidate, and then the tier below, still get their
        turn. `bool` is checked first because it is a subclass of `int` and
        `True == 1`; without that order the `int` branch would swallow it. This
        is the mirror image of `_as_percent`, which refuses a `bool` for the
        opposite reason: there a bare 0/1 must not be misread as a percentage,
        here a bare 0/1 is exactly what a charging flag is allowed to be.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            if value in (0, 1):
                return bool(value)
            raise ValueError("an int charging flag must be 0 or 1")
        if isinstance(value, str):
            normalised = value.strip().lower()
            if normalised in ("true", "1", "yes", "on"):
                return True
            if normalised in ("false", "0", "no", "off"):
                return False
            raise ValueError("unrecognised charging string")
        raise TypeError("not a charging flag")

    @staticmethod
    def _client(plugin: Any) -> Any:
        """Returns the plugin's PiSugar hardware client if it is ready, else None.

        The battery state is not on the plugin object: `pisugarx_ext` loads a
        `PiSugar(plugins.Plugin)` that holds its client at `self.ps`, and both
        the values and their accessors live there (F29). Reaching for `ps` is
        third-party attribute access like any other, so a plugin without one is
        a miss and never an error.

        A client that does not report itself ready counts as absent. Its
        `battery_level` and `power_plugged` are `0` and `False` from the
        constructor until a device is actually found, and the refresh thread
        that would replace them never starts while none is (F29). Reading those
        would mean a confident empty battery, a latched `capabilities.pisugar`
        and the tier below pre-empted for good, so instead both fields fall
        through as if the plugin held no client at all. `ready` is probed as
        defensively as `ps`: absent, `None`, or a property that raises all mean
        not ready.
        """
        try:
            client = getattr(plugin, "ps", None)
        except Exception:
            return None
        if client is None:
            return None
        try:
            ready = getattr(client, "ready", None)
        except Exception:
            return None
        return client if ready else None

    @staticmethod
    def _probe(
        plugin: Any, client: Any, names: Sequence[str], cast: Callable[[Any], Any]
    ) -> Any:
        """Tries a list of candidate attribute names, returning the first usable value.

        Each candidate is looked for on the plugin and then on its hardware
        client, since that is where the reference unit keeps the reading. The
        client is resolved by the caller, once per plugin per read, and is None
        when there is none or it is not ready. The plugin object itself is
        probed either way: another plugin may hold its reading there and have no
        client at all. These names belong to third-party plugins and are
        deliberately not pinned in SPEC section 11, so they are probed rather
        than assumed. Every miss is "unavailable", never an error.
        """
        targets = [plugin]
        if client is not None:
            targets.append(client)
        for name in names:
            for target in targets:
                try:
                    value = getattr(target, name, None)
                    if callable(value):
                        value = value()
                except Exception:
                    continue
                if value is None:
                    continue
                try:
                    return cast(value)
                except Exception as err:
                    # Anything the conversion refuses is a miss, never an error.
                    # `float()` raises OverflowError on a huge int, and a custom
                    # `__float__` may raise anything at all; neither is a
                    # TypeError or a ValueError, and both used to escape. The
                    # catch is broad enough to hide a defect in the cast itself,
                    # so the refusal is logged rather than silently dropped.
                    log.debug("[companion] battery value from %s refused: %s", name, err)
                    continue
        return None


class HandshakeStore:
    """Enumerates and annotates the capture directory.

    The listing comes from the directory, never from agent._handshakes, which
    only holds the current session. Two counts are reported: `pcapng` matches
    what the e-ink display shows (the stock counter globs *.pcapng only, SPEC
    F15) and `total` is the honest figure including legacy .pcap captures.
    """

    def __init__(self, directory: str, limit: int = HANDSHAKE_LIMIT) -> None:
        self._directory = directory
        self._limit = limit

    def counts(self) -> tuple[int, int]:
        """Returns (pcapng_count, total_count)."""
        pcapng = total = 0
        for name in self._names():
            total += 1
            if name.endswith(".pcapng"):
                pcapng += 1
        return pcapng, total

    def entries(self) -> tuple[list[dict], bool, int]:
        """Returns (entries, truncated, total), newest first.

        Caps at `limit` and reports truncation rather than dropping silently. An
        unreadable file is skipped, not fatal.
        """
        records = []
        for name in self._names():
            path = os.path.join(self._directory, name)
            try:
                stat = os.stat(path)
            except OSError:
                # One unreadable file must not cost the whole listing.
                continue
            ssid, bssid = parse_handshake_filename(name)
            records.append(
                {
                    "filename": name,
                    "ssid": ssid,
                    "bssid": bssid,
                    "mtime": float(stat.st_mtime),
                    "size": int(stat.st_size),
                    "gps": self._read_sidecar(path),
                }
            )
        records.sort(key=lambda entry: entry["mtime"], reverse=True)
        total = len(records)
        truncated = total > self._limit
        return records[: self._limit], truncated, total

    def _names(self) -> list[str]:
        """Capture filenames in the directory. Missing directory yields nothing."""
        try:
            return [
                name
                for name in os.listdir(self._directory)
                if name.endswith(".pcapng") or name.endswith(".pcap")
            ]
        except OSError:
            return []

    @staticmethod
    def _read_sidecar(capture_path: str) -> dict | None:
        """Reads and normalises the sidecar beside a capture, or None."""
        path = sidecar_path_for(capture_path)
        try:
            with open(path, "rt", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, Mapping):
            return None
        return normalise_sidecar(raw)

    def newest(self) -> dict | None:
        """The most recent capture as a LastHandshake, or None if the directory is empty."""
        newest_name = None
        newest_mtime = -1.0
        for name in self._names():
            try:
                mtime = os.stat(os.path.join(self._directory, name)).st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest_mtime, newest_name = mtime, name
        if newest_name is None:
            return None
        ssid, bssid = parse_handshake_filename(newest_name)
        return {
            "filename": newest_name,
            "ssid": ssid,
            "bssid": bssid,
            "mtime": float(newest_mtime),
        }

    def write_sidecar(self, capture_path: str, gps: Mapping[str, Any], now: float) -> str | None:
        """Writes a .gps.json beside a capture and returns its path.

        Does nothing and returns None when there is no fix - absent coordinates
        are not 0.0, 0.0 - or when a sidecar already exists.
        """
        if not gps or not gps.get("fix"):
            # No coordinates is not 0.0, 0.0.
            return None
        path = sidecar_path_for(capture_path)
        if os.path.exists(path):
            return None
        try:
            with open(path, "wt", encoding="utf-8") as handle:
                json.dump(sidecar_payload(gps, now), handle)
        except OSError as err:
            log.error("[companion] could not write %s: %s", path, err)
            return None
        return path


# ---------------------------------------------------------------------------
# Protocol core
# ---------------------------------------------------------------------------


# The incoming contract, mirrored from docs/schemas/incoming/*.json.
#
# Duplicated deliberately rather than validated with jsonschema at runtime: the
# schemas are the source of truth, but shipping a JSON Schema engine to a Pi
# Zero to re-derive a dozen field names on every frame is not a trade worth
# making. The conformance test asserts this table and the schemas agree, so the
# duplication cannot drift silently.
#
# type -> (required fields, optional fields)
INCOMING_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "auth": (frozenset({"token"}), frozenset()),
    "get_stats": (frozenset(), frozenset()),
    "get_access_points": (frozenset(), frozenset()),
    "get_handshakes": (frozenset(), frozenset()),
    "get_peers": (frozenset(), frozenset()),
    "get_log": (frozenset(), frozenset({"lines"})),
    "get_face_status": (frozenset(), frozenset()),
    "get_screen": (frozenset(), frozenset()),
    "get_gps_data": (frozenset(), frozenset()),
    "gps_data": (frozenset({"latitude", "longitude"}), frozenset({"accuracy"})),
    "ping": (frozenset(), frozenset()),
    "set_mode": (frozenset({"mode"}), frozenset()),
    "set_pasv": (frozenset({"on"}), frozenset()),
    "reboot": (frozenset(), frozenset()),
    "shutdown": (frozenset(), frozenset()),
}

# Present on every incoming message regardless of type.
INCOMING_ENVELOPE_FIELDS = frozenset({"type", "message_id"})


def validate_incoming(message: Mapping[str, Any]) -> None:
    """Checks one incoming message against INCOMING_FIELDS.

    Raises ProtocolError("bad_request") for a missing required field or an
    unexpected key. Every incoming schema sets additionalProperties false, so an
    unknown key is a client that disagrees with us about the contract - and
    silently ignoring it is how the two sides drift apart without anyone
    noticing.
    """
    kind = message.get("type")
    spec = INCOMING_FIELDS.get(str(kind))
    if spec is None:
        raise ProtocolError("unknown_command", f"unknown command {kind!r}")
    required, optional = spec

    missing = sorted(required - set(message))
    if missing:
        raise ProtocolError("bad_request", f"missing field(s): {', '.join(missing)}")

    allowed = required | optional | INCOMING_ENVELOPE_FIELDS
    unexpected = sorted(set(message) - allowed)
    if unexpected:
        raise ProtocolError("bad_request", f"unexpected field(s): {', '.join(unexpected)}")


class Router:
    """Turns one incoming message into the messages that answer it.

    Deliberately free of sockets, threads and the event loop: `handle` takes a
    parsed dict and returns a list of outgoing dicts. Everything the protocol
    does can therefore be tested directly, which is what keeps branch coverage
    of the protocol within reach, and the transport above stays thin enough to be covered
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
        self._options = options
        self._deps = deps
        self._agent_getter = agent_getter
        self._session = session
        self._gps = gps
        self._battery = battery
        self._handshakes = handshakes
        self._plugin_version = plugin_version

    # -- authentication ----------------------------------------------------

    @property
    def auth_required(self) -> bool:
        """Whether a token is configured. When false, `auth` is accepted but unnecessary."""
        return bool(option(self._options, "token"))

    def check_token(self, candidate: Any) -> bool:
        """Constant-time token comparison. A non-string candidate is a failure, not a crash."""
        if not isinstance(candidate, str):
            return False
        expected = str(option(self._options, "token") or "")
        # compare_digest, not ==: string equality short-circuits on the first
        # differing byte and leaks the length of the shared prefix through timing.
        return hmac.compare_digest(candidate, expected)

    # -- dispatch ----------------------------------------------------------

    def handle(self, message: Mapping[str, Any], authenticated: bool) -> list[dict]:
        """Dispatches one incoming message.

        Returns the messages to send, in order: the acknowledgment first when
        the request carried a message_id, then the reply or a broadcast. Raises
        nothing - a failure becomes an `error` message, because an exception
        here would take down the connection handler and, without the guard in
        the transport, the event loop with it.
        """
        now = self._deps.now()
        message_id = message.get("message_id")
        kind = message.get("type")

        if not isinstance(kind, str):
            return [error_envelope("bad_request", "missing message type", now, message_id)]

        try:
            validate_incoming(message)
        except ProtocolError as err:
            return [error_envelope(err.code, err.message, now, message_id)]

        if kind == "auth":
            if not self.auth_required or self.check_token(message.get("token")):
                return [envelope("acknowledgment", {"acknowledged": "auth"}, now, message_id)]
            return [error_envelope("unauthorized", "authentication failed", now, message_id)]

        if self.auth_required and not authenticated:
            return [error_envelope("unauthorized", "authentication required", now, message_id)]

        out: list[dict] = []
        if message_id is not None:
            out.append(envelope("acknowledgment", {"acknowledged": kind}, now, message_id))

        try:
            out.extend(self._dispatch(kind, message, message_id))
        except ProtocolError as err:
            out.append(error_envelope(err.code, err.message, now, message_id))
        except Exception as err:
            # A handler must never take down the connection, and without this
            # the transport would have to guess which failures are survivable.
            log.exception("[companion] %s failed", kind)
            out.append(error_envelope("internal_error", str(err), now, message_id))
        return out

    def _dispatch(
        self, kind: str, message: Mapping[str, Any], message_id: str | None
    ) -> list[dict]:
        """Routes one authenticated command. Raises ProtocolError for a refusal."""
        now = self._deps.now()

        if kind == "get_stats":
            return [envelope("stats", self.stats(), now, message_id)]
        if kind == "get_access_points":
            return [envelope("access_points", self.access_points(), now, message_id)]
        if kind == "get_handshakes":
            entries, truncated, total = self._handshakes().entries()
            payload = {"entries": entries, "truncated": truncated, "total": total}
            return [envelope("handshakes_list", payload, now, message_id)]
        if kind == "get_peers":
            return [envelope("peers_list", {"entries": self.peers()}, now, message_id)]
        if kind == "get_log":
            return [envelope("log_lines", self.log_lines(message.get("lines")), now, message_id)]
        if kind == "get_face_status":
            return [envelope("face_status", self.face_status(), now, message_id)]
        if kind == "get_screen":
            return [envelope("screen_image", self.screen_image(), now, message_id)]
        if kind == "get_gps_data":
            return [envelope("gps_update", self._gps.current(), now, message_id)]
        if kind == "gps_data":
            self._gps.set_browser_position(
                message.get("latitude"), message.get("longitude"), message.get("accuracy")
            )
            return [envelope("gps_update", self._gps.current(), now, message_id)]
        if kind == "ping":
            return [envelope("pong", {}, now, message_id)]
        if kind == "set_mode":
            return self.set_mode(str(message.get("mode") or ""))
        if kind == "set_pasv":
            return self.set_pasv(bool(message.get("on")))
        if kind == "reboot":
            return self.reboot()
        if kind == "shutdown":
            return self.shutdown()

        # Unreachable while INCOMING_FIELDS and this dispatch agree, which the
        # conformance test enforces. Kept so that adding a type to the table
        # without a route fails loudly instead of returning nothing.
        raise ProtocolError("unknown_command", f"unknown command {kind!r}")

    # -- agent access ------------------------------------------------------

    def _agent(self) -> Any:
        """The agent, or None before on_ready has fired."""
        return self._agent_getter()

    def _require_agent(self) -> Any:
        """The agent, or a ProtocolError if pwnagotchi has not handed it over yet."""
        agent = self._agent()
        if agent is None:
            raise ProtocolError("internal_error", "agent is not ready yet")
        return agent

    @staticmethod
    def _pasv_plugin() -> Any:
        """The loaded pasv_mode plugin, or None. Never imported (soft dependency)."""
        try:
            return plugins.loaded.get("pasv_mode")
        except Exception:
            return None

    def _mode(self) -> str:
        """AUTO, PASV or MANUAL as the client should display it.

        Never raises below the agent getter: face_status() depends on that to
        always have a mode to report. `self._agent()` itself is trusted not to
        raise - it is our own closure over `on_ready`'s argument, not a call
        into third-party code - but everything read off what it returns is
        guarded: `agent.mode`, in case a fork turns it into a property that
        raises, and `pasv_mode.passive`, which is more exposed than either,
        being a plugin we do not control and treat as a soft dependency
        precisely because of that. `_pasv_plugin()` only guards the lookup in
        `plugins.loaded`; reading `.passive` off what it returns is a separate
        call into that plugin and needs its own guard.
        """
        agent = self._agent()
        if agent is None:
            return "AUTO"
        try:
            mode = getattr(agent, "mode", "auto")
        except Exception:
            mode = "auto"
        if mode == "manual":
            return "MANUAL"
        plugin = self._pasv_plugin()
        try:
            passive = plugin is not None and bool(getattr(plugin, "passive", False))
        except Exception:
            passive = False
        if passive:
            return "PASV"
        return "AUTO"

    def initial_burst(self) -> list[dict]:
        """The messages pushed to a client on accept: stats, access_points, face_status.

        Sent unprompted so a freshly connected client has state without a round
        of polling, which matters on a link this slow.
        """
        return [
            self.push("stats", self.stats()),
            self.push("access_points", self.access_points()),
            self.push("face_status", self.face_status()),
        ]

    # -- individual replies ------------------------------------------------

    def stats(self) -> dict:
        """Builds the Stats payload. Must not touch agent.session() (SPEC F7)."""
        agent = self._agent()
        store = self._handshakes()
        pcapng, total = store.counts()
        peers = getattr(agent, "_peers", {}) or {}
        access_points = getattr(agent, "_access_points", []) or []

        newest_peer = None
        if peers:
            try:
                newest_peer = normalise_peer(
                    max(peers.values(), key=lambda p: to_epoch(getattr(p, "last_seen", 0)) or 0.0)
                )
            except Exception:
                newest_peer = None

        return {
            "uptime": int(pwnagotchi.uptime()),
            "mode": self._mode(),
            # The attribute, not agent.session(): that is a blocking HTTP call
            # with a 30 second timeout and must never sit on the request path.
            "channel": self._int_or_none(getattr(agent, "_current_channel", None)),
            "battery": self._battery.read(),
            "temperature": self._temperature(),
            # Matches the e-ink display, which counts *.pcapng only.
            "handshakes": pcapng,
            "handshakesTotal": total,
            "peers": len(peers),
            "accessPoints": len(access_points),
            "lastHandshake": store.newest(),
            "lastPeer": newest_peer,
            "gps": self._gps.current(),
            "sessionAge": self._session.age,
            "capabilities": self.capabilities(),
        }

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _temperature() -> int | None:
        """Reads the SoC temperature, or None if the sysfs node is unavailable."""
        try:
            return int(pwnagotchi.temperature())
        except Exception:
            return None

    def access_points(self) -> list[dict]:
        """Maps agent._access_points. Never calls the side-effecting getter (SPEC F3)."""
        agent = self._agent()
        # _access_points, never get_access_points(): the getter fires
        # plugins.on("wifi_update"), runs epoch.observe and drives strategy
        # channel selection, so reading it would perturb the unit's behaviour.
        raw = getattr(agent, "_access_points", []) or []
        mapped = []
        for entry in raw:
            if isinstance(entry, Mapping):
                mapped.append(map_access_point(entry))
        return mapped

    def peers(self) -> list[dict]:
        """Maps agent._peers values. grid.peers() is not used: different shape (SPEC 2.8)."""
        agent = self._agent()
        peers = getattr(agent, "_peers", {}) or {}
        out = []
        for peer in peers.values():
            try:
                out.append(normalise_peer(peer))
            except Exception:
                continue
        return out

    def log_lines(self, count: int | None) -> dict:
        """Tails the configured log. Path comes from config, never hardcoded (SPEC F17)."""
        try:
            requested = LOG_LINES_DEFAULT if count is None else int(count)
        except (TypeError, ValueError):
            requested = LOG_LINES_DEFAULT
        requested = max(1, min(requested, LOG_LINES_MAX))

        agent = self._require_agent()
        try:
            path = agent._config["main"]["log"]["path"]
        except Exception:
            raise ProtocolError("log_unavailable", "no log path in configuration")

        try:
            lines = tail_lines(path, requested)
        except OSError as err:
            raise ProtocolError("log_unavailable", str(err))
        return {"lines": lines, "path": path}

    def screen_image(self) -> dict:
        """Reads the e-ink frame under its lock (SPEC F18).

        Raises ProtocolError("no_frame") when the frame file does not exist yet,
        which is normal early in a boot. The lock is held for the read only,
        never across a send.
        """
        from pwnagotchi.ui import web as ui_web

        path = ui_web.frame_path
        try:
            with ui_web.frame_lock:
                with open(path, "rb") as handle:
                    raw = handle.read()
                mtime = os.stat(path).st_mtime
        except OSError:
            raise ProtocolError("no_frame", "the display frame has not been written yet")
        # The lock covers the read only, never the send: this payload is large
        # and the display thread must not wait on a slow Bluetooth link.
        return {"png": base64.b64encode(raw).decode("ascii"), "mtime": float(mtime)}

    def face_status(self) -> dict:
        """Current face and status as text. Faces are unicode, never images (D11).

        Never raises, on the same trust boundary as _mode(): every read below
        the agent getter is guarded, and the getter itself is trusted not to
        raise. on_ui_update depends on that to always have something to push,
        so a view it cannot read still degrades to a partial payload instead
        of silencing face_status altogether (SPEC.md 2.13). Note that _mode()
        is called from the return statement, outside the try below, which
        covers the view read alone - the guarantee comes from _mode() keeping
        its own, not from being wrapped here.
        """
        face = status = ""
        try:
            # No hasattr guard: F28a pins `Agent.view`, so the only ways this
            # fails are the agent being None before on_ready and a fork that
            # renamed the accessor. Both raise, both mean the same empty pair,
            # and one `except` covers them - where the guard added a leg that
            # coverage.py cannot see, being a ternary rather than a statement.
            view = self._agent().view()
        except Exception:
            view = None
        if view is not None:
            face = self._view_text(view, "face")
            status = self._view_text(view, "status")
        return {"face": face, "status": status, "mode": self._mode()}

    @staticmethod
    def _view_text(view: Any, key: str) -> str:
        """Reads one text element from the UI view.

        `View.get` returns the element's value rather than the widget, and `None`
        for a key the state does not hold (F28). Both `face` and `status` exist
        on a running unit, so `None` can only mean a future version renamed a
        key; it becomes an empty string because the protocol types both fields
        as strings and a null is not a shape the schema allows. The `try` stays
        because this is a third-party fork that updates itself underneath us,
        and nothing may escape into the event loop.
        """
        try:
            value = view.get(key)
        except Exception:
            return ""
        return value if isinstance(value, str) else ""

    def capabilities(self) -> dict:
        """What this instance can do: pasv, pisugar, configured gps source, version.

        The client gates its controls on this and must not infer availability any
        other way.
        """
        return {
            "pasv": self._pasv_plugin() is not None,
            "pisugar": self._battery.available,
            "gpsSource": str(option(self._options, "gps_source")),
            "pluginVersion": self._plugin_version,
        }

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
        if mode not in ("auto", "manual"):
            raise ProtocolError("bad_request", "mode must be 'auto' or 'manual'")

        agent = self._require_agent()
        current = "manual" if getattr(agent, "mode", "auto") == "manual" else "auto"
        if mode == current:
            # Nothing to do, and restarting the services to arrive where we
            # already are would be a gratuitous outage.
            return []

        # Two different vocabularies meet here. pwnagotchi.restart takes MANU or
        # AUTO; the wire uses the Mode enum from common.json, which is MANUAL.
        # Sending the restart argument to the client would emit a value no
        # schema accepts.
        restart_arg = "MANU" if mode == "manual" else "AUTO"
        wire_mode = "MANUAL" if mode == "manual" else "AUTO"
        now = self._deps.now()
        broadcast = envelope(
            "restarting", {"reason": "mode_change", "mode": wire_mode}, now
        )

        # NOT agent.mode = mode. The mode is fixed at process start from the
        # --manual flag; the attribute is only a label (SPEC F10). The real
        # switch restarts bettercap and pwnagotchi, taking this plugin with
        # them - so the client is told first and the effect is scheduled after.
        self._deps.spawn(lambda: self._deps.restart_pwnagotchi(restart_arg))
        return [broadcast]

    def set_pasv(self, on: bool) -> list[dict]:
        """Toggles passive mode through the pasv_mode plugin (SPEC F12).

        Raises ProtocolError("pasv_unavailable") when the plugin is not loaded
        and ("pasv_requires_auto") outside auto. Emits plugins.on and returns
        only an acknowledgment: dispatch is asynchronous (SPEC F8), so the new
        state cannot be read back here and reaches the client with the next
        stats.
        """
        plugin = self._pasv_plugin()
        if plugin is None:
            raise ProtocolError("pasv_unavailable", "the pasv_mode plugin is not installed")

        agent = self._require_agent()
        if getattr(agent, "mode", "auto") != "auto":
            raise ProtocolError("pasv_requires_auto", "passive mode only applies in auto")

        plugins.on("pasv_on" if on else "pasv_off")
        # No read-back of plugin.passive here: plugins.on() queues the work onto
        # each plugin's own thread and returns nothing (SPEC F8), so the value
        # would still be the old one. The client learns the new state from the
        # next stats, and shows the toggle as pending until then.
        return []

    def reboot(self) -> list[dict]:
        """Broadcasts `restarting` then schedules pwnagotchi.reboot (SPEC F9)."""
        broadcast = envelope("restarting", {"reason": "reboot", "mode": None}, self._deps.now())
        self._deps.spawn(self._deps.reboot_device)
        return [broadcast]

    def shutdown(self) -> list[dict]:
        """Broadcasts `restarting` then schedules pwnagotchi.shutdown (SPEC F9)."""
        broadcast = envelope("restarting", {"reason": "shutdown", "mode": None}, self._deps.now())
        self._deps.spawn(self._deps.shutdown_device)
        return [broadcast]

    # -- pushes ------------------------------------------------------------

    def on_handshake_message(self, filename: str, access_point: Any, station: Any) -> dict:
        """Builds the `handshake` push, normalising both hook signatures (SPEC F20)."""
        gps = self._gps.current()
        return self.push(
            "handshake",
            {
                "filename": os.path.basename(filename or ""),
                # Both hook signatures reach here: MAC strings on one path,
                # bettercap dicts on the other (SPEC F20).
                "ap": mac_of(access_point),
                "station": mac_of(station),
                "gps": (
                    normalise_sidecar(sidecar_payload(gps, self._deps.now()))
                    if gps.get("fix")
                    else None
                ),
            },
        )

    def push(self, kind: str, data: Any) -> dict:
        """Wraps a push payload in an envelope with the current timestamp."""
        return envelope(kind, data, self._deps.now())


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def resolve_ws_serve() -> Callable[..., Any]:
    """Returns the websockets server factory for whatever version is installed.

    The pwnagotchi image pins no version (SPEC F21) and the library moved its
    server API: websockets.serve was superseded by
    websockets.asyncio.server.serve in 14. Picking one breaks on the other, and
    the failure only shows up when a client connects.
    """
    try:
        from websockets.asyncio.server import serve  # websockets >= 14
    except ImportError:
        from websockets import serve  # type: ignore[no-redef]
    return serve


def websockets_version() -> str:
    """The installed websockets version, for the startup log. Never raises."""
    try:
        import websockets

        return str(getattr(websockets, "__version__", "unknown"))
    except Exception:
        return "unavailable"


def build_ssl_context(cert_path: str, key_path: str) -> ssl.SSLContext | None:
    """Loads the certificate chain, or returns None if it cannot.

    Returning None is the whole point: the caller starts no listener at all.
    There is no plaintext fallback, because a silent one is a worse outcome than
    being down - iOS requires TLS, so a plaintext server would not serve the app
    and would quietly expose the socket instead (D4).
    """
    for label, path in (("certificate", cert_path), ("key", key_path)):
        if not path:
            log.error("[companion] no TLS %s configured; refusing to start any listener", label)
            return None
        if not os.path.isfile(path):
            log.error(
                "[companion] TLS %s %s does not exist; refusing to start any listener",
                label,
                path,
            )
            return None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
    except (ssl.SSLError, OSError, ValueError) as err:
        log.error("[companion] TLS material unusable (%s); refusing to start any listener", err)
        return None
    return context


def content_security_policy(bound_addresses: Sequence[str], ws_port: int) -> str:
    """Builds the Content-Security-Policy header value for one response.

    `connect-src` names `'self'` plus one `wss://<address>:<ws_port>` origin
    per address the plugin is currently bound to (SPEC 2.15.1, 2.3.1), so it
    must be rebuilt per request rather than cached: a rebind changes the
    bound set and the header has to follow it. Every address reaching here is
    an IPv4 literal - `Listeners` enforces that on both sides of the address
    seam (SPEC 2.3.1) - so none needs the bracket an IPv6 literal would need
    in a URL authority.
    """
    connect_src = " ".join(
        ["'self'"] + [f"wss://{ip}:{ws_port}" for ip in bound_addresses]
    )
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data: https://*.tile.openstreetmap.org; "
        f"connect-src {connect_src}; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    )


def make_http_handler(
    web_root: str,
    bound_addresses: Callable[[], Sequence[str]],
    ws_port: int,
) -> type:
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

    `bound_addresses` is called on every response, not just once at bind time,
    so the Content-Security-Policy header (SPEC 2.15.1) reflects the bound set
    as it stands when the response is sent, including one taken after a
    rebind.
    """
    import http.server

    root = os.path.abspath(web_root)

    class Handler(http.server.SimpleHTTPRequestHandler):
        # Declared rather than inherited from the system database: a minimal
        # Raspberry Pi OS image does not know .webmanifest, and iOS silently
        # refuses to install an app whose manifest arrives as octet-stream.
        extensions_map = {
            "": "application/octet-stream",
            ".html": "text/html; charset=utf-8",
            ".webmanifest": "application/manifest+json",
            ".js": "text/javascript",
            ".mjs": "text/javascript",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/vnd.microsoft.icon",
            ".wasm": "application/wasm",
            ".map": "application/json",
            ".woff2": "font/woff2",
        }

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=root, **kwargs)

        def log_message(self, fmt: str, *args: Any) -> None:
            # At info this would flood the pwnagotchi log on every asset.
            log.debug("[companion:http] " + fmt, *args)

        def list_directory(self, path: str) -> None:
            self.send_error(404, "Not Found")
            return None

        def translate_path(self, path: str) -> str:
            resolved = super().translate_path(path)
            # Belt and braces: the base class normalises away traversal, and
            # send_head rejects it earlier, but web_root is the boundary and it
            # is worth enforcing where the answer is computed.
            if not os.path.abspath(resolved).startswith(root):
                return os.path.join(root, "index.html")
            return resolved

        def send_head(self):  # type: ignore[override]
            raw = self.path.split("?", 1)[0].split("#", 1)[0]
            if ".." in raw:
                self.send_error(400, "Bad Request")
                return None
            target = self.translate_path(self.path)
            if not os.path.isfile(target) and "." not in os.path.basename(raw):
                # Single-page app: a deep link is not a missing file, it is a
                # route the client will resolve once index.html has loaded.
                self.path = "/index.html"
            return super().send_head()

        def end_headers(self) -> None:
            # `self.path` is unset when the request line itself was rejected: a malformed
            # line, one over the length limit, or an unsupported HTTP version all call
            # send_error before parse_request assigns it. Reading it there raised, and the
            # response never left, so a caller who sent garbage got a traceback in the log
            # and nothing on the wire.
            name = os.path.basename(getattr(self, "path", "").split("?", 1)[0])
            if name in ("", "index.html", "sw.js", "service-worker.js", "registerSW.js"):
                # Everything else Vite emits is content-hashed and immutable.
                self.send_header("Cache-Control", "no-cache")
            # On every response, not only the ones above: the token lives in
            # localStorage and anything that runs script on this origin reads
            # it (SPEC 2.15.1). `bound_addresses` is called here, not cached,
            # so a rebind is reflected on the very next response.
            self.send_header(
                "Content-Security-Policy",
                content_security_policy(bound_addresses(), ws_port),
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

    return Handler


class Listeners:
    """Owns the bound sockets and reconciles them as addresses come and go.

    The tether address usually does not exist at load - a PAN link has none
    until the phone connects - so binding is a continuous reconciliation rather
    than a one-off. The state is keyed on the address itself, which is what
    lets a new DHCP lease converge without a plugin reload:

        selected address, nothing bound    bind both listeners
        bound address gone from the host   close both, drop its clients
        another address in the same block  a new selection: bind it, drop the old
        nothing matches                    skip quietly after the first log
        bind raises OSError                log, leave unbound, retry next pass
        one interface fails to enumerate   skip it, the rest of the pass stands
        enumeration itself raises          log, change nothing, retry next pass

    Never binds 0.0.0.0, :: or a hostname, and never decides a bind from an
    interface name (D5.1). A failure here is logged and retried, never fatal:
    one unbindable address must not take down the ones that work.
    """

    def __init__(
        self,
        options: Mapping[str, Any],
        deps: Deps,
        ssl_context: ssl.SSLContext,
        client_handler: Callable[..., Any] | None = None,
    ) -> None:
        self._options = options
        self._deps = deps
        self._ssl = ssl_context
        self._client_handler = client_handler
        if options is not None and "interfaces" in options:
            # Honouring it would keep the exposure D5.1 closes: an interface
            # name says which device, not which network, and bnep numbering
            # follows the order peers connected rather than the owner's intent.
            log.warning("[companion] the `interfaces` option is ignored, use `bind_addresses`")
        self._selectors = parse_bind_addresses(bind_addresses_option(options))
        # ip -> (ws_server, http_server)
        self._bound: dict[str, tuple[Any, Any]] = {}
        self._nothing_logged = False
        self._ever_enumerated = False
        self._loop: Any = None
        self._loop_thread: threading.Thread | None = None
        # `_bound` is written from two threads: the background loop calling
        # `reconcile()` and whichever thread pwnagotchi calls `on_unload` on,
        # which calls `stop()`. The lock alone would make each pass atomic but
        # would not stop a `reconcile()` that was already waiting on it from
        # binding right after `stop()` finished, re-opening what teardown just
        # closed; `_stopped` is what makes that pass a no-op instead.
        #
        # This only covers the writers. A serving thread's HTTP handler also
        # reads `_bound` for the CSP header, on every response, without this
        # lock (see the `lambda: sorted(self._bound)` passed to
        # `make_http_handler` in `_serve_http`). That read is not made
        # consistent by this lock and does not need to be: it is a snapshot
        # for a header on one response, a stale one only widens or narrows
        # `connect-src` by whichever address is mid-rebind, and the next
        # response reads again. Locking it would only add contention.
        self._lock = threading.Lock()
        self._stopped = False

    def reconcile(self) -> list[str]:
        """Runs one reconciliation pass. Returns the addresses bound after it.

        Holds the lock for the whole pass, not just the writes to `_bound`:
        interleaving with `stop()` is what this guards against, and a pass
        `stop()` cannot see until it finishes is the point, not a side effect.

        The cost is bounded, address by address, for every wait the plugin
        controls: `_serve_ws` waits up to ten seconds for its future, closing
        an address waits up to five for `_shutdown_ws`, and `_shutdown_http`
        costs one `poll_interval` (0.5s) for `serve_forever` to notice.
        `_serve_http`'s `Server.get_request` wraps each accepted connection
        in TLS, not the listening socket, and does so without
        `do_handshake_on_connect`, so `accept()` on that socket is a plain TCP
        accept and `serve_forever`'s select loop is never parked inside a
        handshake (#100) - the 0.5s figure is a real bound, not a best case.
        Enumerating local addresses is either a handful of local syscalls or,
        on the `ip -4 addr show` fallback, capped by its own 5 s
        `subprocess.run` timeout. Everything else in the locked path -
        selection, binding the HTTPS socket, starting a server's thread,
        releasing a socket that never served - is local and synchronous,
        deliberately: `_serve_http`'s `Server` overrides `server_bind` to skip
        the reverse-DNS lookup `http.server.HTTPServer` would otherwise make,
        which on the reference hardware resolves against whatever the iPhone
        hands out over BT PAN and can cost seconds per address with nothing
        reachable at it. A pass touching several addresses adds these,
        otherwise-bounded, costs up rather than sharing one bound. (This
        assumes the default `Deps.list_local_ipv4`; a caller that replaces the
        seam with something that blocks indefinitely owns that trade.)
        """
        with self._lock:
            if self._stopped:
                return sorted(self._bound)
            return self._reconcile_locked()

    def _reconcile_locked(self) -> list[str]:
        """The reconciliation pass itself. Caller holds `self._lock` and has
        already checked `self._stopped`; nothing here checks either again.
        """
        try:
            local = list(self._deps.list_local_ipv4())
        except Exception as err:
            # A failed enumeration observed nothing, so it cannot be read as
            # "no address is local": tearing the bound set down on a hiccup of
            # `ip -4 addr show` would drop a healthy tether. Leave it as it is
            # and let the next pass decide.
            #
            # A blip and a unit that will never work read identically here, so
            # they are logged at different levels: if no enumeration has ever
            # succeeded since load, this is a permanent failure - no `ip`
            # binary and no `netifaces` - that nobody would otherwise notice.
            # Having bound nothing is not that: a unit whose tether has not
            # come up, or a `bind_addresses` matching nothing, enumerates fine
            # on every pass, and that is a configuration problem with its own
            # message below.
            if self._ever_enumerated:
                log.warning("[companion] could not enumerate local addresses: %s", err)
            else:
                log.error(
                    "[companion] could not enumerate local addresses and no enumeration has "
                    "ever succeeded, the companion is unreachable: %s",
                    err,
                )
            return sorted(self._bound)
        self._ever_enumerated = True
        selected = select_bind_addresses(local, self._selectors)

        for ip in [bound for bound in self._bound if bound not in selected]:
            log.info("[companion] %s is no longer a local address, closing its listeners", ip)
            self._close(ip)

        if not selected:
            if not self._nothing_logged:
                log.info("[companion] no local address matches bind_addresses, waiting")
                self._nothing_logged = True
            return []

        self._nothing_logged = False
        for ip in selected:
            if ip not in self._bound:
                self._open(ip)
        return sorted(self._bound)

    def _open(self, ip: str) -> None:
        """Binds both listeners on one address. A failure is logged, never fatal.

        Precondition: `ip` came out of `select_bind_addresses`, which is where
        D5.1 is enforced - it drops anything that is not a literal, bindable
        IPv4, so 0.0.0.0, :: and hostnames cannot reach here. The check is not
        repeated in this method: D5.1 is already gated on both sides of the
        address seam (SPEC 2.3.1), and a third copy this far down would say
        nothing the other two do not while being free to drift from them. Any
        new caller must go through that selection, not call `_open` with an
        address from elsewhere.

        The HTTPS server is created and bound here, but not TLS-wrapped and not
        started: TLS is applied per accepted connection, not at creation (see
        `_serve_http`), and the serving thread is only launched once
        `self._bound[ip]` holds this address, so a request cannot be
        dispatched, and answered with a Content-Security-Policy header, before
        that address exists to be named in it (SPEC 2.15.1).
        """
        http_server = ws_server = None
        try:
            http_server = self._serve_http(ip)
            ws_server = self._serve_ws(ip)
        except OSError as err:
            log.warning("[companion] could not bind on %s: %s", ip, err)
            # Not `_shutdown_http`: that server's `serve_forever` never ran,
            # because `_start_http` only starts it once `_serve_ws` has also
            # succeeded. `shutdown()` waits on an event that only a running
            # `serve_forever` sets, so calling it here blocks forever - and
            # since `_open` runs under `self._lock`, that hang used to cost the
            # background thread and now costs `stop()`, i.e. `on_unload`.
            self._close_unstarted_http(http_server)
            return
        self._bound[ip] = (ws_server, http_server)
        try:
            self._start_http(http_server, ip)
        except Exception as err:
            # `threading.Thread.start()` can refuse - `RuntimeError: can't
            # start new thread` under memory pressure, routine on the
            # reference hardware - after both sockets are already bound and
            # `self._bound[ip]` already set. Left as it stood, that entry
            # would hold an HTTPS server whose `serve_forever` never ran, and
            # the next `stop()` would hang in `_shutdown_http` exactly as the
            # `OSError` path above used to (see its comment). Unwind the same
            # way: pop the entry, close the WSS server that did start, and
            # release the HTTPS socket without `shutdown()`.
            #
            # Popping before closing is the opposite of `_close`'s ordering,
            # which is load-bearing there (#67, see its docstring): a request
            # already in flight must still find its address in `self._bound`
            # so the CSP header it receives is not missing the one it arrived
            # on. That does not apply here - this server never served, so no
            # request was ever dispatched on it to render a header - which is
            # exactly why popping first is safe on this path and would not be
            # on `_close`'s.
            log.warning(
                "[companion] could not start the HTTPS serving thread on %s: %s", ip, err
            )
            self._bound.pop(ip, None)
            self._shutdown_ws(ws_server)
            self._close_unstarted_http(http_server)
            return
        log.info(
            "[companion] listening on wss://%s:%s and https://%s:%s",
            ip,
            option(self._options, "ws_port"),
            ip,
            option(self._options, "http_port"),
        )

    def _serve_http(self, ip: str) -> Any:
        """Creates the HTTPS static server. Does not serve yet, and does not
        negotiate TLS yet either.

        The socket is already bound and listening once the constructor below
        returns - connections can queue in the backlog - but nothing is
        accepted until `_start_http` runs the serving thread, which `_open`
        only does after `self._bound[ip]` is set.

        The listening socket itself is never TLS-wrapped. `Server.get_request`
        wraps each accepted connection instead, with `do_handshake_on_connect=
        False`: wrapping there returns immediately - it only builds the SSL
        object, it does not touch the network - so `accept()` stays a plain
        TCP accept and the handshake happens lazily on the connection's first
        read, inside `SimpleHTTPRequestHandler.handle_one_request`, which runs
        on the handler thread `ThreadingMixIn` spawns for that connection, not
        on the `serve_forever` loop (#100).

        The residual is one thread and one file descriptor per silent peer, not
        one in total. `daemon_threads = True` below is what stops
        `server_close()` waiting on a handshake that will never finish - the
        entire point of this change - but the same setting means that thread is
        never joined, by `stop()`, by a reload, or by anything else. It outlives
        all three: a peer that connects and sends nothing keeps its thread and
        its socket alive for the life of the process, and each further silent
        peer adds one more of each. Tracked separately as its own defect: #102.
        """
        import http.server
        import socketserver

        handler = make_http_handler(
            str(option(self._options, "web_root")),
            # Read without `self._lock`: this runs on the serving thread, not
            # the locked reconcile/stop path, and a header a moment stale is
            # harmless (see the note on `self._lock` in `__init__`).
            lambda: sorted(self._bound),
            int(option(self._options, "ws_port")),
        )
        ssl_context = self._ssl

        class Server(http.server.ThreadingHTTPServer):
            # `ThreadingMixIn.block_on_close` stays at its default `True`, so
            # `server_close()` (called by both `_shutdown_http` and
            # `_close_unstarted_http`) joins `self._threads`. That join
            # returns promptly only because `_Threads.append` never records a
            # daemon thread, which is what `daemon_threads = True` buys here -
            # flip it and `server_close()` starts waiting, with no timeout,
            # for every in-flight handler thread to finish on its own. That
            # now includes a thread parked in the lazy TLS handshake described
            # on `_serve_http` above, which is exactly why this still has to
            # hold: `server_close()` must never wait on one of those either.
            daemon_threads = True
            allow_reuse_address = True

            def server_bind(self) -> None:
                # `HTTPServer.server_bind` calls `socket.getfqdn(host)` after
                # binding, a reverse-DNS lookup this class has no use for:
                # `SimpleHTTPRequestHandler` never reads `server_name`, and
                # the CGI machinery that does is not in play here. Over BT PAN
                # the resolver is whatever the iPhone hands out, and with
                # nothing reachable at it the lookup costs the full
                # `resolv.conf` timeout-times-attempts-times-nameservers,
                # inside `self._lock`, once per address `_open` binds. Skip
                # straight to `socketserver`'s bind and fill in the two
                # attributes `HTTPServer`'s protocol handling still reads,
                # without the lookup.
                socketserver.TCPServer.server_bind(self)
                host, port = self.server_address[:2]
                self.server_name = host
                self.server_port = port

            def get_request(self) -> tuple[Any, Any]:
                # Wraps the *accepted* connection, not the listening socket
                # (see `_serve_http`): `do_handshake_on_connect=False` means
                # this only builds the SSL object around `conn`, it does not
                # negotiate anything, so this method - called from the
                # `serve_forever` loop - returns as fast as the plain
                # `accept()` it wraps. `super()`, not the concrete
                # `socketserver.TCPServer`, so this keeps resolving correctly
                # if a mixin ever adds its own `get_request`.
                conn, addr = super().get_request()
                wrapped = ssl_context.wrap_socket(
                    conn, server_side=True, do_handshake_on_connect=False
                )
                return wrapped, addr

            def handle_error(self, request: Any, client_address: Any) -> None:
                # The default prints a full traceback to stderr, unconditionally.
                # `get_request` above moved the TLS handshake onto this handler
                # thread's first read, so a caller who sends plaintext, or breaks
                # the handshake off, or anything else that raises here, reaches
                # this method with no check of its own in the way - this server
                # has none, unlike the token check on the WSS side. The default
                # would let that caller fill the log with tracebacks on demand.
                # `exc_info=True` would do the same through this logger, so only
                # the exception itself is logged, one line, at debug like every
                # other error this class logs - never the frames that produced it.
                #
                # `%r` rather than `%s` is load-bearing, not style: `str()` of an
                # OSError carries the path it failed on, while `repr()` drops it.
                # This log is read, and pasted into issues, by people who are not
                # the only ones who can reach this port.
                log.debug(
                    "[companion:http] error handling request from %s: %r",
                    client_address,
                    sys.exc_info()[1],
                )

        server = Server((ip, int(option(self._options, "http_port"))), handler)
        return server

    @staticmethod
    def _start_http(server: Any, ip: str) -> None:
        """Starts the HTTPS static server's serving thread.

        `threading.Thread.start()` is not guarded here: a failure to start a
        thread is not this server's problem to swallow, and `_open` is the
        caller that knows how to unwind `self._bound[ip]` when it happens.
        """

        def run() -> None:
            try:
                server.serve_forever(poll_interval=0.5)
            except Exception as err:
                log.debug("[companion] http server on %s stopped: %s", ip, err)

        threading.Thread(target=run, daemon=True).start()

    def _serve_ws(self, ip: str) -> Any:
        """Starts the WSS server on the shared asyncio loop."""
        if self._client_handler is None:
            return None
        import asyncio

        loop = self._ensure_loop()
        serve = resolve_ws_serve()
        port = int(option(self._options, "ws_port"))
        handler = self._client_handler
        ssl_context = self._ssl

        async def start() -> Any:
            # serve() must be CALLED on the loop, not merely awaited there:
            # websockets >= 14 reaches for the running loop while constructing
            # the server object. Building it on the caller's thread raises
            # "no running event loop" - and pwnagotchi calls on_loaded from
            # exactly such a thread, so the plugin would die at load.
            return await serve(handler, ip, port, ssl=ssl_context)

        future = asyncio.run_coroutine_threadsafe(start(), loop)
        return future.result(timeout=10)

    def _ensure_loop(self) -> Any:
        """Starts the private asyncio loop on first use."""
        import asyncio

        if self._loop is not None:
            return self._loop
        loop = asyncio.new_event_loop()

        def run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=run, daemon=True, name="companion-ws")
        thread.start()
        self._loop, self._loop_thread = loop, thread
        return loop

    def _close(self, ip: str) -> None:
        """Closes both listeners bound on one address, dropping its clients.

        Stops serving before removing the entry, not after: `self._bound`
        drives `connect-src` (SPEC 2.15.1), and popping first left a window
        where a request already accepted, or one landing before the accept
        loop noticed `shutdown()`, rendered a policy that omitted an address
        its own response was still using. Looked up rather than popped here so
        the entry survives while the listeners are still taking work.

        Not a guarantee for every in-flight response: shutdown() returns while a
        handler may still be running, so a request accepted just before the close
        can render after the pop and omit the address it arrived on. That
        direction over-restricts a client whose listeners are already down, which
        is the harmless one; the reverse ordering got it wrong the other way.
        """
        entry = self._bound.get(ip)
        if entry is None:
            return
        ws_server, http_server = entry
        self._shutdown_http(http_server)
        self._shutdown_ws(ws_server)
        self._bound.pop(ip, None)

    @staticmethod
    def _shutdown_http(server: Any) -> None:
        """Stops a running HTTPS server and releases its socket.

        Only for a server whose serving thread was started: `shutdown()` sets
        an event and waits for `serve_forever`'s poll loop to notice it, and
        that loop is what clears the event once. Called on a server that never
        served, the wait never ends. `_close` is the only caller, and `_open`
        never lets an entry into `self._bound` unless `_start_http` returned
        without raising: the `OSError` path and the thread-start failure path
        both unwind before touching `self._bound[ip]` (or pop it straight back
        out). So every server `_close` reaches went through `_start_http`
        successfully - see `_close_unstarted_http` for the two paths where it
        didn't.
        """
        if server is None:
            return
        try:
            server.shutdown()
            server.server_close()
        except Exception as err:
            log.debug("[companion] http server shutdown failed: %s", err)

    @staticmethod
    def _close_unstarted_http(server: Any) -> None:
        """Releases the socket of an HTTPS server whose serving thread never started.

        Both callers are in `_open`, and neither ever started this server: the
        WSS bind failed after the HTTPS socket was already open, or the
        serving thread would not start. Either way the socket is open and
        `serve_forever` never ran. `server_close()` alone is enough to release
        it; `shutdown()` would wait on a poll loop that will never run and
        never return (see `_shutdown_http`).
        """
        if server is None:
            return
        try:
            server.server_close()
        except Exception as err:
            log.debug("[companion] http server close failed: %s", err)

    def _shutdown_ws(self, server: Any) -> None:
        """Closes a WSS server and waits for it, on the loop that owns it.

        Scheduling close() and moving on is not enough: stop() halts the loop
        immediately afterwards, the callback never runs, and the listening
        socket survives as a ResourceWarning raised from somewhere unrelated.
        """
        if server is None or self._loop is None:
            return
        import asyncio

        async def close() -> None:
            server.close()
            waiter = getattr(server, "wait_closed", None)
            if waiter is not None:
                await waiter()

        try:
            asyncio.run_coroutine_threadsafe(close(), self._loop).result(timeout=5)
        except Exception as err:
            log.debug("[companion] ws server shutdown failed: %r", err)

    def stop(self) -> None:
        """Closes every listener. Idempotent.

        Takes the same lock as `reconcile()`, for the same reason: closing
        `_bound` while a pass is opening it is the double-close and the
        leaked-listener case this whole thing exists to rule out. `_stopped`
        is set before anything is closed so a `reconcile()` unblocked by this
        call sees it and returns without touching `_bound` again.
        """
        with self._lock:
            self._stopped = True
            for ip in list(self._bound):
                self._close(ip)
            loop, thread = self._loop, self._loop_thread
            self._loop, self._loop_thread = None, None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception as err:
            log.debug("[companion] could not stop the ws loop: %s", err)
        if thread is not None:
            thread.join(timeout=2)
        try:
            # Stopping the loop is not closing it; an unclosed loop leaks its
            # selector and surfaces as a ResourceWarning somewhere unrelated.
            loop.close()
        except Exception as err:
            log.debug("[companion] could not close the ws loop: %s", err)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


class ConnectionState:
    """Per-connection authentication state and its deadline.

    Extracted from the connection handler so the rule in SPEC 2.3.3 can be
    driven with a fake clock instead of only through the integration test. An
    unauthenticated peer holding a socket open is exactly the case that needs a
    unit test, because it never happens by accident during manual testing.
    """

    def __init__(self, router: Router, deps: Deps, auth_timeout: float) -> None:
        self._router = router
        self._deps = deps
        # With no token configured there is nothing to authenticate and no
        # deadline to enforce (D5). Note this is a convenience default, not an
        # isolated link: a Personal Hotspot is a shared /28, so anything else on
        # it reaches this socket. SPEC 2.3.4 and docs/SETUP.md say so where the
        # user actually makes the choice.
        self._authenticated = not router.auth_required
        self._deadline = deps.now() + float(auth_timeout)

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def expired(self) -> bool:
        """Whether an unauthenticated connection has outlived its deadline."""
        if self._authenticated:
            return False
        return self._deps.now() > self._deadline

    def observe(self, message: Mapping[str, Any], replies: Sequence[Mapping[str, Any]]) -> None:
        """Marks the connection authenticated after a successful auth exchange."""
        if self._authenticated or message.get("type") != "auth":
            return
        if any(reply.get("type") == "acknowledgment" for reply in replies):
            self._authenticated = True

    @staticmethod
    def rejected(replies: Sequence[Mapping[str, Any]]) -> bool:
        """Whether the replies carry an authorisation failure, which closes the socket."""
        for reply in replies:
            data = reply.get("data") or {}
            if reply.get("type") == "error" and data.get("code") == "unauthorized":
                return True
        return False


class ClientSet:
    """The set of live client sockets, safe to touch from more than one thread.

    The connection handler adds and removes on the asyncio loop, while
    broadcast() iterates from whichever pwnagotchi hook thread fired. That makes
    the membership genuinely shared, so the lock lives with the set rather than
    in the plugin class - which cannot be constructed at all without a
    pwnagotchi, and would put the one piece of concurrency in this file out of
    reach of the tests.
    """

    def __init__(self) -> None:
        self._clients: set[Any] = set()
        self._lock = threading.Lock()

    def add(self, client: Any) -> None:
        with self._lock:
            self._clients.add(client)

    def discard(self, client: Any) -> None:
        with self._lock:
            self._clients.discard(client)

    def snapshot(self) -> list:
        """A copy, so a caller can iterate while the loop mutates the real set."""
        with self._lock:
            return list(self._clients)

    def __len__(self) -> int:
        with self._lock:
            return len(self._clients)


def make_connection_handler(
    router: Router, deps: Deps, auth_timeout: float, clients: ClientSet
) -> Callable[..., Awaitable[None]]:
    """Builds the coroutine that serves one WebSocket connection.

    A factory rather than a method so the protocol side of the transport can be
    driven without constructing the plugin: `Listeners` takes a handler and
    never builds one, and the integration test needs to name the thing in
    between (SPEC 2.3.3).
    """

    async def handler(websocket: Any, *_: Any) -> None:
        # Takes *_ so it works with both websockets handler signatures: the
        # legacy one passes (websocket, path), the modern one passes (websocket)
        # alone (SPEC 2.3.2).
        import asyncio

        state = ConnectionState(router, deps, auth_timeout)
        admitted = False

        async def admit() -> None:
            # Two things happen here and neither may happen earlier.
            #
            # The burst carries the unit's stats, its access points and its
            # face, so sending it before the auth frame would hand all of that
            # to anyone able to open a socket (SPEC 2.4).
            #
            # Joining the client set is the same disclosure by another route:
            # broadcast() fans wifi_update, handshake, peer_detected and
            # status_change out to every member, so a socket registered on
            # accept would receive the same data through the push channel for
            # the whole auth_timeout window, renewable by reconnecting.
            nonlocal admitted
            clients.add(websocket)
            admitted = True
            for message in router.initial_burst():
                await websocket.send(json.dumps(message))

        try:
            if state.authenticated:
                await admit()
            while True:
                if state.authenticated:
                    raw = await websocket.recv()
                else:
                    # A silent peer never reaches the body of this loop, so the
                    # deadline has to be enforced on the wait itself rather than
                    # on the next frame (SPEC 2.3.4).
                    try:
                        raw = await asyncio.wait_for(
                            websocket.recv(), timeout=AUTH_POLL_INTERVAL
                        )
                    except asyncio.TimeoutError:
                        if state.expired():
                            await websocket.close(
                                code=1008, reason="authentication timeout"
                            )
                            return
                        continue
                if state.expired():
                    await websocket.close(code=1008, reason="authentication timeout")
                    return
                try:
                    parsed = json.loads(raw)
                except (TypeError, ValueError):
                    await websocket.send(
                        json.dumps(error_envelope("bad_request", "invalid JSON", deps.now()))
                    )
                    continue
                if not isinstance(parsed, Mapping):
                    # Valid JSON that is not an object cannot carry a `type`, so
                    # it is the same class of failure as a parse error.
                    await websocket.send(
                        json.dumps(error_envelope("bad_request", "not an object", deps.now()))
                    )
                    continue
                replies = router.handle(parsed, state.authenticated)
                state.observe(parsed, replies)
                for reply in replies:
                    await websocket.send(json.dumps(reply))
                if ConnectionState.rejected(replies):
                    await websocket.close(code=1008, reason="unauthorized")
                    return
                if state.authenticated and not admitted:
                    # The acknowledgment for the auth frame has just gone out, so
                    # the client knows it is in before the state arrives.
                    await admit()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            log.debug("[companion] client disconnected: %s", err)
        finally:
            clients.discard(websocket)

    return handler


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
        self.options: dict[str, Any] = dict(DEFAULTS)
        self.deps = Deps()
        self._agent: Any = None
        self._session: SessionCache | None = None
        self._gps: GpsResolver | None = None
        self._battery: BatteryReader | None = None
        self._router: Router | None = None
        self._listeners: Listeners | None = None
        self._clients = ClientSet()
        self._stop = threading.Event()
        self._last_face: tuple[str, str] | None = None

    # -- lifecycle ---------------------------------------------------------

    def on_loaded(self) -> None:
        """Reads configuration and starts the transport, or refuses to start.

        Without a usable certificate this logs an explicit error and starts
        nothing at all.
        """
        self.options = {**DEFAULTS, **(self.options or {})}
        log.info("[companion] %s loading (websockets %s)", __version__, websockets_version())

        context = build_ssl_context(
            str(option(self.options, "tls_cert")), str(option(self.options, "tls_key"))
        )
        if context is None:
            # No plaintext fallback, deliberately. iOS will not install a PWA
            # over http, so a fallback would not serve the app - it would only
            # expose an unencrypted socket while looking like it worked (D4).
            log.error("[companion] no usable TLS material: not starting any listener")
            return

        self._battery = BatteryReader(self.options, self.deps)
        self._session = SessionCache(
            lambda: self._agent, self.deps, float(option(self.options, "session_poll_interval"))
        )
        self._gps = GpsResolver(self.options, self.deps, self._session)
        self._router = Router(
            self.options,
            self.deps,
            lambda: self._agent,
            self._session,
            self._gps,
            self._battery,
            self._handshake_store,
        )
        handler = make_connection_handler(
            self._router,
            self.deps,
            float(option(self.options, "auth_timeout")),
            self._clients,
        )
        self._listeners = Listeners(self.options, self.deps, context, handler)
        self._listeners.reconcile()

    # -- internals ---------------------------------------------------------

    def _handshake_store(self) -> HandshakeStore:
        """A store over the configured capture directory, read fresh each time."""
        directory = ""
        try:
            directory = self._agent._config["bettercap"]["handshakes"]
        except Exception:
            directory = ""
        return HandshakeStore(directory)

    def broadcast(self, message: Mapping[str, Any]) -> None:
        """Sends one message to every connected client. A dead client is dropped."""
        if not message:
            return
        payload = json.dumps(message)
        targets = self._clients.snapshot()
        listeners = self._listeners
        if listeners is None or listeners._loop is None:
            return
        import asyncio

        for client in targets:
            try:
                asyncio.run_coroutine_threadsafe(client.send(payload), listeners._loop)
            except Exception:
                self._clients.discard(client)

    def _background(self) -> None:
        """Refreshes the session and gpsd caches, and reconciles the listeners.

        Everything slow lives here so that no request handler ever blocks on an
        HTTP call to bettercap or a gpsd socket.
        """
        poll = float(option(self.options, "session_poll_interval"))
        rebind = float(option(self.options, "rebind_interval"))
        keepalive = float(option(self.options, "keepalive_interval"))
        next_rebind = 0.0
        # Seeded a full interval out rather than at zero: the first pass runs
        # before any client has had time to connect, so firing immediately would
        # spend a broadcast on an empty set and then leave the first real
        # keepalive an entire interval away.
        next_keepalive = self.deps.now() + keepalive
        # The loop wakes on the session poll, so a keepalive can only go out on
        # a tick, and waking often enough for both keeps the spacing bounded.
        # No client behaviour rests on that bound: the client reconnects on an
        # unanswered ping and reads staleness from the sessionAge a frame
        # carries, never from when a keepalive arrived (SPEC 4.3.1). What a
        # late keepalive costs is lateness, not a false state. This whole
        # computation goes when the broadcasts move to a ticker of their own
        # (SPEC 2.4, issue #65).
        tick = min(poll, keepalive) if keepalive > 0 else poll
        while not self._stop.is_set():
            try:
                if self._session is not None:
                    self._session.refresh()
                if self._gps is not None:
                    self._gps.refresh_gpsd()
                now = self.deps.now()
                if self._listeners is not None and now >= next_rebind:
                    self._listeners.reconcile()
                    next_rebind = now + rebind
                # An idle client cannot otherwise tell a quiet plugin from a
                # dead link: over BT PAN a dropped tether does not close the
                # socket, it just stops delivering, and the app would sit on a
                # dead connection showing stale data (SPEC 2.4).
                if keepalive > 0 and now >= next_keepalive:
                    self.broadcast(envelope("keepalive", {}, now))
                    next_keepalive = now + keepalive
            except Exception:
                log.exception("[companion] background pass failed")
            self._stop.wait(tick)

    def on_ready(self, agent: Any) -> None:
        """Captures the agent and starts the background refresh and rebind threads."""
        self._agent = agent
        if self._session is not None:
            self._session.refresh()
        self.deps.spawn(self._background)

    def on_unload(self, ui: Any = None) -> None:
        """Stops the threads and closes every listener."""
        self._stop.set()
        if self._listeners is not None:
            self._listeners.stop()
            self._listeners = None

    # -- pushes ------------------------------------------------------------

    def on_handshake(
        self, agent: Any, filename: str, access_point: Any, client_station: Any
    ) -> None:
        """Writes the GPS sidecar and pushes the `handshake` message.

        Both argument shapes are accepted: this hook is fired from two paths with
        different types (SPEC F20).
        """
        if self._router is None or self._gps is None:
            return
        try:
            gps = self._gps.current()
            if gps.get("fix"):
                self._handshake_store().write_sidecar(filename, gps, self.deps.now())
        except Exception:
            log.exception("[companion] could not write the GPS sidecar")
        try:
            self.broadcast(
                self._router.on_handshake_message(filename, access_point, client_station)
            )
        except Exception:
            log.exception("[companion] could not push the handshake")

    def on_peer_detected(self, agent: Any, peer: Any) -> None:
        """Pushes `peer_detected`."""
        if self._router is None:
            return
        try:
            self.broadcast(self._router.push("peer_detected", normalise_peer(peer)))
        except Exception:
            log.exception("[companion] could not push peer_detected")

    def on_wifi_update(self, agent: Any, access_points: Iterable[Mapping[str, Any]]) -> None:
        """Pushes `wifi_update`."""
        if self._router is None:
            return
        try:
            mapped = [
                map_access_point(ap)
                for ap in (access_points or [])
                if isinstance(ap, Mapping)
            ]
            self.broadcast(self._router.push("wifi_update", mapped))
        except Exception:
            log.exception("[companion] could not push wifi_update")

    def on_channel_hop(self, agent: Any, channel: int) -> None:
        """Pushes `channel_hop`."""
        if self._router is None:
            return
        try:
            self.broadcast(self._router.push("channel_hop", {"channel": int(channel)}))
        except Exception:
            log.exception("[companion] could not push channel_hop")

    def on_ui_update(self, ui: Any) -> None:
        """Pushes `face_status` only when the face or status text actually changed."""
        if self._router is None:
            return
        try:
            current = self._router.face_status()
        except Exception:
            # Unreachable while face_status() honours its documented contract
            # of never raising (SPEC.md 2.13); this except exists because
            # §2.14 forbids an unhandled exception escaping a hook, not
            # because the call is expected to fail. Reachable in a test
            # through the self._router seam, so it is not dead code.
            log.exception("[companion] could not build face_status")
            return
        signature = (current.get("face", ""), current.get("status", ""))
        if signature == self._last_face:
            # This hook fires on every display refresh. Pushing unchanged text
            # down a Bluetooth link several times a second is pure waste.
            return
        try:
            self.broadcast(self._router.push("face_status", current))
        except Exception:
            # The signature stays as it was, so the next refresh carrying this
            # same face is a retry rather than a suppressed duplicate.
            log.exception("[companion] could not push face_status")
            return
        self._last_face = signature

    def _push_mood(self, mood: str) -> None:
        """Pushes a status_change for one of the mood hooks."""
        if self._router is None:
            return
        try:
            status = self._router.face_status().get("status", "")
            self.broadcast(self._router.push("status_change", {"status": status, "mood": mood}))
        except Exception:
            log.exception("[companion] could not push status_change")

    def on_bored(self, agent: Any) -> None:
        """Pushes `status_change` with mood "bored"."""
        self._push_mood("bored")

    def on_excited(self, agent: Any) -> None:
        """Pushes `status_change` with mood "excited"."""
        self._push_mood("excited")

    def on_lonely(self, agent: Any) -> None:
        """Pushes `status_change` with mood "lonely"."""
        self._push_mood("lonely")

    def on_sad(self, agent: Any) -> None:
        """Pushes `status_change` with mood "sad"."""
        self._push_mood("sad")
