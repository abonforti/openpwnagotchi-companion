# openpwnagotchi-companion

A free, self-hostable **PWA companion for pwnagotchi** (jayofelony fork). It does what the paid
iOS app [Pwnagotchi Companion](https://apps.apple.com/us/app/pwnagotchi-companion/id6751243451)
by Braeden Pelletier does, plus the things it does not, and it runs entirely on your own device:
the pwnagotchi serves the app, your phone installs it from the unit, nothing is hosted by anyone
else.

> **Status: in development.** The specification is complete and locked
> ([`SPEC.md`](SPEC.md)); the protocol is defined ([`docs/schemas/`](docs/schemas/)); the plugin
> and the frontend are being built. There is no release yet.

<!-- TODO: screenshot of the dashboard on an iPhone, once the frontend exists -->

## What it does

| | [Pwnagotchi Companion](https://apps.apple.com/us/app/pwnagotchi-companion/id6751243451) (paid, iOS) | this |
|---|---|---|
| Live stats (uptime, battery, temperature, channel, counters) | yes | yes |
| Face and status | yes | yes |
| Nearby access points | yes | yes |
| AUTO / MANU switch, reboot, shutdown | yes | yes |
| PASV toggle (with [`pasv_mode`](https://github.com/abonforti/pwnagotchi-plugins/blob/master/pasv_mode.py) installed) | no | yes |
| Detailed handshake list, with GPS tags | no | yes |
| Peer / mesh view | no | yes |
| Live log viewer | no | yes |
| Full e-ink screen mirror | partial | yes |
| GPS with wardriving map | partial | yes, on-device source first with browser fallback |
| Price | $5.99 (App Store, at the time of writing) | free, GPL-3.0 |
| Platforms | iOS 16.6+ | anything with a modern browser |

The backend of that app was always open source: only the iOS client was paid. Its plugin,
[`pwnios.py`](https://github.com/BraedenP232/PwnIOS), is published under the GPL, so this project
forks and hardens it and replaces the client with an installable web app. Credit where it is
due - that project made this one possible by publishing the hard part.

If you want a polished native iOS app and would rather pay for it than build one, buy theirs.
This exists for people who want the source, the self-hosting, and the three features it lacks.

## How it works

```
iPhone  ──BT PAN──  pwnagotchi unit
   │                    ├── companion plugin: WSS :8082, HTTPS :8443
   └── PWA installed from https://<unit-ip>:8443
```

The plugin binds **only** to your tether interfaces (`bnep0`, `usb0`), never `0.0.0.0`, and
only over TLS. There is no plaintext fallback and no cloud component.

## Security model, honestly

To install a PWA and open a WebSocket, iOS requires real HTTPS. The unit has no public hostname,
so you generate a **private CA**, issue a server certificate carrying the unit's IP addresses as
`iPAddress` SANs, and install and fully trust that CA on your phone.

That is a real trust decision and the docs do not soften it:

- The CA certificate must have `basicConstraints = critical, CA:TRUE`, or iOS will not let you
  trust it, and HTTPS then fails **silently**.
- The server certificate needs `IP:` SANs, not `DNS:`, and at most 825 days of validity.
- Keep the CA private key on your machine. Never commit it. See [`SECURITY.md`](SECURITY.md).

Full walkthrough: [`docs/SETUP.md`](docs/SETUP.md) and
[`docs/CERTIFICATES.md`](docs/CERTIFICATES.md).

Beyond TLS, the exposure surface is the tether link itself, and it is worth being precise about
what that link is. An iPhone Personal Hotspot hands out `172.20.10.0/28`: a shared subnet with
room for fourteen hosts, not a private wire between two devices. Anything else joined to that
hotspot can reach the plugin. An optional shared token is available and **off by default**,
which is a convenience default rather than a claim that nobody else is there. If you tether with
other people around, set one.

## Compatibility

**No compatibility guarantee across pwnagotchi versions.** Verified against jayofelony 2.9.5.6.

That is a deliberate non-promise. Declaring a supported range would mean committing to test every
version inside it, and a range that is claimed but not tested is worse than no claim at all,
because it reads as covered. Instead, a scheduled job checks the pwnagotchi symbols this plugin
depends on against the current upstream and opens an issue when one of them drifts.

If it breaks on your version, that is a bug worth reporting, not a configuration you got wrong.
The symbols the plugin is allowed to touch, each with the source line that proves it exists, are
in [`SPEC.md`](SPEC.md) §11.

## Quickstart

Not yet: there is no release to install. Once there is, the shape is:

1. `tools/gen-ca.sh` then `tools/gen-cert.sh <unit-ip> [<unit-ip> ...]` on your machine.
2. Copy `server.crt` / `server.key` to the unit, install `plugin/companion.py`, enable it in
   `config.toml`.
3. `tools/install-on-pi.sh` to install both halves on the unit: the plugin and the built PWA.
4. Install and fully trust `ca.crt` on the phone, open `https://<unit-ip>:8443` in Safari, and
   Add to Home Screen.

See [`docs/SETUP.md`](docs/SETUP.md).

## Repository layout

| path | what |
|---|---|
| `plugin/` | the pwnagotchi plugin |
| `frontend/` | Svelte + TypeScript + Vite PWA |
| `docs/schemas/` | JSON Schema: the authoritative wire format |
| `docs/` | setup, certificates, protocol, pinned pwnagotchi facts |
| `tools/` | CA and certificate generation, protocol type generation, on-unit install |
| `tests/` | plugin test suite (runs without a pwnagotchi installation) |
| `SPEC.md` | the complete build specification |

Each user builds and serves their own copy. Nothing here phones home, and there is no shared
instance to sign up for.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The short version: the repository is English-only,
`docs/schemas/` is edited before any code that speaks the protocol, pwnagotchi APIs come from
the pinned list in `SPEC.md` §11 and nowhere else, and changes ship with their tests.

## Licence and credit

GPL-3.0. This project is a fork of the `pwnios.py` plugin from
[`BraedenP232/PwnIOS`](https://github.com/BraedenP232/PwnIOS), used and redistributed under the
GPL with attribution preserved. Thanks to that project for publishing the backend.

pwnagotchi itself is by [evilsocket](https://github.com/evilsocket) and maintained in the
[jayofelony fork](https://github.com/jayofelony/pwnagotchi).

---

Proudly built on [Raspberry Pi](https://www.raspberrypi.com/). _Slowly_.
