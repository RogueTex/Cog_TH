"""Minimal GitHub REST client (issues + pull requests) built on httpx."""
from __future__ import annotations

import re
from typing import Any

import httpx


class GitHubError(RuntimeError):
    """Raised when a GitHub API call fails."""


_PR_URL_RE = re.compile(r"github\.com/(?P<repo>[^/]+/[^/]+)/pull/(?P<number>\d+)")


def parse_pull_request_url(url: str) -> tuple[str, int] | None:
    """Extract (repo, number) from a GitHub PR URL, or None if it does not match."""
    match = _PR_URL_RE.search(url)
    if not match:
        return None
    return match.group("repo"), int(match.group("number"))


class GitHubClient:
    """Thin wrapper over the GitHub REST API for the endpoints we need."""

    def __init__(
        self,
        token: str | None,
        *,
        api_base: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._api_base = api_base.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        """Fetch a single issue. Raises GitHubError on failure."""
        url = f"{self._api_base}/repos/{repo}/issues/{issue_number}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:  # network-level failure
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if resp.status_code == 404:
            raise GitHubError(f"Issue {repo}#{issue_number} not found")
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub returned {resp.status_code} for issue {repo}#{issue_number}: "
                f"{resp.text[:300]}"
            )
        return resp.json()

    def get_pull_request(self, repo: str, number: int) -> dict[str, Any]:
        """Fetch a single pull request. Raises GitHubError on failure."""
        url = f"{self._api_base}/repos/{repo}/pulls/{number}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=self._timeout)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if resp.status_code == 404:
            raise GitHubError(f"Pull request {repo}#{number} not found")
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub returned {resp.status_code} for PR {repo}#{number}: {resp.text[:300]}"
            )
        return resp.json()

    def get_pull_request_by_url(self, pr_url: str) -> dict[str, Any]:
        parsed = parse_pull_request_url(pr_url)
        if not parsed:
            raise GitHubError(f"Not a recognizable GitHub PR URL: {pr_url}")
        repo, number = parsed
        return self.get_pull_request(repo, number)

    def create_issue_comment(self, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        """Create an issue comment. Requires a token with issue write access."""
        url = f"{self._api_base}/repos/{repo}/issues/{issue_number}/comments"
        try:
            resp = httpx.post(
                url,
                headers=self._headers(),
                json={"body": body},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub returned {resp.status_code} creating issue comment "
                f"for {repo}#{issue_number}: {resp.text[:300]}"
            )
        return resp.json()

    def update_issue_comment(self, repo: str, comment_id: int, body: str) -> dict[str, Any]:
        """Update an existing issue comment."""
        url = f"{self._api_base}/repos/{repo}/issues/comments/{comment_id}"
        try:
            resp = httpx.patch(
                url,
                headers=self._headers(),
                json={"body": body},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise GitHubError(
                f"GitHub returned {resp.status_code} updating issue comment "
                f"{repo}#{comment_id}: {resp.text[:300]}"
            )
        return resp.json()
