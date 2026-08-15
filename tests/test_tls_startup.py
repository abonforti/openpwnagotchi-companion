"""TLS is mandatory, and its absence means silence.

D4 and pitfall 19: if the certificate or the key is missing, unreadable or
malformed, the plugin logs an explicit error and starts **no listener at all**.
There is no plaintext fallback, and "helpfully" serving HTTP would be worse than
being down - iOS will not load the app over plain HTTP anyway, so the fallback
would serve nobody while quietly exposing a socket on the tether.

The binding assertions here watch `socket.bind` itself rather than the plugin's
own bookkeeping, because "started no listener" is a claim about the kernel.
"""

from __future__ import annotations

import os
import ssl

import pytest

from plugin import companion


def options_for(tmp_path, tls_cert, tls_key, ws_port, http_port):
    web_root = tmp_path / "web"
    web_root.mkdir(exist_ok=True)
    (web_root / "index.html").write_text("<!doctype html><title>t</title>")
    return {
        "interfaces": ["lo"],
        "ws_port": ws_port,
        "http_port": http_port,
        "tls_cert": str(tls_cert),
        "tls_key": str(tls_key),
        "web_root": str(web_root),
        "token": "",
    }


# ---------------------------------------------------------------------------
# build_ssl_context
# ---------------------------------------------------------------------------


def test_a_usable_pair_builds_a_server_context(tls_material):
    context = companion.build_ssl_context(
        str(tls_material["cert"]), str(tls_material["key"])
    )

    assert isinstance(context, ssl.SSLContext)
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_a_missing_certificate_yields_no_context(tmp_path, tls_material):
    assert companion.build_ssl_context(
        str(tmp_path / "absent.crt"), str(tls_material["key"])
    ) is None


def test_a_missing_key_yields_no_context(tmp_path, tls_material):
    assert companion.build_ssl_context(
        str(tls_material["cert"]), str(tmp_path / "absent.key")
    ) is None


def test_both_missing_yields_no_context(tmp_path):
    assert companion.build_ssl_context(
        str(tmp_path / "absent.crt"), str(tmp_path / "absent.key")
    ) is None


def test_a_malformed_certificate_yields_no_context(tmp_path, tls_material):
    broken = tmp_path / "broken.crt"
    broken.write_text("-----BEGIN CERTIFICATE-----\nnot base64 at all\n")

    assert companion.build_ssl_context(str(broken), str(tls_material["key"])) is None


def test_an_empty_certificate_yields_no_context(tmp_path, tls_material):
    empty = tmp_path / "empty.crt"
    empty.write_text("")

    assert companion.build_ssl_context(str(empty), str(tls_material["key"])) is None


def test_a_key_that_is_a_directory_yields_no_context(tmp_path, tls_material):
    directory = tmp_path / "key-shaped-directory"
    directory.mkdir()

    assert companion.build_ssl_context(str(tls_material["cert"]), str(directory)) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a 000 file")
def test_an_unreadable_key_yields_no_context(tmp_path, tls_material):
    key = tmp_path / "server.key"
    key.write_bytes(tls_material["key"].read_bytes())
    key.chmod(0o000)

    assert companion.build_ssl_context(str(tls_material["cert"]), str(key)) is None


def test_a_key_that_does_not_match_the_certificate_yields_no_context(
    tmp_path, tls_material, tls_material_second
):
    assert companion.build_ssl_context(
        str(tls_material["cert"]), str(tls_material_second["key"])
    ) is None


def test_the_failure_is_logged_explicitly(tmp_path, tls_material, caplog):
    with caplog.at_level("ERROR"):
        companion.build_ssl_context(str(tmp_path / "absent.crt"), str(tls_material["key"]))

    assert caplog.records, "a refusal to start must say why in the log"


# ---------------------------------------------------------------------------
# Zero listeners
# ---------------------------------------------------------------------------


def test_a_usable_certificate_does_bind(tmp_path, tls_material, bind_recorder, free_port):
    """The control for the test below: with a good certificate, sockets appear.

    Without this, "no sockets were bound" would pass for the wrong reason on any
    machine where the interface could not be resolved.
    """
    plugin = companion.Companion()
    plugin.options = options_for(
        tmp_path, tls_material["cert"], tls_material["key"], free_port, free_port + 1
    )
    try:
        plugin.on_loaded()

        assert bind_recorder.addresses, "a usable certificate must produce listeners"
        bind_recorder.assert_no_wildcard()
        assert all(host == "127.0.0.1" for host in bind_recorder.hosts)
    finally:
        plugin.on_unload()


@pytest.mark.parametrize(
    "case", ["missing_cert", "missing_key", "malformed", "unreadable"]
)
def test_broken_tls_material_binds_nothing(
    tmp_path, tls_material, bind_recorder, free_port, case
):
    cert = tls_material["cert"]
    key = tmp_path / "server.key"
    key.write_bytes(tls_material["key"].read_bytes())

    if case == "missing_cert":
        cert = tmp_path / "absent.crt"
    elif case == "missing_key":
        key = tmp_path / "absent.key"
    elif case == "malformed":
        key.write_text("-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n")
    elif case == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root can read a 000 file")
        key.chmod(0o000)

    plugin = companion.Companion()
    plugin.options = options_for(tmp_path, cert, key, free_port, free_port + 1)
    try:
        plugin.on_loaded()

        assert bind_recorder.addresses == [], "no plaintext fallback, no listener at all"
    finally:
        plugin.on_unload()


def test_a_refusal_to_start_is_not_a_crash(tmp_path, free_port):
    plugin = companion.Companion()
    plugin.options = options_for(
        tmp_path, tmp_path / "absent.crt", tmp_path / "absent.key", free_port, free_port + 1
    )

    plugin.on_loaded()
    plugin.on_unload()


def test_unloading_twice_is_harmless(tmp_path, tls_material, free_port):
    plugin = companion.Companion()
    plugin.options = options_for(
        tmp_path, tls_material["cert"], tls_material["key"], free_port, free_port + 1
    )
    plugin.on_loaded()

    plugin.on_unload()
    plugin.on_unload()
