"""The capture directory: filename parsing, listing, and the two hook shapes.

Filename parsing is the part that gets written wrong. pwnagotchi names captures
`SSID_BSSID.pcapng`, and SSIDs legitimately contain underscores, so splitting on
the first one silently truncates half the networks in the list. The rule is to
anchor on the trailing 12-hex-digit group and, when it is not there, to report
`bssid: null` rather than guess - hence the property test as well as the
examples.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from plugin import companion

SSID_ALPHABET = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_- "
)
SSIDS = st.text(alphabet=SSID_ALPHABET, min_size=1, max_size=32)
HEX12 = st.text(alphabet="0123456789abcdefABCDEF", min_size=12, max_size=12)
EXTENSIONS = st.sampled_from([".pcapng", ".pcap"])


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def test_an_ssid_containing_underscores_survives():
    assert companion.parse_handshake_filename("TestNet_001_aabbccddeeff.pcapng") == (
        "TestNet_001",
        "aabbccddeeff",
    )


def test_a_plain_ssid_parses():
    assert companion.parse_handshake_filename("TestNet_aabbccddeeff.pcapng") == (
        "TestNet",
        "aabbccddeeff",
    )


def test_an_unparseable_name_yields_a_null_bssid_not_a_guess():
    assert companion.parse_handshake_filename("no-bssid-here.pcapng") == (
        "no-bssid-here",
        None,
    )


def test_a_short_hex_tail_is_not_a_bssid():
    assert companion.parse_handshake_filename("TestNet_aabbcc.pcapng") == (
        "TestNet_aabbcc",
        None,
    )


def test_a_bssid_only_name_keeps_the_whole_basename_as_the_ssid():
    # There is no SSID in front of the separator, so nothing can be split off.
    assert companion.parse_handshake_filename("aabbccddeeff.pcapng") == (
        "aabbccddeeff",
        None,
    )


def test_the_bssid_is_lowercased():
    assert companion.parse_handshake_filename("TestNet_AABBCCDDEEFF.pcapng")[1] == (
        "aabbccddeeff"
    )


def test_a_leading_directory_is_stripped():
    assert companion.parse_handshake_filename(
        "/etc/pwnagotchi/handshakes/TestNet_001_aabbccddeeff.pcapng"
    ) == ("TestNet_001", "aabbccddeeff")


@given(ssid=SSIDS, bssid=HEX12, extension=EXTENSIONS)
@settings(max_examples=200, deadline=None)
def test_any_ssid_survives_a_well_formed_name(ssid, bssid, extension):
    parsed_ssid, parsed_bssid = companion.parse_handshake_filename(
        f"{ssid}_{bssid}{extension}"
    )

    # The split is anchored at the end, so no underscore inside the SSID can
    # move it: whatever the SSID contains, it comes back whole.
    assert parsed_ssid == ssid
    assert parsed_bssid == bssid.lower()


@given(name=st.text(min_size=0, max_size=48).filter(lambda value: "\x00" not in value))
@settings(max_examples=200, deadline=None)
def test_parsing_never_raises_and_never_invents_a_bssid(name):
    ssid, bssid = companion.parse_handshake_filename(name + ".pcapng")

    assert isinstance(ssid, str)
    if bssid is not None:
        assert len(bssid) == 12
        assert bssid == bssid.lower()
        assert all(character in "0123456789abcdef" for character in bssid)


# ---------------------------------------------------------------------------
# Sidecar naming
# ---------------------------------------------------------------------------


def test_sidecar_name_for_a_pcapng():
    assert companion.sidecar_path_for("/tmp/TestNet_aabbccddeeff.pcapng") == (
        "/tmp/TestNet_aabbccddeeff.gps.json"
    )


def test_sidecar_name_for_a_legacy_pcap():
    # The stock plugin's filename.replace('.pcapng', ...) leaves a .pcap capture
    # with a sidecar nobody will ever find.
    assert companion.sidecar_path_for("/tmp/Legacy_112233445566.pcap") == (
        "/tmp/Legacy_112233445566.gps.json"
    )


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_both_extensions_are_listed(handshake_dir):
    entries, truncated, total = companion.HandshakeStore(str(handshake_dir)).entries()

    names = {entry["filename"] for entry in entries}

    assert names == {
        "TestNet_001_aabbccddeeff.pcapng",
        "Ocean_Buoy_998877665544.pcapng",
        "no-bssid-here.pcapng",
        "Legacy_Net_112233445566.pcap",
    }
    assert truncated is False
    assert total == 4


def test_counts_separate_the_display_number_from_the_honest_one(handshake_dir):
    pcapng, total = companion.HandshakeStore(str(handshake_dir)).counts()

    assert (pcapng, total) == (3, 4)


def test_entries_are_newest_first(handshake_dir):
    for index, name in enumerate(sorted(os.listdir(handshake_dir))):
        os.utime(handshake_dir / name, (1_700_000_000 + index, 1_700_000_000 + index))

    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()

    times = [entry["mtime"] for entry in entries]
    assert times == sorted(times, reverse=True)


def test_an_entry_carries_size_and_mtime(handshake_dir):
    capture = handshake_dir / "TestNet_001_aabbccddeeff.pcapng"
    os.utime(capture, (1_755_000_000, 1_755_000_000))

    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(item for item in entries if item["filename"] == capture.name)

    assert entry["size"] == capture.stat().st_size
    assert entry["mtime"] == pytest.approx(1_755_000_000)
    assert set(entry) == {"filename", "ssid", "bssid", "mtime", "size", "gps"}


def test_an_unparseable_name_lists_with_a_null_bssid(handshake_dir):
    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(item for item in entries if item["filename"] == "no-bssid-here.pcapng")

    assert entry["ssid"] == "no-bssid-here"
    assert entry["bssid"] is None


def test_a_valid_sidecar_is_normalised_onto_the_entry(handshake_dir):
    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(
        item for item in entries if item["filename"] == "TestNet_001_aabbccddeeff.pcapng"
    )

    assert entry["gps"]["lat"] == pytest.approx(-33.512345)
    assert entry["gps"]["lon"] == pytest.approx(-25.098765)
    assert set(entry["gps"]) == {"lat", "lon", "accuracy", "source"}


def test_a_malformed_sidecar_costs_only_its_own_coordinates(handshake_dir):
    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(
        item for item in entries if item["filename"] == "Legacy_Net_112233445566.pcap"
    )

    assert entry["gps"] is None
    assert len(entries) == 4


def test_a_sidecar_without_a_fix_is_not_a_position(handshake_dir):
    # 0.0 / 0.0 is what bettercap writes when it has no lock.
    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(
        item for item in entries if item["filename"] == "Ocean_Buoy_998877665544.pcapng"
    )

    assert entry["gps"] is None


def test_a_capture_without_a_sidecar_reports_null_gps(handshake_dir):
    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()
    entry = next(item for item in entries if item["filename"] == "no-bssid-here.pcapng")

    assert entry["gps"] is None


def test_an_unreadable_file_is_skipped_not_fatal(handshake_dir):
    (handshake_dir / "Dangling_aabbccddeeff.pcapng").symlink_to(
        handshake_dir / "nothing-here.pcapng"
    )

    entries, _, _ = companion.HandshakeStore(str(handshake_dir)).entries()

    assert "Dangling_aabbccddeeff.pcapng" not in {entry["filename"] for entry in entries}
    assert len(entries) == 4


def test_a_missing_directory_lists_nothing_rather_than_raising(tmp_path):
    store = companion.HandshakeStore(str(tmp_path / "not-created"))

    assert store.entries() == ([], False, 0)
    assert store.counts() == (0, 0)
    assert store.newest() is None


def test_the_cap_sets_truncated_rather_than_dropping_silently(tmp_path):
    directory = tmp_path / "many"
    directory.mkdir()
    for index in range(companion.HANDSHAKE_LIMIT + 1):
        capture = directory / f"TestNet_{index:012x}.pcapng"
        capture.write_bytes(b"")
        os.utime(capture, (1_700_000_000 + index, 1_700_000_000 + index))

    entries, truncated, total = companion.HandshakeStore(str(directory)).entries()

    assert len(entries) == companion.HANDSHAKE_LIMIT
    assert truncated is True
    assert total == companion.HANDSHAKE_LIMIT + 1
    # The cap keeps the newest, not an arbitrary slice.
    assert entries[0]["mtime"] == pytest.approx(1_700_000_000 + companion.HANDSHAKE_LIMIT)


def test_exactly_the_cap_is_not_truncated(tmp_path):
    directory = tmp_path / "exact"
    directory.mkdir()
    for index in range(companion.HANDSHAKE_LIMIT):
        (directory / f"TestNet_{index:012x}.pcapng").write_bytes(b"")

    entries, truncated, total = companion.HandshakeStore(str(directory)).entries()

    assert len(entries) == companion.HANDSHAKE_LIMIT
    assert truncated is False
    assert total == companion.HANDSHAKE_LIMIT


def test_files_that_are_not_captures_are_ignored(handshake_dir):
    (handshake_dir / "notes.txt").write_text("not a capture")
    (handshake_dir / "TestNet_001_aabbccddeeff.pcapng.tmp").write_bytes(b"")

    _, _, total = companion.HandshakeStore(str(handshake_dir)).entries()

    assert total == 4


def test_newest_is_the_last_handshake_shape(handshake_dir):
    capture = handshake_dir / "Ocean_Buoy_998877665544.pcapng"
    os.utime(capture, (2_100_000_000, 2_100_000_000))

    newest = companion.HandshakeStore(str(handshake_dir)).newest()

    assert set(newest) == {"filename", "ssid", "bssid", "mtime"}
    assert newest["filename"] == capture.name
    assert newest["ssid"] == "Ocean_Buoy"
    assert newest["bssid"] == "998877665544"


# ---------------------------------------------------------------------------
# The hook, in both shapes
# ---------------------------------------------------------------------------

STRING_SHAPE = ("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66")
DICT_SHAPE = (
    {"mac": "aa:bb:cc:dd:ee:ff", "hostname": "TestNet_001", "channel": 6},
    {"mac": "11:22:33:44:55:66", "vendor": "Synthetic Devices"},
)


def test_both_hook_shapes_normalise_identically(router):
    from_strings = router.on_handshake_message(
        "TestNet_001_aabbccddeeff.pcapng", *STRING_SHAPE
    )
    from_dicts = router.on_handshake_message(
        "TestNet_001_aabbccddeeff.pcapng", *DICT_SHAPE
    )

    assert from_strings["data"] == from_dicts["data"]
    assert from_strings["data"]["ap"] == "aa:bb:cc:dd:ee:ff"


def test_the_station_mac_never_reaches_the_wire(router):
    """SPEC 2.13, issue #33: the hook is handed a station beside the access
    point, and the push carries `{filename, ap, gps}` and nothing else - the
    station is dropped after normalising nothing from it. Checked for both
    argument shapes (F20), because a fix that only strips the string form
    would still leak a client MAC through the dict path."""
    from_strings = router.on_handshake_message(
        "TestNet_001_aabbccddeeff.pcapng", *STRING_SHAPE
    )
    from_dicts = router.on_handshake_message(
        "TestNet_001_aabbccddeeff.pcapng", *DICT_SHAPE
    )

    assert "station" not in from_strings["data"]
    assert "station" not in from_dicts["data"]


def test_the_handshake_push_carries_the_basename(router):
    message = router.on_handshake_message(
        "/etc/pwnagotchi/handshakes/TestNet_001_aabbccddeeff.pcapng", *STRING_SHAPE
    )

    assert message["type"] == "handshake"
    assert message["data"]["filename"] == "TestNet_001_aabbccddeeff.pcapng"
    assert set(message["data"]) == {"filename", "ap", "gps"}


def test_an_unusable_mac_argument_becomes_null_not_a_crash(router):
    # The unusable dict sits in the access point slot: the station slot is
    # no longer read (SPEC 2.13, issue #33), so a value there exercises nothing.
    message = router.on_handshake_message(
        "TestNet_001_aabbccddeeff.pcapng", {"hostname": "no mac here"}, None
    )

    assert message["data"]["ap"] is None


@pytest.mark.parametrize(
    "value,expected",
    [
        ("aa:bb:cc:dd:ee:ff", "aa:bb:cc:dd:ee:ff"),
        ({"mac": "aa:bb:cc:dd:ee:ff"}, "aa:bb:cc:dd:ee:ff"),
        ({"bssid": "aa:bb:cc:dd:ee:ff"}, "aa:bb:cc:dd:ee:ff"),
        ({"mac": "", "bssid": "aa:bb:cc:dd:ee:ff"}, "aa:bb:cc:dd:ee:ff"),
        ({}, None),
        ("", None),
        (None, None),
        (42, None),
        ([], None),
    ],
)
def test_mac_of_accepts_both_shapes_and_refuses_the_rest(value, expected):
    assert companion.mac_of(value) == expected


def test_get_handshakes_replies_with_the_listing(router):
    replies = router.handle({"type": "get_handshakes"}, authenticated=True)

    reply = replies[-1]
    assert reply["type"] == "handshakes_list"
    assert reply["data"]["total"] == 4
    assert reply["data"]["truncated"] is False
    assert len(reply["data"]["entries"]) == 4


# ---------------------------------------------------------------------------
# An unknown directory is not an empty one (SPEC 2.7, issue #153)
# ---------------------------------------------------------------------------
#
# `total: null` with an empty `entries` means the directory is unknown;
# `total: 0` with an empty `entries` means it was found and holds nothing.
# The two cases must be told apart, and the ticket asks for it proven with
# the directory *unset* - not pointed at an empty temporary directory, which
# is the other, "real zero" case and is already covered by
# `test_handshakes.py`'s neighbour, `test_a_missing_directory_lists_nothing_rather_than_raising`
# at the `HandshakeStore` level. Both are exercised here, side by side, so
# the distinction is the assertion rather than an assumption about it.


def test_no_directory_at_all_gives_a_null_total_and_an_empty_listing(
    harness, tls_material, tmp_path
):
    """Deliberately not `router_factory`: its `_handshake_store` closure
    (conftest.py) stands in for "unknown" with its own hand-built
    `_UnknownHandshakeStore` double, which reports the right shape by
    construction and so cannot tell a correct `HandshakeStore(None)` from a
    broken one - the double would report `total: None` even if production's
    `HandshakeStore.entries()` did not. Proving what production actually
    does needs a real `Companion`, driven through `on_loaded` the way
    `test_handshake_dir.py`'s `plugin_factory` does, with neither
    `handshake_dir` set nor an agent supplied.
    """
    plugin = companion.Companion()
    plugin.deps = harness.deps
    plugin.options = {
        **companion.DEFAULTS,
        "enabled": True,
        "tls_cert": str(tls_material["cert"]),
        "tls_key": str(tls_material["key"]),
        "web_root": str(tmp_path),
    }
    try:
        plugin.on_loaded()

        reply = plugin._router.handle({"type": "get_handshakes"}, authenticated=True)[-1]

        assert reply["type"] == "handshakes_list"
        assert reply["data"]["total"] is None
        assert reply["data"]["entries"] == []
        assert reply["data"]["truncated"] is False
    finally:
        plugin.on_unload()


def test_a_directory_that_exists_and_is_empty_gives_a_real_zero(
    router_factory, agent_factory, tmp_path
):
    """The other half of the distinction: a directory the plugin was told
    about, that happens to hold nothing, is a real, known `0` - never
    `null`. Told via `handshake_dir` here, the same way `handle` on a fresh
    `HandshakeStore` over an empty directory already reports `(0, 0)`
    (`test_a_missing_directory_lists_nothing_rather_than_raising` covers the
    even blanker "does not exist" case with the same real-zero answer).
    """
    empty = tmp_path / "empty-but-known"
    empty.mkdir()

    reply = router_factory(
        None, overrides={"handshake_dir": str(empty)}
    ).handle({"type": "get_handshakes"}, authenticated=True)[-1]

    assert reply["data"]["total"] == 0
    assert reply["data"]["entries"] == []
