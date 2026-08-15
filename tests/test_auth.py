"""Optional token authentication.

Two rules, both easy to get subtly wrong.

The comparison is `hmac.compare_digest`, never `==`: a short-circuiting string
compare leaks the token one character at a time to anyone who can measure the
reply, and on a point-to-point Bluetooth link the measurement is clean.

A connection that never authenticates is closed after `auth_timeout`. Without
that deadline an unauthenticated peer holds sockets open indefinitely, which on
a Pi Zero is the whole resource budget.
"""

from __future__ import annotations

import hmac

import pytest

from plugin import companion

TOKEN = "s3cr3t-shared-token"


@pytest.fixture
def token_router(router_factory, agent):
    return router_factory(agent, overrides={"token": TOKEN})


# ---------------------------------------------------------------------------
# Whether auth applies at all
# ---------------------------------------------------------------------------


def test_no_token_configured_means_no_auth(router):
    assert router.auth_required is False


def test_a_configured_token_requires_auth(token_router):
    assert token_router.auth_required is True


def test_an_empty_token_is_not_a_token(router_factory, agent):
    assert router_factory(agent, overrides={"token": ""}).auth_required is False


def test_commands_work_unauthenticated_when_no_token_is_configured(router):
    replies = router.handle({"type": "get_stats"}, authenticated=False)

    assert replies[-1]["type"] == "stats"


# ---------------------------------------------------------------------------
# The comparison
# ---------------------------------------------------------------------------


def test_the_right_token_is_accepted(token_router):
    assert token_router.check_token(TOKEN) is True


@pytest.mark.parametrize(
    "candidate",
    ["", "wrong", TOKEN + "x", TOKEN[:-1], TOKEN.upper(), " " + TOKEN],
)
def test_a_wrong_token_is_refused(token_router, candidate):
    assert token_router.check_token(candidate) is False


@pytest.mark.parametrize("candidate", [None, 42, {"token": TOKEN}, [TOKEN], True])
def test_a_non_string_candidate_is_a_failure_not_a_crash(token_router, candidate):
    assert token_router.check_token(candidate) is False


def test_the_comparison_is_constant_time(token_router, monkeypatch):
    calls = []
    original = hmac.compare_digest

    def recording(left, right):
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(hmac, "compare_digest", recording)

    token_router.check_token(TOKEN)

    assert calls, "the token must be compared with hmac.compare_digest, never with =="


# ---------------------------------------------------------------------------
# The auth message
# ---------------------------------------------------------------------------


def test_an_authenticated_command_is_served(token_router):
    replies = token_router.handle({"type": "get_stats"}, authenticated=True)

    assert replies[-1]["type"] == "stats"


def test_an_unauthenticated_command_is_refused(token_router):
    replies = token_router.handle({"type": "get_stats"}, authenticated=False)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["data"]["code"] == "unauthorized"


def test_the_auth_frame_itself_is_allowed_while_unauthenticated(token_router):
    replies = token_router.handle(
        {"type": "auth", "token": TOKEN}, authenticated=False
    )

    assert [reply["type"] for reply in replies] != ["error"]


def test_a_wrong_token_is_one_error_and_no_detail(token_router):
    replies = token_router.handle(
        {"type": "auth", "token": "wrong"}, authenticated=False
    )

    errors = [reply for reply in replies if reply["type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["data"]["code"] == "unauthorized"
    # Never say whether it was wrong, malformed or absent, and never echo it.
    assert "wrong" not in errors[0]["data"]["message"]
    assert TOKEN not in errors[0]["data"]["message"]


def test_an_auth_frame_without_a_token_is_refused(token_router):
    replies = token_router.handle({"type": "auth"}, authenticated=False)

    assert replies[-1]["type"] == "error"
    assert replies[-1]["data"]["code"] in {"unauthorized", "bad_request"}


def test_an_auth_frame_with_a_non_string_token_is_refused(token_router):
    replies = token_router.handle({"type": "auth", "token": 42}, authenticated=False)

    assert replies[-1]["type"] == "error"


def test_auth_is_accepted_but_unnecessary_without_a_token(router):
    replies = router.handle({"type": "auth", "token": "anything"}, authenticated=False)

    assert [reply["type"] for reply in replies] != ["error"]


def test_the_configured_token_never_appears_in_any_reply(token_router):
    for message in (
        {"type": "auth", "token": "wrong"},
        {"type": "get_stats"},
        {"type": "nonsense"},
    ):
        for reply in token_router.handle(message, authenticated=False):
            assert TOKEN not in repr(reply)


# ---------------------------------------------------------------------------
# The deadline
# ---------------------------------------------------------------------------


def test_the_auth_timeout_default_is_ten_seconds():
    assert companion.option({}, "auth_timeout") == 10


def test_an_unauthenticated_connection_is_closed_after_the_deadline():
    """The deadline of SPEC 2.3.3, which nothing in the module currently exposes.

    `Router` is transport-free by design, and the WSS layer above it publishes
    only `Listeners`, which owns sockets rather than connections. There is no
    seam here to drive a connection that stays silent past `auth_timeout`, so
    this rule cannot be checked at unit level as the module is currently shaped.

    It is a hard requirement (SPEC 2.3.3: "without it an unauthenticated peer can
    hold sockets open"), so the gap is a failure rather than a skip. The fix is
    on the implementation side: expose the per-connection handler, or a
    `close_unauthenticated(deadline)` helper, so the deadline can be exercised
    with a fake clock instead of only in the integration test.
    """
    candidates = ["handle_client", "_handle_client", "ConnectionState", "AuthDeadline"]
    available = [name for name in candidates if hasattr(companion, name)]

    assert available, (
        "no seam exposes the auth deadline; SPEC 2.3.3 requires an unauthenticated "
        f"connection to be closed with 1008 after auth_timeout. Looked for: {candidates}"
    )
