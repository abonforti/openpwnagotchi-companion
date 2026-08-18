"""The Content-Security-Policy header and the two beside it (SPEC 2.15.1, issue #67).

The token in `localStorage` (SPEC 4.3.6) is readable by anything that runs script on this
origin, and every SSID, peer name and status line the app renders was chosen by somebody the
owner has never met. This is the layer that stops an escape from executing: no inline script,
no inline style, nothing external but the map tiles.

The one behavioural rule in the section is `connect-src`: it is built per request from the
addresses the plugin is currently bound to, so a rebind changes the next response's policy
without a restart. That is the rule this file spends the most effort on.
"""

from __future__ import annotations

import http.client
import socket
import threading
from http.server import ThreadingHTTPServer

from urllib.parse import urlsplit

import pytest

from plugin import companion

INDEX = "<!doctype html><title>companion</title><div id=app></div>"

WS_PORT = 8082


def parse_csp(header: str) -> dict[str, list[str]]:
    """Splits a CSP header into {directive: [tokens...]}, tolerant of trailing ';' and spacing."""
    directives: dict[str, list[str]] = {}
    for chunk in header.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, *tokens = chunk.split()
        directives[name] = tokens
    return directives


def raw_request(server_address, raw: bytes) -> tuple[bytes, int | None, dict[str, str]]:
    """Sends raw bytes over a fresh TCP connection and reads back an HTTP response.

    A malformed request line, one over the length limit, and an unsupported HTTP
    version are all rejected by `http.server` before `self.path` exists -- no client
    library will construct any of the three, so the only way to drive them is a
    socket that skips it.

    Returns `(data, status, headers)`. `data` is every byte the connection produced;
    it is empty exactly when an unhandled exception in the handler discarded the
    buffered response and closed the socket, which is the failure this function
    exists to tell apart from a legitimate answer. `status` and `headers` are
    populated only when a parseable status line and header block are present --
    which is not guaranteed for every rejection `http.server` produces (see the
    unsupported-HTTP-version test below), so a caller must check `data` for
    "a response arrived at all" and `status`/`headers` separately for
    "and it looked like HTTP".
    """
    with socket.create_connection(server_address, timeout=5) as sock:
        sock.sendall(raw)
        sock.settimeout(5)
        chunks: list[bytes] = []
        try:
            while b"\r\n\r\n" not in b"".join(chunks):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
        data = b"".join(chunks)

    if not data or b"\r\n\r\n" not in data:
        return data, None, {}

    header_blob = data.split(b"\r\n\r\n", 1)[0]
    lines = header_blob.split(b"\r\n")
    try:
        status = int(lines[0].decode("iso-8859-1").split()[1])
    except (IndexError, ValueError):
        return data, None, {}
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            name, _, value = line.partition(b":")
            headers[name.decode("iso-8859-1").strip().lower()] = value.decode(
                "iso-8859-1"
            ).strip()
    return data, status, headers


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "app-a1b2c3.js").write_text("export const app = 1;")
    return root


@pytest.fixture
def addresses():
    # A box, not a bare list: tests that exercise the rebind rule *reassign*
    # box["value"] rather than mutating a list in place. Reassignment is
    # deliberate — the callable returns a fresh `list(...)` copy on every
    # call, so if the implementation ever cached the result of one call
    # instead of invoking the callable per request, that cached copy would
    # be a distinct object from a later reassignment and the test would see
    # the stale value. An in-place mutation of a list held open by the
    # callable's closure would still be visible through the same reference
    # even from a call made once at startup, which would let that bug pass.
    return {"value": ["172.20.10.2"]}


@pytest.fixture
def server(web_root, addresses):
    handler = companion.make_http_handler(str(web_root), lambda: list(addresses["value"]), WS_PORT)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def get(server):
    def _get(path: str):
        connection = http.client.HTTPConnection(*server, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            headers = {name.lower(): value for name, value in response.getheaders()}
            return response.status, headers, response.read()
        finally:
            connection.close()

    return _get


# ---------------------------------------------------------------------------
# The header trio on every response, not only a successful GET
# ---------------------------------------------------------------------------


def _assert_headers_present(headers: dict[str, str]) -> None:
    assert "content-security-policy" in headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"


def test_headers_on_a_successful_response(get):
    status, headers, _ = get("/index.html")

    assert status == 200
    _assert_headers_present(headers)


def test_headers_on_the_spa_fallback(get):
    status, headers, _ = get("/some/deep-link")

    assert status == 200
    _assert_headers_present(headers)


def test_headers_on_the_traversal_rejection(get):
    status, headers, _ = get("/../etc/passwd")

    assert status == 400
    _assert_headers_present(headers)


def test_headers_on_a_directory_request(get):
    status, headers, _ = get("/assets/")

    # SPEC 2.15 leaves the status open (200 via fallback, 403 or 404); only a
    # listing is forbidden. The header trio is not conditional on which one.
    assert status in (200, 403, 404)
    _assert_headers_present(headers)


def test_headers_on_a_hashed_asset(get):
    # The traversal case above is also a 200-that-is-not-index.html for the
    # header trio, but only incidentally: it is really about the 400. This
    # is the direct case, a plain successful fetch of a fingerprinted file.
    status, headers, _ = get("/assets/app-a1b2c3.js")

    assert status == 200
    _assert_headers_present(headers)


# ---------------------------------------------------------------------------
# The fixed directives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,tokens",
    [
        ("default-src", ["'self'"]),
        ("script-src", ["'self'"]),
        ("style-src", ["'self'"]),
        ("font-src", ["'self'"]),
        ("object-src", ["'none'"]),
        ("base-uri", ["'none'"]),
        ("form-action", ["'none'"]),
        ("frame-ancestors", ["'none'"]),
    ],
)
def test_a_fixed_directive_is_exact(get, name, tokens):
    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    assert directives[name] == tokens


def test_no_unsafe_inline_anywhere_in_the_policy(get):
    _, headers, _ = get("/index.html")

    assert "'unsafe-inline'" not in headers["content-security-policy"]


def test_no_unsafe_eval_anywhere_in_the_policy(get):
    _, headers, _ = get("/index.html")

    assert "'unsafe-eval'" not in headers["content-security-policy"]


# ---------------------------------------------------------------------------
# img-src: the one external origin, and only there
# ---------------------------------------------------------------------------


def test_img_src_carries_self_data_and_the_tile_origin(get):
    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    assert set(directives["img-src"]) == {"'self'", "data:", "https://*.tile.openstreetmap.org"}


def test_blob_is_not_in_img_src(get):
    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    assert "blob:" not in directives["img-src"]


def test_the_tile_origin_appears_nowhere_else(get):
    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    for name, tokens in directives.items():
        if name == "img-src":
            continue
        assert "https://*.tile.openstreetmap.org" not in tokens
        # By host rather than by substring: the point is that no spelling of the
        # tile origin reaches another directive, and a host comparison says that
        # without also matching an unrelated origin that merely contains the name.
        hosts = [(urlsplit(token).hostname or "").removeprefix("*.") for token in tokens]
        assert not any(
            host == "tile.openstreetmap.org" or host.endswith(".tile.openstreetmap.org")
            for host in hosts
        )


# ---------------------------------------------------------------------------
# connect-src: built per request from the currently bound addresses
# ---------------------------------------------------------------------------


def test_connect_src_is_self_plus_one_wss_origin_per_bound_address(get):
    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    assert set(directives["connect-src"]) == {"'self'", f"wss://172.20.10.2:{WS_PORT}"}


def test_connect_src_carries_every_bound_address(get, addresses):
    addresses["value"] = ["172.20.10.2", "10.0.0.2"]

    _, headers, _ = get("/index.html")

    directives = parse_csp(headers["content-security-policy"])

    assert set(directives["connect-src"]) == {
        "'self'",
        f"wss://172.20.10.2:{WS_PORT}",
        f"wss://10.0.0.2:{WS_PORT}",
    }


def test_connect_src_follows_a_rebind(get, addresses):
    # The most direct test of the one behavioural rule in SPEC 2.15.1: the
    # set of bound addresses changes between two requests to the same
    # running server, with no restart, and the next response's policy names
    # what is bound now rather than what was bound when the server started.
    _, first_headers, _ = get("/index.html")
    first = parse_csp(first_headers["content-security-policy"])
    assert set(first["connect-src"]) == {"'self'", f"wss://172.20.10.2:{WS_PORT}"}

    addresses["value"] = ["10.0.0.2"]

    _, second_headers, _ = get("/index.html")
    second = parse_csp(second_headers["content-security-policy"])

    assert set(second["connect-src"]) == {"'self'", f"wss://10.0.0.2:{WS_PORT}"}
    assert f"wss://172.20.10.2:{WS_PORT}" not in second["connect-src"]


# ---------------------------------------------------------------------------
# The request line itself rejected: self.path does not exist yet when these
# are handled, and only a raw socket can produce the shapes that trigger it
# ---------------------------------------------------------------------------


def test_headers_survive_a_malformed_request_line(server):
    # http.client cannot be made to send this. Four words is not
    # "METHOD path HTTP/version"; parse_request rejects it on the word-count
    # check, which runs after the trailing word was accepted as a valid
    # version and before self.command or self.path is ever assigned. (A
    # single garbled token instead of four words also rejects with 400, but
    # by a branch that never sets self.request_version away from the class
    # default "HTTP/0.9"; send_header itself then suppresses every header
    # on that shape, independently of self.path. It cannot carry headers
    # on a correct server either, so it cannot tell the fix from the
    # defect and is not used here.)
    data, status, headers = raw_request(server, b"GET /a /b HTTP/1.1\r\n\r\n")

    assert data, "no response arrived at all"
    assert status == 400
    _assert_headers_present(headers)


def test_headers_survive_a_request_line_over_the_length_limit(server):
    # http.server reads the request line with readline(65537); past that cap
    # it rejects the request before parse_request runs at all, so neither
    # self.command nor self.path is ever assigned for this one.
    oversized = b"GET /" + b"a" * 70000 + b" HTTP/1.1\r\n\r\n"

    data, status, headers = raw_request(server, oversized)

    assert data, "no response arrived at all"
    assert status == 414
    _assert_headers_present(headers)


def test_headers_survive_an_unsupported_http_version(server):
    # HTTP/2.0 is rejected by parse_request's version check, which runs
    # before self.path is assigned from the request line's second word.
    #
    # Unlike the other two shapes, this one cannot be used to check the header
    # trio: the >= (2, 0) branch returns before self.request_version is ever set
    # away from the class default "HTTP/0.9", by construction of parse_request,
    # and http.server's own send_header suppresses every header (and the status
    # line) whenever request_version is still "HTTP/0.9" -- verified empirically
    # against this server. That is true on a correctly-fixed server as much as a
    # broken one, so a header assertion here could not distinguish the two; only
    # that a response body still arrives can be pinned.
    data, status, headers = raw_request(server, b"GET / HTTP/2.0\r\n\r\n")

    assert data, "no response arrived at all"
