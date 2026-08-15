# openpwnagotchi-companion

A free, self-hostable **PWA companion for pwnagotchi** (jayofelony fork). It does what the paid
iOS app "Pwnagotchi Companion" does, plus the things it does not, and it runs entirely on your
own device: the pwnagotchi serves the app, your phone installs it from the Pi, nothing is hosted
by anyone else.

> **Status: in development.** The specification is complete and locked
> ([`SPEC.md`](SPEC.md)); the protocol is defined ([`docs/schemas/`](docs/schemas/)); the plugin
> and the frontend are being built. There is no release yet.

<!-- TODO: screenshot of the dashboard on an iPhone, once the frontend exists -->

## What it does

| | paid iOS app | this |
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
| GPS with wardriving map | partial | yes, on-Pi source first with browser fallback |
| Price | $5.99 | free, GPL-3.0 |

The backend of the paid app was always open source — only the iOS client was paid. This project
forks and hardens that plugin, and replaces the client with an installable web app.

## How it works

```
iPhone  ──BT PAN──  Pi (pwnagotchi)
   │                    ├── companion plugin: WSS :8082, HTTPS :8443
   └── PWA installed from https://<pi-ip>:8443
```

The plugin binds **only** to your tether interfaces (`bnep0`, `usb0`) — never to `0.0.0.0` — and
only over TLS. There is no plaintext fallback and no cloud component.

## Security model, honestly

To install a PWA and open a WebSocket, iOS requires real HTTPS. The Pi has no public hostname,
so you generate a **private CA**, issue a server certificate carrying your Pi's IP addresses as
`iPAddress` SANs, and install and fully trust that CA on your phone.

That is a real trust decision and the docs do not soften it:

- The CA certificate must have `basicConstraints = critical, CA:TRUE`, or iOS will not let you
  trust it, and HTTPS then fails **silently**.
- The server certificate needs `IP:` SANs, not `DNS:`, and at most 825 days of validity.
- Keep the CA private key on your machine. Never commit it. See [`SECURITY.md`](SECURITY.md).

Full walkthrough: [`docs/SETUP.md`](docs/SETUP.md) and
[`docs/CERTIFICATES.md`](docs/CERTIFICATES.md).

Beyond TLS, the exposure surface is the tether link itself, which is point to point. An optional
shared token is available and off by default.

## Quickstart

Not yet — there is no release to install. Once there is, the shape is:

1. `tools/gen-ca.sh` then `tools/gen-cert.sh <pi-ip> [<pi-ip> …]` on your machine.
2. Copy `server.crt` / `server.key` to the Pi, install `plugin/companion.py`, enable it in
   `config.toml`.
3. `tools/install-on-pi.sh` to fetch the built PWA onto the Pi.
4. Install and fully trust `ca.crt` on the phone, open `https://<pi-ip>:8443` in Safari, and
   Add to Home Screen.

See [`docs/SETUP.md`](docs/SETUP.md).

## Repository layout

| path | what |
|---|---|
| `plugin/` | the pwnagotchi plugin |
| `frontend/` | Svelte + TypeScript + Vite PWA |
| `docs/schemas/` | JSON Schema: the authoritative wire format |
| `docs/` | setup, certificates, protocol, pinned pwnagotchi facts |
| `tools/` | CA and certificate generation, protocol type generation, on-Pi install |
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
