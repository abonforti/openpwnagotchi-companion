# openpwnagotchi-companion — Build Specification

Version 2. Supersedes v1. Every API claim in this document has been verified against
`jayofelony/pwnagotchi` tag `v2.9.5.6`; the evidence is in §11 (Pinned Facts). Claims that
could not be verified are marked **UNVERIFIED** and carry an explicit instruction on what to
do instead of guessing.

A free, self-hostable PWA companion for pwnagotchi (jayofelony fork), replacing the paid
iOS app "Pwnagotchi Companion" (`BraedenP232/PwnIOS`). Backend is a hardened fork of that
project's GPL plugin `pwnios.py`; frontend is an installable PWA the user builds and serves
from their own Pi.

This document is the complete build brief. It is written to be executed by implementer agents
with no further design decisions required.

### How to use this document (implementer agents, read first)

1. Decisions in §0 are locked. Do not re-litigate them.
2. Every pwnagotchi attribute, method, config key and event name you may use is listed in §11.
   **If a symbol is not in §11, you may not use it.** Do not infer it from another plugin, from
   upstream `pwnios.py`, from an older pwnagotchi release, or from memory. If you believe §11
   is missing something you need, stop and report it instead of inventing a name.
3. The wire format is defined by the JSON Schemas in `docs/schemas/`, not by prose. Prose in
   §2.4 is a summary; the schema wins on any disagreement.
4. Every unit of work ships with its tests (§10). A task is not done until its tests pass.
5. **The entire repository is written in English.** Code, comments, docstrings, commit
   messages, documentation, issue titles, log strings. No exceptions.

---

## 0. Decisions Ledger (locked, do not change)

| # | Decision | Value |
|---|---|---|
| D1 | Backend | Fork of `BraedenP232/PwnIOS` `pwnios.py`, hardened. GPL-3.0, attribution preserved. |
| D2 | Frontend stack | Svelte + TypeScript + Vite + `vite-plugin-pwa` (Workbox). Map: Leaflet + OSM tiles. |
| D3 | Repo name | `openpwnagotchi-companion`, public. |
| D4 | Transport security | Private CA. Server cert with `iPAddress` SAN, `basicConstraints=critical,CA:TRUE` on the CA, server validity ≤ 825 days. HTTPS static on 8443, WSS on 8082. No plaintext fallback. |
| D5 | Socket auth | Bind only to configured tether addresses, never `0.0.0.0`. Optional shared token, disabled by default. |
| D5.1 | Binding identifier | Supersedes the interface-name form of D5 (`interfaces = ["bnep0", "usb0"]`). Listeners are chosen by `bind_addresses`, a list of IPv4 addresses or CIDR blocks (§2.3.1). An interface name identifies a device, not a network: `bnep` devices are numbered by the order PAN links come up, so `bnep0` is whichever Bluetooth peer connected first, which on a unit paired with more than one machine is not the one the owner meant. With `token` empty by default, that put an unauthenticated socket on a link nobody chose. |
| D6 | Delivery | GitHub Actions builds the PWA into `dist.tgz`, attached to a Release. An install script pulls it onto the Pi; the plugin serves it over HTTPS. Each user runs their own; nothing centrally hosted. |
| D7 | Config | All IPs and ports configurable in the PWA settings screen. BT PAN IP prefilled (`172.20.10.7`). USB IP (`10.0.0.2`) supported as an extra configurable host. |
| D8 | v1 features | Full parity with the paid app + three gaps it lacks: detailed handshake list, peer/mesh view, live log viewer. Plus full GPS (Pi-source detection + browser fallback) and wardriving map, and full e-ink screen mirror (on-demand + slow auto ~5 s). |
| D9 | Controls | AUTO/MANU switch, PASV toggle (soft dependency on `pasv_mode.py`), reboot, shutdown. Destructive actions require a two-step confirm in the UI. |
| D10 | Web push | Dropped entirely, not even roadmap. Event notifications are in-app only, while the app is open. |
| D11 | Face rendering | Faces are text (unicode). The PWA renders the text face + status natively. No PNG face transport. |
| **D12** | **Mode switching** | AUTO/MANU is switched with `pwnagotchi.restart("AUTO"\|"MANU")`, **not** by assigning `agent.mode`. This restarts the pwnagotchi service and therefore drops the WebSocket. The mode switch is a **disruptive** action and gets the same two-step confirm as reboot/shutdown. See §2.6.1. |
| **D13** | **`.gps.json` format** | Handshake GPS sidecars are written in **bettercap session shape** (`Latitude`, `Longitude`, `Altitude`, `Updated`, …), byte-compatible with the stock `gps.py` plugin and `webgpsmap`, so both tools stay interoperable. Normalisation to `{lat, lon, accuracy}` happens on read, in the plugin, never on disk. See §2.12.1. |
| **D14** | **Static server behaviour** | SPA deep links fall back to `index.html`; MIME types are declared explicitly in the handler, never inherited from the system `mimetypes` database. See §2.15. |
| **D15** | **Protocol source of truth** | `docs/schemas/*.json` (JSON Schema draft 2020-12) is authoritative. `docs/PROTOCOL.md` documents it, frontend TS types are generated from it, and both plugin and frontend are tested against it. |
| **D16** | **Repository language** | English only, everywhere. Enforced by review. An automated word-list check was tried and removed: see §5.1. |

---

## 1. Repository structure

```
openpwnagotchi-companion/
├── LICENSE                          # GPL-3.0
├── README.md                        # top-level, see §7
├── ROADMAP.md                       # milestones narrative, see §8
├── CHANGELOG.md                     # Keep a Changelog, see §12
├── CONTRIBUTING.md
├── SECURITY.md                      # threat model + no-secrets policy, see §6.4
├── SPEC.md                          # this file
├── CLAUDE.md
├── .gitignore
├── plugin/
│   ├── companion.py                 # the pwnagotchi plugin (installable, flat)
│   └── README.md                    # plugin-specific install/config (mirrored in __help__)
├── frontend/
│   ├── package.json
│   ├── package-lock.json            # COMMITTED — `npm ci` in CI requires it
│   ├── vite.config.ts
│   ├── vitest.config.ts
│   ├── tsconfig.json
│   ├── svelte.config.js
│   ├── index.html
│   ├── public/
│   │   ├── manifest.webmanifest
│   │   └── icons/                   # 180/192/512 PNG + maskable
│   └── src/
│       ├── main.ts
│       ├── app.css                  # global styles, safe areas, dark theme
│       ├── App.svelte
│       ├── lib/
│       │   ├── ws.ts                # WebSocket client: connect, reconnect, queue, heartbeat
│       │   ├── protocol.ts          # GENERATED from docs/schemas — do not hand-edit
│       │   ├── stores.ts            # Svelte stores
│       │   ├── settings.ts          # persisted connection settings (localStorage)
│       │   ├── router.ts            # the seven routes of §4.5, and nothing more
│       │   └── geo.ts               # browser Geolocation acquisition + push to plugin
│       ├── shell/                   # the navigation shell (§4.5), not a view
│       │   ├── Nav.svelte           # bottom bar in portrait, leading rail in landscape
│       │   └── MoreSheet.svelte     # dialog holding Peers, Mirror, Settings
│       ├── views/
│       │   ├── Dashboard.svelte
│       │   ├── WiFi.svelte            # Nearby + Captured segments, see §4.5
│       │   ├── Peers.svelte
│       │   ├── Map.svelte
│       │   ├── Log.svelte
│       │   ├── Mirror.svelte
│       │   └── Settings.svelte
│       └── __tests__/               # vitest specs, see §10.5
├── tools/
│   ├── gen-ca.sh                    # create a private CA (idempotent)
│   ├── gen-cert.sh                  # issue an iPAddress-SAN server cert from that CA
│   ├── install-on-pi.sh             # pull latest Release dist.tgz to the Pi serve dir
│   └── gen-protocol-types.mjs       # docs/schemas/*.json -> frontend/src/lib/protocol.ts
├── tests/
│   ├── conftest.py                  # fake `pwnagotchi` package + FakeAgent, see §10.2
│   ├── fakes/
│   │   ├── pwnagotchi_stub/         # importable stand-in for the real package
│   │   └── fixtures/                # bettercap session JSON, handshake dirs, log samples
│   ├── test_stats.py
│   ├── test_access_points.py
│   ├── test_handshakes.py
│   ├── test_peers.py
│   ├── test_gps.py
│   ├── test_log_tail.py
│   ├── test_battery.py
│   ├── test_controls.py
│   ├── test_tls_startup.py
│   ├── test_auth.py
│   ├── test_binding.py
│   ├── test_static_server.py
│   ├── test_protocol_conformance.py # every outgoing message validated against its schema
│   ├── test_integration_ws.py       # real WSS server + real client, in-process
│   └── tools/
│       └── test_certs.py            # gen-ca.sh / gen-cert.sh assertions, see §10.6
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                   # lint + tests + build on push/PR
│   │   └── release.yml              # build dist.tgz + create Release on tag
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   └── feature_request.yml
│   ├── labels.json                  # the label taxonomy (§6.1)
│   ├── sync_labels.sh               # applies labels.json to the repository (§6.1)
│   ├── check_plugin.py              # AST-based plugin sanity check (§5.1)
│   ├── check_schemas.py             # JSON Schema validity and framing invariants (§5.1)
│   └── check_secrets.py             # generic credential scan (§5.1)
└── docs/
    ├── SETUP.md                     # full setup: CA, cert, iOS trust, install, first run
    ├── CERTIFICATES.md              # deep dive on the cert constraints and why
    ├── PROTOCOL.md                  # human-readable rendering of docs/schemas
    ├── PINNED-FACTS.md              # §11 of this document, extracted for standalone reference
    └── schemas/
        ├── common.json              # shared defs: AP, Handshake, Peer, Gps, Envelope
        ├── incoming/*.json          # one schema per client->plugin message type
        └── outgoing/*.json          # one schema per plugin->client message type
```

Note: the plugin lives in `plugin/companion.py`, NOT the repo root. It is installed by the
install script or by manual copy, documented in SETUP.md — do NOT rely on the flat-root
`custom_plugin_repos` unpack for this repo.

---

## 2. Backend plugin — `plugin/companion.py`

### 2.1 Identity and base

- Class `Companion(plugins.Plugin)`.
- `__author__`, `__version__`, `__license__ = 'GPL3'`, `__description__`, and a full `__help__`
  (config reference, ports, cert requirement, security model). `__version__` is the **project**
  version (§12), not a plugin-private one: CI asserts it equals the release tag. The fork
  lineage is credited in the header comment and the README, not encoded in a version number.
- Preserve a header comment crediting `BraedenP232/PwnIOS` (GPL-3.0) as the origin.

### 2.2 Config schema (`[main.plugins.companion]`)

```toml
[main.plugins.companion]
enabled = true
bind_addresses = ["172.20.10.0/28", "10.0.0.2"]   # IPv4 addresses or CIDR blocks (§2.3.1)
ws_port = 8082                     # WSS
http_port = 8443                   # HTTPS static (serves the PWA)
tls_cert = "/etc/pwnagotchi/companion/server.crt"   # PEM, must carry iPAddress SAN
tls_key  = "/etc/pwnagotchi/companion/server.key"
web_root = "/var/www/openpwn-companion"             # where install-on-pi.sh puts the built PWA
token = ""                         # optional shared secret; empty = disabled
auth_timeout = 10                  # seconds a client may stay unauthenticated before close
pisugar = true                     # battery via pisugar (auto-detect pisugarx_ext / pisugarx)
gps_source = "auto"                # auto | bettercap | gpsd | none (browser fallback always on)
gpsd_host = "127.0.0.1"
gpsd_port = 2947
session_poll_interval = 5          # seconds between background bettercap session refreshes
rebind_interval = 30               # seconds between local-address re-enumeration passes
save_gps_log = false
gps_log_path = "/var/tmp/pwnagotchi_gps.log"
mirror_auto_interval = 5           # seconds for the mirror auto-refresh option (client-driven)
keepalive_interval = 20            # seconds between unsolicited keepalive frames; 0 disables
```

Every value has a hardcoded default in the plugin matching the table above. Missing keys must
never raise; use `self.options.get(key, default)`.

### 2.3 Networking model

- On `on_loaded`, start a background thread running a private asyncio loop (same shape as
  pwnios).
- Enumerate the host's current IPv4 addresses via `netifaces` if importable, else parse
  `ip -4 addr show`. Do not add a hard dependency on `netifaces`. The enumeration returns
  addresses; which interface carries one is not consulted and must not reach a bind decision.
- Select the addresses to bind by matching that enumeration against `bind_addresses` (§2.3.1).
- For each selected IP:
  - Start a WSS server (see §2.3.1 for the `websockets` API compatibility rule).
  - Start an HTTPS static server serving `web_root` (§2.15), bound to `(ip, http_port)`.
- `ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ssl_ctx.load_cert_chain(tls_cert, tls_key)`.
  If cert/key are missing or unreadable: log an explicit error and **start no listener at all**
  (no plaintext fallback — iOS requires TLS, and a plaintext server would be a silent security
  foot-gun). Set `ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2`.
- Never bind `0.0.0.0`, `::`, or a hostname. Only literal IPv4 addresses the host actually has,
  selected by §2.3.1.

#### 2.3.1 Address selection and the rebinding loop

`bind_addresses` is a list whose entries are each either a literal IPv4 address or an IPv4 CIDR
block. A local address is selected when it equals a literal entry or falls inside a block entry.

Blocks exist because the literal address is less certain than it looks: over an iPhone Personal
Hotspot the subnet is always `172.20.10.0/28`, but the host part comes from the phone's DHCP
server, so a literal-only default would leave some units with no listener and the unhelpful
symptom "nothing started".

Entries are validated when `Listeners` is constructed, before any pass runs, and a rejected entry
never contributes a listener. An address that matches more than one entry is bound once, and so
is an address the enumeration itself reports more than once - the same IPv4 configured on two
interfaces, or a backend that lists it under both a primary and a secondary label:

| Entry | Action |
|---|---|
| valid IPv4 address, or block with prefix `/24` or longer | accepted |
| the wildcard, as an address (`0.0.0.0`) or inside a block (`0.0.0.0/24`) | rejected, logged at error. D5 and §11.1 forbid it in any bind call unconditionally, and that rule outranks "it parses as an IPv4 address". The same refusal applies to a wildcard arriving from the address enumeration, not only to a configured entry. |
| block with a prefix **shorter than `/24`** | rejected, logged at error. A block is a declaration of which network the owner chose, and one wider than a `/24` stops being a choice. `0.0.0.0/0` is refused by the wildcard row above before this one is reached; what this row actually catches is a plausible-looking private-range block, a `/8` or a `/16` copied out of somebody's router configuration. |
| block written with host bits set (`172.20.10.5/28`) | accepted, normalised to its network (`172.20.10.0/28`). The intent is unambiguous and refusing it would only produce a puzzling error. |
| anything else (hostname, IPv6, malformed) | rejected, logged at error; the remaining entries still bind |
| `bind_addresses` is not a list at all (a bare string, a number, a table) | treated as empty, logged at error. A string is iterable, and iterating one character by character would turn a typo into a pile of nonsense entries. The type check is `isinstance(value, list)`: TOML produces nothing else, so accepting other sequences would only widen what counts as a configuration. |
| `bind_addresses` is present but null | treated as empty, logged at error — **not** as absent. TOML has no null literal, so this does not arrive from `config.toml`; it arrives from a caller passing the options dict directly, which is how the plugin is driven under test and how it could be driven by anything embedding it. The rule exists because `self.options.get(key, default)` cannot tell a nulled key from a missing one, and the shipped default binds a whole subnet: a present-but-unusable value must never resolve to something more permissive than nothing. |
| every entry rejected, or the list empty | no listener at all, logged at error. Never fall back to a default that binds more. |

A legacy `interfaces` key is **ignored**, with a warning naming `bind_addresses`. Carrying the
old behaviour over silently would preserve the exposure D5.1 exists to close.

A `rebind_interval` timer (default 30 s) re-enumerates the host's addresses and reconciles:

| Observed | Action |
|---|---|
| selected address present, no listener bound | bind WSS + HTTPS on it, log at info |
| bound address no longer present locally | close both listeners for that address, drop its clients, log at info |
| a different address in the same block appears | it is a new selection: bind it, and unbind the old one under the rule above |
| no address matches any entry | skip silently after the first log; do not spam |
| bind raises `OSError` (`EADDRINUSE`/`EADDRNOTAVAIL`) | log at warning, leave unbound, retry next pass. Never crash the loop. |
| the **enumeration itself** raises, whatever the exception type | log at warning, make no change to the bound set, retry next pass. A failed enumeration observed nothing, and the rows above are about what was observed: treating it as an empty address list would tear down a healthy tether every time `ip -4 addr show` hiccups. "Never crash the loop" is about staying available, not merely about not propagating. |

State is a dict `{ip: (ws_server, http_server)}`, keyed on the address itself, so a flapping
tether or a new DHCP lease converges without a plugin reload.

#### 2.3.2 `websockets` API compatibility (mandatory)

The pwnagotchi image pins no `websockets` version (`pyproject.toml` lists a bare
`"websockets"`), and the library's server API changed incompatibly across major versions:
the legacy handler signature is `(websocket, path)`, the modern one is `(websocket)`, and
`websockets.serve` was superseded by `websockets.asyncio.server.serve`.

The reason to write both is the **absence of a pin**, not a guess about which version is out
there. Nothing constrains the dependency (F21) and `auto-update.py` runs `pip install` on the unit
after the image is built, so the installed major is a property of one unit's build-and-update
history rather than of the release it claims to be. A unit running 2.9.5.6 was observed at 17.0.1
(F27), which proves a modern major occurs in the field; it does not license assuming one, and it
does not license dropping the legacy branch either. Both forms must work, and neither may be
treated as the expected case.

Implementers MUST write both tolerantly and MUST NOT pick one form:

```python
async def _handle_client(websocket, *_):   # absorbs the legacy `path` argument
    ...

try:
    from websockets.asyncio.server import serve as ws_serve   # websockets >= 14
except ImportError:
    from websockets import serve as ws_serve                  # websockets < 14
```

Log the resolved `websockets.__version__` at load. `tests/test_integration_ws.py` must pass
against whatever version is installed in CI.

#### 2.3.3 The connection handler seam

`Listeners` takes the connection handler as its fourth argument and never builds one, so the
socket layer and the protocol layer can be tested apart. The handler is built by a module-level
factory with this exact signature, because both the plugin and the integration test have to
name it:

```python
def make_connection_handler(
    router: Router, deps: Deps, auth_timeout: float, clients: "ClientSet"
) -> Callable[..., Awaitable[None]]:
    async def handler(websocket, *_):   # *_ absorbs the legacy `path` argument (§2.3.2)
        ...
```

`ClientSet` is a small thread-safe set of live sockets with `add`, `discard` and `snapshot`.
It exists because the connection handler runs on the asyncio loop while `broadcast()` is called
from pwnagotchi's hook threads, so the membership set is genuinely shared across threads and
guarding it inside the plugin class would put the one piece of concurrency in the file behind
the one class that cannot be constructed without a pwnagotchi.

The handler owns exactly: the initial burst, the receive loop, JSON parsing, the authentication
deadline, dispatch into `Router.handle`, and registration in the `ClientSet`. It owns no
sockets and no TLS.

#### 2.3.4 Authentication

If `token` is non-empty, the client's first frame must be `{"type":"auth","token":"..."}`.

- Compare with `hmac.compare_digest`, never `==`.
- A connection that has not authenticated within `auth_timeout` seconds is closed with code
  `1008`. This is a hard requirement: without it an unauthenticated peer can hold sockets open.
- On failure, send one `error` message then close `1008`. Do not reveal whether the token was
  wrong or malformed.
- If `token` is empty, skip auth entirely (D5). **The justification is convenience, not
  isolation, and earlier revisions of this document got that wrong.** An iPhone Personal Hotspot
  hands out `172.20.10.0/28`: that is a shared subnet with room for fourteen hosts, not a
  point-to-point link. Anything else joined to the same hotspot can reach the listener, and with
  no token it gets handshakes, GPS and controls. The default stays disabled per D5 because the
  usual case is one phone and one unit, but the exposure has to be stated where the user chooses,
  not assumed away here. See `docs/SETUP.md`.
- The deadline is measured with `deps.now()`, not with the event loop's clock. Every other
  time-dependent rule in this plugin already goes through that seam, and a second notion of
  "now" would mean this one rule could only be tested by actually waiting.
- **The deadline must be enforced against a silent client, not only against the next frame.**
  Checking it when a message arrives is worthless: the peer this rule exists to stop is exactly
  the one that opens a socket and then says nothing. The receive is therefore polled with a
  timeout of `AUTH_POLL_INTERVAL` (0.25 s) while the connection is unauthenticated, and the
  deadline is re-checked on every expiry. The cost is bounded because it applies only before
  authentication, and an unauthenticated connection either becomes authenticated or is closed.
- A successful `auth` is answered with an `acknowledgment` **whether or not a `message_id` was
  supplied**, which is the one exception to the rule in §2.4. It is the client's only signal
  that the connection is usable: failure is an error followed by a close, so without it success
  is indistinguishable from a slow link.
- **Any** reply carrying `error.code == "unauthorized"` closes the connection with `1008`, not
  only a failed `auth` frame. A client that sends `get_stats` before authenticating is either
  broken or probing, and in both cases leaving the socket open serves nothing: the contract is
  that the auth frame comes first (§2.4), so a command that arrives before it is not a race the
  plugin has to be forgiving about.

### 2.4 WebSocket contract

**Authoritative form: `docs/schemas/`.** The tables below are a readable summary. Where prose
and schema disagree, the schema is correct and the prose is a bug to be fixed. Framing and
lifecycle rules that a schema cannot express live in `docs/PROTOCOL.md`.

Framing is asymmetric, and this is locked (it matches the upstream `pwnios` wire format):

- **Incoming is flat** — payload fields sit next to `type`:
  `{"type":"set_mode","mode":"manual","message_id":"c7f1"}`.
- **Outgoing uses an envelope** — payload always under `data`, `timestamp` always present:
  `{"type":"acknowledgment","data":{"acknowledged":"set_mode"},"timestamp":1755264000.1,"message_id":"c7f1"}`.

Every schema sets `additionalProperties: false`; an unexpected key is an `error` with code
`bad_request`, not something to ignore.

Once a connection is authenticated the plugin sends an initial `stats`, `access_points` and
`face_status` unprompted, so a fresh client has state without a round of polling.

**The burst comes after authentication, never before it.** Sending it on accept would hand the
unit's stats, its access point list and its face to anyone who can open a socket, which makes
the token worth nothing for reading and turns the initial burst into the leak. The exact order
is: with no token configured, the burst goes out on accept, because there is nothing to wait
for (D5); with a token configured, nothing is sent until the `auth` frame succeeds, and then
the `acknowledgment` goes first and the burst follows it.

**A connection joins the broadcast set at the same moment, and not on accept.** Every push
message - `wifi_update`, `handshake`, `peer_detected`, `status_change`, `face_status` - goes to
every member of that set, so a socket registered on accept would receive the same data the
burst was just held back from, through the other channel, for the whole `auth_timeout` window,
and a peer could renew that window by reconnecting. Admission is one step: join the set, then
send the burst.

#### Incoming commands (client → plugin)

| type | payload | action |
|---|---|---|
| `auth` | `{token}` | validate if token configured (§2.3.4) |
| `get_stats` | — | reply `stats` |
| `get_access_points` | — | reply `access_points` |
| `get_handshakes` | — | reply `handshakes_list` |
| `get_peers` | — | reply `peers_list` |
| `get_log` | `{lines?}` | reply `log_lines` |
| `get_face_status` | — | reply `face_status` |
| `get_screen` | — | reply `screen_image` |
| `set_mode` | `{mode: "auto"\|"manual"}` | §2.6.1 — service restart, disruptive |
| `set_pasv` | `{on: bool}` | §2.6.2 — soft dependency |
| `reboot` | — | §2.6.3 |
| `shutdown` | — | §2.6.3 |
| `ping` | — | reply `pong` |
| `gps_data` | `{latitude, longitude, accuracy}` | store as browser-sourced GPS; no reply beyond the acknowledgment |
| `get_gps_data` | — | reply `gps_update` |

`gps_data` deliberately pushes nothing back. The sender already knows the position it just
sent, and echoing a `gps_update` to it would make the browser fallback look like a fix coming
from the Pi. A client that wants the merged view asks for it with `get_gps_data`.

Unknown `type` → reply `error` with code `unknown_command`. Malformed JSON → `error` with code
`bad_request`; never let a parse failure kill the connection handler. A frame that is valid
JSON but is not an object (an array, a number, a string, `true`, `null`) is also `bad_request`:
it cannot carry a `type`, and `bad_request` is the only enum member that fits.

#### Outgoing messages (plugin → client)

| type | when | `data` shape |
|---|---|---|
| `stats` | reply / broadcast | `Stats` (§2.5) |
| `access_points` | reply | `AccessPoint[]` (§2.5) |
| `handshakes_list` | reply | `{entries: HandshakeEntry[], truncated, total}` (§2.7) |
| `peers_list` | reply | `{entries: Peer[]}` (§2.8) |
| `log_lines` | reply | `{lines: string[], path}` (§2.9) |
| `screen_image` | reply | `{png: <base64>, mtime}` (§2.10) |
| `face_status` | reply / push | `{face, status, mode}` |
| `handshake` | push, `on_handshake` | `{filename, ap, station, gps}` (§2.13) |
| `peer_detected` | push, `on_peer_detected` | `Peer` |
| `wifi_update` | push, `on_wifi_update` | `AccessPoint[]` |
| `channel_hop` | push, `on_channel_hop` | `{channel}` |
| `status_change` | push, mood hooks | `{status, mood}` |
| `gps_update` | reply / push | `Gps` (§2.12) |
| `restarting` | push, before a restart or power off | `{reason, mode}` (§2.6.1) |
| `acknowledgment` | reply, when `message_id` was supplied, and always for a successful `auth` (§2.3.4) | `{acknowledged}` |
| `error` | reply | `{code, message}`, `code` from a closed enum |
| `keepalive` / `pong` | infra | empty object |

`keepalive` is broadcast every `keepalive_interval` seconds (default 20, `0` disables it) from
the background thread, which therefore wakes at `min(session_poll_interval, keepalive_interval)`
rather than on the session poll alone: the frame can only go out on a tick, so a loop that woke
every 5 s would turn a nominal 20 s cadence into 20-25 s and make a client whose timeout is set
to the nominal value reconnect for no reason.

It exists so an idle client can tell a quiet plugin from a dead link: over BT PAN a dropped
tether does not close the socket, it simply stops delivering, and without a periodic frame the
app would sit on a dead connection showing stale data. The frontend treats a missed interval as
a reason to reconnect (§10.5).

### 2.5 `stats` payload

**Blocking-call rule (critical).** `agent.session()` is a synchronous HTTP GET to bettercap with
a 30-second timeout (§11, F7). Calling it from inside the asyncio handler stalls every client
for up to 30 seconds. It MUST NOT be called on the request path. A background thread refreshes
a cached session snapshot every `session_poll_interval` seconds, guarded by a lock and a
try/except; all readers use the cache and expose its age.

```
uptime          = pwnagotchi.uptime()               # int seconds        (F1)
mode            = "MANUAL" if agent.mode == "manual" else pasv_or_auto() (F11)
channel         = agent._current_channel            # attribute, NOT session()  (F6)
battery         = battery_info()                    # §2.11
temperature     = pwnagotchi.temperature()          # int °C             (F2)
handshakes      = count of *.pcapng in the handshakes dir  # matches the device display (F15)
handshakesTotal = count of *.pcapng plus *.pcap            # always >= handshakes
peers           = len(agent._peers)                                      (F5)
accessPoints    = len(agent._access_points)                              (F3)
lastHandshake   = newest handshake dir entry {filename, ssid, bssid, mtime} or null
lastPeer        = most recently seen entry from agent._peers, or null
gps             = current_gps()                     # §2.12
sessionAge      = seconds since the cached bettercap session was refreshed, or null
capabilities    = {pasv, pisugar, gpsSource, pluginVersion}
```

Neither count comes from `len(agent._handshakes)`, which only holds the current session.
`handshakes` deliberately mirrors `utils.total_unique_handshakes` so the number in the app
matches the number on the e-ink display; `handshakesTotal` is the honest total including legacy
`.pcap` captures.

`capabilities.pasv` is `'pasv_mode' in plugins.loaded`; `capabilities.pisugar` is a **latch** —
false until some provider answers once, true from then on. A live flag would flicker every time
an I2C read happened to fail, and a control that appears and disappears is worse than one that
is simply present; `capabilities.gpsSource` echoes the **configured** `gps_source`,
not the currently active one (that is `gps.source`); `capabilities.pluginVersion` is
`__version__`, so a client can disable features rather than firing commands an older plugin
will reject with `unknown_command`.

`pasv_or_auto()`: if `agent.mode == 'auto'` and `pasv_active()` → `"PASV"`, else `"AUTO"`.
`pasv_active()`: `bool(getattr(plugins.loaded.get('pasv_mode'), 'passive', False))` (F12).

`capabilities.pasv` is `'pasv_mode' in plugins.loaded`. The PWA hides the PASV control when it
is false; it must not infer availability any other way.

AP object, mapped from `agent._access_points` (bettercap AP dicts — **the key is `mac`, not
`bssid`**, F4):

```
{ bssid:      ap['mac'],
  hostname:   ap.get('hostname') or ap.get('mac'),
  channel:    ap.get('channel', 0),
  rssi:       ap.get('rssi', 0),
  encryption: ap.get('encryption', ''),
  vendor:     ap.get('vendor', ''),
  clients:    len(ap.get('clients') or []) }
```

`ap.get('clients')` may be `None` as well as absent; the `or []` is required.

### 2.6 Controls

#### 2.6.1 `set_mode` — service restart, not an attribute write (D12)

v1 of this spec, and upstream `pwnios.py`, both assign `agent.mode = mode` and assume the main
loop reacts. **It does not.** The mode is chosen at process start from the `--manual` CLI flag;
`agent.mode` is only a label set inside `do_manual_mode()` / `do_auto_mode()` (F10). Writing it
changes the reported mode and nothing else.

The real switch, as used by the stock `webcfg` plugin (F13), is:

```python
import pwnagotchi
threading.Thread(target=pwnagotchi.restart, args=("MANU" if manual else "AUTO",),
                 daemon=True).start()
```

`pwnagotchi.restart(mode)` uppercases the argument, touches `/root/.pwnagotchi-manual` or
`/root/.pwnagotchi-auto`, and restarts both the `bettercap` and `pwnagotchi` services (F9).

Consequences the implementer MUST handle:

- The plugin dies with the service. Before spawning the restart thread, send
  `acknowledgment` for the `message_id`, then broadcast `restarting` to every client, then
  flush, then start the thread.
- **Two vocabularies meet here and must not be confused.** `pwnagotchi.restart` takes `"MANU"`
  or `"AUTO"`; the wire uses the `Mode` enum from `common.json`, which is `AUTO | PASV | MANUAL`.
  The broadcast therefore carries `{"reason":"mode_change","mode":"MANUAL"}` — sending the
  restart argument to the client emits a value no schema accepts. (An earlier revision of this
  section quoted `"MANU"` here; that was a prose bug, caught by the conformance test.)
- The PWA treats `restarting` as an expected disconnect: show "restarting in MANU…", suppress
  the error banner, and reconnect with backoff (typical return: 20-60 s).
- Because it is disruptive, `set_mode` gets the **same two-step confirm** as reboot/shutdown
  (D9 extended by D12).
- If the requested mode equals the current one, reply `acknowledgment` and do nothing.
- The plugin does **not** close the socket after `restarting`. It has nothing to add and the
  process is about to go away on its own; closing first would replace an informative
  "restarting" state in the app with a plain disconnect, which is exactly the ambiguity the
  broadcast exists to remove. The same applies to `reboot` and `shutdown` (§2.6.3).

#### 2.6.2 `set_pasv` — soft dependency

- If `'pasv_mode' not in plugins.loaded`: reply `error` code `pasv_unavailable`.
- If `agent.mode != 'auto'`: reply `error` code `pasv_requires_auto`. PASV is only meaningful
  in auto.
- **The order matters and is fixed**: when the plugin is absent *and* the mode is not auto, the
  reply is `pasv_unavailable`. The two lead to different client behaviour — a missing plugin
  hides the control, the wrong mode greys it out — so the precedence cannot be left to chance.
- Otherwise emit `plugins.on('pasv_on')` or `plugins.on('pasv_off')` (F12).

`plugins.on()` is asynchronous: it queues work onto each plugin's own thread and returns no
value (F8). **Do not read `pasv_mode.passive` back immediately** — it will still hold the old
value. Reply `acknowledgment` only; the new state reaches the client with the next `stats`
broadcast. The PWA shows the toggle as pending until `stats.mode` confirms it.

Never `import pasv_mode`. Access it only through `plugins.loaded`.

#### 2.6.3 `reboot` / `shutdown`

There is no public `agent.reboot()` or `agent.shutdown()`. The module-level
`pwnagotchi.reboot()` and `pwnagotchi.shutdown()` exist and perform the graceful path
(UI notice, sync, unmount) (F9). Use exactly those, in a daemon thread, after broadcasting
`restarting` with the matching reason.

Fallback only if the import or call raises: `subprocess.run(['sudo','reboot'])` /
`subprocess.run(['sudo','shutdown','-h','now'])`.

These are gated behind the two-step UI confirm; the plugin still executes immediately when the
command arrives — the confirm is a client-side affordance, not a protocol handshake.

### 2.7 Handshake list (`handshakes_list`)

Enumerate `agent._config['bettercap']['handshakes']` (F14; default `/etc/pwnagotchi/handshakes`).

- Match `*.pcapng` **and** `*.pcap`. The stock counter only globs `*.pcapng` (F15), but older
  captures and other tools produce `.pcap`; count and list both, and report the stock-compatible
  `.pcapng` count separately in `stats.handshakes` so the number matches the device display.
- Derive `ssid` / `bssid` from the filename. pwnagotchi names captures `SSID_BSSID.pcapng` with
  the BSSID lowercased and stripped of colons. Parse **defensively**: split on the *last*
  underscore, accept a 12-hex-digit tail as the BSSID, and if it does not match, emit
  `{ssid: <full basename>, bssid: null}` rather than guessing. SSIDs legitimately contain
  underscores; never split on the first one.
- Include `mtime` (float epoch) and `size` (bytes).
- If a sibling GPS sidecar exists (§2.12.1), include the **normalised**
  `{lat, lon, accuracy, source}`. On a missing, malformed or fix-less sidecar the field is
  `null` — `HandshakeEntry` requires `gps` to be present, so "omit it" is not available; the
  entry survives either way. Accuracy falls back to `hdop_to_m(HDOP)` when the sidecar carries
  no explicit accuracy, which is the case for every sidecar the stock `gps.py` plugin writes.
- Sort newest first. Cap at 500 entries and set `truncated: true` rather than silently dropping.
- A single unreadable file must not abort the listing.

### 2.8 Peer/mesh view (`peers_list`)

**Use `agent._peers.values()` only.** v1 suggested falling back to `pwnagotchi.grid.peers()`;
that returns raw pwngrid JSON with a different shape, which would put two incompatible payloads
behind one message type. `grid.peers()` is not used by this plugin.

`agent._peers` is a dict keyed by peer identity holding `Peer` objects, not dicts (F5, F16).
The real members, and the exact mapping to emit:

| Emitted field | Source | Note |
|---|---|---|
| `name` | `peer.name()` | from `adv`, defaults `'???'` |
| `fingerprint` | `peer.identity()` | from `adv`, defaults `'???'` |
| `fullName` | `peer.full_name()` | `name@identity` |
| `rssi` | `peer.rssi` | attribute |
| `channel` | `peer.last_channel` | attribute — **not** `.channel` |
| `firstSeen` | `peer.first_seen` | `datetime` → epoch float |
| `prevSeen` | `peer.prev_seen` | `datetime` → epoch float |
| `firstMet` | `peer.first_met` | `datetime` → epoch float |
| `lastSeen` | `peer.last_seen` | already a **float epoch** — do not convert |
| `encounters` | `peer.encounters` | int |
| `pwndRun` | `peer.pwnd_run()` | method — **not** `pwnd_run` attribute |
| `pwndTotal` | `peer.pwnd_total()` | method — **not** `pwnd_tot` |
| `version` | `peer.version()` | |
| `uptime` | `peer.uptime()` | |
| `face` | `peer.face()` | text face |

Note the type asymmetry: `first_seen`/`prev_seen`/`first_met` are `datetime` objects while
`last_seen` is a `time.time()` float. Normalise all five to epoch floats on the wire, and unit
test that mismatch explicitly (§10.2) — it is the single most likely place for a silent bug.

Every accessor goes through a `try/except` returning `None`; `Peer.__init__` itself falls back
to strings when timestamp parsing fails (F16), so `first_met` can be a `str` in practice.

### 2.9 Log viewer (`log_lines`)

Read the path from `agent._config['main']['log']['path']` (F17; default on this fork is
`/etc/pwnagotchi/log/pwnagotchi.log`, **not** `/var/log/pwnagotchi.log`). Never hardcode.

Return the last `lines` as a list of strings, oldest first. Implement a bounded tail: seek from
the end in chunks until enough newlines are collected, decode with `errors='replace'`, and split
on `\n` only — `str.splitlines()` also breaks on `\x0b`, `\x1e` and U+2028, inventing lines the
log never contained. Never read the whole file; it is rotated but can still be megabytes.

**`lines` is clamped, not rejected.** The schema declares `maximum: 1000`, which is the limit a
conformant client respects; the plugin additionally clamps to `[1, 1000]` with a default of 200,
because a client asking for more should get a bounded answer rather than an error. Absent or
unparseable means the default.

`tail_lines` propagates `OSError` for a missing or unreadable file; the Router turns that, and a
missing log path in the configuration, into `error` code `log_unavailable`.

### 2.10 Screen mirror (`screen_image`)

```python
from pwnagotchi.ui import web as ui_web       # package __init__, F18
with ui_web.frame_lock:
    with open(ui_web.frame_path, 'rb') as f:  # /var/tmp/pwnagotchi/pwnagotchi.png
        data = base64.b64encode(f.read()).decode()
```

On-demand only; the client drives the optional ~5 s auto-refresh by polling. Never push the
frame on UI updates — it is far too heavy for the BT link. Missing frame file → `error` code
`no_frame`. Hold `frame_lock` for the read only, never across the send.

### 2.11 Battery

Auto-detect in order, each step guarded:

1. `plugins.loaded.get('pisugarx_ext')`, then `plugins.loaded.get('pisugarx')` — read whatever
   battery level / charging attributes they expose, via `getattr` with defaults. **UNVERIFIED:**
   these are third-party plugins and their attribute names are not pinned. Probe with `getattr`
   over a small candidate list and treat every miss as "unavailable"; do not import them and do
   not assume a name.
2. Direct PiSugar I2C read (bus 1, address `0x57`, register `0x2A` for percentage), wrapped in
   try/except, only if `pisugar = true`.
3. `{percent: null, charging: null}`.

Never hard-fail, never raise into the asyncio loop.

### 2.12 GPS abstraction — Pi-source-first, browser fallback

`Gps` is **one uniform shape** (`common.json#/$defs/Gps`), never a union. When nothing is
available, `enabled` is `false` and every coordinate field is `null`; there is no alternative
short form. `source` is `"bettercap" | "gpsd" | "browser" | null` — note that both `bettercap`
and `gpsd` are on-Pi sources, which is why the boolean the client actually gates on is `piFix`,
not a comparison against a source name.

`current_gps()` resolves in this priority, honouring `gps_source`:

1. **bettercap** — from the **cached** session snapshot (§2.5), key `gps`. Bettercap uses
   capitalised keys: `Latitude`, `Longitude`, `Altitude`, `FixQuality`, `NumSatellites`, `HDOP`
   (F19). A fix counts only if `Latitude` and `Longitude` are both present, finite, and non-zero.
   **Either** coordinate being zero means no lock: the stock `gps.py` plugin writes a sidecar
   only when both are truthy (F19), so a half-zero reading is a receiver without a fix, not a
   position on the equator. Emit `source: "bettercap"`, `fix: true`, `piFix: true`,
   `accuracy: hdop_to_m(HDOP)`. `hdop_to_m` is unrounded: rounding to one decimal turns a very
   small HDOP into `0.0` metres, which claims perfect precision instead of reporting high
   precision.
2. **gpsd** — connect to `gpsd_host:gpsd_port`, send `?WATCH={"enable":true,"json":true}`, read
   TPV objects with `mode >= 2` and finite lat/lon. Use `gpsd-py3`/`gps` if importable, else a
   minimal raw JSON poll. Bounded: socket timeout ≤ 2 s, refreshed from the same background
   thread as the session cache, never from the request path. Emit `source: "gpsd"`,
   `piFix: true`. The zero-coordinate rule applies here too, for the same reason it applies to
   bettercap: a receiver reporting exactly `0.0` on either axis has not locked.
3. **browser** — the last `gps_data` pushed by the client. `source: "browser"`, `piFix: false`,
   dropped after 10 s of staleness.
4. none → `enabled: false`, `fix: false`, `piFix: false`, all coordinates `null`.

`piFix` is the contract with the client: **while it is true the PWA must not send `gps_data`**,
because the browser reading is the worst of the three and must never overwrite a device fix.

#### 2.12.1 Handshake GPS sidecars (D13)

On `on_handshake`, write a sidecar next to the capture.

- **Filename**: `os.path.splitext(filename)[0] + '.gps.json'`. Do **not** copy the stock
  plugin's `filename.replace('.pcapng', '.gps.json')` — it silently produces a wrong name for a
  `.pcap` capture.
- **Content**: the bettercap-shaped dict — capitalised keys, exactly what the stock `gps.py`
  plugin writes (F19) — so `webgpsmap` and any existing sidecars on the device stay readable by
  the same code. When the fix came from gpsd or the browser, synthesise the same shape
  (`Latitude`, `Longitude`, `Altitude`, `Updated`) and add a non-conflicting
  `"Source": "gpsd"|"browser"` key.
- **Do not write a sidecar with no fix.** No coordinates is not `0.0, 0.0`.
- Reading is normalised in the plugin to `{lat, lon, accuracy, source}`; the disk format is
  never the wire format.
- If a sidecar already exists, do not overwrite it.

### 2.13 Event hooks (push)

`on_handshake`, `on_peer_detected`, `on_wifi_update`, `on_channel_hop`, and the mood hooks
(`on_bored` / `on_excited` / `on_lonely` / `on_sad` → `status_change`) broadcast to connected
clients through the async queue, as pwnios does. `on_ui_update` diffs face/status and pushes
`face_status` (text only — no PNG). Keep the heartbeat/keepalive machinery from pwnios.

**`on_handshake` has two argument shapes.** The agent fires it from two code paths (F20):

```python
plugins.on('handshake', self, filename, ap_mac, sta_mac)   # str, str
plugins.on('handshake', self, filename, ap, sta)           # dict, dict
```

So `access_point` and `client_station` may each be a MAC string **or** a bettercap dict. The
handler must normalise both:

```python
def _mac_of(x):
    if isinstance(x, dict):
        return x.get('mac') or x.get('bssid')
    return x if isinstance(x, str) else None
```

This is tested in `tests/test_handshakes.py` with both shapes. Never assume the dict form.

### 2.14 Hardening vs upstream `pwnios.py` (checklist)

- [ ] Bind to configured addresses, never `0.0.0.0` (upstream binds `0.0.0.0:8082`), and never
      decide a bind from an interface name. (D5, D5.1)
- [ ] TLS mandatory; refuse to start listeners without a usable cert/key. (D4)
- [ ] `bssid` ← `ap['mac']` (upstream reads `ap['bssid']`, which is always empty). (F4)
- [ ] Read `_access_points` / `_handshakes` / `_peers` / `mode` directly; delete the
      non-underscore `hasattr(agent, 'access_points')` branches — they are false on this fork.
- [ ] Never call `get_access_points()`: it fires `plugins.on('wifi_update')`, runs
      `_epoch.observe`, and drives `_strategy.select_next_channels`. (F3)
- [ ] Handshake count from the directory, not `len(_handshakes)` (session-only).
- [ ] `set_mode` via `pwnagotchi.restart`, not an attribute write. (D12, F10)
- [ ] `agent.session()` never on the request path; cached in a background thread. (F7)
- [ ] Channel from `agent._current_channel`, not from a session round-trip. (F6)
- [ ] Peers mapped from `Peer` objects with the correct method/attribute names. (F16)
- [ ] `on_handshake` accepts both str and dict arguments. (F20)
- [ ] Optional token auth with `hmac.compare_digest` and an unauthenticated-connection timeout.
- [ ] PASV soft dependency via `plugins.loaded`, never an import.
- [ ] All paths/ports/dirs from config; nothing host-specific hardcoded.
- [ ] Every I2C / GPS / file / socket read guarded; no unhandled exception can kill the loop.
      A top-level `except Exception` in the handler logs and continues.

### 2.15 HTTPS static server (D14)

A `ThreadingHTTPServer` subclass whose socket is wrapped with `ssl_ctx.wrap_socket(...,
server_side=True)`, running in its own thread, with `SimpleHTTPRequestHandler(directory=web_root)`.

Two requirements v1 omitted, both of which break the PWA silently:

1. **Explicit MIME map.** The system `mimetypes` database on a minimal Raspberry Pi OS image
   does not know `.webmanifest`; serving it as `application/octet-stream` makes iOS ignore the
   manifest and refuse installation. Set the handler's `extensions_map` explicitly for at least:

   ```
   .html .webmanifest .js .mjs .css .json .png .svg .ico .wasm .map .woff2
   ```

   `.js` and `.mjs` must be `text/javascript` — a service worker served with the wrong type is
   rejected by the browser. `.webmanifest` is `application/manifest+json`, `.ico` is
   `image/vnd.microsoft.icon`, `.map` is `application/json`. For the rest what matters is only
   that nothing the app needs arrives as `application/octet-stream`.

2. **SPA fallback.** For a `GET` whose path does not resolve to an existing file and does not
   look like an asset request, serve `index.html` with status 200 so deep links and refreshes
   work. Requests for paths containing `..` are rejected with 400 before any resolution.

Additional rules:

- Set `Cache-Control: no-cache` on `index.html` and on the service worker — which
  `vite-plugin-pwa` emits as `sw.js`, alongside its `registerSW.js` registration shim, so both
  names are covered. Everything else Vite emits is content-hashed and immutable, so it may be
  cached aggressively.
- A request for a directory must not produce a listing. Any of 200 (serving `index.html` through
  the SPA fallback), 403 or 404 is acceptable; what is forbidden is disclosing the contents.
- Log at debug only; a request log at info would flood the pwnagotchi log.
- Directory listings disabled.
- The server must not raise out of its thread; wrap `serve_forever` in try/except and log.

---

## 3. TLS / CA (`tools/`, `docs/CERTIFICATES.md`)

The hard iOS constraints, all mandatory:

- The **CA** cert must carry `basicConstraints = critical, CA:TRUE`. Without `critical`,
  Python and iOS reject it even though `curl` accepts it — this exact failure has already been
  hit once on this setup.
- The **server** cert must list each Pi IP as `subjectAltName = IP:<addr>` (an `iPAddress`
  general name, **not** `DNS:`). Include every IP the app will use (`172.20.10.7`, `10.0.0.2`, …).
- Server cert validity **≤ 825 days**, or iOS rejects it.
- The root CA is installed on the iPhone as a profile **and** enabled in
  Settings → General → About → Certificate Trust Settings (full trust).

Both scripts are POSIX `sh` (`set -eu`), depend on nothing but `openssl`, and are
**non-interactive**: they never prompt, so the same invocation works from a terminal, from
`tests/tools/test_certs.py` and from a future CI job. Everything either script needs comes from
its arguments.

Shared conventions, pinned so the tests and the scripts can be written independently of each
other:

- `--out DIR` is where all four files live — `ca.key`, `ca.crt`, `server.key`, `server.crt` —
  and it defaults to the current directory. `gen-cert.sh` reads the CA from that same
  directory; there is no separate `--ca-dir`, because splitting them buys nothing and doubles
  the ways to get the invocation wrong. `gen-ca.sh` creates the directory if it does not exist; `gen-cert.sh` does not, because a
  directory with no CA in it is the missing-CA refusal below, and creating it first would only
  make the error point at a path the script had just invented.
- Keys are RSA with SHA-256: 4096 bits for the CA, 2048 for the server. iOS rejects anything
  weaker, and an EC key buys nothing here since the certificate is generated on a workstation,
  not on the Pi.
- Private keys are written with mode `0600`, certificates `0644`.
- Exit status: `0` success, `1` refusal (a CA already exists, or the CA is missing) or an
  `openssl` failure, `2` usage error (unknown flag, a flag without its value, an argument the
  script takes no positional arguments for, no IP given, an argument that is not an IPv4
  address). Usage errors are decided before refusals, so an invocation that is wrong in both
  ways exits `2`.
- Every non-zero exit writes nothing at all: on success the output directory holds exactly
  `ca.key`, `ca.crt`, `server.key`, `server.crt` and nothing else, and on failure it holds
  exactly what it held before. Intermediates - the CSR, the generated `openssl` config, the CA
  serial - live in a temp directory and never appear next to the four files the user is told
  about. An `openssl` failure prints openssl's own stderr before the script's own message.
- Both scripts start with `#!/bin/sh` and are committed executable (mode 755), so
  `./tools/gen-ca.sh` works and the file cannot silently become bash-only.
- "IPv4 address" means a canonical dotted quad: four decimal octets, each `0-255`, no leading
  zero on a multi-digit octet, no trailing dot. Leading zeros are rejected rather than guessed
  at, because `010.1.1.1` means ten to `inet_pton` and eight to anything that reads it as octal,
  and a certificate is the wrong place to find out which. `0.0.0.0` is rejected as well: it is
  the wildcard D5 exists to keep out, and it is never a real address to reach the Pi on.

### 3.1 `gen-ca.sh`

    gen-ca.sh [--out DIR] [--force]

Creates `ca.key` and a self-signed `ca.crt`, subject `CN=OpenPwnagotchi Companion CA`, valid
3650 days, with `basicConstraints=critical,CA:TRUE` and `keyUsage=critical,keyCertSign,cRLSign`.

Refuses to overwrite an existing CA: if either `ca.key` or `ca.crt` is present, it exits `1`
and leaves both files byte-identical, unless `--force` is given. This is not politeness. A
regenerated CA silently invalidates every certificate issued from it and every phone that
trusts it, and the failure shows up later as an app that will not load, with nothing pointing
back at the moment the CA was replaced.

### 3.2 `gen-cert.sh`

    gen-cert.sh [--out DIR] IP [IP ...]

Requires at least one IP. Each argument must parse as an IPv4 address; anything else — a
hostname, an IPv6 address, `1.2.3.4/24` — exits `2` without writing. A hostname here is the
`DNS:`-instead-of-`IP:` trap, and it fails on iOS in a way that looks like a network problem
rather than a certificate one, so it is rejected at the only point where the mistake is still
visible.

Builds an OpenSSL config with `[alt_names]` holding `IP.1=…`, `IP.2=…` for every argument,
generates `server.key` plus a CSR, and signs it with the CA into `server.crt`: subject
`CN=<first IP>`, `-days 820`, `basicConstraints=CA:FALSE`, `keyUsage=critical,
digitalSignature,keyEncipherment`, `extendedKeyUsage=serverAuth`, and `subjectAltName` from the
IP list. No `DNS:` entry is ever emitted, not even for the CN.

Unlike the CA it overwrites `server.key`/`server.crt` without `--force`, because reissuing after
an address change is the normal case, and requiring a flag for the routine operation trains the
user to pass it for the dangerous one too. Exits `1` if the CA is not in `--out`.

Prints the paths to set as `tls_cert`/`tls_key` in the plugin config, and the path of the
`ca.crt` to install on the phone.

`docs/CERTIFICATES.md` documents the required extensions so users on an existing PKI (EasyRSA
and friends) can issue from their own CA instead.

### 3.3 iOS install steps (`docs/SETUP.md`)

AirDrop or email `ca.crt` → install profile → **enable full trust** → open `https://<ip>:8443`
in Safari → Add to Home Screen. Include the silent-failure note: if the CA is not fully trusted,
HTTPS fails quietly, the service worker never registers, and the app appears simply broken.

---

## 4. Frontend PWA (`frontend/`)

### 4.1 Stack and build

- Svelte + TypeScript + Vite. `vite-plugin-pwa` generates the service worker (Workbox,
  `registerType: 'autoUpdate'`, app shell precached) and wires the manifest.
- Leaflet bundled locally; OSM tiles load from the internet. This works because the phone is
  the hotspot (it keeps cellular) and the Pi is the BT PAN client at `172.20.10.7` — the phone
  is not routing through the Pi. Tiles are therefore online-only in v1; offline tile caching is
  a roadmap item.
- `package-lock.json` is committed. CI runs `npm ci` and will fail without it.
- Output to `frontend/dist/`, packed to `dist.tgz` by CI.

### 4.2 iOS specifics (mandatory)

- `index.html` head:
  ```html
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <link rel="apple-touch-icon" href="/icons/apple-touch-icon-180.png">
  ```
- `manifest.webmanifest`: `display: standalone`, `scope: "/"`, `start_url: "/"`, dark
  `theme_color` / `background_color`, name/short_name, 192 + 512 icons including
  `purpose: "maskable"`.
- Global CSS honours safe areas via `env(safe-area-inset-*)`; a sticky top bar accounts for the
  Dynamic Island, a bottom **navigation** bar for the home indicator, and in landscape the
  leading-edge rail for `inset-left`/`inset-right` (§4.5). It is deliberately not called a tab
  bar: §4.5 rejects tab semantics for it, and the name is what an implementer reaches for when
  choosing the ARIA role.
- Optimised for iPhone 17 Pro: dark theme by default, large touch targets, `prefers-color-scheme`
  respected, smooth at 120 Hz — animate transforms and opacity, avoid layout thrash.

### 4.3 WebSocket client (`lib/ws.ts`)

- Connect to `wss://<host>:<ws_port>` from settings. Reconnect with exponential backoff plus
  jitter, capped.
- Outbound queue with a per-message `message_id`; resolve on the matching `acknowledgment`;
  re-send on reconnect. Heartbeat `ping` every N seconds; force a reconnect if no `pong`
  arrives within the timeout.
- If a token is set, send `{type:'auth',token}` as the first frame.
- Typed `send()` plus an event dispatch that updates the stores. Types come from
  `lib/protocol.ts`, which is **generated** from `docs/schemas` by
  `tools/gen-protocol-types.mjs` — never hand-edited.
- Connection state: `connecting | connected | degraded | offline | restarting`. Because the
  socket only works while tethered, `offline` is the normal disconnected state and is shown
  calmly, not as an error. `restarting` is entered on a `restarting` message (§2.6.1) and
  suppresses the error banner while showing the reason.

### 4.4 Stores (`lib/stores.ts`)

Svelte writable/derived stores: `connection`, `stats`, `accessPoints`, `handshakes`, `peers`,
`log`, `gps`, `face`, `capabilities`. Views subscribe; pushes from the plugin update them live.

### 4.5 Navigation model

Seven views, five slots. The bottom bar carries **Dashboard, Wi-Fi, Map, Log, More**; the More
control opens a sheet holding **Peers, Mirror, Settings**.

Log sits in the bar rather than Peers because a log is read while something is going wrong,
often one-handed and in a hurry, whereas the peer list is consulted deliberately.

**Every view is a route**, and the bar entries are anchors, not buttons:

| View | Route |
|---|---|
| Dashboard | `/` |
| Wi-Fi | `/wifi` |
| Map | `/map` |
| Log | `/log` |
| Peers | `/peers` |
| Mirror | `/mirror` |
| Settings | `/settings` |

Routes are not decoration. D14 and §2.15 make the static server serve `index.html` for any path
that resolves to no file, and that fallback has nothing to fall back *for* unless deep links
exist. The Wi-Fi segment is **not** in the URL: it is view state, not a location, and `/wifi`
must resolve without one.

Routing is hand-rolled in `lib/router.ts` against the History API. Seven static routes with no
parameters, no nesting and no guards do not justify a routing library, and every dependency here
is paid for over a Bluetooth link.

This is therefore site navigation and not a tab control. The bar is a `<nav>` whose current
entry carries `aria-current="page"`; because `aria-current` belongs on a link, the anchors above
are what makes the attribute correct rather than decorative. The fifth slot opens a dialog
rather than switching a panel, so a `tablist` role on the bar would be a lie. **While a
sheet-hosted view is current, the More entry carries `aria-current="page"`**, otherwise the
indicator vanishes from the bar entirely whenever the user is in Peers, Mirror or Settings.

The More sheet is a dialog and carries a dialog's obligations: `role="dialog"` with
`aria-modal="true"` and an accessible name; dismissed by Escape, by the backdrop, or by choosing
an entry; focus trapped while open and **returned to the More control on close**; `inert` at
rest, so its controls are neither focusable nor in the accessibility tree. Focus that is trapped
but never returned is the most common defect in this pattern and the one screen-reader users
notice first.

**Landscape.** The rail applies at exactly `@media (orientation: landscape) and (max-height:
560px)`; landscape taller than that keeps the bottom bar. The word "landscape" alone is not a
condition: `(orientation: landscape)` and `(min-aspect-ratio: 1/1)` disagree, and on iOS the
software keyboard flips `orientation` underneath the layout.

In that range the bar becomes a **72px rail on the leading edge** and the header collapses to
one line, keeping the unit name and the connection state. The 72px is content width **outside**
`env(safe-area-inset-left/right)`: the leading edge in landscape is exactly where that inset is
non-zero, and which side it lands on depends on the rotation direction, so a rail that ignores
it puts controls under the notch. Rail entries are **icon-only with an `aria-label`**; 72px does
not hold a label like "Dashboard" at a readable size, and a truncated one is worse than none.

Landscape exists for the Log and the Map, which are the two views that gain from width; every
other view keeps a reading measure rather than stretching.

**Wi-Fi is one view with two segments**, `Nearby` (access points currently seen) and `Captured`
(handshake files on disk). They were two separate views in an earlier draft and are merged
because they answer one question, what is on the air and what did we get, and because the fifth
slot is worth more to the Log.

The segmented control inside the view *is* a real `tablist`: roving tabindex, Arrow/Home/End
movement, **automatic activation** (selection follows focus), each tab owning a
`role="tabpanel"` labelled back by `aria-labelledby`. Both panels stay mounted so an expanded row
survives a switch, but **the inactive panel carries `hidden`**. Left merely present, it exposes
two tab panels to VoiceOver at once, which is the exact defect the role is being claimed to
avoid. `Nearby` is selected on first launch; the choice is kept while the app is open and is not
persisted across launches.

**Views stay mounted when navigated away from.** The Map keeps its viewport and zoom, and the
Log keeps its buffer and scroll position, rather than re-initialising on every visit. The cost is
memory for seven views; the alternative is a Leaflet instance that re-centres every time the user
checks something else, which reads as a bug.

### 4.5.1 Views

- **Dashboard**: text face + status card, mode badge (AUTO / **PASV** / MANU), uptime, battery
  (percentage, charging), temperature, channel, counters (APs / handshakes / peers), GPS source
  and fix indicator, connection banner. Controls: mode switch, PASV toggle (rendered only when
  `capabilities.pasv`), reboot, shutdown. **Mode switch, reboot and shutdown all use the
  two-step confirm** (D9 + D12) and all show the `restarting` state afterwards.
- **Wi-Fi**: one view, two segments (§4.5).
  - *Nearby*: live AP list, sortable by RSSI/channel, with client counts, encryption, vendor.
  - *Captured*: directory-backed handshake list (SSID, BSSID, time, size, GPS pin when tagged),
    with the `truncated` notice when the cap is hit. v1 is view-only; download-over-BT is
    roadmap.
  - Each segment badge shows the length of the list it names, and renders `500+` when
    `truncated` is set. **It is not required to agree with the Dashboard counters**, and on the
    reference hardware it will not: §2.5 defines `stats.handshakes` as `*.pcapng` only, so that
    it matches the number on the e-ink display (F15), while the Captured list is directory-backed
    over both extensions and includes legacy `.pcap` files. The list and `stats` also arrive on
    separate pushes, so they differ between frames even when the totals agree. The authoritative
    number for "how many handshakes does this unit have" is `stats.handshakesTotal`; the badge
    answers the narrower question of how many rows are in front of you.
- **Peers**: pwngrid peers with signal, last-seen, pwnd counters.
- **Map**: Leaflet plotting handshake sidecar positions plus the current location marker.
- **Log**: live tail with a text filter and an auto-scroll toggle.
- **Mirror**: full e-ink PNG via `get_screen`, manual refresh plus an auto ~5 s toggle.
- **Settings**: host list (add/edit/remove; BT `172.20.10.7` prefilled, USB `10.0.0.2`
  suggested with a note that it is desktop-only — an iPhone cannot use the USB gadget path),
  ws/http ports, optional token, quick-connect from history, diagnostics (last error, latency,
  negotiated plugin version).

### 4.6 Geolocation (`lib/geo.ts`)

When `stats.gps.piFix` is false, request browser Geolocation (with permission) and push
`gps_data` on a sane interval or on significant change. Never push while `piFix` is true — the
on-Pi fix, whether from bettercap or gpsd, always wins.

---

## 5. CI/CD (`.github/workflows/`)

### 5.1 `ci.yml` (push + PR)

- **plugin-lint**: `.github/check_plugin.py` (AST-based, adapted from the owner's
  `check_plugins.py`: parses without importing, verifies the `plugins.Plugin` subclass and the
  required metadata, and greps for committed secrets), then `python -m compileall plugin/`.
- **plugin-tests**: `pytest` on **Python 3.13**, the version the device ships (F26), and again on
  **3.11**, the floor upstream declares (F21). The device's version is the one that blocks a merge;
  the floor runs because somebody can be on an older image. No pwnagotchi installation required:
  the fake package in `tests/fakes/pwnagotchi_stub/` stands in. The coverage gate of §10.8 runs here and is what
  fails the build, not a warning. `tests/tools/test_certs.py` runs in the same job; the runner
  already has `openssl`.
- **language**: there is no automated check, deliberately. One was written and removed. A
  word-list matcher cannot tell an Italian word from an English one that happens to be spelled
  the same, and this repository legitimately contains `serve`, `come`, `fare`, `solo` and
  `prima` — `websockets.serve`, "types come from", "serve `index.html`". Even after pruning
  those, what remained caught nothing real while threatening to fail a build over prose.

  A check that cries wolf gets ignored, and an ignored check is worse than none: it converts a
  rule people follow into a rule people route around. **D16 still holds** — the repository is
  English only — but it is enforced by review (`companion-reviewer`), where a human or an agent
  can tell the difference. Do not reintroduce a word-list gate.
- **frontend**: `npm ci`, `svelte-check`, `tsc --noEmit`, `vitest run`, `npm run build`.
- **schemas**: `.github/check_schemas.py` — every schema is valid draft 2020-12, each message's
  `type` const matches its filename, incoming messages are flat while outgoing ones wrap their
  payload under `data` with a `timestamp`, `additionalProperties: false` throughout, every `$ref`
  resolves, and no definition in `common.json` is left unreferenced.
- **protocol-types**: `node tools/gen-protocol-types.mjs --check` — the generated types must be
  committed and in sync with the schemas. Drift here means the plugin and the PWA have stopped
  agreeing about the wire format, which is the exact failure the schema-first design prevents.
- **secret scan**: `.github/check_secrets.py` — PEM private keys, provider token shapes, and
  credential assignments carrying a real literal rather than a placeholder. A PEM block counts
  only when it has a plausible base64 body: a test that checks how malformed TLS material is
  rejected legitimately contains a header with junk inside it. It also refuses file types that
  must never be tracked at all, with one narrow exemption — `tests/fakes/fixtures/` may hold
  capture files, because the handshake listing cannot be tested without them. Those fixtures are
  synthetic by policy (§13), enforced by review and by the security auditor rather than by the
  scanner. Matched text is never echoed, because the match
  is the secret. Note what this job **cannot** do: the owner-specific infrastructure denylist
  lives outside the repository by design (§13), so that check is a local pre-commit gate only.
  CI catches secrets that look like secrets to anyone; it cannot catch a hostname it is not
  allowed to know, and a green run is not proof a diff is safe to publish.
- **no coverage exclusions**: `# pragma: no cover` is banned outright. The 100% branch gate only
  means something if nobody can opt out of it.
- **mutation** (scheduled, not a PR gate): `mutmut` over `plugin/`, reported not enforced.
  Mutation testing is too slow to block a pull request and too valuable to skip entirely.

### 5.2 `release.yml` (on tag `v*`)

Build the frontend, pack `frontend/dist` → `dist.tgz`, create a GitHub Release for the tag and
attach the archive. Include a `SHA256SUMS` asset. `tools/install-on-pi.sh` downloads the latest
release asset into `web_root`.

### 5.3 `tools/install-on-pi.sh`

    install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]
                     [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]
                     [--plugin-only] [--web-only] [--dry-run]

Runs **on the Pi**, as root. POSIX `sh`, `set -eu`, and nothing beyond `curl`, `tar` and
`sha256sum`. Non-interactive: it never prompts, so it is the same invocation by hand, from a
test and from a future CI job.

**It installs both halves, not just the web root.** §14 step 10 says to run this and then test
end to end from the phone, which cannot work if the plugin is not on the unit: a script that
installs the PWA and leaves `companion.py` to a manual `scp` is a script that produces a working
web server serving an app with nothing to talk to. So:

1. `plugin/companion.py` into the custom plugins directory. That directory is **resolved, never
   hardcoded**: read `main.custom_plugins` from `--config` (default `/etc/pwnagotchi/config.toml`,
   F24), fall back to the `defaults.toml` value `/usr/local/share/pwnagotchi/custom-plugins/`
   (F23). The two paths shipped with the image disagree (F25), so guessing picks the wrong one
   on some units and the plugin silently never loads.
2. The PWA into `--web-root` (default `/var/www/openpwn-companion`).

The archive comes from a GitHub Release: the latest, or the one named by `--tag`, or a local
file given with `--archive` (which skips the download and is how the tests drive it without a
network). The `SHA256SUMS` asset is fetched alongside and verified before anything is unpacked;
a mismatch is a hard failure that leaves the existing installation untouched, because an archive
that fails its checksum is either corrupt or hostile and there is no third case.

Extraction goes into a temporary directory beside the target and is swapped in only once
complete, so an interrupted download never leaves a half-written web root. The previous web root
is kept as `<web-root>.previous`, replacing any earlier one: the first thing anyone wants after
a bad update is the version that worked.

Idempotent and re-runnable. `--plugin-only` and `--web-only` do one half; passing both is a
usage error. `--dry-run` prints what would happen and writes nothing.

It does **not** edit `config.toml`. Enabling the plugin and setting its options is the user's
step, documented in `docs/SETUP.md`, because a script that rewrites the main configuration of a
running pwnagotchi can break far more than it installs.

Exit status: `0` success, `1` failure, `2` usage error. Anything non-zero leaves the installation
exactly as it was.

#### 5.3.1 The details that decide whether two people build the same thing

Written out because each one admits two readings, and a reading chosen in code is a decision
nobody else can see.

**Archive layout.** `dist.tgz` holds the contents of `frontend/dist` at the archive root, with
no `dist/` prefix: it is packed with `tar -czf dist.tgz -C frontend/dist .`, so `index.html` is
a top-level member and the installer never needs `--strip-components`.

**Checksums are verified always, including for `--archive`.** `SHA256SUMS` is expected beside
the archive, in `sha256sum` output format, and must list the archive's basename. A missing file,
a file that does not name this archive, or a digest that does not match is exit `1`. There is
deliberately no way to skip it: the local path is exactly how someone would install an archive
they did not build, and an unverified local file is not safer than an unverified downloaded one.

**Where the plugin comes from.** `dist.tgz` contains only the built PWA, so `companion.py` is
taken from the checkout the script lives in - `$(dirname "$0")/../plugin/companion.py`. If it is
not there, exit `1`.

**Archive members are validated before extraction, by type as well as by name.** Any member
whose path is absolute or contains a `..` component is refused with exit `1`, and nothing is
unpacked. GNU tar strips a leading `/` by default and would otherwise quietly write the rest
wherever it points, which on a script that runs as root is the whole traversal problem.

Names alone are not enough. A symlink member `assets -> /etc` followed by a regular member
`assets/passwd` has two entirely innocent names and escapes anyway, so **symlink and hardlink
members are refused outright** - a built PWA has no legitimate use for either. The refusal names
the member and says `link member`, because the two failures are distinguishable only by their
message and a test has to be able to tell them apart. An archive that fails to unpack is also
exit `1`.

**Extraction does not honour recorded ownership or modes** (`--no-same-owner`,
`--no-same-permissions`), and the installed modes are set afterwards. Running as root, tar would
otherwise reproduce whatever the packer recorded, setuid bit included. Note this rule can only
be observed as root: for an ordinary user tar already ignores recorded ownership and applies the
umask, so a test of it must skip rather than pass when not run as root.

**`--repo`, `--tag` and `--web-root` are validated because of where they end up.** `--repo` must
match `owner/name` and `--tag` must be a bare version tag, neither containing a `..` segment:
both are interpolated into a URL, and although the host cannot be moved, curl normalises `..`
away and would fetch a different repository on github.com - whose `SHA256SUMS` matches its own
archive and so verifies perfectly. Validating only one of the two leaves the same hole open under
another name. `--web-root` must be absolute:
the staging directory is `<web-root>.new` and is removed before use, so a relative or empty value
deletes something in whatever directory the script was started from. Both are exit `2`.

**Checksum entries match the recorded name as written**, not on its basename, and a second entry
for the same name is an error rather than a last-one-wins. A line for `other/dist.tgz` describes
a different file; letting it satisfy a bare `dist.tgz` is how a sums file for one build blesses
another.

**Resolving `main.custom_plugins`.** The key must be found in every spelling a real unit
carries: the flat dotted form `main.custom_plugins = "..."`, with or without spaces around `=`;
the same key inside a `[main]` table, including an indented table; and either quote style. A
commented-out line is not a match, and neither is `custom_plugins` under any other table - a
plugin of its own may legitimately have such a key. A trailing slash on the value is tolerated.
When the file is absent, or the key is not present in any of those forms, fall back to the F23
default and say which path was chosen and why on stdout. `--plugins-dir` overrides both.

**Updates replace, they do not merge.** The new tree is swapped in whole, so a file the previous
build shipped and the new one does not is gone afterwards. A stale asset served alongside a new
one is how a partially updated PWA fails in ways nobody can reproduce.

**`.previous` on a first install.** Nothing is kept, because there is nothing to keep: the
directory is created and no `.previous` appears beside it. On the second and every later install
`<web-root>.previous` holds the tree that was there before, replacing any earlier one. A failed
install does not rotate it, so `.previous` is always a version that worked.

**`--dry-run` verifies before it reports.** It performs every check a real run performs -
checksum, archive members, plugin source, path resolution - and stops before the first write. A
dry run that approves an archive the real run would refuse is worse than no dry run. It prints
the resolved plugins directory, web root and config path, which is also the only way to see
which side of the F23/F25 disagreement the script landed on without installing anything.

**Modes and ownership.** Installed PWA files are `0644` and directories `0755`, because the
HTTPS server has to read them; `companion.py` is `0644`. Ownership is left to whatever the
process running the script produces - the plugin runs as root on this image, and second-guessing
that from an installer is how permissions end up wrong in a way nobody notices until an update.

**Flag interactions.** `--plugin-only` does not need `--archive` and must not require it, since
it installs nothing from one. `--archive` together with `--tag` or `--repo` is a usage error
(`2`): they name two different sources and silently preferring one is how the wrong build gets
installed.

**Root is not checked, writability is.** The script does not test its effective uid. It attempts
the operation and reports what actually failed, because the euid check is both wrong in
principle - the targets may be writable without it - and fatal in practice: it would make the
entire test suite unrunnable as an ordinary user, and a test suite that cannot run is a test
suite that does not run.

---

## 6. GitHub project setup

An implementer with `gh` authentication performs these after the repo exists.

### 6.1 Labels (`.github/labels.json`, applied via `.github/sync_labels.sh`)

`.github/labels.json` is the taxonomy, and `.github/sync_labels.sh` applies it: without a
script nobody runs the file is decoration, the real taxonomy lives in GitHub's settings, and
the drift only surfaces when someone labels an issue with something the file never heard of.
The script creates or updates every label, and `--prune` reports what exists on the repository
but not in the file without deleting it, because removing a label strips it from every issue
that carries it with no undo.

Four groups, and an issue normally carries one from each of the first three:

- **type** — `type: bug`, `type: feature`, `type: docs`, `type: test`, `type: infra`,
  `type: security`. Each has its own colour.
- **area** — `area: plugin`, `area: frontend`, `area: protocol`, `area: tls`, `area: gps`,
  `area: ui`, `area: ci`. All share one colour, so the eye reads them as a set rather than
  trying to decode seven hues.
- **prio** — `prio: high` (blocks the milestone), `prio: med`, `prio: low`. Graded red to pale.
- **workflow and triage** — `blocked`, `good first issue`, `help wanted`, `accessibility`,
  `question`, `duplicate`, `invalid`, `wontfix`. These cut across the taxonomy on purpose;
  `accessibility` is a concern rather than an area, which is why it sits here and not next to
  `area: ui`.

### 6.2 Milestones

A milestone groups the work toward a deliverable; a tag marks a release. They are **not** the
same thing and are not named after each other. Version numbers advance through `0.x` while the
single milestone below stays open (§12).

**`Field-ready`** — open, **no due date**.

The name is deliberately not a version number. A milestone called `1.0` sitting next to a future
tag called `v1.0.0` reads as though they were the same object, and they are not: one is a body
of work, the other is a release you can install. Naming the milestone after what closing it
*means* keeps the two apart in the issue list and in conversation.

No due date either: a date on a spare-time project with no plugin yet goes red within weeks and
is then ignored, which is worse than having none. The exit criteria below are functional instead.

> *Everything needed for someone other than the author to install this on their own pwnagotchi
> and use it from their phone.*

That sentence is the perimeter. Anything that does not serve it belongs in the backlog.

**In scope**

| Area | Work |
|---|---|
| Plugin | Config schema; TLS and address binding with the rebind loop (§2.3); `websockets` compatibility shim; optional token auth; the full message contract (§2.4); `stats`, access points, handshakes, peers, log, screen mirror, face status; the pushes; all four controls including the restart path; battery detection; the GPS abstraction and `.gps.json` sidecars; the HTTPS static server with explicit MIME and SPA fallback |
| Tooling | `gen-ca.sh`, `gen-cert.sh`, `install-on-pi.sh` |
| Frontend | The seven views and the navigation model (§4.5), `lib/` (generated `protocol.ts`, `ws`, `stores`, `settings`, `geo`), PWA manifest, service worker, iOS specifics (§4.2) |
| Tests | The whole of §10, including the 100% branch-coverage gate |
| CI | `ci.yml`, `release.yml`, `check_plugin.py`, schema validation, protocol type sync, secret scan |
| Docs | `SETUP.md`, `CERTIFICATES.md`, `PROTOCOL.md`, `PINNED-FACTS.md`, README, ROADMAP, CONTRIBUTING, issue templates |

**Out of scope** — backlog, no milestone: pcap download over BT, handshake filtering and search,
map clustering and tracks, offline tile cache, theme options, multi-device switcher, WiGLE/CSV
export, wpa-sec upload. Web push is closed outright (D10).

**Exit criteria** — all must hold, and each is checkable rather than a judgement call:

1. A person following `docs/SETUP.md` alone, with no help from the author, gets from a bare Pi
   to an installed PWA. If a step only works because the author knew something, the document is
   not finished.
2. All seven views work against a real pwnagotchi, from an iPhone, over the BT PAN link, in
   both orientations, and reachable through the bottom bar and the More sheet.
3. All four controls exercised on real hardware — including a mode switch, which restarts the
   service and drops the socket, and including the reconnect that follows.
4. Every CI job green, the coverage gate included.
5. No open `BLOCK` finding from `companion-reviewer` or `companion-security-auditor`.

Closing `Field-ready` is what earns the `v1.0.0` tag; criterion 3 is the field test §12 makes a
precondition of leaving `0.x`.
- **Backlog** (no milestone): everything deferred. Items are promoted into a milestone when
  someone actually starts them, not before — an open milestone nobody is working in is noise in
  the issue list. Currently held here:
  - *Comfort*: pcap download over BT, handshake filtering and search, richer map (clustering,
    track), offline tile cache, theme options.
  - *Reach*: multi-device switcher, wardriving export (WiGLE/CSV), wpa-sec upload trigger.
  - Web push stays explicitly closed (D10) — record a wontfix note rather than leaving it open.

### 6.3 Seed issues (created under the `Field-ready` milestone, labelled)

One issue per §2 backend capability, per §4 view, plus the TLS scripts, CI, docs, the test suite,
the schema pipeline, and the GitHub setup itself. Each issue carries a crisp title and acceptance
criteria lifted from this spec, with area and type labels. A tracking/epic issue links them all.
`ROADMAP.md` is populated from the milestones.

### 6.4 Issue templates, CONTRIBUTING, SECURITY

`bug_report.yml` (device, pwnagotchi version, plugin version, iOS version, cert setup, logs),
`feature_request.yml`. `CONTRIBUTING.md` covers the English-only rule, the AST/parse CI rules,
the schema-first protocol workflow, and the requirement that changes ship with tests.
`SECURITY.md` states the threat model (tether-local exposure, private CA, optional token) and
the hard rule: no private keys, CA keys, tokens, or pwnagotchi whitelists in the repository,
ever.

---

## 7. README (top level)

What it is (free PWA companion, self-hosted), a screenshot placeholder, the honest security
model (a private CA and a fully trusted profile are required, and why), a feature matrix against
the paid app (parity plus the three gaps plus GPS/map and mirror), a quickstart pointing at
`docs/SETUP.md`, the each-user-builds-their-own note, and GPL-3.0 with attribution to
`BraedenP232/PwnIOS`.

---

## 8. ROADMAP.md

Narrative of the §6.2 milestones, each with its issue list, and a clearly marked "Not planned"
section for web push with the one-line reason (only useful while tethered with the app open).

---

## 9. Anticipated pitfalls (read before coding)

1. **`bssid` vs `mac`** — bettercap AP dicts key on `mac`; the upstream plugin's `bssid` reads
   return empty. Fixed in §2.5.
2. **`get_access_points()` side effects** — it fires `plugins.on('wifi_update')`, calls
   `_epoch.observe`, and drives strategy channel selection. Read `_access_points`.
3. **`agent.session()` blocks** — a 30-second-timeout HTTP call. Never on the request path.
4. **`set_mode` is a service restart** — assigning `agent.mode` changes a label, nothing more.
5. **Peers are objects** — `pwnd_total()` not `pwnd_tot`, `last_channel` not `channel`, and
   `last_seen` is a float while its siblings are `datetime`.
6. **`on_handshake` has two argument shapes** — str/str or dict/dict.
7. **Face is text** — no PNG face files on a stock text-face setup. Render text. The full-screen
   PNG is a separate thing (`frame_path`).
8. **Mixed content** — an HTTPS PWA may only open `wss://`, never `ws://`. Both listeners share
   one cert.
9. **`iPAddress` SAN** — Safari validates the IP against `IP:` SANs; a `DNS:` entry holding an
   IP is silently wrong.
10. **`basicConstraints` must be critical on the CA** — without it, iOS will not offer the
    profile for full trust, HTTPS then fails silently and no service worker registers.
11. **825-day cap** — longer server certificates are rejected by iOS.
12. **The tether address may not exist at load** — the PAN link often has no address until the
    tether is up. Re-enumerate on a timer, skip without crashing, rebind on change, never fall
    back to `0.0.0.0`. Do not identify the link by interface name: `bnep` numbering follows the
    order peers connect, so on a unit paired with more than one machine `bnep0` is whichever
    one got there first (D5.1).
13. **Log and handshake paths differ by image** — always read from config. This fork's log
    default is `/etc/pwnagotchi/log/pwnagotchi.log`.
14. **`.webmanifest` MIME** — unknown to a minimal image's mimetypes DB; declare it explicitly
    or iOS refuses to install the app.
15. **SPA deep links** — `SimpleHTTPRequestHandler` returns 404; fall back to `index.html`.
16. **`websockets` API drift** — handler signature and `serve` location differ across major
    versions; write tolerantly (§2.3.2).
17. **BT bandwidth** — keep payloads small. The screen PNG and any pcap transfer are on demand,
    never streamed. The face is text, not base64.
18. **PASV only in auto** — reject `set_pasv` outside auto; hide the control when `pasv_mode`
    is absent (soft dependency, no import). `plugins.on()` is async, so do not read the state
    back immediately.
19. **No plaintext fallback** — if the certificate is missing, log and stay down. Do not
    "helpfully" serve HTTP.
20. **USB is desktop-only** — an iPhone cannot use the `usb0` gadget path. Support the IP in
    the cert and settings, but do not advertise it as an iPhone option.

---

## 10. Test suite

Tests are part of the deliverable, not a follow-up. Every task in §14 lands with its tests.

### 10.1 Principles

- The plugin is tested **without a pwnagotchi installation**. `tests/fakes/pwnagotchi_stub/` is
  a minimal importable package injected into `sys.modules` by `conftest.py`, exposing exactly
  the symbols pinned in §11 and nothing else. If the plugin reaches for a symbol the stub does
  not provide, the test fails — which is precisely the guard against inventing APIs.
- `FakeAgent` provides `_access_points`, `_handshakes`, `_peers`, `mode`, `_current_channel`,
  `_config`, and a `session()` that returns a fixture and **records how many times it was
  called**, so the "never call session() on the request path" rule is machine-checked.
- Fixtures are real captured shapes: a bettercap `session` JSON, a handshake directory with
  `.pcap`, `.pcapng`, a sidecar, and a deliberately malformed name; a log file large enough to
  exercise the bounded tail.

### 10.2 Unit tests (`tests/`)

| File | Covers |
|---|---|
| `test_stats.py` | field-by-field `stats` mapping; `pasv_or_auto` across all four states; `capabilities` reflects `plugins.loaded`; **asserts `session()` call count is 0** during `get_stats` |
| `test_access_points.py` | `mac`→`bssid` mapping; `clients: None`; missing keys; empty list |
| `test_handshakes.py` | SSID containing underscores; `.pcap` and `.pcapng`; unparseable name yields `bssid: null`; sidecar present/absent/malformed; 500-entry cap sets `truncated`; unreadable file skipped; both `on_handshake` argument shapes normalise identically |
| `test_peers.py` | every field in the §2.8 table; the `datetime` vs float `last_seen` asymmetry; a `Peer` whose timestamp parsing fell back to `str`; missing `adv` keys |
| `test_gps.py` | source priority order; `gps_source` config overrides; `0.0/0.0` is not a fix; browser staleness expiry; sidecar written in bettercap shape (D13); no sidecar without a fix; existing sidecar not overwritten; `.pcap` gets the right sidecar name |
| `test_log_tail.py` | bounded tail returns the last N; default and cap; file smaller than one chunk; undecodable bytes; missing file yields `log_unavailable` |
| `test_battery.py` | each detection tier; every tier failing yields nulls and raises nothing |
| `test_controls.py` | `set_mode` calls `pwnagotchi.restart` with `"AUTO"`/`"MANU"` and **never assigns `agent.mode`**; broadcasts `restarting` before restarting; no-op when already in the mode; `set_pasv` errors outside auto and when `pasv_mode` is absent, emits the right event otherwise, and does not read `passive` back; reboot/shutdown call the module-level functions |
| `test_tls_startup.py` | missing cert, missing key, unreadable key and malformed PEM each result in **zero listeners** and a logged error — the no-plaintext-fallback rule |
| `test_auth.py` | token accepted, rejected, wrong type, missing; `compare_digest` used; unauthenticated connection closed after `auth_timeout`; auth skipped when no token configured |
| `test_binding.py` | selection: literal match, containment in a block, a local address matching nothing; every entry-validation row of §2.3.1, including the `/24` floor, host bits normalised, a non-list value, and the empty-after-rejection case; a legacy `interfaces` key warns and binds nothing; the reconciliation table: appear, disappear, a new address in the same block, none matching, `EADDRINUSE`; asserts `0.0.0.0` is never passed to a bind call and that no bind decision reads an interface name |
| `test_static_server.py` | `.webmanifest` and `.js` content types; SPA fallback serves `index.html`; `..` rejected with 400; no directory listing; `no-cache` on index and service worker |

### 10.3 Protocol conformance (`tests/test_protocol_conformance.py`)

Every outgoing message the plugin can produce is generated against the fixtures and validated
with `jsonschema` against `docs/schemas/outgoing/<type>.json`. Every incoming schema is exercised
with a valid and an invalid sample. A message type present in the plugin but absent from
`docs/schemas/` fails the test, and vice versa — the two cannot drift apart.

### 10.4 Integration (`tests/test_integration_ws.py`)

Generate a throwaway CA and server certificate in a `tmp_path` (reusing `tools/gen-ca.sh` and
`tools/gen-cert.sh` so the scripts themselves are exercised), start the plugin's WSS server on
`127.0.0.1` against a `FakeAgent`, connect with a real `websockets` client that trusts the
throwaway CA, and drive every command in §2.4 end to end, asserting the reply type and schema.
Also covers: unknown command, malformed JSON, `message_id` echo, heartbeat, and a client
disconnecting mid-broadcast without taking the server down.

This is the test that catches `websockets` API drift (§2.3.2) on whatever version CI resolves.

### 10.5 Frontend (`frontend/src/__tests__/`, vitest)

- `ws.spec.ts` against a mock WebSocket: backoff with jitter, queue drain on reconnect,
  `acknowledgment` resolution, `message_id` correlation, heartbeat timeout forcing a reconnect,
  auth frame sent first, `restarting` entering the dedicated state instead of the error banner.
- `stores.spec.ts`: each push message updating the right store; `capabilities.pasv` gating the
  PASV control.
- `settings.spec.ts`: persistence round-trip, defaults, migration of an unknown shape.
- `protocol.spec.ts`: sample payloads from `docs/schemas` examples typecheck against the
  generated types.
- `navigation.spec.ts`: the shell's own logic, which §10.7 would otherwise leave uncovered
  because it excuses view components from coverage. That exclusion was written for list markup
  and does not hold for navigation: current-view selection and `aria-current` including the More
  slot standing in for a sheet-hosted view, sheet open, Escape, backdrop and choose-an-entry
  dismissal with focus returned to the More control, segment roving tabindex and arrow movement
  with the inactive panel `hidden`, the rail breakpoint driven by a `matchMedia` mock, and views
  surviving navigation with their state intact.

**Playwright is in scope**, reversing an earlier line that ruled it out for v1. The reason is
evidence: a set of behaviour and geometry checks written against an interactive mockup found
defects that three rounds of human review had missed, and caught two regressions introduced while
fixing them. Those checks are worth more as a gate than as a discarded prototype.

- `frontend/tests/e2e/`, run against the built `dist/` in **Chromium and WebKit**, both
  orientations at 402x874 and 874x402. WebKit is not Safari, but it is the same engine family,
  and the class of defect that hurt here (viewport units, nested contexts, safe areas) is that
  family's.
- Geometry invariants rather than pixels: nothing painted below the content area in either
  orientation, the map never owning the full width or height of the views area so a drag always
  has somewhere to scroll the app, touch targets at 44px, contrast computed for every token
  against every surface it can land on.
- **Screenshot baselines are not committed.** They are platform-specific, CI runs amd64 and the
  review host is arm64, so a shared baseline fails on font rendering alone and a gate nobody
  trusts is worse than no gate. Screenshots are uploaded as workflow artifacts for human review
  instead.
- Known harness limitation: headless WebKit paints nothing in a full-viewport screenshot when the
  shell is `position: fixed` (issue #40, ruled out on real Safari). Element screenshots and the
  geometry assertions are unaffected; a blank WebKit screenshot is not a product defect.

### 10.6 Tools (`tests/tools/test_certs.py`)

Written in pytest, not bats: the scripts are driven with `subprocess` and inspected with
`openssl`, which removes a dependency and puts every Python-side result under one coverage
report.

Run `gen-ca.sh` and `gen-cert.sh` into a `tmp_path`, then assert with
`openssl x509 -noout -text`:

- the CA has `X509v3 Basic Constraints: critical` containing `CA:TRUE`;
- the server certificate's SAN contains `IP Address:` entries for every argument, and **no**
  `DNS:` entry holding an IP literal;
- `notAfter - notBefore <= 825 days`;
- the server certificate verifies against the CA (`openssl verify -CAfile`);
- `gen-ca.sh` run twice does not clobber the key without `--force`;
- `gen-cert.sh` rejects a hostname argument.

These three certificate assertions encode the exact failures already hit on this setup once.

### 10.7 Coverage gate

Every line and every branch of `plugin/` and `frontend/src/lib/` is covered. This is enforced,
not aspired to:

- `pytest --cov=plugin --cov-branch --cov-fail-under=100`. **Branch** coverage, not line
  coverage: `if a and b` has four outcomes, not one.
- **`# pragma: no cover` is banned.** A CI grep fails the build on any occurrence. Without that
  rule the gate is trivially defeated and the number becomes a lie.
- Vitest thresholds set to 100 for lines and branches on `frontend/src/lib/`. Svelte view
  components are excluded — they are exercised by hand, and a coverage number for markup buys
  nothing.

Two honest limits, and what is done about each:

1. **100% branch coverage is not "every possible case."** It proves every path was executed,
   not that the assertions were meaningful. The complement is **mutation testing** (`mutmut`,
   §5.1): it mutates the source and checks the suite notices. Reported on a schedule, not on
   every pull request, because it is slow.
2. **Enumerated cases miss what nobody thought of.** The complement is **property-based
   testing** (`hypothesis`) on the parsers, which is where unenumerated inputs actually bite:
   handshake filename parsing, the bounded log tail, HDOP conversion, gpsd TPV parsing. State
   the invariant and let the machine hunt the counterexample.

**Design consequence, binding on the implementer:** every external effect sits behind an
injectable seam — the clock, the gpsd socket, the I2C bus, `subprocess`, local address
enumeration (`Deps.list_local_ipv4() -> list[str]`, §2.3.1), thread spawning, and
`pwnagotchi.restart`/`reboot`/`shutdown`. Without those seams,
full branch coverage of the I2C and gpsd paths is unreachable and the gate would have to be
weakened. Constructor injection with real defaults; no monkeypatching of module internals from
the tests.

Two things are deliberately **not** seams on `Deps`. The filesystem is reached through
configured paths, so a test points them at a `tmp_path` and needs no indirection. The
`websockets` server factory is resolved at call time by `resolve_ws_serve()` and the connection
handler is passed to `Listeners`, because the transport is covered by the integration test
(§10.4) rather than by unit tests — putting it on `Deps` would widen the seam surface without
buying a single covered branch.

### 10.7.1 Where the acknowledgment comes from

`Router.handle` emits the `acknowledgment` when the incoming message carried a `message_id`, and
the individual command methods (`set_mode`, `set_pasv`, `reboot`, `shutdown`) return only their
own output — a broadcast, or nothing. Tests assert ordering through `handle`, never by calling a
command method directly and expecting an acknowledgment from it.

### 10.8 Definition of done

A task is complete when: its tests pass locally and in CI, the §10.7 coverage gate is green,
`check_plugin.py` passes, the schema and protocol-type checks are clean, the
`companion-reviewer` and `companion-security-auditor` agents have signed off, and the acceptance
criteria in its issue are satisfied. Not before.

---

## 11. Pinned Facts (verified against `jayofelony/pwnagotchi` v2.9.5.6)

**This is the allowlist.** Symbols not listed here are out of bounds. Line numbers refer to the
v2.9.5.6 tree and are indicative; the names and semantics are what matter.

**This table has a machine-checked twin.** `.github/pinned_symbols.json` encodes the part of it
a script can confirm, and `.github/check_pinned_facts.py` runs weekly against current upstream and
opens a ticket when something no longer holds. **Editing a fact here means editing that file too**,
and `tests/tools/test_pinned_facts.py` fails if a fact gains an entry in one and not the other -
two allowlists drifting apart is the failure the checker exists to prevent, one level up.

| ID | Fact | Evidence |
|---|---|---|
| F1 | `pwnagotchi.uptime()` → int seconds, reads `/proc/uptime` | `pwnagotchi/__init__.py` |
| F2 | `pwnagotchi.temperature(celsius=True)` → int °C from `/sys/class/thermal/thermal_zone0/temp` | `pwnagotchi/__init__.py` |
| F3 | `agent._access_points` is a list of bettercap AP dicts. `agent.get_access_points()` calls `set_access_points()`, which fires `plugins.on('wifi_update')`, caches, calls `_strategy.on_wifi_update` and `select_next_channels`, and `_epoch.observe`. **Do not call it.** | `pwnagotchi/agent.py:191-222` |
| F4 | Bettercap AP dicts carry `mac`, `hostname`, `channel`, `rssi`, `encryption`, `vendor`, `clients`. There is no `bssid` key. | `pwnagotchi/agent.py:203-222` (`ap['mac']`, `ap['hostname']`, `ap['encryption']`) |
| F5 | `agent._peers` is a `dict` of identity → `Peer`, initialised in the `AsyncAdvertiser` mixin | `pwnagotchi/mesh/utils.py:30`; `pwnagotchi/agent.py:200,297` |
| F6 | `agent._current_channel` is a plain attribute; `agent.get_current_channel()` is defined **twice** in `agent.py` and the second definition (returning the attribute) wins | `pwnagotchi/agent.py:153` and `:230` |
| F7 | `agent.session()` performs `requests.get` against the bettercap REST API with `api_timeout = 30`. It is synchronous and blocking. | `pwnagotchi/bettercap.py:26,52-56` |
| F8 | `plugins.on(event_name, *args)` enqueues work on each plugin's thread and returns nothing; dispatch is asynchronous | `pwnagotchi/plugins/__init__.py:54,165` |
| F9 | Module-level `pwnagotchi.shutdown()`, `pwnagotchi.reboot(mode=None)` and `pwnagotchi.restart(mode)` exist. `restart` uppercases `mode`, touches `/root/.pwnagotchi-auto` or `/root/.pwnagotchi-manual`, then restarts the `bettercap` and `pwnagotchi` services. | `pwnagotchi/__init__.py:110,129-140,142` |
| F10 | Mode is selected at process start: `cli.py` calls `do_manual_mode(agent)` or `do_auto_mode(agent)` based on `--manual`, and those set `agent.mode = 'manual'` / `'auto'` as a label. Nothing re-reads `agent.mode` to change behaviour. | `pwnagotchi/cli.py:24-27,45-48,208-215`; `pwnagotchi/agent.py:56` |
| F11 | The `agent.mode == "manual"` comparison is the fork's own convention | `pwnagotchi/plugins/default/webcfg.py:648` |
| F12 | `pasv_mode.py` v1.3.0, class `PasvMode`, defines the attribute `self.passive` and the handlers `on_pasv_toggle`, `on_pasv_on`, `on_pasv_off`; the plugin registers under the key `pasv_mode` in `plugins.loaded` | [`abonforti/pwnagotchi-plugins`](https://github.com/abonforti/pwnagotchi-plugins/blob/master/pasv_mode.py) |
| F13 | The stock `webcfg` plugin switches mode with `threading.Thread(target=restart, args=(self.mode,), daemon=True).start()` | `pwnagotchi/plugins/default/webcfg.py:679` |
| F14 | The handshake directory is `config['bettercap']['handshakes']`, default `/etc/pwnagotchi/handshakes`; the agent creates it if missing | `pwnagotchi/defaults.toml:231`; `pwnagotchi/agent.py:58-59,89` |
| F15 | `pwnagotchi.utils.total_unique_handshakes(path)` globs `*.pcapng` only | `pwnagotchi/utils.py:520-522` |
| F16 | `Peer` members: attributes `first_met`, `first_seen`, `prev_seen` (`datetime`, or `str` on a parse fallback), `last_seen` (float epoch), `encounters`, `session_id`, `last_channel`, `rssi`, `adv`; methods `inactive_for()`, `first_encounter()`, `is_good_friend()`, `face()`, `name()`, `identity()`, `full_name()`, `version()`, `pwnd_run()`, `pwnd_total()`, `uptime()`, `epoch()`, `is_closer()` | `pwnagotchi/mesh/peer.py` |
| F17 | Log path is `config['main']['log']['path']`, default `/etc/pwnagotchi/log/pwnagotchi.log` | `pwnagotchi/defaults.toml:138-140` |
| F18 | `pwnagotchi.ui.web` is a **package** whose `__init__.py` defines `frame_path = '/var/tmp/pwnagotchi/pwnagotchi.png'`, `frame_format = 'PNG'`, `frame_ctype = 'image/png'`, and `frame_lock = threading.Lock()` | `pwnagotchi/ui/web/__init__.py` |
| F19 | Bettercap GPS lives at `session()['gps']` with capitalised keys `Latitude`, `Longitude`, `Altitude`. The stock plugin skips writing when latitude or longitude is falsy, and dumps the raw dict to `<name>.gps.json`. `Updated`, `FixQuality`, `NumSatellites` and `HDOP` come from **bettercap's** payload, not from any pwnagotchi source, so they are used defensively and cannot be verified against this repository. | `pwnagotchi/plugins/default/gps.py:47-61` for the first three |
| F20 | `on_handshake` is fired from two paths with different argument types: `plugins.on('handshake', self, filename, ap_mac, sta_mac)` (strings) and `plugins.on('handshake', self, filename, ap, sta)` (dicts) | `pwnagotchi/agent.py:396,404` |
| F21 | `pyproject.toml` declares `websockets` with **no version constraint**; `requires-python = ">=3.11"`. That is a **floor, not the shipped version** - see F26 and F27 | `pyproject.toml` |
| F26 | The image installs its Python under `/opt/.pwn/`, and that tree is **3.13**, not the `>=3.11` floor of F21. CI must test the shipped version | `stage3/06-patches/files/01-motd` reads `/opt/.pwn/lib/python3.13/site-packages/pwnagotchi/_version.py`; `/opt/.pwn/bin` is likewise hardcoded in `stage3/06-patches/files/profile`, `sudoers` and `pwnagotchi-launcher`. Confirmed as 3.13.5 on a unit running 2.9.5.6 |
| F27 | The **installed `websockets` version is not knowable from upstream** and must not be assumed. `pyproject.toml` pins nothing (F21), so it is whatever pip resolved when that image was built, and `plugins/default/auto-update.py` runs `pip install` on the unit afterwards. One unit running 2.9.5.6 was observed at 17.0.1 - evidence that a modern major occurs in the field, not that every unit has one | `pwnagotchi/plugins/default/auto-update.py`; `pyproject.toml` |
| F22 | Upstream `pwnios.py` binds `websockets.serve(self._handle_client, "0.0.0.0", 8082, ...)` and reads `self.agent.access_points` before falling back to `_access_points` — both are bugs this fork fixes | `BraedenP232/PwnIOS/pwnios.py:292-293,653-656,678-680` |
| F23 | Custom plugins are loaded from `config['main']['custom_plugins']`, whose default is `/usr/local/share/pwnagotchi/custom-plugins/`. The key is optional: `load_from_path` is called only `if 'custom_plugins' in config['main']` | `pwnagotchi/defaults.toml:27`; `pwnagotchi/plugins/__init__.py` |
| F24 | The user configuration the image merges over the defaults is `/etc/pwnagotchi/config.toml` (`--user-config`), and `plugins/__init__.py` writes back to that same path | `pwnagotchi/cli.py`; `pwnagotchi/plugins/__init__.py` |
| F25 | The shipped shell profile aliases `custom` to `/etc/pwnagotchi/custom-plugins/`, which is **not** the `defaults.toml` value. The two disagree, so the directory must be resolved from the running configuration and never hardcoded | `stage3/06-patches/files/profile` vs `pwnagotchi/defaults.toml:27` |

### 11.1 Explicitly forbidden

Do not use, do not invent, do not "improve" into existence:

- `agent.access_points`, `agent.handshakes`, `agent.peers` (no-underscore forms) — false here.
- `agent.get_access_points()` — side-effecting.
- `agent.reboot()`, `agent.shutdown()` — do not exist.
- `agent.set_mode()`, `agent.switch_mode()` — do not exist.
- `peer.channel`, `peer.pwnd_tot`, `peer.fingerprint` — wrong names.
- `pwnagotchi.grid.peers()` for `peers_list` — different shape, excluded by §2.8.
- `from pwnagotchi.ui.web import server` for the frame — the frame lives in the package
  `__init__`.
- Importing `pasv_mode`, `pisugarx`, or `pisugarx_ext` directly.
- Any hardcoded `/var/log/pwnagotchi.log`, `/etc/pwnagotchi/handshakes`, or port number that
  bypasses the config.
- `0.0.0.0`, `::`, or a hostname in any bind call.
- An interface name as the reason for a bind. Names are read only while enumerating the host's
  addresses, and the name must not survive into the selection (D5.1).

If you need something in this list, the answer is a spec change requested from the owner, not a
workaround.

---

## 12. Versioning and releases

One version line for the whole project. The plugin, the frontend package and the git tag always
carry the same number; there is no separate plugin version. The fork lineage from `pwnios.py`
is credited in the plugin header and the README, which is where provenance belongs — encoding
it in a version number would mean maintaining two numbering schemes for one artifact.

**Scheme: SemVer**, tags `vMAJOR.MINOR.PATCH`, with `-rcN` for pre-releases.

**The project starts at `v0.0.1` and stays in `0.x` until the protocol has been exercised
against a real device.** SemVer treats major version zero as explicitly unstable, which is an
accurate description of a wire contract that has never carried a byte. Claiming `1.0.0` before
that would be a promise of stability nobody has earned.

While in `0.x`:

| bump | when |
|---|---|
| MINOR (`0.1.0`) | a breaking change to the WebSocket contract — a removed, renamed or re-shaped message, or a config key no longer honoured |
| PATCH (`0.0.2`) | everything else: new messages, new views, new capabilities, fixes, docs |

`1.0.0` is cut when, and only when, all of the following hold: the v1 feature set (D8, D9) is
complete, the protocol has run end to end against a real pwnagotchi and a real iPhone, and the
owner is willing to keep the contract stable. At that point the normal rules take over:

| bump | when |
|---|---|
| MAJOR | a breaking change to the WebSocket contract or to config compatibility |
| MINOR | a new message type, view, capability, or config key with a default |
| PATCH | fixes and documentation, no contract change |

A breaking change inside `0.x` is permitted, but it is never silent: it bumps MINOR and it is
listed under `Changed` or `Removed` in `CHANGELOG.md`. The rule that message types are added
rather than repurposed holds throughout — a client and a plugin are deployed separately at every
version, so silently changing the meaning of an existing type breaks a user who updated only
one of the two.

Because the plugin and the PWA are versioned together but **deployed separately** — the user
updates the plugin by hand and the PWA with `install-on-pi.sh` — a mismatch is normal and must
degrade gracefully, never crash:

- `stats.capabilities.pluginVersion` reports the plugin version to the client.
- A client newer than the plugin disables the features the plugin lacks rather than firing
  commands that come back `unknown_command`.
- A client older than the plugin ignores message types it does not know. The plugin therefore
  **adds** message types and never repurposes an existing one within a MAJOR.

Release mechanics:

- Tagging is manual and deliberate; there is no auto-tagging on merge.
- Pushing a `v*` tag triggers `release.yml` (§5.2), which builds `dist.tgz` and creates the
  GitHub Release with a `SHA256SUMS` asset.
- CI asserts on tag that `__version__` in `plugin/companion.py`, the `version` in
  `frontend/package.json` and the tag itself all agree. A mismatch fails the release rather
  than shipping a mislabelled artifact.
- `CHANGELOG.md` follows Keep a Changelog, with an `Unreleased` section maintained as changes
  land, so cutting a release is renaming a heading rather than reconstructing history.
- `v0.0.1` is the first tag: the scaffold, the protocol schemas and the tooling, with no working
  plugin yet. Tags then advance through `0.x` as work lands — there is no obligation to tag
  every merge, only what is worth installing. `v1.0.0` is cut when the **`Field-ready` milestone** (§6.2)
  closes, which by definition includes the end-to-end test against real hardware.

## 13. Repository hygiene and the infrastructure rule

This repository is public. The owner's infrastructure is not.

**Never committed, without exception:** hostnames, SSH aliases and jump hosts, machine names,
private network addresses and topology, key material of any kind, tokens, the owner's
pwnagotchi whitelist, and real capture data (a `.pcap`, a `.gps.json`, or a log excerpt
containing real SSIDs, BSSIDs or coordinates is location data about a person).

Generic protocol constants are **not** infrastructure and stay: `172.20.10.0/28` is the fixed
subnet every iPhone Personal Hotspot uses, and `10.0.0.2` is the stock pwnagotchi USB gadget
address documented upstream. Neither identifies anyone. The distinction is "does this describe
*this owner's* setup", not "is this an IP address".

Enforcement, in layers, because a single gate gets forgotten:

1. `.gitignore` blocks the obvious filenames before they can be staged.
2. The **`companion-security-auditor`** agent inspects every diff before it becomes a commit,
   checking both generic secret patterns and an owner-specific denylist.
3. That denylist lives **outside this repository** — it names the very things that must not be
   published, so committing it would be the leak it exists to prevent. It is read from the
   owner's local Claude configuration directory, and neither its contents nor its path appear
   in any tracked file.
4. CI runs a generic secret scan. It cannot run the denylist check, by construction: the runner
   is not allowed to know the terms. CI is the backstop for generic key material only; the
   personal-infrastructure gate is local and must not be assumed to exist in CI.

If an agent needs to know how the device is reached in order to do a task, the answer is to ask
the owner, in the conversation, and not write it down.

### 13.1 Commit signing

**Every commit authored by the owner is GPG-signed.** No exception, including commits an agent
makes on the owner's behalf: `commit.gpgsign` is enabled, so this happens by default, and an
agent must never disable it or reach for `--no-gpg-sign` to get past a problem. A signing
failure is something to report, not to route around.

This is not ceremony. The repository is public and its whole security posture rests on the
owner's own commits being distinguishable from anything else that might reach the branch.

**Contributors are not required to sign.** Demanding a GPG setup as the price of a first pull
request drives away exactly the drive-by contribution a small project wants. That is reconciled
with the signature requirement on `master` by merge policy rather than by exemption:

- The `master` ruleset requires signed commits.
- Pull requests are **squash-merged**. GitHub creates the resulting commit and signs it with its
  own key, so an unsigned contribution still lands on `master` as a signed commit.
- The contributor's authorship is preserved in the squash commit's author field; only the
  committer is GitHub.

The practical rule: unsigned commits may exist on a branch, never on `master`.

Verification is worth checking once rather than assuming: a signed commit still shows as
unverified if the key is not on the GitHub account, or if the commit's author e-mail is not one
of that key's verified addresses. Both must line up, and a key that expires takes future
signatures with it.

## 14. Execution order for the implementer

Each step ships with the tests named in §10 and is not done until they pass (§10.7).

1. Scaffold: `LICENSE`, `README` skeleton, `.gitignore`, `SECURITY.md`, directory tree.
2. `docs/schemas/` + `docs/PROTOCOL.md` + `tools/gen-protocol-types.mjs`. **The protocol comes
   first** — both sides are generated from and tested against it.
3. `plugin/companion.py`: config, TLS and binding (§2.3), the WS loop, the full contract, and
   every item in §2.14. Tests §10.2 and §10.3 alongside.
4. `tools/gen-ca.sh`, `gen-cert.sh`, `install-on-pi.sh`; `docs/CERTIFICATES.md`, `docs/SETUP.md`.
   Tests §10.6.
5. `tests/test_integration_ws.py` — plugin and certificate scripts exercised together.
6. Frontend scaffold (Vite + Svelte + TS + PWA), then **the navigation shell** (§4.5: routes,
   bottom bar, landscape rail, More sheet, theme, safe areas), then `lib/` (generated
   `protocol.ts`, `ws`, `stores`, `settings`, `geo`), then the seven views. Tests §10.5.
   The shell comes before the views on purpose: it is where every layout defect this project has
   hit so far lives, and it is far cheaper to get right against one placeholder view than to
   retrofit under seven.
7. `.github/check_plugin.py`, `release.yml` (CI landed early, see §5.1).
8. README, ROADMAP, CONTRIBUTING, issue templates, `docs/PINNED-FACTS.md`.
9. GitHub project: labels, milestones, seed issues, epic (via `gh`).
10. Tag a `0.x` release → the release workflow builds `dist.tgz`; run `install-on-pi.sh` on the Pi;
    end-to-end test from the iPhone (trust the CA, install the PWA, connect, exercise every view
    and control).

**Access constraint for any live testing:** the owner's device is reached in a way deliberately
not recorded in this repository. Ask the owner; access is **read only** and requires an explicit
go-ahead each time. No writes to the device from any agent, ever.

**Repository constraints:** nothing about the owner's infrastructure is ever committed
(§13). `master` is protected — work lands through a pull request, never a direct push — and no
push happens without the owner's explicit instruction.
