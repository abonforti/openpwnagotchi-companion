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
| D7 | Config | All IPs and ports configurable in the PWA settings screen. BT PAN IP prefilled with `172.20.10.2`, which is a **guess and not a constant**: the `172.20.10.0/28` block is what every iPhone Personal Hotspot uses, but the host inside it is assigned by that hotspot, so the field is editable and the app must not assume it. USB IP (`10.0.0.2`) supported as an extra configurable host. |
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
session_poll_interval = 5          # bettercap session refresh. Stops capping the broadcast tick with #65 (SPEC 2.4)
rebind_interval = 30               # seconds between local-address re-enumeration passes
save_gps_log = false
gps_log_path = "/var/tmp/pwnagotchi_gps.log"
mirror_auto_interval = 5           # seconds for the mirror auto-refresh option (client-driven)
keepalive_interval = 20            # decided: stats broadcasts, clamped 5-20, 0 means default. Not shipped, #65 (SPEC 2.4)
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
| one interface fails to enumerate | skip it; the rest of the pass stands, logged at debug. Routine on a unit that brings `wlan0mon` up and down |
| the **enumeration itself** raises, whatever the exception type | log at warning, make no change to the bound set, retry next pass. A failed enumeration observed nothing, and the rows above are about what was observed: treating it as an empty address list would tear down a healthy tether every time `ip -4 addr show` hiccups. "Never crash the loop" is about staying available, not merely about not propagating. **At `error` rather than `warning` when no enumeration has ever succeeded since load**, saying the companion is unreachable: a unit with no `ip` binary and no `netifaces` fails every pass forever, and at warning it reads exactly like a netlink hiccup on a working unit, so nobody looks. The condition is *never enumerated*, not *never bound* — those differ on the unit described in the row below, where the enumeration succeeds every pass and matches nothing, and where a transient failure must not be announced as a permanent fault. |

State is a dict `{ip: (ws_server, http_server)}`, keyed on the address itself, so a flapping
tether or a new DHCP lease converges without a plugin reload.

##### What the enumeration itself guarantees

The rows above describe what `Listeners` does with the addresses it is given. This says what the
enumeration is allowed to hand over, and it is stated because leaving it implicit produced three
defects that no test on a build host could see (issue #68).

- **The wildcard never leaves the enumeration**, and it is also refused at selection. **One
  rule, enforced on both sides of the seam** — not two gates, which would be a stronger claim
  than what is there: both sides call the same `is_bindable_address`, so loosening that one
  predicate opens both at once. That is deliberate. The threat this guards against is a *bypassed
  call site*, since the enumeration is injectable: a rule enforced only in the seam is walked past
  by a test double, and a rule enforced only at selection is walked past by a replacement seam.
  A second, independently written wildcard check would guard against nothing extra and would be
  the thing that drifts. Selection remains the load-bearing side; the seam's check is there so
  that a caller which forgets is not the only thing in the way.
- **Every value that leaves is an IPv4 literal**, checked with the same predicate selection uses,
  so the two cannot drift. Not a regular expression: `999.1.1.1` matches one and is not an
  address, and `192.0.2.6/24`, `fe80::1%bnep0` and `192.0.2` all have to be refused too.
- **One interface failing is not a failure.** `netifaces` raises for an interface that
  disappeared between `interfaces()` and `ifaddresses()`, which on a pwnagotchi is routine rather
  than exotic — `wlan0mon` is created and torn down as a matter of course. So a failing interface
  is skipped and the pass stands, logged at **debug**, because a warning several times a minute
  for normal operation is how a log stops being read.

  The exception is when **every** interface enumerated failed: then the pass observed nothing at
  all, which is the case below rather than this one, and it raises. The distinction is between
  "we learned everything except that one" and "we learned nothing", and collapsing them in either
  direction produces a defect: skip-always turns a total failure into an empty list that tears the
  tether down, and raise-always turns a routine interface teardown into a lost pass.

  Note that a host with genuinely no interfaces is not this case. It observed, and the answer was
  empty.

  **An interface is all or nothing.** The guard covers reading its address list *and* walking it,
  so an interface that fails halfway contributes none of the addresses already collected from it.
  A library returning a shape it does not document is the same kind of event as a name that
  vanished — one interface did not work out — and splitting the two would mean a partial result
  from one of them, which is the least useful of the three possible answers.

- **A failure raises rather than returning an empty list.** This is the contract the reconcile
  table's last row already depended on, and it did not hold: both halves of the default seam
  caught everything and returned `[]`, which is exactly the "observed nothing" that the row
  forbids being confused with "observed nothing there". A `list[str]` signature has no other
  honest channel — a sentinel would need every replacement seam to speak it, while an exception
  is understood by the `except` that is already there.

The exception type is deliberately not pinned: the seam lets the backend's own exception out, and
`reconcile` catches `Exception`. Naming a type would mean wrapping every backend failure for no
gain, since nothing downstream distinguishes them.

**On the parsing of `ip -4 addr show`**, which is the fallback when `netifaces` is not importable:
take the first token after `inet`, line-anchored, and split the prefix off it. Then validate it
like everything else.

The validation is the point, and it is worth being precise about why, because the obvious story
is wrong. A pattern matching four dot-separated numbers does **not** return the peer from
`inet 10.0.0.2 peer 192.0.2.1/32` — it is already anchored on `inet` and stops at the local
address. What it does do is accept `999.1.1.1`, which is four dot-separated numbers and is not an
address. A regular expression describes a shape; `ipaddress.IPv4Address` decides membership. Only
the second one is what this needs.

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

`keepalive` is broadcast every `keepalive_interval` seconds (default 20, accepted range 5-20 —
see the clamp below) from
the background thread, which today wakes at `min(session_poll_interval, keepalive_interval)`
because it carries the keepalive. **Once #65 ships**, both broadcasts move to a ticker of their
own and this loop wakes on `session_poll_interval` alone, refreshing and broadcasting nothing.

That move is the point of #65. While the broadcast rides this loop its spacing is the loop's,
so a 5 s poll turns a nominal 20 s cadence into 20-25 s and a client whose timeout was set to
the nominal value reconnects for no reason. On a ticker the spacing is `keepalive_interval`
and nothing else, which is what lets §4.3.2 state one.

It exists so an idle client can tell a quiet plugin from a dead link: over BT PAN a dropped
tether does not close the socket, it simply stops delivering, and without a periodic frame the
app would sit on a dead connection showing stale data. The frontend does not reconnect on a missed frame, which would be a guess: it reconnects when
its own `ping` goes unanswered (§4.3.1), and reads staleness from the data (§10.5).

**`stats` rides the ticker with it** (§4.3.7, issue #65). **Not yet: the loop broadcasts only an empty `keepalive` today, and the clamp below does not exist either.** Everything from here to the end of this section describes the decided behaviour, not the shipped one, and the cadence question behind it is answered: issue #65 moves the broadcast onto a ticker of its own, sharing nothing with the bettercap refresh, so the period stated here is one the plugin can hold to once it ships. A periodic frame proves the link is
delivering and says nothing about whether the data behind it moved, and the client needs both:
`degraded` in §4.3.1 is defined as socket-alive-but-data-stale, and it cannot be computed from a
frame that carries nothing. So the ticker sends **both on the same tick**: `stats` for the
freshness, and the `keepalive` that was already there.

Sending both rather than replacing one with the other is deliberate and is a decision, not an
omission. `keepalive` is an existing message type that some client may already act on, and
removing a type is a contract change; the frame is empty, so the cost of keeping it is a few
bytes on a tick that is now carrying a full payload anyway. If it is ever dropped, that is its own
change with its own version bump.

That makes both loop intervals load-bearing for the client, so **the plugin clamps them and logs
when it does** rather than honouring a value that silently disables the app:
`keepalive_interval` to **5-20 s**, `session_poll_interval` to **1-5 s**. A configured
`keepalive_interval = 0`, which previously disabled the broadcast, takes the **default 20** and
not the floor: a unit whose owner deliberately turned the broadcast off should not start sending
a full `stats` payload every second over BT PAN.

The arithmetic is written out because three drafts of this section got it wrong, each time by
reasoning about it in prose.

The ticker re-arms from the moment it actually fired and shares nothing with the refresh, so
consecutive broadcasts are `keepalive_interval` apart plus
whatever a send costs. The ticker no longer waits on `agent.session()`, which is the point of
moving it, but it still waits on itself. Call that the **spacing**.

Staleness is a property of the data, not of the frames (§4.3.1): the client reads whatever
`sessionAge` a frame carries and never extrapolates between frames. So the spacing does not
add to the age. **The refresh loop sets the age; the ticker only decides how often it is
sampled.**

The age therefore peaks once per failed refresh episode. The episode runs from the last
success to the next one, so it carries a poll interval before each attempt and bettercap's
30 s timeout (F7) for each failure:

| episode | peak `sessionAge` at `session_poll_interval = 5` | against `S = 55` |
|---|---|---|
| one refresh times out, the next succeeds | 5 + 30 + 5 = 40 | under: an ordinary slow moment is not reported as stale |
| two in a row | 5 + 30 + 5 + 30 + 5 = 75 | over: a pattern is reported |

**`S = 55` is chosen to sit between them**, and that is the whole of its derivation. It is not
a function of `keepalive_interval`, which is why the clamp on that key is argued below on
other grounds.

That is the intent stated once: **one bettercap timeout is an ordinary event on this unit and
must not show the data as stale; a second is a pattern and should.**

`keepalive_interval` is clamped to 5-20 for a different reason, since it cannot cause a false
`degraded`. It bounds **how long the client believes something that has stopped being true**:
the age it displays, the moment it notices a real stall, and the moment it notices recovery,
are all one spacing behind the unit. It also decides whether the stall is seen at all. In a
two-episode peak at `session_poll_interval = 5` the age is above 55 for 75 - 55 = **20 s**, so
at the ceiling at most one sample lands inside that window, and only while a send costs
nothing worth counting: the ceiling is already at the equality rather than inside a margin, and a larger interval can step over the peak and report nothing, the more often the larger it
is.

At the poll floor the window narrows to 8 s: 1 + 30 + 1 + 30 + 1 is 63. A sampler at the
keepalive ceiling of 20 s misses that more often than not, though one at the keepalive floor of
5 s always catches it. Polling fast therefore trades the report of a two-episode stall for a
faster report of the recovery, and that is the right way round: a stall that persists is caught within a
few episodes, since the sampler and the episode drift against each other, while a recovery the
app fails to notice is a control the user cannot use.

Two clamps interacting this way, to bound a number neither of them names, is the argument for
the capability in the closing note below rather than for more arithmetic here.

`session_poll_interval` is clamped to 1-5 for a different reason, now that it no longer sets
the broadcast spacing. `sessionAge` is wall-clock age, so the poll interval does not govern how
it grows; it governs how soon it is **reset** once bettercap answers again. At 5 s a recovered
unit leaves `degraded` on the next broadcast; at a minute it would stay stale-looking long
after it was well.

**The better shape, deliberately not taken here.** The plugin could publish its effective spacing
in `Capabilities` and let the client derive `S` from it, which would survive any configuration
and remove this arithmetic from the specification altogether. It is a schema change, so it is not
being made in a decisions-only pass — but note that the fragility it removes has now produced a
defect twice in the same section, which is the usual sign that the clamp is a workaround and the
capability is the fix. Recorded in issue #65 for the implementer to weigh.

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
   battery level and charging attributes they expose, via `getattr`. **UNVERIFIED:** these are
   third-party plugins and their attribute names are not pinned. Probe with `getattr` over a
   candidate list and treat every miss as "unavailable"; do not import them and do not assume a
   name. **The values are not on the plugin object.** `plugins.loaded['pisugarx_ext']` is a
   `PiSugar(plugins.Plugin)` that holds its hardware client at `self.ps`, and the battery state
   lives on that client (F29). So each candidate is looked for on the plugin and then on
   `plugin.ps`, and on the reference unit it is found in the second place, as the plain
   attributes `battery_level` and `power_plugged`. Those are current rather than stale: the
   client runs `update_value()` on a daemon thread that refreshes them every three seconds, and
   the accessors `get_battery_level()` and `get_battery_power_plugged()` are one-line wrappers
   that return the same attributes and read no hardware of their own (F29). Probing the
   attributes and probing the accessors are therefore the same reading, and the attributes come
   first because they are earlier in the lists.

   **The client is traversed only when it reports itself ready.** `PiSugarServer.__init__` sets
   `battery_level` to `0` and `power_plugged` to `False`, and `update_value()` is started only
   once a device has actually been found (F29). With the plugin loaded and no PiSugar attached
   the search loops forever, the refresh never starts, and those constructor values stand for the
   lifetime of the process. Probing them would report a confident `{"percent": 0.0, "charging":
   false}`, latch `capabilities.pisugar` true and pre-empt the direct read for good. The client
   carries the fact needed to tell the two apart: `ready` is `False` from the constructor and is
   set `True` only after the device is connected and its registers have been read, and nothing
   sets it back (F29). So a client whose `ready` is not true is treated as absent, exactly as if
   the plugin held no client at all, and both fields fall through to the tier below.

   **The gate is on the client, not on the plugin.** The plugin object itself is still probed for
   every candidate whether or not the client is ready: another plugin may keep its reading on
   itself and hold no client at all, and that shape is why the plugin is a probe target in the
   first place. `pisugarx_ext` mirrors the flag as `self.ready = self.ps.ready` (F29), so on that
   plugin the two are the same fact and gating on the client loses nothing. Reading `ready` is
   third-party attribute access like every other: absent, `None`, or a property that raises all
   mean not ready, and none of them is an error.

   **`battery_charging` must not be probed.** It is assigned `0` once in the client's constructor
   and never again, and `get_battery_charging()` has a body of `pass` (F29). `0` converts to
   `False` under the charging conversion below exactly as it did under a bare `bool()` cast, so a
   `False` is a value rather than a miss either way, and probing it would report "not charging"
   confidently and forever while `power_plugged`, the only charge-related field the plugin
   maintains, was never consulted. A dead field that answers is worse than no field at all. The
   guarded conversion below narrows what counts as a value; it does not rescue this field, because
   `0` is exactly the shape it is built to accept.

   **Every plain attribute is preferred to every accessor, and only then is the order by how
   precisely a name answers the question, with ties going to what was observed.** Precision
   settles `charging` above `power_plugged`, since a plugin exposing both means the two
   differently and the first is the field this reports, and settles `battery_level` above
   `battery`, `level` and `capacity`. It does not settle `battery_level` against `percentage`,
   where both name the quantity exactly; there the tie-break is that `battery_level` is what the
   reference unit's client exposes.

   The attribute rule comes first because it is about cost rather than meaning, and it is why
   `get_battery_level` and `get_battery_power_plugged` sit last in their lists even though they
   name the question more precisely than `capacity` or `plugged` do. Reading an attribute cannot
   block and cannot touch hardware; calling an accessor is third-party code that may do either,
   on the request path. That these two accessors are one-line wrappers today (F29) is a fact
   about one plugin at one version, not a property of the shape being probed, and the probe has
   no way to tell a wrapper from a bus transaction before calling it.
2. Direct PiSugar I2C read (bus 1, address `0x57`), wrapped in try/except. Register `0x2A`
   carries the percentage and **bit 7 of register `0x02`** carries charging. Both are read as a
   **byte**. The library is **`smbus2`, and there is no `smbus` fallback** — stated here because
   until now it was not stated anywhere. The dependency
   lived only in the implementation, which is how a test stub came to offer a fallback that does
   not exist: nothing in this document forbade one.
3. `{percent: null, charging: null}`.

Never hard-fail, never raise into the asyncio loop.

**Resolution is per field, and descending is only ever for what is still missing.** A tier that
supplies one field does not settle the other. The reason is the defect this rule replaces: a
plugin exposing a charge state and no level made the level unreachable for good, in silence, even
though the I2C tier below could read it off a register this document now pins. A field that no
tier supplies is `null`, which is the same answer it was before.

The constraint matters as much as the rule. **A tier is never consulted for a field already in
hand**, so the I2C bus is touched only when there is something to gain by touching it, and on a
unit whose tier-1 plugin answers both fields it is not touched at all. Without that constraint
per-field resolution would mean an I2C transaction on every read for a device that had already
answered, which is a cost paid for nothing.

The order of tiers is unchanged and so is the meaning of each. What changes is that reaching tier
2 is decided per field rather than for the reading as a whole.

**`pisugar: false` disables every tier.** It is the switch for reading a battery at all: with it
off no plugin is looked up, no probe runs, and the answer is two nulls. The narrower reading, where
it gates only the direct read, is distinguishable from this one and would leave the option
half-honouring its name.

**What counts as a value, at any tier.** An attribute that is absent, or present and `None`, or
whose value will not convert to the field's type, is a miss and never an error. A candidate that
is callable is called, because some PiSugar plugins expose `get_battery_level()` rather than a
plain attribute; a call that raises is a miss like any other. And **a percentage is a finite number
in 0-100, regardless of which tier produced it**: §2.11.2 states the rule for the direct read, but
it is a statement about what the quantity can be and not about how it arrived. A tier-1 plugin
reporting 255 must report nothing rather than have the client render a plausible-looking lie. That
is the same answer the direct read already gives for the same byte. Note that the schema does
**not** enforce this: `Battery.percent` is typed `number|null` with no bounds, so this rule is the
only thing standing between a bad gauge and the display.

Three edges of that rule, because each of them passes a careless form of it. **Finite** is load
bearing: `NaN` and the infinities are not caught by asking whether a value is below zero or above
one hundred, since every comparison against `NaN` is false, and a `NaN` that survives reaches
`json.dumps` and is serialised as the literal `NaN`, which is not JSON and which a conformant
client rejects. Write the bound as a range the value must fall *inside*, not as two ways of being
outside it. **A boolean is not a percentage**, though `True` converts to `1.0` and sits happily in
range: a charge flag read into the level field is exactly the plausible-looking lie this rule
exists to stop. And **a value refused for any of these reasons is a miss**, so the next candidate
and then the tier below still get their turn; it is not a reading that is later nulled, because
that would make the level unreachable and reintroduce the defect §2.11's per-field rule removes.

**A value that converts is accepted, including a numeric string.** `"87"` from a third-party
plugin is 87, and the existing clause above already decides it: a miss is a value that *will not*
convert. This tier exists to be forgiving about shapes nobody here controls, and a plugin that
reports its level as text is careless rather than wrong. The refusals in this section are all
values that convert and still cannot be a percentage; a numeric string is the opposite case. Note
that the bool refusal is not an exception to this, since a bool converts to a number that is
inside the range and therefore indistinguishable from a real reading once accepted.

**A refusal is not an answer.** A tier-1 provider whose only usable output was refused has
supplied nothing, so `capabilities.pisugar` stays false on its account. This does not contradict
§2.11.2, where an out-of-range percentage alongside a charging bit is a PiSugar that answered:
there the charging bit is the answer, and here there is none.

**Charging is converted with the same rigor as percent, for the opposite reason.** Percent guards
a range; charging guards a two-valued fact, and a value that is not unambiguously one of the two
is a miss rather than a guess. A bare `bool()` cast has no content on this field: `""` is `False`,
`"false"` is `True`, and `0.5` is `True`, so a plugin that returns any non-empty string or any
non-zero number would be read as charging regardless of what it meant. The guarded conversion
accepts a `bool` as itself; an `int` only when it is exactly `0` or `1`; a `str` only from a fixed
set of spellings read after `strip().lower()` — `"true"`/`"false"`, `"1"`/`"0"`, `"yes"`/`"no"`,
`"on"`/`"off"`. Everything else, including a float and an unrecognised string, is refused like a
failed cast, and the refusal follows the same rule as every other one in this section: it is a
miss, not an answer, so the next candidate and then the tier below still get their turn.

This is why `battery_charging` staying off the candidate list, above, remains load-bearing rather
than redundant: `0` still converts, to `False`, so the guard alone would not have kept a
constructor's stale `0` from being read as a confident "not charging" had that field been probed.

#### 2.11.1 Where the I2C constants come from

Bus 1, address `0x57` and register `0x2A` were inherited from the upstream plugin this forks and
carried no evidence here at all, because §11's tables cover pwnagotchi symbols and nothing else.
The charging bit was not inherited from anywhere: until this change SPEC recorded its source as
unknown outright. All four positions were established on 2026-08-17 against the reference unit,
PiSugar firmware **`v1.3.8`**, and this section records how. Positions, not meanings: what the
charging bit signifies is a separate question and the end of this section does not settle it. Reproduce the dumps with
`i2cdump -y 1 0x57 b`, which issues reads and no writes. The raw output is attached to issue #70,
so every row below can be checked rather than taken on trust.

| Constant | Value | How it was confirmed |
| --- | --- | --- |
| Bus | `1` | The device answers there. The dump carries `v1.3.8` at `0xE0` and the string `www.pisugar.com` at `0xF0`, so the thing answering is a PiSugar and not a coincidence. |
| Address | `0x57` | Same read. |
| Percentage | `0x2A`, byte | Read `0x59` while the on-device display showed 89%. |
| Neighbour | `0x2B` | Read `0x00` in every dump, which is why a word read at `0x2A` returns the right number. |
| Charging | `0x02`, bit 7 | `0xEC` on external power, `0x6C` without it, `0xEC` again on reconnect. Across that transition one bit moves and no other bit of the byte does. **The bit reports external power present, not charge current**: at 100% with the cable still connected the byte reads `0xE8` and bit 7 is still set, so it never clears on a full battery. The byte differs from `0xEC` in bit 2 as well, which moves for reasons this map does not pin and which nothing here reads. The field is named `charging` on the wire because that is what a user reads it as, and on a unit running on external power it is true. |

**The byte-versus-word question is settled, and the word read was wrong even when it agreed.**
`i2cget -y 1 0x57 0x2A w` returned `0x0058`: low byte `0x2A`, high byte `0x2B`. The low byte is
`0x58` and the table above records `0x59`, because the two commands ran a moment apart and the
gauge moved by one point between them, which is the only discrepancy in this section and is
recorded rather than smoothed. The word agreed only because `0x2B` read `0x00`; `0x2B` is a
separate register and nothing observed here promises it stays zero.

**What `charging` actually means.** The dumps cannot say. They were taken at 89%, where a cable
that is connected is also charging, so `0xEC` against `0x6C` is equally consistent with the bit
meaning *external power present* and with it meaning *current flowing into the battery*. No
experiment at 89% can separate them.

The best available reading comes from the plugin the reference unit actually runs, which reaches
the same register and names it:

```python
ctr1 = self.i2creg[0x02]                      # Read control register 1
self.power_plugged  = (ctr1 & (1 << 7)) != 0
self.allow_charging = (ctr1 & (1 << 6)) != 0
```

That is `pisugarx_ext`, at `abonforti/pwnagotchi-plugins`. It calls bit 7 `power_plugged` and puts
a separate flag at bit 6.

**How much that is worth, stated plainly.** It is a variable name, so it is evidence of what its
author understood the bit to mean and not a measurement of what the silicon does. It is also not
independent: that repository belongs to this project's owner, and nothing records where its own
bit assignment came from, so it may trace to the same upstream plugin or the same datasheet this
project would otherwise be reading. Treat it as the strongest available prior, not as a second
witness.

**The same author states the belief outright, in the source rather than in a commit
message.** [`pisugarx_ext.py` at `abbc8777`](https://github.com/abonforti/pwnagotchi-plugins/blob/abbc87774dbd2ca1ff4b8f83b6210ec1984fb6be/pisugarx_ext.py#L985)
carries it as a comment: "The MCU has no 'charge complete' bit, only 'external power
present'", and line 566 of the same file says there is no charge-complete bit anywhere in
the PiSugar 3 register map. That is a stronger *form* of the evidence above, a belief stated
rather than inferred, but it is not a second witness: same author, same file as the variable
name already weighed. Line 567 does point outwards, to a vendor FAQ, but for LED behaviour
rather than for the register map, and that FAQ has not been read here; neither place records
where the register-map assignment itself comes from. It raises confidence in the reading
without raising its independence. What does raise it is the device:
at 100% with the cable connected `0x02` reads `0xE8` with bit 7 still set, which is the
observation the register table above records and #70 collected.

**And the dumps qualify it further.** Bit 6 is set in both recorded bytes, `0xEC` and `0x6C`
alike, so `allow_charging` is true with the cable out. It is a permission flag rather than a
current-flowing signal, which means the register does not carry the *charging current* reading
that would have made the bit-7 interpretation airtight. The distinction this section is trying to
draw is one this register may simply not express.

**Where the chosen reading will be wrong**, then, is a full battery on a connected cable: this
reports charging while nothing is being charged. The divergence is accepted rather than overlooked, because the
alternative is the field staying `null` forever and a charge icon that lingers at 100% is what
most devices do. It has still never been *observed*, since the unit sat at 89% throughout, and a
dump at a full battery would turn the inference above into a measurement. That is the one PiSugar
question issue #70 keeps open.

**The tier-1 plugin attribute names remain unpinned**, which is why they are probed with `getattr`
rather than imported. Nothing in this section changes that: it pins the I2C fallback, and the
fallback is the tier the reference unit never reaches, since it runs `pisugarx_ext`.

#### 2.11.2 When only half the reading arrives

Two registers are read, so there are two ways to get half an answer, and they are not the same
event.

**A register that will not answer discards both readings.** If either read raises, whether the bus
is absent, the device NAKs or a transfer collides, the result is `{percent: null, charging: null}`,
never a half-populated one. Three reasons. The `(percent, charging)` pair where charging is null
is already the *meaningful* answer of tier 1, a PiSugar plugin that exposes a level and not a
charge state, so reusing that shape to mean "the I2C bus is misbehaving" would make two unrelated
situations indistinguishable one layer up in `battery_info`. A bus that has just raised on one
register is not a bus whose other answer has earned confidence. And it keeps the failure path
single: one shape for every transport failure, rather than a shape per combination.

**A percentage outside 0-100 discards only the percentage.** A byte that cannot be a percentage is
a *successful read of a bad value* rather than a failed read; why the gauge produced it is not
recorded here, because nothing observed says. The charging bit came off a different register, is
one bit wide, and either matches the observed pattern or does not, so a nonsensical gauge reading
does not make it suspect. The result is `{percent: null, charging: <the bit>}`.

**A provider has answered when either field arrives.** `capabilities.pisugar` reports whether any
tier has ever produced a reading, and an out-of-range percentage alongside a charging bit is a
PiSugar that answered, however poorly. The test is the same one tier 1 already applies to a plugin
that exposes one field and not the other; the direct read had no reason to be stricter, and looked
stricter only because it could not produce a charging value at all until now.

**A bus that will not close is a transport fault**, and yields `{percent: null, charging: null}`
even when both reads succeeded and were in range. This is the one case the transport/content line
does not decide on its own, since the readings were already in hand. It goes with transport
because a handle that fails to close is evidence about the channel, and because the alternative
is a fourth rule for an event nothing has ever observed.

The line between the two rules is transport versus content. A read that did not happen tells you
nothing about the other read, because the fault is in the channel both share. A read that happened
and returned nonsense tells you about that register, and the `Battery` schema allows each field to
be null independently precisely so a partial answer can be reported as one.

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
`face_status` (text only — no PNG). Both texts come from `agent.view().get('face')` and
`.get('status')` (F28), which return the values themselves rather than the widgets; an absent
key answers `None` and is sent as an empty string, because the schema types both fields as
strings. Keep the heartbeat/keepalive machinery from pwnios.

Three consequences of that source, stated because each was inferred once and should not have to
be inferred again:

- **`status_change.status` is the same view status**, not a separate string. The mood hooks carry
  a mood and the text the unit is displaying at that moment, and the view is the only source
  §11 makes available for it.
- **A view that cannot be read yields empty strings, never a missing field or a null.** That
  covers `Agent.view()` raising and `View.get` raising as well as the absent key above. The
  schema requires three strings, so the alternative is a message that fails its own contract at
  the moment the unit is already misbehaving.
- **The rule in §2.5 that `agent.session()` must not be called on the request path is general**,
  not local to `stats`. It is written under that payload because that is where it was first
  violated upstream, but it binds every handler and every hook.

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
  general name, **not** `DNS:`). Include every IP the app will use (`172.20.10.2`, `10.0.0.2`, …).
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
  the hotspot (it keeps cellular) and the Pi is a BT PAN client somewhere in
  `172.20.10.0/28` — the phone is not routing through the Pi. Tiles are therefore
  online-only in v1; offline tile caching is a roadmap item.
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
  `purpose: "maskable"`, and **no `orientation` key** (§4.5.1: locking it either does nothing on
  iOS or removes the landscape rail from the installed app while leaving it in the browser).
#### 4.2.1 Two iOS facts measured on the device, not assumed

Both were established on an iPhone 17 Pro running the app from the Home Screen, with a
diagnostics page reporting the engine's own numbers. Both contradict what the obvious code would
assume, and both cost a visible defect when they are not known.

**`(display-mode: standalone)` does not match on iOS, even when the app is standalone.** The
device reported `navigator.standalone === true` and the media query `false` in the same frame.
Any CSS or JS conditioned on that media query is silently inert on the only platform this app
targets. **Use `navigator.standalone`**, and treat the media query as a progressive enhancement
for other engines.

**In standalone with `black-translucent`, the viewport is shorter than the screen by exactly the
top inset.** Measured in portrait: screen 402x874, `window.innerHeight` 812, top inset 62, and
874 minus 62 is 812. The web view is placed at the top of the screen but reports a height that
excludes the status bar, so a strip the height of the status bar is left at the bottom that the
page cannot paint. It shows as a dead band under the navigation bar.

`position: fixed; inset: 0` is not at fault: it fills the viewport the engine declares, which is
the correct behaviour and the reason it was chosen over viewport units. The engine declares one
shorter than the screen.

The lever is in the units, which disagree only in this case: in that same frame `100vh` measured
**874**, the full screen, while `100svh` and `100dvh` measured 812. So:

> **In standalone, and only in standalone, the shell is `height: 100vh` instead of being pinned
> by `inset: 0`.** In a browser tab it stays `inset: 0`, because there `100vh` is the large
> viewport and the bottom bar would sit under Safari's toolbar, which is the bug that made
> viewport units unusable in the first place.

The condition is `navigator.standalone`, per the fact above.

Landscape is unaffected and was measured too: `100vh`, `100svh`, `100lvh` and `100dvh` all
reported 402, the fixed box filled 874x402, and the gap was zero. Note that landscape carries a
**62px inset on both the left and the right**, not only on the leading edge, so content must
clear the trailing inset as well as the rail clearing the leading one.

**Both were then confirmed on a second device**, an iPhone SE at 320x568 running iOS 15.8, which
matters because it is notchless and a different iOS major. Same behaviour, same arithmetic:
`100vh` measured 568, the full screen, while `100svh` and `100dvh` measured 548, and the top
inset was 20px. 568 minus 20 is 548, exactly as 874 minus 62 was 812. `navigator.standalone` was
`true` and the media query `false` there too.

The SE also settles a question the first measurement could not. On the 17 Pro the top inset and
the missing strip were both 62px, so the two candidate explanations, "the inset" and "the status
bar", produced the same number and could not be told apart. A notchless device separates them:
its top inset is **not** zero, it is 20px, the height of its status bar. So the inset generalises
to the status bar rather than to the notch, and the explanation above holds rather than being a
coincidence of one device.

Landscape was clean on both: every unit agreed, the fixed box filled the viewport, and no strip
was left over. The anomaly is portrait-only.

Neither fact is reachable by an automated test: jsdom has no viewport, and no browser engine can
be put into iOS standalone mode. They are verified on hardware, and this section is where that
verification is recorded so it is not re-derived by the next person to see a black band.

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
- Connection state: `connecting | connected | degraded | offline | unauthorized | restarting`.
  Because the socket only works while tethered, `offline` is the normal disconnected state and
  is shown calmly, not as an error. `restarting` is entered on a `restarting` message (§2.6.1)
  and suppresses the error banner while showing the reason.

> **Decided, not yet shipped.** The behaviour below is settled; the plugin has not caught up.
>
> The timing rules key a client staleness threshold to the refresh episode, and the periodic
> broadcast to how late the client hears of one. That broadcast is moving off the refresh
> thread onto a ticker of its own, sharing nothing
> with `agent.session()` and its 30 second timeout. That is what makes a cadence promisable:
> the loop period was `work + tick` rather than `tick`, so a slow bettercap could stretch the
> spacing past any threshold stated here with the link perfectly healthy. Clamping did not
> help, because the clamp bounds the sleep and the sleep is not what overran. The decision is
> recorded on issue #65. The arithmetic behind the numbers is derived in §2.4, in the tree,
> rather than left in a ticket.
>
> `degraded` is defined on `sessionAge`, as `common.json` always said: the unit is reachable
> and its broadcasts are arriving, and the data behind them has stopped moving. §4.3.1 once
> retracted that route on the premise that the refresh and the broadcast are the same thread.
> They are the same loop today, and that is not what makes the state impossible.
> `refresh()` swallows its own exception, so a dead bettercap leaves the broadcasts flowing and only `sessionAge` growing.
>
> Until the plugin ships the ticker, a client written against this section will measure a
> staleness that the plugin cannot yet hold to. Issue #65 is the gate.

#### 4.3.1 The connection state machine

The skeleton above left every number as `N` and every state undefined beyond its name. Those
gaps are filled here, because each is decided once when `lib/ws.ts` is written and is expensive
to revisit afterwards.

**It is written as a transition table rather than as prose, and that is not a presentation
choice.** The prose version of this section was reviewed three times and each pass found a state
with a missing exit or a threshold that contradicted its own arithmetic — including one
introduced while fixing another. A paragraph has no way to notice that nothing leaves
`connecting`. A row does: the cell is empty.

**Six states.** Every one is entered by a rule below and left by one; none is terminal except
where the table says so and gives the reason.

| state | the user sees | state-changing controls |
|---|---|---|
| `connecting` | a quiet indicator, no copy — the normal first second | disabled |
| `connected` | the data, unqualified | **enabled** |
| `degraded` | the data, marked stale, with its age | **enabled**, see below |
| `offline` | the calm copy about the Personal Hotspot; never an error dialog | disabled |
| `unauthorized` | that a token is needed or wrong, and a way to fix it | disabled |
| `restarting` | the reason the plugin gave, error banner suppressed | disabled |

`connected` requires that authentication passed **or was not required**, because `token` is empty
by default (D5) and the plugin then skips the step. A definition demanding an authentication step
would exclude the default deployment.

##### The transitions

Read as: in this state, on this event, go here. An event with no row for a state does not occur
there, or is ignored.

| from | event | to |
|---|---|---|
| `connecting` | first `stats` of this connection, `sessionAge` under 55 s and not `null` | `connected` |
| `connecting` | first `stats` of this connection, `sessionAge` 55 s or more, or `null` | `degraded` |
| `connecting` | 55 s elapse without it | `offline` |
| `connecting` | `restarting` message | `restarting` |
| `connecting` | `error` with `code: "unauthorized"`, then close | `unauthorized` (*rejected*) |
| `connecting` | bare 1008, **no `auth` frame was sent** | `unauthorized` (*required*) |
| `connecting` | third consecutive close before any `stats`, no token stored | `unauthorized` (*required*), by inference — see below |
| `connecting` | bare 1008, an `auth` frame **was** sent | `offline` |
| `connecting` | any other close, or the socket fails to open | `offline` |
| `connected` | a `stats` whose `sessionAge` is under 55 s and not `null` | `connected` |
| `connected` | a `stats` whose `sessionAge` is 55 s or more, or `null` | `degraded` |
| `connected` | no `pong` within its timeout | `connecting` |
| `connected` | `restarting` message | `restarting` |
| `connected` | close | as for `connecting`, by the same four close rules |
| `degraded` | a `stats` whose `sessionAge` is back under 55 s, and not `null` | `connected` |
| `degraded` | a `stats` still at or over 55 s, or `null` | `degraded`, with the displayed age updated |
| `degraded` | no `pong` within its timeout | `connecting` |
| `degraded` | `restarting` message | `restarting` |
| `degraded` | close | as for `connecting` |
| `offline` | backoff delay elapses | `connecting` |
| `offline` | the user asks to reconnect | `connecting` |
| `unauthorized` | the user changes the stored token | `connecting` |
| `restarting` | close, reason is `mode_change` or `reboot` | `restarting`; the backoff loop runs underneath |
| `restarting` | close, reason is `shutdown` | `restarting`; **no reconnection is attempted** |
| `restarting` | first `stats` of a new connection, `sessionAge` under 55 s and not `null` | `connected` |
| `restarting` | first `stats` of a new connection, `sessionAge` 55 s or more, or `null` | `degraded`, which after a reboot is the ordinary case rather than the exception |
| `restarting` | patience expires (90 s `mode_change`, 180 s `reboot`) | `offline` |
| `restarting` | patience expires, reason is `shutdown` | never: there is no patience timer |
| `restarting` | the user asks to reconnect | `connecting`, for any reason |

`restarting` is the only state that spans a disconnection, which is what the two close rows say:
the socket **will** drop, that is the point of the message, and the state survives it while the
backoff loop reconnects underneath. Without those rows the table was not total — a mode change
necessarily closes the socket, the close was unlisted and therefore ignored, and nothing opened a
new socket from `restarting`, so "first `stats` of a new connection" was unreachable and an
ordinary 15-second mode switch would have taken the 90-second patience expiry followed by a
backoff to recover.

`shutdown` differs by *guard* rather than by event: it suppresses reconnection and has no
patience timer, because there is nothing to be patient for.

The rows below carry the weight, and each fixes something an earlier draft got wrong.

**`connecting` has a bound.** Without one — and an earlier draft had none — a socket that opens
against a wedged plugin and never sends `initial_burst()` sits there for ever: it cannot reach
`degraded`, which needs a first `stats` to measure staleness from, and it cannot reach `offline`,
which needs no socket. Worse, a dropped tether would present as `connecting` rather than
`offline`, so the calm hotspot copy would be unreachable in the most common failure in the
product's life. The bound is the same 55 s as the staleness threshold, and deliberately one number rather than
two. Its own worst case is much smaller: the first `stats` comes from `initial_burst()` (§4.3.7),
which fires on connect and builds its payload from the caches, never from `agent.session()`
(§2.5), so nothing on that path can time out. The honest bound is a connect and an auth round
trip on a slow link, seconds rather than tens of them. Everything above that is margin, and taking the margin here costs a few seconds in a
state that is already showing a quiet indicator, while a second threshold would be a second
thing to keep true. Beyond it, the socket is open against something that is not answering,
which is what `offline` is for.

**`restarting` with reason `shutdown` is terminal.** `RestartReason` is
`mode_change | reboot | shutdown` (`common.json`), and after a shutdown the unit does not come
back. A client that treats all three alike reconnects for ever against a powered-off unit, in
somebody's pocket. It stays in `restarting`, presenting shutdown copy rather than restart copy,
until the user asks to reconnect.

**`unauthorized` has two entry reasons and one behaviour.** A rejected token and a required-but-
absent token both mean the plugin will not accept the client as it is, both are pointless to
retry unchanged, and both are fixed in the same place. They differ only in the sentence shown, so
the reason is a **field** while the state is a state — the line being that behaviour differences
get states and wording differences get fields. The second reason exists because a client with no
token, connecting to a unit that requires one, sends no `auth` frame at all and is closed on the
authentication timeout with a bare 1008 and no `error` frame (§2.3.4). Read as an ordinary drop,
that tells a user whose hotspot is working perfectly to switch on their hotspot, for ever.

For this discrimination to hold, **the client sends nothing before its `auth` is acknowledged**.
§2.3.4 closes with 1008 on *any* reply carrying `error.code == "unauthorized"`, not only on a
failed `auth`, so a client that fires `get_stats` while unauthenticated receives an `error` frame
and lands in the *rejected* branch when it belongs in the *required* one. The distinction is
observable in a browser: a server-initiated close surfaces as `CloseEvent.code == 1008`, a
transport failure as 1006, and the client knows what it sent.

**Except on the transport this document is written for, where the 1008 may never arrive.** §2.4
says it plainly: over BT PAN a dropped tether does not close the socket, it stops delivering, so
the plugin's close frame can be lost and the client sees 1006. A client with no token, against a
unit that requires one, would then read an ordinary drop, go `offline`, and show hotspot copy for
ever — the failure the *required* reason exists to remove, reached by a different road.

The fallback is a counter rather than a cleverer reading of one close. **After three consecutive
connections closed before any `stats` arrived, while no token is stored, the client presents
*required* anyway.** Three because a slow tether plausibly loses one handshake and implausibly
loses three, and because being wrong costs an offer to set a token rather than a lockout: the
user dismisses it and the reconnection loop carries on. This is the one transition in §4.3.1
entered by inference rather than by an observed event, and it is labelled as such so that nobody
later reads it as something the protocol guarantees.

**`unauthorized` is the one state that suspends reconnection**, which is why it is a state at
all rather than a flavour of `offline`. Everything else here resolves itself and is retried for
ever (§4.3.2). This cannot resolve without the user, and retrying it means holding a socket for
the full `auth_timeout` and writing a rejection into the unit's log every thirty seconds,
indefinitely. It also cannot be a field on `offline`: every site rendering `offline` would have
to remember to check it, and the one that forgets shows hotspot copy to somebody whose token is
wrong. A state cannot be forgotten, because a switch that does not handle it does not compile.

##### What `degraded` actually is

Socket alive, data stale. Without it, data from four minutes ago renders identically to data from
now, which on a screen showing a battery percentage and a handshake count is not cosmetic: it is
the app asserting something it does not know.

Staleness is read from `sessionAge` inside the payload, never from when a frame turned up,
because arrival proves the socket is alive and proves nothing about the data behind it. A
`sessionAge` of `null` is stale by the same rule: it means the bettercap snapshot has never
succeeded, which is the case this state exists for. **This requires the plugin to broadcast
`stats` rather than only answering `get_stats`**, see §4.3.7, a change to the plugin and not
only to the client.

**It is not the presentation of a dropped tether, and an earlier draft claimed it was.** The
arithmetic runs the other way: on a stall the ping goes out within 15 s and the pong times out
10 s later, so the client forces a reconnect within 25 s of the drop. Staleness does not race
it and does not fire late: it is read from a payload, and a dropped tether delivers none. A dropped tether goes `connected → connecting → offline` and never touches
`degraded`.

`degraded` is reached by **one** mechanism: the broadcasts keep arriving and the data inside
them stops moving. `refresh()` swallows its own exception (`plugin/companion.py`), so a
bettercap that has died leaves the ticker sending on time while `sessionAge` climbs inside
the payload. The link is healthy, the plugin is healthy, and the data is old. This is what
`common.json` has always said of `sessionAge`, and it is why staleness is read from the age
the payload declares rather than from the arrival of a frame.

**A unit whose broadcasts have stopped is not `degraded`.** It is `connecting`, and then
`offline`: staleness is read from a payload, so a socket delivering nothing can never move the
client toward `degraded` at all, and the heartbeat catches it in 25 s, and the transition table above has states for exactly that. An earlier draft reached
`degraded` by the broadcast loop wedging while the handler still answered `ping`. That was
true of a broadcast sharing a thread with the bettercap refresh; with the ticker of §4.3.7 it
shares nothing with anything, and a ticker that has stopped is a plugin that has stopped.

**So the controls stay enabled here**, which reverses what an earlier draft said. The unit is
answering, its uplink is proven by `pong`, and it can change mode perfectly well. Disabling
the controls would punish the user for bettercap's failure, in the one state where the app
can still do something useful. The staleness is shown, not acted on.


#### 4.3.2 The timings

Three plugin-side figures set the floor for everything here, and none of them is ours to choose.
`session_poll_interval` defaults to 5 s and is the cache refresh. `keepalive_interval` defaults
to 20 s. `auth_timeout` defaults to 10 s. The refresh loop ticks at
`min(session_poll_interval, keepalive_interval)` today and carries the broadcast with it, so a
frame due at 20 s can land at 25. The ticker of §4.3.7 does not share that loop, so **the
spacing is 20 s** and the numbers below are derived from it.

The transport sets the rest. BT PAN is slow and stalls under load, so a client tuned for a LAN
spends its life reconnecting a link that was merely slow.

| | value | why this number |
|---|---|---|
| server broadcast | 20 s | the plugin's `keepalive_interval`, on a ticker of its own rather than granular to the refresh loop. Carries `stats` (§4.3.7) |
| staleness threshold | 55 s | between one failed refresh episode and two: 5 + 30 + 5 = 40 against 5 + 30 + 5 + 30 + 5 = 75 (§2.4). It is what the data's age can honestly be while the unit is merely slow. Independent of `keepalive_interval`, which sets how late the client hears of it, not how high it climbs |
| heartbeat `ping` | 15 s | the server broadcast proves the **downlink**; this proves the **uplink**, which is what a command needs and which a half-open socket can lose on its own |
| `pong` timeout | 10 s | no answer in ten seconds on a link that normally answers in tens of milliseconds is a dead socket, not a slow one |
| request timeout | 15 s | generous on purpose, because BT PAN under load really does take that long, and a request that fails while the answer is in flight is worse than a slow spinner |
| reconnect backoff | 1, 2, 4, 8, 16, 30, 30, … s | capped at 30, **full jitter**: each delay is drawn uniformly from `[0, cap]` where `cap` is the value in the sequence, so the cap applies before the jitter and the sequence is an upper bound rather than a schedule. Two phones reconnecting to one unit must not do it in lockstep |
| auth window | 10 s | not ours to choose; the plugin closes the socket (§2.3.3) |

The jitter is specified as a form and not as a mood because §10.5 requires `ws.spec.ts` to assert
it, and "with jitter" cannot be asserted. Full jitter over `[0, cap]` can be: the delay is never
above the cap, and over many draws it is not always at it.

**Reconnection is never given up on**, with the two exceptions already named: `unauthorized`,
which cannot resolve without the user, and a `restarting` whose reason was `shutdown`, where
there is nothing to reconnect to. Otherwise the app is a PWA on a phone that leaves and rejoins
the tether all day, and the correct behaviour is to be connected again by the time the user
looks, with nothing to tap. A "Retry" button is the wrong answer to a condition that resolves
itself, and it turns the most common event in the product's life into a chore. The cap at 30
seconds bounds the worst case: a user who returns after an hour waits at most half a minute.

#### 4.3.3 The outbound queue distinguishes reads from commands

§4.3 gives the queue `message_id` correlation and re-send on reconnect. That is right for reads
and **wrong for the four commands that change state** (`set_mode`, `set_pasv`, `reboot`,
`shutdown`).

- **A read is queued and re-sent.** Losing one costs nothing and re-sending it is invisible.
- **A state command is refused at the point of tapping.** The control is enabled in
  `connected` and in `degraded`, and nowhere else; the command never enters the queue.
- **A state command already in flight when the socket drops is discarded and reported as
  failed**, never re-sent. It is the same hazard as the queued one, arriving by a different door.
- **`gps_data` is discarded too**, for a different reason. It is a read by shape and a position
  by meaning, and a position is only true at the instant it was taken: re-sending one on
  reconnect hands the plugin an old coordinate as the current lowest-priority fix, and a
  handshake captured in that window gets a sidecar pointing somewhere the unit never was. The
  plugin says as much by dropping browser coordinates after 10 seconds (`incoming/gps_data.json`);
  the client must not undo that by replaying them.

The reason is a single scenario. A naive queue lets a user tap Reboot on a phone that has lost
the tether, put it in a pocket, and rejoin two hours later — at which point the queue faithfully
delivers a reboot to a unit that is doing something else. For `shutdown` it is worse. A command
whose effect is unbounded in time must not survive the moment it was intended for.

**`degraded` enables the controls, and the reason is how it is reached.** §4.3.1 establishes
that it is not a dropped tether, which the heartbeat catches first and which presents as
`connecting`, and not a stopped broadcast, which is a stopped plugin. It is a unit answering
normally whose bettercap data has gone stale. The uplink is proven, the mode change will
arrive, and the only thing the app cannot vouch for is the age of what it is displaying,
which it says on the face of it.

Two earlier drafts argued the other way, from opposite premises, and both are recorded here
because the wrong derivation is the one a test author reconstructs. The first held that
`degraded` was the signature of a dropped tether and that an open socket meant the request
would arrive safely. The second held that it was a wedged broadcast thread, so a command would
be delivered blind to a unit whose state the app could not see. The first was wrong about the
state, the second about the plugin: once the broadcast has a ticker of its own, a ticker that
has stopped is a plugin that has stopped, and that is not this state.

What survives from both is the habit: a rule about enabling controls has to name the mechanism
it assumes, or it will outlive it.

#### 4.3.4 Nothing is applied optimistically

`acknowledgment.json` says it outright, and `set_pasv.json` repeats it: an acknowledgment means
the command was accepted, not that its effect is visible. `plugins.on()` dispatch is
asynchronous and a mode switch restarts the process.

So the mode badge and the PASV indicator are rendered **from `stats`**, never from the tap that
requested them. Between the tap and the confirming `stats` the control shows the request as
pending. Flipping the badge on tap means claiming MANU while the unit is still AUTO and still
restarting, and — the part that makes it a defect rather than a shortcut — leaving that claim on
screen for ever if the command failed, because nothing arrives to correct it.

#### 4.3.5 An error the user can act on gets somewhere to appear

`error.json` carries a `code`, and the codes are not interchangeable. Some describe a condition
the user created and can undo; those need a surface, or the app silently swallows the one piece
of information that would have helped.

| code | why it happened | what the app shows |
|---|---|---|
| `unauthorized` | the token was rejected | the `unauthorized` state (§4.3.1); fatal to the connection |
| `pasv_requires_auto` | PASV was requested from a mode that is not AUTO | on the control itself: PASV is reachable only from AUTO, and the unit is in MANU |
| `pasv_unavailable` | the `pasv_mode` plugin is not installed | the PASV control is absent rather than present-and-failing, since this one does not change while the unit runs |
| `no_frame` | the display frame has not been written yet | in the Mirror view: not an error, the unit has not drawn yet |
| everything else | | the diagnostics line in Settings, which is where an unrecognised code goes rather than nowhere |

The rule behind the table: an error that names a condition the user can change belongs **next to
the control that caused it**, not in a global banner. A toast that says `pasv_requires_auto`
somewhere near the top of the screen, three seconds after a tap at the bottom, is an error message
that has technically been delivered.

#### 4.3.6 Where the token lives

**`localStorage`, on the origin the PWA is served from**, which is the unit itself over HTTPS
with the private CA (§3). It persists across launches, because a companion app opened twenty
times a day that demands a token each time is an app whose users choose a four-character token.

The exposure this accepts is stated rather than glossed: **anything that can run script in the
app's origin can read it.** Every SSID, peer name and status line on those screens is chosen by
somebody else, so an injection there reaches the token. **Two layers are planned and, at the time
of writing, neither exists.** Both are tickets rather than mitigations, and the difference
matters enough to say plainly rather than to imply protection that is not there yet.

1. **Escaping of remote strings**, issue #32.
2. **A Content-Security-Policy** on the static server, issue #67. Specified separately because it
   has to name the OpenStreetMap tile origin: D2 locks the map to Leaflet with OSM tiles loaded
   over the phone's cellular link, so the app is **not** self-contained and the policy has to
   admit one external origin. That is a real weakening of the policy and belongs in the change
   that makes it, not glossed over here.

Both sit inside the same origin, so neither is a defence against the code the origin itself
serves. A compromised build artifact or a malicious dependency reads the token with any policy
fully satisfied. That is the residual risk this design accepts, stated because a reader deciding
how much to trust the app deserves to know where the layers stop.

**Clearing the token is not revocation, and the interface must not imply that it is.** Settings
offers clearing, which removes this device's copy and nothing more: the token stays valid on the
plugin until `token` is changed in `config.toml` and the plugin restarts. After the injection
described above, clearing is worthless, because the attacker already has the value. Real
revocation is rotating the configured token, and the Settings copy says so rather than offering
a button that feels like revocation and is not.

Clearing the token leaves the app in **`connecting`**, not `unauthorized`. The two are different
conditions: `unauthorized` means the plugin rejected a token that was sent, while no stored token
is an ordinary first-run state — and with the default `token = ""` the plugin requires no
authentication at all, so an app with nothing stored may well reach `connected`. Showing an
authentication failure for a unit that asks for no authentication is the same defect §4.3.1
argues against.

`sessionStorage` was considered and rejected: it is readable by same-origin script exactly as
`localStorage` is, so it buys nothing against the exposure above and only costs persistence.
In-memory only was rejected because a backgrounded PWA is evicted often enough that the token
would have to be retyped several times a day — which is how a user ends up choosing a token
short enough to retype. That last claim is a property of the platform rather than a measurement
of ours, unlike the iOS viewport figures in §4.2.1: if it turns out to be wrong, the decision
changes.

#### 4.3.7 The plugin must broadcast `stats`, not only answer for it

**This is a change to the plugin, and everything above depends on it.** As things stand, `stats`
leaves the unit in exactly two situations: in `initial_burst()` on connection, and as the reply
to a `get_stats` request. The background loop broadcasts `keepalive` and nothing else.

Written against that, §4.3.1 and §4.3.4 are not merely imperfect, they are unimplementable. A
client reads staleness from the `sessionAge` of the last `stats` it received, so with no
periodic `stats` it reads for ever the one number the initial burst gave it: a unit whose
bettercap died an hour ago still presents as fresh, which is the failure this state exists to
prevent. And a mode change shown as pending "until `stats` confirms it" stays pending for
ever, because nothing ever arrives to confirm it.

**The plugin broadcasts `stats` every `keepalive_interval`, from a ticker of its own.** The
ticker reads the caches and sends; it shares no thread with `refresh()` and therefore no
waiting with `agent.session()`, which is what lets §4.3.2 state a spacing at all. `keepalive`
rides it too, so the two frames stay on one tick and the refresh loop goes back to refreshing.
The same frame answers both questions a client has, and receiving it proves both things a
client needs to know: that the link is delivering, and that the
data is current. The alternative — the client polling `get_stats` on a timer — was rejected
because it pays a round trip on a link whose round trips are the expensive part, multiplies with
every connected client where a broadcast does not, and leaves the client unable to distinguish a
stale unit from a lost request.

`keepalive` keeps going out alongside it (§2.4). A client that has just received `stats` has all
the liveness information `keepalive` was carrying, so the frame is redundant rather than wrong,
and dropping a message type other clients may act on is a contract change that buys a few bytes.

Both options are cheap **while the project is in `0.x`**, which is the table that applies and not
the post-`1.0` one: removing the message type is MINOR there, and adding a periodic broadcast of
an existing type is PATCH, since no message shape changes and a client that ignores it is
unaffected. After `1.0` the same removal would be MAJOR, which is a reason to decide it before
then rather than after.

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

Two consequences of using real history, both of which decide behaviour the user notices:

- **A path that matches no route resolves to the Dashboard and the address bar is rewritten to
  `/`**, with `replaceState` rather than `pushState`. The static server hands the shell exactly
  those URLs (§2.15, D14), and leaving the address bar on the unmatched path makes the URL and
  the view disagree for the rest of the session: a later tap on Dashboard pushes `/`, so Back
  returns to the unknown path, which resolves to the Dashboard again and reads as a dead Back
  button.
- **Choosing the entry for the view already showing is not a navigation.** It changes nothing and
  pushes no history entry, so Back still leads out of the view rather than replaying it. On a
  phone the bar is easy to hit twice, and a router that stacked a duplicate would need two Backs
  to leave a view the user only entered once.

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

Two boundaries in that paragraph were left unsaid and cost a round of disagreement between the
implementation and the tests written against it, so they are pinned here:

- **`inert` may sit on the dialog or on the container around it.** Inertness inherits, so a
  wrapper carrying the attribute makes the dialog inside it inert just as effectively. Tests
  assert the sheet is inert, not which element holds the attribute.
- **The focus trap covers the modal container, not the dialog element alone.** A close
  affordance such as a backdrop button legitimately lives beside the dialog rather than inside
  it, and it belongs in the tab cycle: a keyboard user reaching the end of the entries should
  find the way out rather than wrap silently.

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

### 4.5.1 Presentation and safety decisions

Each of these was decided by the owner while reviewing an interactive mockup. They are recorded
here because a decision that lives only in a conversation is one the next agent will reinvent,
and because several of them have a plausible wrong answer that costs a rebuild to discover.

**Theme.** `prefers-color-scheme` is followed by default. An explicit control offers light, dark
and follow-system, and **the forced choice persists across launches**; follow-system is the
initial state, not a fallback for a missing value.

**Orientation is not locked.** `manifest.webmanifest` must not carry `"orientation"`. The
original intent was landscape for the Log only and portrait elsewhere, and that is not
achievable: `ScreenOrientation.lock()` is unavailable in iOS Safari, and the manifest field is
largely ignored there, so a lock either does nothing or removes the rail from the installed app
while leaving it in the browser. Orientation is handled by layout instead, which is what §4.5
already specifies.

**Density.** The compact switch changes layout only, never which values exist. The goal is an
app that is not a long scroll, so where a detail is secondary it moves behind a disclosure or a
popover, and where it answers "is my unit all right", it stays visible without interaction. A
density control that hides a value the operator is looking for has failed at the thing it was
added for.

**Two-step confirm** applies to: mode switch, reboot, shutdown (D9, D12) and **leaving PASV**
(§4.5.2). Entering PASV already confirms; leaving it did not, and the asymmetry was accidental.

**Ports are diagnostics, not settings.** The Settings view shows the negotiated ports read-only.
Making them editable means the plugin accepts writes to its own configuration and then restarts
to apply them, which drops the socket carrying the confirmation: the operation destroys the
channel that would report whether it worked. Ports are changed in `config.toml` on the unit.

**Writing to the unit sits behind an Advanced section with a disclaimer** that says plainly that
a bad edit can leave the pwnagotchi unbootable, and that recovery then means pulling the SD card
and using a reader to read logs or roll back. That is the real recovery path and the disclaimer
should name it rather than gesture at risk.

**Host validation accepts IPv4 only.** Not a simplification: the plugin binds IPv4 literals
only (D5.1, with the `/24` prefix floor), and `gen-cert.sh` refuses IPv6 outright because a
certificate carrying a DNS name would not be matched by iOS against the address the app connects
to. Accepting an IPv6 host would offer a path that neither the binding nor the certificate can
serve. Revisit only if all three change together.

**Log** carries both a level filter and a font-size control.

**Switching unit is done from the host list** in Settings. There is no separate device selector.

**The bottom bar sits above everything, and content stops above it.** A scrolled list ends
where the bar begins; nothing passes underneath it. The bar is translucent in appearance, and
that is styling rather than an invitation to scroll content beneath it: content half-visible
through a blur is not readable, and the last row of a list has to be reachable without
guesswork. The views area therefore reserves exactly the bar's outer height, border included,
and the two are one expression rather than two numbers that must agree (§10.5).

**Touch targets need separation as well as size.** 44px is the floor for how large a control
is, and says nothing about how far it sits from its neighbour. Those are different failures: a
small target is hard to hit, while two large targets flush against each other are easy to hit
*wrongly*, which matters most where the neighbour does something serious. The bar entries, and
any row of adjacent controls, carry a gap.

**Contrast is WCAG 2.1 level AA**: 4.5:1 for body text, 3:1 for large text (>= 24px, or >=
18.66px at weight >= 700) and for icons and other non-text indicators (1.4.11). AAA is not
adopted: 7:1 is not reachable on a dark palette without flattening it, and a level nobody can
meet gets waived case by case until it means nothing.

**No `background-image` behind text.** A gradient has a different contrast ratio at every point,
so text over it can be legible at one end and not the other, and no automated check can return
a verdict. Forbidding it is what keeps the contrast gate meaningful; the gate reports any it
finds rather than silently passing them.

### 4.5.2 Views

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
- **Settings**: host list (add/edit/remove; BT `172.20.10.2` prefilled, USB `10.0.0.2`
  suggested with a note that it is desktop-only — an iPhone cannot use the USB gadget path),
  ws/http ports, optional token, quick-connect from history, diagnostics (last error, latency,
  negotiated plugin version).

### 4.6 Geolocation (`lib/geo.ts`)

When `stats.gps.piFix` is false, request browser Geolocation (with permission) and push
`gps_data` on a sane interval or on significant change. Never push while `piFix` is true — the
on-Pi fix, whether from bettercap or gpsd, always wins.

**The push interval is at most 5 seconds.** It is not a free choice: `incoming/gps_data.json`
says the plugin drops browser coordinates after **10 seconds** of staleness, so an interval at or
above that guarantees the position the client believes it is supplying is periodically not there.
Five seconds gives one missed push of margin. Pushing on significant change *as well* is an
optimisation on top of the floor, never a replacement for it.

#### 4.6.1 The interface has four GPS states, and only three of them are one question

The obvious three — a fix from the Pi, no fix, no hardware fitted — leave out the one that is
**most common in practice**. A unit with no GPS hat, carried outdoors by a phone that has one, is
the ordinary case rather than the exception, and the protocol already supports it: the outgoing
`Gps` shape carries `source`, and `browser` is one of its values, stored by the plugin as the
lowest-priority source.

Note which message that is. `source` is on **`stats.gps`**, the shape the plugin sends. The
incoming `gps_data` the client pushes has only `latitude`, `longitude`, `accuracy` and
`message_id`, and it is `additionalProperties: false` — a client that adds `source` to its push
gets `bad_request`.

The four are **evaluated in order**, first match wins, because they are not mutually exclusive:
permission can be denied while the unit has a Pi fix, and the Pi fix is what matters then.
Precedence left to chance is the defect §2.6.2 already warns about.

| # | state | condition | what it means for a capture |
|---|---|---|---|
| 1 | Pi fix | `stats.gps.piFix` is true | position from bettercap or gpsd; the phone must not push |
| 2 | phone fix | `stats.gps.source == "browser"` and `stats.gps.fix` is true | the phone is the source; sidecars are written and captures reach the map |
| 3 | permission denied | **client-local**: the browser refused Geolocation | the phone *could* be the source and is not, and the user can change that |
| 4 | no fix | `stats.gps.fix` is false | no sidecar for captures made now |

Two things about this table are deliberate.

**Rows 1, 2 and 4 are read from `stats.gps`, never from client-local knowledge.** Row 3 is the
exception and is marked as such: whether the browser refused is knowledge only the client has,
and it is shown ahead of "no fix" because it is the case the user can do something about. The
tempting
definition of "phone fix" is "we have permission and we acquired a position", and it is wrong: the
plugin drops browser coordinates after 10 seconds, so a PWA backgrounded by iOS for fifteen
seconds stops pushing, the unit falls back to no fix, and a client trusting its own state still
shows "phone fix". That is the app telling the user something it does not know, which §4.3.1
rejects. The client's own permission state is an input to *what it should do*; only `stats.gps`
says what the unit actually has.

**"Not fitted" is not a fourth value of this enum**, which is why it is not in the table. It
answers a different question — what hardware the unit has — and it is not derivable from the
wire anyway: `capabilities.gpsSource` reports the *configured* `gps_source`, so `none` means
switched off in configuration rather than absent, and `auto` says nothing about a hat at all.
Where the interface mentions hardware it must not imply anything about whether captures are
located. Copy saying captures "get no sidecar and never reach the map" because the unit has no
GPS hat is **wrong whenever the phone is supplying a position**, which outdoors is most of the
time.

Browser geolocation also needs its own refusal path: permission denied is a state the user chose
and can change, and it is not the same as having no fix.

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
- **no coverage exclusions**: `# pragma: no cover` is banned outright. The 85% floor only means
  something if nobody can opt out of it.
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
| Tests | The whole of §10, including the 85% line-and-branch coverage gate |
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
| `test_battery.py` | the tier order of §2.11 — `pisugarx_ext`, then `pisugarx`, then the I2C bus as a last resort that a loaded provider and a disabled `pisugar` both suppress; every tier failing yields nulls and raises nothing. Then the rules that make the descent per-field rather than per-tier: a tier that supplies only the level takes the charge state from the tier below, a second plugin fills only what the first left missing, and a field no tier supplies is null. The candidate order within a tier (`battery_level` over the vaguer percentage names, `charging` over `power_plugged` and `plugged`), callables called and a raising one treated as a miss, an attribute present and `None` a miss. Percentages: bounded to 0-100, a numeric string accepted and settling the field, a refusal never reported and never stopping the tier or the candidate below, and never reaching the wire. `available` and `capabilities.pisugar` latch on the first answer from either field and do not un-latch on a later refusal. The nested `client` traversal: the reference unit and upstream `pisugarx` shapes, an attribute on the plugin outranking the same one on the client, a flat plugin with no client, a client whose `ready` is false not traversed while the plugin itself still is, and `battery_charging` probed on neither. `_as_charging` as a value grammar in its own right — `bool` as itself, `0` and `1` only among ints, the enumerated spellings after strip and lower, and a refusal for every other int, every float, the empty string, the string `false`, `None` and an unrelated type, each contrasted with what plain `bool()` would have said |
| `test_controls.py` | `set_mode` calls `pwnagotchi.restart` with `"AUTO"`/`"MANU"` and **never assigns `agent.mode`**; broadcasts `restarting` before restarting; no-op when already in the mode; `set_pasv` errors outside auto and when `pasv_mode` is absent, emits the right event otherwise, and does not read `passive` back; reboot/shutdown call the module-level functions |
| `test_tls_startup.py` | missing cert, missing key, unreadable key and malformed PEM each result in **zero listeners** and a logged error — the no-plaintext-fallback rule |
| `test_auth.py` | token accepted, rejected, wrong type, missing; `compare_digest` used; unauthenticated connection closed after `auth_timeout`; auth skipped when no token configured |
| `test_binding.py` | selection: literal match, containment in a block, a local address matching nothing; every entry-validation row of §2.3.1, including the `/24` floor, host bits normalised, a non-list value, and the empty-after-rejection case; a legacy `interfaces` key warns and binds nothing; the reconciliation table: appear, disappear, a new address in the same block, none matching, `EADDRINUSE`; asserts `0.0.0.0` is never passed to a bind call and that no bind decision reads an interface name |
| `test_static_server.py` | `.webmanifest` and `.js` content types; SPA fallback serves `index.html`; `..` rejected with 400; no directory listing; `no-cache` on index and service worker |
| `test_hooks.py` | the hook surface the agent calls: both `on_handshake` argument shapes (F20), sidecar written only on a fix, `on_peer_detected`, `on_wifi_update` skipping non-mappings, `on_channel_hop`, `on_ui_update` pushing only when the face or status text changed, the four mood hooks; every push validated against `docs/schemas/outgoing/`; the router-absent and pre-`on_ready` windows; **a failing broadcast is logged and swallowed in every one of them**, because `plugins.on()` runs these on the agent's thread (F8) and an escaping exception reaches the UI loop |
| `test_deps.py` | the real `Deps` defaults nothing else drives: `spawn`, `run_command` exit statuses and a missing binary, `list_local_ipv4` returning IPv4 literals only and never `0.0.0.0`, `read_pisugar_i2c` (a shape guarantee that holds on any host, a two-tuple that never raises, plus a no-bus case whose precondition is **established** rather than assumed, and a bus that imports but refuses to open; then the register map of SPEC 2.11.1 and both rules of 2.11.2, which is where most of that file now is: bit 7 of `0x02` asserted over all 256 values of the byte rather than the two observed, every other bit of it shown not to matter, the registers actually touched compared as a set so a word read is visible and not only its result, and the transport-versus-content line asserted directly rather than only through its two instances), and `read_gpsd` against a fake gpsd socket — the `?WATCH` handshake, reading past `VERSION`/`DEVICES`, a damaged line, a refused connection, and a silent server bounded by the timeout. **Both backends of every seam**, not only the fallback: `tests/fakes/i2c_stub/` and `tests/fakes/netifaces_stub/` put the libraries on `sys.path` so the branches that run on a unit that has them are exercised, instead of being unreachable by construction because injection is how you avoid running them |
| `test_broadcast_cadence.py` | §4.3.7: `stats` broadcast on the `keepalive_interval` tick, to every authenticated client and to no unauthenticated one; **zero `agent.session()` calls on that path** (§2.5), since a periodic broadcast that blocks is worse than no broadcast; `keepalive_interval` clamped to 5-20 with the clamp logged, and `0` clamping to the default 20 rather than to the floor; `session_poll_interval` clamped to 1-5, which no longer sets the broadcast spacing but does set how soon `sessionAge` is reset after bettercap recovers, so a recovered unit leaves `degraded` promptly; **the `keepalive_interval` ceiling bounds how late the client hears of a stall**, not whether it is reported: the threshold is derived from the refresh episode and not from the spacing (§2.4), so a value above the ceiling delays the badge rather than falsifying it. The floor is about payload cost over BT PAN, not staleness: below it the broadcast is more frequent, not less |

A rule the `read_pisugar_i2c` row exists to state: **a test may not assert a property of the
machine it happens to run on.** The first version of that test asserted the read returns nulls
"when the bus is not there", established nothing, and passed only because the build host has no
I2C. The reference hardware is a Pi with a PiSugar 3, so it would have gone red on the one
machine the plugin is written for. Either the precondition is set up by the test or the
assertion is about the guarantee rather than the environment.

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

  Then everything §4.3 decides, because a rule with no test is a rule until somebody disagrees
  with it. **Backoff**: each delay drawn from `[0, cap]` and never above the cap, over enough
  draws to show it is not always at it. **States**: `connecting` held until the first `stats` of
  the connection rather than entering `degraded` on a reconnect after a long absence; `degraded`
  entered on a `stats` whose `sessionAge` is at or over 55 s, or `null`, and left only on one
  whose `sessionAge` is back under it and not `null`, never merely because a frame arrived. **`unauthorized`**, whose
  three cases are the ones a test will otherwise get backwards: an `error` frame with that code
  enters it with reason *rejected*; a bare 1008 with **no** `auth` frame sent enters it with
  reason *required*; a bare 1008 **after** an `auth` frame was sent is `offline`. Reconnection is
  suspended in `unauthorized` and resumes when the token changes. **`restarting`**: the state
  survives the close and the backoff runs underneath it; `mode_change` and `reboot` differ only
  in patience, both falling through to `offline`; `shutdown` attempts no reconnection and has no
  patience timer at all. **The queue**: a read re-sent on reconnect; a
  state command refused outside `connected`, never queued, and discarded and reported failed if
  the socket drops while it is in flight; `gps_data` discarded on drop rather than replayed.
  The no-optimism rule of §4.3.4 is **not** here: the mode badge following `stats` rather than
  the tap is a store and component assertion, and `ws.spec.ts` is scoped to a mock socket. It
  belongs to `stores.spec.ts` below, which is where it is listed.
- `stores.spec.ts`: each push message updating the right store; `capabilities.pasv` gating the
  PASV control; §4.3.4, the mode and PASV indicators derived from `stats` and never from the tap,
  showing the request as pending in between and **not** reverting silently if the command fails;
  §4.6.1's GPS states resolved first-match-wins in the stated order.
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

### 10.6 Tools (`tests/tools/`)

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

`tests/tools/test_check_coverage.py` covers the gate script described in §10.7.1. It drives it as
a **subprocess** against a throwaway checkout rather than calling `main()`, because the exit
status, the printed annotation and the *absence of a traceback* are the contract, and none of the
three is observable in-process.

### 10.7 Coverage gate

**The floor is 85% of lines and branches. The target is as high as the code allows.** Those are
two different numbers on purpose, and confusing them is how a coverage gate turns into
theatre.

- **CI fails below 85%.** `pytest --cov=plugin --cov-branch --cov-fail-under=85`, and vitest
  thresholds at 85. **Branch** coverage, not line coverage: `if a and b` has four outcomes, not
  one.
- **85 is not the goal.** Writing exactly enough tests to reach 85 and stopping is a misreading
  of this section. The number exists so a gap fails the build, not so effort can stop once it
  is cleared.
- **A drop is reported even when it stays above the floor.** The current figure is recorded and
  compared on every run, and a fall produces a warning rather than a failure. Without that, a
  regression from 100% to 86% passes silently, which is the failure mode a flat floor has.
- **`# pragma: no cover` is banned.** A CI grep fails the build on any occurrence. A lower floor
  makes this rule more important, not less: an opt-out plus a floor is a number that means
  nothing.
- **The frontend gate covers `src/lib/` and `src/shell/`.** The shell holds the focus trap, the
  `inert` handling and the Escape and Tab paths, which is logic, and logic that breaks quietly:
  an accessibility regression does not throw, it just stops helping someone. `src/views/` stays
  outside, because that genuinely is markup.

100% was the earlier rule and it was wrong in a specific way rather than merely ambitious. It is
reachable for a parser and unreachable for a component whose compiled output has branches with
no source-level counterpart, so a single global 100 either forces the exclusion of everything
awkward, which is how the shell came to be outside the gate in the first place, or produces a
threshold nobody can meet and everybody learns to work around.

Two honest limits, and what is done about each:

1. **Branch coverage, at any percentage, is not "every possible case."** It proves a path was
   executed, not that the assertion behind it was meaningful. A test that executes a branch and
   asserts nothing useful counts exactly the same as one that pins it. The complement is
   **mutation testing** (`mutmut`,
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

**The seams do not cover themselves, and that gap is closed separately.** Injection reaches every
*caller* of a seam and, by construction, never the default behind it: injecting is precisely how
you avoid running it. So the branches that execute on a unit with `smbus2` or `netifaces`
installed were unreachable from the suite, which is a poor place for a blind spot — that is the
code that only ever runs on the device. `tests/fakes/i2c_stub/` and `tests/fakes/netifaces_stub/`
put those libraries on `sys.path`, in the manner `tests/fakes/pwnagotchi_stub/` already
established, rather than adding hardware libraries to `tests/requirements.txt`.

Unlike the pwnagotchi stub, these two are deliberately **permissive**. That stub is exact because
§11 pins every pwnagotchi symbol, so a missing attribute there is the guard against an invented
API. `netifaces` and `smbus2` are third-party libraries with published interfaces this
specification does not enumerate, so a stub trimmed to what the plugin happens to call today
would fail correct code tomorrow.

Covering those branches immediately found three defects the injected path could never have shown
(issue #68), which is the argument for doing it rather than a bonus from having done it.

Two things are deliberately **not** seams on `Deps`. The filesystem is reached through
configured paths, so a test points them at a `tmp_path` and needs no indirection. The
`websockets` server factory is resolved at call time by `resolve_ws_serve()` and the connection
handler is passed to `Listeners`, because the transport is covered by the integration test
(§10.4) rather than by unit tests — putting it on `Deps` would widen the seam surface without
buying a single covered branch.

### 10.7.1 What `.github/check_coverage.py` guarantees

The floor, the recorded figure and the drop warning above are prose; this is the contract the
script implements, stated here so it is reviewable and so the numbers in it do not live in one
file only. Tested by `tests/tools/test_check_coverage.py`.

- **One argument, `plugin` or `frontend`.** The plugin figure comes from `coverage.json`
  (`--cov-report=json`), the frontend one from `frontend/coverage/coverage-summary.json`
  (`json-summary`). Both are build artifacts and both are gitignored.
- **Which number is "lines".** Under `--cov-branch`, coverage.py publishes three percentages, and
  the obvious one is the wrong one. `percent_covered` is the **combined** statements-and-branches
  mean — that is what `--cov-fail-under` gates on — while `percent_statements_covered` is lines
  and `percent_branches_covered` is branches. Being a weighted mean, the combined figure can sit
  above 85 while lines sit below it, so this script gates on the separate figures and `"lines"`
  means lines for both components. The two gates therefore answer slightly different questions on
  purpose: `--cov-fail-under` is the runner's own coarse floor, and this script is the one that
  enforces what §10.7 actually says.
- **A fall of more than 0.1 percentage points below the recorded figure warns.** The tolerance
  exists because a single line is worth roughly that much at the current size, and it is compared
  against the committed baseline rather than the previous run, so small falls cannot accumulate
  unseen.
- **`.github/coverage-baseline.json` distinguishes "not recorded" from "malformed."** An absent
  entry, or one that is `null`, means no figure has been recorded yet: the script warns and
  passes. An entry that is present but is not a pair of numbers is an error, because the one thing
  a drop detector must not do is fail open.
- **`--update` refuses to record a figure below the floor.** The build fails on it regardless, so
  writing it would only leave a committed number that misleads the next reader about where the
  project stands.

Note that any targeted local run rewrites `coverage.json` with a partial figure, so running the
script by hand after one reports nonsense. Only CI, which always runs the whole suite, invokes it.

### 10.7.2 Where the acknowledgment comes from

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
| F6 | `agent._current_channel` is a plain attribute; `agent.get_current_channel()` is defined **twice** in `agent.py` and the second definition (returning the attribute) wins. **Machine-checked entries**: F6, F6b pin `_current_channel`'s presence in `agent.py` and that `get_current_channel` is defined twice. | `pwnagotchi/agent.py:153` and `:230` |
| F7 | `agent.session()` performs `requests.get` against the bettercap REST API with `api_timeout = 30`. It is synchronous and blocking. | `pwnagotchi/bettercap.py:26,52-56` |
| F8 | `plugins.on(event_name, *args)` enqueues work on each plugin's thread and returns nothing; dispatch is asynchronous | `pwnagotchi/plugins/__init__.py:54,165` |
| F9 | Module-level `pwnagotchi.shutdown()`, `pwnagotchi.reboot(mode=None)` and `pwnagotchi.restart(mode)` exist. `restart` uppercases `mode`, touches `/root/.pwnagotchi-auto` or `/root/.pwnagotchi-manual`, then restarts the `bettercap` and `pwnagotchi` services. **Machine-checked entries**: F9a, F9b, F9c pin that `restart` takes a `mode` parameter and that `reboot` and `shutdown` are still defined in `pwnagotchi/__init__.py`; module-levelness is not machine-checked, since the checker walks every function including methods, and what each function does once called is not machine-checked either. | `pwnagotchi/__init__.py:110,129-140,142` |
| F10 | Mode is selected at process start: `cli.py` calls `do_manual_mode(agent)` or `do_auto_mode(agent)` based on `--manual`, and those set `agent.mode = 'manual'` / `'auto'` as a label. Nothing re-reads `agent.mode` to change behaviour. | `pwnagotchi/cli.py:24-27,45-48,208-215`; `pwnagotchi/agent.py:56` |
| F11 | The `agent.mode == "manual"` comparison is the fork's own convention | `pwnagotchi/plugins/default/webcfg.py:648` |
| F12 | `pasv_mode.py` v1.3.0, class `PasvMode`, defines the attribute `self.passive` and the handlers `on_pasv_toggle`, `on_pasv_on`, `on_pasv_off`; the plugin registers under the key `pasv_mode` in `plugins.loaded` | [`abonforti/pwnagotchi-plugins`](https://github.com/abonforti/pwnagotchi-plugins/blob/master/pasv_mode.py) |
| F13 | The stock `webcfg` plugin switches mode with `threading.Thread(target=restart, args=(self.mode,), daemon=True).start()` | `pwnagotchi/plugins/default/webcfg.py:679` |
| F14 | The handshake directory is `config['bettercap']['handshakes']`, default `/etc/pwnagotchi/handshakes`; the agent creates it if missing | `pwnagotchi/defaults.toml:231`; `pwnagotchi/agent.py:58-59,89` |
| F15 | `pwnagotchi.utils.total_unique_handshakes(path)` globs `*.pcapng` only | `pwnagotchi/utils.py:520-522` |
| F16 | `Peer` members: attributes `first_met`, `first_seen`, `prev_seen` (`datetime`, or `str` on a parse fallback), `last_seen` (float epoch), `encounters`, `session_id`, `last_channel`, `rssi`, `adv`; methods `inactive_for()`, `first_encounter()`, `is_good_friend()`, `face()`, `name()`, `identity()`, `full_name()`, `version()`, `pwnd_run()`, `pwnd_total()`, `uptime()`, `epoch()`, `is_closer()`. **Machine-checked entries**: F16, F16b pin that `Peer` defines the eight methods `normalise_peer` calls and that the seven attribute names it reads appear in `peer.py`; the remaining methods and attributes above are not machine-checked. | `pwnagotchi/mesh/peer.py` |
| F17 | Log path is `config['main']['log']['path']`, default `/etc/pwnagotchi/log/pwnagotchi.log` | `pwnagotchi/defaults.toml:138-140` |
| F18 | `pwnagotchi.ui.web` is a **package** whose `__init__.py` defines `frame_path = '/var/tmp/pwnagotchi/pwnagotchi.png'`, `frame_format = 'PNG'`, `frame_ctype = 'image/png'`, and `frame_lock = threading.Lock()` | `pwnagotchi/ui/web/__init__.py` |
| F19 | Bettercap GPS lives at `session()['gps']` with capitalised keys `Latitude`, `Longitude`, `Altitude`. The stock plugin skips writing when latitude or longitude is falsy, and dumps the raw dict to `<name>.gps.json`. `Updated`, `FixQuality`, `NumSatellites` and `HDOP` come from **bettercap's** payload, not from any pwnagotchi source, so they are used defensively and cannot be verified against this repository. | `pwnagotchi/plugins/default/gps.py:47-61` for the first three |
| F20 | `on_handshake` is fired from two paths with different argument types: `plugins.on('handshake', self, filename, ap_mac, sta_mac)` (strings) and `plugins.on('handshake', self, filename, ap, sta)` (dicts) | `pwnagotchi/agent.py:396,404` |
| F21 | `pyproject.toml` declares `websockets` with **no version constraint**; `requires-python = ">=3.11"`. That is a **floor, not the shipped version** - see F26 and F27 | `pyproject.toml` |
| F26 | The image installs its Python under `/opt/.pwn/`, and that tree is **3.13**, not the `>=3.11` floor of F21. CI must test the shipped version | `stage3/06-patches/files/01-motd` reads `/opt/.pwn/lib/python3.13/site-packages/pwnagotchi/_version.py`; `/opt/.pwn/bin` is likewise hardcoded in `stage3/06-patches/files/profile`, `sudoers` and `pwnagotchi-launcher`. Confirmed as 3.13.5 on a unit running 2.9.5.6 |
| F27 | The **installed `websockets` version is not knowable from upstream** and must not be assumed. `pyproject.toml` pins nothing (F21), so it is whatever pip resolved when that image was built, and `plugins/default/auto-update.py` runs `pip install` on the unit afterwards. One unit running 2.9.5.6 was observed at 17.0.1 - evidence that a modern major occurs in the field, not that every unit has one | `pwnagotchi/plugins/default/auto-update.py`; `pyproject.toml` |
| F22 | Upstream `pwnios.py` binds `websockets.serve(self._handle_client, "0.0.0.0", 8082, ...)` and reads `self.agent.access_points` before falling back to `_access_points` — both are bugs this fork fixes | `BraedenP232/PwnIOS/pwnios.py:292-293,653-656,678-680` |
| F23 | Custom plugins are loaded from `config['main']['custom_plugins']`, whose default is `/usr/local/share/pwnagotchi/custom-plugins/`. The key is optional: `load_from_path` is called only `if 'custom_plugins' in config['main']`. **Machine-checked entries**: F23, F23b pin the literal default custom-plugins path in `defaults.toml` and the `'custom_plugins' in` guard in `plugins/__init__.py`. | `pwnagotchi/defaults.toml:27`; `pwnagotchi/plugins/__init__.py` |
| F24 | The user configuration the image merges over the defaults is `/etc/pwnagotchi/config.toml` (`--user-config`), and `plugins/__init__.py` writes back to that same path | `pwnagotchi/cli.py`; `pwnagotchi/plugins/__init__.py` |
| F25 | The shipped shell profile aliases `custom` to `/etc/pwnagotchi/custom-plugins/`, which is **not** the `defaults.toml` value. The two disagree, so the directory must be resolved from the running configuration and never hardcoded | `stage3/06-patches/files/profile` vs `pwnagotchi/defaults.toml:27` |
| F28 | `agent.view()` returns whatever the agent was constructed with, which on a running unit is **not a bare `View` but a `pwnagotchi.ui.display.Display`**: `cli.py` builds a `Display` and passes it as `view=`. `Display` subclasses `View` and **defines no `get` of its own**, so `agent.view().get(key)` lands on `View.get` - the whole reason the delegation chain below holds and the plugin needs no widget handling. `View.get(key)` delegates to `State.get`, whose contract is **the element's `.value`, never the widget**, and `None` for a key the state does not hold. The initial state always defines `'face'` and `'status'`, so on a running unit both keys exist and both carry a `str`; a `None` can therefore only mean a future version renamed a key. Reading the view is passive - `get` takes the state lock and returns, with no render and no event. **Machine-checked entries**: F28a-d pin `Agent.view`, the body of `View.get`, the body of `State.get`, and the absence of a `get` on `Display`. That last entry exists because the absence is what makes the other three load-bearing, and an absence nobody asserts is an assumption | `pwnagotchi/cli.py:198` (`display = Display(config=config, ...)`), `:204` (`Agent(view=display, ...)`); `pwnagotchi/ui/display.py:10` (`class Display(View)`, no `get` in its body at `v2.9.5.6` or `4a03bf169e2f`); `pwnagotchi/agent.py:41` (`self._view = view`), `:68-69` (`def view`); `pwnagotchi/ui/view.py:161-162` (`def get` → `return self._state.get(key)`), `:75` (`'face': Text(value=faces.SLEEP, ...)`), `:84` (`'status': Text(value=self._voice.default(), ...)`); `pwnagotchi/ui/state.py:30-32` (`return self._state[key].value if key in self._state else None`) |
| F29 | `pisugarx_ext.py` defines **two** classes. `plugins.loaded['pisugarx_ext']` is the `PiSugar(plugins.Plugin)` at line 544, which holds its hardware client as `self.ps = PiSugarServer()`. The battery state lives on that client, not on the plugin: `PiSugarServer.__init__` sets `battery_level` and `power_plugged`, and the accessors are `get_battery_level()` and `get_battery_power_plugged()`. **The attributes are kept current, and the accessors touch no hardware**: `start_timer()` runs `update_value()` on a `daemon=True` thread, and that loop rewrites the attributes on a `time.sleep(3)` cycle; each accessor is a one-line `return` of the corresponding attribute. **`ready` says whether any of it is real**: it is `False` from the constructor and is set `True` only at the end of `_connect_device`, after a device has been found and its 256 registers have been read, and nothing anywhere in the file sets it back to `False`. `start_timer()` is likewise called only after a device is found, so a loaded plugin with no PiSugar attached loops on `time.sleep(5)` forever with `ready` `False`, no refresh running, and `battery_level` and `power_plugged` still at their constructor values `0` and `False`. The plugin mirrors the flag as `self.ready = self.ps.ready`. **`battery_charging` is dead**: it is assigned `0` in that same constructor and nowhere else in the file, and `get_battery_charging()` has a body of `pass`, so it returns `None`. Upstream `jayofelony` `pisugarx.py` has the identical shape, so the same traversal reaches both tiers; that file is on branch **`noai`** and does **not** exist on `master`, which 404s | [`abonforti/pwnagotchi-plugins` @ `abbc8777`](https://github.com/abonforti/pwnagotchi-plugins/blob/abbc87774dbd2ca1ff4b8f83b6210ec1984fb6be/pisugarx_ext.py) lines 48 (`class PiSugarServer`), 54 (`self.ready = False`), 60-61 (`battery_level`, `battery_charging`), 63 (`power_plugged`), 75-96 (`_connect_device`, `time.sleep(5)` when no device is found), 99 (`self.start_timer()`, reached only after one is), 102 (`self.ready = True`), 105-109 (`start_timer`, `timer_thread.daemon = True` at 108), 111 (`def update_value`), 203 and 206 (`time.sleep(3)`), 330-336 (`get_battery_level`), 528-534 (`get_battery_charging`, body `pass`), 520-526 (`get_battery_power_plugged`), 544 (`class PiSugar(plugins.Plugin)`), 655 (`self.ps = None`), 658 (`self.ps = PiSugarServer()`), 664 (`self.ready = False`), 731 and 947 (`self.ready = self.ps.ready`), and commit [`abbc8777`](https://github.com/abonforti/pwnagotchi-plugins/commit/abbc87774dbd2ca1ff4b8f83b6210ec1984fb6be) (whose message is about display behaviour when the PiSugar does not answer on I2C; the bit-7 belief is in the file it touches, as a comment at line 985, see 2.11.1). Upstream: [`jayofelony/pwnagotchi` branch `noai` @ `9fc1b6f0`, `pwnagotchi/plugins/default/pisugarx.py`](https://github.com/jayofelony/pwnagotchi/blob/9fc1b6f0cc4b7002ecbf5f3ab1e7623109fe5bf9/pwnagotchi/plugins/default/pisugarx.py) lines 48 (`class PiSugarServer`), 54 (`self.ready = False`), 60-61, 63, 102 (`self.ready = True`), 105-109 (`start_timer`, `daemon = True` at 108), 111 (`def update_value`), 202 and 205 (`time.sleep(3)`), 329-335 (`get_battery_level`), 519-525 (`get_battery_power_plugged`), 543 (`class PiSugar(plugins.Plugin)`), 566 (`self.ps = None`), 569 (`self.ps = PiSugarServer()`), 575 (`self.ready = False`), 619 and 811 (`self.ready = self.ps.ready`) |

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
