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
