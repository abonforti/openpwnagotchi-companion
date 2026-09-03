// GENERATED FILE - DO NOT EDIT.
//
// Source of truth: docs/schemas/*.json (SPEC.md D15).
// Regenerate with: node tools/gen-protocol-types.mjs
// CI fails if this file is out of sync with the schemas.

// ---------------------------------------------------------------------------
// Shared shapes (docs/schemas/common.json)
// ---------------------------------------------------------------------------

/**
 * One access point, mapped from a bettercap AP dict held in agent._access_points. The bettercap key is 'mac'; there is no 'bssid' key at the source.
 */
export interface AccessPoint {
  /** From ap['mac']. */
  "bssid": string;
  /** From ap['hostname'], falling back to the MAC when empty. */
  "hostname": string;
  "channel": number;
  "rssi": number;
  "encryption": string;
  "vendor": string;
  /** len(ap.get('clients') or []). The key may be absent or null. */
  "clients": number;
}

/**
 * Normalised reading of a .gps.json sidecar. The sidecar itself is stored in bettercap shape with capitalised keys; this is the wire form only.
 */
export interface HandshakeGps {
  "lat": number;
  "lon": number;
  "accuracy": number | null;
  "source": GpsSource;
}

/**
 * One capture file found in config['bettercap']['handshakes'].
 */
export interface HandshakeEntry {
  /** Basename including the extension. */
  "filename": string;
  /** Parsed from the filename by splitting on the LAST underscore. Falls back to the whole basename when the tail is not a 12-hex-digit BSSID. */
  "ssid": string;
  /** Null when the filename does not parse. Never guessed. */
  "bssid": string | null;
  /** Epoch seconds. */
  "mtime": number;
  /** Bytes. */
  "size": number;
  /** Null when no sidecar exists or the sidecar is malformed. */
  "gps": HandshakeGps | null;
}

/**
 * The newest entry of the handshake directory, or null when the directory is empty.
 */
export interface LastHandshake {
  "filename": string;
  "ssid": string;
  "bssid": string | null;
  "mtime": number;
}

/**
 * One pwngrid peer, mapped from a pwnagotchi.mesh.peer.Peer object held in agent._peers. Peer members are a mix of attributes and methods; see SPEC.md 2.8 for the exact mapping. All timestamps are normalised to epoch seconds on the wire even though Peer stores first_seen/prev_seen/first_met as datetime and last_seen as a float.
 */
export interface Peer {
  /** peer.name(), defaults to '???'. */
  "name": string;
  /** peer.identity(), defaults to '???'. */
  "fingerprint": string;
  /** peer.full_name(), 'name@identity'. */
  "fullName": string;
  "rssi": number | null;
  /** peer.last_channel, NOT peer.channel. */
  "channel": number | null;
  "firstSeen": number | null;
  "prevSeen": number | null;
  "firstMet": number | null;
  /** peer.last_seen is already an epoch float; do not convert it again. */
  "lastSeen": number | null;
  "encounters": number | null;
  /** peer.pwnd_run(). */
  "pwndRun": number | null;
  /** peer.pwnd_total(), NOT pwnd_tot. */
  "pwndTotal": number | null;
  "version": string | null;
  "uptime": number | null;
  /** Text face, never an image. */
  "face": string | null;
}

/**
 * Which provider produced the reading. 'bettercap' and 'gpsd' are both on-Pi sources; the browser fallback must never overwrite either.
 */
export type GpsSource = "bettercap" | "gpsd" | "browser" | null;

/**
 * Uniform GPS shape. When no source is available every coordinate field is null and enabled is false; there is no alternative empty form.
 */
export interface Gps {
  /** False when gps_source is 'none' or no provider answered. */
  "enabled": boolean;
  "source": GpsSource;
  /** True when an on-Pi source (bettercap or gpsd) currently has a fix. The PWA must not push browser coordinates while this is true. */
  "piFix": boolean;
  "fix": boolean;
  "lat": number | null;
  "lon": number | null;
  "altitude": number | null;
  /** Metres. Derived from HDOP for bettercap, from the browser's own accuracy otherwise. */
  "accuracy": number | null;
  /** Epoch seconds of the reading. */
  "updated": number | null;
}

/**
 * Never fails; every field is null when no provider is available.
 */
export interface Battery {
  "percent": number | null;
  "charging": boolean | null;
}

/**
 * What this plugin instance can actually do. The PWA gates its controls on these and must not infer availability any other way.
 */
export interface Capabilities {
  /** 'pasv_mode' in plugins.loaded. */
  "pasv": boolean;
  /** A battery provider answered at least once. */
  "pisugar": boolean;
  /** The configured gps_source value, not the currently active source. */
  "gpsSource": "auto" | "bettercap" | "gpsd" | "none";
  "pluginVersion": string;
}

/**
 * PASV is reported when agent.mode == 'auto' and the pasv_mode plugin reports passive. It is a display state, not a separate pwnagotchi mode.
 */
export type Mode = "AUTO" | "PASV" | "MANUAL";

export interface Stats {
  /** pwnagotchi.uptime(), seconds. */
  "uptime": number;
  /** pwnagotchi.name(), the unit's own hostname (F33). Null when the accessor raised or returned an empty string; never an empty string. */
  "name": string | null;
  /** Null when there is no agent yet, which is what a unit in manual mode gives the plugin (on_ready never fires). Not a guess at the unit's mode. */
  "mode": Mode | null;
  /** agent._current_channel. Never read from agent.session(). */
  "channel": number | null;
  "battery": Battery;
  /** pwnagotchi.temperature(), celsius. */
  "temperature": number | null;
  /** Count of *.pcapng in the handshake directory. Matches the number shown on the device display, which uses utils.total_unique_handshakes. Null means the handshake directory is unknown, not that there are no captures; null together with handshakesTotal. */
  "handshakes": number | null;
  /** Count of *.pcapng plus *.pcap. Always >= handshakes. Null means the handshake directory is unknown, not that there are no captures; null together with handshakes. */
  "handshakesTotal": number | null;
  /** len(agent._peers). */
  "peers": number;
  /** len(agent._access_points). */
  "accessPoints": number;
  "lastHandshake": LastHandshake | null;
  "lastPeer": Peer | null;
  "gps": Gps;
  /** Seconds since the cached bettercap session snapshot was refreshed. Null when it has never succeeded. The client shows stale data as degraded rather than wrong. */
  "sessionAge": number | null;
  "capabilities": Capabilities;
}

/**
 * Faces are unicode text on a stock setup. No PNG face is ever transported.
 */
export interface FaceStatus {
  "face": string;
  "status": string;
  /** Null when there is no agent yet, which is what a unit in manual mode gives the plugin (on_ready never fires). Not a guess at the unit's mode. */
  "mode": Mode | null;
}

export type ErrorCode = "bad_request" | "unknown_command" | "unauthorized" | "pasv_unavailable" | "pasv_requires_auto" | "no_frame" | "log_unavailable" | "not_supported" | "internal_error";

export type RestartReason = "mode_change" | "reboot" | "shutdown";

// ---------------------------------------------------------------------------
// Incoming messages (docs/schemas/incoming/)
// ---------------------------------------------------------------------------

/**
 * Must be the first frame when the plugin is configured with a non-empty token. The token is compared with hmac.compare_digest. A connection that has not authenticated within auth_timeout seconds is closed with code 1008.
 */
export interface IncomingAuth {
  "type": "auth";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
  "token": string;
}

/**
 * Replies with an `access_points` message. The plugin reads agent._access_points; it never calls agent.get_access_points(), which has side effects.
 */
export interface IncomingGetAccessPoints {
  "type": "get_access_points";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `face_status` message. The face is unicode text.
 */
export interface IncomingGetFaceStatus {
  "type": "get_face_status";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `gps_update` message.
 */
export interface IncomingGetGpsData {
  "type": "get_gps_data";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `handshakes_list` message built from the handshake directory.
 */
export interface IncomingGetHandshakes {
  "type": "get_handshakes";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `log_lines` message containing a bounded tail of the pwnagotchi log.
 */
export interface IncomingGetLog {
  "type": "get_log";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
  /** Defaults to 200, capped at 1000. */
  "lines"?: number;
}

/**
 * Replies with a `peers_list` message.
 */
export interface IncomingGetPeers {
  "type": "get_peers";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `screen_image` message. On demand only; the client drives any auto-refresh by polling. Replies `error` with code `no_frame` when the frame file does not exist yet.
 */
export interface IncomingGetScreen {
  "type": "get_screen";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Replies with a `stats` message.
 */
export interface IncomingGetStats {
  "type": "get_stats";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * The client pushes coordinates from the browser Geolocation API. The plugin stores them as the lowest-priority GPS source and drops them after 10 seconds of staleness. The client must NOT send this while stats.gps.piFix is true.
 */
export interface IncomingGpsData {
  "type": "gps_data";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
  "latitude": number;
  "longitude": number;
  /** Metres, as reported by the browser. */
  "accuracy"?: number | null;
}

/**
 * Replies with a `pong` message.
 */
export interface IncomingPing {
  "type": "ping";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Broadcasts `restarting` with reason `reboot`, then calls pwnagotchi.reboot(). Gated behind a two-step confirm in the UI; the plugin executes immediately.
 */
export interface IncomingReboot {
  "type": "reboot";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * DISRUPTIVE. Switching mode restarts the pwnagotchi and bettercap services via pwnagotchi.restart(), which kills this plugin and drops the connection. The plugin acknowledges, broadcasts `restarting`, then restarts. A request for the mode already in effect is acknowledged and ignored.
 */
export interface IncomingSetMode {
  "type": "set_mode";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
  "mode": "auto" | "manual";
}

/**
 * Soft dependency on the pasv_mode plugin. Replies `error` with code `pasv_unavailable` when the plugin is not loaded, or `pasv_requires_auto` when agent.mode is not 'auto'. Otherwise emits plugins.on('pasv_on'|'pasv_off'), which is asynchronous: the new state is only visible in the next `stats`.
 */
export interface IncomingSetPasv {
  "type": "set_pasv";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
  "on": boolean;
}

/**
 * Broadcasts `restarting` with reason `shutdown`, then calls pwnagotchi.shutdown(). Gated behind a two-step confirm in the UI; the plugin executes immediately.
 */
export interface IncomingShutdown {
  "type": "shutdown";
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

export type IncomingMessage =
  | IncomingAuth
  | IncomingGetAccessPoints
  | IncomingGetFaceStatus
  | IncomingGetGpsData
  | IncomingGetHandshakes
  | IncomingGetLog
  | IncomingGetPeers
  | IncomingGetScreen
  | IncomingGetStats
  | IncomingGpsData
  | IncomingPing
  | IncomingReboot
  | IncomingSetMode
  | IncomingSetPasv
  | IncomingShutdown;

export const INCOMING_TYPES = [
  "auth",
  "get_access_points",
  "get_face_status",
  "get_gps_data",
  "get_handshakes",
  "get_log",
  "get_peers",
  "get_screen",
  "get_stats",
  "gps_data",
  "ping",
  "reboot",
  "set_mode",
  "set_pasv",
  "shutdown",
] as const;

export type IncomingMessageType = (typeof INCOMING_TYPES)[number];

// ---------------------------------------------------------------------------
// Outgoing messages (docs/schemas/outgoing/)
// ---------------------------------------------------------------------------

/**
 * Sent as a reply to `get_access_points`.
 */
export interface OutgoingAccessPoints {
  "type": "access_points";
  "data": AccessPoint[];
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent for any incoming message that carried a message_id, and always for a successful auth. Acknowledgment means the command was accepted, not that its effect is already visible: plugins.on() dispatch is asynchronous and service restarts take time.
 */
export interface OutgoingAcknowledgment {
  "type": "acknowledgment";
  "data": {
    /** The `type` of the message being acknowledged. */
    "acknowledged": string;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id, echoed from the request. Present whenever the request carried one. Absent only on the acknowledgment for a successful auth frame that carried none, which is generated because it is the client's only signal that the connection is usable. */
  "message_id"?: string;
}

/**
 * Pushed from on_channel_hop.
 */
export interface OutgoingChannelHop {
  "type": "channel_hop";
  "data": {
    "channel": number;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Never fatal to the connection unless the code is `unauthorized`.
 */
export interface OutgoingError {
  "type": "error";
  "data": {
    "code": ErrorCode;
    /** Human-readable detail. Never leaks a token or a filesystem path outside web_root. */
    "message": string;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_face_status` and pushed from on_ui_update when the face or status text changes.
 */
export interface OutgoingFaceStatus {
  "type": "face_status";
  "data": FaceStatus;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_gps_data` and pushed when the resolved position changes.
 */
export interface OutgoingGpsUpdate {
  "type": "gps_update";
  "data": Gps;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Pushed from on_handshake. That hook is fired from two code paths with different argument types (MAC strings or bettercap dicts); both are normalised here.
 */
export interface OutgoingHandshake {
  "type": "handshake";
  "data": {
    "filename": string;
    /** Access point MAC. */
    "ap": string | null;
    /** Client station MAC. */
    "station": string | null;
    /** Present when a fix was available and a sidecar was written. */
    "gps": HandshakeGps | null;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_handshakes`. Capped at 500 entries, newest first.
 */
export interface OutgoingHandshakesList {
  "type": "handshakes_list";
  "data": {
    "entries": HandshakeEntry[];
    /** True when the 500-entry cap was hit. Entries are never dropped silently. */
    "truncated": boolean;
    /** Number of capture files found before the cap was applied. Null means the handshake directory is unknown (neither `handshake_dir` nor the agent yielded one), not that it was found empty; `entries` stays an empty array in both cases. */
    "total": number | null;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Pushed periodically so an idle client can distinguish a quiet plugin from a dead link.
 */
export interface OutgoingKeepalive {
  "type": "keepalive";
  /** Carries no payload beyond the envelope. */
  "data": Record<string, never>;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_log`. Oldest line first.
 */
export interface OutgoingLogLines {
  "type": "log_lines";
  "data": {
    "lines": string[];
    /** The log path actually read: the plugin's `log_path` when it is set, otherwise config['main']['log']['path']. */
    "path": string;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Pushed from on_peer_detected.
 */
export interface OutgoingPeerDetected {
  "type": "peer_detected";
  "data": Peer;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_peers`. Built from agent._peers only; pwnagotchi.grid.peers() is not used because it returns a different shape.
 */
export interface OutgoingPeersList {
  "type": "peers_list";
  "data": {
    "entries": Peer[];
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent in reply to `ping`.
 */
export interface OutgoingPong {
  "type": "pong";
  /** Carries no payload beyond the envelope. */
  "data": Record<string, never>;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Broadcast immediately before pwnagotchi.restart(), pwnagotchi.reboot() or pwnagotchi.shutdown(). The client must treat the following disconnect as expected: show the reason, suppress the error banner, and reconnect with backoff.
 */
export interface OutgoingRestarting {
  "type": "restarting";
  "data": {
    "reason": RestartReason;
    /** The mode being restarted into. Non-null only when reason is mode_change. */
    "mode": Mode | null;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_screen`. Read under pwnagotchi.ui.web.frame_lock from pwnagotchi.ui.web.frame_path. Heavy over Bluetooth: on demand only.
 */
export interface OutgoingScreenImage {
  "type": "screen_image";
  "data": {
    /** Base64-encoded PNG, no data: prefix. */
    "png": string;
    /** Epoch seconds of the frame file. */
    "mtime": number;
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Sent as a reply to `get_stats` and broadcast periodically.
 */
export interface OutgoingStats {
  "type": "stats";
  "data": Stats;
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Pushed from the mood hooks on_bored, on_excited, on_lonely and on_sad.
 */
export interface OutgoingStatusChange {
  "type": "status_change";
  "data": {
    "status": string;
    "mood": "bored" | "excited" | "lonely" | "sad";
  };
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

/**
 * Pushed from on_wifi_update with the same mapping as `access_points`.
 */
export interface OutgoingWifiUpdate {
  "type": "wifi_update";
  "data": AccessPoint[];
  /** Epoch seconds at which the plugin produced the message. */
  "timestamp": number;
  /** Client-generated correlation id. When present the plugin echoes it in the reply and in the acknowledgment. */
  "message_id"?: string;
}

export type OutgoingMessage =
  | OutgoingAccessPoints
  | OutgoingAcknowledgment
  | OutgoingChannelHop
  | OutgoingError
  | OutgoingFaceStatus
  | OutgoingGpsUpdate
  | OutgoingHandshake
  | OutgoingHandshakesList
  | OutgoingKeepalive
  | OutgoingLogLines
  | OutgoingPeerDetected
  | OutgoingPeersList
  | OutgoingPong
  | OutgoingRestarting
  | OutgoingScreenImage
  | OutgoingStats
  | OutgoingStatusChange
  | OutgoingWifiUpdate;

export const OUTGOING_TYPES = [
  "access_points",
  "acknowledgment",
  "channel_hop",
  "error",
  "face_status",
  "gps_update",
  "handshake",
  "handshakes_list",
  "keepalive",
  "log_lines",
  "peer_detected",
  "peers_list",
  "pong",
  "restarting",
  "screen_image",
  "stats",
  "status_change",
  "wifi_update",
] as const;

export type OutgoingMessageType = (typeof OUTGOING_TYPES)[number];

// ---------------------------------------------------------------------------
// Runtime guards for outgoing messages (SPEC.md 4.3.11, issue #109)
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isAccessPoint(v: unknown): v is AccessPoint {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["bssid"] === 'string' && typeof (v as Record<string, unknown>)["hostname"] === 'string' && typeof (v as Record<string, unknown>)["channel"] === 'number' && typeof (v as Record<string, unknown>)["rssi"] === 'number' && typeof (v as Record<string, unknown>)["encryption"] === 'string' && typeof (v as Record<string, unknown>)["vendor"] === 'string' && typeof (v as Record<string, unknown>)["clients"] === 'number');
}

export function isErrorCode(v: unknown): v is ErrorCode {
  return (v === "bad_request" || v === "unknown_command" || v === "unauthorized" || v === "pasv_unavailable" || v === "pasv_requires_auto" || v === "no_frame" || v === "log_unavailable" || v === "not_supported" || v === "internal_error");
}

export function isFaceStatus(v: unknown): v is FaceStatus {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["face"] === 'string' && typeof (v as Record<string, unknown>)["status"] === 'string' && (isMode((v as Record<string, unknown>)["mode"]) || (v as Record<string, unknown>)["mode"] === null));
}

export function isMode(v: unknown): v is Mode {
  return (v === "AUTO" || v === "PASV" || v === "MANUAL");
}

export function isGps(v: unknown): v is Gps {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["enabled"] === 'boolean' && isGpsSource((v as Record<string, unknown>)["source"]) && typeof (v as Record<string, unknown>)["piFix"] === 'boolean' && typeof (v as Record<string, unknown>)["fix"] === 'boolean' && (typeof (v as Record<string, unknown>)["lat"] === 'number' || (v as Record<string, unknown>)["lat"] === null) && (typeof (v as Record<string, unknown>)["lon"] === 'number' || (v as Record<string, unknown>)["lon"] === null) && (typeof (v as Record<string, unknown>)["altitude"] === 'number' || (v as Record<string, unknown>)["altitude"] === null) && (typeof (v as Record<string, unknown>)["accuracy"] === 'number' || (v as Record<string, unknown>)["accuracy"] === null) && (typeof (v as Record<string, unknown>)["updated"] === 'number' || (v as Record<string, unknown>)["updated"] === null));
}

export function isGpsSource(v: unknown): v is GpsSource {
  return (v === "bettercap" || v === "gpsd" || v === "browser" || v === null);
}

export function isHandshakeGps(v: unknown): v is HandshakeGps {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["lat"] === 'number' && typeof (v as Record<string, unknown>)["lon"] === 'number' && (typeof (v as Record<string, unknown>)["accuracy"] === 'number' || (v as Record<string, unknown>)["accuracy"] === null) && isGpsSource((v as Record<string, unknown>)["source"]));
}

export function isHandshakeEntry(v: unknown): v is HandshakeEntry {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["filename"] === 'string' && typeof (v as Record<string, unknown>)["ssid"] === 'string' && (typeof (v as Record<string, unknown>)["bssid"] === 'string' || (v as Record<string, unknown>)["bssid"] === null) && typeof (v as Record<string, unknown>)["mtime"] === 'number' && typeof (v as Record<string, unknown>)["size"] === 'number' && (isHandshakeGps((v as Record<string, unknown>)["gps"]) || (v as Record<string, unknown>)["gps"] === null));
}

export function isPeer(v: unknown): v is Peer {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["name"] === 'string' && typeof (v as Record<string, unknown>)["fingerprint"] === 'string' && typeof (v as Record<string, unknown>)["fullName"] === 'string' && (typeof (v as Record<string, unknown>)["rssi"] === 'number' || (v as Record<string, unknown>)["rssi"] === null) && (typeof (v as Record<string, unknown>)["channel"] === 'number' || (v as Record<string, unknown>)["channel"] === null) && (typeof (v as Record<string, unknown>)["firstSeen"] === 'number' || (v as Record<string, unknown>)["firstSeen"] === null) && (typeof (v as Record<string, unknown>)["prevSeen"] === 'number' || (v as Record<string, unknown>)["prevSeen"] === null) && (typeof (v as Record<string, unknown>)["firstMet"] === 'number' || (v as Record<string, unknown>)["firstMet"] === null) && (typeof (v as Record<string, unknown>)["lastSeen"] === 'number' || (v as Record<string, unknown>)["lastSeen"] === null) && (typeof (v as Record<string, unknown>)["encounters"] === 'number' || (v as Record<string, unknown>)["encounters"] === null) && (typeof (v as Record<string, unknown>)["pwndRun"] === 'number' || (v as Record<string, unknown>)["pwndRun"] === null) && (typeof (v as Record<string, unknown>)["pwndTotal"] === 'number' || (v as Record<string, unknown>)["pwndTotal"] === null) && (typeof (v as Record<string, unknown>)["version"] === 'string' || (v as Record<string, unknown>)["version"] === null) && (typeof (v as Record<string, unknown>)["uptime"] === 'number' || (v as Record<string, unknown>)["uptime"] === null) && (typeof (v as Record<string, unknown>)["face"] === 'string' || (v as Record<string, unknown>)["face"] === null));
}

export function isRestartReason(v: unknown): v is RestartReason {
  return (v === "mode_change" || v === "reboot" || v === "shutdown");
}

export function isStats(v: unknown): v is Stats {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["uptime"] === 'number' && (typeof (v as Record<string, unknown>)["name"] === 'string' || (v as Record<string, unknown>)["name"] === null) && (isMode((v as Record<string, unknown>)["mode"]) || (v as Record<string, unknown>)["mode"] === null) && (typeof (v as Record<string, unknown>)["channel"] === 'number' || (v as Record<string, unknown>)["channel"] === null) && isBattery((v as Record<string, unknown>)["battery"]) && (typeof (v as Record<string, unknown>)["temperature"] === 'number' || (v as Record<string, unknown>)["temperature"] === null) && (typeof (v as Record<string, unknown>)["handshakes"] === 'number' || (v as Record<string, unknown>)["handshakes"] === null) && (typeof (v as Record<string, unknown>)["handshakesTotal"] === 'number' || (v as Record<string, unknown>)["handshakesTotal"] === null) && typeof (v as Record<string, unknown>)["peers"] === 'number' && typeof (v as Record<string, unknown>)["accessPoints"] === 'number' && (isLastHandshake((v as Record<string, unknown>)["lastHandshake"]) || (v as Record<string, unknown>)["lastHandshake"] === null) && (isPeer((v as Record<string, unknown>)["lastPeer"]) || (v as Record<string, unknown>)["lastPeer"] === null) && isGps((v as Record<string, unknown>)["gps"]) && (typeof (v as Record<string, unknown>)["sessionAge"] === 'number' || (v as Record<string, unknown>)["sessionAge"] === null) && isCapabilities((v as Record<string, unknown>)["capabilities"]));
}

export function isBattery(v: unknown): v is Battery {
  return (isRecord(v) && (typeof (v as Record<string, unknown>)["percent"] === 'number' || (v as Record<string, unknown>)["percent"] === null) && (typeof (v as Record<string, unknown>)["charging"] === 'boolean' || (v as Record<string, unknown>)["charging"] === null));
}

export function isLastHandshake(v: unknown): v is LastHandshake {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["filename"] === 'string' && typeof (v as Record<string, unknown>)["ssid"] === 'string' && (typeof (v as Record<string, unknown>)["bssid"] === 'string' || (v as Record<string, unknown>)["bssid"] === null) && typeof (v as Record<string, unknown>)["mtime"] === 'number');
}

export function isCapabilities(v: unknown): v is Capabilities {
  return (isRecord(v) && typeof (v as Record<string, unknown>)["pasv"] === 'boolean' && typeof (v as Record<string, unknown>)["pisugar"] === 'boolean' && ((v as Record<string, unknown>)["gpsSource"] === "auto" || (v as Record<string, unknown>)["gpsSource"] === "bettercap" || (v as Record<string, unknown>)["gpsSource"] === "gpsd" || (v as Record<string, unknown>)["gpsSource"] === "none") && typeof (v as Record<string, unknown>)["pluginVersion"] === 'string');
}

export function isOutgoingAccessPoints(v: unknown): v is OutgoingAccessPoints {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "access_points" && (Array.isArray((v as Record<string, unknown>)["data"]) && ((v as Record<string, unknown>)["data"] as unknown[]).every((item0: unknown) => isAccessPoint(item0))) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingAcknowledgment(v: unknown): v is OutgoingAcknowledgment {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "acknowledgment" && (isRecord((v as Record<string, unknown>)["data"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["acknowledged"] === 'string') && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingChannelHop(v: unknown): v is OutgoingChannelHop {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "channel_hop" && (isRecord((v as Record<string, unknown>)["data"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["channel"] === 'number') && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingError(v: unknown): v is OutgoingError {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "error" && (isRecord((v as Record<string, unknown>)["data"]) && isErrorCode(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["code"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["message"] === 'string') && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingFaceStatus(v: unknown): v is OutgoingFaceStatus {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "face_status" && isFaceStatus((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingGpsUpdate(v: unknown): v is OutgoingGpsUpdate {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "gps_update" && isGps((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingHandshake(v: unknown): v is OutgoingHandshake {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "handshake" && (isRecord((v as Record<string, unknown>)["data"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["filename"] === 'string' && (typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["ap"] === 'string' || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["ap"] === null) && (typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["station"] === 'string' || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["station"] === null) && (isHandshakeGps(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["gps"]) || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["gps"] === null)) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingHandshakesList(v: unknown): v is OutgoingHandshakesList {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "handshakes_list" && (isRecord((v as Record<string, unknown>)["data"]) && (Array.isArray(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["entries"]) && (((v as Record<string, unknown>)["data"] as Record<string, unknown>)["entries"] as unknown[]).every((item0: unknown) => isHandshakeEntry(item0))) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["truncated"] === 'boolean' && (typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["total"] === 'number' || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["total"] === null)) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingKeepalive(v: unknown): v is OutgoingKeepalive {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "keepalive" && isRecord((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingLogLines(v: unknown): v is OutgoingLogLines {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "log_lines" && (isRecord((v as Record<string, unknown>)["data"]) && (Array.isArray(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["lines"]) && (((v as Record<string, unknown>)["data"] as Record<string, unknown>)["lines"] as unknown[]).every((item0: unknown) => typeof item0 === 'string')) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["path"] === 'string') && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingPeerDetected(v: unknown): v is OutgoingPeerDetected {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "peer_detected" && isPeer((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingPeersList(v: unknown): v is OutgoingPeersList {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "peers_list" && (isRecord((v as Record<string, unknown>)["data"]) && (Array.isArray(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["entries"]) && (((v as Record<string, unknown>)["data"] as Record<string, unknown>)["entries"] as unknown[]).every((item0: unknown) => isPeer(item0)))) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingPong(v: unknown): v is OutgoingPong {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "pong" && isRecord((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingRestarting(v: unknown): v is OutgoingRestarting {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "restarting" && (isRecord((v as Record<string, unknown>)["data"]) && isRestartReason(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["reason"]) && (isMode(((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mode"]) || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mode"] === null)) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingScreenImage(v: unknown): v is OutgoingScreenImage {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "screen_image" && (isRecord((v as Record<string, unknown>)["data"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["png"] === 'string' && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mtime"] === 'number') && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingStats(v: unknown): v is OutgoingStats {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "stats" && isStats((v as Record<string, unknown>)["data"]) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingStatusChange(v: unknown): v is OutgoingStatusChange {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "status_change" && (isRecord((v as Record<string, unknown>)["data"]) && typeof ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["status"] === 'string' && (((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mood"] === "bored" || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mood"] === "excited" || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mood"] === "lonely" || ((v as Record<string, unknown>)["data"] as Record<string, unknown>)["mood"] === "sad")) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingWifiUpdate(v: unknown): v is OutgoingWifiUpdate {
  return (isRecord(v) && (v as Record<string, unknown>)["type"] === "wifi_update" && (Array.isArray((v as Record<string, unknown>)["data"]) && ((v as Record<string, unknown>)["data"] as unknown[]).every((item0: unknown) => isAccessPoint(item0))) && typeof (v as Record<string, unknown>)["timestamp"] === 'number' && ((v as Record<string, unknown>)["message_id"] === undefined || typeof (v as Record<string, unknown>)["message_id"] === 'string'));
}

export function isOutgoingMessage(type: string, v: unknown): v is OutgoingMessage {
  switch (type) {
    case "access_points": return isOutgoingAccessPoints(v);
    case "acknowledgment": return isOutgoingAcknowledgment(v);
    case "channel_hop": return isOutgoingChannelHop(v);
    case "error": return isOutgoingError(v);
    case "face_status": return isOutgoingFaceStatus(v);
    case "gps_update": return isOutgoingGpsUpdate(v);
    case "handshake": return isOutgoingHandshake(v);
    case "handshakes_list": return isOutgoingHandshakesList(v);
    case "keepalive": return isOutgoingKeepalive(v);
    case "log_lines": return isOutgoingLogLines(v);
    case "peer_detected": return isOutgoingPeerDetected(v);
    case "peers_list": return isOutgoingPeersList(v);
    case "pong": return isOutgoingPong(v);
    case "restarting": return isOutgoingRestarting(v);
    case "screen_image": return isOutgoingScreenImage(v);
    case "stats": return isOutgoingStats(v);
    case "status_change": return isOutgoingStatusChange(v);
    case "wifi_update": return isOutgoingWifiUpdate(v);
    default:
      return false;
  }
}
