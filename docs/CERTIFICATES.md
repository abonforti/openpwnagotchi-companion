# Certificates

The companion is served over HTTPS and WSS, and there is no plaintext fallback. That is not
caution for its own sake: iOS will not install a web app served over `http`, and a service
worker will not register without a secure context, so a plaintext mode would not give you a
working app. It would give you an unencrypted socket that looks like it works.

Since the Pi is reached at a bare IP address over a tether, no public certificate authority can
issue for it. You run your own, which takes two commands. The rest of this document is about
why the certificate has to look exactly the way it does, because iOS enforces three rules and
breaks **silently** when any of them is missed.

## The short version

```sh
./tools/gen-ca.sh --out ~/pwn-pki
./tools/gen-cert.sh --out ~/pwn-pki 172.20.10.7 10.0.0.2
```

Copy `server.crt` and `server.key` to the Pi, point `tls_cert` and `tls_key` at them, and
install `ca.crt` on the phone. Full walkthrough in [SETUP.md](SETUP.md).

Keep `~/pwn-pki` off the Pi and out of any repository. `ca.key` signs every certificate you will
ever issue for this device; anyone holding it can impersonate your pwnagotchi to your phone.

## The three rules iOS enforces

### 1. The CA must carry `basicConstraints` marked critical

```
X509v3 Basic Constraints: critical
    CA:TRUE
```

Not `CA:TRUE` alone. The `critical` flag is what tells a verifier it may not ignore the
extension, and both Apple's stack and Python's `ssl` module refuse a root without it.

This one is worth dwelling on because of how it fails. `curl` accepts such a root happily, so
the natural first test succeeds and you conclude the certificate is fine. Then Safari refuses
the connection with a generic message, and nothing anywhere mentions `basicConstraints`. This
exact failure has already cost one debugging session on this setup, which is why
`tools/gen-ca.sh` sets it and `tests/tools/test_certs.py` asserts it.

### 2. The server certificate must list IP addresses as `iPAddress`, never `DNS`

```
X509v3 Subject Alternative Name:
    IP Address:172.20.10.7, IP Address:10.0.0.2
```

When a client connects to `https://172.20.10.7:8443`, it looks for that address among the
`iPAddress` entries of the SAN. An address written as a `DNS:` name is not consulted, no matter
that it reads identically to a human.

The Common Name is not consulted either. It has been deprecated for host verification for years
and Apple's stack ignores it outright, so a certificate whose CN is the right address and whose
SAN is missing or wrong will be rejected while looking correct in every human-readable dump.

`tools/gen-cert.sh` refuses a hostname argument for this reason: accepting one would put a
`DNS:` entry in the SAN, and the failure surfaces on the phone as what looks like a network
problem rather than a certificate one.

List **every** address you will reach the Pi on. Over an iPhone Personal Hotspot the BT PAN
subnet is always `172.20.10.0/28`, and the USB gadget interface is `10.0.0.2` but is not usable
from a phone. Adding an address later means reissuing.

### 3. Validity must not exceed 825 days

Apple rejects server certificates valid for longer, for anything issued after 1 July 2019. The
generator uses 820 days, which leaves enough slack that clock skew between the machine that
issued the certificate and the phone cannot push it over the line.

The CA itself is not subject to this and is issued for ten years. Only the leaf is capped.

## The silent failure that will cost you an evening

Installing the CA profile on the phone is **not** enough. iOS treats a manually installed root
as untrusted until you enable it explicitly:

**Settings → General → About → Certificate Trust Settings**, then turn on the switch for your
CA.

If you skip this, there is no error telling you so. The HTTPS request fails, the service worker
never registers, Safari shows a generic failure page or an empty one, and the app simply appears
broken. Every symptom points away from the certificate.

If the app will not load, check this switch before anything else.

## Using your own PKI

Nothing here requires the shipped scripts. If you already run EasyRSA, `step-ca`, an internal
Active Directory CA or anything else, issue from it - the requirements are only these:

**Root certificate**

| Extension | Value |
|---|---|
| `basicConstraints` | `critical, CA:TRUE` |
| `keyUsage` | `critical, keyCertSign, cRLSign` |

**Server certificate**

| Extension | Value |
|---|---|
| `basicConstraints` | `CA:FALSE` |
| `keyUsage` | `critical, digitalSignature, keyEncipherment` |
| `extendedKeyUsage` | `serverAuth` |
| `subjectAltName` | `IP:<addr>` for every address, and no `DNS:` entry |
| validity | 825 days or fewer |
| signature | SHA-256; RSA 2048 or better |

An intermediate in the chain is fine. The server must then present the full chain, leaf first,
in the file named by `tls_cert`.

## Verifying before you go near the phone

Check the certificate on the machine that issued it. A round trip through AirDrop, a profile
install and a trust switch is a slow way to discover a missing SAN.

```sh
# The CA: critical must appear on the same line as the extension name.
openssl x509 -in ca.crt -noout -text | grep -A1 "Basic Constraints"

# The server certificate: IP Address entries, and no DNS entry at all.
openssl x509 -in server.crt -noout -text | grep -A1 "Subject Alternative Name"

# The chain.
openssl verify -CAfile ca.crt server.crt

# The dates. notAfter minus notBefore must be 825 days or fewer.
openssl x509 -in server.crt -noout -dates

# The key belongs to the certificate: these two must print the same hash.
openssl rsa -in server.key -noout -modulus | openssl sha256
openssl x509 -in server.crt -noout -modulus | openssl sha256
```

You can also test the whole handshake the way the phone will, from any machine that trusts the
CA file:

```sh
openssl s_client -connect 172.20.10.7:8443 -CAfile ca.crt -verify_return_error </dev/null
```

`Verify return code: 0 (ok)` means the chain and the SAN are right. Note that this proves the
certificate is correct; it does not prove the phone trusts the root, which is the separate
switch described above.

## Renewal

The server certificate expires after 820 days. Reissuing is one command and does not disturb the
CA or the phone, because the root is unchanged and still trusted:

```sh
./tools/gen-cert.sh --out ~/pwn-pki 172.20.10.7 10.0.0.2
```

Copy the new `server.crt` and `server.key` to the Pi and restart pwnagotchi. Reissue the same
way if the Pi's address changes or you add one.

**Do not regenerate the CA to fix a server certificate problem.** `gen-ca.sh` refuses to
overwrite an existing CA without `--force` precisely because that is the tempting wrong move: a
new root invalidates every certificate issued from the old one and every phone that trusts it,
and the damage shows up later as an app that will not load, with nothing pointing back at the
moment the CA was replaced.

## Threat model, briefly

The private CA protects the link between your phone and your pwnagotchi. It is a real root on
your phone: anything it signs will be trusted by that device. Two consequences.

Keep `ca.key` on the machine that issued it, with the mode the script gave it (`0600`), and
nowhere else. It never needs to go to the Pi.

Prefer a CA dedicated to this over an existing organisational one. A root whose only job is to
sign one certificate for one device is a root whose compromise costs you one device.

If `ca.key` leaks, remove the profile from the phone, generate a new CA with `--force`, reissue,
and install the new root. There is no revocation path worth relying on here: nothing in this
setup checks a CRL or OCSP.
