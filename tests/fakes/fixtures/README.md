# Test fixtures

Real shapes, synthetic content. Nothing here describes a real network, a real device or a real
place: `.gitignore` blocks `*.pcap`, `*.pcapng` and `*.gps.json` everywhere precisely because a
capture, a sidecar or a log excerpt from a real unit is location data about a person, and this
repository is public. This directory is the single negated exception, and it stays synthetic.

MACs are `aa:bb:cc:dd:ee:ff` and friends, SSIDs are `TestNet_001`, and the coordinates are in
the middle of the South Atlantic.

| Path | Exercises |
|---|---|
| `bettercap_session.json` | the cached session snapshot: `gps` with bettercap's capitalised keys, an AP with `clients: null`, an AP with an empty `vendor` |
| `handshakes/TestNet_001_aabbccddeeff.pcapng` | an SSID that legitimately contains an underscore — splitting on the first one loses it |
| `handshakes/TestNet_001_aabbccddeeff.gps.json` | a well-formed sidecar in bettercap shape |
| `handshakes/Legacy_Net_112233445566.pcap` | a legacy `.pcap`, counted in `handshakesTotal` but not in `handshakes` |
| `handshakes/Legacy_Net_112233445566.gps.json` | a malformed sidecar: truncated JSON, must cost its own entry's coordinates and nothing more |
| `handshakes/Ocean_Buoy_998877665544.pcapng` | a capture whose sidecar carries no fix |
| `handshakes/Ocean_Buoy_998877665544.gps.json` | `0.0 / 0.0` — bettercap's "no lock" reading, which is not a position |
| `handshakes/no-bssid-here.pcapng` | a name that does not parse: `bssid` is null, never guessed |
| `pwnagotchi.log` | 400 lines, comfortably more than one seek chunk, for the bounded tail |

The two capture files carry a valid pcap global header and a valid pcapng section header block
respectively, and no packet data. The plugin only reads their name, size and mtime, but a file
that claims to be a capture should at least be one.
