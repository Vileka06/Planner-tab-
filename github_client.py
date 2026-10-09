"""Thin, well-behaved wrapper around the public GitHub REST API.

Design notes
------------
* This module is the ONLY place that performs HTTP requests.
* It never imports Streamlit, so it can be tested from a plain Python shell.
* Authentication is optional. If a token is supplied it is used; otherwise the
  API is called anonymously (lower rate limit). Tokens are never hardcoded —
  the caller is responsible for reading them from an environment variable or
  Streamlit secrets and passing them in.
* Only public data is requested. Nothing here invents commits or edits
  timestamps; we simply report what GitHub returns.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import requests

GITHUB_API_BASE = "https://api.github.com"

# GitHub usernames: 1-39 chars, letters/digits/hyphens, no leading/trailing '-'
_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class GitHubError(Exception):
    """Base class for all errors raised by this module."""


class UserNotFoundError(GitHubError):
    """Raised when a username does not exist (HTTP 404 on the user endpoint)."""


class RateLimitError(GitHubError):
    """Raised when GitHub refuses the request due to rate limiting."""

    def __init__(self, message: str, reset_hint: str = "") -> None:
        super().__init__(message)
        self.reset_hint = reset_hint


class GitHubValidationError(GitHubError):
    """Raised for bad input that we can detect before calling the network."""


def is_valid_username(username: str) -> bool:
    """Return True if the string looks like a syntactically valid GitHub user."""
    return bool(username) and bool(_USERNAME_RE.match(username.strip()))


class GitHubClient:
    """Small client that returns plain dicts/lists of JSON data."""

    def __init__(self, token: Optional[str] = None, timeout: int = 15) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        # Ask for the stable versioned media type so the schema is predictable.
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "github-contribution-planner",
            }
        )
        if token:
            # Bearer auth raises the anonymous limit from 60 to 5000 req/hour.
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------------ #
    # Low-level request helper
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{GITHUB_API_BASE}{path}"
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise GitHubError("The request to GitHub timed out. Please try again.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise GitHubError(
                "Could not reach GitHub. Check your internet connection."
            ) from exc

        # Interpret GitHub's status codes into friendly, catchable exceptions.
        if response.status_code == 404:
            raise UserNotFoundError("GitHub could not find that user or resource.")
        if response.status_code in (403, 429):
            remaining = response.headers.get("X-RateLimit-Remaining", "0")
            if remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset", "")
                raise RateLimitError(
                    "GitHub API rate limit reached. Wait for the limit to reset "
                    "or add a token to raise the limit.",
                    reset_hint=reset,
                )
            raise GitHubError("GitHub refused the request (HTTP 403).")
        if response.status_code == 401:
            raise GitHubError("The provided GitHub token is invalid or expired.")
        if response.status_code >= 400:
            raise GitHubError(
                f"GitHub returned an unexpected error (HTTP {response.status_code})."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError("GitHub returned a response we could not read.") from exc

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_rate_limit(self) -> dict[str, Any]:
        """Expose remaining requests so the UI can warn the user."""
        data = self._get("/rate_limit")
        return data.get("resources", {})

    def get_user(self, username: str) -> dict[str, Any]:
        """Return a normalized public profile for ``username``."""
        username = (username or "").strip()
        if not is_valid_username(username):
            raise GitHubValidationError(
                "That does not look like a valid GitHub username."
            )
        raw = self._get(f"/users/{username}")
        return {
            "login": raw.get("login"),
            "name": raw.get("name"),
            "avatar_url": raw.get("avatar_url"),
            "bio": raw.get("bio"),
            "company": raw.get("company"),
            "location": raw.get("location"),
            "blog": raw.get("blog"),
            "html_url": raw.get("html_url"),
            "public_repos": raw.get("public_repos", 0),
            "public_gists": raw.get("public_gists", 0),
            "followers": raw.get("followers", 0),
            "following": raw.get("following", 0),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
        }

    def get_repos(
        self, username: str, max_repos: int = 100
    ) -> list[dict[str, Any]]:
        """Return public repositories (most recently pushed first).

        GitHub paginates at 100 per page; we request pages until we reach
        ``max_repos`` or run out of results.
        """
        username = (username or "").strip()
        if not is_valid_username(username):
            raise GitHubValidationError(
                "That does not look like a valid GitHub username."
            )

        repos: list[dict[str, Any]] = []
        page = 1
        while len(repos) < max_repos:
            per_page = min(100, max_repos - len(repos))
            batch = self._get(
                f"/users/{username}/repos",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": "pushed",
                    "direction": "desc",
                    "type": "owner",
                },
            )
            if not isinstance(batch, list):
                break
            repos.extend(batch)
            if len(batch) < per_page:
                break  # last page reached
            page += 1

        return [self._normalize_repo(r) for r in repos[:max_repos]]

    @staticmethod
    def _normalize_repo(raw: dict[str, Any]) -> dict[str, Any]:
        """Pick the fields we need and keep their names stable for analysis."""
        return {
            "name": raw.get("name"),
            "full_name": raw.get("full_name"),
            "description": raw.get("description"),
            "html_url": raw.get("html_url"),
            "language": raw.get("language"),
            "stargazers_count": raw.get("stargazers_count", 0),
            "forks_count": raw.get("forks_count", 0),
            "open_issues_count": raw.get("open_issues_count", 0),
            "watchers_count": raw.get("watchers_count", 0),
            "size": raw.get("size", 0),
            "fork": raw.get("fork", False),
            "archived": raw.get("archived", False),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "pushed_at": raw.get("pushed_at"),
            "default_branch": raw.get("default_branch"),
        }

    def get_commits(
        self,
        owner: str,
        repo: str,
        author: Optional[str] = None,
        per_page: int = 100,
        max_pages: int = 1,
    ) -> list[dict[str, Any]]:
        """Return commits for one repository (public data only).

        Limitations we are honest about:
        * Only commits reachable from the repository's default branch are
          returned by this endpoint.
        * If ``author`` is given, GitHub filters by the commit author login,
          but commits made with an unlinked email may not match.
        * Deep history requires pagination, so ``max_pages`` caps how far we
          look to keep the dashboard responsive.
        """
        commits: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"per_page": per_page, "page": page}
            if author:
                params["author"] = author
            batch = self._get(f"/repos/{owner}/{repo}/commits", params=params)
            if not isinstance(batch, list):
                break
            commits.extend(self._normalize_commit(c) for c in batch)
            if len(batch) < per_page:
                break
        return commits

    @staticmethod
    def _normalize_commit(raw: dict[str, Any]) -> dict[str, Any]:
        """Flatten the nested commit JSON into a simple, flat dict."""
        commit = raw.get("commit", {}) or {}
        author_meta = commit.get("author") or {}
        committer_meta = commit.get("committer") or {}
        return {
            "sha": (raw.get("sha") or "")[:10],
            "message": (commit.get("message") or "").split("\n", 1)[0],
            "author_name": author_meta.get("name"),
            "author_login": (raw.get("author") or {}).get("login"),
            "authored_at": author_meta.get("date"),
            "committed_at": committer_meta.get("date"),
            "html_url": raw.get("html_url"),
        }
