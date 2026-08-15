# Security

## Reporting a vulnerability

Open a GitHub issue with the `type: security` label for anything that is not itself exploitable
by disclosure. For something that is, use GitHub's private vulnerability reporting on this
repository rather than a public issue.

This is a hobby project maintained in spare time. There is no SLA. Expect a first response
within a week or two.

## Threat model

The companion runs on a pwnagotchi and is reachable only over the tether link between that
device and the phone or laptop driving it — Bluetooth PAN or the USB gadget interface. That is
the whole intended exposure surface.

**In scope:**

- Anything that widens the listener beyond the configured tether interfaces.
- Anything that lets an unauthenticated peer read data or issue commands when a token is
  configured.
- A path traversal or similar in the static file server that serves content outside `web_root`.
- Anything that causes the plugin to start a listener without TLS.
- Secrets reaching the repository, a release artifact, or a log file.

**Out of scope:**

- An attacker who is already root on the pwnagotchi. The plugin runs as root; there is no
  boundary to defend there.
- An attacker who is already associated to your tether link *and* you have chosen not to set a
  token. That is the documented default, deliberately, because a Bluetooth PAN pairing is point
  to point and already authenticated at the link layer. Set `token` if your threat model differs.
- The pwnagotchi's own behaviour: what it captures, what bettercap does, deauthentication.
  Report that upstream.
- Trusting a private CA on your phone. That is a real and unavoidable cost of the design, and
  it is documented rather than hidden. See `docs/CERTIFICATES.md`.

## Design rules the code must hold to

These are enforced by the specification (`SPEC.md`) and by tests, not by good intentions:

1. **Bind to interface IPs only.** Never `0.0.0.0`, never `::`, never a hostname. If a
   configured interface has no address, no listener is started for it.
2. **TLS is mandatory.** If the certificate or key is missing, unreadable or malformed, the
   plugin logs an error and starts **no listener at all**. It never falls back to HTTP, because
   a silent plaintext fallback is worse than being down.
3. **Token comparison is constant time** (`hmac.compare_digest`), and an unauthenticated
   connection is closed after `auth_timeout` seconds.
4. **The static server refuses traversal.** Paths containing `..` are rejected before
   resolution; directory listings are disabled.
5. **Errors do not leak.** An `error` message never contains the token or a filesystem path
   outside `web_root`.
6. **No unhandled exception can kill the event loop.** Every I2C, GPS, socket and file read is
   guarded.

## Secrets: never in this repository

Do not commit, and do not add to a release artifact:

- private keys of any kind (`*.key`, `*.pem`, `*.p12`),
- the CA certificate or key you generated for your own devices,
- shared tokens, API keys, or pwnagotchi credentials,
- your pwnagotchi **whitelist** — it is a list of the SSIDs and BSSIDs of places you care about,
  which is location data about you.

`.gitignore` blocks the common filenames, and CI greps for obvious secret material, but neither
is a substitute for looking at your diff. Certificates and keys belong on your machine and on
your Pi, never in git.

Every user generates their own CA and certificates. There is no shared or bundled key material
anywhere in this project, and there never will be.
