"""Access point mapping.

The whole reason this file exists is one key: bettercap AP dicts carry `mac` and
have no `bssid` at all (SPEC F4). Upstream `pwnios.py` reads `ap['bssid']`, which
is always empty, so every access point it reports is anonymous. That bug is the
one thing these tests must make impossible to reintroduce.
"""

from __future__ import annotations

import pytest

from plugin import companion


def test_bssid_comes_from_mac(router):
    points = router.access_points()

    assert [point["bssid"] for point in points] == [
        "aa:bb:cc:dd:ee:ff",
        "11:22:33:44:55:66",
    ]


def test_a_bssid_key_on_the_source_dict_is_not_read(router_factory, agent_factory):
    agent = agent_factory(
        access_points=[{"mac": "aa:bb:cc:dd:ee:ff", "bssid": "de:ad:be:ef:00:00"}]
    )

    assert router_factory(agent).access_points()[0]["bssid"] == "aa:bb:cc:dd:ee:ff"


def test_hostname_falls_back_to_the_mac(router_factory, agent_factory):
    agent = agent_factory(access_points=[{"mac": "aa:bb:cc:dd:ee:ff", "hostname": ""}])

    assert router_factory(agent).access_points()[0]["hostname"] == "aa:bb:cc:dd:ee:ff"


def test_clients_none_counts_as_zero(router):
    assert router.access_points()[1]["clients"] == 0


def test_clients_are_counted_not_carried(router):
    point = router.access_points()[0]

    assert point["clients"] == 1
    assert isinstance(point["clients"], int)


def test_missing_keys_get_defaults(router_factory, agent_factory):
    agent = agent_factory(access_points=[{"mac": "aa:bb:cc:dd:ee:ff"}])

    point = router_factory(agent).access_points()[0]

    assert point == {
        "bssid": "aa:bb:cc:dd:ee:ff",
        "hostname": "aa:bb:cc:dd:ee:ff",
        "channel": 0,
        "rssi": 0,
        "encryption": "",
        "vendor": "",
        "clients": 0,
    }


def test_an_empty_list_maps_to_an_empty_list(router_factory, agent_factory):
    agent = agent_factory(access_points=[])

    assert router_factory(agent).access_points() == []


def test_the_side_effecting_getter_is_never_called(router, agent):
    # FakeAgent.get_access_points raises: it fires plugins.on('wifi_update'),
    # drives channel selection and advances the epoch (SPEC F3).
    router.access_points()
    router.handle({"type": "get_access_points"}, authenticated=True)


def test_map_access_point_is_pure(bettercap_session):
    source = bettercap_session["wifi"]["aps"][0]
    before = dict(source)

    companion.map_access_point(source)

    assert source == before


@pytest.mark.parametrize("channel,expected", [(6, 6), ("6", 6), (None, 0), (0, 0)])
def test_channel_is_always_an_integer(channel, expected):
    mapped = companion.map_access_point({"mac": "aa:bb:cc:dd:ee:ff", "channel": channel})

    assert mapped["channel"] == expected
    assert isinstance(mapped["channel"], int)


def test_wifi_update_uses_the_same_mapping(router):
    from_reply = router.access_points()
    pushed = router.push("wifi_update", from_reply)

    assert pushed["type"] == "wifi_update"
    assert pushed["data"] == from_reply
