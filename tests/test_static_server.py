"""The HTTPS static server that serves the PWA.

Two of these behaviours break the app silently rather than loudly, which is why
they are pinned (D14):

A minimal Raspberry Pi OS image's `mimetypes` database does not know
`.webmanifest`. Served as `application/octet-stream`, iOS ignores the manifest
and simply refuses to install the app, with no error anywhere. A service worker
served as anything but `text/javascript` is rejected by the browser the same
quiet way.

And `SimpleHTTPRequestHandler` answers a deep link with 404, so a refresh on
`/peers` breaks a single-page app that worked a moment earlier.

The handler is exercised over plain HTTP here. The TLS wrap belongs to the
listener (SPEC 2.3), and testing it again through this door would only be
testing `ssl`.
"""

from __future__ import annotations

import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from plugin import companion

INDEX = "<!doctype html><title>companion</title><div id=app></div>"


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "sw.js").write_text("self.addEventListener('install', () => {});")
    (root / "registerSW.js").write_text("// registration shim")
    (root / "manifest.webmanifest").write_text('{"name":"companion"}')
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (root / "assets" / "app-a1b2c3.js").write_text("export const app = 1;")
    (root / "assets" / "app-a1b2c3.mjs").write_text("export const app = 1;")
    (root / "assets" / "app-a1b2c3.css").write_text(":root{color:#fff}")
    (root / "assets" / "app-a1b2c3.js.map").write_text('{"version":3}')
    (root / "assets" / "data.json").write_text('{"ok":true}')
    (root / "assets" / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "assets" / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / "assets" / "engine.wasm").write_bytes(b"\x00asm\x01\x00\x00\x00")
    return root


@pytest.fixture
def server(web_root):
    handler = companion.make_http_handler(str(web_root), lambda: ["172.20.10.2"], 8082)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def get(server):
    def _get(path: str, method: str = "GET"):
        connection = http.client.HTTPConnection(*server, timeout=5)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            # Header names are case-insensitive on the wire and the stdlib sends
            # "Content-type"; comparing case-sensitively would test the casing
            # rather than the type.
            headers = {name.lower(): value for name, value in response.getheaders()}
            return response.status, headers, response.read()
        finally:
            connection.close()

    return _get


# ---------------------------------------------------------------------------
# MIME types
# ---------------------------------------------------------------------------


def test_the_webmanifest_type_is_declared_explicitly(get):
    status, headers, _ = get("/manifest.webmanifest")

    assert status == 200
    assert headers["content-type"].startswith("application/manifest+json")


@pytest.mark.parametrize("path", ["/sw.js", "/assets/app-a1b2c3.js", "/assets/app-a1b2c3.mjs"])
def test_javascript_is_text_javascript(get, path):
    status, headers, _ = get(path)

    assert status == 200
    assert headers["content-type"].startswith("text/javascript")


# ---------------------------------------------------------------------------
# Charset (SPEC 4.2, SPEC 2.15)
# ---------------------------------------------------------------------------

# WebKit does not sniff an undeclared encoding and falls back to a single-byte
# one, so every non-ASCII byte in the document - the whole pwnagotchi face
# vocabulary, any SSID with one - renders as mojibake. The <meta charset> tag
# in the document only helps within its own 1024-byte window and only for a
# document opened outside the plugin; the header set here is the one that
# always wins and the one asserted below.


def test_index_html_carries_the_utf8_charset_header(get):
    _, headers, _ = get("/index.html")

    assert headers["content-type"] == "text/html; charset=utf-8"


def test_the_spa_fallback_also_carries_the_utf8_charset_header(get):
    # The fallback is a different code path from a direct index.html request
    # (SPEC 2.15) and it is the one a deep link or a refresh hits, so the
    # charset rule has to hold there independently rather than being inferred
    # from the direct-request case above.
    _, headers, _ = get("/peers")

    assert headers["content-type"] == "text/html; charset=utf-8"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/index.html", "text/html"),
        ("/assets/app-a1b2c3.css", "text/css"),
        ("/assets/data.json", "application/json"),
        ("/assets/icon-192.png", "image/png"),
        ("/assets/icon.svg", "image/svg+xml"),
        ("/assets/engine.wasm", "application/wasm"),
    ],
)
def test_the_declared_types(get, path, expected):
    status, headers, _ = get(path)

    assert status == 200
    assert headers["content-type"].startswith(expected)


@pytest.mark.parametrize("path", ["/favicon.ico", "/assets/app-a1b2c3.js.map"])
def test_nothing_in_the_declared_set_falls_back_to_octet_stream(get, path):
    status, headers, _ = get(path)

    assert status == 200
    assert not headers["content-type"].startswith("application/octet-stream")


# ---------------------------------------------------------------------------
# SPA fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/peers", "/map/detail", "/settings?host=1"])
def test_a_deep_link_serves_the_app_shell(get, path):
    status, _, body = get(path)

    assert status == 200
    assert body.decode() == INDEX


def test_a_missing_asset_is_a_404_not_the_app_shell(get):
    # Falling back to index.html for a missing script hands the browser HTML
    # where it expected JavaScript, which fails later and further away.
    status, _, body = get("/assets/missing-d4e5f6.js")

    assert status == 404
    assert body.decode() != INDEX


def test_a_missing_image_is_a_404(get):
    status, _, _ = get("/assets/absent.png")

    assert status == 404


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/../etc/passwd",
        "/assets/../../etc/passwd",
        "/..",
        "/assets/..%2f..%2fetc/passwd",
        "/a/../../b",
    ],
)
def test_a_traversal_attempt_is_rejected_before_resolution(get, path):
    status, _, _ = get(path)

    assert status == 400


def test_the_rejection_does_not_leak_a_path_outside_the_web_root(get):
    _, _, body = get("/../etc/passwd")

    assert b"/etc/passwd" not in body


# ---------------------------------------------------------------------------
# Directory listings
# ---------------------------------------------------------------------------


def test_a_directory_is_never_listed(get):
    status, _, body = get("/assets/")

    assert status in (200, 403, 404)
    assert b"app-a1b2c3.js" not in body
    assert b"Directory listing" not in body


def test_the_root_serves_the_app_not_a_listing(get):
    _, _, body = get("/")

    assert body.decode() == INDEX


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/index.html", "/sw.js"])
def test_the_shell_and_the_service_worker_are_not_cached(get, path):
    _, headers, _ = get(path)

    assert "no-cache" in headers.get("cache-control", "")


def test_a_hashed_asset_may_be_cached(get):
    _, headers, _ = get("/assets/app-a1b2c3.js")

    assert "no-cache" not in headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_a_missing_web_root_does_not_take_the_server_down(tmp_path):
    handler = companion.make_http_handler(
        str(tmp_path / "never-installed"), lambda: ["172.20.10.2"], 8082
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        connection.request("GET", "/")
        status = connection.getresponse().status
        connection.close()

        assert status in (404, 500, 503)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_requests_are_not_logged_at_info(get, caplog):
    with caplog.at_level("INFO"):
        get("/index.html")

    # A request log at info floods the pwnagotchi log on a device that also
    # writes the UI there.
    assert not [record for record in caplog.records if "GET" in record.getMessage()]


# ---------------------------------------------------------------------------
# Symlinks (SPEC 2.15, issue #244)
# ---------------------------------------------------------------------------

MARKER = "eel-cave-turquoise-41"


def test_a_symlink_inside_the_web_root_pointing_outside_it_is_not_served(get, web_root, tmp_path):
    """Before the fix `translate_path` only checked the request path for `..`,
    never the real path a symlink resolves to, so a link planted inside the
    root could still hand back a file that lives outside it. This test fails
    on that code: it gets the marker back with 200 instead of the app shell.
    """
    outside = tmp_path / "outside-the-root"
    outside.mkdir()
    (outside / "secret.txt").write_text(MARKER)
    try:
        (web_root / "leak").symlink_to(outside / "secret.txt")
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    status, _, body = get("/leak")

    assert MARKER.encode() not in body
    assert status == 200
    assert body.decode() == INDEX


def test_a_dangling_symlink_whose_target_would_sit_outside_the_root_gets_the_shell(
    get, web_root, tmp_path
):
    """The resolution does not require the target to exist: a link whose
    target would sit outside the root is treated as outside even when there is
    nothing there, and answered with `index.html` rather than with an error
    that says where it pointed."""
    try:
        (web_root / "leak").symlink_to(tmp_path / "never-created" / "secret.txt")
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    status, _, body = get("/leak")

    assert status == 200
    assert body.decode() == INDEX
    assert b"never-created" not in body


def test_a_dangling_symlink_whose_target_would_sit_inside_the_root_is_missing(get, web_root):
    """A dangling link that stays inside the root reaches the base class, and
    a name with a dot in it is an asset request, so it is a missing file, not
    the app shell."""
    try:
        (web_root / "gone.txt").symlink_to(web_root / "never-created.txt")
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    status, _, body = get("/gone.txt")

    assert status == 404
    assert b"never-created" not in body


def test_a_symlink_that_stays_inside_the_web_root_is_followed(get, web_root):
    (web_root / "inside.txt").write_text("kept inside the root")
    try:
        (web_root / "alias").symlink_to(web_root / "inside.txt")
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    status, _, body = get("/alias")

    assert status == 200
    assert body.decode() == "kept inside the root"


def test_a_sibling_directory_whose_name_starts_with_the_root_is_outside(tmp_path):
    """The web root is `tmp_path/www` and the sibling is `tmp_path/www-evil`: a
    guard built on `str.startswith` on the resolved path would treat the
    sibling as inside the root, because its name starts with the root's.
    `os.path.commonpath` on path components does not make that mistake.
    """
    root = tmp_path / "www"
    root.mkdir()
    (root / "index.html").write_text(INDEX)
    evil = tmp_path / "www-evil"
    evil.mkdir()
    (evil / "secret.txt").write_text(MARKER)
    try:
        (root / "leak").symlink_to(evil / "secret.txt")
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    handler = companion.make_http_handler(str(root), lambda: ["172.20.10.2"], 8082)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        connection.request("GET", "/leak")
        response = connection.getresponse()
        status = response.status
        body = response.read()
        connection.close()

        assert MARKER.encode() not in body
        assert status == 200
        assert body.decode() == INDEX
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_web_root_itself_may_be_a_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "index.html").write_text(INDEX)
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"filesystem does not support symlinks: {error}")

    handler = companion.make_http_handler(str(link), lambda: ["172.20.10.2"], 8082)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        status = response.status
        body = response.read()
        connection.close()

        assert status == 200
        assert body.decode() == INDEX
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
