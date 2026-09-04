#!/usr/bin/env python3
"""Closes the issues a token-made auto-merge leaves open (SPEC.md 5.4, issue #252).

`pull-request.yml` enables auto-merge on documentation-only pull requests
with the job's own `GITHUB_TOKEN`, and GitHub then performs the merge as the
Actions app once the required checks pass. A merge made that way does not
close the issues the pull request's body says it closes, even though the
pull request's own `closingIssuesReferences` lists them, and no workflow
runs on the resulting push to `master`, because an event raised with
`GITHUB_TOKEN` never starts another workflow (observed 2026-09-03 with pull
request #239).

This script is the repair `housekeeping.yml` runs on a schedule: it lists
the pull requests merged into `master` in the last `--days` days through the
GraphQL search API, reads each one's `closingIssuesReferences`, and for
every referenced issue that is still open, leaves a comment naming the pull
request and the merge time, then closes it as completed. Nothing is closed
for a pull request that was closed without merging, and nothing is closed
that a person reopened after the merge - that is read from the issue's own
`ReopenedEvent` timeline and skipped.

The half that decides what to close (`decide`) is pure and takes already
parsed data, so it can be tested against fixture responses without a
network call. The half that talks to GitHub (`graphql`, `rest`,
`fetch_merged_prs`, `close_issue`) is plain `urllib`, the way
`check_dependabot_node.py` reaches the GitHub API, authenticated with
`GH_TOKEN` from the environment. A fetch or decode failure is a red job,
never a silent pass (SPEC.md 13, rule 6).

Usage:
    python .github/close_linked_issues.py
        Closes what the last two days of merges into master left open.

    python .github/close_linked_issues.py --days 5 --dry-run
        Widens the window and prints what would be closed without touching
        anything.

Exit status: 0 when the run completes, whether or not anything was closed.
1, named through `fail()`, when `GH_TOKEN` is missing, a GitHub request
fails, or a response is missing a field this script depends on.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

REPO = "abonforti/openpwnagotchi-companion"
API = "https://api.github.com"
GRAPHQL_URL = f"{API}/graphql"
TIMEOUT = 30
# Responses here are a handful of pull requests with a handful of linked
# issues each, not an unbounded feed. The cap exists so a malformed or
# hostile response fails cleanly rather than growing without bound, the
# same reasoning check_dependabot_node.py applies to its own fetch.
MAX_BYTES = 1024 * 1024
DEFAULT_DAYS = 2
# Fixed first line of every comment this script posts, so a closed issue's
# history says unambiguously which job did it and why.
MARKER = "Closed by housekeeping.yml (SPEC 5.4, issue #252):"

MERGED_PRS_QUERY = """
query($searchQuery: String!) {
  search(query: $searchQuery, type: ISSUE, first: 50) {
    pageInfo {
      hasNextPage
    }
    nodes {
      ... on PullRequest {
        number
        mergedAt
        closingIssuesReferences(first: 20) {
          pageInfo {
            hasNextPage
          }
          nodes {
            number
            state
            timelineItems(itemTypes: [REOPENED_EVENT], last: 5) {
              nodes {
                ... on ReopenedEvent {
                  createdAt
                }
              }
            }
          }
        }
      }
    }
  }
}
"""


def fail(message: str) -> NoReturn:
    """Exits 1: the run could not complete, as distinct from completing and
    finding nothing to close.
    """
    print(f"close_linked_issues: {message}", file=sys.stderr)
    raise SystemExit(1)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """POSTs `query` with `variables` to the GraphQL API and returns `data`.

    Any transport failure, non-2xx status, malformed JSON, a `errors` field
    in the response, or a missing `data` field all go through `fail()` -
    there is no partial result this script can act on.
    """
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "openpwnagotchi-companion-housekeeping",
        },
    )
    body = _send(request)
    try:
        response = json.loads(body)
    except json.JSONDecodeError as err:
        fail(f"GraphQL response is not valid JSON: {err}")
    if not isinstance(response, dict):
        fail("GraphQL response is not a JSON object")
    if response.get("errors"):
        fail(f"GraphQL request returned errors: {response['errors']}")
    data = response.get("data")
    if not isinstance(data, dict):
        fail("GraphQL response has no data field")
    return data


def rest(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    """Sends `method` to `API + path` and returns the decoded JSON body.

    Any transport failure, non-2xx status, or malformed JSON goes through
    `fail()`.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "openpwnagotchi-companion-housekeeping",
        },
    )
    payload = _send(request)
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as err:
        fail(f"response from {method} {path} is not valid JSON: {err}")


def _send(request: urllib.request.Request) -> str:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as err:
        detail = err.read(MAX_BYTES)[:2000] if hasattr(err, "read") else b""
        fail(f"GitHub request to {request.full_url} failed: HTTP {err.code} {detail}")
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as err:
        fail(f"GitHub request to {request.full_url} failed: {err}")
    if len(raw) > MAX_BYTES:
        fail(
            f"response from {request.full_url} is larger than {MAX_BYTES} "
            "bytes; refusing to read it"
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as err:
        fail(f"response from {request.full_url} is not valid UTF-8: {err}")


def decide(
    merged_prs: list[dict[str, Any]],
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str, str]]]:
    """Splits `merged_prs` into what to close and what to skip.

    `merged_prs` is the already-parsed list of pull request nodes from
    `MERGED_PRS_QUERY`. Returns `(to_close, skipped)`: `to_close` is a list
    of `(issue_number, pr_number, merged_at)`; `skipped` is a list of the
    same three values plus a reason as a fourth element. A pull request
    without `mergedAt` set closes nothing and is skipped with reason
    `"pull request is not merged"` - it was matched by the search but is
    not actually merged, even though the search query already asked for
    merged pull requests only.

    An issue is closed only when its `state` is `OPEN` and its timeline
    holds no `ReopenedEvent` whose `createdAt` is later than the pull
    request's `mergedAt`. The window is not applied here: the search query
    that produced `merged_prs` already did.
    """
    to_close: list[tuple[int, int, str]] = []
    skipped: list[tuple[int, int, str, str]] = []
    for pr in merged_prs:
        pr_number = pr["number"]
        merged_at = pr["mergedAt"]
        if not merged_at:
            for issue in pr["closingIssuesReferences"]["nodes"]:
                skipped.append(
                    (issue["number"], pr_number, merged_at, "pull request is not merged")
                )
            continue
        merged_at_dt = _parse_timestamp(merged_at)
        for issue in pr["closingIssuesReferences"]["nodes"]:
            issue_number = issue["number"]
            if issue["state"] != "OPEN":
                skipped.append((issue_number, pr_number, merged_at, "issue is not open"))
                continue
            reopened_after = False
            for event in issue["timelineItems"]["nodes"]:
                created_at = _parse_timestamp(event["createdAt"])
                if created_at > merged_at_dt:
                    reopened_after = True
                    break
            if reopened_after:
                skipped.append(
                    (issue_number, pr_number, merged_at, "issue was reopened after the merge")
                )
                continue
            to_close.append((issue_number, pr_number, merged_at))
    return to_close, skipped


def fetch_merged_prs(token: str, since: str) -> list[dict[str, Any]]:
    """Runs `MERGED_PRS_QUERY` for pull requests merged into `master` in
    `REPO` since `since` (a `YYYY-MM-DD` date) and returns the list of pull
    request nodes, each already carrying its `closingIssuesReferences`.

    A response missing any field this script depends on fails through
    `fail()` rather than being treated as an empty result. So does a
    response that says more pages exist for either the search itself or a
    pull request's `closingIssuesReferences`: the window this script acts
    on has to be complete, not a silently truncated prefix of it.
    """
    search_query = f"repo:{REPO} is:pr is:merged base:master merged:>={since}"
    data = graphql(MERGED_PRS_QUERY, {"searchQuery": search_query}, token)
    try:
        search = data["search"]
        nodes = search["nodes"]
        has_more_prs = search["pageInfo"]["hasNextPage"]
    except (KeyError, TypeError) as err:
        fail(f"GraphQL response is missing search.nodes or search.pageInfo: {err}")
    if has_more_prs:
        fail(
            "GraphQL search for merged pull requests has more pages than "
            f"this script reads; widen the window or raise the page size (since={since})"
        )
    prs = []
    for node in nodes:
        if not isinstance(node, dict) or "number" not in node:
            # A search over `type: ISSUE` can return other node shapes; a
            # missing "number" means this one did not match the
            # `... on PullRequest` fragment at all.
            continue
        try:
            closing_issues = node["closingIssuesReferences"]
            has_more_issues = closing_issues["pageInfo"]["hasNextPage"]
        except (KeyError, TypeError) as err:
            fail(
                "GraphQL response's pull request node is missing "
                f"closingIssuesReferences.pageInfo: {err}"
            )
        if has_more_issues:
            fail(
                "GraphQL response's pull request "
                f"#{node.get('number')} has more closingIssuesReferences "
                f"than this script reads (since={since})"
            )
        try:
            prs.append(
                {
                    "number": node["number"],
                    "mergedAt": node["mergedAt"],
                    "closingIssuesReferences": closing_issues,
                }
            )
        except KeyError as err:
            fail(f"GraphQL response's pull request node is missing a field: {err}")
    return prs


def close_issue(
    token: str, issue_number: int, pr_number: int, merged_at: str, dry_run: bool
) -> None:
    """Comments and closes `issue_number` as completed, naming `pr_number`
    and `merged_at`.

    `--dry-run` prints what would happen and does not call GitHub.
    """
    comment = (
        f"{MARKER} pull request #{pr_number} merged at {merged_at} should "
        "have closed this issue, and did not because the merge ran under "
        "the Actions token."
    )
    if dry_run:
        print(
            f"[dry-run] would comment and close issue #{issue_number} (pull request #{pr_number})"
        )
        return
    rest("POST", f"/repos/{REPO}/issues/{issue_number}/comments", token, {"body": comment})
    rest(
        "PATCH",
        f"/repos/{REPO}/issues/{issue_number}",
        token,
        {"state": "closed", "state_reason": "completed"},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"how many days back to look for merged pull requests (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be closed and touch nothing",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN", "")
    if not token:
        fail("GH_TOKEN is not set; refusing to run without a token")

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=args.days)).date().isoformat()

    merged_prs = fetch_merged_prs(token, since)
    to_close, skipped = decide(merged_prs)

    for issue_number, pr_number, merged_at in to_close:
        print(
            f"closing issue #{issue_number}: closed by pull request "
            f"#{pr_number}, merged {merged_at}"
        )
        close_issue(token, issue_number, pr_number, merged_at, args.dry_run)

    for issue_number, pr_number, merged_at, reason in skipped:
        print(
            f"skipping issue #{issue_number} (pull request #{pr_number}, "
            f"merged {merged_at}): {reason}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
