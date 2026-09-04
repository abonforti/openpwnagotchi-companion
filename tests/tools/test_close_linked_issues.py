"""`.github/close_linked_issues.py` (SPEC.md 5.4, issue #252).

`housekeeping.yml` runs this script on a schedule to close the issues a
token-made auto-merge leaves open: `pull-request.yml` enables auto-merge
with the job's own `GITHUB_TOKEN`, and the merge GitHub performs later, as
the Actions app, neither closes the linked issues nor starts another
workflow. This script lists the pull requests merged into `master` in the
last `--days` days through the GraphQL search API, reads each one's
`closingIssuesReferences`, and for every referenced issue that is still
open leaves a comment naming the pull request and the merge time, then
closes it as completed. Nothing is closed for a pull request that was
closed without merging, and nothing is closed for an issue a person
reopened after the merge (a `ReopenedEvent` in its timeline later than
`mergedAt`).

The half that decides (`decide`) is pure and is tested against fixture
data with no network involved. The half that talks to GitHub (`graphql`,
`rest`, `fetch_merged_prs`, `close_issue`, `_send`) is exercised entirely
by monkeypatching the transport - `graphql`/`rest` for the higher-level
functions, `urllib.request.urlopen` for `_send` and for `main`'s
no-`GH_TOKEN` path, which must make no network call at all. No test here
ever reaches the real GitHub API.

Loaded by path, the same way `tests/tools/test_check_dependabot_node.py`
and `tests/tools/test_release_notes.py` load their scripts: `.github` is
not an importable package.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "close_linked_issues.py"


def load_module():
    spec = importlib.util.spec_from_file_location("close_linked_issues", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clo = load_module()


def issue_node(number, state, reopened_at=None):
    reopened_nodes = []
    if reopened_at is not None:
        reopened_nodes.append({"createdAt": reopened_at})
    return {
        "number": number,
        "state": state,
        "timelineItems": {"nodes": reopened_nodes},
    }


def pr_node(number, merged_at, issues):
    return {
        "number": number,
        "mergedAt": merged_at,
        "closingIssuesReferences": {"nodes": issues},
    }


NOW = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# decide(): pure, no network.
# ---------------------------------------------------------------------------


def test_decide_empty_list_returns_two_empty_lists():
    assert clo.decide([]) == ([], [])


def test_decide_closes_a_merged_pr_open_reference():
    merged_prs = [
        pr_node(501, "2026-09-01T10:44:43Z", [issue_node(42, "OPEN")]),
    ]
    to_close, skipped = clo.decide(merged_prs)
    assert to_close == [(42, 501, "2026-09-01T10:44:43Z")]
    assert skipped == []


def test_decide_leaves_a_closed_reference_alone():
    merged_prs = [
        pr_node(502, "2026-09-01T10:44:43Z", [issue_node(43, "CLOSED")]),
    ]
    to_close, skipped = clo.decide(merged_prs)
    assert to_close == []
    assert skipped == [(43, 502, "2026-09-01T10:44:43Z", "issue is not open")]


def test_decide_unmerged_pr_closes_nothing():
    """A pull request with no `mergedAt` (not merged) must not surface any
    of its referenced issues in the to-close list, and must be reported in
    `skipped` with reason "pull request is not merged"."""
    merged_prs = [
        pr_node(503, None, [issue_node(44, "OPEN")]),
    ]
    to_close, skipped = clo.decide(merged_prs)
    assert to_close == []
    assert skipped == [(44, 503, None, "pull request is not merged")]


def test_decide_skips_an_issue_reopened_after_the_merge():
    merged_prs = [
        pr_node(
            504,
            "2026-09-01T10:44:43Z",
            [issue_node(45, "OPEN", reopened_at="2026-09-02T00:00:00Z")],
        ),
    ]
    to_close, skipped = clo.decide(merged_prs)
    assert to_close == []
    assert skipped == [(45, 504, "2026-09-01T10:44:43Z", "issue was reopened after the merge")]


def test_decide_still_closes_an_issue_reopened_before_the_merge():
    merged_prs = [
        pr_node(
            505,
            "2026-09-01T10:44:43Z",
            [issue_node(46, "OPEN", reopened_at="2026-08-31T00:00:00Z")],
        ),
    ]
    to_close, skipped = clo.decide(merged_prs)
    assert to_close == [(46, 505, "2026-09-01T10:44:43Z")]
    assert skipped == []


# ---------------------------------------------------------------------------
# fetch_merged_prs(): monkeypatches graphql, never the network.
# ---------------------------------------------------------------------------


SINCE = (NOW - timedelta(days=2)).date().isoformat()


def _search_result(nodes, search_has_next=False, issue_has_next_by_pr=None):
    """A GraphQL response shaped like the real one: `pageInfo { hasNextPage }`
    on `search` and, independently, on each PR's `closingIssuesReferences`.

    `issue_has_next_by_pr` maps a PR number to the `hasNextPage` value its
    own `closingIssuesReferences.pageInfo` should carry; PRs not listed
    default to `False`.
    """
    issue_has_next_by_pr = issue_has_next_by_pr or {}
    shaped_nodes = []
    for node in nodes:
        node = dict(node)
        node["closingIssuesReferences"] = dict(node["closingIssuesReferences"])
        node["closingIssuesReferences"]["pageInfo"] = {
            "hasNextPage": issue_has_next_by_pr.get(node["number"], False)
        }
        shaped_nodes.append(node)
    return {
        "search": {
            "nodes": shaped_nodes,
            "pageInfo": {"hasNextPage": search_has_next},
        }
    }


def test_fetch_merged_prs_returns_the_search_nodes(monkeypatch):
    nodes = [
        pr_node(506, "2026-09-01T10:44:43Z", [issue_node(47, "OPEN")]),
    ]

    def fake_graphql(query, variables, token):
        assert token == "fixture-token"
        return _search_result(nodes)

    monkeypatch.setattr(clo, "graphql", fake_graphql)

    result = clo.fetch_merged_prs("fixture-token", SINCE)

    assert len(result) == 1
    assert result[0]["number"] == 506


def test_fetch_merged_prs_missing_data_field_fails(monkeypatch):
    monkeypatch.setattr(clo, "graphql", lambda query, variables, token: {})

    with pytest.raises(SystemExit) as excinfo:
        clo.fetch_merged_prs("fixture-token", SINCE)
    assert excinfo.value.code == 1


def test_fetch_merged_prs_missing_search_field_fails(monkeypatch):
    monkeypatch.setattr(clo, "graphql", lambda query, variables, token: {"other": 1})

    with pytest.raises(SystemExit) as excinfo:
        clo.fetch_merged_prs("fixture-token", SINCE)
    assert excinfo.value.code == 1


def test_fetch_merged_prs_missing_search_pageinfo_fails(monkeypatch):
    def fake_graphql(query, variables, token):
        return {"search": {"nodes": []}}

    monkeypatch.setattr(clo, "graphql", fake_graphql)

    with pytest.raises(SystemExit) as excinfo:
        clo.fetch_merged_prs("fixture-token", SINCE)
    assert excinfo.value.code == 1


def test_fetch_merged_prs_search_has_next_page_fails_naming_search(monkeypatch, capsys):
    nodes = [
        pr_node(508, "2026-09-01T10:44:43Z", [issue_node(49, "OPEN")]),
    ]

    def fake_graphql(query, variables, token):
        return _search_result(nodes, search_has_next=True)

    monkeypatch.setattr(clo, "graphql", fake_graphql)

    with pytest.raises(SystemExit) as excinfo:
        clo.fetch_merged_prs("fixture-token", SINCE)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "search" in err.lower()


def test_fetch_merged_prs_closing_issues_has_next_page_fails_naming_it(monkeypatch, capsys):
    nodes = [
        pr_node(509, "2026-09-01T10:44:43Z", [issue_node(50, "OPEN")]),
    ]

    def fake_graphql(query, variables, token):
        return _search_result(nodes, issue_has_next_by_pr={509: True})

    monkeypatch.setattr(clo, "graphql", fake_graphql)

    with pytest.raises(SystemExit) as excinfo:
        clo.fetch_merged_prs("fixture-token", SINCE)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "closingissuesreferences" in err.lower() or "509" in err


# ---------------------------------------------------------------------------
# close_issue(): monkeypatches rest, never the network.
# ---------------------------------------------------------------------------


def test_close_issue_posts_comment_then_patches_closed(monkeypatch):
    calls = []

    def fake_rest(method, path, token, body=None):
        assert token == "fixture-token"
        calls.append((method, path, body))
        return {}

    monkeypatch.setattr(clo, "rest", fake_rest)

    clo.close_issue("fixture-token", 42, 501, "2026-09-01T10:44:43Z", dry_run=False)

    assert len(calls) == 2, f"expected exactly two rest() calls, got {calls!r}"

    comment_method, comment_path, comment_body = calls[0]
    assert comment_method == "POST"
    assert "42" in comment_path
    assert "comments" in comment_path
    assert comment_body["body"].startswith(clo.MARKER)
    assert "#501" in comment_body["body"]
    assert "2026-09-01T10:44:43Z" in comment_body["body"]

    patch_method, patch_path, patch_body = calls[1]
    assert patch_method == "PATCH"
    assert "42" in patch_path
    assert "comments" not in patch_path
    assert patch_body == {"state": "closed", "state_reason": "completed"}


def test_close_issue_dry_run_makes_no_rest_call(monkeypatch, capsys):
    def forbidden_rest(method, path, token, body=None):
        raise AssertionError("rest() must not be called in --dry-run")

    monkeypatch.setattr(clo, "rest", forbidden_rest)

    clo.close_issue("fixture-token", 42, 501, "2026-09-01T10:44:43Z", dry_run=True)

    out = capsys.readouterr().out
    assert "42" in out
    assert "501" in out


# ---------------------------------------------------------------------------
# main(): argument parsing, GH_TOKEN handling, --days.
# ---------------------------------------------------------------------------


def test_main_without_gh_token_exits_one_and_makes_no_network_call(monkeypatch, capsys):
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def forbidden_urlopen(request, timeout=None):
        raise AssertionError("urlopen() must not be called without GH_TOKEN")

    monkeypatch.setattr(clo.urllib.request, "urlopen", forbidden_urlopen)

    with pytest.raises(SystemExit) as excinfo:
        clo.main([])

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "GH_TOKEN" in err


def test_main_dry_run_makes_no_rest_call_and_prints_the_decision(monkeypatch, capsys):
    monkeypatch.setenv("GH_TOKEN", "fixture-token")

    nodes = [
        pr_node(507, "2026-09-01T10:44:43Z", [issue_node(48, "OPEN")]),
    ]
    monkeypatch.setattr(clo, "graphql", lambda query, variables, token: _search_result(nodes))

    def forbidden_rest(method, path, token, body=None):
        raise AssertionError("rest() must not be called in --dry-run")

    monkeypatch.setattr(clo, "rest", forbidden_rest)

    exit_code = clo.main(["--dry-run"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "48" in out
    assert "507" in out


def test_days_flag_puts_a_date_five_days_before_now_into_the_search(monkeypatch):
    fixed_now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)

    class FrozenDateTime(clo.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(clo, "datetime", FrozenDateTime)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")

    captured = {}

    def fake_graphql(query, variables, token):
        captured["query"] = query
        captured["variables"] = variables
        return _search_result([])

    monkeypatch.setattr(clo, "graphql", fake_graphql)

    exit_code = clo.main(["--days", "5"])

    assert exit_code == 0
    expected_date = (fixed_now - timedelta(days=5)).date().isoformat()
    haystack = captured.get("query", "") + json.dumps(captured.get("variables", {}))
    assert expected_date in haystack, (
        f"expected {expected_date!r} (5 days before {fixed_now!r}) in the "
        f"search query or variables, got {haystack!r}"
    )

    search_query = captured["variables"]["searchQuery"]
    for token in (
        "repo:abonforti/openpwnagotchi-companion",
        "is:pr",
        "is:merged",
        "base:master",
        f"merged:>={expected_date}",
    ):
        assert token in search_query, (
            f"expected {token!r} in the search query, got {search_query!r}"
        )


# ---------------------------------------------------------------------------
# _send(): the transport primitive under graphql()/rest(); size cap.
# ---------------------------------------------------------------------------


class FakeResponse:
    """A minimal stand-in for the context manager `urlopen` returns."""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, amount: int = -1) -> bytes:
        if amount is None or amount < 0:
            return self._body
        return self._body[:amount]


def test_send_refuses_a_payload_over_max_bytes(monkeypatch):
    oversized_body = b"x" * (clo.MAX_BYTES + 1)
    monkeypatch.setattr(
        clo.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(oversized_body),
    )
    request = clo.urllib.request.Request("https://api.github.com/graphql")

    with pytest.raises(SystemExit) as excinfo:
        clo._send(request)
    assert excinfo.value.code == 1


def test_send_http_error_fails_naming_the_request(monkeypatch, capsys):
    def raise_http_error(request, timeout=None):
        raise clo.urllib.error.HTTPError("https://api.github.com/graphql", 500, "boom", {}, None)

    monkeypatch.setattr(clo.urllib.request, "urlopen", raise_http_error)
    request = clo.urllib.request.Request("https://api.github.com/graphql")

    with pytest.raises(SystemExit) as excinfo:
        clo._send(request)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "failed" in err.lower()


def test_send_url_error_fails_naming_the_request(monkeypatch, capsys):
    def raise_url_error(request, timeout=None):
        raise clo.urllib.error.URLError("no route to host")

    monkeypatch.setattr(clo.urllib.request, "urlopen", raise_url_error)
    request = clo.urllib.request.Request("https://api.github.com/graphql")

    with pytest.raises(SystemExit) as excinfo:
        clo._send(request)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "failed" in err.lower()


def test_send_non_utf8_body_fails_naming_the_request(monkeypatch, capsys):
    invalid_utf8_body = b"\xff\xfe\xfd"
    monkeypatch.setattr(
        clo.urllib.request,
        "urlopen",
        lambda request, timeout=None: FakeResponse(invalid_utf8_body),
    )
    request = clo.urllib.request.Request("https://api.github.com/graphql")

    with pytest.raises(SystemExit) as excinfo:
        clo._send(request)
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "not valid utf-8" in err.lower()
    assert "https://api.github.com/graphql" in err
