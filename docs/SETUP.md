# Setup

From a working pwnagotchi to the companion installed on your phone.

This is written to be followed straight through, once, by someone who has not read anything else
in this repository. Where a step can fail silently, it says so, because most of the ways this
goes wrong produce no error at all.

## Before you start

You need:

- A pwnagotchi running the [jayofelony fork](https://github.com/jayofelony/pwnagotchi), reachable
  over a tether. Development targets a Pi Zero 2 W. The companion resolves the paths it needs
  from your unit rather than assuming a version's defaults, so it does not ask you to match one.
- Your phone tethered to it, or the unit joined to your phone's Personal Hotspot over Bluetooth.
- A computer with `openssl`, to issue the certificates. Not the pwnagotchi itself.

Throughout this document, **the unit** means the machine running pwnagotchi, and **your
computer** means the one you are typing on. They are never the same machine.

Two facts about the network that save time later. Over an iPhone Personal Hotspot the Bluetooth
PAN subnet is always `172.20.10.0/28`, so the unit's address is somewhere in that range. The USB
gadget interface is `10.0.0.2` and is fine for a laptop, but **an iPhone cannot use it** - if you
are setting this up for a phone, the tether address is the one that matters.

Find the unit's address before continuing. On the unit:

```sh
ip -4 addr
```

The tether address is the one in `172.20.10.0/28` if you are on an iPhone hotspot, and `10.0.0.2`
on the USB gadget. Note which is which: you will need both again when configuring the plugin.

Write down every address you will connect from. You need them all in the certificate, and adding
one later means reissuing.

## 1. Issue the certificates

On your computer, in a clone of this repository:

```sh
./tools/gen-ca.sh --out ~/pwn-pki
./tools/gen-cert.sh --out ~/pwn-pki 172.20.10.2 10.0.0.2
```

Replace those addresses with the ones you wrote down. List every one.

You now have four files in `~/pwn-pki`: `ca.key`, `ca.crt`, `server.key`, `server.crt`.

Keep this directory. `ca.key` signs every certificate you will ever issue for this device, it
never goes on the unit, and it must not end up in a repository. `gen-ca.sh` refuses to
overwrite an existing CA for a reason: regenerating it invalidates every certificate issued from it and every
phone that trusts it.

Why these scripts rather than any certificate: iOS enforces three rules and fails silently when
any is missed. [`CERTIFICATES.md`](CERTIFICATES.md) explains them, and matters if you want to
issue from a CA you already run.

## 2. Copy the server certificate to the unit

```sh
ssh <user>@<unit> 'sudo mkdir -p /etc/pwnagotchi/companion'
ssh <user>@<unit> 'sudo install -m 600 -o root -g root /dev/stdin \
    /etc/pwnagotchi/companion/server.key' < ~/pwn-pki/server.key
ssh <user>@<unit> 'sudo install -m 644 -o root -g root /dev/stdin \
    /etc/pwnagotchi/companion/server.crt' < ~/pwn-pki/server.crt
```

This assumes `sudo` does not ask for a password, which is the case on a stock pwnagotchi image.
If yours prompts, run the two commands from a shell on the unit instead: stdin is bound to the
key here, so a password prompt would read the key's first line as the password.

The key is piped to its final location with the mode set as it is created, rather than copied to
`/tmp` and moved. `/tmp` is world-readable and world-writable: a private key staged there is
briefly readable by every account on the unit, and a symlink planted at that name beforehand
redirects the write somewhere you did not choose. On a single-user unit that is unlikely, but the
correct command is no longer than the incorrect one.

Only the server certificate and its key go to the unit. `ca.key` does not.

## 3. Install the companion

> **The PWA half is not available yet.** The frontend has not been built in this repository and
> there is no published release to download, so `install-on-pi.sh` without arguments exits with
> `no published release found`. Until the first release exists, install the plugin half only:
>
> ```sh
> sudo ./tools/install-on-pi.sh --plugin-only
> ```
>
> Everything from step 4 onwards applies as written, except that there is nothing to open in
> Safari at step 6 yet. When a release exists, or once you can build `frontend/` yourself, the
> full command below installs both halves and this note goes away.

Clone this repository on the unit and run the installer as root:

```sh
sudo ./tools/install-on-pi.sh
```

The default path downloads the latest release, so the unit needs to reach `api.github.com` and
`github.com`. If it does not have internet access, build the archive elsewhere and pass it with
`--archive` - a `SHA256SUMS` must sit beside it, and verification cannot be skipped:

```sh
# on a machine with the frontend built
tar -czf dist.tgz -C frontend/dist .
sha256sum dist.tgz > SHA256SUMS
```

It installs two things: `companion.py` into the unit's custom plugins directory, and the built
PWA into `/var/www/openpwn-companion`.

The plugins directory is **resolved from your unit**, not assumed. The installer reads
`main.custom_plugins` from `/etc/pwnagotchi/config.toml`, and if you have not set it, reads the
same key from the `defaults.toml` of the pwnagotchi installed on the unit. It never carries a
path of its own: that default changed between pwnagotchi versions, and a copy of it would be
right on one and quietly wrong on the other. If neither file answers, the installer stops and
tells you to pass `--plugins-dir`, rather than installing somewhere the unit does not read. It
prints which path it chose either way.

To see what it would do without writing anything:

```sh
sudo ./tools/install-on-pi.sh --dry-run
```

The dry run performs every check the real run performs, including the checksum, and stops before
the first write.

Useful flags: `--tag v0.2.0` for a specific release, `--archive ./dist.tgz` for a local build
(a `SHA256SUMS` must sit beside it; there is no way to skip verification), `--web-root` and
`--plugins-dir` to override the destinations, `--plugin-only` and `--web-only` to do one half,
and `--pwn-prefix` to say where the pwnagotchi package tree lives if it is not under `/opt/.pwn`
(that is where the installer looks for the `defaults.toml` it reads the plugins directory from).

After an update, the previous web root is kept as `/var/www/openpwn-companion.previous`.

## 4. Enable the plugin

The installer does not touch your configuration, because a script that rewrites the main config
of a running pwnagotchi can break far more than it installs. Add this to
`/etc/pwnagotchi/config.toml` yourself:

```toml
[main.plugins.companion]
enabled = true
bind_addresses = ["172.20.10.0/28", "10.0.0.2"]
ws_port = 8082
http_port = 8443
tls_cert = "/etc/pwnagotchi/companion/server.crt"
tls_key = "/etc/pwnagotchi/companion/server.key"
web_root = "/var/www/openpwn-companion"
token = ""
```

`bind_addresses` is the whole security model: the plugin binds only local addresses that match
what you list, never `0.0.0.0`. Do not list a network you would not want the unit reachable from.

Each entry is either an exact address or a CIDR block. The defaults above cover the two normal
cases: `172.20.10.0/28` is the subnet every iPhone Personal Hotspot uses, written as a block
because the phone's DHCP server picks the host part and it is not always the same number, and
`10.0.0.2` is the fixed USB gadget address. If your unit sits on a different tether, replace
these with what `ip -4 addr` showed you earlier.

The plugin refuses a block wider than `/24`, so there is no way to spell "everything" here. An
address that appears on no interface simply gets no listener, and an entry it cannot parse is
logged and skipped rather than taking the plugin down with it.

Configuring **addresses rather than interface names** is deliberate. A name like `bnep0` is
assigned in the order Bluetooth peers connect, so on a unit that is also paired with a laptop it
can belong to the laptop today and the phone tomorrow. An address is a network you chose.

If you are upgrading from an earlier version, the old `interfaces` key is ignored and warned
about in the log rather than honoured. Replace it.

`token` is an optional shared secret, empty by default, and empty means no authentication at all.

**Consider setting one.** A Personal Hotspot is not a private wire between two devices: iOS hands
out `172.20.10.0/28`, a subnet with room for fourteen hosts, and anything else joined to that
hotspot can reach the unit. With no token it gets your handshake list, your GPS track and the
controls, including reboot and shutdown. If the only thing on the hotspot is ever your own phone,
empty is fine. If you tether with other people around, or share the hotspot, it is not.

Set one by putting the same string in `token` here and in the app's settings. Nothing is sent
over the WebSocket before it is accepted, not even the initial state. Note it does not gate the
static files: the HTTPS server on 8443 serves the app itself to anyone who can reach it, and it
is the data behind the app that the token protects. Every option and its default is in
[`SPEC.md`](../SPEC.md) §2.2.

**If your unit runs in manual mode, set `handshake_dir`.** A plugin loaded at boot is handed its
agent only on the auto-mode boot path, and the capture directory is one of the things the plugin
reads from it. Enabling the plugin later from the web UI does hand it one in any mode, but that
is not how it comes up after a restart. Without an agent it does not know where your captures are, so the app shows a dash for
the handshake count rather than a zero it cannot stand behind. Point it at the directory yourself
and the count works with no agent at all. Copy the value your own `config.toml` gives
`bettercap.handshakes`:

```toml
handshake_dir = "<the same path as bettercap.handshakes>"
```

When both are set, this one wins. If you ever report a problem with the handshake count,
say whether the key is set rather than quoting the path: it names a directory on your own unit.

Restart:

```sh
sudo systemctl restart pwnagotchi
```

Check it came up:

```sh
sudo journalctl -u pwnagotchi -n 50 | grep companion
```

You should see it listening on `wss://<ip>:8082` and `https://<ip>:8443`. If instead you see
`no usable TLS material: not starting any listener`, the certificate or key path is wrong, or the
key is unreadable. **There is no plaintext fallback and this is deliberate**: iOS will not install
a web app over `http`, so a fallback would not give you a working app, only an unencrypted socket.

An interface with no address yet is normal and logs `has no address yet, waiting`. The plugin
rebinds by itself when the tether comes up; you do not need to restart it.

## 5. Trust the CA on the phone

AirDrop or email `ca.crt` to the phone, then:

1. Open it. iOS says the profile was downloaded.
2. **Settings → General → VPN & Device Management**, select the profile, **Install**.
3. **Settings → General → About → Certificate Trust Settings**, and turn the switch on for your
   CA.

**Step 3 is not optional and is the one people miss.** Without it there is no error message
anywhere: the HTTPS request fails, the service worker never registers, and the app simply looks
broken. If anything goes wrong later, check this switch first.

## 6. Install the app

In Safari on the phone, with the tether up, open:

```
https://172.20.10.2:8443
```

Use your unit's address. It must be the address, and it must be one you put in the certificate.

Then **Share → Add to Home Screen**. Launch it from the home screen, not from Safari: that is
what gives you the standalone window, and what makes iOS keep the app around.

## If it does not work

Work down this list. It is ordered by how often each one is the answer.

**The app will not load, no error.** The CA is installed but not trusted. Step 5.3.

**Safari says the connection is not private.** The address you opened is not in the
certificate's SAN, or the CA is not installed at all. Check with:
```sh
openssl x509 -in ~/pwn-pki/server.crt -noout -text | grep -A1 "Subject Alternative Name"
```

**Nothing listens on the port.** Check the plugin loaded:
```sh
sudo journalctl -u pwnagotchi -n 100 | grep companion
```
`no usable TLS material` means the paths in `config.toml` are wrong. No companion lines at all
means the plugin is not where pwnagotchi looks - run `sudo ./tools/install-on-pi.sh --dry-run`
and compare the plugins directory it prints against `main.custom_plugins` in your config.

**It worked and then stopped after a tether drop.** Expected, briefly. The plugin re-enumerates
addresses every 30 seconds and rebinds; the app reconnects on its own. If it does not come back,
run `ip -4 addr` and confirm an address matching `bind_addresses` is present again. A phone that
handed out a different address after reconnecting is covered if you configured the block rather
than the single address.

**The app loads but shows nothing.** The static server works and the WebSocket does not. Both
use the same certificate, so this is usually `ws_port` blocked or a `token` set on the unit and
not in the app.

## Updating

```sh
cd /path/to/openpwnagotchi-companion && git pull
sudo ./tools/install-on-pi.sh
sudo systemctl restart pwnagotchi
```

The certificates are unaffected. Reissue only when an address changes or the server certificate
approaches its 820-day expiry - the CA and the trust you set up on the phone stay as they are.
