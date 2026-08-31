# openpwnagotchi-companion — Build Specification

Version 2. Supersedes v1. Every API claim in this document has been verified against one
tag of `jayofelony/pwnagotchi`, and **that tag is named in exactly one place**:
`verified_against` in `.github/pinned_symbols.json`. The evidence is in §11 (Pinned Facts).
Claims that could not be verified are marked **UNVERIFIED** and carry an explicit instruction
on what to do instead of guessing.

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
├── .claude/
│   └── agents/                       # the agents CLAUDE.md's workflow names
│       ├── companion-implementer.md
│       ├── companion-qa.md
│       ├── companion-reviewer.md
│       ├── companion-security-auditor.md
│       └── companion-test-author.md
├── .githooks/                        # local gates, installed per CONTRIBUTING.md
│   ├── pre-commit                    # owner denylist, before a commit exists (§13)
│   └── pre-push
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   ├── config.yml
│   │   ├── connection_problem.yml
│   │   └── feature_request.yml
│   ├── workflows/
│   │   ├── ci.yml                    # lint + tests + build on push/PR (§5.1)
│   │   ├── codeql.yml                # CodeQL, Python and JavaScript
│   │   ├── pull-request.yml          # labelling and documentation-only auto-merge
│   │   ├── release.yml               # build dist.tgz + create Release on tag (§5.2)
│   │   └── upstream-drift.yml        # scheduled run of check_pinned_facts.py
│   ├── check_coverage.py             # the coverage ratchet (§10.7.1)
│   ├── check_no_inline_script.py     # inline-script gate over the built page (§2.15.1)
│   ├── check_pinned_facts.py         # §11 against upstream (§11.2)
│   ├── check_release_version.py      # tag against CHANGELOG and package.json (§5.2)
│   ├── check_schemas.py              # JSON Schema validity and framing invariants (§5.1)
│   ├── check_secrets.py              # generic credential scan (§5.1)
│   ├── check_shipped_files.py        # what ships, and nothing else (§13.2)
│   ├── CODEOWNERS
│   ├── coverage-baseline.json        # the recorded figures the ratchet compares against
│   ├── dependabot.yml
│   ├── labels.json                   # the label taxonomy (§6.1)
│   ├── pinned_symbols.json           # the pinned facts, and what they were verified against (§11)
│   └── sync_labels.sh                # applies labels.json to the repository (§6.1)
├── docs/
│   ├── assets/
│   │   └── banner.jpg
│   ├── schemas/
│   │   ├── incoming/                 # one schema per client->plugin message type
│   │   │   └── ...
│   │   ├── outgoing/                 # one schema per plugin->client message type
│   │   │   └── ...
│   │   └── common.json               # shared defs: AP, Handshake, Peer, Gps, Envelope
│   ├── CERTIFICATES.md               # deep dive on the cert constraints and why
│   ├── PROTOCOL.md                   # human-readable rendering of docs/schemas
│   └── SETUP.md                      # full setup: CA, cert, iOS trust, install, first run
├── frontend/
│   ├── public/
│   │   ├── icons/                    # 180/192/512 PNG + maskable
│   │   │   ├── apple-touch-icon-180.png
│   │   │   ├── icon-192.png
│   │   │   ├── icon-512.png
│   │   │   └── icon-maskable-512.png
│   │   └── manifest.webmanifest
│   ├── src/
│   │   ├── __tests__/                # vitest specs, see §10.5
│   │   │   ├── helpers/              # assertions shared by more than one spec
│   │   │   │   └── ...
│   │   │   └── ...
│   │   ├── lib/
│   │   │   ├── controls.ts           # the control messages and what may send one
│   │   │   ├── format.ts             # value to string, the §4.5.1.1 rules a view must not hold
│   │   │   ├── geo.ts                # browser Geolocation acquisition + push to plugin
│   │   │   ├── lists.ts              # sorting and identity for the list views
│   │   │   ├── log.ts                # log line parsing and the level filter
│   │   │   ├── map.ts                # the track, the viewport and the tile origin (§4.5.2.7)
│   │   │   ├── peers.ts              # peer identity and presence
│   │   │   ├── protocol.ts           # GENERATED from docs/schemas - do not hand-edit
│   │   │   ├── router.ts             # the seven routes of §4.5, and nothing more
│   │   │   ├── screen.ts             # orientation and the landscape rail
│   │   │   ├── session.ts            # the one place a client is built and attached (§4.8)
│   │   │   ├── settings.ts           # persisted connection settings (localStorage)
│   │   │   ├── stores.ts             # Svelte stores
│   │   │   ├── viewRefresh.ts        # one place decides how often a view refetches
│   │   │   ├── wifi.ts               # the Nearby and Captured segments' data
│   │   │   └── ws.ts                 # WebSocket client: connect, reconnect, queue, heartbeat
│   │   ├── shell/                    # the navigation shell (§4.5), not a view
│   │   │   ├── MoreSheet.svelte      # dialog holding Peers, Mirror, Settings
│   │   │   ├── Nav.svelte            # bottom bar in portrait, leading rail in landscape
│   │   │   └── Segmented.svelte
│   │   ├── views/
│   │   │   ├── Dashboard.svelte
│   │   │   ├── Log.svelte
│   │   │   ├── Map.svelte
│   │   │   ├── Mirror.svelte
│   │   │   ├── Peers.svelte
│   │   │   ├── Settings.svelte
│   │   │   └── WiFi.svelte           # Nearby + Captured segments, see §4.5
│   │   ├── app.css                   # global styles, safe areas, dark theme
│   │   ├── App.svelte
│   │   └── main.ts
│   ├── tests/
│   │   └── e2e/                      # Playwright, four projects (§10.5)
│   │       └── ...
│   ├── .npmrc                        # the Node floor is enforced, not warned about (§5.1.1)
│   ├── index.html
│   ├── package-lock.json             # COMMITTED - `npm ci` in CI requires it
│   ├── package.json
│   ├── playwright.config.ts
│   ├── svelte.config.js
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── vitest.config.ts
├── plugin/
│   └── companion.py                  # the pwnagotchi plugin (installable, flat)
├── tests/
│   ├── fakes/
│   │   ├── fixtures/                 # bettercap session JSON, handshake dirs, log samples
│   │   │   ├── handshakes/
│   │   │   │   └── ...
│   │   │   └── ...
│   │   ├── i2c_stub/                 # importable stand-in, PiSugar over smbus2
│   │   ├── netifaces_stub/           # importable stand-in for netifaces
│   │   ├── pwnagotchi_stub/          # importable stand-in for the real package
│   │   └── README.md
│   ├── tools/
│   │   ├── test_certs.py             # gen-ca.sh / gen-cert.sh assertions, see §10.6
│   │   ├── test_check_coverage.py
│   │   ├── test_check_release_version.py
│   │   ├── test_install.py
│   │   └── test_pinned_facts.py
│   └── ...
├── tools/
│   ├── gen-ca.sh                     # create a private CA (idempotent)
│   ├── gen-cert.sh                   # issue an iPAddress-SAN server cert from that CA
│   ├── gen-protocol-types.mjs        # docs/schemas/*.json -> frontend/src/lib/protocol.ts
│   └── install-on-pi.sh              # pull latest Release dist.tgz to the Pi serve dir
├── .gitignore
├── CHANGELOG.md                      # Keep a Changelog, see §12
├── CLAUDE.md
├── CONTRIBUTING.md
├── LICENSE                           # GPL-3.0
├── pytest.ini                        # coverage configuration, and the ban on pragma: no cover
├── README.md                         # top-level, see §7
├── ruff.toml                         # E and F only, argued in §5.1
├── SECURITY.md                       # threat model + no-secrets policy, see §6.4
└── SPEC.md                           # this file
```

Note: the plugin lives in `plugin/companion.py`, NOT the repo root. It is installed by the
install script or by manual copy, documented in SETUP.md — do NOT rely on the flat-root
`custom_plugin_repos` unpack for this repo.

### 1.1 The tree above is checked, and here is what it claims (issue #201)

A tree in a specification decays silently. This one named four files that did not exist, one of
them a CI gate described in §5.1 in enough detail to be believed, and it named two of the seven
check scripts in `.github/` while missing a hook directory and the whole end-to-end suite.
The fourth was `plugin/README.md`, "plugin-specific install/config (mirrored in `__help__`)",
which nothing else in this document or in the repository refers to: it is removed rather than
written, and it is named here because a removal nobody records reads later as an oversight.
Nobody noticed by
reading it, which is the point: it was found by a script comparing it to `git ls-files`, and the
fix that lasts is that comparison running in CI rather than this particular correction.

So the tree is a claim with a contract, and `tests/test_spec_tree.py` fails the build when the
claim stops being true.

- **Every path named exists** as a tracked file or a tracked directory. A name with nothing
  behind it fails, whether it was deleted, renamed, or never written.
- **Every tracked directory is named.** A new directory is a new area of the repository, and the
  cost of adding one line here is the point rather than an inconvenience.
- **A directory whose entries end with `...` is not enumerated for files.** Its subdirectories
  are still named and still checked; only the file list is open. This is where the test suites
  live: a repository whose every new test costs a SPEC edit gets fewer tests, and the tree
  carries nothing about `test_gps.py` that its name does not.
- **A directory named with no children under it at all is opaque**, and nothing inside it is
  checked. There are three, all of them importable stand-ins for packages this project does not
  own: `tests/fakes/pwnagotchi_stub/`, `tests/fakes/i2c_stub/` and
  `tests/fakes/netifaces_stub/`. Their internal shape is upstream's, not a decision of ours, and
  pinning it here would be pinning somebody else's layout.
- **The comments are prose and are not checked.** They say why a file exists; a stale one is a
  review finding, not a build failure.
- **The repository root is enumerated**, like any other directory without a `...` in it. A new
  top-level file costs a line here, and a top-level file is the one addition that should.

`.gitignore`d files are not tracked and so are not named: `frontend/node_modules/`,
`frontend/dist/`, `.venv/` and the rest are absent from the tree because they are absent from
the repository, and the tree describes what is committed rather than what is on a working host.

The general form is worth stating once, because this is the second instance.
`.github/check_shipped_files.py` declares what ships and fails on anything else in that
directory (§13.2). §1's tree is the same kind of claim about the repository as a whole, and
until issue #201 nothing compared the two. A claim about a set of files, written by hand, is a
claim that needs a test.

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
session_poll_interval = 5          # bettercap session refresh, clamped 1-5 (SPEC 2.4)
rebind_interval = 30               # re-enumeration, clamped 5-300, 0 means the floor (SPEC 2.4)
keepalive_interval = 20            # stats and keepalive broadcast, clamped 5-20, 0 means default (SPEC 2.4)
handshake_dir = ""                 # optional; where the captures are. Empty = ask the agent (SPEC 2.5)
log_path = ""                      # optional; the log to read. Empty = ask the agent (SPEC 2.6.0.1)
```

Every value has a hardcoded default in the plugin matching the table above. Missing keys must
never raise; use `self.options.get(key, default)`.

**Three keys were removed rather than implemented (issue #156).** `save_gps_log`, `gps_log_path`
and `mirror_auto_interval` were accepted, defaulted, listed in this table, and read by no code in
`plugin/` or `frontend/src/`. A key that is advertised and does nothing is worse than a missing
one: the owner who sets it has been answered, and the answer is false. `mirror_auto_interval` was
the clearest case, a plugin setting for an interval the client owns (§4.5.2.6) and which the
plugin has no way to enforce. They are gone from the defaults and from this table, so §2.2.1 now
names them as unknown keys, which is the true statement: a config carrying one of these is a
config asking for something this plugin does not do.

#### 2.2.1 An unknown key is named, not ignored (issue #137)

A misspelled key is silent today. `option()` does not find it, falls back to the default, and the
unit runs on a value the owner did not write. The failure mode is the bad one: it does not refuse
to start and it does not bind nothing, it **half works**. A unit configured with `bind_address`,
singular, came up healthy on the two shipped defaults while a third address, added deliberately,
was silently absent. Debugging that from the symptom means suspecting the network,
the certificate, the firewall and the browser before suspecting a letter.

`bind_addresses` is why this is worth a rule rather than a nicety. §2.3 makes it the whole
security model, so a typo there is a silently different security posture in **both** directions:
an address added does nothing, and an address removed from a list that then falls back to the
defaults can bind a network the owner had just decided to stop binding.

**The rule.** At `on_loaded`, every key present under `[main.plugins.companion]` that the plugin
does not accept is logged **once**, at WARNING, naming the key. The plugin then starts normally on
its defaults. It never refuses: a pwnagotchi that will not boot because of a misspelling is a
worse outcome than the misspelling, and this plugin degrades rather than taking the unit down.

**The scan runs before the plugin decides whether it can start at all**, and in particular before
the TLS material is read (§2.6, §2.15). A unit that refuses to start still names the key. The
ordering is the rule, not an accident of where the code sits: the misspelling is often the reason
for the refusal - `tls_cert` written wrong is a certificate that cannot be opened - and reporting
only the refusal hides the one line that explains it. The two messages together say what happened
and why; the refusal alone sends the reader to look at a certificate that is fine.

**The accepted set is the `DEFAULTS` table above, plus `enabled`** (F32). `interfaces` is excluded
from this warning because it already has a more specific one naming its replacement (§2.3.1);
saying it twice, once by name and once as "unknown", would read as two problems.

**And it is spelled once (issue #173).** Two sites decide independently that `interfaces` is the
withdrawn key: this scan subtracts it from the accepted set, and the transport emits §2.3.1's
warning when it appears. The comment above the first says the two "must never be expressed
separately, or they can drift apart" -- and then expressed them separately, in two string
literals in one file. Rename the key in one and the other silently changes meaning: the scan
would start calling it unknown while the warning still fired, which is the two-problems reading
this rule exists to prevent, arrived at from the opposite direction. One name, referenced twice.

**No near-match is suggested.** `bind_address` to `bind_addresses` is the obvious case and it is
tempting to print it, but a guess is friendly exactly when it is right and misleading when it is
wrong, and the wrong guess sends the reader to correct a key that was never the problem. The
warning names the unknown key and the accepted set, and lets the reader do the matching, which
they can do correctly and the plugin cannot.

**The way this rule could do harm does not exist, and that had to be checked rather than assumed.**
A key pwnagotchi injected that this list did not know about would be warned on falsely, on every
unit, at every boot, and a warning nobody can act on is how a log teaches its reader to skip
warnings. F32 pins that `plugin.options` **is** the `config.toml` table, assigned wholesale, so
every key a plugin sees is a key somebody typed - except `enabled`, which `toggle_plugin` writes
and persists, and which is accepted for exactly that reason. An earlier draft of this section
claimed that as evidence the repository did not have; the fact is pinned now because it was
challenged, which is the only reason to trust it.

The rule therefore rests on F32 rather than on a hope. If upstream ever composes that dict instead
of assigning it, the machine-checked entry fails and this section is what has to change.

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

A `rebind_interval` timer (default 30 s, clamped to 5-300 s by §2.4) re-enumerates the host's
addresses and reconciles. **The first pass after load reconciles unconditionally**, whatever the
interval; the timer spaces the passes after it. That is what binds the tether a unit already had
when the plugin loaded, rather than making the first listener wait out an interval, and two tests
rest on it, so it is stated here and not left to the initialiser that happens to implement it:

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

**A pass and a teardown cannot interleave, and a teardown always wins.** `reconcile()` runs on the
background thread on a timer; `stop()` runs on whatever thread pwnagotchi calls `on_unload` on.
Nothing else serialises the two, so without a rule here a pass could be mid-bind while `stop()`
iterates the same state, or `stop()` could finish and hand back a torn-down unit only for a pass
already under way to reopen what it just closed. Both are ruled out by a lock and a flag together,
not by either alone:

- a lock makes one pass, and one teardown, atomic with respect to each other, so neither observes
  the state mid-mutation;
- a flag, set by `stop()` before it closes anything, is what stops a pass that was *waiting on the
  lock* from binding once `stop()` releases it: the lock alone only orders the two, it does not
  say that the later one should do nothing. Without the flag, a pass queued behind `stop()` would
  still run to completion and rebind everything a moment after teardown, which is the leaked
  listener this rule exists to prevent.

A pass that arrives after a teardown has begun does nothing and reports the state as it found it.
`stop()` stays idempotent: a second call closes an already-empty set. The lock is held for the
whole pass, so `stop()` can wait for one already under way, bounded address by address for every
wait the plugin controls: up to ten seconds per address for the WSS bind, a few seconds more to
close one, and one `poll_interval` (0.5s) for a closing HTTPS server's `serve_forever` loop to
notice `shutdown()` - real on a pass touching several addresses at once but not indefinite. That
last figure is a real bound and not a best case: `Server.get_request` in `_serve_http` TLS-wraps
each accepted connection, not the listening socket, and does so without
`do_handshake_on_connect`, so `accept()` on that socket is a plain TCP accept and the
`serve_forever` loop is never parked inside a handshake waiting for a peer that will never send
anything (issue #100, fixed). The HTTPS bind itself does not add to the bound either:
`Server.server_bind` in `_serve_http` skips the reverse-DNS
lookup `http.server.HTTPServer` normally makes after binding, which over BT PAN resolves against
the iPhone and, with nothing reachable at it, would otherwise cost seconds per address on top of
the above.

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
see the clamp below) from a ticker of its own. The session-refresh loop wakes on
`session_poll_interval` alone and broadcasts nothing.

**A tick with no client connected is skipped whole**, payload and frames alike. Building `stats`
scans the handshake directory, reads a sysfs node and may reach the PiSugar over I2C, which is
not worth paying every 20 s on a Pi Zero with nobody listening. No client can observe the
skip: one admitted between two ticks already has its state from `initial_burst()`, and the
ticker re-arms whether or not it sent, so the next broadcast lands when it would have anyway.

The broadcast used to ride that loop, and moving it off was the point of #65. Sharing the
loop meant sharing its spacing, so a 5 s poll turned a nominal 20 s cadence into 20-25 s and
a client whose timeout was set to the nominal value reconnected for no reason. On a ticker
the spacing is `keepalive_interval` and nothing else, which is what lets §4.3.2 state one.

It exists so an idle client can tell a quiet plugin from a dead link: over BT PAN a dropped
tether does not close the socket, it simply stops delivering, and without a periodic frame the
app would sit on a dead connection showing stale data. The frontend does not reconnect on a missed frame, which would be a guess: it reconnects when
its own `ping` goes unanswered, and reads staleness from the data rather than from the
spacing of frames (§4.3.1 for both).

**`stats` rides the ticker with it** (§4.3.7, issue #65). A periodic frame proves the link is
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

A value that is not a usable number at all - a string, `nan`, either infinity - takes the
default and is reported at **WARNING**, one level above the clamp itself, because a number
outside the range is a preference the plugin overrode while `"20s"` is a mistake the owner
wants to hear about. `nan` is not hypothetical: TOML has a bare float literal for it, and
before the check it reached `Event.wait`, which returns immediately, and spun the ticker at
full CPU rebuilding a payload it then broadcast as fast as the sockets accepted.

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

`rebind_interval` is clamped to **5-300 s** (issue #105). Until that ticket it was the one
interval that went through no clamp at all: `_background` read it with a bare `float()` at
thread entry, outside the loop's own `try`, so a `rebind_interval = "30s"` raised `ValueError`
before the first pass and killed the thread that does the session refresh, the gpsd poll and
the reconcile alike. The plugin then held its sockets open and served stale data for the life
of the process, and a tether that moved was never followed. That is why the clamp is not a
tidiness: this one key could stop the other two loops.

The **floor** is derived only as far as *above 1*, and 5 is then chosen rather than derived.
The reconcile only runs when the background loop wakes, and that loop wakes on
`session_poll_interval`, itself clamped to 1-5, so any value at or below 1 makes the pass run
on every wake. A pass enumerates the host's addresses through `netifaces` when it is importable
and by forking `ip -4 addr show` when it is not, and §2.3 forbids making `netifaces` a hard
dependency, so the fork is the case that must be assumed rather than the unlucky one. Once a
second on a Pi Zero 2 W that is a real cost, and the event it is watching for, a tether changing
address, does not happen at that rate. None of that distinguishes 5 from 2 or 3. **5 is chosen
for consistency with the `keepalive_interval` floor**, and that analogy is one of size and not
of kind: that floor is about payload cost over BT PAN, this one about fork and CPU cost on the
unit. The first draft of this paragraph presented 5 as derived and review caught it, which is
the same failure §2.4 already records twice: a number that sounds argued is worse than one
admitted to be a choice, because nobody rechecks it.

The **ceiling of 300** exists so that no configured value can quietly turn the rebind off.
While the plugin is bound to an address that no longer exists there is no listener to reach:
the app is not slow, it is absent. The client's backoff saturates at a **cap** of 30 s and is full
jitter (§4.3.2), so each delay is drawn uniformly from `[0, 30]` and averages about 15: at the
ceiling roughly **twenty** reconnection attempts fail before the plugin looks at the address
list again, which is five minutes of an app reporting offline over a tether that is up. The
count is approximate because the draw is random. The five minutes is not, and it is the half
that decides the number. Past that the timer stops
being a repair the user waits for and becomes one they beat by restarting the app, and a
`rebind_interval = 86400` would disable the reconcile while reading like a preference.

The ceiling bounds the other direction too, and that is the half a reader looks for. An address
removed from the host right after a pass keeps its listening socket until the next one, so the
socket outlives the address by four terms, and **no single total is correct**, because the last
of them scales with how many addresses are being torn down:

| term | worst case at both ceilings | why |
|---|---|---|
| waiting for `next_rebind` | 300 s | the `rebind_interval` ceiling |
| the loop wake after it | 5 s | the `session_poll_interval` ceiling |
| the pass before the clock is sampled | 30 s + about 4 s | the clock is read after both slow calls: the session refresh carries bettercap's 30 s timeout (F7), and the gpsd read passes 2.0 s to both the connect and the socket timeout and checks its deadline before each `recv`, so a `recv` entered just under it can return about 2 s late, twice 2 |
| the reconcile after the sample, which is where the socket actually closes | 5 s enumeration, then per address 5 s for the WSS shutdown and 0.5 s for the HTTP poll interval | the `ip -4 addr show` fallback the floor paragraph above insists must be assumed, and a teardown that is per address rather than shared |

Two things the table assumes, said here rather than left for the next reader to discover. It
bounds the working path: an enumeration that fails returns early and leaves the bound set alone
on purpose, per the last row of the §2.3.1 table, so each consecutive failure defers the close by
another whole cycle. And the gpsd figure assumes `gpsd_host` is an address literal, which the
default is: `socket.create_connection`'s timeout does not cover `getaddrinfo`, so a hostname
there is not covered by the 2.0 s bound at all, and costs whatever the resolver's own retry
budget costs, the same hazard §2.3.1 already records for the reverse lookup in `server_bind`.
That one is a defect rather than a caveat, and is issue #163.

For one stale address that is about **350 s**. Three drafts of this paragraph gave a single
smaller figure, 305, then 335, then 340, each by naming fewer terms than the loop has. The table
is here instead of a number because the fourth term has no ceiling in the number of addresses,
and because a total invites the next reader to trust it rather than recount. It is not an
exposure at any of those figures. While the address is absent the kernel delivers nothing to
that socket, so the plugin is holding a dead socket rather than serving on an address it should
have dropped, and if the address returns it returns through the same `bind_addresses` selectors
that admitted it (§2.3.1), which a reconcile would have re-opened anyway. There is no
configuration in which the stale socket serves something the selectors would have refused.
Stated here because the number is small and checkable, and a reader who cannot find it assumes
it is large.

A configured `0` therefore takes the **floor**, not the default. Unlike `keepalive_interval = 0`
it never meant *disable*: it meant *reconcile on every pass*, and 5 s is the nearest the plugin
is willing to honour that. A value that is not a usable number, `nan` and both infinities take
the **default 30** and are reported at WARNING, exactly as the other two keys are, and the
thread starts and keeps running whichever of those it was given.

**The clamp costs the tests their extremes, and that is the intended trade.** A test that set
`rebind_interval = 0` to force a reconcile on every pass, or `3600` to suppress it for the
duration, gets neither now: both land inside the range. The reconcile is driven by
`self.deps.now()`, so a test that needs a pass at a chosen moment advances that clock instead of
asking for an interval the plugin would refuse from a `config.toml`.

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

**The plugin runs without an agent, and says so rather than guessing.** In manual mode
pwnagotchi never calls `on_ready` **on the boot path**, so a plugin loaded at boot never gets an
agent there. Not an omission upstream could fix by remembering: the manual path does not run the
code that fires the event (F31). The plugin must therefore work with no agent for the whole life
of the process, and must also cope with one arriving late, since F31's second route hands an
agent to a plugin enabled from the web UI in any mode. Two consequences, and both were defects
(issue #140).

**The threads start in `on_loaded`, not in `on_ready`.** Only one of the three things `on_ready`
used to do needs an agent. The background pass also polls gpsd and reconciles the listeners, and
that reconcile is what binds a tether when it comes up, so a plugin that starts no threads in
manual mode never notices one. `on_ready` now captures the agent and nothing else; every loop
tolerates the agent being absent, which they already had to do for the window before `on_ready`
fires in auto mode.

**`mode` is null when there is no agent**, rather than `AUTO`. The first implementation defaulted
to `AUTO`, and on a unit whose display read MANU the app confidently read AUTO: not missing, but
wrong, which is the worse of the two. It is the one field where the plugin asserted something it
had no basis for, on a screen whose whole presentation rule (§4.5.1.1) is that an unknown value
renders as a dash. Nothing else in `stats` needed changing, because everything else agent-derived
was already reporting a real zero or a real null.

That makes `Mode` nullable at both its use sites, `Stats` and `FaceStatus`, which is a re-shaped
message and therefore a MINOR bump under §12: **0.1.0**.

**The handshake counts were the same defect, and both are now nullable, issue #146.** The
capture directory was read from `agent._config['bettercap']['handshakes']` (F14) and nowhere
else, which with no agent raises and leaves the directory empty, so `handshakes` and
`handshakesTotal` reported **zero** on a unit that may have hundreds of captures on disk. A
confident zero of exactly the kind this section is about, in a field nobody had looked at.

Two changes together, because either alone leaves the unit worse off than the operator would
accept. Both counts become **nullable**, so the wire can say "not known" where it used to say
zero; and the plugin gains its own `handshake_dir` config key (§2.2), empty by default, so a unit
with no agent can be *told* where its captures are instead of only being able to say it does not
know. The first is the honest shape, the second is what makes the screen useful on the unit that
needed the honesty.

**Where the directory comes from, in order.** `handshake_dir`, when it is set to a non-empty
string; otherwise `agent._config['bettercap']['handshakes']` (F14), when **that** yields a
non-empty string; otherwise it is unknown. Neither source may contribute an empty path: an empty
path is nowhere to look, and a store over one scans nothing and reports zero, which is this
ticket's own defect written a second time. The
config key wins deliberately: it is the one an operator can set on a unit whose agent never
arrives, and a setting that only applies when it is not needed is not a setting. The two can
disagree, and that is the cost of the key. It is paid knowingly: the operator who sets it has
said which directory they mean.

**Unknown is not the same as empty, and only unknown is null.** When the directory is unknown
both counts are `null`. When it is known, the counts are what a scan of it finds, and a directory
that is missing or unreadable counts **zero** - the plugin knew where to look and found nothing
there, which is a real answer, not an absence. The two counts are null together or neither: they
come from one scan of one directory, and a client may rely on that.

`lastHandshake` was already `null` in this state, from the same source, which is the shape this
change gives the counts.

Widening these two fields is a re-shaped message and therefore a MINOR bump under §12: **0.2.0**,
the project's second, following #140 one ticket earlier. The alternative considered and rejected was pinning pwnagotchi's default
capture path as a fact in §11 and falling back to it: cheapest at the call site and the most
fragile, since a path that moved upstream would silently resume reporting a zero, and this
repository does not rely on a pwnagotchi behaviour it has no evidence for.

**`get_handshakes` still answers an unknown directory as an empty listing**, issue #153. That is
the same confident zero at a different field, left standing because `handshakes_list` types
`total` as a non-null integer and setting `handshake_dir` removes the case entirely. Named here
rather than left for the next reader to rediscover.

```
uptime          = pwnagotchi.uptime()               # int seconds        (F1)
mode            = None if no agent yet, else                             (F11)
                  "MANUAL" if agent.mode == "manual" else pasv_or_auto()
channel         = agent._current_channel            # attribute, NOT session()  (F6)
battery         = battery_info()                    # §2.11
temperature     = pwnagotchi.temperature()          # int °C             (F2)
handshakes      = count of *.pcapng in the handshakes dir  # matches the device display (F15)
                  or None when the directory is unknown
handshakesTotal = count of *.pcapng plus *.pcap            # always >= handshakes
                  or None, together with handshakes, never alone
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

#### 2.6.0 What each control does with no agent (issue #147)

A unit in manual mode never hands the plugin an agent (F31, §2.5). The first implementation
guarded all three of these with the same check and answered `internal_error`, "agent is not ready
yet", for the life of the process. So the controls were unavailable exactly on the units that
needed them, and `set_mode` is the one that would have taken such a unit back to auto: the app
offered a button that could not work, on the one screen from which the unit could have been
rescued.

The message was wrong as well as the behaviour. "not ready yet" describes a wait. Nothing is
being waited for: the event never fires on that path, so a client retrying is retrying something
that cannot change without a restart.

**None of the three needs an agent as much as it looked.**

| control | what it used the agent for | with no agent |
|---|---|---|
| `set_mode` | reading the current mode, to refuse a restart into the mode already in effect | the current mode **is** manual (F31). A request for `manual` is the no-op it always was; a request for `auto` restarts, which is the whole point |
| `set_pasv` | checking that the mode is auto | it is not auto, so the answer is the one the contract already has: `pasv_requires_auto` |
| `log_lines` | the log path, which comes from `agent._config` (F17) | readable when `log_path` is set, and `log_unavailable` when it is not (§2.6.0.1). The same shape as the handshake counts (§2.5): a key the operator can set on a unit whose agent never arrives, and an honest refusal when neither source yields a path |

#### 2.6.0.1 The log has a configured path too, for the same reason the captures do (issue #154)

§2.5 gave the handshake directory a `handshake_dir` key because a unit with no agent could not be
told where its captures were. The log was left with the same problem and no key, and the
asymmetry is hard to defend: a unit in manual mode never gets an agent (F31), so the log is
unavailable for the life of the process on exactly the units somebody is most likely to be
debugging. §2.6.0 already says the boot-window case "is the one that stings, because a stuck boot
is exactly when somebody wants the log".

**`log_path`, empty by default, resolved before the agent**, exactly as `handshake_dir` is. When
it is a non-empty string the plugin reads that file; otherwise the path comes from
`agent._config` (F17); otherwise the answer is `log_unavailable`, which stays the honest reply
rather than becoming an internal error. Neither source may contribute an empty path, for §2.5's
reason: an empty path is nowhere to read, and a reader over one returns nothing and calls it a
log.

**The path is never influenced by a client.** `get_log` carries a line count and nothing else
(§2.9), and it must stay that way. A message that could name a file would make this plugin an
arbitrary-file reader on the owner's unit, reachable over a socket, which is a different program
from the one this document specifies. The only two sources are the config the owner wrote and the
agent's own configuration.

**The inference "no agent means manual" has a window, and it is named rather than hidden.** In
auto mode the agent arrives when `agent.start()` fires `ready`, and that is the **last** statement
of `start()`, after it has waited for bettercap, brought up monitor mode and started its pollers.
So the window is not a fixed few seconds: it lasts until bettercap is answering and monitor mode
has come up, and on a unit where either is struggling it lasts as long as that does. An earlier
draft of this paragraph said seven seconds, measured between the plugin loading and `cli.py`
logging `entering auto mode ...`, a line printed **before** `start()` is called. It measured the
wrong endpoint and stopped where the window begins.

Inside that window the plugin has no agent and the unit is not manual, and all three controls
behave as though it were: `set_mode` to `auto` restarts a unit that was already going to be auto,
`set_pasv` answers `pasv_requires_auto` on a unit that will be auto shortly, and `log_lines`
answers `log_unavailable` on a unit whose log is perfectly readable. The last of those is the one
that stings, because a stuck boot is exactly when somebody wants the log.

**It is accepted anyway, and the comparison is what settles it.** Two of the three are benign and
retriable: nothing is lost by asking again once the unit is up. The one with a cost, `set_mode`,
trades an unnecessary restart of a unit that is already failing to finish its own startup against
a control that otherwise **never** works on a manual unit. Somebody looking at a unit wedged
waiting for bettercap is somebody for whom a restart is a reasonable next move rather than a
punishment.

The alternative is a timer that waits to see whether an agent turns up, which is the timed
fallback another plugin resorts to for the same reason. It would make every mode switch wait for
a deadline chosen to second-guess a fact §11 pins, and it would still be wrong on the unit where
bettercap takes longer than the deadline.

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
broadcast, which the ticker of §4.3.7 sends within `keepalive_interval`. The PWA shows the toggle as pending until `stats.mode` confirms it.

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

Enumerate the directory §2.5 resolves: `handshake_dir` when it is a non-empty string, otherwise
`agent._config['bettercap']['handshakes']` (F14; default `/etc/pwnagotchi/handshakes`).

**When neither source yields a directory, `total` is null and the list is empty (issue #153).**
§2.5 made the counts nullable so the wire could say "not known" where it used to say zero, and
this reply had the same confident zero at a different field: `entries: []` with `total: 0` reads
as a unit with no captures, which is a claim, and the plugin has not looked anywhere to support
it. A client cannot tell that from a unit whose directory the plugin was never told.

So `total` is nullable here for the reason it is nullable there, and `entries` stays an array
because an empty list is what the plugin actually has to offer. **`total: null` with an empty
`entries` means the directory is unknown; `total: 0` with an empty `entries` means it was found
and holds nothing.** No new error code is needed: this is a fact about a
field, not a failure of the request, and `get_handshakes` succeeded at everything it was able to
attempt. **What the client renders for a null total is §4.5.2.3's and §4.5.2.7's business, and
both say so there.** An earlier draft of this paragraph pointed at §4.5.1.1 instead, which is the
Dashboard's `stats` rows and has nothing to say about this field; the citation was wrong and it
is what let the client half of this change go unwritten, because a rule that is already covered
somewhere else needs nobody to write it.

A new error code was the alternative and was rejected. `log_unavailable` exists because the log
is one thing that is either readable or not; a handshake listing is a count and a set of entries,
and half of it is still true when the directory is unknown -- there are no entries, and that is
not a guess.

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

`tail_lines` propagates `OSError` for a missing or unreadable file; the Router turns that, a
missing log path in the configuration, and having no agent to read that configuration from
(§2.6.0), into `error` code `log_unavailable`.

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
   thread as the session cache, never from the request path. **`gpsd_host` must be an IPv4 or
   IPv6 address literal, and a hostname is refused when the config is read** (issue #163). A
   socket timeout applies to the connect, not to the name resolution that precedes it:
   `getaddrinfo` runs first and honours no timeout the caller can set, so a hostname behind a
   DNS server that does not answer blocks the poll for as long as the resolver takes, in the
   thread that also refreshes the session cache. Refusing at load rather than resolving at poll
   is the bounded answer and costs nothing real: gpsd on a pwnagotchi runs on the unit itself,
   the default is `127.0.0.1`, and a companion talking to somebody else's gpsd over a name is
   not a configuration this project supports.

   **What the refusal does**, because "refused" on its own leaves the important half unsaid: it
   disables the gpsd source and nothing else. The plugin starts, `gps_source = "auto"` falls
   through to bettercap and to the browser as it always did, and a forced `gps_source = "gpsd"`
   over a refused host reports no fix rather than raising. §2.2.1's rule governs this as it
   governs a bad `bind_addresses` entry: a pwnagotchi that will not boot because of a
   misconfiguration is a worse outcome than the misconfiguration.

   **The check happens at the poll, on the value the poll is about to use.** An earlier reading
   of this rule validated once when the plugin loaded and cached the answer, and the value
   handed to the socket was read again later, live. Everything else in the resolver reads its
   options live per call, so the two could disagree -- and a poll that dispatches a host the
   guard never saw is the unbounded `getaddrinfo` this rule exists to prevent, arrived at
   through the guard rather than around it. One read, validated and used.

   **The refusal is logged where it has an effect, and not before.** It fires when the gpsd
   source would actually have been polled -- `gps_source` of `auto` or `gpsd` -- and stays quiet
   on a unit configured for `bettercap` or `none`, where a stale hostname in `gpsd_host` changes
   nothing. An error about a setting that has no effect is how a log teaches its reader to skim,
   and this project has made that argument once already for the unknown-key scan. The cost is
   named rather than hidden: an owner who later switches `gps_source` back to `auto` gets the
   error then, at the moment it starts being true, rather than having seen it at a boot where it
   was not.

   **The message names the key and never the value.** This log is served to a client by
   `get_log` (§2.9), so a config value written into it is a config value on the wire. The
   unknown-key scan (§2.2.1) already holds to this, and the reader has their own configuration
   in front of them. Emit `source: "gpsd"`,
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

Consequences of that source, stated because each was inferred once and should not have to be
inferred again:

- **`status_change.status` is the same view status**, not a separate string. The mood hooks carry
  a mood and the text the unit is displaying at that moment, and the view is the only source
  §11 makes available for it.
- **A view that cannot be read yields empty strings, never a missing field or a null.** That
  covers `Agent.view()` raising and `View.get` raising as well as the absent key above. The
  schema requires three strings, so the alternative is a message that fails its own contract at
  the moment the unit is already misbehaving. This is deliberately not distinguishable from a
  genuinely empty face or status: the protocol carries display text, not display health, and it
  carries no signal that reports this class of failure at all. `degraded` (§4.3.1) does not
  answer this either, since it is defined purely on `stats.sessionAge` staleness, and a unit
  with a collapsed view stays `connected` with a perfectly fresh session age.
- **An unreadable `mode` degrades to `"AUTO"`, not to an empty string**, unlike `face` and
  `status`. `common.json` `$defs.Mode` types it as an enum (`AUTO`, `PASV`, `MANUAL`), and an
  empty string is not a shape the contract allows there, so the empty-string rule above cannot
  extend to this field. It costs precision: a client cannot tell an
  `"AUTO"` that was actually read from an `"AUTO"` that was guessed because the mode could not
  be determined. **This is now the only case that falls back**, and it is a different case from
  having no agent, which reports `null` (§2.5, issue #140). An earlier version of this paragraph
  justified the fallback by saying `"AUTO"` was already what an agent-less unit reported; that
  sentence stopped being true when the agent-less case was fixed, and it is removed rather than
  reworded, because the two cases now differ in exactly the way it claimed they did not: an agent
  that answers and lies about how, against no agent at all. That is accepted deliberately; the alternative, a fourth enum member such as
  `UNKNOWN`, changes the wire format for a path that is nearly unreachable (§11 pins `mode` as a
  plain attribute, not a property that is expected to raise), and it would leak into
  `stats.mode`, which shares the same `$ref`.
- **Both of those rules exist so that `face_status` is pushed, never suppressed.** A hook that
  cannot build any part of the payload must still build the rest of it, because silence on this
  channel is indistinguishable from a plugin that has stopped, and the client has no state that
  means "the hook is failing but the process is alive." A partial payload is worse information
  than a complete one, but it is strictly better than nothing, because it still tells the client
  the socket is alive.
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

A `ThreadingHTTPServer` subclass running in its own thread, with
`SimpleHTTPRequestHandler(directory=web_root)`. The listening socket itself is never TLS-wrapped;
each *accepted* connection is, in `get_request()`, with `ssl_ctx.wrap_socket(conn,
server_side=True, do_handshake_on_connect=False)`. That keeps `accept()` a plain TCP accept and
lets the handshake happen lazily on the connection's first read, on the handler thread
`ThreadingMixIn` spawns for it, not on the `serve_forever` loop (issue #100).

That fix has a residual, and it is not one thread total, it is one thread and one file
descriptor per silent peer. `daemon_threads = True` (`_serve_http`) is what stops
`server_close()` waiting on a handshake that will never finish, which is the entire point of the
fix above - but the same setting means that thread is never joined, by `stop()`, by a reload, or
by anything else. It outlives all three: a peer that connects and sends nothing keeps its thread
and its socket for the life of the process, and every further silent peer adds one more of each,
unbounded in aggregate. Tracked separately as issue #102.

Two requirements v1 omitted, both of which break the PWA silently:

1. **Explicit MIME map.** The system `mimetypes` database on a minimal Raspberry Pi OS image
   does not know `.webmanifest`; serving it as `application/octet-stream` makes iOS ignore the
   manifest and refuse installation. Set the handler's `extensions_map` explicitly for at least:

   ```
   .html .webmanifest .js .mjs .css .json .png .svg .ico .wasm .map .woff2
   ```

   `.js` and `.mjs` must be `text/javascript` — a service worker served with the wrong type is
   rejected by the browser. `.webmanifest` is `application/manifest+json`, `.ico` is
   `image/vnd.microsoft.icon`, `.map` is `application/json`. **`.html` must be
   `text/html; charset=utf-8`**, charset included: the transport layer outranks the document's
   own `<meta charset>`, so this is what actually decides how the page is decoded, and §4.2 says
   what a wrong decode costs. For the rest what matters is only that nothing the app needs
   arrives as `application/octet-stream`.

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
- **An exception raised while handling a request is logged as the exception itself, at debug,
  never as its frames.** `exc_info=True` (or an equivalent that lets the logger format the
  traceback) is forbidden here for the same reason `end_headers` was fixed to stop doing it
  (issue #93, #67): this server sits behind no check of its own, unlike the token on the WSS
  side, so whoever can reach the port decides how often this path runs, and moving the TLS
  handshake onto the handler thread (this section, issue #100) is what put a caller-triggered
  exception in this handler in the first place - a broken or absent handshake now raises where a
  request is handled, not at `accept()`. The two belong to one decision: a handler thread an
  unauthenticated caller can enter at will must never let that caller choose what a traceback of
  it writes into a log the caller does not own. The exception is logged by its `repr`, not
  its `str`: `str()` of an `OSError` carries the path it failed on and `repr()` does not,
  so the choice is part of the rule rather than a formatting preference.

#### 2.15.1 Content-Security-Policy and the headers beside it (issue #67)

The token lives in `localStorage` (§4.3.6), so anything that runs script on this origin reads it,
and every SSID, peer name and status line the app renders was chosen by somebody the owner has
never met. Escaping (issue #32) is one layer; this is the second, and the two fail independently.
An escape missed on one screen does not execute if inline script cannot run.

**Emitted as a response header on every response the static server produces, never as a `<meta>`
tag.** A meta-delivered policy silently drops `frame-ancestors`, `sandbox`
and reporting, which is three of the directives below. Every response includes the ones the server generates on its own: the 400 for a path
containing `..`, the 404, and the errors raised before the request line is even parsed. That
last case needs care in the handler, because the request path does not exist yet at that
point, and reading it there means the response never leaves at all.

The exception is a request that never establishes an HTTP/1.x version: an HTTP/0.9 line, an
unsupported version, a line with no version at all. The header machinery is inert until a
version is set, so the server answers with a bare body or nothing. No browser produces any of
them. Named by mechanism rather than by example, so the claim above can stay absolute for
everything a client can actually send.

```
default-src 'self';
script-src 'self';
style-src 'self';
img-src 'self' data: https://*.tile.openstreetmap.org;
connect-src 'self' <one wss:// origin per bound address, on the WSS port>;
font-src 'self';
object-src 'none';
base-uri 'none';
form-action 'none';
frame-ancestors 'none'
```

No `'unsafe-inline'` and no `'unsafe-eval'`, in either `script-src` or `style-src`. Both are the
difference between a policy and a decoration.

**The tile origin is a real weakening and is written here rather than glossed.** D2 locks the map
to Leaflet with OpenStreetMap tiles over the phone's link, so the app is not self-contained. An
external origin in `img-src` is a channel an injected script can encode data into, one request
at a time. It buys the map, which is most of the product, and it is the only external origin in
the policy.

**`connect-src` is built per request from the addresses the plugin is bound to** (§2.3.1), each
as `wss://<address>:<ws_port>`. `'self'` does not cover the socket: CSP matches the port, and the
page is served on 8443 while the socket listens on 8082. Two consequences, both deliberate:

- A unit that rebinds emits a different policy afterwards, which is correct rather than
  surprising: the policy names what exists now.
- A client served by unit A and pointed at unit B by the Settings host list (§4.5.2) will be
  blocked by A's policy. That is the price of the directive meaning anything; relaxing it to a
  bare `wss:` would delete it as a control. The Settings list is for a client that will be served
  by the unit it talks to.

**The service worker must not be able to serve a page without this header.** After registration
it answers navigations from the precache, and a `Response` it synthesises rather than replays
carries no headers at all: that page would run unconstrained on the origin holding the token. The
navigation fallback must be a precached network response, and a test must assert the header is
present on a document served by the worker, not only on one served by the plugin. **That test
does not exist yet** and cannot be written until there is a service worker to register: issue
#91 carries it.

**`script-src 'self'` holds against today's build output, which is a property of the
configuration and not of the tools.** `vite-plugin-pwa` with `injectRegister: 'auto'` emits an
external `registerSW.js` today and could emit an inline one tomorrow. The setting is pinned, and
the build asserts that no HTML file it emits carries an inline `<script>`, because the alternative
is a policy that breaks the first time somebody changes a build option.

**The assertion now exists** (issue #141), and what it would have cost to keep deferring it is
worth recording, because the answer is not "an outage". Two CSP violations in a browser console
on a working unit were read as this regression having happened, and produced a high-priority
ticket with a wrong mechanism and a wrong consequence before anybody looked at the built file,
which was one grep away and said the opposite. A gate that cannot fail is also a gate that cannot
tell you it did not fail. The check runs in CI after the build, so the answer to "did an inline
script get in" is a job result rather than an argument.

It fails on any `<script>` element with no `src`, which is the whole rule: an external script is
covered by `'self'`, an inline one is not, and nothing in this app has a reason to inline one.
**An empty or whitespace-only `src` counts as no `src`.** It names no file, so it is a mistake
in every case it could arise from, and refusing it costs nothing because nothing legitimate emits
one. The check is about every script on the page being a real file the policy can cover, not
about the presence of an attribute.

**A `src` that points at another origin fails as well**, and for the same reason rather than for
a new one. `script-src 'self'` covers a file this unit serves; it does not cover
`https://cdn.example.com/index.js`, which the browser refuses exactly as it refuses an inline
block. A check that accepted it would be reading the wrong property of the attribute: not "is
there a value" but "is this a file the policy can cover". A `base` pointed elsewhere, or a plugin
emitting an absolute URL, produces one, and it fails on the unit rather than in CI, which is the
shape of failure this whole gate exists to move earlier. So the value must be relative or
root-relative: no scheme, and no leading `//`.

**A page with no `<script>` at all fails too**, and that is the rule that keeps this check from
being decoration. Today's single page carries two, so a file with none is a wrong file, an empty
file, or a build that did not run, and every one of those is a reason to stop rather than a clean
result. It is the same rule §13 states for a scan that could not run (issues #112, #126): a gate
that reports success when it had nothing to look at is worse than no gate, because it also
reports success on the day something is wrong.

**The check reads every HTML file under `dist/`, found by search rather than by name** (issue
#144). It used to inspect `dist/index.html` alone, because that is the only page the build emits
today. The day a second entry point appeared it would have shipped unchecked while the gate
reported success, which is this check's own failure mode arrived at from the other side: not wrong
about the file it read, wrong about the question it was asked. The search is recursive, since an
entry point emitted into a subdirectory is exactly the case worth catching, and the files are
processed in sorted order so a failing build names the same file first every time. **The sorted
order is a real requirement and it is harder to test than it looks**: directory iteration over a
small directory usually comes back alphabetical by accident, so a check with the sort removed
passes any test that compares two files at the same depth. Pinning it takes a file that sorts
after another and is visited before it, which a top-level name against a nested one produces.

**Every failure says which one it is, because they want different next actions.** "No HTML file
anywhere" says the build did not run; "`index.html` is missing" says the entry point is gone;
"no scripts in this file" says this page is wrong; "could not read this file" says this page
could not be opened; and a subtree that could not be read and one that was not followed each say
the gate could not look there. They are not counted here: the count went stale twice while this
section was being written, and the list is the code's to grow. Every one of them **names the
inode the reader has to change**, by the rule stated below, and that is the half a test forgets
to pin: an assertion looking for `sub` in the output is satisfied by the word "subtree" around
it, and one looking for `linked` by the word "symlinked". Pin the path, not a fragment of the
prose.

**No two messages may be printed together when one contradicts the other.** This went wrong three
times in one ticket, each time one level further down: a message saying the gate could not see
into something must not be followed by one asserting no page exists, because the first says the
second is not known. When a failure is about a place the gate could not look, the "no HTML
anywhere" line is suppressed, whichever call discovered it and whether it was `dist/` itself or a
subtree.

The name must end in `.html`, matched case-sensitively, because that is what the build emits and a
gate should say which files it claims to cover rather than implying all of them. A page arriving
as `.htm` or `.HTML` would pass unexamined, which is a narrower version of the hole this ticket
closed; it is named here so the next person to add one knows to widen the pattern with it.

**`dist/index.html` must still exist by name.** Searching for every page removed the one thing the
old check got for free: it read `index.html` directly, so a build that did not produce it failed.
A search does not, and a `dist/` holding only some other page would have passed. That is the one
way widening this gate could have narrowed it, so the named file is asserted separately from the
search. The app has one entry point (§4.2 serves `index.html` as the SPA fallback); a build
without it is broken whatever else it emitted.

**A subtree the gate could not read is a failure, and so is one it declined to follow.** A
directory that cannot be opened is skipped silently by an ordinary recursive search, which is the
vacuous pass again wearing a permission error: the check would report success having examined
fewer pages than exist. It fails instead, naming the directory. A **symlinked** subdirectory fails
for the neighbouring reason: following one risks a loop that hangs the job, and not following it
means the gate cannot vouch for what is inside, so it says which of the two it is rather than
quietly choosing the second. The build emits neither today; both are failures because a gate is
worth having only where it fails on what it could not see.

**`dist/` being a symlink is followed, and that asymmetry is deliberate.** A walk follows its own
root, so every page under a symlinked `dist/` is examined and none goes unchecked - which is the
whole reason the subdirectory rule exists, and it does not apply here. Refusing a symlinked `dist/`
would fail a legitimate build layout to guard against nothing.

**"No HTML anywhere" always arrives with "`index.html` is missing", and they cannot be separated.**
If `index.html` existed the search would have found it, so there is no reachable state where the
search returns nothing and the named file is present. The two lines are printed together by
construction, not by coincidence of how a fixture was built, and a test trying to produce one
without the other is chasing a state the code cannot enter. Said here because it looks like a
confound worth removing and is not one.

**A failure that names `dist/` itself suppresses the "no HTML anywhere" line, and the test for it
is decided by mode.** The first version keyed the suppression on which *call* had raised, so a
`dist/` at mode `0111` - searchable but not readable - printed both the named failure and a
sentence saying the build had not run, which was false: `index.html` had been stat'd successfully
three lines earlier. The condition is what the error is **about**, not which call found it. Note
how this repeats: mode `0`, mode `0444` and mode `0111` each take a different path through the
same code, and a fixture at one mode passes while the others crash or lie.

**`dist/` itself being unreadable is reported, not raised.** Checking for `index.html` by name
touches the directory before the search does, and `Path.is_file()` propagates a permission error
rather than swallowing it, so a `dist/` with no read permission ended the gate in a traceback: no
annotation for the reader, and an absolute local path on stderr that every other message here
avoids. It fails closed either way, which is the property that matters, but a gate that crashes
while reporting what it could not see is the shape this whole section is against. It is caught and
named, and kept distinct from "no HTML file anywhere", because a directory that will not open
wants a different next action from one that is genuinely empty.

**Every stat on the way down is a place this can raise, not only the first.** The unreadable-`dist/`
rule above was fixed one level too high: a subdirectory that is readable but **not executable**
lists its own entries, so the walk populates the directory names and never reaches the error
callback, and the symlink test on each of those names raises the permission error instead. Same
crash, same absolute path on stderr, one level down, and the test written with mode `0` missed it
because mode `0` fails at the listing and takes the path that works. Any call that stats a child
during the walk is treated as the walk failing on that directory: caught, recorded, and reported
with the same named message.

**No message the gate prints carries an absolute path, with one named exception.** The rules
below hold for every one of the messages. They did not hold for a `UnicodeDecodeError`, which is a
`ValueError` rather than an `OSError`, escaped the handler, and ended the run in a traceback
carrying this script's absolute path and a fragment of the offending page. That was issue #159; it
is caught now, and the paragraph below says which case it is. The history is kept because the
guarantee is only worth as much as the exception that once escaped it.

The exception is the fallback that renders a path **outside** the repository root, which returns
the path as it stands because `relative_to` raises rather than approximating. No reachable route
to it is known: the root is resolved, `dist/` is built from it, and every path printed derives
lexically from `dist/`. It is named here rather than claimed closed, alongside the `.htm` and
`DT_UNKNOWN` gaps, because an absolute stated without its exception is the shape this section
keeps having to correct -- three times tonight, each time a rule that was true of most of the
messages written as though it were true of all of them.

**No message prints an operating-system error object whole.** `str(OSError)` embeds the absolute
filesystem path, and every message here is deliberately repo-relative, so the reason is printed
from `strerror` and the path from the value this gate chose. The fallback when `strerror` is
`None` prints the errno rather than the exception, because an `OSError` **can** carry a filename
with no `strerror` - `OSError(13, None, '/abs/path')` stringifies with the path in it - even
though CPython always sets `strerror` from errno on a real syscall failure, so no live path is
disclosed today. An earlier draft of this paragraph asserted the opposite as the reason the
fallback was safe. It was wrong, and the audit that caught it was reading the claim rather than
the code. It is a small thing that went
inconsistent twice in one file: one handler was written the careful way and its neighbour, added
the same day, was not.

**One narrow case stays open and is named rather than closed.** `os.walk` swallows an error raised
while classifying an entry as a file or a directory, and reclassifies it as a file: on a
filesystem that answers `DT_UNKNOWN`, or for an entry whose `stat` fails after its name was
listed, a subdirectory can go undescended without reaching the error callback. Closing it means
walking with `os.scandir` directly so the classification error is ours to record. It is named here
for the same reason as the `.htm` gap: a known limit invites less doubt about the ones that are
closed than a silent one does.


**One naming rule, and it covers every message this gate can print (issues #159, #160).** A
failure names **the inode the reader has to change**. Where that is unambiguous the message names
that one and nothing else, and where it is genuinely ambiguous the message names both candidates.
The rule is about the reader's next action rather than about which call happened to raise, and it
resolves the three cases this gate actually has:

- **Where the walk *raises*, that is the directory**, not the child that raised. A subdirectory
  with the wrong mode makes the stat on its children fail; the child is a bystander, the
  directory's mode is the thing to change, and naming children would print one line per entry for
  a single cause. No child is named individually for a stat failure, which is pinned by a test.
  The walk's **other** message is not this case and must not be read as an exception to it: a
  symlinked subdirectory names the entry itself, because there the entry *is* the inode the
  reader changes -- nothing about its parent is wrong, and the next action is about that link.
- **A read that fails on a file is the ambiguous case.** `open()` really was called on that file,
  so the file's own mode is a candidate; but so is its parent's, and the message cannot tell which
  without a second stat it has no reason to make. It names both. That was issue #160: it named the
  file alone, which is the victim whenever the cause is one level up. It names both **only where a
  parent's mode can actually have caused the failure**, which is `EACCES` and nothing else:
  path-resolution denial is `EACCES`, while `EPERM` from `open()` is always about the file itself,
  and appending the clause to `ENOENT` or `EPERM` points the reader at an inode that cannot be
  implicated -- issue #160 inverted, and a smaller version of the same defect. The `dist/`-by-name
  check has the same two-candidate shape and deliberately does not name both, because the second
  candidate is `frontend/` and there is no useful next action above it; that is a scoped exception
  and not a second reading of the rule.
- **A file that opened and did not decode is unambiguous, and the inode is the file.** No
  directory mode can cause a decode error, so naming a directory there would send the reader to an
  inode that is not implicated.
- **The `dist/` block emits two messages and they name different inodes**, which is the rule
  working rather than an inconsistency: a `dist/` that will not open names `dist/`, whose mode the
  reader changes, and a `dist/` with no `index.html` names `frontend/dist/index.html`, the file
  the reader has to produce.

An earlier version of this paragraph said a failure names the failing call's path **and** the
directory that could have caused it, always. That was wrong in both directions at once -- it
contradicted the walk's deliberate rule, which a test already pinned, and it contradicted the
decode message added in the same change. It is recorded rather than quietly replaced because the
absolute was easier to write than the true rule and read as more rigorous than it was.

**A file that opened and did not decode is its own case.** `read_text` raises `UnicodeDecodeError`
on a page that is not valid UTF-8, and that is a `ValueError`: it escapes an `except OSError` and
ends the run in a traceback. The gate does not falsely pass -- the exit status is still non-zero,
which is the property that matters most -- but a traceback carries no `::error file=` annotation,
so GitHub renders nothing against the file and the reader gets a Python stack instead of a
sentence naming the page. It gets its own sentence rather than being folded into "could not read",
because the two want different next actions: a file that would not open is a permission or a mode,
and a file that opened and did not decode is a wrong or corrupt build artifact, which is exactly
the class of thing this gate exists to catch early. The promise that a failure says which of its
cases it is has to hold for this one too.

**`style-src 'self'` also forbids inline `style="..."` attributes**, not only `<style>` blocks.
No component uses one today, and Leaflet mutates `element.style.*` through the CSSOM, which CSP
does not intercept. It is written here because an inline style is the obvious thing to reach for
when the Map and Mirror views are built, and it would fail only on the device.

**`blob:` is not in `img-src`.** The Mirror frame arrives as a base64 PNG, which needs `data:`
alone.

**No reporting, and the reason rather than the silence.** `report-to` needs a collector, and a
unit on a personal hotspot has nowhere to send reports that the owner could read; adding an
endpoint to the plugin would be new attack surface for a diagnostic. The most likely violation is
the build regressing to an inline script, and that is caught by the assertion above rather than
in production. Revisit if the app ever has a place to report to.

**Trusted Types are not required here.** `require-trusted-types-for 'script'` blocks the sink
where `script-src` blocks the payload, which is what issue #32 is about; it belongs to that
change, with the escaping, rather than to this one.

**Alongside it, on the same responses:**

- `X-Content-Type-Options: nosniff`. The explicit MIME map above decides the type; this stops the
  browser from deciding differently.
- `Referrer-Policy: no-referrer`. The tile requests are the only cross-origin traffic the app
  makes, and the path of a page on a private address is not something to hand to a tile server.

The residual risk is unchanged and stated in §9: both layers live inside the origin, so a
compromised build artifact reads the token with the policy fully satisfied.

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

- `index.html` head, with the charset declaration **first**:
  ```html
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <link rel="apple-touch-icon" href="/icons/apple-touch-icon-180.png">
  ```

  The charset is first because it is only honoured within the first 1024 bytes of the document.
  It is mandatory because a document that does not declare one is decoded with an
  implementation-defined fallback: served without the tag and without a charset in the
  `Content-Type`, both Chromium and WebKit decode as `windows-1252` and `(◕‿‿◕)` renders as
  `(â—•â€¿â€¿â—•)`. Measured in this repository's own Playwright harness, including with a body
  full of non-ASCII text to give an encoding detector something to work with.

  Issue #39 recorded this as WebKit failing while Chromium sniffed its way out. That is not what
  reproduces here, and the difference matters more than the fix does: an engine's encoding
  detector is conditional on locale and content and is not a guarantee any engine offers, so the
  rule cannot rest on one engine being forgiving. Do not restore that reasoning.

  This is not one mojibake in one string: the whole pwnagotchi face vocabulary is non-ASCII and
  it is the most recognisable thing in the interface, and the same failure hits every SSID
  carrying a non-ASCII character, which is the hostile-name case of §4.5.3.

  The declaration is not the only line of defence. The static server sends
  `text/html; charset=utf-8` (§2.15), and the transport layer is consulted before the document is
  prescanned, so the header wins over the tag and a document served by the plugin decodes
  correctly even if the tag is ever dropped from the build.

  Both exist because they fail in different places, and the two gaps are narrower than they look.
  The header does not reach a document opened from the filesystem. The tag does not reach HTML
  the build did not produce, such as the handler's own error pages, which take their type from
  the server alone. A document replayed by the service worker is covered by **both**: workbox
  precaches the response with the headers it was fetched with, so the plugin's charset survives
  into Cache Storage.
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
- Outbound queue with a per-message `message_id`, and **reads are re-sent on reconnect while
  state commands are not** (§4.3.3). **A read resolves on its typed reply, a command on its
  `acknowledgment`** (§4.3.8).
- Heartbeat `ping` every N seconds; force a reconnect if no `pong` arrives within the timeout.
- If a token is set, send `{type:'auth',token}` as the first frame.
- Typed `send()` plus an event dispatch that updates the stores. Types come from
  `lib/protocol.ts`, which is **generated** from `docs/schemas` by
  `tools/gen-protocol-types.mjs` — never hand-edited.
- Connection state: `connecting | connected | degraded | offline | unauthorized | restarting`.
  Because the socket only works while tethered, `offline` is the normal disconnected state and
  is shown calmly, not as an error. `restarting` is entered on a `restarting` message (§2.6.1)
  and suppresses the error banner while showing the reason.

> **Decided; the plugin side has shipped, the client side has not.** The behaviour below is
> settled, and #65 built the half of it that lives in the plugin.
>
> The timing rules key a client staleness threshold to the refresh episode, and the periodic
> broadcast to how late the client hears of one. That broadcast has moved off the refresh
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
> They shared a loop until #65, and that is not what made the state impossible.
> `refresh()` swallows its own exception, so a dead bettercap leaves the broadcasts flowing and only `sessionAge` growing.
>
> The ticker shipped with #65, so a client written against this section measures a staleness
> the plugin holds to.

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
| `connecting` | third consecutive close **of a socket that opened**, before any `stats`, no token stored | `unauthorized` (*required*), by inference — see below |
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
*required* anyway.**

**The counter counts closes, and a socket that never opened is not one.** This is the boundary
the first implementation missed, and it cost the app everything: it counted a failure to connect
alongside a close, so three refused connections were read as three lost 1008s. A unit restarting
produces far more than three of those in the ten to twenty seconds it is down, and `unauthorized`
is the one state that suspends reconnection - so any restart the app did not itself order left it
telling the owner to add a token to a unit that requires none, and never retrying (issue #139).
The argument above is entirely about a socket that **opened**, was refused, and was closed by a
frame that did not arrive. A connection refused never reached the plugin at all, and says only
that nothing is listening, which is `offline` and is retried for ever. The browser separates the
two plainly: an opened socket fires `onopen` first, and a transport failure closes with
1006.

**A refusal in between does not interrupt the run**, and "consecutive" is about the closes
rather than about wall-clock order. A socket that never opened neither increments the counter
nor resets it, so two opened closes, fifty refusals and a third opened close still infer
*required*. Only events that carry evidence count, and the event that genuinely says the unit
is answering is a `stats`, which resets the counter wherever it arrives.

Three because a slow tether plausibly loses one handshake and implausibly
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
succeeded, which is the case this state exists for. The plugin broadcasts `stats` on its
ticker rather than only answering `get_stats` (§4.3.7), which is what makes this state
measurable at all.

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
to 20 s. `auth_timeout` defaults to 10 s. The broadcast has a ticker of its own
and shares no wait with the refresh loop, so **the spacing is 20 s** and the numbers below
are derived from it.

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

The jitter is specified as a form and not as a mood because "with jitter" is not a claim anything
can check, and a rule nobody can check is a preference. Full jitter over `[0, cap]` can be: the delay is never
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
| `log_unavailable` | the log file, or the configuration naming it, could not be read -- including because there is no agent to read the configuration from (§2.6.0) | in the Log view, with its own sentence (§4.5.2.5). The no-agent case is a unit in manual mode, which is a unit somebody is probably trying to debug, so an unreadable log must not look like an empty one |
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

1. **Escaping of remote strings**, issue #32. **The rule exists** (§4.5.3) and CI enforces the
   part of it a grep can reach; the hostile-name fixtures wait on the views that would render
   them.
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

**Everything above depends on this, and it is why #65 was a bug rather than an enhancement.** Before it,
`stats` left the unit in exactly two situations: in `initial_burst()` on connection, and as the
reply to a `get_stats` request. The background loop broadcast `keepalive` and nothing else.

Written against that, §4.3.1 and §4.3.4 were not merely imperfect, they were unimplementable. A
client reads staleness from the `sessionAge` of the last `stats` it received, so with no
periodic `stats` it would read for ever the one number the initial burst gave it: a unit whose
bettercap died an hour ago still presenting as fresh, which is the failure this state exists to
prevent. And a mode change shown as pending "until `stats` confirms it" would stay pending for
ever, because nothing would ever arrive to confirm it.

**The plugin broadcasts `stats` every `keepalive_interval`, from a ticker of its own.** The
ticker reads the caches and sends, skipping the pass entirely when no client is connected
(§2.4); it shares no thread with `refresh()` and therefore no waiting with `agent.session()`,
which is what lets §4.3.2 state a spacing at all. `keepalive`
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

#### 4.3.8 What a request resolves with, and when its clock starts

**A read resolves on its typed reply. A command resolves on its `acknowledgment`.** Not
"whichever arrives first", which is the reading that looks equivalent and is not.

`Router.handle` appends the `acknowledgment` **before** the typed reply for every message that
carried a `message_id` (§10.7.2). So a promise that settles on the first correlated frame settles
on the acknowledgment every time, deterministically, and `get_handshakes` resolves with a receipt
rather than with handshakes. The caller is then left fishing its own answer out of the message
stream with no correlation, which is the thing the `message_id` exists to spare it.

The two cases differ because the reply differs. For `set_mode`, `set_pasv`, `reboot` and
`shutdown` the acknowledgment **is** the reply: there is no typed answer, and §4.3.4 is explicit
that the acknowledgment means the command was accepted rather than that its effect is visible.
For a read the acknowledgment is a transit receipt, and the answer is the `stats`,
`access_points`, `handshakes_list` or `log_lines` frame behind it. Those are the names in
`docs/schemas/outgoing/`, which is the only place they come from.

An `error` carrying the `message_id` settles either one, as a failure.

**The 15 s request timeout runs from the call, not from the send.** A read asked for while the
socket is down is queued (§4.3.3) and its timer starts anyway, because the caller is a view that
will otherwise wait for ever on a promise nobody will settle: the app is designed around a link
that drops, so "queued" is a normal condition and not a pause in the contract. A read that times
out while still queued leaves the queue with it; re-sending on reconnect applies to what is still
outstanding, not to what has already failed.

#### 4.3.9 Three smaller decisions the connection layer needs

**The token is re-read from storage on every connect attempt**, unless the caller supplied one
explicitly, which then wins for as long as that client lives. The explicit form is for a caller
that already holds the value; it is not how the app gets its token. The app stores the token and
lets the client read it, which is what makes changing it take effect on the next attempt. §4.3.1 has the row
`unauthorized` → the user changes the stored token → `connecting`, and a client that captured the
token once at construction cannot honour it: it re-sends the rejected value for ever, and
clearing the token to reach the tokenless path does nothing. **The client does not watch
storage**, so the transition belongs to whoever changed the token: the settings screen writes it
and then asks the client to connect. A token changed with nobody calling `connect()` changes
nothing, which is the right division and not an omission. The key is **`companion.token`**,
pinned here rather than left to each module, because it persists in every installed PWA and
renaming it later logs everyone out with no way to tell them why. Storing an empty string is
storing nothing: the read answers `null` for it rather than the empty string, so clearing and
setting to `""` are the same act, and a caller testing for `null` is not misled by a value that
is present and useless.

**A client with no token configured sends nothing until the first frame arrives from the unit.**
It may not flush its queue when the socket opens. §4.3.1 needs to tell *rejected* from *required*,
and a tokenless client talking to a unit that does require a token would draw
`error unauthorized` from its own first read and land in *rejected*, which is the wrong sentence
to show: the user has no token, they did not supply a wrong one. The unit answers this by itself.
With no token configured the burst goes out on accept (§2.4), so the arrival of any frame is the
unit saying it admitted the socket, and its absence is the unit saying it did not. The gate is per
socket rather than per client: every reconnection has to see its own first frame, because what
is being read is whether *this* connection was admitted, and a latch set once would carry an old
answer across a token that changed in between.

**Before `connect()` is called there is no connection, and `state()` reports `offline`.** That is
the honest value, but `offline` carries the calm copy about the Personal Hotspot (§4.3.1), and
showing it to someone who has not yet asked for anything would answer a question nobody put. A
view renders connection copy only after `connect()`; the state before that is for the code, not
for the screen.

#### 4.3.10 The last error and the round trip, and what each is allowed to claim (issue #176)

§4.5.2 asks the Settings screen for three diagnostics and §4.3.5's table sends every unhandled
error code to the same place. Neither could be built, because the client kept no last error and
measured no latency: it tracked `lastErrorWasUnauthorized` as a boolean used once to pick a close
reason, and it cleared a timer on `pong` without ever reading a clock. Both are surfaces on
`lib/ws.ts`, and both are the kind of thing a view will otherwise manufacture out of whatever it
can already reach.

**The client exposes a diagnostics pair, and the stores mirror it.**

```ts
export type LocalErrorCode = 'pong_timeout' | 'connect_timeout' | 'socket_failed'

export type LastError =
  | { source: 'frame'; code: string; message: string; at: number }
  | { source: 'close'; code: string; message: string; at: number }
  | { source: 'local'; code: LocalErrorCode; message: string; at: number }

export interface Diagnostics {
  lastError: LastError | null
  latencyMs: number | null
}
```

`WsClient` gains `diagnostics(): Diagnostics` and `onDiagnostics(handler): () => void`, and
`ConnectionView` (§4.4.1) gains the two fields. **A second callback rather than more work for
`onState`**: latency changes without the state changing, once per ping interval, and a client
that fired `onState` to announce a round trip would be telling every subscriber the state moved
when it did not.

**The clock is injected.** `WsDeps` gains `now(): number`, defaulting to `() => Date.now()`, for
the reason `random` is already there: a measurement asserted against the wall clock is asserted
against how long the test host took to run the test.

**The remote clock is never read.** `pong.json` carries a `timestamp` and this measurement
ignores it. A round trip computed across two clocks measures their disagreement as well as the
link, and a pwnagotchi that has been off for a week and has no RTC boots with a clock that is
wrong by days. Both stamps are taken locally, so the figure is a duration and not a subtraction
of two different opinions about what time it is.

**Only a pong that answers an outstanding ping updates the latency.** This is a second guard and
not a reuse of the one already there: the existing protection against a duplicate pong lives in
the function that arms the next ping, which clears any timer still running before setting a new
one, and it says nothing about whether a send stamp exists. So the measurement is guarded on the
send stamp itself. With no outstanding ping there is nothing to measure from, and the alternative
is to date the round trip from the previous ping, which reports a number that is not a
measurement of anything -- or, worse, to subtract from a null and put `NaN` on the screen.

**The last `stats` is remembered with the timestamp it arrived under.** `lastStats()` exists so
the store adapter can seed a view mounted into a live connection (§4.4.2) instead of showing an
empty screen for up to a broadcast interval. It returned the payload alone, and §4.4.1's channel
rule needs the envelope's `timestamp` to decide whether what it holds is older than what arrives
next -- so a seeded channel was written with no timestamp to compare against, and the ordering
guarantee started working only from the next live push. A guarantee with a hole at exactly the
moment a view mounts is one that fails when somebody opens the app, which is the moment it is
for. The client keeps the envelope stamp beside the payload and hands both over, as a
`StatsSnapshot` of `{ stats, timestamp }` -- the same pairing `Diagnostics` already uses in that
interface, rather than a second accessor that a caller could read without the first.

**Latency is cleared when the socket goes**, not carried across the gap. The figure describes a
link, and a link that has dropped has no round trip; showing 40 ms next to `offline` claims the
last thing the app knew is still true, which is the one thing it cannot be.

**The last error survives the reconnection that fixed it, until the connection is admitted.**
It is cleared when a connection **becomes** admitted -- `connected`, or `degraded`, which is an
admitted connection carrying stale data and not a failed one -- and not before: the failure that
matters to
somebody reading this screen is the one that is still happening, and a client that retries every
few seconds would otherwise erase the reason at the start of each attempt, which is where
§4.3.1's backoff spends most of its time. Once a connection is admitted the error describes a
condition that has demonstrably passed, and holding it on screen invites the owner to fix a
thing that is no longer broken. `at` is there so that a view can say when, rather than leaving
the reader to assume it was now.

**Cleared on the transition, never on the state being re-entered.** Every `stats` push sets the
state again while the connection is already live, once per broadcast interval. A clearing rule
written against the target state alone therefore fires every 20 s, and every error that arrives
on a live connection -- `pasv_unavailable`, `pasv_requires_auto`, `log_unavailable`, `no_frame`,
and every rejected request -- is erased seconds after it is recorded. The screen would show a
dash while the unit is refusing something on every tap. The test that distinguishes the two
readings is an error frame followed by a `stats` frame with no reconnection in between; the
condition is that the state was not already admitted, computed the same way the teardown side
already computes leaving.

**A restart is asked for, and so is the close that carries it.** `reboot`, `shutdown` and a mode
change all end the socket on purpose, and the state machine has a name for that window:
`restarting`. A close arriving in it records nothing. The app knows what it did and the owner
watched themselves do it, so putting `The connection closed (code 1006)` on the diagnostics line
explains an event that needs no explanation -- and for `shutdown` the client is terminal by
guard and never re-admitted, so the clearing rule never fires and that sentence stays for the
life of the app. The test is a close reached through `restarting` rather than through a frame,
which is the path the state machine's own tests do not take.

**A close records an error only when the app did not ask for it.** `close()` called by a screen
ends in `offline` like a dropped tether does (§4.4.2 says the client cannot tell those apart
afterwards), but at the moment of the call it knows perfectly well which one this is: `stopped`
is set by the caller before the socket goes. A user who taps disconnect and is then shown a last
error has been told their own action was a fault.

**A drop this client detected itself is recorded too, and it is the common case.** The pong
timeout is how a tether that went away is noticed: the socket is discarded deliberately, its
`onclose` is unhooked first so the teardown runs once, and the browser's own `1006` never
arrives. An implementation that records only on a close event therefore shows a dash next to
`offline` for the single failure this screen exists to explain. The same applies to the bound on
how long `connecting` may last. Both record a `LastError` with `source: 'local'` -- a third
source beside `frame` and `close`, because it is neither -- and a `code` naming the detection
rather than a number: `pong_timeout` and `connect_timeout`. `message` is empty; there is no
remote to have said anything, and inventing a sentence here would put words in the unit's mouth.

**A socket that throws while being rebuilt is the third one (issue #122).** §4.8 wraps the first
build, the first attach and the first `connect()`, so a `new WebSocket` that throws cannot escape
into a Svelte subscriber and wedge the store layer. It covers the first attempt only. The retry
chain -- the connecting bound firing, then offline, then the reconnect timer, then `beginConnect`
again -- runs the last of those inside a bare `setTimeout` callback with nothing above it, and a
`createSocket` that throws there escapes into the timer, where the app has no handler at all. It
is the same throw the first attempt is protected from, on the path taken hundreds of times more
often. It records `source: 'local'` with `socket_failed`, and the retry schedule continues, since
a browser that refused to open a socket once may not refuse the next one.

**The state stays `connecting`, and the connecting bound is cleared.** `beginConnect` sets
`connecting` and arms the bound before it asks for a socket, so a throw leaves both behind. The
state is right and stays: a retry is scheduled, the client is trying, and `connecting` is what
§4.3.1 calls trying -- dropping to `offline` would tell a screen the app has given up while a
timer is pending. The bound is wrong and goes: it was armed to catch a socket that takes too long
to open, and there is no socket. Left running it fires while the backoff is still waiting and
records `connect_timeout` over the `socket_failed` the owner is meant to read, which is a
diagnostic overwriting the diagnosis. Today the backoff caps below the bound so the retry clears
it first; that is an accident of two constants, not a guarantee, and the next person to raise the
cap would not know they were reading this paragraph.

**Every `beginConnect` reached from a timer is guarded, not just the one this ticket found.** The
reconnect schedule is one such caller and the pong timeout is another: it discards the dead socket
and rebuilds immediately, from inside its own `setTimeout` callback, with the same nothing above
it. Guarding the path that was reported and leaving its neighbour is how a defect gets fixed
twice, six months apart, by two people who each read a ticket rather than the file. The rule is
the shape, not the instance: a call that can throw, made from a timer, needs a handler in the
timer.

**Each of the three gets its own sentence, and the five-code table above does not govern them.**
That table is about codes the wire carries, where the rule is to reuse the wording another screen
already gives them. These three came from this client and no screen has ever worded them, so they
are written here: `pong_timeout` says the connection stopped responding, `connect_timeout` says
the unit did not answer while connecting, and `socket_failed` says the browser refused to open
the connection -- which is the one of the three that is about the phone rather than the unit, and
the sentence says so, because an owner told the unit is not answering will go and check the
unit. They must not collapse into each other or into the
generic sentence, because they are different failures -- a link that was working and went
away, one that never came up, and one the phone itself refused -- and the difference is most of
what the reader is on this screen to learn. Neither shows its code: unlike a close code, these
are names this app chose, and
printing an identifier the owner has no way to look up is not a diagnostic.

**A close that follows an `error` frame does not overwrite it, and `unauthorized` is what that
means.** The rule exists for one sequence: the plugin sends `unauthorized` and then closes with
1008, and recording the close last would replace the code that says what happened with a number
that says only that something ended. Read as "any frame error blocks any later close on this
connection" it does something else entirely -- a benign `pasv_unavailable` at ten in the morning
would suppress the drop that ends the link four hours later, and the screen would explain the
wrong event. So the rule is scoped to the code it was written for: **a close does not overwrite a
last error whose code is `unauthorized`**, which §4.3.5's table already names as the one code
fatal to the connection. Every other frame error is overwritten by whatever ends the
connection, because by then the connection ending is the newer fact. `unauthorized` arrives as a
frame and the plugin then closes with 1008 (§4.3.1); recording the close last would replace the
one code that says what happened with a number that says only that something ended. The frame
wins for the connection it arrived on, which is the same scope §4.3.1 gives the unauthorized
latch and for the same reason.

**`code` for a close is the close code in digits, and it is not a wire code.** `source`
discriminates them, so nothing has to guess whether `1006` is an `error.json` code that happens
to be numeric. `message` is the close reason when the socket carried one and the empty string
when it did not, which is the ordinary case for `1006`: a browser that never saw a close frame
has nothing to report but the code it synthesised.

**A close code is shown, and a wire code is not.** §4.5.2.2's rule that a code selects a sentence
rather than being printed is about strings a remote chose. A close code is not one: the browser
generates it, the set is closed, and it is small enough to read. It is also the only thing that
separates the three failures this screen exists for. `1006` with no reason is the tether gone or a
certificate the phone refused, `1008` is the unit answering and declining the token, and a clean
`1000` from the other end is a unit that shut down while connected. Collapsing those into one
sentence about the connection closing unexpectedly throws away the diagnosis on the screen whose
whole job is to give it. So the close case renders the number beside its sentence; the frame case
renders the sentence alone.

**The line says when.** `at` is rendered, as a time of day rather than an age: an age has to be
recomputed to stay true, and §4.5.2.5 spends the one timer this client is allowed on the Log. An
absolute time is written once and stays correct.

**A stamp from another day says so.** A last error is cleared only on admission, so one recorded
at 23:59 survives a night with the app open and offline, and a bare `HH:MM` then reads as
something that happened today. The date is added once the stamp is not the current day, which is
computed at render and costs no timer: the value being compared is the calendar day, and a render
that happens after midnight without one is a render nobody is looking at.

**It is the phone's clock, and it is not `formatUnitTime`.** `at` is stamped by `deps.now()` in
the browser. Every other timestamp this app renders came off the wire and belongs to the unit,
whose clock can be days out (§4.3.10's reason for ignoring `pong.timestamp` is the same one). A
diagnostics line that formatted a phone stamp with the unit's formatter would be at its most
misleading exactly when the two clocks disagree, which is the case somebody is on this screen to
work out.

**Every code goes through, including the ones §4.3.5 routes elsewhere.** `log_unavailable` has
its own sentence in the Log view and it is still the last error the connection saw. The table in
§4.3.5 says where an error is *acted on*; this records what *happened*, and a diagnostics block
that omits the errors somebody else already handled is a diagnostics block that goes quiet
exactly when the app is at its busiest.

**The latency is whole milliseconds with its unit, and it is not rounded to seconds.** `123 ms`.
On this link the interesting range is tens of milliseconds against several hundred, and a figure
rendered in seconds is `0 s` for both. `0 ms` is a real measurement and renders as one: it is a
round trip that completed inside the clock's resolution, not an absent value, and the two must not
collapse into the same cell (§4.5.1.1 gives `data-empty` to the absent one only).

**Which sentence a frame code selects is fixed here**, so that two screens do not word the same
code differently. `unauthorized`, `pasv_requires_auto`, `pasv_unavailable`, `log_unavailable` and
`no_frame` reuse the sentence they already carry where §4.3.5's table sends them; every other
frame code gets one generic sentence. `unauthorized` reuses the **reason** it already carries and
not the call to action beside it: "Fix it in Settings" is a sentence for the screen that sends the
reader somewhere, and this line is already on the screen it would send them to.

The generic sentence is not a failure of the mapping, it is §4.3.5's own answer for an
unrecognised code, and it is why the `message` is rendered beside it in both cases.

**"The close case renders the number beside its sentence" above is about the code**,
which a frame never shows and a close always does; the `message` is a separate field and it is
shown whenever there is one. A line is assembled from at most three parts -- the sentence, the
remote message when it is not empty, and the time -- and it reads as prose rather than as a
sentence with its own full stop glued to a second one. Not necessarily *one* sentence: the copy a
code reuses is whatever that code already carries, and `log_unavailable` carries two, because the
Log view needs the second one. Reusing it here means this line carries two as well, which is the
price of the rule that one code is worded once. What the assembly owes is the absence of a
doubled stop at a join, not a sentence count.

**The sentences arrive with their full stops and the assembly takes them off.** That is string
surgery and it looks like something to tidy away, so the constraint is written down here to stop
somebody tidying it: every sentence this line reuses is an exported constant that other screens
render on its own, where the full stop belongs. The Dashboard's unauthorized banner joins the
reason to its call to action with one, and the PASV control renders its reason standing alone.
Removing the full stop at the source to spare this line would break those, and duplicating each
sentence in a second, stopless form is exactly the two-wordings-for-one-code problem this
section already refuses.

**The bound is 200 characters**, counted in code points and not in UTF-16 units, so a truncation
can never cut a surrogate pair in half and leave a lone half to render as a replacement
character. The number is not load-bearing and no behaviour depends on its exact value; it is
written down here so that a test can assert it instead of asserting that some bound exists,
which is an assertion a missing bound also satisfies.

**A message that was cut says it was cut**, with `...` after the two hundredth code point and
only when something was actually removed. The bound counts the content, so a truncated line
carries 203 code points and an untruncated one carries what it carried. Without the marker a unit
that sent a long message and a unit that sent a terse one read identically at the cut, and the
reader has no way to tell that the sentence they are looking at stops mid-thought because this
app decided it should.

**A `message` that is not a string has no message.** The type says `string` and nothing validates
it (issue #109), so the runtime value can be anything the wire carried. Coercing it prints
`null`, `undefined` or `[object Object]` on the line, attributed to the unit, which is worse than
saying nothing: it is a sentence the unit never sent, on the one screen whose job is to report
faithfully what it did send. So a non-string is treated as absent -- the code's sentence and the
time still render, because those are facts this client established itself -- and the bound is
computed on the string that survives, not on a `.length` that may not be a character count at
all. That last part is what the audit demonstrated: an array-valued `message` sailed past a
200-character bound and put 1012 characters on the screen.

**While the state is `unauthorized` two rows carry the same sentence, and that is allowed.** The
reason row and the last-error row sit four rows apart and both read "the unit refused the stored
token", the second with a time after it. It reads as the screen repeating itself, and the two
obvious ways to stop it both cost more than the repetition does: letting `unauthorized` fall to
the generic sentence throws away the one code the reader most needs named, and suppressing the
last-error row while a reason is showing blanks the diagnostics line during the exact failure it
was built for. They answer different questions -- what the connection is doing now, and what
happened and when -- and the labels say which is which. The repetition is the honest cost of both
rows being right.

**Neither value is rendered raw.** `message` came off the wire, so §4.5.3 applies to it in full:
it is text, never markup, never an attribute, and the view bounds its length rather than trusting
a remote to have been brief. `code` is matched against known sentences and never printed, which
§4.5.2.2 already required of it on the command path.

### 4.4 Stores (`lib/stores.ts`)

Svelte writable/derived stores: `connection`, `stats`, `accessPoints`, `handshakes`, `peers`,
`log`, `gps`, `face`, `capabilities`, `channel`. Views subscribe; pushes from the plugin update them live.

**The stores subscribe to the client, never the reverse.** `lib/ws.ts` knows nothing about
Svelte and nothing about this file: it is handed to an adapter here, which subscribes to its
state and its messages and writes the stores. That keeps the connection layer testable without a
store and the stores testable without a socket, and it is why §4.3 could be built and reviewed
before this section existed.

#### 4.4.1 What each store holds, and what writes it

| store | written by | rule |
|---|---|---|
| `connection` | the client's state, its unauthorized reason, and its diagnostics (§4.3.10) | a mirror, not a second opinion. §4.3.1 owns the state machine. **With no client attached it reads `offline`**, which is the same rule rather than an exception to it: a mirror of nothing shows nothing connected, and leaving the last client's final state standing would have the app claim a connection that no longer exists |
| `stats` | the last `stats` message | **the payload as it arrived, never patched.** See below |
| `capabilities` | derived from `stats` | not a store of its own with its own writer: it arrives inside `stats` and having two sources for one fact is how they disagree |
| `accessPoints` | `wifi_update`, and the `access_points` reply | **replaced whole, never merged** |
| `handshakes` | the `handshakes_list` reply only | **the whole payload**, `entries` with `truncated` and `total` beside them, not the entries alone: §4.5.2 renders the truncation notice and a `500+` badge, and a view subscribes to stores rather than to the client, so a fact dropped here is a fact no view can reach. A push asks for the list rather than writing one, see below |
| `peers` | the `peers_list` reply, and each `peer_detected` push | list replaces; a push upserts on `fingerprint`, which is the identity in `common.json#/$defs/Peer`, not `name`, which two units can share. **The default identity `'???'` (§2.8) is not an identity and never matches**: those peers are appended, because collapsing every unadvertised unit into one row would show one peer where there are several. Order in the list is the view's business (§4.5.2 sorts by signal and last seen), so this file promises none |
| `log` | the `log_lines` reply | **replaces, never appends.** The plugin owns the tail and the clamp (§2.9); appending would grow a second, divergent buffer in the app |
| `gps` | `gps_update`, and the `gps` field of `stats` | whichever arrived later. Both are the same resolved shape (§2.12), so there is nothing to reconcile |
| `face` | `face_status`, and the status half of `status_change` | §2.13. `status_change` also carries a `mood`, which is deliberately dropped: `FaceStatus` has no field for it and no view in §4.5 renders one |
| `screen` | the `screen_image` reply | on demand only, never pushed (§2.10). Held like everything else rather than read off the request's promise, so §4.5.1.1's rule that a view subscribes to stores has no exception carved for the one message that happens to be heavy. Cleared by `resetStores()` with the rest: one unit's display left on another's Mirror is the same defect as one unit's log lines on another's Log, and the harder one to notice, because a picture of a face carries no address to give it away |
| `channel` | `channel_hop`, and the `channel` field of `stats` | the later of the two sources, see below. Exported because the hop is worth showing before the next broadcast, which on this link is up to 20 s away |

**`stats` is never patched, and that is the decision this table exists for.** `channel_hop`
carries a channel and `stats` carries one too, arriving up to 20 s apart. Writing the hop into
the last `stats` object would produce a payload that never left the unit, whose `sessionAge`
claims a freshness the patched field does not have, and staleness is read from that field
(§4.3.1). So the hop goes to a `channel` store of its own. A view that wants the current channel
reads that store; a view that wants the unit's own snapshot reads `stats`.

**"Whichever came later" is a timestamp, not an arrival order (issue #162).** Both messages carry
the channel and both carry the unit's own `timestamp`. A plain writable set from each handler
gives *last arrived*, and the two are not the same: `stats` is broadcast on a ticker while
`channel_hop` is pushed when the hop happens, so a `stats` produced before a hop can be delivered
after it and overwrite a newer channel with an older one. The app then shows a channel the unit
has already left. That is why the app and the e-ink display disagreed **sometimes** rather than
always: the sometimes is the window between the two.

The store therefore keeps the timestamp it last wrote and ignores a message carrying an older
one. **Both timestamps come from the same unit**, so this compares a clock against itself, which
is the one comparison a remote clock can be trusted for -- §4.3.10 refuses to subtract the unit's
clock from the phone's, and this does not. An equal timestamp is accepted: two messages can
carry the same second, and the one that arrived second is the newer of them.

**The `restarting` reason reaches the store, because two of its values mean opposite things
(issue #131).** §4.3.1 made the reason a field rather than a state on the grounds that
`mode_change`, `reboot` and `shutdown` differ in wording alone. That is true of the first two and
false of the third: §4.3.1 makes `shutdown` terminal, suppresses reconnection and arms no
patience timer, so it is the one case in the machine where nothing is going to happen next. A
banner that says "the unit is restarting" to somebody who just tapped shut down tells them the
opposite of what the app knows, and leaves them waiting for a screen that is not coming.

So `ConnectionView` carries it as **`restartReason`**, beside the state, the same way it carries
`unauthorizedReason` and for the same reason: a view subscribes to stores and never to the
client, so a fact the adapter does not copy is a fact no screen can reach. It is null in every
state but `restarting`,
which is what the unauthorized reason already does in every state but `unauthorized`.

**And the Dashboard banner renders it, in this change rather than a later one.** A field written
by the adapter and read by no view is plumbing, and plumbing that nobody has connected looks
identical to plumbing that works: the next reader assumes the fact is on screen because the store
carries it. The banner already has the branch and a comment naming this issue as the reason both
reasons share one line. The copy: `mode_change` and `reboot` keep **"The unit is restarting."**,
and `shutdown` reads **"The unit is shutting down."** -- present tense, no promise of a return,
because §4.3.1 makes that state terminal and the app is not waiting for anything. That is the
whole of the defect this ticket describes: somebody taps shut down and is told the unit is coming
back.

**A `handshake` push does not write the list, it asks for it.** The push carries `{filename,
ap, station, gps}` (§2.13); an entry in the list carries `ssid`, `bssid`, `mtime` and `size`,
which the plugin gets from parsing the capture name and stat-ing the file (§2.7). The three
ways to close that gap are to invent the missing fields, to reimplement the plugin's filename
parsing in the client, or to ask. Inventing puts fabricated data on a screen, and a `size` of
zero or a `ssid` holding a filename is worse than a row that appears a moment later.
Reimplementing duplicates a rule that lives in §2.7 and will drift from it. So the push is a
**signal**, not a payload: it tells the client the list changed, and the client issues
`get_handshakes` and takes the reply. **At most one such refresh is in flight**, so a unit that
captures several handshakes in a burst produces one round trip and not one per capture, on a
link where the round trip is the expensive part.

**That refresh belongs to the client that asked for it**, and a remount is not a reason to ask
again: the same client attaching a second time finds its own refresh still outstanding and
coalesces into it, because the reply will arrive and write the store either way. A refresh owned
by a client that is no longer attached is a different thing and counts as not in flight: its
reply can no longer reach a store, so treating it as outstanding would let a new host's first
capture disappear into a previous host's request. Nothing wedges in either case, because every
read settles, if not by a reply then by the 15 s timeout that §4.3.8 arms at the call.

The push is still worth having for what it carries directly, which is that a capture *just
happened*: that is a notification, and it does not need the row to be on screen yet.

**`accessPoints` is replaced rather than merged**, and the reason is the failure merging causes:
bettercap's list is what is visible now, so an access point that dropped out of range is absent
from the next one. A merge keeps it on the screen for ever, and the app's whole claim is that it
shows what the unit sees.

#### 4.4.2 What survives a reconnect

**One client at a time.** The stores are module singletons, so a second `connectStores` while
one is still attached would have two adapters writing the same stores, and either one's
`unauthorized` would blank the other's data. It is refused rather than tolerated: calling it
twice is a bug in the caller, and a loud failure at mount is cheaper than two units' data
interleaved on one screen. **Switching hosts is therefore teardown, then `resetStores()`, then
attach** (§4.5.2 gives Settings a host list, so this is a real flow and not a hypothetical): without
the reset, the previous unit's access points, peers and handshakes stay on screen under the new
one, which is the same disclosure the clearing rule below forbids on `unauthorized`.

**The data stores keep their last values across a drop.** The link goes down often by design
(D1), and blanking every screen each time would make an ordinary tether hiccup look like a
failure. The connection state says what is happening and §4.3.1 already decides how staleness is
shown; the data does not need to vanish to make that point.

**They are cleared on `unauthorized`,** where the data belongs to a session the unit refused:
leaving it on screen behind a token prompt shows somebody the contents of a unit that has just
declined to talk to them. **After an explicit `close()` they are cleared too, but by the
caller**, for the reason given below.

Nothing is cleared on `offline`, which is the one people will be tempted to add.

Detaching sets `connection` to `offline` for the same reason: the adapter is what reflects a
client, and after a teardown there is none. This matters most where it is least visible, when a
client could not be built at all (§4.8): there is no client to emit a state, so without this the
banner would still show whatever the last one said before it went away.

**That is the caller's because the client cannot tell it apart.** An explicit `close()` and a
dropped tether both end in `offline` and nothing on the client's surface distinguishes them, so
the adapter clears on `unauthorized`, which it can see, and the screen that asked to disconnect
calls the reset itself. `resetStores()` clears the data and leaves `connection` alone: it is a
mirror of the client (§4.4.1), and reporting `offline` while the client is connected is exactly
the second opinion that row forbids. Discriminating them inside the
adapter would mean adding a signal to §4.3 for the benefit of this file, which is the wrong
way round.

**The adapter seeds from the client when it attaches**, reading the current state and the
last `stats` rather than waiting for the next push. A view mounted while the connection is
already live would otherwise show an empty screen for up to a broadcast interval, which on
this link is 20 s of blank for data the client already holds.

**A `status_change` arriving before any `face_status` is dropped.** It carries the status
half only (§2.13), and there is nothing to merge it into; filling the other fields with
placeholders would put a face on screen that the unit never drew.

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
and the two are one expression rather than two numbers that must agree. The consequence,
nothing painted below the content area in either orientation, is asserted as a geometry
invariant (§10.5).

**Touch targets need separation as well as size.** 44px is the floor for how large a control
is, and says nothing about how far it sits from its neighbour. Those are different failures: a
small target is hard to hit, while two large targets flush against each other are easy to hit
*wrongly*, which matters most where the neighbour does something serious. The bar entries, and
any row of adjacent controls, carry a gap.

**Contrast is WCAG 2.1 level AA**: 4.5:1 for body text, 3:1 for large text (>= 24px, or >=
18.66px at weight >= 700) and for icons and other non-text indicators (1.4.11). AAA is not
adopted: 7:1 is not reachable on a dark palette without flattening it, and a level nobody can
meet gets waived case by case until it means nothing.

**A map never owns the whole views area.** The map view scrolls the page under a drag as well
as panning the map itself, and those two gestures start the same way. If the map filled the
area in both directions there would be nowhere to begin a drag that means "scroll the page", and
on a phone that is the only way out of the view. So the map is always narrower or shorter than
the views area, in both orientations, and the leftover strip is the part of the rule that does
the work rather than an aesthetic margin. Asserted as geometry rather than as pixels (§10.5).

**No `background-image` behind text.** A gradient has a different contrast ratio at every point,
so text over it can be legible at one end and not the other, and no automated check can return
a verdict. Forbidding it is what keeps the contrast gate meaningful; the gate reports any it
finds rather than silently passing them.

#### 4.5.1.1 Dashboard presentation (issue #121)

The rules below were decided before the view was written rather than during it, because each
has a plausible wrong answer that the screen would then carry for ever. The section grew across
two review passes and is no longer the five decisions it started as; what it is now is the whole
contract for this view, and the tests for it are written from here rather than from the code.

**A value that is not known renders as a dash, and its row stays.** `stats` is null before the
first frame, and afterwards `channel`, `temperature`, `lastHandshake`, `lastPeer` and both
`battery` fields are nullable by schema. Every one of them renders as `-` in the value slot with
its label still beside it. Hiding the row instead would move everything else under the reader's
thumb between two frames twenty seconds apart, and it would erase the difference between a unit
with no temperature sensor and a value that has not arrived yet. Those are the same statement,
"this is not known", and the screen makes it once, the same way, everywhere. Before the first
frame every row is a dash and the banner says why: the same rule, not a special case for it. A
dash is not readable aloud, so the value carries an accessible *unavailable* alongside it.

**The staleness marker is the banner, not the fields.** In `degraded` the data stays at full
contrast and is marked in one place, with its age (§4.3.1). Dimming the values would trade
legibility for a signal in the one state where the app can still act, and against the AA floor
this section already commits to. Marking each field would repeat a single fact once per row,
when staleness is a property of the payload as a whole and of no field in it. The age is
rendered in whole seconds below a minute and in whole minutes above it, and a `sessionAge` of
`null` says the snapshot has never refreshed rather than showing an age of zero, which is what
that field means (§2.5).

**Outside `degraded`, the banner is still the marker, and the data stays.** In `offline`, and in
the `connecting` a pong timeout re-enters, the last snapshot remains on screen with no age beside
it. That is deliberate and it is not a hole in the rule above. §4.4.2 owns what survives a
reconnect, and clearing the screen every time a phone walks out of range would empty the app
several times an hour on this transport for no gain. The banner naming the disconnection is a
stronger statement about the data than any age would be: `degraded` needs an age because the
socket is alive and the reader would otherwise believe the numbers are current, while `offline`
has already said the app is not connected to anything. `unauthorized` is the one state that
clears the data instead, and it does so in `lib/stores.ts` rather than here (§4.4.2), because
that data belongs to a session the unit refused.

**The counters say what they count.** The handshake counter is `stats.handshakesTotal`, which
§4.5.2 already names as the authoritative answer to "how many handshakes does this unit have".
`stats.handshakes` is the narrower `*.pcapng` count that matches the unit's own display, and it
appears **only when the two disagree**, as a second line naming what it is. That is the whole
of the decision: when the numbers agree there is one number and no explanation to read, and
when they disagree the screen explains the disagreement itself instead of leaving the reader to
discover it against the Captured badge and conclude that one of the two is broken.

**Both counts can be null, and the second line needs both.** The plugin sends `null` for
`handshakes` and `handshakesTotal` when it does not know where the captures are (§2.5), so the
handshake counter dashes on a null exactly as it does on a missing `stats`. The
`handshakesOnUnit` line is rendered only when **both** counts are known and they differ: with a
null on either side there is no disagreement to explain, and a row reading "On the unit's own
display: —" beside a dashed total explains nothing while implying the app looked and found
something absent. One dash on the counter is the whole of what the app knows.

**The card is one snapshot, with two named exceptions.** Mode, uptime, battery, temperature and
the counters all come from the `stats` store, so the age the banner declares describes every
one of them. The exception is the channel, which comes from the `channel` store: §4.4.1 exports
that store precisely because a hop is worth showing before the next broadcast, up to 20 s away
on this link. GPS is the second exception, and for the same reason: §4.4.1 gives the `gps` store the later of
`stats.gps` and a `gps_update` push, so reading `stats.gps` here would leave a fix acquired
between two broadcasts invisible for up to 20 s while a channel hop appeared at once. That
asymmetry was in the first implementation of this view and nobody had decided it. Two exceptions
and no more: both are stores §4.4 created precisely because their value moves faster than the
broadcast that carries it, and a third would mean the one-snapshot rule is not the rule.

The face and the status line come from the `face` store, which is a different
message with a different cadence (§2.13) and makes no claim about the session snapshot's age.
The mode badge is not read from `face.mode`: two sources for one fact is how they disagree, and
`stats` is the one the banner speaks for. A **null** mode renders as a dash like any other
unknown value (issue #140): the plugin sends null when it has no agent to ask, which is what a
unit in manual mode gives it, and a badge is not exempt from the rule the rest of the card
follows. The badge renders `MANUAL` as **MANU**, matching the
word on the unit's own display, which is what the operator is comparing it against, and it is a badge rather than another
label-and-value row: §4.5.2 asks for one, and the mode is the single field on this screen that
the two-step controls of §4.5.2 will change, so it is the one the eye should find without
reading the column.

**The battery is two rows, and that is a reversal.** A single row combining them was tried
first, rendering `84%, charging`. It fails on the partial read, which is not hypothetical: the
plugin returns a charging state with no percentage when a provider answers one and not the
other. Combined, `{percent: null, charging: true}` had to become either a dash, discarding a
fact the unit sent, or the bare word `charging` under a label reading `Battery`, which reads as
the battery's whole answer while the percentage is silently missing. The mirror case,
`{percent: null, charging: false}`, was worse still: it rendered as unavailable while the unit
had in fact said something. Two rows have no such case. Each is one fact, each dashes on its own
`null`, and the rule needs no exception.

**`gpsSource` and `gpsFix` are two fields, not one.** §4.5.2 names "GPS source and fix
indicator", and they answer different questions: which provider resolved the position, and
whether there is currently a position at all.

**The last handshake and the last peer are named, not merely timed.** `lastHandshake` renders
its `ssid` beside its time of day, and `lastPeer` its `name` beside its `lastSeen`. A bare time
answers "when" and leaves out the half of the row an operator is actually reading, which is what
the unit caught and who it met. It is also the reason this view is where §4.5.3 first bites: an
SSID is 32 bytes chosen by a stranger and a peer name arrives over the mesh from somebody else's
unit, so those two strings are the hostile ones on this screen, next to the `face` and the
`status` line. When the timestamp is absent but the name is not, the name still renders and the
time is a dash; when the whole object is `null` the row is a dash by the rule above.

**A GPS source that is switched off has not failed to get a fix.** When `gps.enabled` is false
the fix indicator reads `off` rather than `no fix` (§2.12: `enabled` is false when `gps_source`
is `none` or no provider answered). `no fix` says the unit is looking and has not found one,
which is a thing to wait for; `off` says nobody is looking, which is a thing to change in
`config.toml`. Reporting the second as the first sends the reader outdoors to wait for a fix
that is never coming.

**A timestamp from the unit renders as a time of day, never as a relative age.** The reference
hardware has no real-time clock, so after a boot with no network its clock can be hours out.
A relative age is computed against the phone's clock and would then read "in four hours" for
something that has already happened, which is a lie the app manufactures out of two honest
values. A time of day is the instant the unit stamped, rendered in the reader's own timezone,
which is where the reader is; it is wrong exactly as far as the unit's clock is wrong, and not
in a new way of the app's making. Uptime and `sessionAge` are unaffected: they are durations the
unit measured, not instants it stamped.

**It carries its date whenever it is not today, and its year whenever it is not this year.** A
bare `09:14` on a handshake caught last Tuesday reads as this morning, which is a freshness the
payload never claimed - the same defect as a relative age, arrived at by omission rather than by
arithmetic. The last handshake and the last peer are routinely days old on a unit that has been
out in a pocket, so this is the common case rather than the edge one. The year is the same
omission one unit up, and it is not hypothetical on a device that keeps its captures until
somebody clears them: `5 Aug 09:14` on a handshake from last August reads as three weeks ago.
Today's events keep the bare time and this year's keep the bare date, because a date on every
row, or a year on every date, is noise on the rows the reader is most likely to be looking at.

The day is written without a leading zero. That is a real decision and not a detail: a test
written against a padded day, matching on a word boundary, is green from the thirteenth of the
month and red from the fourth to the twelfth, because `5` does not sit on a word boundary inside
`05`. Found exactly that way, in this section's own tests, on a day of the month where it
passed.

**The banner is one element that always exists, carrying the state, and it has copy only where
the state has something to say.** A live region, so a change is announced without the reader
having to go back and look. Every sentence is pinned verbatim:

| state | line |
|---|---|
| `connecting` | *(no copy)* |
| `connected` | *(no copy)* |
| `degraded`, `sessionAge` known | `Connected, and the data is <age> old.` |
| `degraded`, `sessionAge` null | `Connected, and the data has never refreshed.` |
| `offline` | `Not connected. Reconnecting automatically. Check that the Personal Hotspot is on.` |
| `unauthorized` (*rejected*) | `The unit refused the stored token. Fix it in Settings.` |
| `unauthorized` (*required*) | `The unit requires a token and none is stored. Add it in Settings.` |
| `restarting` | `The unit is restarting.` for both reasons today, see below |

**The two silent rows are §4.3.1's, not an omission here.** That section's own table says
`connecting` shows "a quiet indicator, no copy - the normal first second" and `connected` shows
"the data, unqualified". A first draft of this section gave both a sentence, which put two
answers to the same question in one document; §4.3.1 is the one that wins, and it is right:
a banner that says `Connected.` on every screen the user opens is a line they stop reading, and
the day it says something else they will not notice. The element still carries its state, so a
test can tell "connected, and deliberately silent" from "the banner is broken".

**The copy is pinned verbatim rather than paraphrased**, and that is a reversal within this
section's own life. The paraphrased version produced two defects in one review pass. A mutation
that merged `degraded`'s two sentences into one template survived a test asserting the presence
of `never refreshed`, because `Connected, and the data is never refreshed old.` contains it. And
swapping the two `unauthorized` arms, so that a user whose token was refused is told to add one,
survived a test asserting only that both sentences mention a token and differ from each other.
Both mutations are invisible to any assertion weaker than equality, and a test cannot assert
equality against wording that lives nowhere. So the wording lives here. The cost is real and
accepted: an improvement to the copy is now a change to this document, which is the correct
price for copy that is the only thing standing between a user and a wrong instruction in a
field.

**`degraded` is two sentences and not one sentence with a hole in it.** A `sessionAge` of `null`
is not an age, so substituting `formatAge(null)` into the first row produces `the data is never
refreshed old`. The two rows above are written out separately for that reason and neither is
derived from the other.

**Which sentence to use is decided by what `formatAge` returned, not by testing the field for
`null`.** `formatAge` answers `never refreshed` for a non-finite value as well as for `null`, so
a guard reading `sessionAge === null` lets exactly that string back into the first sentence and
reconstructs the defect one layer up. `format.ts` exports the constant so the comparison is
against the same string the formatter returns rather than against a copy of it.

**`restarting` has one sentence today and should have two, issue #131.** `connection` mirrors
the client's state and its unauthorized reason and nothing else (§4.4.1), and the reason a
`restarting` message carried lives inside `lib/ws.ts`, where it drives the patience timer and is
exposed to nobody. So a view can tell `restarting` from `connected` and cannot tell a reboot
from a shutdown, and the row above is the one line it can honestly render.

**The second sentence is not written here, and that is deliberate.** An earlier draft of this
paragraph claimed the two were "specified", while the table held one row and the other wording
appeared nowhere in the document - which is precisely the failure this section argues against
two paragraphs above, copy that a test cannot assert equality against because it lives nowhere.
The distinction is worth having and the reason is worth stating: `shutdown` is the one state in
§4.3.1 where nothing happens next, and telling that user the unit is restarting is the opposite
of what the app knows. Deciding the two sentences belongs to #131, together with the store
change that makes either of them renderable. §4.3.1's own state table, which says `restarting`
shows "the reason the plugin gave", is wrong for the same reason and is corrected there by the
same ticket.

**The functions, and what each returns.** Named here rather than left to the implementation,
because the tests for this view are written from this document and not from the code (§10):

| function | returns |
|---|---|
| `DASH` | the dash itself, `'-'`, exported so no caller writes the character |
| `EMPTY_LABEL` | the accessible text carried beside a dash, `'unavailable'` |
| `NEVER_REFRESHED` | what `formatAge` returns when there is no age, `'never refreshed'`, exported so the banner compares against the formatter's own answer rather than a copy of it |
| `formatUptime(seconds)` | the two largest units that are not both zero: `45s`, `1m 30s`, `1h 0m`, `1d 1h`. The second unit is kept even at zero, so the string does not change shape as it counts down. Seconds alone below a minute |
| `formatAge(seconds \| null)` | whole seconds below a minute (`40s`), whole minutes above it, floored (`4m`), and `never refreshed` for `null` |
| `formatTemperature(celsius \| null)` | `48 °C`, rounded to the nearest degree; `DASH` for `null` |
| `formatBatteryPercent(percent \| null)` | `84%`, rounded; `DASH` for `null` |
| `formatCharging(charging \| null)` | `charging`, `on battery`, and `DASH` for `null` |
| `formatChannel(channel \| null)` | the number as it arrived; `DASH` for `null` |
| `formatMode(mode)` | the mode as the unit's own display writes it: `AUTO`, `PASV`, and `MANU` for `MANUAL` |
| `formatGpsFix(gps)` | `off` when `enabled` is false, otherwise `fix` or `no fix` |
| `formatGpsSource(gps)` | the source name as it arrived; `DASH` when it is `null`, which is the ordinary dash rule and not a fourth word. `off` belongs to the fix indicator, which is the row that answers "is it looking": a source is a name, and the absence of a name is the absence of a name |
| `formatUnitTime(epochSeconds \| null)` | the time of day in the reader's timezone, hours and minutes, and in front of it the date whenever the instant is not today: `09:14` today, `5 Aug 09:14` earlier this year, `5 Aug 2025 09:14` in another year. The day is **not** zero-padded. `DASH` for `null`. A timestamp in the phone's future still renders, which is the whole point of the rule above |

Rounding rather than truncating, for the temperature and the battery: 47.6 shown as 47 is a
degree the unit never reported, and the direction of that error is always the same one. Ages and
uptimes floor instead, and for the opposite reason: they are durations that are still running,
so rounding `59.6 s` up to a minute would have the screen reach a threshold before the unit did.

**A value that is not a finite number is not known.** Every function above returns `DASH` for
`NaN`, for either infinity and for a negative duration, and `formatAge` returns its
`never refreshed` for the same inputs, which is that function's own way of saying the same
thing. None of this is reachable from a conformant plugin: the schemas make these numbers and
the plugin is their only writer. It is stated because the alternative, which an implementation
reached on its own, is a screen reading `NaN:NaN` or an uptime of `0s` on a unit that has been
up for a week - a value nothing ever sent, which is the one thing §4.4.1 forbids everywhere
else. It is stated *here* rather than left as defence in depth because §10.7 gates
`src/lib/**` on branch coverage, and a guard nobody specified is a branch no independent test
author knows to reach.

**The DOM hooks.** `data-field` on the value element of every field: `uptime`, `mode`, `battery`,
`temperature`, `charging`, `channel`, `accessPoints`, `handshakes`, `handshakesOnUnit`, `peers`,
`lastHandshake`, `lastPeer`, `gpsSource`, `gpsFix`, `face`, `status`. Named for the field, which
is usually a `Stats` member and is not always one: `face` and `status` come from `FaceStatus`,
and `handshakesOnUnit` is a second rendering of `stats.handshakes` beside `handshakesTotal`.
`data-empty="true"` on any field with no value, and `data-banner` on the banner carrying the
connection state it is rendering.

**`data-empty` is decided by the value, never by the string, on the fields that carry a remote
string.** A field is empty when the value behind it is absent, not when the text it produced
happens to equal a dash. The two come apart on exactly the strings this view exists to be
careful with: a network named `-` is a legal SSID, and a peer may call itself `-` as easily as
anything else. Deciding by the string hands a stranger the ability to make a real reading render
as `data-empty="true"` with the accessible *unavailable* beside it, which is a remote party
choosing what the screen says about the unit's own state (§4.5.3). Those fields are `face`,
`status`, `lastHandshake`, `lastPeer` and `gpsSource`.

**A remote string that is empty is empty.** `face`, `status` and `gpsSource` are absent when
their string is `null` **or** `''`, and the first implementation checked only the first, so a
unit that sent an empty face produced a row with a label and nothing beside it: no dash, no
`data-empty`, and silence where a screen reader should have heard *unavailable* (issue #142).
That is not trimming or normalising the value, which §4.5.3 forbids: a name of `-`, or of a
single space, is a real reading and renders as one. The claim is only that a string with nothing
in it is nothing to show.

For `lastHandshake` and `lastPeer` the value is the pair, so the field is empty when the object
is absent **or** when it carries neither half: an empty name and no timestamp. Without that
second clause a peer with an empty name and no `lastSeen` renders a dash that nothing declares
empty and that carries no accessible *unavailable*, which is the one hole the value-decided rule
would otherwise leave. Testing the formatted string instead would close that hole and reopen the
other one, since a peer may legally call itself `-`.

Everywhere else the comparison against `DASH` is the correct test and the safer one, because the
value is a number and no number formats to a dash except by the rule that says it is unknown.
It is also the only test that stays true: a first implementation re-derived emptiness in the
view as `=== null`, which agreed with `format.ts` on `null` and disagreed with it on every
non-finite value, so a dash appeared with nothing declaring it empty and no accessible
*unavailable* beside it. Two expressions of one rule, in two files, one of them outside the
coverage gate (§10.7). The formatter owns the rule; the view asks it. Every label also stays beside its value, which is half of the rule above and
the half a test is most likely to leave unasserted. Declared hooks, the same way `data-view`
and `data-region` are (§4.5), and for the same reason: a test that finds a value by walking the
markup is a test that breaks when the markup is rearranged, which is the change this view will
see most.

**Every rule above that turns a value into a string lives in `lib/format.ts`, not in the
component.** Rounding a temperature, choosing seconds or minutes for an age, cutting an uptime
to its two largest units, deciding that `null` is a dash: that is logic, and §10.7 excuses
`src/views/` from the coverage gate on the grounds that a view is markup. It is the right
exclusion and this is how it stays right - a view that formats is a view that computes, and the
part that computes belongs where the gate can see it. `Dashboard.svelte` subscribes to stores
and lays out what those functions return.

### 4.5.2 Views

- **Dashboard**: text face + status card, mode badge (AUTO / **PASV** / MANU), uptime, battery
  (percentage and charging state, one row each), temperature, channel, counters (APs /
  handshakes / peers), the last handshake and the last peer, each named as well as timed
  (§4.5.1.1), GPS source and fix indicator, connection banner. Controls: mode switch, PASV toggle (rendered only when
  `capabilities.pasv`), reboot, shutdown. **Mode switch, reboot and shutdown all use the
  two-step confirm** (D9 + D12, shaped in §4.5.2.2) and all show the `restarting` state
  afterwards. **A null `mode` does not disable the mode switch**, which is the one place a dash means something other than
  "nothing to do here": a null mode is a unit with no agent, which is a unit in manual mode
  (§2.6.0), and the switch is the control that takes it back to auto. Greying it out there would
  disable the rescue on exactly the unit that needs rescuing, which is the defect issue #147
  removed from the plugin and which the client is equally able to reintroduce.
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

#### 4.5.2.1 Settings presentation and DOM hooks (issue #134)

This is the screen that decides which unit the app talks to, and on a fresh install it is the
only screen that matters, because nothing else can be reached until it has been used. §4.7 owns
the data; what follows is the shape.

**The host list comes first and the add-host form sits under it.** The common visit is an owner
correcting the address of the unit in front of them, not adding a second unit, and a form above
the list pushes the list off a phone screen for the case that almost never happens.

**The add-host form is always visible, with no toggle.** A toggle is one more state and one more
tap for nothing, and it fails exactly where it matters: §4.7 makes an empty host list a
reachable state, and a screen whose only escape hatch is hidden behind a control is a screen
somebody can be stuck on. The order and the absence of a toggle are recorded here because the
first implementation arrived at both by accident, having been shaped to fit a test file whose
author was guessing.

**Ports are rendered and not editable**, with §4.5.1's reason: making them editable means the
plugin accepts writes to its own configuration and then restarts to apply them, which drops the
socket carrying the confirmation.

**A token is edited in the row, beside the host it belongs to.** It has no grammar to check - a
token is whatever the unit was configured with - so there is nothing for a validation step to
do, and putting it behind an edit mode separates it from the one fact that gives it meaning,
which is the unit it authenticates to.

**It is masked, with a control to reveal it.** The first implementation rendered every host's
token in a plain text field, so a screen opened on a phone held outdoors showed every unit's
shared secret at once, to anybody beside the owner and to any screenshot. Masked by default
closes that; a reveal control is what keeps the field usable, because a token typed wrong is
otherwise indistinguishable from a token the unit rejects. The control is `data-action="reveal"`
and it acts on one field at a time. **The add-host form's token is masked on the same terms.**
It holds a secret being typed rather than one already saved, which changes how long it is on
screen and not who can see it, and a form where the same field is masked in one place and plain
in another teaches nobody anything except that the rule is decorative.

**One field is revealed at a time, across the whole screen.** Revealing a token re-masks whatever
was showing before it. Independent per-field state would allow two secrets to sit uncovered on
the same screen at once, which is the thing being prevented, and the screen holds every unit the
owner has.

**Quick-connect from history**, which §4.5.2 also names, is not built. What "history" means when
a saved host is already one tap from active is a decision rather than an omission, and it is
issue #177.

**An address is validated in the form** (§4.7), with the message next to the field and
`role="alert"` on it, and the mutator's throw is caught as the backstop it is meant to be.

**Diagnostics render what exists, and name what does not.** §4.5.2 asks for the last error,
the latency and the negotiated plugin version, and all three now render, alongside the connection
state and, when the state is `unauthorized`, its reason (§4.3.1). The first two were missing for
most of this project's life because `lib/ws.ts` had no surface for them, and the rule that kept
them missing rather than approximated still stands: a view must not manufacture one, because
labelling the authentication reason as "the last error" says nothing happened when a TLS failure
or a refused connection is exactly what did.
§4.3.10 builds the two that were missing: the client keeps the last error -- an `error` frame's
code and message, the code of a close the app did not ask for, or the drop it detected itself
when neither of those ever arrives -- with the time it happened, and
measures the round trip on the ping/pong pair it was already sending. Both reach this screen
through the `connection` store like the state does, and both are absent rather than zero when
there is nothing to report: no error yet, and no latency while the socket is down.

**The DOM hooks**, declared here for the reason §4.5.1.1 gives: a test that finds an element by
walking the markup breaks when the markup is rearranged, and this screen will be rearranged.

- `data-view="settings"` on the view root, as §4.5 requires of every view.
- `data-host-id` on each row of the list, carrying the host id, and `data-host-active="true"` on
  the row of the active host.
- `data-host-field` on the element carrying a host's value or the input editing it:
  `label`, `address`, `wsPort`, `httpPort`, `token`.
- `data-action` on every button: `activate`, `remove`, `edit`, `save`, `cancel`, `add`.
- `data-field-error="address"` on the validation message.
- `data-add-field` on the add-host form's inputs, over `label`, `address` and `token`. The form
  is not a host and must not wear `data-host-field`: overloading the two made a test filter for
  "the one address input outside any row", which is a test walking the markup by another name.
- `data-field` on each diagnostic value, reusing §4.5.1.1's convention and its `data-empty="true"`:
  `connectionState`, `pluginVersion`, `lastError`, `latency`, and `unauthorizedReason` on the row
  that appears only while the state is `unauthorized`. That last one is named here because the
  first implementation rendered the row without a hook, correctly refusing to invent one the list
  did not carry. It is also the one field that carries **no** `data-empty`: the row exists only
  when there is a reason, so the two can never both be true, and a branch that cannot be reached
  is worse than absent - it reads as a case somebody handled. `lastError` and `latency` do carry
  it, and carry it often: an app that has just started has neither.

**The address grammar has one expression, and the form asks for it.** §4.7 says to validate in
the form and keep the mutator's throw as the backstop that says the form forgot. That is right,
and it was first implemented by copying the grammar into the view - which makes the backstop
unreachable by construction, since the two agree on every input, and leaves two expressions of
one rule in two files with one of them outside the coverage gate. §4.5.1.1 already refused that
arrangement for the same reason, about a different rule. `lib/settings.ts` exports the predicate;
the form calls it. The `try`/`catch` stays, and stays unreachable, and that is now a property of
there being one grammar rather than a coincidence of two that happen to match.

This is also what the tests can say. A test cannot reach a backstop that nothing can trip, so
what it pins instead is that the form and the mutator refuse the same set - driven from one
table, so a value added to it is asserted on both sides at once.

**The copy lives in `lib/format.ts`, and the wording lives here.** The sentence shown for each
connection state, and the one shown for each unauthorized reason, are strings chosen by rule from
a value, which §4.5.1.1 settles: a view that formats is a view that computes, and §10.7 excuses
`src/views/` from the coverage gate on the grounds that a view is markup. `Settings.svelte`
subscribes and lays out what those functions return, the same way `Dashboard.svelte` does.

The wording is tabled below for the reason §4.5.1.1 gives about its own banner: a test cannot
assert equality against wording that lives nowhere, and a test that reads the string out of the
module it is testing pins nothing at all.

| state | sentence |
|---|---|
| `connecting` | Connecting |
| `connected` | Connected |
| `degraded` | Connected (data is stale) |
| `offline` | Offline |
| `unauthorized` | Unauthorized |
| `restarting` | Restarting |

| reason | sentence |
|---|---|
| `rejected` | The unit refused the stored token. |
| `required` | The unit requires a token and none is stored. |

**Those two sentences are also half of the Dashboard's banner**, which §4.5.1.1 pins as *The unit
refused the stored token. Fix it in Settings.* and *The unit requires a token and none is stored.
Add it in Settings.* One source, not two: the banner is this sentence followed by its call to
action, and the call to action is the second thing `lib/format.ts` returns for a reason. Typed
out twice they would drift on the first correction, and the correction most likely to be made is
to the half that tells somebody what to do.

**Any constant the library owns is imported, never restated.** The port defaults are the case
that arose: the view minted hosts from its own `8082` and `8443` while `lib/settings.ts` held the
same two numbers, so a change to the library's defaults would have left the screen quietly
creating hosts on the old ports, with `sanitizePort` repairing nothing because both values are in
range. It is the address grammar's mistake in a second place, found by the same review.

**The remote string on this screen is the plugin version**, and §4.5.3 governs it like any other.
The unauthorized reason is not one: `lib/ws.ts` mints it locally from a close code, so what
arrives from the unit is the code and the reason is one of two values this codebase chose. An
earlier draft of this section called both of them remote, and the test author who read it duly
cast a script tag into `UnauthorizedReason` to defend a path the client cannot produce. A
defence against something that cannot happen is not free: it reads as evidence that it can.

#### 4.5.2.2 Controls: confirm, pending and where a refusal appears (issue #184)

The four state commands (§2.6) have been implemented in the plugin, and sendable by `lib/ws.ts`,
since before any of them had a button. This section is the shape of the buttons, written before
they exist for the reason §4.5.2.1 gives about the screen that came before it.

**The controls sit at the end of the Dashboard, under the readings.** The card answers "is my
unit all right"; the controls are what an owner reaches for after reading the answer, and three
of the four restart or stop the unit. A shutdown button above the battery row is the most
destructive control on the screen placed where the thumb already rests.

**Each control is one button, and the label names the outcome rather than the state.** *Switch to
MANU*, not a toggle reading *AUTO*. A toggle that shows the current state has to be read twice --
once for what it says and once for what tapping it will do -- and the second reading is the one
made in a hurry.

**Which mode the switch offers is a rule, so it is not in the view** (§4.5.1.1): `MANUAL` offers
AUTO, `AUTO` and `PASV` both offer MANU, and **a null mode offers AUTO**. §4.5.2 already fixed
that last one and gives the reason: a null mode is a unit with no agent, which is a unit in manual
mode (§2.6.0), and this switch is what takes it back. The button is enabled there like anywhere
else.

**Two vocabularies meet on this button and a third is nearby.** `set_mode` carries lowercase
`auto` / `manual`; `stats.mode` reports `AUTO | PASV | MANUAL` or null; the badge writes `MANUAL`
as **MANU** (§4.5.1.1); and `pwnagotchi.restart()`'s own `"MANU"` (§2.6.1) never reaches the
client at all. The request value and the displayed value are produced by different functions and
neither is derived from the other.

##### The PASV control

**It is rendered only when `capabilities.pasv`**, per §4.5.2, and it is **disabled when the mode
is not AUTO or PASV**, which is §4.3.5's "a missing plugin hides the control, the wrong mode greys
it out" and §2.6.2's precedence read from the client side. A null mode is not auto, so the control
is disabled there -- the opposite of the mode switch above, and for the opposite reason: entering
PASV on a unit with no agent is not a rescue, it is a request the unit will refuse.

**A disabled control says why, and it says it in the same words the unit would have used.** The
sentence beside it is the one `pasv_requires_auto` gets in the table below, from one place, so the
client-side rule and the server-side refusal cannot drift into two different explanations of one
condition.

**Both refusals are still handled, and neither is an unreachable branch.** `capabilities` and `mode`
both arrive inside `stats`, so they are as old as the last broadcast -- up to `keepalive_interval`
(§2.4) -- and the mode can change under the app: the unit's own display switches it, and so does a
second client. Tapping a control the app believed was available, on a unit that has since left
AUTO, is a race rather than an impossibility, and §4.5.2.1's warning applies in reverse here: this
defence is against something that can happen, and a test reaches it by answering a tap with the
error rather than by contriving the state. `pasv_unavailable` is the same race one step further
out -- the last broadcast said the plugin was loaded and it has since been unloaded -- and is
rarer rather than different in kind, which is why the control shows it too instead of treating a
capability it was told about as a capability that cannot change.

##### The two-step confirm is inline, not a dialog

D9 and D12 require the confirm; neither says what it looks like, and issue #37's criteria were
written against "the pattern already used by the other confirmations" at a time when there were
none. The pattern is settled here, and #37 is amended rather than contradicted.

**Tapping a control replaces it with its own confirmation**: a confirm button and a cancel button
in the space the control occupied, with the consequence written beside them. A modal was
considered and rejected on three counts. The question belongs next to the thing that asked it,
and a dialog moves it. A dialog has to trap focus, restore it, close on Escape, close on a
backdrop tap and close on the state change that makes the question moot -- five things to get
right where the inline form has one, and a focus trap that is subtly wrong is worse than no
dialog. And §4.2.1's smallest supported viewport is 320x568, which is the layout with the least
room to put a box over.

**One control is armed at a time.** Arming a second disarms the first, the same exclusivity rule
§4.5.2.1 applies to a revealed token and for a related reason: two live questions on one screen
invite an answer to the wrong one.

**Arming does not expire.** A timer was considered and rejected: an armed control is not a hair
trigger, it is a visible question with the word *Confirm* on it, and it is already cleared
whenever the control stops being available. Recorded so it is not re-litigated.

**Focus moves to Cancel, not to Confirm.** The button that was tapped no longer exists, so focus
has to go somewhere, and a repeat tap or a held Enter landing on Confirm would defeat the two-step
in exactly the case the two-step is for. A keyboard user tabs once to reach Confirm; that friction
is the feature.

##### Nothing is applied optimistically, and nothing waits on a clock

§4.3.4 forbids flipping the badge on tap and requires the control to show the request as pending
until `stats` confirms it. **Every rule below is expressed in observations that already flow
through the stores, not in milliseconds.** `lib/controls.ts` owns no timer. A bound in
milliseconds would be a second opinion about how long a unit takes to come back, beside the
patience `lib/ws.ts` already spends on the same restart (§4.3.1), and the two would disagree the
first time either was tuned: a control that gives up while the socket is still waiting tells the
owner it failed about a unit that is on its way back.

| request | pending ends when |
|---|---|
| `set_mode`, either value | the connection state becomes `restarting` |
| `set_pasv` `on` | `stats.mode` is `PASV` |
| `set_pasv` `off` | `stats.mode` is `AUTO` |
| `reboot` | the connection state becomes `restarting` |
| `shutdown` | the connection state becomes `restarting` |

**Every row that waits on `restarting` waits on the state and not on its reason**, and the app has
no choice about that: `restarting` carries a `reason` (§2.6.1) and `ConnectionView` does not expose
it, which is issue #131. So a restart the app did not ask for -- started from the unit's own
display, or by a second client -- resolves whichever request was pending as though it had been the
cause. Recorded rather than left to be discovered, because the day #131 lands this rule can be
tightened and nothing else here changes.

**The `set_mode` row claims only that the restart began, in both directions, and that is the
strongest honest claim available.** A unit in manual mode never hands the plugin an agent, so
`on_ready` never fires and `stats.mode` is **null**, not `MANUAL` (§2.5, §2.6.0, F31): nothing
the unit sends says "the switch to manual succeeded".

The other direction looks confirmable and is not. An earlier draft of this table had the `auto`
row wait for `stats.mode` to become `AUTO` or `PASV`, on the reasoning that PASV *is* auto with a
plugin on top (§2.5) and so a unit answering either had switched. That reasoning is sound and the
row was still wrong, because of what comes **before** that answer: after the restart the socket
returns and the mode is null for the whole of §2.6.0's startup window, which that section is at
pains to say is **not** a fixed few seconds -- "it lasts until bettercap is answering and monitor
mode has come up, and on a unit where either is struggling it lasts as long as that does". The
abandonment rule below would have fired inside that window and reported *The unit did not confirm
the change* over a rescue that had in fact worked, on a unit that was still booting. That is
issue #147's defect in the client: the null-mode rule three paragraphs above exists so the app
can rescue a unit with no agent, and this would have told the owner the rescue failed.

So null is ambiguous in both directions and stays ambiguous for an unbounded time, which means
**no pending rule for a mode change can be built on `stats.mode` at all**. What is observable is
that the restart began. The badge says the rest, in its own time and with §4.5.1.1's dash while
it does not know -- which is the division of labour that section already describes, and the
reason the badge is rendered from `stats` rather than from the tap.

**Two more things end a pending request**, and between them nothing can leave a control pending
for ever:

- **The command fails.** `command()` rejects -- refused before it was sent, timed out, or answered
  with an `error` -- and the sentence for that failure appears beside the control.
- **Two `stats` frames arrive without the confirmation.** Two, not one: the first may have been
  assembled before the command was handled, since the ticker (§4.3.7) shares nothing with the
  command path. The second was not. For the three rows that wait on `restarting`, this is the
  backstop for a restart that never began; for the two `set_pasv` rows it is the unit having
  ignored the request. It is bounded in every case because it counts frames on a connection that
  is still up: a request whose restart *did* begin has already resolved by the time the socket
  drops.

**At most one request is pending at a time**, and the other controls are disabled while one is.
Three of the four restart the unit, and there is no honest way to render two pending requests that
contradict each other -- PASV being entered while the unit is on its way to MANU. The rule is
cheaper than the interface that would be needed to permit it.

##### Before the first `stats` frame

The rules above already decide this and it is written down because it was asked. `capabilities`
arrives inside `stats` (§4.4.1), so before the first frame there are none, and **the PASV control
is not rendered**: the rule is `capabilities.pasv`, and absent is not true. The mode switch is
rendered and offers AUTO, because a null mode does. Reboot and shutdown are rendered.

All four are disabled, not by a rule of their own but by the one they already follow: nothing has
been received, so the connection is `connecting`, and `canSendCommand` is false there. **The PASV
control appearing when the first frame lands is correct and is not a flicker to design around.**
Rendering it hidden-then-shown is the honest sequence: the app did not know whether the unit had
the plugin, and now it does.

##### A refusal appears next to the control that caused it

§4.3.5 settles the principle and this is where it lands. `lib/ws.ts` today rejects an in-flight
command with `new Error(message.data.message)`, which keeps the human sentence and drops the
`code`, so no caller can tell `pasv_requires_auto` from `internal_error`. **The rejection carries
the code**, as a distinct error type, for reads as well as commands: it is one code path and
splitting it would be two rules to keep in step for no gain.

**One sentence, two ways to reach it.** The first row above is a disjunction and not a
conjunction: the client-side rule and the unit's refusal are two paths to one string, and the
refusal arrives precisely when the app believed the control was enabled, which is the race two
paragraphs above. Written as "disabled *and* refused" it would describe a state that never occurs.

**Both PASV sentences are scoped to the PASV control**, and the scoping is the point rather than
tidiness. The code is chosen by whatever answered, so a `reboot` answered with `pasv_unavailable`
would otherwise put *The unit does not have the PASV plugin* beside the Reboot button -- a
sentence that is true of nothing the owner just did, offered as the explanation of why their
reboot failed. A sentence only explains the control it is about.

**The code is remote-chosen and is never rendered.** It selects one of the sentences below, and
anything unrecognised selects the last of them (§4.5.3): `error.json`'s `code` is not validated at
the boundary (issue #109), so a unit -- or something in front of one -- can put any string there.

§4.3.5's table sends an unrecognised code to the Settings diagnostics line, and that line does not
exist (issue #176). Until it does, an unrecognised code shows beside the control as well, which is
strictly better than nowhere and does not contradict the rule the table states. **It does not
discharge #176**, and the reason is not that this sentence is short-lived -- an earlier draft said
so and was wrong about its own design: the message survives every re-render and is cleared only by
arming that control again or by the unit changing. The reason is that it is in the wrong place. An
error worth diagnosing is one somebody goes looking for **after** the screen it happened on, and
this one can only be read by whoever is still standing in front of the control that produced it.

**A message outlives the control being disabled.** Going offline does not erase what the unit
already said, so a failure or an abandonment stays on screen through a drop and a reconnect. What
does take precedence over it, while it applies, is the static disabled reason -- and there is
exactly one of those.

**That reason is shown only while the app could send the command.** PASV's sentence is a claim
about the unit's current mode, and while the socket is down the mode is whatever the last
broadcast said, which is not the same fact. A control disabled for connectivity says nothing of
its own (above); it must not say something of the unit's either, on a reading it can no longer
refresh.

**A control disabled because the app is not connected says nothing of its own.** The banner
(§4.5.1.1) is already saying it, in a sentence with more to offer than a repeat under each of four
buttons.

**Whether a command may be sent at all is `lib/ws.ts`'s rule and is asked, not restated.**
`command()` refuses outside `connected` and `degraded` (§4.3.3), and a button that decided its own
enabled state from a copy of that list would be §4.5.2.1's duplicated address grammar in a third
place -- with the refusal that should have caught the drift reachable only as a rejection the user
sees. The predicate is exported from `lib/ws.ts` and used by both.

##### The copy

Tabled here for §4.5.1.1's reason: a test cannot assert equality against wording that lives
nowhere, and one that reads the string out of the module under test pins nothing. The strings
live in `lib/format.ts`.

| control | label |
|---|---|
| mode, offering auto | Switch to AUTO |
| mode, offering manual | Switch to MANU |
| PASV, off | Enter PASV |
| PASV, on | Leave PASV |
| reboot | Reboot |
| shutdown | Shut down |

| request | confirm button | consequence |
|---|---|---|
| `set_mode` `auto` | Confirm switch to AUTO | Restarts the unit's services. It comes back in 20 to 60 seconds. |
| `set_mode` `manual` | Confirm switch to MANU | Restarts the unit's services. It comes back in 20 to 60 seconds. |
| `set_pasv` `on` | Confirm entering PASV | The unit stops attacking and only listens. |
| `set_pasv` `off` | Confirm leaving PASV | The unit returns to AUTO and resumes attacking. |
| `reboot` | Confirm reboot | The unit reboots. It comes back in a minute or two. |
| `shutdown` | Confirm shutdown | The unit powers off. It cannot be turned back on from here. |

The cancel button is **Cancel** in every case.

| request | pending |
|---|---|
| `set_mode`, either value | Restarting. |
| `set_pasv`, either value | Waiting for the unit to confirm. |
| `reboot` | Rebooting. |
| `shutdown` | Shutting down. |

| condition | sentence |
|---|---|
| the PASV control disabled for the mode, **or** code `pasv_requires_auto` on it | PASV is reachable only from AUTO. |
| code `pasv_unavailable`, on the PASV control | The unit does not have the PASV plugin. |
| any other code, or a failure carrying none | The unit did not accept the command. |
| two `stats` without the confirmation | The unit did not confirm the change. |

The shutdown consequence says *It cannot be turned back on from here* rather than naming the
button on the unit: which button that is depends on what the unit is built from, and the reference
hardware's PiSugar 3 is not the only answer. What is true of every unit is that this app is not
the way back.

##### The DOM hooks

Declared here, per §4.5.1.1, because this block will be rearranged and a test that finds a button
by walking the markup will break when it is.

- `data-control` on each control's root, over `mode`, `pasv`, `reboot`, `shutdown`.
- `data-control-state` on the same root, over `idle`, `armed`, `pending`.
- `data-action` on each button, over `request`, `confirm`, `cancel`.
- `data-control-message` on the one element per control that carries a sentence -- the disabled
  reason, the failure, the abandonment, the pending sentence, and the consequence shown while the
  control is armed -- with `role="alert"` on it, as §4.5.2.1 does for its validation message.
  **One element, not five.** Which kind of sentence it is carrying is what `data-control-state`
  already says, and a second hook would only exist so that a test could ask the question the
  state attribute answers. An earlier draft of this list named the other four and forgot the
  consequence, and both the implementation and the tests independently read the list as closed
  and left that sentence with no hook at all -- so the test that had to assert it went back to
  reading the control's whole `textContent`, which is §4.5.1.1's walking-the-markup by another
  name. The list is closed; this is the list.
- **No data hook for disabled.** The `disabled` attribute is what the browser acts on, and a test
  should assert the thing that has the effect rather than a second attribute that agrees with it.


#### 4.5.2.3 Wi-Fi: two segments, and the data behind them (issue #187)

§4.5 settles what the segmented control is and §4.5.2 settles what the two lists hold. This
section is the rest: where the data comes from, what an empty list means, and the hooks.

**A null `total` is not a count, and the truncation notice is not shown for one (issue #153).**
§2.7 makes `handshakes_list.total` nullable to distinguish a directory the plugin was never told
about from one it found and read. The notice exists to say how many entries were left out of a
capped list, which is a subtraction, and there is nothing to subtract from when the total is
unknown. The two cannot occur together today -- a list that was truncated was a list that was
enumerated, so `truncated` cannot be true while `total` is null -- and the rule is written here
anyway, because "cannot happen" held by two independent facts is exactly the pairing that stops
holding when one of them changes.

##### The segment lives in `src/shell/`, not in the view

`Segmented.svelte` is shell furniture, beside `Nav.svelte`. The reason is not reuse -- one view
has segments today -- it is §10.7: `src/views/` is excused from the coverage gate on the grounds
that a view is markup, and roving tabindex, Arrow/Home/End and automatic activation are not
markup. §10.7 already made this argument once, for the shell's own navigation, and the segment is
the same kind of thing in a smaller box. A tablist implemented inside `WiFi.svelte` would be
keyboard logic sitting outside every gate the project has.

**Arrow movement wraps.** §4.5 does not say, and with two tabs the question is reachable on the
first keypress: ArrowRight on the last tab selects the first. Wrapping is what the pattern's
usual implementations do, and on a two-tab list the alternative is an arrow key that does nothing
half the time, which reads as a broken control rather than as a boundary.

Its behaviour is otherwise §4.5's, restated nowhere: a real `tablist`, roving tabindex, Arrow/Home/End,
**automatic activation** so selection follows focus, each tab owning a `role="tabpanel"` labelled
back by `aria-labelledby`, both panels mounted so an expanded row survives a switch, and the
inactive panel carrying `hidden`. `Nearby` is selected on first launch, the choice is kept while
the app is open, and it is not persisted across launches or written to the URL (§4.5).

##### Somebody has to ask, and today nobody does

`access_points` is pushed once, in `initial_burst()`, and never again unless requested.
`get_handshakes` is sent only by `refreshHandshakes()` (§4.4.1), which fires on a `handshake`
push, so between connecting and the unit's next capture the Captured list is empty. Both lists
would therefore open stale or empty, and **that failure is invisible**: an empty list looks
exactly like an empty list.

**The refresh is asked for by `lib/`, on three occasions, and by no timer.** The view **becoming
the current view** asks, a control on the view asks, and **the active host changing under a view
that is already current** asks.

The third was found by implementing the first, and it is a different fact rather than a special
case of it: switching unit is done from Settings (§4.5.1) and the owner then navigates to Wi-Fi, so
the route does change -- but they may equally switch and come back to a Wi-Fi view that never
stopped being current, and §4.5 keeps it mounted throughout. What is being asked is not "has the
route changed" but "is this list the current unit's", and the honest expression of that is the
identity of the client the last ask was made for. A boolean saying an ask has happened is the
shape `lib/stores.ts` already rejects for its own refresh owner, in a comment that gives the same
reason: a host switch can leave A's work standing where B's belongs.

**Becoming current, not mounting**, and the difference is not pedantry. §4.5 keeps every view
mounted when it is navigated away from, deliberately, so that the Map keeps its viewport and the
Log its buffer. A refresh on mount therefore fires once per app launch: an owner who looks at
Wi-Fi at ten and again at eleven reads an hour-old list, and nothing on the screen says so. The
trigger is the route becoming `/wifi`, which is a fact `lib/router.ts` already holds.

**Being connected is not a precondition, and there is still no third occasion.** An earlier
draft of this paragraph said a request sent while the socket is down is refused at the point of
sending. That is what happens to a **state command** (§4.3.3, §4.5.2.2); a **read** is queued and
flushed on reconnect, which is the distinction §4.3.3 draws and which this section had collapsed.
The correction matters because the two lead to different code: a gate on the connection state here
would be a third expression of a rule `lib/ws.ts` already owns, and the wrong one.

So the view asks whenever it becomes current, connected or not, and the queue decides what that
means. What it does **not** amount to is a reconnect trigger: a queued read carries §4.3.8's
15 second timeout from the moment it was asked for, not from the moment it is sent, so an outage
longer than that ends in a rejection the refresh swallows rather than in a list that fills when the
tether returns. The answer to a longer outage is the refresh control the screen already has.

A trigger on reconnect is still refused, for the reason it always was: it would put a fetch on
every reconnect nobody asked for, including the one that follows a mode change, when the unit has
more urgent things to answer. §4.3.7 already rejected client polling for `stats`, and the
argument is stronger here: an access-point list re-fetched on a schedule spends round trips on a
link whose round trips are the expensive part, for a phone that is usually in a pocket. A list
that ages is a list with a visible refresh, not a list that refreshes itself.

**Both lists are fetched together, not the visible one.** Both panels stay mounted and both
badges carry a count, so a segment nobody is looking at is still on screen.

**The request is coalesced per client**, exactly as `refreshHandshakes()` already is and for the
reason its comment gives: a host switch (§4.4.2) can leave unit A's request in flight when B
attaches, and the owner is compared against the requesting client so B sends its own rather than
coalescing into a reply that will never reach B's stores.

**A view calling a `lib/` function when it mounts is not a view that computes** (§4.5.1.1). The
rule is about turning values into strings and deciding which string; asking the layer that owns
the socket to go and ask is neither. It is named here because it is the first time a view
initiates anything.

##### An empty list and a list nobody has fetched are different, and must read differently

`accessPoints` initialises to `[]` and `handshakes` to an empty payload, which is also what a unit
that sees nothing reports. §4.5.1.1 says to say plainly what is not known, and a screen that says
*No access points* about a unit it has never spoken to is saying something it does not know.

The two are told apart **by the connection state**, not by a third store field: while the app is
not `connected` or `degraded`, an empty list has not been fetched.

**There are two windows where this is wrong, and both are accepted rather than papered over.**
The first is at connect: `stats` has arrived and `access_points` has not. Both leave the unit in
the same burst, so it is one frame wide. The second is a host switch, and it is wider: `rebuild()`
empties the stores and attaches the new client, the burst carries `access_points` but **not**
`handshakes_list` (§2.4), so Captured reads *The unit has no captures.* for one round trip about a
unit that has been asked and has not yet answered.

Closing either honestly would mean a per-list "have we ever received one" flag in `lib/stores.ts`,
which is a second source of truth for something the connection state answers the rest of the time,
and a fourth sentence for a state that lasts a round trip. The cost of being wrong here is a
sentence that corrects itself when the reply lands; the cost of the flag is a second thing that can
disagree with the first. This is recorded because it has now been noticed twice from opposite
directions, and a known window that nobody wrote down gets rediscovered as a bug.

##### Order

**Nearby is sorted by the client, Captured is not.**

Nearby sorts by RSSI, strongest first, because *what is near me* is the question the segment
answers, with a control to sort by channel, lowest first, instead. Both orders are **total**: the
tie-break is the BSSID, ascending, so two access points reporting the same RSSI hold their places
between frames instead of swapping every twenty seconds. The directions are written down because
neither is derivable and a test cannot assert an order nobody chose. The BSSID is attacker-chosen like everything else here, and
using it to order rows is not using it as truth.

Captured arrives newest first and is rendered in the order it arrived. The client must **not**
re-sort it, and the reason is the cap: the plugin applies the 500-entry limit to its own ordering
(§2.8), so the list is the 500 newest. Re-sorting client-side would present exactly that set in
an order that hides what it is a set of -- an alphabetical list of 500 captures that silently
omits the older ones.

##### What a row holds

Every string below is chosen by somebody else and is rendered as text with no element created
(§4.5.3): the SSID, the hostname, the vendor and the encryption all come from the air.

- **Nearby**: hostname, BSSID, channel, RSSI, encryption, vendor, client count (§4.5.2).
  `access_points.json` says `hostname` falls back to the MAC when empty, so an empty one is the
  plugin failing to keep a guarantee rather than a normal reading. It still renders as §4.5.1.1
  requires of any unknown remote string, a dash with its accessible label, because a presentation
  rule for a broken guarantee is not the same thing as a second implementation of the guarantee.
- **Captured**: SSID, BSSID, time, size, and a GPS pin when the sidecar carried one. `bssid` is
  null when the filename did not parse (§2.7) and is never guessed, so it dashes. The time is
  `formatUnitTime`, which already exists and already decides how much of a date to show.

**Every remote string on a row follows §4.5.1.1**, which means `vendor` and `encryption` dash
with their accessible label when they arrive empty, exactly as `hostname` does. The numbers do
not: `channel`, `rssi` and `clients` are non-nullable by schema, so a dash rule for them would be
a branch nothing can reach, which §4.5.2.1 already refused for being worse than absent.

**The GPS pin says whether there is one, and not where.** The row renders `Pinned` or a dash, and
never the coordinates. The Map view owns plotting a position (§4.5.2), a latitude and a longitude
in a list row on a 320px screen are unreadable, and printing two numbers beside a capture invites
the reader to compare them against numbers they cannot see. What this list answers is which
captures will appear on the map.

**A size needs a formatter and the wording is fixed here**, because a test cannot assert equality
against wording that lives nowhere (§4.5.1.1): bytes below 1024 render as `<n> B`, then `<n.n> kB`
and `<n.n> MB`, each to one decimal place, powers of 1024, with the unit spelled as shown.

**The unit is chosen after rounding, not before**, and the boundary is named because the two
orders disagree there. 1 048 575 bytes is 1023.999 kB, which rounds to `1024.0 kB` -- a reading
the table above does not contain, since 1024 kB is what the next unit is for. Choosing the unit
from the unrounded value produces exactly that, so the rounding happens first and the answer is
`1.0 MB`. Both orders round; only one of them prints a number the thresholds say cannot occur. A
capture is a file on a disk and its size is read by somebody deciding whether it is worth pulling
over Bluetooth, so the unit is always shown and never implied.

##### The badges, the notice, and the number that disagrees

§4.5.2 already settles all three and they are not restated: each badge shows the length of the
list it names and renders `500+` when `truncated`; the Captured segment shows the truncation
notice when the cap was hit; and the badge is **not required to agree with the Dashboard's
counters**, for the reasons §4.5.2 gives at length. The wording of the notice is tabled below
with the rest of the copy, for §4.5.1.1's reason.

##### The DOM hooks

Declared here, per §4.5.1.1, and closed: this is the list.

- `data-view="wifi"` on the view root, as §4.5 requires of every view.
- `data-segment` on each tab, over `nearby` and `captured`, and `data-segment-panel` on each
  panel, over the same two values.
- `data-segment-badge` on the badge inside each tab. Its text is the count and nothing else: the
  bare number, or `500+`. What the badge counts is said by the tab it sits in.
- `data-row` on each row of either list, carrying `nearby` or `captured`, and `data-row-key`
  carrying the BSSID for a Nearby row and the filename for a Captured one. The filename is the
  one field §2.7 guarantees unique; the SSID is not, and two captures of one network are two
  rows.
- `data-field` on each value inside a row, reusing §4.5.1.1's convention and its
  `data-empty="true"`: `hostname`, `bssid`, `channel`, `rssi`, `encryption`, `vendor`, `clients`
  on a Nearby row; `ssid`, `bssid`, `time`, `size`, `gps` on a Captured one.
- `data-action` on the two controls, over `refresh` and `sort`. **Refresh sits above the tablist**,
  because it fetches both lists whichever segment is showing, and a control that acts on something
  off-screen must not sit inside the panel that is on it. **Sort sits inside the Nearby panel**,
  because it orders that list and there is nothing to order in the other one.
- `data-empty-message` on the element that carries the empty or not-yet-fetched sentence, one per
  panel, with `role="status"` on it. It is absent when the list has rows.
- `data-truncation-notice` on the Captured notice, absent when `truncated` is false.

##### The copy

| condition | sentence |
|---|---|
| Nearby empty, connected | The unit reports no access points. |
| Nearby empty, not connected | Not connected, so this list has not been read. |
| Captured empty, connected | The unit has no captures. |
| Captured empty, not connected | Not connected, so this list has not been read. |
| Captured truncated | Showing the newest 500 captures of <total>. |
| a capture with a sidecar position | Pinned |

| control | label |
|---|---|
| refresh | Refresh |
| sort, currently by RSSI | Sort by channel |
| sort, currently by channel | Sort by signal |

The sort control names what tapping it will do rather than the order in force, which is §4.5.2.2's
rule for the mode switch and holds for the same reason: a label that states the current state has
to be read twice.


#### 4.5.2.4 Peers, and the refresh rule stops being the Wi-Fi view's (issue #190)

§4.5.2 says what this screen holds: pwngrid peers with signal, last-seen and pwnd counters. What
follows is the rest, and most of it is §4.5.2.3's, which is the point.

##### The refresh rule now has two callers, so it has one expression

§4.5.2.3 settled when a list is asked for: the route becoming the view's own, a control on the
view, and the active host changing under a view that is already current -- three occasions and no
timer. Peers wants exactly that, and `initial_burst()` does not carry peers at all (§2.4), so
without it the screen opens empty and stays empty until another unit walks past. That is
§4.5.2.3's invisible failure with a longer fuse.

**It is shared, not copied.** `lib/stores.ts` already holds two near-identical coalescing owners
and a third by copy is how the first correction to any of them fails to reach the other two. The
same goes for the view-side trigger, which is a rule about routes and client identity and not
markup: two views deriving it separately is §4.5.1.1's objection in a new place. One expression,
asked twice.

This is the first time a rule in this specification has been written for one screen and then
claimed by a second, so the direction is stated: **§4.5.2.3 keeps the reasoning and this section
keeps only what differs.** A reader looking for why there is no timer, or why being connected is
not a precondition, goes there and finds it argued once.

##### The list is not keyed on anything a peer chooses

`lib/stores.ts` upserts a peer by `fingerprint` and its own comment explains that `'???'` is
`peer.identity()`'s documented default for a unit that has not advertised one -- so the store
**deliberately** produces a list in which several rows share that value. Keying a Svelte `{#each}`
on it throws `each_key_duplicate` in production and takes the screen down, which is the defect
issue #187 hit on `AccessPoint.bssid`.

The difference is worth naming rather than treating this as the same bug twice. There, the
duplicate needed either a hostile unit or a bettercap entry with no mac. Here **the duplicate is
the documented default**, produced by the unit's own library on any peer that has not advertised
an identity, and the store's comment says as much. A key must be positional, and the identity
fields are rendered rather than relied on.

##### What a row holds

Signal, last seen, and the two pwnd counters, which is what §4.5.2 asks for, beside the identity
the peer advertises. Every one of `name`, `fullName`, `fingerprint`, `face` and `version` arrives
over pwngrid from another person's unit, which §4.5.3 names specifically: this is the first screen
on which **every** text field was chosen by a stranger, rather than by a network that happened to
be in range.

`rssi`, `channel`, `lastSeen`, `encounters`, `pwndRun` and `pwndTotal` are all nullable on the
wire, so §4.5.1.1's dash applies to each, and `name` and `fingerprint` carry `'???'` rather than
null when unadvertised. **`'???'` is rendered, not translated into a dash.** It is what the peer's
own unit reports and it means something different from a value the plugin could not read: the dash
says this app does not know, and `'???'` says the peer did not say. Collapsing them would make a
peer that is deliberately anonymous indistinguishable from a field the app failed to fetch.

**`face` and `version` dash when null, and that is not inconsistent with the paragraph above.**
They carry the same meaning `'???'` carries for a name -- the peer did not advertise one -- and
they carry it as `null`, because pwngrid's own library supplies a sentinel string for two fields
and nothing at all for the others. So on those two the app genuinely cannot tell *the peer did not
say* from *we could not read it*, the wire having given it one value for both, and §4.5.1.1's
answer for a value the app does not know is the dash. The asymmetry is the library's rather than
this screen's, and it is written down here so it does not read as an oversight.

`lastSeen` is `formatUnitTime` (§4.5.1.1), which is not written a second time here. It is **not**
composed with the name into one string: this screen has a column for each. The Dashboard's
single-line summary of a last peer had to carry both facts in one row and once did compose them,
through a `formatNamedEvent` that §4.5.3 has since removed -- a composed line cannot isolate the
remote half of itself, so that row now builds its two pieces in the markup. Named here because
the two shapes are easy to confuse and the hook list below settles it.

##### Order

**Sorted by last seen, most recent first**, with the signal as the tie-break, strongest first, and
the fingerprint after that, ascending, so the order is total in the sense §4.5.2.3 requires -- rows
that do not swap between frames. The direction of that last one is stated for §4.5.2.3's reason:
a test cannot assert an order nobody chose, and an earlier draft of this paragraph left it out. Last seen leads rather than signal because a peer list answers *who has been
around*, and a peer met an hour ago at a strong signal is further from that question than one met a
minute ago at a weak one. There is no sort control: §4.5.2.3 gave Nearby one because *what is near
me* and *what is on which channel* are two questions about one list, and this screen has only one.

A null `lastSeen` sorts last, and a null `rssi` sorts after a known one within its group. Neither
is a judgement about the peer; it is what keeps the order total when the wire is missing a field.

##### The DOM hooks

Declared here, per §4.5.1.1, and closed.

- `data-view="peers"` on the view root, as §4.5 requires of every view.
- `data-row="peer"` on each row, with `data-row-key` carrying the fingerprint -- **rendered as a
  hook, never used as the `{#each}` key**, for the reason above.
- `data-field` on each value in a row, with §4.5.1.1's `data-empty="true"`: `name`, `fullName`,
  `fingerprint`, `face`, `version`, `rssi`, `channel`, `lastSeen`, `encounters`, `pwndRun`,
  `pwndTotal`.
- `data-action="refresh"` on the refresh control.
- `data-empty-message` on the element carrying the empty or not-yet-fetched sentence, with
  `role="status"`, absent when the list has rows.

##### The copy

| condition | sentence |
|---|---|
| empty, connected | The unit has met no peers. |
| empty, not connected | Not connected, so this list has not been read. |

The second is §4.5.2.3's sentence, unchanged and shared: it says nothing about which list it is
under, because the reason it appears has nothing to do with the list.


#### 4.5.2.5 The Log, and the one timer this client is allowed (issue #193)

§4.5.2 asks for a live tail with a text filter and an auto-scroll toggle, and §4.5.1 adds a
font-size control and a level filter. Three of those five are buildable today and this section
says what happens to the other two.

##### There is no log push, so "live" costs a timer

`log_lines` exists only as a reply to `get_log` (§2.9), and §4.4.1 has the store **replace**
rather than append, because the plugin owns the tail and the clamp and a second buffer in the app
would diverge from it. So a tail that follows means the client asking again on a schedule, which
§4.5.2.3 forbids every other view and which §4.3.7 argued against for `stats`.

**It is allowed here, once, and the exception is argued rather than assumed.** §4.3.7's case
against polling was that the unit already broadcasts what the client needs, so a poll pays a round
trip for something arriving anyway. That does not hold here: nothing arrives anyway. §4.5.2.3's
case was that a list re-fetched on a schedule spends round trips on a link whose round trips are
expensive, for a phone in a pocket. That half still holds, and it is what shapes the exception
rather than removing it.

**The follow is opt-in, off on arrival, and stops the moment the view is not current.** An owner
who opens the Log to read what already happened pays nothing. An owner watching a unit misbehave
turns it on, and can see that they did.

**Turning it on asks immediately, and then every ten seconds.** Waiting out the first interval
before anything happens is a control that appears not to work, and the owner turning it on is
asking to see what is happening *now* rather than what will be happening shortly.

**The toggle survives navigating away and back; only the asking stops.** §4.5 keeps every view
mounted, and this control is no different from the Wi-Fi segment in that respect: an owner who
checks the Dashboard and comes back finds Follow as they left it, and the timer resumes with the
view. Resetting it silently would leave somebody watching a log that stopped updating for a reason
nothing on the screen gives.

**This is the whole of the exception.** Nothing else in this client may hold a timer, and a second
one arriving here later is a specification question rather than a local decision -- §4.5.2.3 says
so for every other view, and the argument that carved this one out is specific to there being no
push.

**The interval is 10 seconds.** The reasoning is a bracket rather than a preference: the plugin's
own ticker runs at `keepalive_interval`, 20 s by default (§2.4), so the Dashboard beside it is
already up to 20 s behind the unit; a log that followed more slowly than that would be visibly
behind a screen the owner can compare it against. Below about 5 s the round trips cost more than
the freshness is worth on this link. Ten sits between, and is written down here so it is a
decision rather than a number somebody typed.

**Each ask carries no `lines` value**, so the plugin's own default and clamp decide the window
(§2.9). The client does not restate 200: the clamp lives in one place, and a client that names a
number is a second opinion about how much log is worth sending.

##### The three occasions are §4.5.2.3's, adopted rather than restated

The route becoming `/log`, the refresh control, and the active host changing under a view that is
already current, exactly as §4.5.2.4 adopted them for Peers. §4.5.2.3 keeps the reasoning; the
follow timer above is an addition to that list on this screen only, not a replacement for it.

##### The filter filters what is on the screen, not what is asked for

The text filter is applied to the buffer the app already holds, and never to the request. The
plugin owns the tail, so filtering at the source would mean the last 200 *matching* lines, which
is a different and much more expensive question, and it would make the buffer's meaning depend on
a control the owner is still typing into. Matching is a plain case-insensitive substring: the
field is a filter and not a query language, and a regular expression typed into a phone in the
field is a way to hide lines by accident.

##### The level filter is not built, and the reason is a rule rather than a shortage of time

§4.5.1 asks for one. A level filter has to find the level inside a line, and **what a line looks
like is not a pinned fact**: §2.9 returns opaque strings, §11 has no entry for the format, and
§11.1 forbids using what is not pinned. Matching `[INFO]`, `INFO:` or any other shape would be a
guess about somebody else's logging configuration, shipped as a feature and wrong on the first
unit that configured its own.

What unblocks it is the mechanism §11 already has, a **dated observation** of real lines from a
unit, and that is issue #194. This is recorded here rather than left as an omission because the
next person to read §4.5.1 will otherwise assume the filter was forgotten.

##### Following and scrolling are one control

§4.5.2 names an auto-scroll toggle and a live tail as two things. They are one: auto-scroll with
nothing arriving scrolls to the same place it already is, and following without scrolling puts new
lines below the fold of a view whose whole purpose is watching. So there is one toggle, it is
called Follow, and it does both -- re-asking while the view is current, and keeping the newest
line in sight. Turning it off leaves the buffer where it is rather than jumping.

**Keeping the newest line in sight means scrolling the container to its end after every buffer
change, and only while following.** Not on every render, which would fight an owner scrolling
back through what they came to read, and not once on the tap, which would drift out of date on
the next interval. A buffer that replaces rather than appends (§4.4.1) has no anchor to preserve,
so there is nothing subtler to do here and nothing to be clever about.

##### An unreadable log is not an empty one

`get_log` answers `error` code `log_unavailable` when the file is missing or unreadable, when the
configuration carries no log path, or when there is **no agent to read that configuration from**
(§2.6.0, §2.9). That last case is a unit in manual mode, which is a unit somebody is very likely
trying to debug, so this is the screen where the distinction is worth the most. It gets its own
sentence, and it names the ticket that would fix the common cause rather than only reporting the
symptom (issue #154).

##### Every line is hostile

§4.5.3 names log lines specifically: a pwnagotchi logs the SSIDs it just saw, so a line is a
string a stranger chose with a timestamp in front of it. Rendered as text, like everything else,
and the filter must not build markup out of the match either: **a matched substring is not
highlighted**, because highlighting means splitting a hostile string and putting the pieces into
elements, which is the one operation §4.5.3 exists to prevent and buys a wrapper around a word.

##### Geometry

The log area **fills what is left and scrolls inside itself**, in both orientations, and nothing
below it is pushed off the screen.

**The height starts at the view slot and is a percentage, not an inset.** This is written here
once, and the code points at it rather than restating it: `position: absolute; inset: 0` resolves
against the containing block's **padding** box, and `main` carries the header and bar as its own
padding, so an inset box lands back across the strip the padding reserved -- forty pixels of
overflow on every engine and orientation, which is issue #38's defect exactly, and which the first
attempt at this reproduced. A percentage height on an in-flow box resolves against the **content**
box instead, and picks up whichever sides carry the padding in the current layout, so it survives
§4.5.1's rail without a second copy of the formula. That last part is why the fix is not a
`calc()` in the view: restating the bar's height in a second file is the arrangement §4.5.1 already
refuses for the bar itself. This closes issue #38, which found the mockup's log box
overflowing its own container at 874x402 -- landscape being the orientation §4.5.1 says the Log
exists for, and therefore the worst place for the defect. §10.5's geometry invariant already
asserts the shell owns every strip of the viewport; this adds that the log's own box stops where
the view area does, with both orientations and an empty and a full buffer covered.

##### The font size is kept while the app is open, and not persisted

§4.5.1 persists the theme choice across launches and says nothing about this control. It is not
persisted here, and the reason is that persistence belongs with the theme and the density controls
that §4.5.1 describes and nothing has built: a storage key written now for one control means two
places deciding what survives a launch, and the second one arrives with the settings that were
always going to own it. Kept while the app is open, like the Wi-Fi segment choice (§4.5).

##### The DOM hooks

Declared here, per §4.5.1.1, and closed.

- `data-view="log"` on the view root, as §4.5 requires of every view.
- `data-log-lines` on the scrolling container, and `data-log-line` on each rendered line. **The
  container is always present**, empty buffer or not. It is the box the geometry rule below is
  about, and issue #38's own criteria require measuring it with the log both empty and full; a
  container that appears only once lines arrive is a different layout in the state a fresh launch
  spends the most time in, and the one nobody would have measured.
- `data-action` on the controls, over `refresh`, `follow` and `font-size`, with the follow control
  carrying `aria-pressed` because it is a toggle rather than a command.
- `data-font-size` on the view root, over `small`, `medium` and `large`. **The control cycles**
  those three in that order and wraps, which is one control rather than three and no popover; the
  root carries the step so the styling and a test read the same fact, and neither has to infer it
  from a rendered size.
- `data-log-filter` on the filter input, which carries an accessible name of its own rather than
  a placeholder: a placeholder is not a name, and every other input in this app is labelled
  (§4.5.1.1). Its label is in the copy table below with everything else the screen says.
- `data-empty-message` on the element carrying the empty, unavailable or not-connected sentence,
  with `role="status"`, absent when lines are rendered.
- `data-view-slot` on each of the shell's view slots in `App.svelte`, carrying the view's id. It
  is the shell's hook rather than this view's, and it is declared here because this view is the
  only thing that needs it and the reason is the geometry rule below: a scoped stylesheet cannot
  reach the slot its own view is mounted into, and the slot is where the height has to start. It
  is named in a closed list so that a comment in `App.svelte` claiming SPEC targets it is true
  rather than merely plausible.

##### The copy

| condition | sentence |
|---|---|
| no lines, connected | The unit's log is empty. |
| no lines, not connected | Not connected, so this list has not been read. |
| no lines held, and the unit answered `log_unavailable` | The unit could not read its log. With no agent it has no configuration to find the path in. |
| lines held, none matching the filter | No lines match the filter. |

**Not connected outranks an unreadable log.** `log_unavailable` is an answer from a session that
has since ended, and nothing clears it when the socket drops -- `resetStores()` runs on an
explicit disconnect and on `unauthorized`, not on an ordinary drop (§4.4.2). Left ranked the other
way, a unit that answered once and then went out of range leaves the screen asserting that it has
no agent, indefinitely and about a connection that no longer exists, which is §4.3.1's argument
against a mirror that keeps showing the last thing it saw.

**A held buffer wins over a failed refresh.** The row above is qualified deliberately: the
sentence appears only when there is nothing to show. A unit that delivered lines and then failed a
later refresh keeps its lines on screen, because they are still the last thing that unit said and
replacing them with a sentence would discard the reading in order to report the failure to get a
newer one. What that costs is honest and is accepted: the screen does not say the last refresh
failed, and until the diagnostics line of issue #176 exists there is nowhere for it to say so
without taking the log away.

**An empty log wins over an unmatched filter.** When there are no lines at all and a filter is
also typed, the sentence is the empty one: *No lines match the filter* would blame the filter for
an absence that has nothing to do with it, and would send somebody to clear a field that was never
the reason.

| control | label |
|---|---|
| follow, off | Follow |
| follow, on | Following |
| font size | Text size |
| the filter input's label | Filter |

The not-connected sentence is §4.5.2.3's, shared unchanged, as §4.5.2.4 already shares it: it says
nothing about which list it sits under, because the reason it appears has nothing to do with the
list.


#### 4.5.2.6 The Mirror, the second timer, and a string that becomes a URL (issue #196)

§4.5.2 asks for the full e-ink PNG via `get_screen`, a manual refresh and an auto toggle at about
five seconds. §2.10 fixes the other half: on demand only, the client drives the refresh by
polling, and the plugin must **never** push the frame, being far too heavy for the BT link.

##### The frame is held, because everything this app holds is held in one place

`screen_image` is today the one reply the client can receive and has nowhere to put: `lib/stores.ts`
drops it in its default arm, and §4.4.1's table has no row for it. It gets a store and a row, and
`resetStores()` clears it with everything else -- a host switch leaving unit A's display on B's
Mirror is the same defect as A's log lines on B's Log, and the more convincing one, because a
picture of a face carries no address to give it away.

##### This is the second timer, and it is granted rather than assumed

§4.5.2.5 allowed the Log a follow timer, argued the exception, and said plainly that a second one
is a specification question rather than a local decision. This is that question, answered here.

**It is granted, on identical terms**: opt-in, off on arrival, asking immediately when switched on
and then on the interval, stopping the moment the view is not current, surviving the trip away and
back. The Log's argument carries over unchanged, and §2.10 makes it stronger rather than weaker:
there is not merely no push today, there must never be one.

**What is different is the cost, and the honest position is that it has not been measured.** A
frame is the heaviest reply in the protocol, and §4.5.2 and §2.10 both say about five seconds
without anybody having watched one arrive over BT PAN. The reference display is small and its PNG
should be small with it, but "should be" is not a measurement, and this specification does not
record numbers nobody took (§11). So the interval is five seconds as already written, and **the
first hardware run is expected to revisit it** -- if a frame takes longer than the interval to
arrive, the toggle is asking for a second frame before the first has landed, and the coalescing
guard turning that into a no-op is a symptom rather than a design.

**The count is now two and the next one is harder to justify, not easier.** Both exceptions exist
because a specific message type has no push and cannot have one. A third view wanting a timer for
any other reason -- freshness, convenience, a value that changes often -- is not covered by either
argument and remains a specification question.

##### A base64 string from the wire becomes a URL, which is the one thing §4.5.3 names

The frame arrives as base64 with no prefix (`screen_image.json`) and has to become
`data:image/png;base64,...` in an `img` `src`. §4.5.3 forbids a remote string reaching "a URL, an
attribute or a style without the escaping that context needs", and nothing validates the wire
(issue #109).

**The shape is checked before the string becomes a URL**, in `lib/`: the base64 alphabet, padding
only at the end, and a length that is a multiple of four. This is a guard against a malformed or
hostile payload producing a URL that is not the one intended, not an attempt to decode a PNG in
the client or to prove the bytes are an image. **An empty payload is not a frame** and fails the
check with everything else, and it needs no clause of its own: a base64 string's last group holds
between two and four characters, so a rule that admits only complete groups already refuses the
empty string. An earlier draft of this paragraph reasoned that zero is a multiple of four and
therefore demanded a separate guard; that was wrong about the rule it was amending, and the guard
written for it turned out to change nothing. Recorded because the mistake is easy to repeat and
because a redundant guard reads as a case somebody handled (§4.5.2.1).

The alphabet is the half that matters for §4.5.3: a quote or a space in a `src` is a string
leaving the URL it was supposed to be inside. **The length is checked for a different reason**,
and it is worth separating so the check is not read as validation it is not doing: a payload of
the wrong length is not dangerous, it is merely unrenderable, and without the check the screen
shows a broken image with nothing saying why. Cheap to test, and it turns a silent failure into
the sentence below. What is **not** checked is whether the bytes are a PNG at all, and no amount
of shape-checking would establish that. A string that fails renders a sentence instead of an image, and the sentence says the frame
could not be read rather than blaming the unit, because a frame that arrives corrupt and a frame
this app mishandles look identical from the screen.

`data:` only. §2.15.1's `img-src` carries `data:` and deliberately not `blob:`, and building a blob
URL would fail on the device and nowhere else.

**No inline `style` attribute anywhere in this view.** §2.15.1 says `style-src 'self'` forbids the
attribute and not only the block, and names this view as the place somebody will reach for one
when sizing an image. Sizing belongs in the component's stylesheet.

##### What the screen says beside the picture

**The frame's own `mtime`**, through `formatUnitTime` (§4.5.1.1). A mirror of a display that
updates rarely is indistinguishable from a mirror that has stopped updating, and the timestamp is
the only thing that tells them apart. It is the frame's time, not the time it arrived: those differ
by the whole round trip and by however long the file sat there.

**An accessible name that says what the image is, not what it shows.** The frame is a picture of
text this app cannot read -- the face and status are on the wire separately, in `face_status`, and
reading them out of the picture is not something the client does. So the name names the object,
and anybody who wants the words has them on the Dashboard.

##### The DOM hooks

Declared here, per §4.5.1.1, and closed.

- `data-view="mirror"` on the view root, as §4.5 requires of every view.
- `data-screen-frame` on the `img` when a frame is rendered, absent otherwise.
- `data-field="frameTime"` on the frame's timestamp, with §4.5.1.1's `data-empty="true"`. **The
  field is present whether or not a frame is held**, dashed when it is not: §4.5.1.1's rule is
  that the row stays and the value becomes a dash, and a timestamp that appears only once there is
  something to timestamp makes the screen change shape rather than change value.
- `data-action` on the controls, over `refresh` and `auto`, with the auto control carrying
  `aria-pressed` because it is a toggle rather than a command.
- `data-empty-message` on the element carrying the no-frame, unreadable or not-connected sentence,
  with `role="status"`, absent when a frame is rendered.

##### The copy

| condition | sentence |
|---|---|
| no frame held, connected | The unit has not drawn a frame yet. |
| no frame held, not connected | Not connected, so this list has not been read. |
| the unit answered `no_frame` | The unit has not drawn a frame yet. |
| a frame arrived that is not readable | The frame could not be read. |

| control | label |
|---|---|
| auto, off | Refresh automatically |
| auto, on | Refreshing automatically |
| refresh | Refresh |
| the image's accessible name | The unit's display |

The not-connected sentence is §4.5.2.3's, shared unchanged, and it says "list" about a picture on
purpose: it is one sentence about the connection rather than four about four screens, and the
alternative is a sentence per view that differs only in a noun.

`no_frame` and a frame that has never arrived say the same thing because they **are** the same
thing from the owner's side: the unit has not drawn. One is the unit saying so and the other is the
app not having asked yet, and a distinction the owner cannot act on is not worth a second sentence.


### 4.5.3 Remote strings are attacker-chosen (issue #32)

Every string this app renders that did not come from the owner came from somebody else, and on
this product that somebody is within radio range rather than on the other side of an account
system. SSIDs are 32 bytes of anything and this is a tool for displaying the names of networks it
did not choose to see. `Peer.fullName`, `Peer.face` and `Peer.identity` arrive over pwngrid from
another person's unit. Handshake filenames are derived from SSIDs. Log lines are whatever any
plugin logged, which on a pwnagotchi includes the SSIDs it just saw.

The payload lands on the owner's phone, in a page that also holds the reboot and shutdown
controls and, in `localStorage`, the token (§4.3.6). So the rule is not a style preference:

- **`{@html}` is banned.** Svelte escapes `{...}` by default, which makes the safe path the
  default one; `{@html}` is the only way out of it, and there is no case in this product that
  needs one. If a case ever seems to, that is a specification question and not a local decision.
  CI greps for it, in the same spirit as the `# pragma: no cover` ban.
- **Event handlers are bound, never built as markup.** An inline handler assembled by string
  concatenation breaks on an apostrophe with no hostility at all, and executes with a little.
- **A remote string is rendered as text.** Never into a URL, an attribute or a style without the
  escaping that context needs, which is not the same escaping in each of them.

**A string rendered as text can still lie about its neighbours (issue #219).** That is this
rule's second half and it is a different question from the first. U+202E and the other bidi
overrides are preserved verbatim by every escaping this section requires -- correctly, because
escaping is about not executing a string -- and the browser then renders the rest of the line
reversed. On a diagnostics line that means a hostile unit can reorder its own sentence against
the timestamp printed beside it; in a list it means a row that reads as though it belongs to a
different network.

**The answer is isolation, not stripping.** A remote string is rendered inside a `<bdi>`, the
element the platform has for exactly this: it isolates the bidirectional context of its contents
from the surrounding text, with no CSS and no filtering. Stripping the characters was the other
option and it is wrong for this product: an Arabic or Hebrew SSID is a name, not an attack, and
this app displays names it did not choose. This section's own rule about a remote name refuses to
mangle one
for a related reason, and deleting characters to make a layout behave is the same mistake with a
security justification attached to it.

**A separator between the two must be expressed so the compiler cannot drop it.** Taking a
composed line apart puts the local text into its own block, and Svelte trims whitespace at a block
boundary: `{#if hasTime} at {time}{/if}` renders `nameat 09:14`. This is the same compiler
behaviour issue #188 recorded for the accessible label beside a dash, met again in a new place
because the isolation rule creates exactly the shape that provokes it -- a remote piece and a
local piece with a space between them, now separated by a tag or a block. Write the separator as
an expression, `{' at '}`, which is a value rather than markup and survives.

**Only the remote substring goes inside the element, never the composed line.** This is the part
that decides whether the fix works at all: an override inside an isolate still reorders the text
*beside it within that isolate*, so wrapping `<name> at <time>` as one unit isolates it from the
rest of the page and leaves the timestamp exactly as reorderable as before. The local text -- the
separators, the sentence the code selected, the time this client stamped -- stays outside. Three
sites had to be taken apart rather than tagged, because they were building one string out of a
remote piece and a local one before anything could wrap either: the Dashboard's last handshake and
last peer, and the Settings diagnostics line.

That has a consequence worth stating: **a formatter that composes a remote value with local text
into one string cannot be used at a site that needs isolation.** The composition has to happen in
the markup, where the boundary can exist, rather than in the function.

`formatNamedEvent` was that formatter and it has no callers left. It goes, with its tests: a
function kept because deleting it felt wasteful is a function the next reader will use, and this
one now composes a shape the isolation rule forbids. What was worth keeping from it -- that a
remote name passes through unchanged, hostile or not, and that a missing name and a missing
timestamp each have an answer -- is a property of the markup that replaced it, and belongs in that
markup's tests.

**What a hostile-string test asserts changes with this, and the old form was always the weaker
one.** Those tests read "the field has no child elements", which stood in for "the remote string
produced no markup" only while the template happened to create no elements of its own. It creates
one now, deliberately, so the proxy is false and the tests it protected would fail for a reason
unrelated to any attack. The property was never about the count: it is that **no element in the
field came from the remote string**. Assert that -- the string's own characters present as text,
and no `script`, `img` or other injected node anywhere under the field -- because an assertion
that a container is empty stops being about security the moment somebody legitimately puts
something in it.

The realistic worst case is modest and worth saying plainly: nobody gets code execution out of
this. It is a display-integrity bug on a tool whose whole job is to report faithfully what a unit
saw, which is why it is worth the element rather than a shrug.

This is one of two layers. The other is a Content-Security-Policy on the static server, issue
#67, which is not shipped yet: when it is, an escape missed on one screen will not execute,
because inline script will not run. They fail independently, which is the only reason two
layers are worth more than one careful one, and until the second lands this rule is carrying
the whole weight on its own.

The tests for it belong with the views that render these strings, and cannot be written before
them: a hostile-name fixture asserted to render as text, with an SSID containing `<script>`, one
that is nothing but quotes, a peer name carrying markup, and a log line carrying an SSID that
does.

#### 4.5.2.7 The Map, the one external origin, and a viewport that has to survive (issue #198)

§4.5.2 asks for Leaflet plotting handshake sidecar positions plus the current location marker.
Nothing new goes on the wire for it: `handshakes_list` entries already carry
`gps: HandshakeGps | null`, and §4.4.1's `gps` store already holds the unit's position. This is a
presentation section, and if building it turns out to need a protocol change, that is a finding
worth its own issue rather than a quiet schema edit.

**The caption says how many captures there are, and says it differently when it does not know
(issue #153).** `handshakes_list.total` is nullable: null means the plugin has no directory to
enumerate -- no agent and no `handshake_dir`, which is a manual-mode unit -- and zero means it
looked and found nothing. The caption's first clause counts captures, so on a null it must not
count: it names the condition instead. The second clause, how many carry a position, stays a real
count of the entries in hand, because that one is true either way -- there are none, and that is
not a guess.

**The wording is pinned here, because §4.5.1.1's convention does not transfer.** That section
renders an unknown *value* as a dash with an accessible label beside it, which works in a cell
where the dash sits where a number would. This is a sentence, and "- captures, 0 with a position"
is not one. So the caption reads **`Capture count unavailable, 0 with a position.`**, reusing the
same `unavailable` word §4.5.1.1 gives to the screen reader rather than inventing a second one for
the same idea. An earlier draft of this paragraph said only "the shape §4.5.1.1 uses", which is
the kind of instruction that produces two different answers from two people who both followed it:
one read it as the dash and one as the word.

##### The refresh rule is claimed a third time, and claimed rather than restated

§4.5.2.3 settled when a list is asked for and §4.5.2.4 settled that the rule is shared rather
than copied. The Map wants the same three occasions and the same coalescing owner: it is the
**second caller of `refreshHandshakes`**, the one the Wi-Fi view already uses, and it reaches it
through `watchViewRefresh` like every other view whose data nothing pushes. Nothing here is new,
which is the point of writing it down: a third screen deriving the rule again is the failure
§4.5.2.4 was written to stop, and the reasoning lives in §4.5.2.3 for anyone who wants it.

##### An empty map and a map nobody has asked for are different screens

`hasListDataArrived` already tells the two apart from the connection state, and the Map uses it
rather than a "have we ever received one" flag of its own -- §4.5.2.3 argued that out and accepted
the one-frame window where it is wrong. There are **three** states here rather than the lists'
two, because a unit can have captures and none of them located: no reply yet, a reply with no
captures at all, and a reply whose captures all carry `gps: null`. The third is the interesting
one, since it is the ordinary state of a unit whose GPS has never had a fix, and a screen that
renders it identically to "nothing captured" would say the wrong thing about a night's work.

##### What may be plotted, and what a number from the wire is worth

A capture is plotted when its `gps` is not null **and** both coordinates are finite numbers.
The schema says they are numbers, and `lib/stores.ts` writes the wire payload with no runtime
validation (issue #109), which is the same reasoning `compareKnownFirst` and `formatPeerNumber`
are already built on. A `NaN` reaching Leaflet is not a rejected marker, it is a marker at an
unspecified place or a thrown error inside a library's own render, and neither is a thing this
view can report. A reading that is not finite is treated as a reading that is not there.

`accuracy` is carried on the wire and is **not** drawn as a circle in this change. That is a
scope decision rather than an oversight: the radius would be the first thing on this screen
whose size claims a precision nobody has checked against a real sidecar.

##### The current location marker is the unit's location, not the phone's

Worth stating because §4.6 makes the distinction easy to lose: `lib/geo.ts` acquires the
**browser's** position, and it does so in order to push it to the unit as the lowest-priority
source. What comes back in the `gps` store is the unit's position whatever produced it, and that
is what the marker shows. A second marker for the phone is not in this change; if it is ever
wanted it is a different fact about the world and needs its own argument.

##### The map is created once, and told its size when it becomes visible

§4.5 keeps views mounted so the Map keeps its viewport and zoom across navigation, and this view
is the reason that sentence is in §4.5. It follows that the Leaflet instance is created once and
never torn down on navigation. It also follows that the map is sized while its view is hidden,
where a hidden element measures zero, so **becoming current has to tell Leaflet to measure again**
or the map renders into a box of no size and stays there. This is the one piece of Leaflet
lifecycle this specification names, because it is the one that fails silently and only after a
navigation rather than on first load.

##### The hooks and the copy, so a test pins a sentence rather than a paraphrase

The root carries `data-view="map"`, like every other view. The map's own element carries
`data-map-surface`, which is what the geometry assertion measures and what tells a test the map
was built at all without reaching into Leaflet's class names -- those are a library's private
vocabulary and a test written against them breaks on a Leaflet upgrade that changed nothing this
app cares about.

The three states of the paragraph above get three sentences, and two of them already exist:

| State | Sentence |
| --- | --- |
| No reply yet | `Not connected, so this list has not been read.` |
| A reply with no captures | `The unit has no captures.` |
| A reply whose captures carry no position | `None of the unit's captures has a position.` |

The first two are `lib/format.ts`'s existing strings, taken unchanged. The first is the shared
one §4.5.2.4 already refused to duplicate, and it says nothing about which list it is under
because the reason it appears has nothing to do with the list. The second is the Captured
segment's, and it is the same fact about the same list, so a second wording would be a second
answer to one question. Only the third is new, and it is new because no other view has ever had
to say it. All three appear in a `[data-empty-message]` element, as they do elsewhere.

**A caption says how many captures have a position**, in the form `12 captures, 3 with a
position.` It is there because state three is a spectrum rather than a switch: a unit with
twelve captures and one located reads as a working map, and the thing the owner wants to know is
that eleven are missing. It also makes the finite-coordinate rule observable without a test
having to count Leaflet markers, which is the same argument as `data-map-surface` above.

##### Geometry, and where the assertion lives

§4.5.1's rule applies here and this is the view it was written for: the map is narrower **and**
shorter than the views area, in both orientations, and the leftover strip is where a drag that
means "scroll the page" can begin. It is asserted as geometry in
`frontend/tests/e2e/geometry.spec.ts`
across all four projects, the same way the rule is asserted for every other view, rather than as
pixels (§10.5).

##### The tile origin is the only external thing on screen

§2.15.1 admits `https://*.tile.openstreetmap.org` in `img-src` and says plainly that this is a
real weakening of the policy rather than a formality: it is the only external origin in it. What
follows for this view is narrow and absolute in the way §4.5.3 is: **a tile URL is built from the
template and integers, and nothing that arrived on the wire is interpolated into it.** No SSID,
no filename, no `source`, no subdomain chosen from a payload. The coordinates decide which tiles
are fetched, which is unavoidable and is why they are checked for finiteness above, but they
reach the URL as numbers Leaflet computed and not as strings this app pasted.

##### The inline-style gate moves to the built page, and that is not a weakening

§2.15.1 forbids an inline `style` attribute because `style-src 'self'` refuses one, and the Mirror
shipped with a test asserting no element in its mounted tree carries one. **That assertion cannot
be kept for this view, and copying it would be worse than not having it.** Leaflet positions its
own layers by writing `element.style.*` through the CSSOM, CSP does not intercept that, and the
reflection into a `style` attribute is not something this repository authored. A mounted-tree
assertion would therefore fail on a correct build, and the way it would be made to pass is by
being narrowed until it stops asking anything -- this session's own vacuity rule (§11.3) arriving
in a test.

So the gate is on the **built page**, where every attribute present was authored here, and it is
the inline-script scan's neighbour rather than a per-view test. It is tracked on issue #91, whose
subject is the `style-src` twin of the hole #144 closed for `script-src`.

##### A pin's popup is a string from the air, and Leaflet will treat it as markup

`bindPopup` given a string parses it as HTML. `HandshakeEntry.ssid` is chosen by whoever named
the access point, which makes the naive `bindPopup(entry.ssid)` the one place in this view where
a remote string becomes DOM -- §4.5.3's subject exactly, and the reason that section exists.
**The popup content is built as an element with its text assigned, never as a string handed to
Leaflet.** Every other screen in this app renders a hostile string through Svelte's own escaping;
this is the same rule reaching the one place Svelte is not in the path, because Leaflet builds
that node itself.

##### The marker images are bundled, and this is where a CDN would sneak back in

Leaflet resolves its default marker icons by guessing a path from the URL its own script was
loaded from, and that guess is wrong under a bundler: the icons 404. The failure is cosmetic and
therefore dangerous, because the obvious repair -- pointing the icon path at unpkg -- puts an
external origin back into a policy that admits exactly one, and §2.15.1 spent an argument on
admitting that one. The three default images are imported from the package and the default icon
is pointed at them, so the paths the bundler emits are this origin's.

##### Where the map starts, and the one time it moves on its own

Not a rule so much as a decision that had to be made and is better written down than rediscovered.
The map opens on a world view. It centres itself **once**, on the unit's position if there is one
and otherwise on the first located capture, and never again on its own -- a map that re-centres
whenever a position arrives fights the reader who panned away from it, and this view keeps its
viewport across navigation precisely so that panning means something.

**The caption** renders only once `hasListDataArrived` is true. A caption reading
`0 captures, 0 with a position.` while disconnected is a claim about data that never arrived,
which is §4.3.1's rule and not a new one. The empty sentence is the opposite case and the table
above already covers it: the not-connected state has a sentence precisely because that is the
state worth naming.

**An empty sentence never appears over a map that is still showing pins.** Losing the connection
does not erase what was already plotted, and a screen that plots twelve captures while saying the
list has not been read is telling the reader two things that cannot both be true. The sentence
appears when there is nothing on the map; the caption disappearing is what marks the readings as
no longer current. `views/WiFi.svelte` already behaves this way for the same reason -- a
disconnect leaves its rows visible and adds no contradicting sentence.

**The caption counts what the unit has, not what arrived.** `handshakes_list` carries `total` and
`truncated`, and a truncated reply that made the caption read `50 captures` when the unit holds
four hundred would understate the thing the caption exists to report. It counts `total`, and a
truncated reply carries the same notice the Captured segment already shows.

**Which source centres the map is decided by what is available, not by which arrived first.** The
unit's position wins when there is one; the first located capture is the fallback. An
implementation where two effects race and whichever ran with data first wins would centre on a
capture whenever `handshakes_list` beat the first `stats`, and then lock the unit's own position
out for good.

##### The attribution keeps the credit and loses the decoration

Leaflet's attribution control ships a default prefix containing a small flag drawn as SVG, and it
fails this project's contrast floor: measured on the built page, `rgb(255, 213, 0)` on
`rgb(248, 248, 248)` is **1.34:1 against a floor of 3:1** for a non-text indicator, and a second
path in the same mark measures 1.74:1. Two of three hundred and thirty pairs, in both palettes.

It is removed rather than waived. §4.5.1 rejects a level that gets waived case by case until it
means nothing, and the first waiver being for a decoration is how that starts. **What is kept is
the credit**: OpenStreetMap's tile policy requires attribution to its contributors, that string is
this repository's own, and it stays. Leaflet's own credit stays too. Only the decorative mark
goes, through the attribution control's documented `prefix` option, which is a supported setting
and not a patch of the library.

Worth saying because it is the shape of the next argument: a contrast gate that scans everything
on the page will find things a dependency drew, and the answer is to configure the dependency or
to drop it, never to narrow the gate until the dependency passes.

##### Leaflet is a dependency, and D2 naming it does not pre-approve it

D2 locked the choice years ago. It did not approve a version, a bundle size, or a transitive tree,
and `CONTRIBUTING.md` names a new dependency as one of the three things read closely. §4.1's
position stands -- Leaflet is bundled locally and only the tiles come from the internet -- so
nothing here loads a script from a CDN, which `script-src 'self'` would refuse anyway.

What was checked, on 2026-08-30 and stated as an observation of that day rather than as a
standing claim: **`leaflet@1.9.4`, BSD-2-Clause, with no runtime dependencies at all.** `1.9.4`
is the `latest` tag; `2.0.0` exists only as an alpha and is not a candidate. The types come from
`@types/leaflet`, which pulls `@types/geojson` and is a development dependency, so neither
reaches the bundle. A transitive tree of zero is the reason this dependency is cheap to accept,
and it is the fact most likely to stop being true at the next major, which is why the version it
was true of is written next to it.

**`leaflet` is pinned exactly and `@types/leaflet` is not**, which is a deliberate asymmetry and
not an oversight. The first reaches the bundle the owner installs on a unit, so an unreviewed
version arriving on a fresh install is a change to what ships. The second is types only: it
affects what the type-checker believes and never what runs, a wrong version fails `tsc` rather
than reaching a device, and it carries the same caret as the eleven other development
dependencies in that file. One line pinned differently from its neighbours is an anomaly somebody
eventually tidies away without knowing what it was for, so the reason is here instead.

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

#### 4.6.2 Acquiring the position: the tap, the third timer, and what backgrounding takes away (issue #175)

§4.6 says what to push and how often. It does not say what makes the browser hand over a
position in the first place, and on the one platform this app targets that is not a detail:
**iOS refuses the permission prompt unless a tap asked for it**, and a `watchPosition()` called
without one fails silently rather than raising. A module written against the specification as it
stood would have called `watchPosition()` on connect, seen nothing, and had no way to tell that
from a phone that simply has no fix yet.

The four platform facts this section is built on, each verified rather than assumed:

| # | fact | consequence here |
|---|---|---|
| G1 | `getCurrentPosition`/`watchPosition` need a user gesture on iOS to show the prompt; without one the call fails silently, with neither callback firing | the ask happens on a tap and nowhere else |
| G2 | `navigator.permissions.query({name: 'geolocation'})` exists from iOS 16 and does not itself prompt, but on iOS it reports `prompt` for a permission denied in system settings | it is not called at all, for the reason below; were it ever called, **it could never be the reason this app says "denied"** |
| G3 | `watchPosition` stops delivering while the document is hidden and does not resume on its own | the watch is torn down on hide and rebuilt on show; a client that merely kept it would push a frozen position |
| G4 | `GeolocationPositionError.code` is `1` `PERMISSION_DENIED`, `2` `POSITION_UNAVAILABLE`, `3` `TIMEOUT` | `1`, and only `1`, is what §4.6.1's row 3 is |

Sources: W3C Geolocation (`PositionOptions`, "position updates are exclusively delivered to
fully active documents"), MDN `Permissions.query()` and `GeolocationPositionError.code`, and the
Apple developer forum thread recording the `query()` inconsistency of G2. A fifth report -- that
the prompt does not open at all for some units in standalone display mode -- is recorded in
issue #175 as a hazard for the first hardware run and is **not** designed around: there is no
client-side workaround for an alert that never appears, and pretending otherwise would be a
mechanism that cannot be tested.

##### Sharing is off at launch, and one tap turns it on for the life of the app

Not persisted. The tempting design is a stored preference, and it is wrong for a reason G1
supplies: a stored `true` cannot produce the gesture the next launch needs, so the app would
come up believing it is sharing, push nothing, and report a state it does not have -- which is
exactly what §4.3.1 forbids. Off at launch and one tap to arm is the same rule §4.5.2.5 already
gives the Log's follow toggle, and for a stricter reason.

The control is in **Settings** (§4.7), in a section of its own, and it is the only thing in this
app that calls into `navigator.geolocation`. It is not on the Dashboard and not on the Map: a
permission prompt is not something a view should be able to raise as a side effect of being
navigated to.

Turning it on attempts the watch immediately, inside the tap's own call stack. Nothing may be
awaited before that call: a permission check, a store read or any other `await` first would spend
the gesture and G1 would take the prompt away.

**`navigator.permissions` is not called at all**, and that is a decision rather than an
omission. G2 rules it out as a source of the denied state, which leaves it able to answer one
question only -- has the owner been asked before -- and the copy table below has no sentence
that turns on the answer. A call whose result nothing renders is a branch nothing can reach,
which §4.5.2.1 already refused for being worse than absent, and it would be a call into the
permissions layer kept alive for a use that does not exist. If a later section finds a sentence
that needs it, G2 is the constraint that call has to be written under: **after** the watch has
been started, never before, and never as the reason this app says "denied".

##### The states this module holds are client-local, and they are not §4.6.1's four

§4.6.1 decides what the *interface* says about the unit's position, and rows 1, 2 and 4 of that
table are read from `stats.gps`. This module holds something narrower: what this browser is
doing. The two meet at one point only -- row 3, permission denied, the one row §4.6.1 marks as
client-local -- and this module is where that fact comes from.

**It supplies that fact and does not compose the four rows**, and that division is deliberate
rather than incidental. A function evaluating §4.6.1's table belongs in the change that renders
its answer, which is issue #34; written here it would be an export nothing calls, which is the
same objection this section already makes against calling `navigator.permissions` for an answer
no sentence uses. An unrendered resolver is also an unreviewed one: the row conditions have a
gap -- a unit with a fix from `gpsd` and no Pi fix matches no row as written -- and that gap is
worth finding against a screen that has to show something, not against a unit test agreeing with
a table.

| state | reached when |
|---|---|
| `off` | at launch, and after the control is turned off |
| `waiting` | the control is on and nothing has been acquired: either the watch is running and has produced nothing yet, or the document is hidden and the watch is torn down (see Backgrounding) |
| `sharing` | a position has been received; it is what the push timer sends |
| `denied` | the watch reported `GeolocationPositionError.code === 1` |
| `unsupported` | `navigator.geolocation` is absent |

`denied` is sticky for the life of the app: the owner changes it in the browser's own settings,
not in this app, and the only honest thing the control can do afterwards is let them try again,
which returns the state to `waiting`.

**A callback from a watch this module no longer owns is ignored, whatever it says.** `clearWatch`
does not promise that a callback the platform has already queued is cancelled, so a
`PERMISSION_DENIED` from a watch that has been torn down can land afterwards. Each watch is
started with a token, the callbacks carry it, and one that does not match the current watch does
nothing at all.

This reverses an earlier decision in this section, and the reasoning is worth keeping because the
first answer was defensible and still wrong. It said the state should become `denied` even for a
control the owner had already switched off, on the grounds that `denied` is a fact about the
browser rather than about the control, and that suppressing it discards an explanation the app
already has. Both halves of that are true. What it missed is the **second** consequence of the
same race, which is worse than the one it was reasoning about: off, then "Try again", then the
*previous* watch's queued refusal arrives and re-denies the fresh attempt and tears down the watch
that was just started. The retry -- the one recovery this state exists to offer -- becomes
defeatable by a callback belonging to a watch nobody is waiting on.

And the explanation is not lost by dropping it, which is what made the first answer look free.
Geolocation permission is per-origin and not per-watch, so a browser that refused the old watch
refuses the new one too, and the denial arrives again from the watch that is actually current. The
app says the same thing, one callback later, about something it owns.

**A state this module keeps cannot tell a stale callback from a live one**, which is why the token
is on the watch rather than a rule about ordering. Tearing a watch down invalidates its token
before anything else happens, so a callback from it is inert immediately and not merely once a
replacement exists. That is the general form: any answer that
depends on reasoning about which callback arrives when is an answer that will be wrong on a
platform nobody here has measured. **Reaching `denied` tears the watch and the timer down**,
rather than leaving them running behind a state that says the browser refused: a watch that has
reported `PERMISSION_DENIED` will not deliver a position afterwards, so keeping it is the module
still appearing to try at something it has been told it may not do. The fresh attempt starts a
new watch, and it is a new one rather than the old one resumed. There is no state for "the browser accepted the watch and
said nothing", because G1 makes that indistinguishable from `waiting` and inventing a distinction
the platform does not offer is the same defect as inventing a symbol.

**A guard is dead when this module's own control flow makes it dead, and not when a fact about
the platform does.** Three guards in the first version of this module were unreachable by the
call graph alone: every caller had already asked the same question, synchronously, with nothing
in between that could change the answer, and they were deleted on the argument this section makes
twice over. The guard in front of `watchPosition` is not one of them, and the distinction is
worth stating because it looks identical. It is reached again from the visibility handler,
arbitrarily long after the tap that first checked, and what stands between the two calls is not
control flow but the platform assumption below. An assumption about the environment is a weaker
thing than a proof about the code, and a guard that rests on one is defence rather than
decoration.

**`unsupported` is recomputed from the same guard every time the state is published, and in a
browser the answer never changes.** A browser does not remove `navigator.geolocation` from a
document that is already running, so what this state costs is one capability read per publish and
no branch of its own. What is deliberately **not** added is a publish of its own at the point the
control is tapped and refuses: that would be a branch reachable only from a test, which §4.5.2.1
refuses and which this section refused again for `navigator.permissions`. The distinction is
narrow and worth stating exactly, because an earlier draft of this paragraph claimed the state
was decided once and never revisited, and the code contradicts it -- the guard and the state read
the same function, at every publish.

`unsupported` is not a fifth GPS state and must never be presented as one: it says this browser
cannot be a source, which is a fact about the phone, exactly as §4.6.1 says "not fitted" is a
fact about the unit and belongs to neither table.

##### The third timer, and why it is not a third exception

§4.5.2.6 granted the second timer and said a third is harder to justify. This one does not stand
on that argument at all, and it would be wrong to record it as a third view timer.

The two granted so far are **polls**: a view asking the unit for something the unit will not
push. This is a **cadence**, outbound, and §4.6 fixed it at five seconds before either poll
existed, for a reason that is in the schema rather than in a view: `incoming/gps_data.json`
drops browser coordinates after ten seconds, so a client that pushes less often than that is
one whose position is periodically absent from the unit that is writing the sidecars. Five
seconds is one missed push of margin, and it is not a preference.

What it does have in common with them is the discipline: it exists only while the control is on,
it stops the moment the control is off, and it stops on hide (G3). Pushing on significant change
as well -- every `watchPosition` callback -- is the optimisation §4.6 already allows on top of
the floor, and it is not a replacement for the timer, because a stationary phone produces no
callbacks and its position is still true.

**The watch asks for high accuracy.** `PositionOptions.enableHighAccuracy` defaults to false,
and the default is wrong here rather than merely unconsidered: this app exists to say where a
capture was taken, and a position good to the nearest cell tower does not locate a capture, it
locates the neighbourhood. It costs battery, on a phone that is already the unit's hotspot, and
that cost is the reason the whole feature is opt-in behind a tap rather than something the app
does because it can.

**The timer pushes nothing while `stats.gps.piFix` is true.** That is §4.6's rule, read from the
`gps` store (§4.4.1) at push time rather than latched when the control was tapped: a unit whose
on-Pi fix drops while the app is open must start receiving pushes without anyone touching the
control, and one that acquires a fix must stop receiving them the same way. The timer keeps
running across that transition rather than being stopped and restarted, so there is one rule and
one place it is evaluated.

**The tap consents to sharing with whichever unit is active, and not with the unit that was
active when it was tapped.** `currentClient()` is asked at every push, so activating a different
host while sharing is on starts pushing to that unit with no fresh tap. That is intended and is
written down rather than left to be inferred: every unit in the host list is the owner's own, the
list is edited on the same screen as this control, and a consent that had to be re-given per unit
would mean the position silently stopping when the owner switched units, which is the failure
this whole feature exists to avoid. It is stated here because it is the kind of decision that
looks like an implementation detail and is not.

**The push goes through `lib/ws.ts`'s `sendGps`, and through the client `lib/session.ts` holds.**
This module does not build a client, does not hold one, and asks for the current one at each
push. With no client, or with a socket that is not usable, `sendGps` discards the frame (§4.3.3),
which is the whole of the offline rule: a position is only true at the instant it was taken, so
there is nothing here to queue and nothing to replay on reconnect. A host switch needs no
handling in this module for the same reason -- what the phone knows about where it is does not
belong to the unit it was last connected to.

##### Backgrounding

On `visibilitychange` to hidden: the watch is cleared, the timer is stopped, **and the last
reading is dropped there rather than on the way back**. That placement is the one that makes the
state true throughout: `sharing` is defined in the table above as a position having been received and being
what the timer sends, and while hidden nothing is sent, so a state left at `sharing` for the
duration is the app describing something it is not doing. It is not invisible either -- iOS
renders the hidden document in the app switcher. Dropping the reading on hide makes the state
`waiting` for the whole trip, which is true, and it leaves the resume with nothing stale to
push. On the way back
to visible, with the control still on, both are started again -- and starting them clears
whatever is already running first, unconditionally, so that an event that fires without the
visibility having actually changed cannot leave two watches live with only the second one
reachable to clear. G3 is the whole reason -- a watch that survives the trip delivers nothing and
the module would push the last position it saw, which after a walk to somewhere else is a wrong
position rather than a missing one, the worse of the two.

**Coming back is a fresh start, not a resumption.** Tearing the watch down on hide stops the
pushes that happen *while* hidden, and that alone is only half of what G3 asks for: the timer
re-armed on the way back would fire up to a full interval before the new watch has delivered
anything, and what it would send is the coordinate taken before the phone was pocketed. That is
the wrong position rather than the missing one, which is the trade this whole paragraph exists to
refuse. Dropping the reading with the watch is what closes it, and the state on resume is
therefore `waiting` until the new watch produces something -- the same thing the control's own
tap does, for the same reason.

**Which property says the document is hidden is not a rule this section makes.** `document.hidden`
is defined as `visibilityState !== 'visible'`, so the two cannot disagree in a browser, and a
specification that picked one would be writing a test fixture's shortcoming into normative text.
A fixture that sets only one of them is an incomplete fixture; §10.7 carries that, because it is
a fact about the fake and not about the app.

This is a lifecycle listener, and it is the first in this app. It is **not** the answer to issue
#120, which asks the same question about the socket: releasing a socket on hide and re-opening it
on show is a decision about the connection with consequences this one does not have, and the two
must not be settled together because one of them happens to arrive first.

##### DOM hooks and copy

The control is a toggle whose label names what it offers, with `aria-pressed` carrying whether
it is on, the same shape the Log's follow control and the Mirror's auto-refresh control already
have. That is a precedent in this codebase and not a rule §4.5.1 states -- an earlier draft of
this paragraph cited §4.5.1 for it and the sentence is not there, which is the same fabricated
citation this specification has caught itself in twice now. `data-geo-toggle` on the
control and `data-geo-state` on its section, carrying the state name from the table above, are
what a test reads, with `data-geo-message` on the element holding the sentence. That third hook
is there so that a test asserting the copy reads the one element that is the copy, rather than
the section's flattened text, which also contains the control's own label and would pass a
sentence that had gone missing as long as the label still matched.

The control carries **`Share this phone's position`**, except at `denied`, where it carries
**`Try again`**: that is not a state the control can be pressed back out of, and the only thing
left to offer is another attempt. `aria-pressed` carries whether the watch is running, which is
`waiting` or `sharing` and neither of the other three. At `unsupported` the control is
`disabled`, since the tap has nothing to do and a control that responds to nothing is worse than
one that says it cannot.

| state | what the section says |
|---|---|
| `off` | Not sharing this phone's position. |
| `waiting` | Waiting for a position. |
| `sharing` | Sharing this phone's position. |
| `denied` | This browser refused location access. Allow it in the browser's settings, then try again. |
| `unsupported` | This browser cannot supply a position. |

None of these five sentences says anything about what the unit has. That is §4.6.1's table and
§4.4.1's `gps`, and a section that mixed the two would be telling the owner their captures are
located because their phone is willing, which is the mistake §4.6.1 spends its second half on.

---

### 4.7 Settings (`lib/settings.ts`)

The host list of §4.5.2, persisted, and the one place that decides which unit the client talks
to. It is the piece between §4.3 and the views: `lib/ws.ts` takes a URL and a token and asks no
questions about where they came from.

**A host is `{id, label, address, wsPort, httpPort, token}`**, with the ports defaulting to 8082
and 8443 and `token` nullable and per host, `null` meaning the unit asks for none. One list, one active id, persisted under
`companion.settings`, beside the token key §4.3.6 pins.

**The token stays under `companion.token`, and this module is its only writer.** A per-host token
and a single key the client reads are not in conflict as long as one of them is the source: the
list holds each host's token, and activating a host writes that host's token to the key the
client reads. The alternative, teaching `lib/ws.ts` about a host list, would put the choice of
unit inside the connection layer, which is exactly the coupling §4.4 avoided in the other
direction.

**The stored blob is validated on the way in and on the way out.** A field the Settings screen
writes is no more trustworthy than one read from storage: both end up persisted, and only one
of them is repaired by the next load. A port that is not an integer in range is repaired to its
default wherever it arrives, and an address that is not an IPv4 literal is refused where it is
written rather than allowed to sit in storage until something else trips over it.

The two refusals differ, and deliberately. **The parser repairs or drops**, because it is reading
data nobody is standing in front of and the only useful outcome is a usable app. **A mutator
throws on a bad address**, because its caller is the Settings screen, which has the owner in
front of it and a field to put a message next to, and because an address is the one field with no
substitute: a repaired port is still that host, a repaired address is a different unit. A throw reaching a user is a bug in that screen, not a path
somebody walks: validate in the form, and the throw is the backstop that says the form forgot.

**An address is an IPv4 literal**, and nothing else. §4.5.1 decided this before any of this
section existed and gave the reason: the plugin binds IPv4 literals only (D5.1), and
`gen-cert.sh` refuses IPv6 because a certificate carrying a DNS name is not matched by iOS
against the address the app connects to. A hostname or an IPv6 literal offers a path that
neither the binding nor the certificate can serve, and the failure it produces is the worst
kind: the socket opens against something, TLS fails, and the app reports the unit as
unreachable, which is true and useless. The field the address was typed into is the only
place that could have said it can never work.

No scheme, no path, no credentials and no port either. `wss://10.0.0.2:8082` as an *address* produces a URL with two schemes;
`10.0.0.2@example.test` produces a URL whose authority is `example.test`, so the token would go
somewhere other than the entry the owner is looking at. The CSP of §2.15.1 fails that closed
rather than allowing it, which makes it a confusing failure rather than a leak, and it is still
not a shape to accept.

**The grammar is the whole rule: four decimal parts, each 0 to 255, none of them written with
a leading zero.** That last clause is the one worth explaining, because it started as a round
trip through `new URL` and ended as a regex.

`new URL` reads a part with a leading zero as octal. `010.0.0.2` becomes `8.0.0.2`, so the entry
displays one unit and the token goes to another, which is the hazard the round trip was built to
catch. But `08.0.0.2` is worse: 8 is not an octal digit, so the parser **throws** rather than
rewriting, and §4.7 promises this module repairs or drops rather than throwing. A persisted blob
carrying that address would take `loadSettings` down with it.

Refusing the spelling in the grammar closes both at once, and closes them earlier, in the shape
rather than in an exception. It also retires the round trip: with hex refused by the absence of
`x`, leading zeros refused by the grammar and every part bounded, there is nothing left that the
parser would give back differently from what it was given.

**`0.0.0.0` and `255.255.255.255` are refused by name.** Both are well-formed and neither names a
host to connect to: one is the wildcard nothing in this project ever binds, the other is the
limited broadcast address. A grammar cannot tell either from an ordinary address, so they are the
two values the shape rule cannot decide and the only ones that need naming.

**A settings blob that will not parse is replaced by the defaults, and the app still starts.**
Those defaults are the ones a first run produces, derivation included: a blob nothing can read
leaves no edit of the owner's to preserve, so the argument against re-deriving does not apply,
and the address that has just served the page is still the address that works. The alternative -
falling back to the two literals - would answer a corrupted install with the failure mode issue
#134 exists to remove, on the one occasion the owner has no saved entry to fall back to.
`localStorage` survives every upgrade the app will ever ship, so a shape written by an older
version and read by a newer one is the normal case rather than corruption. Refusing to boot over
it would strand somebody with an app that cannot be fixed from inside itself, on a device whose
only recovery path is the settings screen it will not render. Individual fields are validated the
same way: a bad port falls back to its default rather than rejecting the host.

**Derived from the origin on first run, prefilled beside it, and never discovered (issue #134).**
There is no scan. The app is served by the unit, so the address it was reached on is the one that
just worked, and that address is where the host list starts: on first run the active entry is
built from `location.hostname`, with the Bluetooth address `172.20.10.2` and the USB gadget
address `10.0.0.2` offered beside it as alternatives rather than as the starting point.

This is a decision reversed rather than a rule applied, and the reason is that the sentence above
- the address it was reached on is the one that works - argued for derivation while the code
prefilled a literal. Over a Personal Hotspot the subnet is always `172.20.10.0/28` and **the host
part comes from the phone's DHCP server** (§2.3.1, which is why `bind_addresses` accepts a CIDR
block rather than a literal). A unit that did not get `.2` served the app correctly and then could
not be connected to, from a screen that could not be edited, which is the whole of issue #134.

Three things this is not, each of which was an argument for leaving it alone:

- **The page and the socket are different ports**, and §2.15.1 already had to say that `'self'`
  does not cover the socket for exactly that reason. Same host, different port, is an assumption
  and not a fact. It is a much smaller assumption than same-subnet-different-host, which is what
  the literal assumed and got wrong.
- **Only the address is derived. The ports are not.** `location.port` is the HTTPS port and is
  therefore knowable, but nothing on the page knows the WebSocket port, and a pair where one half
  was measured and the other guessed is harder to reason about than two defaults. Both start at
  8082 and 8443 and are shown read-only as diagnostics (§4.5.1).
- **A derived address that is not an address is not used.** `location.hostname` is whatever served
  the page: a desktop dev server derives `localhost`, and a unit reached by name derives a name.
  Neither is an IPv4 literal, so neither passes the grammar above, and in that case the list falls
  back to the two prefilled entries with Bluetooth active. That is the shipped list with one
  correction: what shipped left **no** host active at all, which is the dead end issue #134 is
  named for, and a fallback that reproduces it would fix nothing on the machines the derivation
  cannot help. The derivation can only ever add an entry that would have been typed by hand.

**It is derived once and then it is a host like any other.** The entry is minted on first run,
persisted, and never re-derived: an owner who edits its label or its address keeps that edit, and
a later visit from a different address adds nothing and changes nothing. Re-deriving would undo
the owner's own correction, and on the transport where the address moves, undo it repeatedly.
Its id is the stable `origin`, for the same reason `bluetooth` and `usb` are stable.

The note that the USB path is desktop-only, because an iPhone cannot use it, is rendered by the
Settings screen (§4.5.2) and is **not** part of the default label: a label is persisted on first
run and never re-derived, so a warning living there is a warning the owner can delete by renaming
the entry.

**The list is for a client served by the unit it talks to.** A client served by unit A and
pointed at unit B is blocked by A's Content-Security-Policy, which names A's addresses and
nothing else (§2.15.1). That is the price of the directive meaning anything, and Settings should
say so where the second host is added rather than leaving somebody to discover it as a silent
failure.

**The settings blob is persisted before the token key is written.** The two are separate writes
and a browser can fail between them, so the order decides which way the pair breaks: this way
round, an interrupted switch leaves a saved active host whose token has not been written yet,
and the next load reconciles it. The other way round leaves a token written for an active host
that was never saved, which nothing later can detect, because there is nothing to compare it
against.

**Storage that refuses to answer does not stop the app.** `localStorage` throws rather than returns
when it is disabled or full, and both are reachable on the browser this ships to. A read that
throws is a blob that will not parse, which already has a rule; a write that throws leaves the
session working from memory, because refusing to switch host over a full disk helps nobody.

**A token write that fails clears the key instead of leaving it.** Swallowing the failure and
moving on is what the rule above says to do for the settings blob, and it is the wrong answer
for this one key: the value left behind belongs to the host that was active before, so the next
connection would send unit A's token to unit B, which is the failure the whole per-host rule
exists to prevent. An absent token fails authentication loudly and recoverably. A wrong one
authenticates to the wrong unit.

**A duplicate host id is dropped on the way in, keeping the first (issue #116).** The blob is
treated as hostile because anything that can run script on this origin can write it, and the
token lives in the same storage. Two hosts sharing an id is exactly the shape a hand-written or
downgraded blob produces, and it is not benign: `updateHost` rewrites every entry with the id
through a `map`, while `activeHost` and the other readers `find` the first. So editing the host
on screen writes a second record the screen never showed, **including its token**. The parse
refuses that rather than the writers defending against it one by one: an invariant enforced at
one door beats the same invariant remembered at five, and a reader that used `find` was already
assuming this was true.

First wins because the readers already agree with it -- `find` returns the first, so keeping the
first is the entry the app was already treating as real, and dropping it in favour of a later
duplicate would change which host is active for a blob that merely repeated itself.

**The module mints host ids**, from `crypto.randomUUID`, which exists because §3 makes the origin
secure. The two prefilled hosts are the exception and carry the stable ids `bluetooth` and
`usb`: they are the same two entries on every install, and a stable id is what lets a later
version recognise them. `loadSettings()` reads and reconciles, and the stores hold the defaults until it runs.

**Switching host is teardown, `resetStores()`, attach** (§4.4.2), in that order, and this
module does not perform it. It has no client to attach and no business holding one: what it
owns is the **guarantee that makes the switch safe**, which is that the new host's token is
in `companion.token` before any subscriber is told the active host changed. The sequence then
belongs to whoever holds the client, which is the app shell subscribing to `activeHost`.

The same rule extends past activation, because otherwise the key and the host record drift.
**Every path that changes which host is active, or what the active host is, reconciles the
key**, and there are more of them than activation:

- Editing the active host's token rewrites the key.
- Removing the active host clears it.
- **Loading settings reconciles it**, which is the one that is easy to miss: a reload restores
  an active host from storage while the key still holds whatever the last session left, and the
  parser can drop that host or null a dangling id, so the key can end up paired with a host
  that is no longer in the list.
- **Editing a host's address clears that host's token, on the record and not only in the
  key.** A token is issued by the unit at an address; re-pointing the entry elsewhere does not
  carry it, and sending unit A's token to unit B is the failure this whole rule exists to
  prevent. Clearing only the key leaves the value on the record, where the next load or the
  next activation writes it straight back: the key is a copy, and clearing a copy fixes
  nothing. It applies to any host and not only the active one, because an entry edited today is
  activated tomorrow, and by then nothing remembers that its address ever changed. A patch that
  sets a token in the same breath means the owner has supplied the new unit's token, and that
  wins.

A token left behind after its host is gone would be sent to whatever unit is selected next.

**An empty host list is a state, not a corruption.** Deleting the last host is reachable from
the Settings screen, so a stored blob whose `hosts` is a valid empty array is left empty.

A list that is empty **because every entry was dropped** is a different thing, and the defaults
come back: nobody chose that state, and leaving it empty strands somebody with no entry to
connect to and no prefilled one to start from, on the screen that is their only way back.

**Those defaults have Bluetooth active**, and this is not the same path as a first run: nothing
is derived here, because a blob that parses is a blob whose remaining fields were the owner's,
and the entries that fell out are not evidence about the address the page came from. But the
active id has to be one of them. Returning a list with nothing active would answer a damaged
install with precisely the dead end issue #134 exists to remove, and it is the one moment the
owner has nothing saved to fall back to. The three cases stay distinct - unparseable derives,
every-host-dropped restores with Bluetooth active, a deliberately empty list stays empty - and
none of them leaves the app with nowhere to connect except the one the owner chose.

**A host with no usable `id` or `address` is dropped; every other bad field is repaired.** Those
two have no substitute: a host with no address is not a host. A missing label falls back to the
address, a bad port to its default, which is the difference between losing one field and
losing the entry.

### 4.8 The session (`lib/session.ts`)

Three layers exist and nothing joins them. §4.3 holds a connection and asks no questions about
which unit; §4.4 writes stores and never touches a client; §4.7 decides which unit and cannot
attach one. The join is small, and it is written down because it is where the ordering rules
each of those sections states become somebody's actual responsibility.

**Nothing it does may throw out of a store subscriber.** It reacts to `activeHost` from inside
a Svelte subscription, and building a client can fail: `new WebSocket` throws on a port the
browser refuses to open and on a URL its parser rejects, and §4.7 validates the address rather
than the browser's opinion of the port. An exception there does not merely lose the connection.
It escapes the `settingsWritable.update` that produced the notification, so the settings write
aborts half done, and it leaves Svelte's subscriber queue non-empty, after which **every later
store notification in the app is silently dropped**. The whole app stops updating and nothing
says why. So the build, the attach and the connect are wrapped, and a failure becomes
connection state rather than an exception.

**It owns the switch.** §4.4.2 requires teardown, then `resetStores()`, then attach, in that
order, and says the module that performs it is whoever holds the client. That is this one. Doing
it in the wrong order leaves the previous unit's access points, peers and handshakes on screen
under the new unit's name, which is the disclosure §4.4.2 refuses on `unauthorized` arriving by
a different road.

**`loadSettings()` runs before the subscription, and since issue #134 that ordering decides
whether a socket opens.** It was already the right order, for a small reason: subscribing first
would fire once for the module's defaults and again when the load overwrote them, which was extra
work and a second place to get the ordering right. The reason is no longer small. §4.7 now has
`defaultSettings()` activate `bluetooth`, so the module's initial value names a host, and a
subscription set up before the load would build a client and open a socket to `172.20.10.2`
before storage had been read - on every start, including one whose stored settings name a
different unit. The fix that removed a dead end downstream made an ordering load-bearing
upstream, which is the kind of thing that is obvious in the sentence that says it and invisible
in the diff that causes it. A test pins the order.

**It subscribes to `activeHost` and reacts.** A host change is the event; the session tears the
old client down, resets the stores, builds a client for the new host with `wsUrlFor`, and
attaches, in that order, and calls `connect()` last so nothing the client emits during the
handshake arrives before the stores are listening.

**The token is not passed to the client.** It could be: the session holds the host record the
key was written from. But §4.3.9 makes the client re-read `companion.token` on every attempt
precisely so a corrected token takes effect, and an explicit `options.token` wins for that
client's whole life. Passing it would put a second source of truth beside the one §4.7 spent a
whole section making authoritative, and the two would agree only for as long as nobody
changes the code that keeps them in step. §4.7 guarantees the new host's token is in place before that
subscriber runs, which is what makes the rebuild safe rather than racy.

**It survives a Settings screen that changes nothing.** A host edited without changing its
address or its token is the same connection, and rebuilding it would drop a working socket to
produce an identical one. The trigger is the identity of what the connection is made of, which is
the address, the **ws** port and the token, and not the fact that the record was written.

`httpPort` is not part of it, and the distinction is the whole point of the rule rather than a
detail: that port is where the app was served from, and the app is already running. Editing it
changes nothing about the socket, so rebuilding on it would drop a working connection to produce
an identical one, which is the regression this paragraph exists to refuse.

**A host list with nothing active is a state to arrive at, not a case to skip.** Removing the
active host, or emptying the list, tears down and resets exactly as a switch does and then
attaches nothing. §4.7 already says an empty list is a state rather than a corruption; this is
what that means for the connection.

**It calls `loadSettings()`, because nothing else does.** The stores hold the defaults until it
runs (§4.7), so without this a returning owner's chosen host would be read by the tests and by
nobody else. It runs before the subscription, so the first firing already reflects what was
persisted rather than a default followed by a correction.

**The identity that decides a rebuild does not include the host id.** Activating a different
entry that carries the same address, ws port and token keeps the socket, which is right: the
connection is made of those three things and of nothing else, and two entries that agree on all
three are two names for one connection.

**Nothing else creates a client.** A view that wants to send a command asks the session for the
one that exists. Two clients means two sockets, two auth attempts and two sets of pushes into
stores that were built for one, and §4.4 already refuses the second attach loudly rather than
letting it happen quietly.

**The stop is idempotent, and it belongs to the session that handed it out.** Calling it twice
does nothing the second time, and a stop kept from an earlier session cannot close the client
of a later one: the release is tracked per closure rather than per module, because the client
is module state and a stale stop reaching it would tear down a session it never started. A
setup that throws releases the guard on its way out, so a failed start does not make every
later start fail.

**It is started once, by the app shell, and starting it twice is refused.** Not refused by
accident, downstream, where `connectStores` throws on the second attach: by then a second
client exists and a second socket is opening, and the failure names the store layer for a
mistake made here. The same answer as §4.4.2, given in the same place the mistake is: calling
it twice is a bug in the caller, and the caller is the app shell.

**It is stopped when the shell unmounts, and released when the page is hidden away (issue
#120).** The unmount is what a Svelte component can observe, and on a phone it almost never
happens: the app is backgrounded, not closed, and backgrounding unmounts nothing. So the session
also listens for the page going away.

**`pagehide` releases it and `pageshow` builds it again.** That pair is the one iOS actually
fires for a standalone PWA being suspended and resumed, and it is symmetric, which matters more
than it sounds: a listener that only tears down leaves an app that has been backgrounded once
permanently disconnected, which is a worse bug than the one being fixed.

**`pageshow` rebuilds only a session that is not running.** It fires on a cold load too, not just
on a restore, and the listener is installed before the window finishes loading -- so a rule that
rebuilds unconditionally makes every single app open build a client, connect, tear it down and
build another, paying a TLS handshake and an auth round trip over Bluetooth to arrive where it
already was. The guard is the same one `visibilitychange` uses, and it is stated as a condition on
the session rather than as a test of the event's `persisted` flag: what matters is whether there
is a live client, and a rule written against the app's own state stays true if the browser ever
changes which events it fires.

**`visibilitychange` alone does not release it.** Hiding is not leaving. The notification shade,
the app switcher, a phone call and a glance at a message all hide the document for a few seconds,
and a socket torn down and rebuilt on each of those costs a TLS handshake and an auth round trip
on a Bluetooth link, to save nothing: the socket was idle. Only the transition the platform uses
for suspension releases it. A `visibilitychange` back to visible **does** restart a session that
is not running, because that is the cheap half and it catches the case where the release happened
by some path this rule did not predict.

**Releasing is re-entrant-safe, and this is not hypothetical.** Tearing a session down closes its
socket, and a close handler and a page-lifecycle event can land in the same tick -- so a
`pagehide` can arrive while a release is already on the stack, which calls release again, which
closes again. Without a guard that is unbounded recursion, and it is what a test written for the
overlap actually produced: `RangeError: Maximum call stack size exceeded`, from inside an event
listener, with every assertion in the suite still green because the throw was unhandled rather
than failing a test. A release entered while a release is in progress returns immediately.

The same holds for the resume side, for the same reason and with less drama: rebuilding tears the
previous session down first, so the two share the path that can re-enter.

**The socket may already be gone by the time the listener runs**, and that is not an error. iOS
suspends the process; the connection dies with it or shortly after, and `pagehide` may fire after
the fact. Releasing an already-dead session must be a no-op rather than a throw, for the reason
the paragraph above gives about throwing out of a subscriber.

## 5. CI/CD (`.github/workflows/`)

### 5.1 `ci.yml` (push + PR)

- **plugin-lint**: `python -m ruff check plugin/ tools/ tests/ .github/*.py`, then
  `python -m compileall plugin/`. `ruff.toml` enables `E` and `F` and argues for that pair: a
  formatter is not wanted here and a style rule that fires on prose in a docstring costs more
  attention than it saves.

  This bullet described a different script for an unknown length of time (issue #201). SPEC named
  `.github/check_plugin.py`, an AST-based check that parsed the plugin without importing it,
  verified the `plugins.Plugin` subclass and the metadata, and grepped for secrets. It does not
  exist and it is not what CI runs. A specification naming a gate nobody can run is worse than
  one saying nothing, because the next person writing a gate reads it as already covered, which
  is why §1.1 now has a test behind it rather than a promise.

  What that script claimed to check is covered elsewhere, and the audit that established this is
  worth keeping rather than repeating: the secret grep is `.github/check_secrets.py` as its own
  job and does the job better; `__version__` is asserted against `package.json` by
  `tests/test_release_workflow.py`; the `plugins.Plugin` subclass is covered in effect, because
  the suite imports the plugin against `tests/fakes/pwnagotchi_stub/` and would fail if it
  stopped being one. `__author__` and `__license__` were covered by nothing, which is why they
  now have a test of their own. `__license__` is pinned to the literal `GPL3` because §2.1
  requires the plugin to declare it, and this is a GPL fork whose licence declaration is the
  thing a downstream reader checks first. The header comment crediting `BraedenP232/PwnIOS` is a
  separate requirement of §2.1 and a separate string; neither stands in for the other.
- **plugin-tests**: `pytest` on **Python 3.13**, the version the device ships (F26), and again on
  **3.11**, the floor upstream declares (F21). The device's version is the one that blocks a merge;
  the floor runs because somebody can be on an older image. No pwnagotchi installation required:
  the fake package in `tests/fakes/pwnagotchi_stub/` stands in. The coverage gate of §10.7 runs here and is what
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
- **frontend**: `npm ci`, `svelte-check`, `tsc --noEmit`, `vitest run`,
  `check_coverage.py frontend`, `npm run build`, and last `check_no_inline_script.py` over
  the built page (§2.15.1), which is last because it reads what the build produced.
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
  is the secret. Its outcome is three-valued and every pattern in it is probed in both
  directions (§5.1.2). Note what this job **cannot** do: the owner-specific infrastructure denylist
  lives outside the repository by design (§13), so that check is a local pre-commit gate only.
  CI catches secrets that look like secrets to anyone; it cannot catch a hostname it is not
  allowed to know, and a green run is not proof a diff is safe to publish.
- **no coverage exclusions**: `# pragma: no cover` is banned outright. The 85% floor only means
  something if nobody can opt out of it.
- **exit status of the ban scans**: each reads `git grep` in three cases rather than two. A match
  fails the job, exit 1 is the clean result, and anything else fails the job as a scan that did
  not run (§13). Without that, a pattern that stops compiling turns its own ban off and the job
  stays green. The rule is not about `git grep`: it reaches every gate whose clean verdict is an
  absence, and §5.1.2 applies it to the secret scan, which used to reach a file it could not
  decode and exit 0 anyway.
- **mutation** (scheduled, not a PR gate): `mutmut` over `plugin/`, reported not enforced.
  Mutation testing is too slow to block a pull request and too valuable to skip entirely.

#### 5.1.1 The Node floor is one number in three files, and two of them are pins (issue #132)

`frontend/package.json` declares `engines.node`. `ci.yml` and `release.yml` each declare a
`NODE_VERSION`, because a workflow cannot read another's `env` (§5.2). Three spellings of one
constraint, and the same argument §5.2 already makes about the Python pair applies here with one
extra file in it.

**The floor and the pins are not the same kind of number, and the check between them cannot
pretend they are.** `engines.node` is a lower bound with three components, `>=X.Y.Z`;
`NODE_VERSION` is what `actions/setup-node` resolves, and it is a **major alone**, meaning "the
latest of that major the runner can get". So the check is that each workflow's pinned major is
the floor's major, asserted against `package.json` separately for each of the two rather than
only between them -- two pins that drift to the same wrong major still agree with each other,
which is the one case comparing them to each other cannot see. It **cannot** establish that the
runner's resolved version is at or above the floor's minor, because that depends on what
`setup-node` returns at the moment the job runs, and a test claiming otherwise would be
asserting something it did not check.

**That gap is closed by the install rather than by the check, and it is worth saying where.**
`engine-strict=true` below applies to the CI job's own `npm ci` exactly as it applies to a
contributor's, so a runner resolving a version under the floor fails at install with
`EBADENGINE` instead of proceeding to a suite that would die inside `undici`. The static check
answers "do the three files agree"; the install answers "is what actually turned up good
enough". Two different questions, and neither is a substitute for the other: the install cannot
notice that a workflow's pin has drifted to another major, because whatever it resolves will
satisfy a floor from that other major just as well.

**This section names shapes and not values, deliberately.** The floor's digits are **authored**
in `engines.node` and nowhere else, including here: a specification that quotes today's number is
a fourth spelling of it, and one that no test compares, which is the defect this section exists to
close arriving in the place that forbade it.

Authored is the load-bearing word. `frontend/package-lock.json` carries the same digits, because
npm writes the `engines` field into it, and that is not a second spelling: nothing edits the lock
file by hand, `npm ci` regenerates the relationship, and a lock file disagreeing with its
`package.json` is a state npm itself refuses. The test is that a human wrote the number once.

**Why there is a floor at all, recorded once so nobody measures it twice.** `jsdom` brings
`undici`, which calls `webidl.util.markAsUncloneable`. The Node 20 line does not have it, and on
20 the vitest workers do not start: the whole frontend suite dies before a single assertion, with
an error naming `undici` and naming neither Node nor a version. Observed on Node 20.19.2 with npm
9.2.0 on 2026-08-30, and by issue #132 on the same line before that. Those are dated
observations, which §11 permits because they age into history rather than into being wrong; what
must hold today is the range in `engines.node`.

**`frontend/.npmrc` sets `engine-strict=true`**, so `npm install` refuses rather than warning.
`engines` is advisory by default and `EBADENGINE` scrolls past with the rest of the install
output. The judgement issue #132 asked for is that this is safe **because the file is in
`frontend/`**: it governs an install of the frontend's own dependencies, which is by definition
frontend work, and it cannot block a contributor who only touches the plugin, the tools or the
tests. A repository-root `.npmrc` would have been the wrong call for exactly that reason.

**It reaches one thing that is not a contributor and not CI, and that is written down here
because the failure would be invisible.** Dependabot's npm updater reads a repository's committed
`.npmrc` and honours `engine-strict`; an updater running below the floor fails the update with
`EBADENGINE` and no pull request appears. There is no configuration option to make it ignore the
file, and the upstream issue asking for one was closed as not planned. The updater's own Node
version is pinned in `dependabot-core` and is not a version this repository can set or read: it
was Node 24 when this was written, comfortably above the floor, and it has been on the Node 20
line within the last year, which is below it.

So the hazard is not present today and is not hypothetical either, and its symptom is the
dangerous part: **an update that does not happen looks exactly like a week with no updates.**
There is nothing red to notice. It is kept rather than traded away because what it buys -- an
install that refuses instead of warning -- is the whole of what issue #132 asked for, and the
alternative on offer is to relax the floor to whatever the updater happens to run, which is the
floor being set by the least relevant consumer of it. Issue #202 carries the watch.

#### 5.1.2 The secret scan has three outcomes, and there were files it never opened (issue #199)

The scanner holds ten patterns as this is written, and issue #199's title says nine, which is
the whole argument against writing the count anywhere a reader could take it for a fact. Two of
the ten had tests, both arriving with the pair issue #132 needed, and a green run therefore said
that ten patterns found nothing while saying nothing at all about whether any of them could
still find anything. That is the shape §13's rule 6 names one level up and it applies here
unchanged: a zero-hit result is not evidence unless something proved the matcher alive.

**Every pattern is probed, in both directions.** Each entry in `PATTERNS` fires on a synthetic
probe written for it by hand, and does not fire on the placeholder shapes its own comment
tolerates: `token = ""`, `password = <redacted>`, `api_key = "your-key-here"`. The test
enumerates `PATTERNS` itself rather than a copy of the list and fails on a label it has no probe
for, so an eleventh pattern arrives with its tests or does not arrive, and no count has to be
kept true in two places. The probes are composed for the test and are never drawn from real
credential material: a fixture that is a working token is the leak the gate exists to prevent. A
pattern edited into uselessness, or dropped in a merge, now fails a test instead of quietly
widening the tree.

**A file that was skipped is not a file that was clean.** The scanner read every path as UTF-8
and, on a `UnicodeDecodeError`, printed a warning and carried on to exit 0. Nine tracked files
decode as nothing of the sort -- four icons, a banner, four capture fixtures -- so key material
pasted into any of them produced a warning line and a clean verdict, which is precisely the
outcome the script's own comment at that `except` says it exists to prevent. It prevented the
silence and not the pass.

The fix is to stop treating decoding as a precondition for scanning. The bytes are read once and
decoded as `latin-1`, which is total -- every one of the 256 byte values maps to a character, so
no input can fail it -- and which leaves the ASCII range fixed, and ASCII is the alphabet every
one of the patterns is written in. A credential that lies in a `.png` as bytes is recognisable
for the same reason it is in a `.py`. Line numbers still count `\n`, so what is reported is still
a place a person can go and look.

What this buys is exactly the bytes as they lie in the file, and the section says so rather than
letting the next reader take more. A credential inside a deflate stream, a PNG `zTXt` chunk, an
archive member or a UTF-16 encoding is still invisible to every pattern, and those files now
count towards the number in the clean verdict. So the count means "these files had the patterns
run over their bytes", not "the content of these files was examined". That was equally true
before -- UTF-16 decoded as UTF-8 with a NUL between every character and matched nothing either
-- and it is written down here because this is the section arguing about not calling unexamined
things clean, and a sentence in it that overstates the reach is worse placed than anywhere else. This was measured before it was chosen rather than
assumed to be safe: on 2026-08-30 the tree held 208 tracked files, of which the scanner examines
207 because it exempts itself, and decoding every one of them and matching every pattern produced
zero hits. Closing the hole cost no false positive on the tree as it stood.

**Three outcomes, not two.** `0` is clean, `1` is at least one finding, and `2` is a scan that
did not run. Exit `2` covers two cases and both were exit `0` before:

- **A path that could not be read.** An `OSError` leaves a file unexamined, and an unexamined
  file is the thing this rule refuses to call clean. It is reported by name and the run fails.
  This covers reaching the file as well as reading it: `Path.is_file()` answers `False` for a
  file whose parent directory cannot be traversed, because it swallows the `OSError` underneath,
  so a check written on it drops exactly the unreadable file it was meant to catch and drops it
  one line before the `except` that would have caught it. The path is stat'ed inside the `try`
  for that reason, and the quiet skip is left only for a path that stats cleanly and is not a
  regular file.
- **A run that considered nothing at all.** `check_secrets.py <a path that is not a file>`
  walked zero files and printed `secret scan: 0 file(s), no credential material found.` A
  verdict earned by examining nothing is the vacuity fail-open in its plainest form, and it
  matters more once the hook of issue #203 passes the scanner a list of staged paths, because
  there the argument list is computed rather than typed.

  **Considered is not the same as scanned, and the difference is what keeps this usable.** The
  scanner exempts itself and refuses some files by name without reading them, and neither is a
  file it failed to look at: it is a file it looked at and decided about. A commit that stages
  only `.github/check_secrets.py` is an ordinary commit, and the first version of this rule
  answered it with "the gate is broken", which is how a gate teaches people to pass `--no-verify`.
  So a self-exempt path and a by-name refusal both count as considered, and exit `2` is reserved
  for a run in which nothing was scanned, nothing was refused and nothing was exempt. A path that
  A run that considered paths and scanned none of them because all of them were exempt says
  exactly that and exits `0`, and it names any path it was handed and did not scan, because a
  path that stats cleanly and is not a regular file was the last place in the file where
  something passed through leaving no trace. A sentence containing the word "all" has to be able
  to survive the reader checking it against the argument list. It does not borrow the wording of a scan that looked: a count of
  zero beside "no credential material found" is a verdict the run did not earn, and this is the
  section that argues against writing one. A path that is not there is not one of the three
  either, and it fails the run deliberately: the hook passes
  paths it staged as added or modified, and a name in that list with nothing behind it is a
  broken gate rather than a quiet day. It fails it as an unreachable path, named, rather than by
  falling through to the count being zero, because the two sentences a reader needs are which
  path and what was wrong with it.

When a run has both -- something unreadable and something found -- the status is `2` and the
findings are still printed. A run that could not read everything cannot vouch for its own list
being complete either, so the weaker claim is the one the exit status makes, and the findings are
reported anyway because they are true whatever else went wrong.

A finding outranks the vacuity check, and that ordering is load-bearing rather than incidental:
a run over nothing but by-name refusals scans no bytes at all, and it has to exit `1` for the
refusal rather than `2` for having scanned nothing.

The separation of `1` from `2` is not decoration. A caller has to be able to tell "this diff
carries a credential" from "this gate is broken", and the first is a finding to act on while the
second is a gate to repair before anything is trusted.

§13's survey of rule 6 concluded that this script "already read the status of what they call and
needed no change". That was a statement about the exit status of the `git ls-files` it runs, and
it was correct about that. It was silent about what the script then does with the files that
command names, which is where both of the failures above lived.

**The refusals by name are pinned too.** `FORBIDDEN_SUFFIXES` and `FORBIDDEN_NAMES` refuse a
file whatever its content, and the one exemption is exactly one path prefix:
`tests/fakes/fixtures/`. A `.pcap` inside it passes and the same file one directory up does not,
which is the difference between an exemption for the fixtures the handshake listing cannot be
tested without and an exemption for anything ending in the right characters.

That prefix is measured from the repository root and from nowhere else, which is what makes it
safe to hand the scanner a computed list of paths. A caller's working directory does not enter
into it, and a directory named `tests/fakes/fixtures/` somewhere outside this repository is not
the exempt one: a path that does not sit under the root is compared whole and matches no prefix.
So issue #203's hook, whose staged paths are by construction inside the repository, gets the
same exemption for the same files as the tracked-tree walk does, and gets it for nothing else.

**A test for a pattern cannot spell the pattern out.** `tests/test_check_secrets.py` is itself a
tracked file, and one of its own tests runs the scanner over the tracked tree, so a probe written
as a contiguous literal makes the suite fail on its own source. The probes are assembled at run
time from separate pieces instead.

That rule is enforced by a test that reads this module's own source and refuses a probe that
appears in it whole, and not by the tree test, which cannot do it. The security audit of this
change is what established the difference. Eight probes for the `npm registry credential` pattern
were written contiguously and the tree test passed over them anyway, because that pattern is
anchored to the start of a line and the opening quote of the Python string sits between the
indentation and the key: the pattern matches the probe when handed the probe, and does not match
it when reading the file the probe lives in. So the tree test only enforces the rule for a probe
the patterns would catch in place, which is to say it enforces it wherever it is not needed.

The distinction matters beyond tidiness. A probe no pattern catches in its contiguous form sits
in the file in one piece and nothing goes red, and that is precisely the shape a real credential
would take if one were ever pasted into this module, which is the one file in the repository
whose subject is credential-shaped strings. Widening the anchor was measured and rejected: it
catches a probe in a list literal and a key in a markdown code span, misses `x = "_authToken=..."`
which is the likelier paste, and turns a documentation line that begins with a backtick into a
false positive. Half a fix bought with a false positive is worse than the honest limit the
pattern's own comment already declares, so the limit stands and the discipline is enforced where
it can be enforced for every pattern at once, including the ones added after this was written.

Two behaviours are deliberately unchanged, and are pinned so that a later edit has to argue with
a test rather than with nobody. The script exempts itself, because it necessarily contains the
patterns it looks for. Matched text is never echoed in a finding, because the match is the
secret; a finding is a path, a line number and a label, and that is enough to go and look.

**A credential is not always at the start of a line (issue #211).** The npm pattern is anchored to
column 0, which is a deliberate concession stated in its own comment: it spares prose that writes
`_authToken=<your token>` mid-sentence. #199 measured how wide the concession really is, and it is
wider than the `#` case that comment names -- `    "_authToken=VALUE",` inside a list literal,
`probe = "_authToken=VALUE"`, and anything else with a character before the key all escape it,
and `credential assignment` does not cover them either because it requires the quote to follow the
`=` and here it precedes the key.

Widening the anchor to tolerate leading quotes was tried during #199 and rejected on measurement:
it catches the list-literal form, still misses `x = "_authToken=..."`, which is the likelier
paste, and turns any documentation line starting with a backtick into a false positive. Half a fix
bought with a false positive is worse than an honest limit.

**So the second pattern is about the value, not the position.** It matches the key anywhere on the
line and requires what follows to look like a credential rather than a placeholder: a run of at
least sixteen characters from the alphabet real tokens use, with no spaces, no angle brackets, and
none of the words a document uses to mean "put yours here". `_authToken=<your token>` does not
match, because a placeholder announces itself; a quoted assignment carrying sixteen or more
token characters does, because a real token cannot announce itself. The anchored pattern stays as
it is -- it catches the column-0 case with no value heuristic at all, and two patterns with
different failure modes are the point.

**The placeholder exclusion is about the whole value, not about a word boundary.** The first
attempt anchored the excluded words with `\b`, which holds against a hyphen and not against an
underscore -- and the underscore is inside the token alphabet, so `YOUR_TOKEN_HERE_XXXX` fired
while `your-token-here-xxxx` did not. `YOUR_TOKEN_HERE` is the commonest documentation
placeholder there is, which makes that the exact false positive §5.1.2 gives as its reason for
rejecting the widened anchor, arrived at from the other direction. So the rule is: **a value
containing any of the placeholder words anywhere in it is not a credential.** A real token is
random and will not contain `changeme`; a document that means "put yours here" almost always
says so in the value. The false negative that buys -- a genuine secret that happens to spell one
of those words inside itself -- is the cheaper mistake, because the other one turns the scanner
off.

**A colon is an assignment too.** All three patterns require `=`, so a credential written the way
JSON, YAML and a JavaScript object literal write it -- `{"_authToken": "..."}`, `_authToken:
Ab3x...` -- escapes every one of them unless it happens to sit at column 0. That is the honest
answer to "can it be missed by trivial reformatting", and it is the form a token most often
arrives in, because it is the form a config file and a paste from a lockfile take. The
value-shaped pattern accepts `=` or `:` between the key and the value, with **an optional quote on
either side of the separator**: in these formats the value is quoted, and in JSON the key is
quoted too, so `"_authToken": "..."` puts one quote before the colon and another after it. The
first attempt allowed only the second quote and still missed the JSON form -- the very form this
paragraph was written for -- which was found by running the pattern against the shapes rather than
by reading it. A scanner is the one thing in this repository where a rule and its measurement
must not be separated: it is the tool the audits are conducted with. Half the fix is worse than none here: it would make the section
claim the JSON form is covered while it is not. The anchored one does not change: it is a
deliberate, documented concession and widening it is what §5.1.2 already refused.

The neighbouring `credential assignment` pattern misses these too, for a related reason -- it
opens with `\b`, and a leading underscore is not a word boundary, so `_authToken` defeats it
before the value is ever considered. That is pre-existing and it is not this pattern's job to
compensate: two patterns covering for each other's blind spots is how both end up untested.

`none`, `null`, `true` and `false` come out of the list. They cannot be reached: the shortest is
four characters against a sixteen-character floor, so as alternatives they were dead the moment
the floor was written, and a dead alternative in a list of live ones is read as a case somebody
considered.

**Both are probed in both directions, like every other pattern**, and the probe for the new one
carries a value that is credential-shaped rather than a word, or it would be testing the key and
not the rule. **Every excluded word gets its own probe**, shaped so it passes the length and
alphabet tests and is refused only by the exclusion: a probe that fails on shape first proves
nothing about the word list, and a word list with one probe standing for all of it is a list
where deleting eight entries is invisible.

### 5.2 `release.yml` (on tag `v*`)

Build the frontend, pack `frontend/dist` into `dist.tgz`, create a GitHub Release for the tag and
attach the archive and a `SHA256SUMS` asset. `tools/install-on-pi.sh` downloads the latest
release's assets into `web_root` (§5.3).

This is the half of the delivery path that was specified, referenced from four other sections and
installed against, and then never written (issue #128). The installer has always been finished:
it downloads `dist.tgz`, downloads `SHA256SUMS`, and refuses an archive whose digest does not
match. Tagging produced nothing for it to download, and nothing failed, because no gate asked
whether the workflows this document names exist.

**The trigger is a pushed tag matching `v*` and nothing else.** No `workflow_dispatch`: a release
is a tag, and a second way to produce one is a second way to produce an asset nobody can point at
a commit.

**The tag must be an ancestor of `master`.** A tag can be pushed to any commit, including one on a
branch that never opened a pull request, and the ruleset that guards `master` says nothing about
tags. The job fails, loudly, naming the commit.

**What that check is, and what it is not.** It catches mistagging - a tag on the wrong commit, a
tag on a branch that was never merged - which is the case that actually happens. It is **not** a
guarantee that everything published passed review, and the sentence that said so was wrong: GitHub
loads the workflow from the tagged ref, so a tag pushed to a commit whose `release.yml` has no
ancestry step runs no ancestry step. A workflow cannot bind a tag that carries its own copy of it.
The guarantee needs a tag protection ruleset, which lives in repository settings where nothing here
can assert it, and that is issue #181.

**The check fails closed, and says which failure it was.** `git merge-base --is-ancestor` exits 1
for "not an ancestor" and 128 for a question it could not answer - an unresolvable `origin/master`,
a missing object. Both must refuse the release, per §13, and they must not be reported as the same
thing: the moment an operator most needs to know the check could not run is the moment a plain
`if !` tells them their tag is bad instead.

**The three version strings must agree with the tag**, and this is where §2.1's claim that "CI
asserts it equals the release tag" becomes true. `plugin/companion.py`'s `__version__`,
`frontend/package.json`'s `version`, and the tag with its leading `v` removed are compared, and
any disagreement fails the job before anything is built or published. A release whose plugin
reports a version other than its own tag is a support question nobody can answer: the unit says
one number, the Release page says another, and `capabilities.pluginVersion` puts the first one on
the wire.

**What the coverage gate does not see.** `.github/check_release_version.py` sits outside
`--cov=plugin`, as `check_pinned_facts.py` and `check_coverage.py` do, so the 85% floor says
nothing about it. The argument for extracting it was that the part which computes goes where a test
can reach it, and that is what happened - the gate that watches it is `tests/tools/test_check_release_version.py`,
written by hand, and not the coverage floor. Worth stating so that "extracted so the gate can see
it" is not read as a claim about the floor.

**The tag is read by a script, not by the workflow.** The three-way version comparison and the
pre-release decision are the only logic this workflow contains, and logic inside a `run:` block is
logic no test can reach: a test can assert that a `case` statement mentioning the tag sits near
the word `prerelease`, and that assertion passes just as happily when the condition is **inverted**
and every stable release is published as a candidate. That mutant was found and survived, which is
the whole argument. The rule is the one §4.5.1.1 states for views and §10.7 states for coverage,
in a third place: the part that computes goes where the gate can see it. The workflow calls the
script, fails when it fails, and reads its answer.

**A tag carrying a pre-release suffix marks the Release as a pre-release.** §12 allows `-rcN`; a
release candidate that appears as the latest stable release is one an installer picks up by
default, and `install-on-pi.sh` asks for the latest release rather than for a version.

**The order of the refusals is part of the rule.** The grammar is checked first, then the
versions, then the ancestry, then the existing Release. A step that interpolates the tag into a
URL before anything has decided the tag is a version is a step trusting a value nobody has looked
at, and the script's own claim to validate "before anything else" is only true if the workflow
calls it first.

**The tag is validated against §12's grammar before anything else.** `vMAJOR.MINOR.PATCH`, with an
optional pre-release suffix introduced by a hyphen, and nothing else. The trigger is `v*`, which
matches `v0.1.0.4` and `vwip` as happily as a version, and a tag outside the grammar would publish
a Release whose name means nothing under §12's own rules. Any hyphenated suffix counts as a
pre-release, not only `-rcN`: erring towards pre-release is the safe direction, since the cost is a
release nobody auto-installs rather than a candidate everybody does.

**The archive layout is `tar -czf dist.tgz -C frontend/dist .`**, so `index.html` sits at the
archive root and not under a `dist/` prefix. The installer unpacks into `web_root` directly and a
prefix would bury the app one directory below where the static server looks (§2.15).

**`SHA256SUMS` names the archive without a path.** The installer matches on the file name and
refuses an entry that is path-qualified, because `other/dist.tgz` describes a different file and
letting it satisfy a bare `dist.tgz` is how a sums file for one build blesses another. Generate it
in the directory holding the archive.

**An existing Release for the tag is a failure, not something to overwrite.** Re-running a
workflow that replaces an asset in place means a `SHA256SUMS` someone already downloaded now
describes a file that no longer exists, and the failure it produces looks like corruption or an
attack rather than like a rebuild. If a release has to be redone, the tag is the thing to move,
deliberately, by hand.

**Absent is not the same as unanswerable here either.** The check asks whether a Release exists,
and a query that fails on a 401, a 5xx or a rate limit has not answered no. Reading any failure as
"no release exists" is the §13 mistake in the one step whose entire purpose is to refuse.

**Permissions are `contents: write` and nothing else**, declared on the job, with the workflow
itself declaring `permissions: {}` so that a job added later inherits nothing rather than the
repository default. The grant has to be written down to exist.

**The build and the publish are two jobs, and only one of them holds the token.** `npm ci` runs
lifecycle scripts from the whole transitive dependency tree. A job that does that and later calls
`gh release create` hands those scripts a way to reach the token even when the token is only in a
later step's `env:`: every step can write `$GITHUB_PATH` and `$GITHUB_ENV`, so a lifecycle script
can plant a `gh` earlier on the path and read the token when the publish step runs it. Splitting
is what removes the reachability rather than narrowing it: the build job runs third-party code
with no write anywhere in its token and uploads the archive as an artefact, and the publish job
holds `contents: write`, downloads that artefact, and runs nothing it did not write.

The build job's grant is `contents: read` rather than `permissions: {}`, and the difference is
worth a sentence because the second reads stronger. It is not, here: this repository is public, so
`contents: read` is what an anonymous clone already has, and the property being bought - that a
lifecycle script cannot reach a token able to write - is bought identically by both. What `{}`
would additionally cost is certainty: whether `actions/checkout` succeeds with a zero-permission
token is not something this repository can find out before a tag is pushed, and the one place a
guess must not be made is the workflow whose first execution is a release.

**The build job caches nothing.** A dependency cache is restored from a key any branch of this
repository can populate, which is a write-influenced input to the one job the split exists to
isolate. `npm ci` verifies every package against the committed lockfile, so the exposure is small
and the saving is a minute once per tag - the wrong side of that trade for the job that produces
what people install.

The checks that decide whether a release may happen at all - the tag grammar, the version
agreement, the ancestry, the absence of an existing Release - belong before the build, because a
release that must not happen should cost nothing and because a failure there is about the tag
rather than about the code.

**Neither checkout leaves a token in the working tree.** `actions/checkout` persists the
`GITHUB_TOKEN` into `.git/config` by default, and a persisted credential is reachable by anything
running in that directory afterwards. The split already means no job both runs third-party code and
holds a write scope, so this is the second layer rather than the first: `persist-credentials: false`
on both checkouts, so that neither the job that reads the tag nor the job that runs `npm ci` leaves
a credential on disk for the next thing to find. It costs nothing - the git operations either job
performs are reads of a public repository.

**The publish job has no checkout, so it must be told which repository it is publishing to.** `gh`
resolves the repository from the git remote, and a job with no working tree has none; it reads
`GH_REPO` and not `GITHUB_REPOSITORY`, so the value has to be passed deliberately. This is the kind
of thing that only fails on a real tag, which is where the whole of §5.2's caution comes from.

**Nothing reaches a `run:` block by expression interpolation.** `${{ }}` inside a shell body is
substituted before the shell sees it, so a value containing shell syntax becomes shell. Values
arrive through `env:` and are read as variables. That is true even of values this repository
generates itself: the pre-release flag is a literal `true` or `false` written by
`check_release_version.py`, which makes the interpolation harmless today and makes it a
constraint nobody records at the call site, which is how it stops being true.

**The toolchain versions are CI's, and something checks that they are.** `release.yml` declares
its own `PYTHON_VERSION` and `NODE_VERSION` because a workflow cannot read another's `env`, so the
numbers exist in two files and a test compares them. Two spellings of one version with nothing
comparing them is the defect issue #151 was about, arriving in a fourth place.

**The build is `npm ci` and `npm run build`, and not a second run of CI's Frontend job.** The lint,
the typecheck, the unit tests and the coverage gate ran on the way to `master`, and the ancestry
rule above is what makes that sentence true rather than hopeful: a tag that is not on `master` is
refused, so a tag that is accepted names a commit those gates have already passed. Re-running them
here would buy a second opinion on the same commit and would put the release's success at the mercy
of a flake in a check that has already answered.

The cost of that reasoning is that it is only as good as the ancestry rule, which is why the
ancestry rule is checked and not assumed, and why it fails loudly.

### 5.3 `tools/install-on-pi.sh`

    install-on-pi.sh [--web-root DIR] [--plugins-dir DIR] [--config FILE]
                     [--tag vX.Y.Z] [--archive FILE] [--repo owner/name]
                     [--plugin-only] [--web-only] [--dry-run] [--pwn-prefix DIR]

Runs **on the Pi**, as root. POSIX `sh`, `set -eu`, and nothing beyond `curl`, `tar` and
`sha256sum`. Non-interactive: it never prompts, so it is the same invocation by hand, from a
test and from a future CI job.

**It installs both halves, not just the web root.** §14 step 10 says to run this and then test
end to end from the phone, which cannot work if the plugin is not on the unit: a script that
installs the PWA and leaves `companion.py` to a manual `scp` is a script that produces a working
web server serving an app with nothing to talk to. So:

1. `plugin/companion.py` into the custom plugins directory. That directory is **resolved, never
   hardcoded, and the fallback is resolved too**: read `main.custom_plugins` from `--config`
   (default `/etc/pwnagotchi/config.toml`, F24), and when the user configuration does not set it,
   read the same key from the `defaults.toml` **of the pwnagotchi installed on this unit** rather
   than from a copy of its value (§5.3.1). The script runs on the unit, so the authoritative
   answer is under its hand and there is no reason to guess.
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
When the file is absent, or the key is not present in any of those forms, fall back to the
unit's own `defaults.toml` as described below, and say which path was chosen and why on stdout.
`--plugins-dir` overrides everything.

**The fallback is read, not remembered (issue #157).** Earlier versions of this script carried
`/usr/local/share/pwnagotchi/custom-plugins/` as a literal, which was upstream's default at
2.9.5.6. Upstream moved it to `/etc/pwnagotchi/custom-plugins/` at 2.9.5.8, and on any newer unit
whose configuration does not set the key the script would have written the plugin to a directory
pwnagotchi no longer reads, reported success, and left the owner with a plugin that never loads.
Nobody had seen it because it only fires when the user configuration is silent, and a unit
whose configuration names the key never reaches the fallback at all. That is the shape of a
defect that waits for somebody else.

The script runs on the unit as root, so the value it wants is on the disk in front of it. It
resolves the fallback by reading `custom_plugins` out of the installed package's own
`defaults.toml`, found under the image's Python tree (F26), with the **same parser** the user
configuration goes through: one implementation, so the two files cannot be read by two sets of
rules that drift apart. The resolution order is therefore:

1. `--plugins-dir`, which overrides everything and is how a unit with an unusual layout is
   handled without teaching the script about it.
2. `main.custom_plugins` in `--config`.
3. `custom_plugins` in the installed `defaults.toml`.
4. **No fallback after that.** If none of the three answers, the script fails, names all three
   places it looked, and points at `--plugins-dir`. It does not install to a guess. A wrong
   directory here is invisible: every file is written, every permission is set, the script exits
   0, and the plugin simply never loads. Refusing is louder than that and no more inconvenient,
   because the fix is one flag.

**More than one Python tree under `/opt/.pwn/` is refused, not resolved.** It means a unit in a
state this script has not seen, most plausibly a partial upgrade, and choosing between the trees
would put back the silent wrong answer the whole change exists to remove. The script lists every
`defaults.toml` it found and stops. This is the same judgement as the point above and is written
down separately because it is the one case where the script has an answer available and declines
to use it, which a later reader would otherwise be tempted to tidy away.

**`--pwn-prefix DIR` names the tree, defaulting to `/opt/.pwn`.** It is a **seam, documented
rather than hidden**, and calling it anything else would be dressing it up: F26 pins `/opt/.pwn`
as hardcoded in the image's own `01-motd`, `profile`, `sudoers` and `pwnagotchi-launcher`, so a
unit whose tree is elsewhere is not an image this document describes, and `--plugins-dir` already
answers that unit in one flag. What the seam buys is that the third resolution step, which is the
whole of the fix, can be exercised by an ordinary test run. Without it that step is reachable only
by a test that needs root and writes into the host's real `/opt/.pwn`, and a behaviour covered
only by a test most runs skip is not covered. A documented flag beats an undocumented environment
variable for the same job, so it is in `usage()` and in `docs/SETUP.md` with the others.

It is validated the way `--web-root` is, as an absolute path, and it is interpolated into a glob
rather than into a URL, so the reasoning behind `--tag`'s character check does not apply: a
strange value here fails to match and the script refuses, which is the behaviour it would have
had anyway. **Whitespace is the exception and is rejected outright.** The value feeds an unquoted
glob and a space-separated accumulator that counts the matches, so a prefix containing any of the
default `IFS` characters, space, tab or **newline**, word-splits and the count is wrong. The
first version of the guard listed space and tab and let a newline through, which is the same
mistake in miniature: enumerating the cases you thought of instead of naming the class. It fails
closed, which is exactly why nothing caught it for a round. Refusing outright is a usage error
the caller can read, where the alternative is an `IFS` dance in two places in POSIX `sh` to
support a path nobody has.

**This costs the fallback its version independence and that is the point.** The script no longer
knows or cares what upstream's default is, on any version, which is what makes F23's literal
something the drift check reports rather than something the installer depends on.

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
where the resolution below landed without installing anything.

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
| CI | `ci.yml`, `release.yml`, `ruff`, schema validation, protocol type sync, secret scan |
| Docs | `SETUP.md`, `CERTIFICATES.md`, `PROTOCOL.md`, README, CONTRIBUTING, issue templates |

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
The milestones are the roadmap (§8).

### 6.4 Issue templates, CONTRIBUTING, SECURITY

`bug_report.yml` (device, pwnagotchi version, plugin version, iOS version, cert setup, logs),
`feature_request.yml`. `CONTRIBUTING.md` covers the English-only rule, the AST/parse CI rules,
the schema-first protocol workflow, and the requirement that changes ship with tests. It said
"the AST/parse CI rules" until issue #201: that was `check_plugin.py`, the only AST check this
repository ever described, and CONTRIBUTING.md never carried such a rule to begin with.
`SECURITY.md` states the threat model (tether-local exposure, private CA, optional token) and
the hard rule: no private keys, CA keys, tokens, or pwnagotchi whitelists in the repository,
ever.

---

## 7. README (top level)

What it is (free PWA companion, self-hosted), a screenshot placeholder, the honest security
model (a private CA and a fully trusted profile are required, and why), a feature matrix against
the paid app (parity plus the three gaps plus GPS/map and mirror), a quickstart pointing at
`docs/SETUP.md`, the each-user-builds-their-own note, a link to the GitHub milestones as the
roadmap (§8), and GPL-3.0 with attribution to `BraedenP232/PwnIOS`.

---

## 8. The roadmap is the milestones

The roadmap is the §6.2 milestones on GitHub, each with its issues, and it is not a tracked file.

SPEC declared a `ROADMAP.md` carrying a narrative of those milestones and it was never written
(issue #201). Writing it now would put the milestone list in two places, one of which updates
when an issue is closed and one of which updates when somebody remembers, and a roadmap that
disagrees with the issue tracker is read as the tracker being wrong. So the file is gone from §1
rather than created, and §7 requires the README to link the milestones, which is what a reader
follows.

The one thing the file was to carry that the milestones cannot is the **"Not planned"** note, so
it is written here: **web push is not planned**, because it is only useful while tethered with
the app open, which is the case where the app is already showing what it would notify about.

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
| `test_plugin_loads.py` | F30: loads `plugin/companion.py` via `spec_from_file_location` + `exec_module` with the module absent from `sys.modules` throughout, and again with it registered first; both assert a `Companion` class present, a `plugins.Plugin` subclass, and instantiable; `sys.modules` cleanup scoped to the two names the file itself registers |
| `test_spec_tree.py` | §1.1: every path §1's tree names is tracked, every tracked directory is named, and the two exemptions are exactly the seven open listings and the three opaque stubs. Parses the tree from `SPEC.md` and compares it with `git ls-files`; the parse is asserted against landmarks and floors before any comparison, because a parser that returned nothing would make every later assertion pass |
| `test_stats.py` | field-by-field `stats` mapping; `pasv_or_auto` across all four states; `capabilities` reflects `plugins.loaded`; **asserts `session()` call count is 0** during `get_stats`; `SessionCache.refresh` before `on_ready` (agent getter returning `None`) and on a non-`Mapping` bettercap snapshot both return `False` without raising and leave a prior snapshot and its climbing age untouched, and the `None`-agent case is asserted to log nothing at all - it is a normal startup state, not the failure a dead bettercap logs |
| `test_access_points.py` | `mac`→`bssid` mapping; `clients: None`; missing keys; empty list |
| `test_handshakes.py` | SSID containing underscores; `.pcap` and `.pcapng`; unparseable name yields `bssid: null`; sidecar present/absent/malformed; 500-entry cap sets `truncated`; unreadable file skipped; both `on_handshake` argument shapes normalise identically |
| `test_handshake_dir.py` | where the capture directory comes from (§2.5): `handshake_dir` wins over the agent's `bettercap.handshakes`; falls back to the agent when unset or empty; unknown when neither yields one; a store over an unknown directory reports **both** counts as `None` while a store over a directory that is merely missing or unreadable reports `0`, `0`; asserted on the store rather than through a fixture whose directory the test chose (issue #146) |
| `test_peers.py` | every field in the §2.8 table; the `datetime` vs float `last_seen` asymmetry; a `Peer` whose timestamp parsing fell back to `str`; missing `adv` keys |
| `test_gps.py` | source priority order; `gps_source` config overrides; `0.0/0.0` is not a fix; browser staleness expiry; sidecar written in bettercap shape (D13); no sidecar without a fix; existing sidecar not overwritten; `.pcap` gets the right sidecar name |
| `test_log_tail.py` | bounded tail returns the last N; default and cap; file smaller than one chunk; undecodable bytes; missing file yields `log_unavailable` |
| `test_battery.py` | the tier order of §2.11 — `pisugarx_ext`, then `pisugarx`, then the I2C bus as a last resort that a loaded provider and a disabled `pisugar` both suppress; every tier failing yields nulls and raises nothing. Then the rules that make the descent per-field rather than per-tier: a tier that supplies only the level takes the charge state from the tier below, a second plugin fills only what the first left missing, and a field no tier supplies is null. The candidate order within a tier (`battery_level` over the vaguer percentage names, `charging` over `power_plugged` and `plugged`), callables called and a raising one treated as a miss, an attribute present and `None` a miss. Percentages: bounded to 0-100, a numeric string accepted and settling the field, a refusal never reported and never stopping the tier or the candidate below, and never reaching the wire. `available` and `capabilities.pisugar` latch on the first answer from either field and do not un-latch on a later refusal. The nested `client` traversal: the reference unit and upstream `pisugarx` shapes, an attribute on the plugin outranking the same one on the client, a flat plugin with no client, a client whose `ready` is false not traversed while the plugin itself still is, and `battery_charging` probed on neither. `_as_charging` as a value grammar in its own right — `bool` as itself, `0` and `1` only among ints, the enumerated spellings after strip and lower, and a refusal for every other int, every float, the empty string, the string `false`, `None` and an unrelated type, each contrasted with what plain `bool()` would have said |
| `test_controls.py` | `set_mode` calls `pwnagotchi.restart` with `"AUTO"`/`"MANU"` and **never assigns `agent.mode`**; broadcasts `restarting` before restarting; no-op when already in the mode; `set_pasv` errors outside auto and when `pasv_mode` is absent, emits the right event otherwise, and does not read `passive` back; reboot/shutdown call the module-level functions |
| `test_tls_startup.py` | missing cert, missing key, unreadable key and malformed PEM each result in **zero listeners** and a logged error — the no-plaintext-fallback rule |
| `test_auth.py` | token accepted, rejected, wrong type, missing; `compare_digest` used; unauthenticated connection closed after `auth_timeout`; auth skipped when no token configured |
| `test_binding.py` | selection: literal match, containment in a block, a local address matching nothing; every entry-validation row of §2.3.1, including the `/24` floor, host bits normalised, a non-list value, and the empty-after-rejection case; a legacy `interfaces` key warns and binds nothing; the reconciliation table: appear, disappear, a new address in the same block, none matching, `EADDRINUSE`; asserts `0.0.0.0` is never passed to a bind call and that no bind decision reads an interface name |
| `test_listeners.py` | the teardown and error-guard paths of `Listeners`, which run only once something has already gone wrong and are what stands between a failure and a crash: the HTTP server thread surviving a `serve_forever` that raises; `_serve_ws` returning nothing and starting no loop when no client handler was supplied; closing an address that was never bound; a `shutdown()`, a `close()` and a `call_soon_threadsafe` that each raise, none of which may reach the caller; a websockets server object with no `wait_closed`; `stop()` idempotent and joining nothing when there is no loop thread. **The early returns are pinned by the silence of the log, not by the absence of an exception** - the guarded line below each of them swallows and logs, so removing the guard is invisible to a test that only asserts nothing raised |
| `test_listeners_stop_race.py` | issue #90: `Listeners.stop()` racing `Listeners.reconcile()` on separate threads, driven into a deterministic overlap through the `Deps.list_local_ipv4` seam rather than left to chance - a teardown that starts while a pass is parked mid-flight leaves nothing bound afterwards, and a pass that runs after teardown already completed binds nothing, both asserted against real sockets and `socket.bind` and neither call ever raising. Also the two adjacent hangs the same lock exposed: a bind failure on one listener (WSS occupied) must still let `stop()` return and must not leave the other side listening; a serving thread that fails to start (`RuntimeError: can't start new thread`, injected by thread target rather than by address) must leave nothing in `self._bound`, must not hang `stop()`, and must not poison the address for a later pass. And the `server_bind` override: `socket.getfqdn` never runs during a bind, `server_name`/`server_port` are set from the literal address, and a real HTTPS request still returns the CSP header naming it |
| `test_https_handshake_off_accept.py` | issue #100: the HTTPS accept loop no longer runs the TLS handshake itself. A peer that completes the TCP connect and sends nothing does not stop a normal HTTPS request to the same server getting served, does not stop `stop()` returning, and leaves nothing listening once `stop()` has run - all three asserted against real sockets and a real `Listeners` instance, with an explicit timeout on every wait so a regression fails the test rather than hanging the job. Also a peer that sends garbage instead of a ClientHello: the failed handshake does not block the next client either, and produces no raw traceback on either output it could reach - neither the log (via `caplog`, which also rejects a record carrying `exc_info`) nor stderr (via `capsys`, catching the `handle_error` override being removed outright rather than merely weakened) |
| `test_static_server.py` | `.webmanifest` and `.js` content types; SPA fallback serves `index.html`; `..` rejected with 400; no directory listing; `no-cache` on index and service worker; `text/html; charset=utf-8` on a direct `index.html` request and on the SPA fallback alike (SPEC 4.2, SPEC 2.15) |
| `test_csp.py` | the three shapes that reject a request line before the path exists, a malformed line, an over-long one and an unsupported version, asserting that a response arrives at all and carries the headers wherever the HTTP version permits any; the three headers (Content-Security-Policy, X-Content-Type-Options: nosniff, Referrer-Policy: no-referrer) on every response the static server produces, not only a successful GET — the SPA fallback, the 400 for a traversal attempt, a directory request; every fixed directive of §2.15.1 asserted exactly; no `'unsafe-inline'` or `'unsafe-eval'` anywhere in the policy; `img-src` carries `'self' data: https://*.tile.openstreetmap.org` and no `blob:`, and the tile origin appears nowhere else; `connect-src` built per request as `'self'` plus one `wss://<address>:<ws_port>` per bound address, and follows a rebind between two requests to the same running server |
| `test_ci_gates.py` | every step under `.github/workflows/` that scans through `grep` or `git grep`, driven as real shell with stubbed `git`, `grep` and `gh` exiting with a status the test chooses: a match fails the job and prints the ban's own message, exit 1 is the clean result, and 128 fails without printing the clean line; the auto-merge job enables the merge with `--auto --squash` on a documentation-only file list, leaves a list containing code to a human, refuses to decide when the scan could not run, and reports rather than fails when `gh pr merge` does; a step whose shape cannot be driven has to be named in an explicit registry, and a guard test fails when an uncovered one appears, so the next scan added cannot fall silently outside the parametrization; the stubs exit loudly on any invocation they do not recognise rather than reaching the real binary, and nothing touches the network |
| `test_shipped_files.py` | the inventory of every directory copied verbatim into the build (§13.2), in both directions: an undeclared file fails and is named, a declared file that is gone fails as missing rather than as unexpected, and the real repository passes as it stands so a wrong inventory is caught here rather than in CI; the index and the disk are each proven to be read, with a file tracked but deleted locally and a file present but untracked; `.githooks/pre-commit` refuses on an undeclared file before it needs a denylist at all; and the hygiene job in `ci.yml` actually invokes the script, because a gate nobody runs is not a gate |
| `test_no_inline_script.py` | the no-inline-script gate, `.github/check_no_inline_script.py` (§2.15.1, issue #141): every element shape that must fail — a `<script>` with a body, an empty `<script></script>`, one with other attributes but no `src`, and one whose `src` is present but empty — each failing and naming the offending file; an external `<script src="...">` passes, and so does the real, built `dist/index.html` when it is present; a missing `dist/` and an unreadable HTML file each fail rather than pass quietly, the same rule §13 states for the shipped-inventory gate (issues #112, #126); and the `frontend` job of `ci.yml` actually invokes the script, after `Build` and on a pull request. **Every page, found by search** (issue #144): a `dist/` holding two HTML files has both inspected, a violation in the second one alone fails and names that file rather than the first, one nested in a subdirectory is found too, a clean file beside a dirty one does not rescue it, every file is checked rather than the run stopping at the first failure, which is a different rule and is asserted with two dirty files both named in one run, and a `dist/` with no HTML at all fails instead of reporting a build that emitted nothing as clean. The sorted order is pinned by a top-level file that sorts **after** a nested one, because two files at the same depth do not pin it: directory iteration usually returns them alphabetical anyway, and the mutant with the sort removed survived that shape before this test was written. **Every way widening it could have narrowed it**: a `dist/` holding a clean page that is not `index.html` fails and names `index.html`; an unreadable subdirectory fails and names the directory, at **both** modes that produce it - `0` where the listing itself fails, and `0444` where the listing succeeds and the stat on a child raises, which is the mode that shipped a traceback because the `0` fixture takes the path that works - each asserted with a page inside it that *would* have failed the element rule so the gate is not merely missing a clean file, and skipped as root because root reads it anyway; `dist/` itself at mode `0111`, where the named failure must **not** be joined by the "no HTML anywhere" sentence, since the gate stat'd `index.html` successfully before the walk failed; the `.htm` and `.HTML` gap pinned as a **characterisation** test that says so in its docstring, so widening the pattern fails it and it gets rewritten rather than passing on quietly; a symlinked subdirectory fails and names it, with the linked page's content asserted never to surface, which catches a silent follow and a silent skip alike; and `dist/` itself at mode 0 failing with a named message and **no traceback**, asserted as the absence of one, since that is the half of it that is a fix rather than a rename. Every one of those pins the **path**, not a word from the sentence around it: `sub` matches "subtree" and `linked` matches "symlinked", and two assertions written that way passed against a gate that had stopped naming the directory at all |
| `test_pre_commit_hook.py` | the leak gate's own exit-status rule (§13): a `git diff --cached` that fails refuses the commit instead of reading an empty staged list as nothing to scan, on both the file list and the unified diff; an unset `companion.denylist` still gives the existing "not configured" failure rather than the new one, so the two stay distinguishable and `tolerate=(1,)` is real; a `git config` failing with any other status is a failure; a clean staged diff exits 0, a matching one exits 1, and the matching line is never printed because it is the secret; and the shipped-inventory gate the hook now runs first refuses when its `git ls-files` fails, before the denylist is looked up at all |
| `test_hooks.py` | the hook surface the agent calls: both `on_handshake` argument shapes (F20), sidecar written only on a fix, `on_peer_detected`, `on_wifi_update` skipping non-mappings, `on_channel_hop`, `on_ui_update` pushing only when the face or status text changed and pushing a degraded payload rather than nothing when a read fails (§2.13), the four mood hooks; every push validated against `docs/schemas/outgoing/`; the router-absent and pre-`on_ready` windows; **a failing broadcast is logged and swallowed in every one of them**, because `plugins.on()` runs these on the agent's thread (F8) and an escaping exception reaches the UI loop |
| `test_config_keys.py` | §2.2.1: a key that is not in `DEFAULTS` is logged once at WARNING naming the key, and the plugin still comes up on its defaults; `enabled` never warns; `interfaces` gets its own §2.3.1 message and not this one as well; a block with nothing but valid keys logs nothing at all, which is the assertion that stops the rule becoming noise; the misspelling that motivated it, `bind_address` for `bind_addresses`, asserted by name; and the key is still named when unusable TLS material makes the plugin refuse to start, which is the ordering §2.2.1 requires and the case where the warning matters most (issue #137) |
| `test_deps.py` | the real `Deps` defaults nothing else drives: `spawn`, `run_command` exit statuses and a missing binary, `list_local_ipv4` returning IPv4 literals only and never `0.0.0.0`, `read_pisugar_i2c` (a shape guarantee that holds on any host, a two-tuple that never raises, plus a no-bus case whose precondition is **established** rather than assumed, and a bus that imports but refuses to open; then the register map of SPEC 2.11.1 and both rules of 2.11.2, which is where most of that file now is: bit 7 of `0x02` asserted over all 256 values of the byte rather than the two observed, every other bit of it shown not to matter, the registers actually touched compared as a set so a word read is visible and not only its result, and the transport-versus-content line asserted directly rather than only through its two instances), and `read_gpsd` against a fake gpsd socket — the `?WATCH` handshake, reading past `VERSION`/`DEVICES`, a damaged line, a refused connection, and a silent server bounded by the timeout. **Both backends of every seam**, not only the fallback: `tests/fakes/i2c_stub/` and `tests/fakes/netifaces_stub/` put the libraries on `sys.path` so the branches that run on a unit that has them are exercised, instead of being unreachable by construction because injection is how you avoid running them |
| `test_broadcast_cadence.py` | §4.3.7: `stats` broadcast on the `keepalive_interval` tick, to every client in the broadcast set (that only authenticated sockets join it is `test_integration_ws.py`); **a failure building `stats` must not cost that tick's `keepalive`**, since the liveness frame is what tells an idle client a quiet plugin from a dead link and a broken payload is exactly when it is needed; a tick with nobody connected sends nothing at all (§2.4) and does not move the next one; **zero `agent.session()` calls on that path** (§2.5), since a periodic broadcast that blocks is worse than no broadcast; `keepalive_interval` clamped to 5-20 with the clamp logged, and `0` clamping to the default 20 rather than to the floor; `session_poll_interval` clamped to 1-5, which no longer sets the broadcast spacing but does set how soon `sessionAge` is reset after bettercap recovers, so a recovered unit leaves `degraded` promptly; **the `keepalive_interval` ceiling bounds how late the client hears of a stall**, not whether it is reported: the threshold is derived from the refresh episode and not from the spacing (§2.4), so a value above the ceiling delays the badge rather than falsifying it. The floor is about payload cost over BT PAN, not staleness: below it the broadcast is more frequent, not less; `rebind_interval` clamped to 5-300 with the clamp logged, `0` clamping to the **floor** rather than to the default because unlike `keepalive_interval = 0` it never meant disable, and a non-numeric value, `nan` and both infinities taking the default 30 at WARNING. In every one of those cases **the background thread starts and keeps running** (#105): the defect was a bare `float()` at thread entry, outside the loop's `try`, so one bad key killed the session refresh, the gpsd poll and the reconcile together and the plugin served stale data for the life of the process |

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
  **Every row of the §4.3.1 table has a test named for it**, which is the point of writing that
  section as a table: a row with no test is the missing exit the table exists to prevent. Beyond
  the rows: that a read resolves on its typed reply and a command on its `acknowledgment`
  (§4.3.8), which is not the same rule and looked like it was; that the request clock starts at
  the call rather than at the send, so a read asked for while the socket is down still fails in
  15 s instead of hanging for ever; that the token is re-read from storage on each attempt, so a
  corrected token actually reaches the wire; that a client with no token stays silent until the
  unit speaks, per socket and not per client; that `unauthorized` carries the reason its two
  entry paths differ by; and that a state command in flight when the socket drops is reported as
  failed rather than re-sent, which is the reboot-in-a-pocket scenario §4.3.3 is written for.
  The clock is faked throughout: a 55 s threshold must cost the suite no wall-clock time.

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
  showing the request as pending in between and **not** reverting silently if the command fails.
  This entry used to claim §4.6.1's four GPS states were resolved here, first-match-wins, and
  nothing in `lib/stores.ts` has ever resolved them. The composition belongs to whichever change
  renders it, which is issue #34; §4.6.2 says so and `lib/geo.ts` supplies the one row that is
  client-local. An inventory claim with nothing behind it is precisely what this inventory exists
  to prevent, so it is struck here rather than in a follow-up.
- `settings.spec.ts`: persistence round-trip, defaults, migration of an unknown shape, and the rules that make this module the one place a token can go to the wrong unit: every malformed-blob shape asserted separately rather than as a class, the token key reconciled on load against a dropped host, a dangling id and a stale key, cleared when the active host is removed or its address is edited, an address accepted only as an IPv4 literal so that neither a credential nor a leading-zero octet can point the URL somewhere other than the entry on screen, and storage that throws leaving the app usable. It also holds the three cases §4.7 keeps apart, which is where they belong because they are the loader's behaviour and not the derivation's: a blob that will not parse taking the first-run path, a blob that lost every host restoring the defaults with Bluetooth active and **without** deriving, and a deliberately empty list staying empty. Each of the three drives the hostname seam with a usable address, because under a hostname that is not one the deriving and non-deriving branches produce the same list and an assertion made there proves nothing about the defect it names.
- `protocol.spec.ts`: sample payloads from `docs/schemas` examples typecheck against the
  generated types.
- `format.spec.ts`: every §4.5.1.1 and §4.5.2.1 rule that turns a value into a string, asserted
  on the function rather than through the DOM. The §4.5.2.1 half is the six connection-state
  sentences, the two unauthorized-reason sentences and the two calls to action, each by equality
  against the tables there, plus the assertion that no two states and no two reasons share a
  sentence, which is the mutant a per-case table alone does not catch. One assertion there is
  worth more than the rest together: that the reason and its call to action, composed, equal
  §4.5.1.1's banner text byte for byte. It is what keeps the Settings row and the Dashboard
  banner one source, and it is paired with an assertion in `dashboard.spec.ts` that the view
  actually composes them - two failures worth telling apart, the wording drifting and the view
  ceasing to use it. Then: `null` to a dash for each nullable field of `Stats`;
  an uptime cut to its two largest units at every boundary that changes which two those are;
  a `sessionAge` in whole seconds below a minute and whole minutes above it, and `null` reading
  as never refreshed rather than as zero; a temperature rounded, not truncated; a unit timestamp
  as a time of day and never as a relative age, including one stamped in the phone's future,
  which is the case the rule exists for.
- `dashboard.spec.ts`: the view mounted against the stores directly, with no client and no
  socket. Every field §4.5.2 names present with its label; the dash and the row that stays for
  each nullable one, before the first frame and after a frame carrying nulls; the six connection
  states each asserted for their own banner, per state rather than in one pass, with the two
  `unauthorized` reasons carrying different sentences; the
  handshake counter reading `handshakesTotal`, and the `*.pcapng` figure appearing only when the
  two disagree and both are known, with a null on either side asserted to leave the second line
  off the screen rather than dashed (issue #146); the channel following the `channel` store rather than the last `stats`, and the
  mode badge following `stats` rather than `face`; the GPS rows following the `gps` store rather
  than `stats.gps`; every banner sentence asserted by equality against §4.5.1.1, per state and
  per reason, with `connecting` and `connected` asserted to carry their state and no copy, and
  the two `restarting` reasons asserted to carry the **same** line, which is what the app can do
  today and is tied to issue #131 in the test itself, so that the day the store carries the
  reason the assertion fails and names the ticket that made it stale; a
  hostile `face`, `status`, `lastPeer.name` **and `lastHandshake.ssid`** - a script tag, an
  event-handler attribute, a quote-and-angle-bracket soup - rendering as text with no element
  created, asserted on the DOM rather than assumed from Svelte's default, since §4.5.3 is the
  reason this view is the first place it matters; and a hostile name of exactly `-`, which must
  not make the field report itself as empty.
- `settings-origin.spec.ts`: §4.7's first-run derivation, which is the whole of issue #134 below
  the screen. The `origin` entry minted from the page's hostname and active, with the two
  prefilled entries beside it and the ports left at their defaults; the fallback asserted over a
  table of hostnames that are not addresses rather than over one, since `localhost`, a name, a
  bracketed IPv6 literal, the two addresses §4.7 refuses by name and a leading-zero octet each
  fail for a different reason; and that it is derived **once**, so a second load from a different
  address changes nothing and an owner's edit to that entry survives. The hostname is driven
  through the seam rather than through whatever the test environment reports, because a default
  that agrees with the fallback by accident hides the branch that matters.
- `settings-view.spec.ts`: the Settings screen against §4.5.2.1's declared hooks and never by
  walking the markup. Add, edit, remove and activate; the list before the form and the form
  present with no interaction, both of which are specified with reasons and are what a rearrange
  would silently undo; the ports rendered and not editable; the token edited in the row and
  masked; the address refused by the form with the message beside it and no throw reaching the
  user; the USB note in the screen and not in the persisted label; the diagnostics rendering the
  state and the plugin version, the unauthorized reason only while the state is `unauthorized`,
  and **no** last error, since §4.5.2.1 says a view must not manufacture a surface that does not
  exist (issue #176). Two assertions carry more than their size: one table of addresses driven
  through the exported predicate, through the mutator and through the form, which is what
  replaces the coverage a deliberately unreachable backstop can never have; and §4.5.3 on the
  plugin version and the reason, asserted on the DOM rather than assumed from Svelte's default.
- `dashboard-controls.spec.ts`: the four state commands against §4.5.2.2's declared hooks, mounted
  through `startSession` with a fake client, which is how the view, `lib/controls.ts` and
  `lib/ws.ts` are exercised as the one thing an owner actually taps. The mode switch offering the
  right mode for each `stats.mode`, null included, and enabled there; PASV absent without
  `capabilities.pasv` and disabled with its sentence outside AUTO; the confirm required in both
  directions, arming a second control disarming the first, and nothing on the wire until the
  second tap, which is issue #37 closed; the exact frame checked against the schema JSON, so the
  lowercase `auto`/`manual` of `set_mode` cannot drift into the uppercase `Mode` enum; each of the
  six pending rows resolved by the observation §4.5.2.2 names **and** held pending through an
  observation it does not, which is what keeps the asymmetric `manual` row honest -- a test that
  confirmed it with `stats.mode === 'MANUAL'` would be asserting something the unit cannot say;
  one `stats` frame not being enough to abandon a request and the second one being enough; a
  rejection carrying each of the two named codes reaching the PASV control and nowhere else; an
  attacker-chosen code asserted absent from the DOM (§4.5.3); the enabled state compared against
  the **imported** `canSendCommand` for every connection state rather than a restated list; and
  the state before the first frame, which every launch passes through. It owns no fake timers,
  because §4.5.2.2 owns no timer: a control resolving only after a clock is advanced would be
  evidence of the thing the section forbids.
- `wifi-view.spec.ts`: the Wi-Fi view against §4.5.2.3's declared hooks, mounted through
  `startSession` with a fake client, so the view, `src/shell/Segmented.svelte`, `lib/wifi.ts` and
  the refresh in `lib/stores.ts` are exercised as the one screen an owner actually opens. The
  tablist in full -- roles, roving tabindex, both arrow directions with the wrap §4.5.2.3 settles,
  Home/End, automatic activation, click activation, the `hidden` on the inactive panel, the same
  panel node surviving a switch, and an unhandled key leaving selection, focus and
  `defaultPrevented` alone, which is the keyboard-trap guard; the trigger being the route becoming
  `/wifi` rather than the component mounting, with a return visit on the still-mounted view asking
  again, which is the defect that made this section be rewritten; both lists fetched together and
  coalesced per client, from four angles including the one that only appears mid-switch, where A's
  request settles after B has taken over and must not release an owner that is now B's; the offline
  ask being made rather than gated, because a read is queued where a state command is refused
  (§4.3.3); the four empty-versus-never-fetched sentences by equality; Nearby's order asserted both
  as a direction and as stability under a permuted arrival and a later frame, which are different
  claims and only the second keeps rows from swapping; Captured asserted to render in the order it
  arrived, from a payload in no client-computable order; the badges, the `500+` and the truncation
  notice, with a frame where the badge and `stats.handshakesTotal` disagree and both are shown; and
  §4.5.3 on all four hostile fields, asserted on the DOM; and two rows keyed on a duplicate and on
  an empty BSSID, which is where §4.5.3 stops being about escaping: the plugin writes `""` when
  bettercap reports no mac, Svelte throws on a duplicate `{#each}` key in production, and the two
  together take the screen down with no attacker needed. It owns no fake timers. What it leaves
  uncovered is defensive arms only, of two kinds: values the schema forbids and nothing validates
  (issue #109), and type-driven guards for states the caller cannot produce. They are described
  rather than counted, because a count in prose is wrong the first time a test covers one of them
  by accident -- which is what happened to an earlier draft of this entry.
- `peers-view.spec.ts`: the Peers view against §4.5.2.4's declared hooks, on the harness
  `wifi-view.spec.ts` established. What it pins that no other view's tests can: several peers
  sharing `'???'` rendering as several rows, which is `each_key_duplicate` waiting for the
  documented default of `peer.identity()` rather than for a hostile unit, and `'???'` itself
  rendering verbatim with no `data-empty` beside a null `face` that dashes -- the two halves of
  the distinction §4.5.2.4 draws between *the peer did not say* and *this app does not know*. The
  order asserted as a direction on all three keys and, separately, as stability under a permuted
  arrival, which are different claims. The three refresh occasions, coalescing per client, and the
  refresh control tapped with no active host, which does nothing and says so. Every one of the five
  identity strings carrying a hostile payload, asserted on the DOM: this is the first screen where
  every text field was chosen by a stranger's unit rather than by a network in range. What it
  leaves uncovered, named here rather than left to the general licence above: the comparator arm
  where two peers tie on every sort key, including the fingerprint, which is a defence against a
  payload the schema forbids and nothing validates (issue #109). The shared comparator itself is
  fully covered, and getting it there is what found the case worth having: `Array.prototype.sort`
  calls a comparator in whichever argument order it likes, so "an unknown value sorts last" is two
  claims and the fixtures originally made only one of them. A comparator that answers one order
  and not the other is not merely wrong, it is inconsistent, and an inconsistent comparator gives
  an order that depends on the array's length and the engine's strategy -- right in a test with
  three rows and wrong in the field with thirty.
- `log-view.spec.ts`: the Log view against §4.5.2.5's declared hooks. What it pins that no other
  view's tests can: the follow timer, which is the only timer this client is allowed -- off on
  arrival, asking immediately on the tap and then at the interval rather than merely eventually,
  stopping when the view is no longer current, and surviving the trip away and back with the
  control and the timer both as they were. This is the one file where advancing a clock is
  correct rather than evidence of the thing §4.5.2.3 forbids. Also `log_unavailable` reaching the
  screen as its own sentence rather than as a swallowed rejection, with the code itself asserted
  absent from the DOM; the filter narrowing what is rendered and sending nothing, and a match
  rendering as one text node because highlighting would mean splitting a hostile string into
  elements; the buffer replacing rather than appending; and an empty log beating an unmatched
  filter, which is a precedence rule and not a sentence.
  The e2e side carries issue #38's own criterion in `geometry.spec.ts`, and it earned its place
  immediately: it failed on all four projects, with the log box forty pixels past the views area,
  because `position: absolute; inset: 0` resolves against the containing block's **padding** box
  while the views area is its **content** box. The change would otherwise have shipped closing an
  issue whose defect it still had. The full-buffer half of that criterion is a stated skip naming
  issue #185, because an e2e with no unit attached cannot fill a log.
- `mirror-view.spec.ts`: the Mirror against §4.5.2.6's declared hooks. What it pins that no other
  view's tests can: a frame arriving at all, since `screen_image` was until this change dropped in
  `lib/stores.ts`'s default arm; that unit A's frame is gone from the screen the moment the active
  host changes, which §4.5.2.6 calls the harder defect to notice because a picture of a face
  carries no address to give it away; and the base64 guard, from four hostile payloads, a
  wrong-length one and an empty one, each asserted to render **no** `img` at all rather than a
  broken one. The `src` is asserted to begin `data:image/png;base64,` and never `blob:`, and no
  element in the mounted tree carries a `style` attribute -- both are §2.15.1 rules that would fail on
  the device and nowhere else, which is the only place they can be caught cheaply. The second
  timer, on the Log's terms and at five seconds.
  Two of its results came from mutation rather than from a red test. Replacing the whole shape
  check with `return true` fails seven of these tests, so the §4.5.3 half is well pinned. Removing
  the separate empty-string guard failed none of them, which is how that guard was found to be
  redundant against the pattern beside it and removed -- with `screen.ts` reporting full coverage
  throughout, since the line was executed either way. A line whose removal changes nothing is not
  covered in any sense the number can express.
- `geo.spec.ts`: browser geolocation (§4.6.2), which is the first thing in this client that acts
  on the world rather than only rendering it, and the first with a lifecycle listener. What it
  pins that no other file can: that the watch is started **synchronously inside the tap's own call
  stack**, asserted before any `await`, which is the one rule iOS enforces by failing silently
  (G1) and therefore the one no manual test on a desktop would ever catch. The 5 s cadence with a
  phone that has stopped moving, since a stationary position is still true and the plugin drops
  what it has not been sent for ten seconds. `piFix` read at push time and not latched, including
  the sharp case: the interval does **not** restart when `piFix` clears mid-window. The pushed
  frame checked against `incoming/gps_data.json`, with `source` asserted absent, because the
  incoming shape is `additionalProperties: false` and a client that adds it gets `bad_request`.
  `denied` from error code `1` and from neither `2` nor `3`, and independent of `piFix` in both
  directions: a Pi fix appearing does not clear a client-local denial, and a denial does not touch
  what the unit reports. Backgrounding, both halves, and the half that is easy to leave out:
  coming back is a fresh start, so the timer's first tick after a resume must not carry the
  position taken before it.
  **Coordinates in fixtures follow this repository's convention and are obviously nowhere**:
  `10.1 / -30.2` is open Atlantic, used by `dashboard.spec.ts`, `dashboard-controls.spec.ts`,
  `format.spec.ts`, `stores.spec.ts` and `wifi-view.spec.ts`; `12.34 / 56.78` is a digit sequence,
  used by `ws.spec.ts`. Both lists were wrong on the first attempt at this paragraph and were then
  produced with `grep`, which is the only way a citation like this is worth writing: a rule that
  certifies fixtures and misnames its own precedent teaches the next reader to look in the wrong
  file. The first version of this file used
  four plausible pairs clustered inside forty kilometres of each other in one inhabited region,
  which a security audit caught before it was committed. No assertion anywhere depends on a
  coordinate being plausible, so the convention costs nothing, and this is the only file in the
  project where getting it wrong would have narrowed a person to a place.
  Two more fixture notes that belong here rather than in §4.6.2, because both are facts about the
  fake rather than about the app. `visibilitychange` fakes set `document.visibilityState` **and**
  `document.hidden`: they cannot disagree in a browser, so a fake that sets one of them tests
  whichever property the implementation happens to read, and an earlier version of this file did
  exactly that. And `GeolocationPositionError` has to be stubbed as a global, because jsdom does
  not implement it at all while every real browser exposes it -- without the stub the error path
  raises `ReferenceError` and the test observes the fake's gap rather than the code.
  One more thing this file has to do that no other view's tests do, and it is a consequence of
  the module being a singleton with a listener on `document`: a test that leaves sharing on
  leaves that listener attached across `vi.resetModules()`, since there is no reset seam and
  deliberately so. Teardown turns the control off. Absolute-count assertions are what surfaced
  the stale listeners; relative ones never noticed, which is the same lesson as the assertion
  that passed for the wrong reason above.
  Its mutation results are recorded with the same care as the assertions, because two of them
  turned out to be claims about the fixture rather than about the code. **The `document.hidden`
  misread is no longer a mutant this file can kill, and that is correct**: once the fixture sets
  both properties, as it must, reading either one is behaviourally identical and no test can
  distinguish them. The defect was the fixture's incompleteness, and it is fixed there.
  The mutant that does remain -- inverting the hidden branch -- is killed by **six** of the nine
  backgrounding tests, measured twice against two different versions of the implementation and
  giving the same six names both times.

  That number is worth the space it takes, because three parties produced three answers for it and
  only one of them ran anything. The first figure recorded here was four, from a measurement taken
  before one of the tests existed. A review then traced the mutant by hand and reported seven. The
  measurement says six. Nobody was being careless: a stale measurement and a careful trace are both
  ordinary, and both are the same mistake, which is publishing a number about a mutant nobody
  currently has in front of them. **A mutation result that was not produced this time is not a
  mutation result.**
  One of those assertions originally **passed for the wrong reason**: it checked only that the old
  watch id had been cleared, and the buggy branch cleared it too, on its way to starting a second
  one. It now also asserts that no new watch was started. That is the same standard the security
  audits use on a zero-hit scan, applied to a green test: an assertion that cannot distinguish the
  fix from the defect has not observed anything.
  One mutant survived and is recorded rather than dropped: re-arming the interval from inside the
  push, at the moment it fires, produces an identical future schedule and is unobservable from
  outside at any test's resolution. The rule §4.6.2 actually warns about is the event-driven
  restart on the `piFix` transition, which is a different implementation and which a test does
  kill.
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
- Geometry invariants rather than pixels, for the rules §4.5.1 states: nothing painted below the
  content area in either orientation, the map never owning the full width or height of the views
  area so a drag always has somewhere to scroll the app, touch targets at 44px, contrast computed
  for every token against every surface it can land on.
- **A view is allowed to be taller than the viewport, and the invariant is about where its
  overflow goes, not about its height.** The first version of the geometry suite asserted that
  every view root stayed inside the shell, which was true of seven placeholder views two lines
  long and stopped being true of the first view with content on it: a `<section>` 897 px tall in
  a 402 px viewport reports a bounding box below the shell, which is what a scroll container
  looks like from the outside and not a defect. What must hold instead is that the overflow
  belongs to the views area and to nothing else - the document itself still does not scroll, the
  area's own `scrollHeight` accounts for the view, and no pixel of it is painted below the
  navigation. The horizontal edges keep the original rule: a view that escapes sideways has
  nowhere legitimate to go, because nothing scrolls that way.
- `charset.spec.ts` (SPEC 4.2): the built `dist/index.html` declares `utf-8` within the first
  1024 bytes, checked once against the raw response bytes rather than once per project since the
  rule has no engine dependency; and `document.characterSet` is `UTF-8` in every project, which
  is the assertion that would have caught the original defect - in either engine, as measured:
  neither sniffs its way out of a missing declaration under this harness. The fourth criterion of issue #39, the pwnagotchi face
  rendering identically in both engines, is not here: no view renders one yet, and the file says
  so rather than letting the gap pass for covered.
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

`tests/tools/test_install.py` drives `install-on-pi.sh` the same way, as a subprocess against a
throwaway root. Since issue #157 the part of it worth naming here is the resolution of
`main.custom_plugins` (§5.3.1): each of the three sources answering only when the one above it
did not, the refusal when none of them does, and the refusal when more than one tree matches.
Two of those assertions are unusual enough to say why they exist. **One reads the shipped script
as text and asserts neither candidate custom-plugins path appears in it at all**, which is the
assertion that would have caught the original defect and the only one that still catches it if a
literal is reintroduced in a branch no test happens to take. **The other asserts the exit status
and that nothing was written**, rather than that a message appeared: a run that prints the right
complaint and installs anyway is the failure being guarded against, and the message alone cannot
tell the two apart. `--pwn-prefix` is what lets the third source be exercised without root and
without writing into the host's real `/opt/.pwn`.

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
- **The recorded figure ratchets upward, and lowering it takes a second flag and a reason
  (issue #191).** `--update` writes a figure at or above the recorded one and refuses one below
  it. Lowering is `--update --allow-drop`, and the flag requires a reason, which is written into
  the baseline file beside the number it lowered.

  This is a change of kind rather than of degree, so the argument for it belongs here. The figure
  was being rewritten to whatever the branch happened to measure: frontend branches sat at 89.55
  for a fortnight, one change took them to 95.14, and the three merges after it moved them down
  each time, 0.53 off the peak, with nothing anywhere recording that they had. Each drop was
  individually small and individually defensible. What the three together showed is that a
  baseline which moves down to meet every change cannot fail one, and a number that cannot fail
  anything is a record rather than a gate, whatever the file is called.

  The reason is not a comment for a human to skim past. It is the thing a reviewer reads in the
  diff, and it is the reason the flag exists rather than the refusal simply being removable: a
  lowering that costs one word is one nobody notices, and a lowering that has to be justified in
  the file is one somebody has to defend. What may not happen is a drop landing with neither.

  **The floor is the one comparison still made on the raw measurement, and its message has to
  say so.** The floor is not a ratchet: nothing is written when it fails, and a build measuring
  84.999 is below eighty-five however the number is displayed. But printing that as `line coverage
  is 85.00%, below the 85% floor` is a sentence contradicting itself, so the message shows enough
  digits to be true rather than the two the file stores.

  **A `"reason"` read back off disk is checked the way one arriving on the command line is.** The
  same string cannot be refused as an argument and accepted as a file value: a `reason` that is
  not a string, or is blank, makes the entry malformed like any other unreadable field.

  **What is compared is what would be written, not what was measured.** The file holds figures
  rounded to two decimals, so comparing a full-precision measurement against a rounded record
  refuses a re-run that measured exactly the same coverage: a true 90.03999 is recorded as 90.04
  and then read back as higher than itself. The bullet says `--update` writes a figure at or above
  the recorded one, and an equal one is at it. Round first, then compare, so the question asked is
  the one the file can answer.

  **An equal figure keeps its reason; only a rise removes it.** The reason explains the number on
  file, and re-recording the same number leaves that number, and therefore its justification,
  standing. This is the one place the removal rule has to be stated as *raises* rather than
  *updates*, and it is worth a sentence because the two read the same until the rounding above
  makes an equal update possible at all.

  **A malformed entry is not repaired by `--update`.** Recovering it would mean this tool
  overwriting a figure it could not read, which is the defect the bullet exists to close arriving
  from the other direction: a file that changed when nothing established what it should change
  from. The repair is a hand edit, which lands in a diff a reviewer sees, and the refusal is the
  same one the read-only path already gives.

  **`--allow-drop` without `--update`, and `--reason` without `--allow-drop`, are usage errors
  refused during argument parsing.** They are named here so the refusal is a stated rule rather
  than an implementation detail of how the parser happens to be wired, and so a test may assert
  them.

  **The reason describes the change, not where it came from.** It is written verbatim into
  `.github/coverage-baseline.json`, a public committed file, and the natural sentence to reach
  for when a figure drops names a branch, an internal ticket, or a machine. Name what stopped
  being covered and why that was acceptable. The branch it happened on is in the log, and the
  log is not public in the way a file in the tree is. This has to be said where the person
  typing the command will see it -- the `--reason` help text and the module docstring -- and not
  only here, because nobody consults a specification while composing a command line.

  **The reason does not outlive the number it explains.** It describes why *that* figure was
  accepted, so the next update that raises the figure removes it along with the number it was
  about. A file accumulating the reasons for lowerings that have since been undone would be a
  history, and the history is in the log; what this file holds is a claim about now. The
  comparison is exact rather than tolerant, unlike the drop warning next to it: the warning
  forgives a tenth of a point because a single line is worth about that much, and a gate that
  forgives is not what this bullet is for.

  **Refusing is refusing.** A lowering without the flag, or with the flag and no reason, exits
  non-zero and writes nothing. A reason of whitespace alone counts as none: the flag exists to
  make a lowering cost a sentence somebody has to defend, and a space satisfies a presence check
  while defending nothing. A well-typed empty value is the one input that makes a check pass by
  asking nothing, and it is indistinguishable in the output from a check that asked everything
  and was satisfied. The distinction matters because the defect being closed is a file
  that changed when it should not have, so a version of this that warned and wrote anyway would
  close nothing at all.

  **A drop in either figure is a drop, and the two are never averaged.** An update where lines
  fall and branches rise is a lowering and needs the flag, even though the pair read together
  looks like an improvement. The two numbers answer different questions and a fall in one is not
  paid for by a rise in the other; a rule that netted them would let any drop through behind a
  large enough rise somewhere else, which is the accumulate-unseen failure this bullet exists to
  stop, arriving one column over.

  **The reason is `"reason"`, a key in the component's own object beside the two figures.** The
  name is written here rather than left to the implementation because the test that asserts the
  reason does not outlive its number has to look somewhere, and a structural search for the string
  anywhere in the file passes for an implementation that writes it in the wrong place.

  **The flag and the reason on an update that lowers nothing are a usage error.** Not ignored,
  and not recorded: recording would put a justification beside a figure it does not justify, and
  ignoring would let somebody believe a lowering had been explained when nothing was lowered and
  nothing was written. This is the same shape as `--ref` with `--latest` in §11.2 -- two flags
  that together ask no coherent question, answered by refusing rather than by picking one.

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
the §5.1 gates pass, the schema and protocol-type checks are clean, the
`companion-reviewer` and `companion-security-auditor` agents have signed off, and the acceptance
criteria in its issue are satisfied. Not before.

---

## 11. Pinned Facts (verified against the tag in `.github/pinned_symbols.json`)

**This is the allowlist.** Symbols not listed here are out of bounds. Line numbers refer to the
verified tree and are indicative; the names and semantics are what matter.

**One place names the version, and it is not this heading.** `verified_against` in
`.github/pinned_symbols.json` is that place, and CI runs the check against it on every pull
request (§11.2), so it cannot go stale unnoticed. Two drafts of this paragraph claimed that
before it was true: the first said the drift check already read the value, and it did not, and
the second said the value being read was enough, when nothing was asking the question it
answers. Both were caught in review. The claim is only worth making once something executes it.
This document, the reference-hardware note in `CLAUDE.md`, `README.md` and `docs/SETUP.md` all
cite it rather than repeat it.
A version literal that does appear in this document is there for one of two reasons, and never
to restate the current answer. Either it dates a **particular observation** on a particular unit
(F26's Python 3.13.5, F27's `websockets` 17.0.1), or it names a **tag other than the verified
one** in order to say what changed between them, which is what F23 and §5.3.1 do about the
custom-plugins default. Both are claims a reader can check; a second copy of `verified_against`
is not, because nothing keeps it honest.
That distinction is the whole of issue #151: "`agent.mode` is a plain attribute" is not a claim
about pwnagotchi, it is a claim about a version of it, and a reader has to be able to tell which
version without guessing which of two numbers is the current one.

**The tag the facts hold for and the version a given unit runs are allowed to differ**, and this
paragraph is where that is decided rather than somewhere it can be cited from. Issue #151 said
§12 already settled it; §12 is about this project's own SemVer line and says nothing about
pwnagotchi versions at all, and the claim was repeated from the ticket into a draft of this
paragraph before anyone read §12. Recorded because it is the second time in this document that a
citation was inherited rather than checked, and the check costs one grep. When they do, the facts
are what the code was written against and the unit is what it has to survive: the two are named
separately wherever both matter, and the installer resolving its own paths from the running unit
rather than from a literal in this document (§5.3.1) is the pattern for handling the difference.

**The heading claims less than it looks like it claims.** Three entries are not about
`jayofelony/pwnagotchi` at all and cannot be checked at any tag of it: F12 pins `pasv_mode.py` in
`abonforti/pwnagotchi-plugins`, F22 pins upstream `pwnios.py` in `BraedenP232/PwnIOS`, and F29
pins a commit in `pwnagotchi-plugins`. Each carries its own ref. And the manifest encodes names
and shapes, never behaviour: that `agent.session()` blocks, that `pwnagotchi.restart()` touches
what it touches, and the reading of `ready` in manual mode are asserted from source and are not
re-verified by any run. Several entries say outright which of their clauses are not
machine-checked. So the tag covers **what the manifest encodes**, and the rest is evidence a
person read, cited so the next person can read it too.

**This table has a machine-checked twin.** `.github/pinned_symbols.json` encodes the part of it
a script can confirm, and `.github/check_pinned_facts.py` checks it against upstream and opens a
ticket when something no longer holds. **Editing a fact here means editing that file too**, and
`tests/tools/test_pinned_facts.py` fails if a fact gains an entry in one and not the other - two
allowlists drifting apart is the failure the checker exists to prevent, one level up. §11.2
below says which of two questions a given run is asking.

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
| F23 | Custom plugins are loaded from `config['main']['custom_plugins']`. The key is optional in the user configuration: `load_from_path` is called only `if 'custom_plugins' in config['main']`, and the merged view always has it because `defaults.toml` sets it. **The default value is version-dependent and must not be copied anywhere.** It was `/usr/local/share/pwnagotchi/custom-plugins/` at 2.9.5.6 and is `/etc/pwnagotchi/custom-plugins/` at 2.9.5.8, which is why §5.3.1 has the installer read it from the unit it is installing on rather than carry a literal (issue #157). **Machine-checked entries**: F23, F23b pin the current default in `defaults.toml` and the `'custom_plugins' in` guard in `plugins/__init__.py`. The first exists to report the move, not to be depended on. | `pwnagotchi/defaults.toml:27`; `pwnagotchi/plugins/__init__.py` |
| F24 | The user configuration the image merges over the defaults is `/etc/pwnagotchi/config.toml` (`--user-config`), and `plugins/__init__.py` writes back to that same path | `pwnagotchi/cli.py`; `pwnagotchi/plugins/__init__.py` |
| F31 | **On the boot path, `ready` is fired only in auto mode**, because the manual path does not reach the code that fires it. `automata.py` emits `plugins.on('ready', self)` and is reached through `agent.start()`; `cli.py` has two entry points, and `do_auto_mode` calls `agent.start()` while `do_manual_mode` sets the mode, parses the last session and enters a loop that sleeps and emits `internet_available`. **There is a second route and it is not on the boot path**: `toggle_plugin` in `plugins/__init__.py` fires `ready` at the single plugin it is enabling, handing it `view.ROOT._agent`, so a plugin switched on from the web UI receives an agent in any mode. So the rule is about how a unit boots, not about the event: a plugin loaded at boot into manual mode gets no agent, ever (§2.5, §2.6.0, issue #140), and the same plugin enabled by hand afterwards gets one. Manual mode is not hookless either, since that loop emits `internet_available` every five seconds. **Machine-checked entries**: F31a, F31b, F31c pin where the boot path emits `ready`, that `cli.py` never emits it itself, and that the toggle path exists. That `do_manual_mode` does not call `agent.start()` is read from the two function bodies and is not machine-checked. | `pwnagotchi/automata.py`; `pwnagotchi/cli.py`, `do_manual_mode` and `do_auto_mode`; `pwnagotchi/plugins/__init__.py`, `toggle_plugin`, observed on 2.9.5.8 |
| F32 | **A plugin's `options` are the configuration table itself, and pwnagotchi puts nothing of its own in them.** `load()` assigns `plugin.options = config['main']['plugins'][name]` wholesale - the same object, not a copy, and not a dict it composed. So every key a plugin sees is a key somebody wrote in `config.toml`, which is what lets this plugin treat anything outside its own `DEFAULTS` as a misspelling and say so (§2.2.1, issue #137). **One exception, and it is the reason `enabled` is accepted**: `toggle_plugin` writes `config['main']['plugins'][name]['enabled'] = enable` and persists it with `save_config`, so `enabled` arrives in the table without the owner typing it. `load()` also reads `'enabled' in options` to decide what to load at all. **Machine-checked entries**: F32a-c pin the two assignments above and, in F32c, **the absence** of a per-key write or a bulk update into a plugin's options. That third entry is not decoration: the claim this fact rests on is an absence, and two pinned presences would let a write added elsewhere in that file pass straight through (the reason F30b exists, one fact up). The rule of §2.2.1 depends on the first: the day that line composes a dict rather than assigning one, a correct configuration could start warning | `pwnagotchi/plugins/__init__.py`, `load` (the wholesale assignment) and `toggle_plugin` (the `enabled` write), verified on `v2.9.5.6` and `v2.9.5.8`. `enabled` has a third writer, `pwnagotchi/plugins/cmd.py:134,145` (`pwnagotchi plugins enable` / `disable`), which writes the same key into the same table; named so the evidence column is not read as a complete enumeration when it is not |
| F30 | **A plugin is executed but never registered in `sys.modules`.** `load_from_file` is `spec_from_file_location`, `module_from_spec`, `exec_module`, and no assignment to `sys.modules[plugin_name]`. So during import `sys.modules.get(__name__)` is `None`, and **any module-scope construct that resolves its own module through `sys.modules` fails at import**: with `from __future__ import annotations` in force, that includes `@dataclasses.dataclass`, which looks the module up to resolve string annotations while searching for `KW_ONLY` (CPython `dataclasses.py`, `_is_type`). It equally includes `typing.get_type_hints`, and anything else that resolves annotations by name. The plugin was unloadable on every unit for this reason (issue #136), while 925 tests passed, because pytest imports it the ordinary way and an ordinary import registers it. `tests/test_plugin_loads.py` pins the contract. **Machine-checked entries**: F30a, F30b pin that the three calls `load_from_file` is made of are still in that file, and that the file never names `sys.modules` at all. The second is the fact itself rather than a proxy for it; the first only reports that the three calls are present, since it is not scoped to one function and cannot see a fourth call added beside them. | `pwnagotchi/plugins/__init__.py`, `load_from_file`, observed on 2.9.5.8 |
| F25 | The shipped shell profile aliases `custom` to `/etc/pwnagotchi/custom-plugins/`. Nothing else watches that file, so the pin stays and fires if the profile moves. It once recorded a **disagreement** with `defaults.toml` and the rule was drawn from it; at the verified tag the two agree (issue #157), and the rule now rests on F23 | `stage3/06-patches/files/profile` |
| F28 | `agent.view()` returns whatever the agent was constructed with, which on a running unit is **not a bare `View` but a `pwnagotchi.ui.display.Display`**: `cli.py` builds a `Display` and passes it as `view=`. `Display` subclasses `View` and **defines no `get` of its own**, so `agent.view().get(key)` lands on `View.get` - the whole reason the delegation chain below holds and the plugin needs no widget handling. `View.get(key)` delegates to `State.get`, whose contract is **the element's `.value`, never the widget**, and `None` for a key the state does not hold. The initial state always defines `'face'` and `'status'`, so on a running unit both keys exist and both carry a `str`; a `None` can therefore only mean a future version renamed a key. Reading the view is passive - `get` takes the state lock and returns, with no render and no event. **Machine-checked entries**: F28a-d pin `Agent.view`, the body of `View.get`, the body of `State.get`, and the absence of a `get` on `Display`. That last entry exists because the absence is what makes the other three load-bearing, and an absence nobody asserts is an assumption | `pwnagotchi/cli.py:198` (`display = Display(config=config, ...)`), `:204` (`Agent(view=display, ...)`); `pwnagotchi/ui/display.py:10` (`class Display(View)`, no `get` in its body at `4a03bf169e2f`); `pwnagotchi/agent.py:41` (`self._view = view`), `:68-69` (`def view`); `pwnagotchi/ui/view.py:161-162` (`def get` → `return self._state.get(key)`), `:75` (`'face': Text(value=faces.SLEEP, ...)`), `:84` (`'status': Text(value=self._voice.default(), ...)`); `pwnagotchi/ui/state.py:30-32` (`return self._state[key].value if key in self._state else None`) |
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

### 11.2 The checker has two questions, and they are not the same question

**"Do the facts still hold where we said they held?"** is the default: no `--ref` and no
`--latest`. The script reads `verified_against` from the manifest and checks against that tag.
This is what makes the recorded version load-bearing rather than decorative: a fact edited
without re-verifying it fails the next run, and a `verified_against` nobody maintains fails too,
because the facts have to hold at whatever it names.

**"Has upstream moved since?"** is `--latest`, which resolves the newest release and checks
against that. This is discovery, it is expected to fail eventually, and failing is the point: it
is how the project learns that a fact needs re-checking. `.github/workflows/upstream-drift.yml`
runs this one on its schedule and files the ticket.

**The two questions belong in different places, and this is the whole reason for splitting
them.** The default runs **in CI, on every pull request, as a required check**. The `--latest`
run stays out of CI and stays advisory, exactly as `upstream-drift.yml` has always argued: an
upstream release is not a reason for somebody's unrelated pull request to go red. That argument
is about `--latest` and does not carry to the default, which checks a **fixed tag**: nothing
upstream does can turn it red, so the only way it fails is that this repository changed a fact
without re-verifying it, or lost the tag it verifies against. That is precisely a defect worth
blocking a merge for. It needs the network, like every other CI job that installs anything, and
a fetch failure is a red job rather than a silent pass, because a check that cannot run has not
passed. That is the rule §13 states for the security scans, from issue #126, and it applies here
for the same reason.

Two things this needs in order to be true, and both are easy to leave undone. It must be **an
ordinary job that fails the workflow**, never `continue-on-error` and never otherwise advisory.
And its context, `Pinned facts still hold`, must be in the **required checks of the ruleset on
`master`**, which lives in the repository's settings and not in any file here. The second is
worth naming because `.github/workflows/pull-request.yml` auto-merges on required checks alone:
a job outside that list is one an automatic merge does not wait for, and a pull request touching
only Markdown is classified documentation-only, so the job would be exactly the kind of gate that
looks present and is not. `tests/test_ci_gates.py` pins the half that lives in the repository,
including that the job's `name:` is that exact string, since renaming the job would leave the
required context reporting nothing for ever. The context was added to the ruleset on 2026-08-28
(issue #164), deliberately after the branch that introduced the job rather than with it: a
required context that a pull request's head cannot report leaves that pull request waiting
indefinitely, and three were open at the time. Nothing here can check that half, so it was
verified by reading the ruleset back from the API and recording the read-back on the issue, which
is the most a repository can do about a fact kept outside it.

**Putting the checker on a pull-request path changed who can steer it.** The repository it fetches
comes from `upstream` in the manifest, interpolated into a URL, and until this change nothing
validated it: a pull request editing that field redirected CI's outbound fetch wherever it liked.
It is contained rather than dangerous on the pull-request path, where `ci.yml`'s job holds
`contents: read`, a fork's pull request gets no secrets, and the archive is parsed and
string-matched and never executed. **On the cron path it is `upstream-drift.yml`, and that job
holds `contents: read` and `issues: write`**, which is the grant worth protecting and the one the
workflow's own comment names: a token that can file an issue is a token that can be made to file
a fabricated one. It is still a value out of the diff steering a network call, so `upstream` is
now checked against the `owner/name` shape, the same check and the same reasoning
`tools/install-on-pi.sh` applies to `--repo`, and a value that fails it is the check refusing to
run rather than a drift result. What bounds a hostile archive is §11.3, and the finding was the
ordinary consequence of a script's audience widening:
it was written for a weekly cron, where a dead runner cost nothing and nobody could reach it
from outside.

`--ref` names a tag explicitly and overrides both, which is how a candidate version is examined
before anybody decides to move to it. Passing `--ref` and `--latest` together is a **usage error**
and not a precedence rule: they are the two different questions, and a script that silently picked
one would answer a question nobody asked. An **empty** `--ref` is the same usage error and exits
the same way, because the flag was given and names no tag: it asks no question at all, and the
failure mode to avoid is that it stops being distinguishable from not passing the flag. That holds
whether it arrives alone or with `--latest`, and the second is worth saying because `"" and True`
is falsy, so a mutual-exclusion check written on truthiness lets `--ref "" --latest` through the
very rule that exists to catch it.

**A manifest with no `verified_against`, or an empty one, is the check failing rather than
drifting.** It exits the way a malformed manifest already does, above 1, so
`.github/workflows/upstream-drift.yml` turns the job red instead of filing a ticket saying
upstream moved. Nothing moved: the repository lost the answer it checks against, which is a defect
here and reads nothing like the ticket the other exit produces.

Before this was written down the script had one mode, defaulting to the latest release, so the
number in the manifest was read by nothing and the weekly job answered only the second question
(issue #151). The distinction matters because the two failures mean opposite things. The first
means **this repository is wrong about the code it was written against**, which is a defect here.
The second means **upstream changed**, which is news, and may well end in widening what the plugin
tolerates rather than following it.

### 11.3 What the checker says when it fails, and what it refuses to read (issues #150, #165, #167)

§11.2 settled which question the checker is answering. This settles the two things that go wrong
after it has an answer: the sentence it prints, and how much of upstream it is willing to hold in
memory to get there.

Both matter more here than they would elsewhere, and for the same reason. The `--latest` run's
output does not go to somebody watching a terminal; `upstream-drift.yml` files an issue with it,
and the first person to read that issue is reading it cold, weeks later, with no idea what the
run was doing. A wrong sentence there is not a cosmetic defect. It is a wrong instruction to the
only person who will ever act on it.

#### The reason comes from the fact, never from the branch that caught it

A fact fails with the reason **that fact carries**, from its own `why`, and never with a reason
fixed in the branch that caught it. The branch does not know why the entry is there.

The printer already had this right: every drifted entry is reported with its `why` appended
underneath, as `matters because: ...`, and in the JSON output as its own field. What was wrong
was one branch adding a *second* reason of its own on top, and getting it wrong for three of the
four entries that reach it. So the correction is subtraction: the `absent` branch says what it
observed and stops, which is what every other kind already did.

The `absent` kind was written for §11.1, and its message says so: it reports a symbol "which
section 11.1 forbids relying on". **That is false of every entry using it, all four**, and the
first draft of this paragraph said "one of the four", which was itself the defect this section
is about, arriving in the sentence describing it. §11.1 names none of them. They are **positive
claims about upstream's shape**: F28d pins that `display.py` defines no `get`, which is what
makes the delegation chain F28b and F28c pin hold; F30b pins that `plugins/__init__.py` never
names `sys.modules`; F31b pins that `cli.py` never emits `ready`, which is why manual mode is
agent-less; F32c pins that pwnagotchi puts no key of its own into a plugin's options, which is
F32's load-bearing claim. Each of those failing would mean upstream **gained** something, and in
most of them that would be good news: a constraint this project works around would have lifted.
The filed issue would announce it as a forbidden symbol somebody had started relying on and send
the reader to §11.1, which does not mention any of them.

The count was got wrong by reading the kind's name rather than the four entries, which is the
same shortcut the original message took. It is left visible here rather than corrected in place,
because a section arguing that a citation must be checked is the last place to quietly fix an
unchecked one.

**Nothing may cite §11.1 for a fact §11.1 does not mention.** A citation to a section that does
not contain the thing cited is worse than no citation, because it is checkable and it survives
being checked: the reader looks, finds nothing, and concludes they have misunderstood.

This generalises past the `absent` kind, and it is written as a rule rather than as a fix so that
the next kind added does not repeat it: every message a fact failure prints is built from that
fact's own entry. A kind may say what it observed -- what it found, what it expected -- because
that is the branch's own knowledge. Why the observation matters is the entry's, and the entry
already carries it.

#### A malformed manifest is the check failing, never upstream drifting

§11.2 already draws the line: exit 1 means upstream moved and `upstream-drift.yml` files a ticket
saying so, and anything above 1 means the check could not run. `verified_against` already honours
it. `upstream` did not, and the gap was in the shape of the guard rather than in its intent: the
guard tests `".." in repo` and matches a pattern against it, and both raise `TypeError` for a
value that is not a string, while a missing key raises `KeyError`. An uncaught exception exits
**1**, which is the one status that means something specific and false.

**Nothing a fact asserts with may be empty, at any depth, and this is the rule rather than the
three fixes that produced it.** Three times on this branch a security audit found the same shape:
a value of the required type whose *empty* value makes the assertion vacuous, so the check holds
for ever and the required job exits 0. First `facts` itself. Then `params`, where `""` skips the
signature half of a fact. Then `text` and `names`, which between them carry **33 of the 42 real
facts** -- 31 through `text` as a list and 2 more through `names`: emptying one `text` array
turns a red run green while the output still says forty-two
facts were checked. It was isolated end to end -- twenty-seven genuinely drifted facts report
`exit 1, drifted 27`, and the same twenty-seven with `text: []` report `exit 0, drifted 0`.

Emptiness is not the whole of it either: `text: [""]` is non-empty and still vacuous, because
`"" in source` is true of every source there has ever been. So the rule is about what the value
**asserts**, not about its length: every collection a fact checks with must be non-empty, and so
must its elements. A `why` of `""` is the same defect wearing #150's clothes -- the key is
present, the type is right, and the report prints no reason at all.

**And it reaches the fields a fact is written *about*, not only the ones it asserts *with*.** A
first pass guarded `text`, `names`, `params` and `why` and left `file`, `name`, `class` and `id`
type-checked only, which is the same rule applied to half the manifest. An empty one of those does
not make the check vacuous; it makes it **permanently false**, which is worse, because the run
exits 1 and `.github/workflows/upstream-drift.yml` reads exactly that status as upstream having
moved. `name: ""` reports `m.py no longer defines ()`, `file: ""` reports ` no longer exists
upstream`, and an empty `id` prints a drift line with nothing identifying it. That is the same
fabricated drift the `count: "1"` case was fixed for, arriving through four more doors.

The general form is worth more than the instances. **A gate's arguments must be checked for
vacuity and not only for shape**, because a well-typed empty value is the one input that makes a
check pass by asking nothing, and it is indistinguishable in the output from a check that asked
everything and was satisfied.

**A manifest with no facts in it is the check failing, not the check passing.** This is written
first because it is the one way this section could make things worse than it found them. The
`facts` key was read as `manifest["facts"]`, which raised for a manifest that lacked it and went
red; routing every read through a validated local with a default turns that into a clean exit
reporting zero facts checked. `Pinned facts still hold` is a required check on every pull
request, so a diff that empties or drops that key would report success while checking nothing,
which is the failure §5.1's rule about the ban scans exists to prevent, arriving through the
door marked tidiness. `facts` must be present, must be a list, and must not be empty.

**And an unknown key on a fact is refused, not ignored.** The same audit found `params` reaching a
branch with no validation behind it, where `params: ""` makes the signature half of a fact silently
not checked and the run exits 0, and `params: "celsius"` iterates characters and reports that a
function no longer takes `c`, `e`, `i`, `l`, `s`, `u`. A misspelt key is the same defect with a
better disguise: `param:` disables a check and leaves nothing behind to notice. This is the
argument §2.2.1 already makes about an unknown configuration key, applied to the manifest, and it
is stronger here because nobody reads this file except a script. The same
applies one level up: a manifest whose top level is not an object at all is malformed, and
reaching `.get()` on it raises and exits 1, which is the status that means upstream moved.

**Every manifest value is type-checked before it is used, and a value of the wrong type exits the
way a missing one does.** Every value means every value the script reads, not the ones that
happened to have a guard nearby: `upstream`, `verified_against`, `facts` and, on each fact, every
key any branch reaches -- `file`, `id`, `kind`, `why`, and the per-kind fields. The first version
of this change checked four of them and left `file`, `id`, `count`, `name` and `class` unguarded,
and review found three shapes that still exited **1**: a `file` that is a list is unhashable and
raises building the wanted set, a fact with no `id` raises in the reporter, and a `count` that is
a string compares unequal to an integer for ever and reports a drift that is not one. This is
stated for the
manifest as a whole rather than for `upstream` alone, because the failure was not really about
`upstream`: it was about a validator that checked the shape of `facts` and left the fields beside
it to whatever guard happened to be nearest the point of use. A guard written against the value it
expects is not a guard against the value it does not.

**`why` is required, and that is a consequence of #150's subtraction rather than a tidiness
rule.** Before it, the `absent` branch carried a reason of its own; after it, the reason comes
only from the entry, so a fact with no `why` produces a report with no reason at all. A rule that
the reason comes from the fact is only safe if every fact has one.

#### The checker keeps what the manifest asked for, and refuses an archive that will not fit

`fetch_tree` bounds the compressed download and bounds each member as it reads it, and bounds
neither the number of members nor the total it accumulates. Every text member of the whole
upstream repository is decoded and kept, of which the manifest names a handful. An archive of
many small members stays under both existing caps and exhausts the runner.

The changes below, and the first is the one that does the work. They are not counted, for the
reason this section gives twice already:

- **Only the paths the manifest names are kept.** The checker knows exactly which files it is
  about to ask questions of; keeping the rest is not a precaution against anything and is what
  makes the memory a function of upstream's size rather than of this repository's manifest. This
  is the bound that cannot be tuned wrong, because it is not a number.
- **Exit 1 means upstream moved, and nothing else may produce it.** `upstream-drift.yml` files a
  ticket saying pwnagotchi drifted when it sees 1, so every failure that is *not* drift has to
  exit above it. That makes the transport and the archive part of this rule rather than incidental
  to it: a download cut short raises `http.client.IncompleteRead`, and a truncated or corrupt
  gzip stream raises `EOFError` or a `zlib` error while the members are being iterated. None of
  those is an `OSError`, so an `except OSError` around the download does not see them and the
  member loop outside every `try` does not either; the process dies with the interpreter's own
  status, which is 1, and the workflow reports drift that did not happen.

  **There are four `except` clauses across three stages, carrying two different exception sets,
  and the split is load-bearing.** The download has two of them: one for `HTTPError`, with its own
  message, and one for `(URLError, TimeoutError, OSError, HTTPException)`. `tarfile.open()` and the
  member loop have one each, and those two share `(TarError, EOFError, zlib.error, OSError,
  HTTPException)`. Only the last two carry the same set; a reader who unifies all four on the
  strength of a sentence saying "the same exception set" drops either `URLError` and `TimeoutError`
  or `TarError`, `EOFError` and `zlib.error`, which is the silent reopening this paragraph exists
  to prevent -- and an earlier draft of it said exactly that, which is why the arithmetic is spelt
  out here rather than summarised. The messages differ because an archive that would not open and
  one that broke while being read are different failures with different next actions. The split
  is not tidiness: a truncated
  stream raises from the constructor or from the loop depending on where the cut lands and how
  well the content compressed, and the two paths were closed on different days -- the constructor
  one only after a test author's fixture kept going red and the red turned out to be real. A later
  simplification back to one handler would reopen it silently, which is why the arrangement is
  stated here rather than left in a comment.

  **Two silent skips remain in the loop and they are named rather than closed.** `if not
  member.isfile(): continue` fires for a wanted path served as a symlink or a directory, and it
  produces the same fabricated "no longer exists upstream" the decode case was fixed to stop. It
  is left because a pinned path becoming a symlink upstream is arguably real drift, unlike a
  decode failure, so the reporter's sentence is arguable rather than wrong -- but it is arguable,
  which is why it is written down. `if handle is None: continue` is unreachable:
  `TarFile.extractfile()` returns a file object for every member `isfile()` admits, so the branch
  is dead and cannot be covered.

  **A wanted member that is not valid UTF-8 fails the run.** Skipping it leaves the manifest's
  path absent from what was read, and the reporter says the file no longer exists upstream: exit
  1, and a ticket saying pwnagotchi moved. Silently skipping an undecodable member was right when
  every text member of the tree was decoded and skipping binaries was the point; once only the
  paths the manifest names are kept, the skip can fire on nothing but a file the manifest asked
  for by name, and it turns a decode problem into a fabricated fact about upstream.
- **The member count is bounded**, and the archive is iterated rather than indexed:
  `getmembers()` materialises the whole index before the first bound can apply, so a hostile
  archive wins before any cap is consulted. The number lives in the script and not here, and
  that is a different choice from the Node floor in §5.1.1 rather than the same one: there, one
  constraint genuinely has three spellings across three files and the answer is a check between
  them. Here there is no second spelling to check against, because the specification does not
  repeat the number at all. It says the bound exists and what it is derived from -- and
  it is derived from **upstream's whole tree**, not from the manifest. A real pwnagotchi checkout
  runs to a few thousand files, so the cap sits generously above any legitimate release and far
  below what a crafted archive can pack into the compressed-input bound. The manifest's path count
  is what the *total kept* is derived from, in the bullet below; the two bounds answer different
  questions and an earlier draft of this bullet conflated them, which would have sent the next
  person retuning the number to the wrong quantity.
- **The total kept is bounded, by its own number, and the arithmetic is why.** The first
  version of this section said no third bound was needed, because what survives the filter is
  the manifest's path count times the existing per-member cap, "a small fixed quantity". That
  reasoning is sound and the adjective is wrong. The arithmetic, done on 2026-08-30 rather than
  assumed: twenty paths in the manifest times a four mebibyte per-member cap is eighty
  mebibytes of decoded text held in a dictionary, and it grows with every fact anybody adds.
  **Those two numbers are written out here and the other caps are not, deliberately.** A worked
  sum has to show its operands or it is not a sum, and the rule against repeating a constant is
  about a limit that could drift out of step with the code -- this is a calculation done on a
  stated date against the values of that day, which ages into history the way §11's dated
  observations do rather than into being wrong. An
  archive serving each of those real paths padded to just under the per-member cap compresses
  to almost nothing, passes the download cap, passes the member count, and is exactly the shape
  issue #165 was opened about. So there is a total, it is checked as members are kept, and
  exceeding it is the check refusing to run. The number lives at the constant with its
  derivation, not here; what belongs here is that the derivation has to be done rather than
  asserted, which is the whole reason this bullet exists.

- **An oversized member is refused, not truncated**, and this one is not about memory at all.
  The reader stopped at the per-member cap and kept what it had, which is the only limit in the
  file that fails by continuing rather than by stopping. A truncated file is a file this checker
  then asks questions of: a `contains_all` fact whose string sits past the cut reports that
  upstream no longer contains it, `upstream-drift.yml` files an issue saying upstream moved, and
  nothing moved. That is this section's own subject arriving inside the mechanism the section is
  about. Every other bound here refuses; so does this one.

Exceeding any of them is the check refusing to run, above 1, not a drift result.

**Every one of the numeric caps bounds memory, and none bounds decompressed bytes, which is
named here rather than closed.** Two shapes get past them: a PAX or GNU long-name header, whose
payload `tarfile` reads
whole while walking headers, before any of the caps can apply; and a member the manifest does not
name, which is skipped by inflating past it, so `MAX_ARCHIVE_BYTES` of compressed input can
still cost the runner tens of gigabytes of decompression. Both are CPU rather than memory, both
are contained in
practice because codeload generates the archive from real repository content, and neither is
closed. It is written down for the same reason §2.15.1 writes down the `os.walk` gap it does not
close: a known limit invites less doubt about the ones that are closed than a silent one does.
It is contained rather than dangerous -- on the pull-request path `ci.yml`'s job holds
`contents: read`, a fork's pull request gets no secrets, and the archive is parsed and
string-matched and never executed -- so the failure mode is a dead runner and not a compromised
one. `upstream-drift.yml` additionally holds `issues: write`, which is why exhausting its runner
is worth naming at all: the job that dies is the one that would otherwise have filed the ticket.
It is worth fixing anyway, because the script's audience
widened: it was written for a weekly cron where a dead runner cost nothing and nobody outside
could reach it, and §11.2 put it on a pull-request path that gates merges.

## 12. Versioning and releases

One version line for the whole project. The plugin, the frontend package and the git tag always
carry the same number; there is no separate plugin version. The fork lineage from `pwnios.py`
is credited in the plugin header and the README, which is where provenance belongs — encoding
it in a version number would mean maintaining two numbering schemes for one artifact.

**Scheme: SemVer**, tags `vMAJOR.MINOR.PATCH`, with a hyphenated suffix for pre-releases and
`-rcN` as the convention. The grammar accepts any hyphenated suffix and §5.2 enforces it there,
because the release workflow treats a hyphen as the pre-release marker: a spelling this section
allowed but that workflow did not would publish as a stable release, which is the direction that
costs something. `-rcN` remains what to write; the wider grammar is what is refused against.

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
- **The structure is checked, because it had already gone wrong twice.** Under a version
  heading, each type heading appears **at most once**, every type heading is one of Keep a
  Changelog's six, and they appear in that specification's own order. Both defects had landed:
  `Unreleased` carried two separate `### Changed` blocks, and six entries that add something
  sat under the second of them. Neither is a formatting quibble. A reader looking for what a
  cycle added finds the first `Added` heading and stops, and entries filed under the wrong one
  are entries nobody looking for them will see -- which is how the whole of the frontend came
  to be missing from a changelog that had a section for it.
- **What lands in the changelog is what a person installing this would want to know**, which is
  not the same as what the diff did. A change with no user-visible consequence does not need an
  entry, and the judgement is the author's; what is not acceptable is a cycle's worth of work
  arriving with no entry because nobody was looking. Issue #179 carries the question of whether
  that judgement should be gated by anything, and this specification does not pre-empt it.
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
5. The denylist is written in Python's regex dialect and is matched with Python, never with a
   shell `grep`. The patterns need lookahead and lookbehind to carve generic constants out of
   a broader range, and a tool with a different dialect rejects those patterns rather than
   applying them. A scan that skipped a pattern it could not compile is a **failed gate**, not
   a clean one, and must say so: the failure mode this rule exists to prevent is a partial
   scan whose output is indistinguishable from a scan that found nothing.
6. The same rule holds for every gate that scans through an external tool, whatever the
   patterns. Distinguish "no match" from "the scan did not run" by exit status, and fail on
   the second. `git grep` exits 1 when nothing matched and 128 when it could not compile the
   pattern or read the tree, so the common `if grep ...; then fail; fi` shape reports a broken
   scan as a clean one.
   The repository was surveyed once against this rule rather than assumed to satisfy it, and
   the survey found five scans that did not hold it and two that did.
   - The three ban scans in `ci.yml` used the `if grep ...; then fail; fi` shape. Fixed.
   - The auto-merge job in `pull-request.yml` decided documentation-only status through
     `grep -vP ... || true`, which turned a scan that could not run into an empty result and
     therefore into "everything changed is documentation", on the job that merges without a
     human. Fixed, and the pipeline dropped for a here-string so the status read is grep's own
     rather than a pipeline's that happens to agree with it.
   - `.githooks/pre-commit` matched with Python, which is half the rule, but its `git`
     helper returned `.stdout` and ignored the exit status. A `git diff --cached` that failed
     produced an empty staged list, and an empty staged list means "nothing to scan, exit 0":
     the leak gate itself reported a scan it had not run. Fixed.
   - `.github/check_secrets.py` and the drift job in `upstream-drift.yml` already read the
     status of what they call and needed no change. That verdict was about the exit status of
     the commands they run, and it stands. It was not a verdict on what `check_secrets.py` did
     with the files `git ls-files` named for it, and there the same rule was broken twice: a
     file it could not decode was warned about and passed, and a run that scanned nothing at all
     printed a clean verdict. Issue #199, fixed in §5.1.2. A survey answers the question it
     asked.

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

### 13.2 What ships, and what may not be in it

`frontend/public/` is Vite's static directory. Everything in it is copied verbatim into `dist/`,
packed into the release archive, unpacked into `web_root` by `tools/install-on-pi.sh` and served
by the plugin's static server (§2.15). A file that lands there is not untidy, it is published to
anyone who can reach the unit over the tether.

This is written down because it nearly happened. An agent's mutation run left a full copy of
`frontend/src/` at `frontend/public/src/`, twenty-odd files including test fixtures and stale
copies of source. Nothing reached a commit, and the only reason is that somebody read `git status`
before typing `git commit`. `git add -A` sweeps whatever is untracked, so that reading was the
entire protection.

**The rule.** Every directory copied verbatim into the build declares its contents exactly, in
`.github/check_shipped_files.py`. An unexpected file fails, and a declared file that is missing
fails too: a manifest that vanished ships an app that does not install, which is the same mistake
seen from the other side. Both git's index and the disk are read, because the index alone misses
the untracked scratch that is about to be swept in, and the disk alone misses a tracked file
deleted locally. On disk, anything that is not a real directory counts as an entry, symlinks
included, named but never followed: filtering on "is it a file" would let `public/src -> ../src`
through, which is the same incident in one line instead of twenty files. An empty directory is invisible to the check
and deliberately so: git cannot track one, so it carries nothing and ships nothing.

Worth knowing when reading a green result: `globPatterns` in the service worker covers only
`js,css,html,png,svg,webmanifest`, so a stray `.ts` or `.json` under `public/` is copied into the
build and served without ever being precached. That is the shape of the risk rather than a
mitigation of it.

The check runs in CI and in `.githooks/pre-commit`, from the same script so the two cannot drift.
The hook runs it before the denylist scan and runs it whether or not anything is staged, because
the file it catches is usually untracked and there is no staged diff to look at yet. A git that
cannot be read is a failure and not a pass, reported as a message naming the command and its
status rather than as a traceback: the caller is a commit hook, and a stack trace there says
nothing about what to do (§13).

**Which directories.** `frontend/public/` is the only one today. `dist/` is built rather than
copied and is not tracked; `coverage/` and `dev-dist/` are ignored build output; the plugin ships
as a single file the installer names explicitly, not as a directory scan. The declaration is a
mapping rather than a single path so that adding a directory is one entry, and it should be added
the day the build starts copying one rather than the day something unexpected turns up in it.

**What was rejected.** A `.gitignore` entry, which was the obvious first answer. It hides the
mistake instead of reporting it: the scratch directory stops appearing in `git status`, which is
precisely the reading that caught it, and a differently-named directory or a `git add -f` walks
straight past. An ignore rule is right for output nobody should look at and wrong for a place
where anything unexpected is a defect.

**Scratch belongs outside the repository.** No gate helps if the scratch is written somewhere the
gate does not cover, and the agents that produce it are told to work on copies. Their charters
name a path outside the repository rather than "outside the tree you were given", which was the
wording that produced `frontend/public/src/` in the first place: it is outside `frontend/src/`,
and inside the directory that ships.

### 13.3 Two leak gates, and the moment each one runs (issue #203)

This project has two leak gates and they used to run at two different moments, and nothing said
so in one place. That is written here so the next person does not have to reconstruct it from two
files.

- The **owner denylist** is hostnames, addresses, machine names, and it lives outside this
  repository by construction (§13, rule 3). `.githooks/pre-commit` runs it, and it runs **before
  a commit exists**.
- The **generic scan** is PEM key material, provider token shapes, credential assignments and
  file types that must never be tracked. `.github/check_secrets.py` is it, and until this section
  it ran **only in CI**, which is after a push to a public remote.

So the half that catches a private key or a provider token caught it once it was already
published. The denylist gate, the early one, cannot be taught those shapes without turning the
owner's out-of-repository list into a general-purpose scanner it was never meant to be, and the
generic scanner cannot be taught the owner's terms without the runner knowing them, which is the
leak. Neither gate can absorb the other. What was missing was not a merge of the two, it was the
generic one running early as well.

**`git push --force` does not unpublish a blob a crawler already fetched.** That asymmetry is the
whole argument. A hostname committed and removed is embarrassing; a private key committed and
removed has to be rotated, and rotation is a thing you do afterwards rather than instead. The
denylist was put in the hook for this reason and the other half was left in CI without the reason
ever being revisited.

So the hook now runs both, and the split is by **what** each gate knows rather than by **when**
it runs:

| gate | knows | runs |
| --- | --- | --- |
| owner denylist | this owner's infrastructure | pre-commit only, and never in CI |
| `check_secrets.py` | shapes that are credentials to anyone | pre-commit and CI |

CI keeps the generic scan rather than delegating it to the hook, because a hook is a local
courtesy: it is not installed until somebody runs `git config core.hooksPath`, it can be skipped
with `--no-verify`, and a contributor's clone has neither. The hook is where a leak is cheapest
to stop and CI is where it is guaranteed to be noticed, and those are different guarantees.

**The staged content is what is scanned, not the working tree.** A file clean on disk and dirty
in the index must fail, so `check_secrets.py --staged` reads each blob out of the index rather
than off the filesystem. Materialising the index into a temporary directory and scanning that was
the obvious alternative and it is wrong twice: the paths reported would be temporary ones rather
than the ones a person has to go and fix, and the `tests/fakes/fixtures/` exemption is measured
from the repository root (§5.1.2), so every capture fixture in a staged commit would be refused
by name.

**`--staged` reads the repository the script lives in.** The anchor is the script's own
location, not the caller's working directory, because the hook is installed in the repository
being committed to and a mode that scanned whatever repository somebody happened to be standing
in would be a surprising thing to hand a gate. The consequence is worth stating because it costs
an hour to rediscover: a test cannot exercise `--staged` against a throwaway repository without
copying the script into it.

**An empty staging area is not a vacuous scan.** §5.1.2 makes a run that considered nothing exit
`2`, because a verdict earned by examining nothing is a fail-open. `--staged` on a commit that
adds and modifies nothing is a different thing: the index was read successfully and it genuinely
holds nothing to scan, which is what a deletion-only commit looks like. That exits `0` and says
which of the two it was. The distinction is the whole of §5.1.2's argument applied honestly:
"nothing was there" and "nothing was examined" are different sentences, and a gate that confuses
them either fails-open or refuses ordinary commits.

**A rename is a change, and neither gate used to think so.** Both enumerations filtered the
staged diff with `--diff-filter=ACM`, and git classifies a file that is renamed and modified in
one commit as `R`. So `ACM` returned nothing for such a commit, the hook reached its
"nothing staged, exit 0" line, and **the denylist gate did not run at all**. Renaming a file and
adding a credential to it in the same commit walked past the gate whose whole purpose is to stop
that. Found while wiring the generic scan in, reproduced before it was believed: `git mv` plus an
edit yields `R086` and an empty `ACM` listing. Renames and copies are in the filter now, in both
gates, and the letter is spelled out in a comment beside each so nobody narrows it back on the
grounds that a rename is not a change.

The two gates still read different things about that file and that is deliberate. The denylist
reads **the lines this commit added**, because re-reporting a pre-existing line on every commit
trains people to ignore the hook. The generic scan reads **the whole blob from the index**,
because a credential does not have to be on a line this commit touched to be published by it.
Neither is the other's bug.

**Failing closed, in the hook's own terms.** The rule of §13 rule 6 applies to the hook's call as
much as to anything else. A missing interpreter, an unreadable file, a `check_secrets.py` that is
not there, or any exit status that is not one of the three it documents, all refuse the commit
and say which happened. Exit `1` and exit `2` are reported differently, because "this commit
carries a credential" and "the gate is broken" ask the author for different things.

That distinction is a contract and not a wording preference, so it is pinned here rather than
left to whoever edits the hook next. **The hook's own line says `found a possible credential`
when, and only when, the scanner exited 1.** Any other status produces a line naming the status
it got and saying it was not one the hook understands, and that line must not claim a credential
was found.

Naming the phrase is not pedantry, and the reason is worth recording because a test author found
it rather than reasoned it. The hook echoes the scanner's own stderr on both paths, and that
stderr says `possible credential assignment` whenever there was a finding. So a test asserting
only that the combined output mentions a credential passes even when the hook has routed a
finding into the broken-gate branch: the wrapped tool's output satisfies the assertion instead of
the hook's own words. Mutating `returncode == 1` to `returncode == 2` produced the self
contradicting line `the secret scan exited 1, not 0 or 1.` and every test stayed green. What the
test has to assert is the hook's **own** sentence, which is why this section now says what that
sentence contains.

**The order the hook runs its gates**, because it is observable and a test should not have to
guess: the shipped-inventory check first and unconditionally, since the file it catches is
typically untracked and there is no staged diff to scan yet; then the generic secret scan, also
unconditional; then the owner denylist, which depends on `companion.denylist` being configured
and refuses the commit when it is not. Each refusal is final, so the first gate to object is the
one whose message the author sees.

---

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
7. The `.github/` gates and `release.yml` (CI landed early, see §5.1).
8. README, CONTRIBUTING, issue templates.
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
