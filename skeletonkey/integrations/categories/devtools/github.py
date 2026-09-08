"""GitHub Integration for SkeletonKey (P5.5).

REST client for the GitHub API (https://docs.github.com/rest):

- Repositories, users, releases, tags and branches
- Issues and pull requests (read + write)
- Repository contents (read + write)
- Code and repository search
- Rate-limit introspection

Zero mandatory dependencies (ADR-0001): the transport is stdlib
``urllib.request`` and JSON. ``PyGithub`` (import name ``github``) is treated
as an optional accelerator; when it is installed the integration records it in
health metadata, but the wire path is identical either way so behavior never
depends on the extra package.

Auth: a token may be supplied via config (``token``), ``integrations.secrets``
(``token``/``github_token``), or the ``GITHUB_TOKEN``/``GH_TOKEN`` environment
variables. Public read endpoints work without a token (GitHub's anonymous rate
limit applies); writes and code search require one.
"""

from __future__ import annotations

import base64
import importlib.util
import logging
import os
import urllib.parse
from typing import Any

from skeletonkey.core.errors import E, SkeletonKeyError
from skeletonkey.integrations import (
    IntegrationCapability,
    IntegrationCategory,
    IntegrationConfig,
    IntegrationDependency,
    IntegrationPlatform,
    IntegrationType,
    RestIntegration,
    register_integration,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "skeletonkey-toolforge"


@register_integration(
    name="github",
    title="GitHub",
    version="1.0.0",
    integration_type=IntegrationType.SDK,
    category=IntegrationCategory.DEVTOOLS,
    platform=IntegrationPlatform.CROSS_PLATFORM,
    description="GitHub REST API client (repos, issues, pull requests, search)",
    author="SkeletonKey Team",
    license="Apache-2.0",
    keywords=["github", "git", "repositories", "issues", "pull-requests", "search"],
    dependencies=[
        IntegrationDependency(
            name="github",
            version=">=2.3.0",
            optional=True,
            description="PyGithub - optional accelerator, not required",
        ),
    ],
    capabilities=[
        IntegrationCapability(
            name="network",
            description="Requires network access to api.github.com",
            required=True,
            default_granted=False,
        ),
    ],
)
class GitHubIntegration(RestIntegration):
    """GitHub REST API integration.

    Tools are grouped by resource (``github.repo.*``, ``github.issues.*``,
    ``github.pulls.*``, ``github.search.*``, ...). Every response carries the
    upstream payload plus, when present, ``_rate`` (GitHub rate-limit window).
    """

    def __init__(self, config: IntegrationConfig):
        """Initialize the GitHub integration."""
        super().__init__(config)
        self._base_url: str = str(
            config.config.get("base_url") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._timeout: float = float(config.config.get("timeout", 30))
        self._token: str | None = None
        self._has_sdk = False

    # ------------------------------------------------------------------ lifecycle
    def initialize(self) -> bool:
        """Resolve the token and note whether the optional SDK is installed."""
        self._token = self._resolve_token()
        self._has_sdk = importlib.util.find_spec("github") is not None
        self._initialized = True
        logger.info(
            "GitHub integration initialized (base=%s, token=%s, sdk=%s)",
            self._base_url,
            "set" if self._token else "unset",
            self._has_sdk,
        )
        return True

    def cleanup(self) -> None:
        """Release the in-memory token."""
        self._token = None
        self._initialized = False

    def check_health(self) -> tuple[str, str]:
        """Probe the rate-limit endpoint (no token needed)."""
        if not self._initialized:
            return ("unhealthy", "Integration not initialized")
        try:
            resp = self._request("GET", "/rate_limit")
            if resp["status"] < 400:
                return ("healthy", "GitHub API is reachable")
            return ("degraded", f"GitHub API returned HTTP {resp['status']}")
        except SkeletonKeyError as e:
            return ("degraded", str(e))

    # ------------------------------------------------------------------ config
    def _resolve_token(self) -> str | None:
        """Token precedence: secrets -> config -> environment."""
        for key in ("token", "github_token", "access_token"):
            value = self.config.secrets.get(key) or self.config.config.get(key)
            if value:
                return str(value)
        return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    def _require_token(self) -> str:
        """Return the token or raise a usage error naming the fix."""
        if not self._token:
            raise SkeletonKeyError(
                E.BAD_ARGS,
                "GitHub token required for this operation",
                details={"field": "token", "sources": ["GITHUB_TOKEN", "GH_TOKEN", "config.token"]},
                hint="Set GITHUB_TOKEN or pass config={'token': ...} on integrations.load.",
            )
        return self._token

    # ------------------------------------------------------------------ transport
    def _auth_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, path: str, *, query=None, body=None, params=None):
        response = super()._request(method, path, query=query, body=body, params=params)
        response["rate"] = self._rate_info(response["headers"])
        response["next"] = self._next_url(response["headers"])
        return response

    @staticmethod
    def _rate_info(headers: dict[str, str]) -> dict[str, int] | None:
        """Extract the GitHub rate-limit window from response headers."""
        limit = headers.get("x-ratelimit-limit")
        remaining = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset")
        if limit is None and remaining is None and reset is None:
            return None
        info: dict[str, int] = {}
        if limit is not None:
            info["limit"] = int(limit)
        if remaining is not None:
            info["remaining"] = int(remaining)
        if reset is not None:
            info["reset_epoch"] = int(reset)
        return info or None

    @staticmethod
    def _next_url(headers: dict[str, str]) -> str | None:
        """Return the ``rel=next`` pagination URL from a Link header, if any."""
        link = headers.get("link")
        if not link:
            return None
        for part in link.split(","):
            if 'rel="next"' not in part:
                continue
            start = part.find("<")
            end = part.find(">")
            if start != -1 and end != -1:
                return part[start + 1 : end]
        return None

    def _paged(self, resp: dict[str, Any], page: int, per_page: int) -> dict[str, Any]:
        """Wrap a list payload with pagination metadata."""
        items = resp["data"]
        result: dict[str, Any] = {
            "items": items if isinstance(items, list) else [],
            "count": len(items) if isinstance(items, list) else 0,
            "page": page,
            "per_page": per_page,
            "has_more": resp["next"] is not None,
        }
        if resp["rate"]:
            result["_rate"] = resp["rate"]
        return result

    def _envelope(self, resp: dict[str, Any]) -> dict[str, Any]:
        """Attach rate metadata to a passthrough payload."""
        data = resp["data"]
        if isinstance(data, dict) and resp["rate"]:
            data = dict(data)
            data["_rate"] = resp["rate"]
        return data

    def get_tools(self) -> list[dict[str, Any]]:
        """Get the list of tools provided by this integration."""
        return [
            {
                "id": "github.rate_limit",
                "title": "Rate Limit",
                "description": "Get the current GitHub API rate-limit window",
                "capability": "github.rate_limit",
                "risk": "read",
                "typical_latency_ms": 200,
                "tier": "task",
                "tags": ["github", "rate-limit", "auth"],
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "id": "github.user.get",
                "title": "Get User",
                "description": "Get a GitHub user profile by username",
                "capability": "github.user.get",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["github", "user", "profile"],
                "input_schema": {
                    "type": "object",
                    "properties": {"username": {"type": "string", "minLength": 1}},
                    "required": ["username"],
                },
            },
            {
                "id": "github.repo.get",
                "title": "Get Repository",
                "description": "Get a repository by owner and name",
                "capability": "github.repo.get",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["github", "repo", "repository"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.repo.list",
                "title": "List Repositories",
                "description": "List repositories for a user or organization",
                "capability": "github.repo.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "repo", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                        "sort": {"type": "string", "enum": ["created", "updated", "pushed", "full_name"]},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["owner"],
                },
            },
            {
                "id": "github.repo.contents",
                "title": "Get Repository Contents",
                "description": "Get the contents of a file or directory in a repository",
                "capability": "github.repo.contents",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "repo", "contents", "file"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "default": ""},
                        "ref": {"type": "string"},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.repo.tags",
                "title": "List Tags",
                "description": "List the tags of a repository",
                "capability": "github.repo.tags",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "repo", "tags", "releases"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.repo.branches",
                "title": "List Branches",
                "description": "List the branches of a repository",
                "capability": "github.repo.branches",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "repo", "branches", "git"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.releases.list",
                "title": "List Releases",
                "description": "List the releases of a repository",
                "capability": "github.releases.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "repo", "releases"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.issues.list",
                "title": "List Issues",
                "description": "List the issues of a repository",
                "capability": "github.issues.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "issues", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.issues.get",
                "title": "Get Issue",
                "description": "Get a single issue by number",
                "capability": "github.issues.get",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["github", "issues", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "issue_number": {"type": "integer", "minimum": 1},
                    },
                    "required": ["owner", "repo", "issue_number"],
                },
            },
            {
                "id": "github.issues.create",
                "title": "Create Issue",
                "description": "Open a new issue in a repository",
                "capability": "github.issues.create",
                "risk": "write",
                "typical_latency_ms": 500,
                "tier": "task",
                "tags": ["github", "issues", "create", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "body": {"type": "string"},
                        "labels": {"type": "array", "items": {"type": "string"}},
                        "assignees": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["owner", "repo", "title"],
                },
            },
            {
                "id": "github.issues.comment",
                "title": "Comment on Issue",
                "description": "Add a comment to an issue or pull request",
                "capability": "github.issues.comment",
                "risk": "write",
                "typical_latency_ms": 500,
                "tier": "task",
                "tags": ["github", "issues", "comment", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "issue_number": {"type": "integer", "minimum": 1},
                        "body": {"type": "string", "minLength": 1},
                    },
                    "required": ["owner", "repo", "issue_number", "body"],
                },
            },
            {
                "id": "github.pulls.list",
                "title": "List Pull Requests",
                "description": "List the pull requests of a repository",
                "capability": "github.pulls.list",
                "risk": "read",
                "typical_latency_ms": 400,
                "tier": "task",
                "tags": ["github", "pull-requests", "list"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["owner", "repo"],
                },
            },
            {
                "id": "github.pulls.get",
                "title": "Get Pull Request",
                "description": "Get a single pull request by number",
                "capability": "github.pulls.get",
                "risk": "read",
                "typical_latency_ms": 300,
                "tier": "task",
                "tags": ["github", "pull-requests", "get"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "pull_number": {"type": "integer", "minimum": 1},
                    },
                    "required": ["owner", "repo", "pull_number"],
                },
            },
            {
                "id": "github.pulls.create",
                "title": "Create Pull Request",
                "description": "Open a new pull request",
                "capability": "github.pulls.create",
                "risk": "write",
                "typical_latency_ms": 600,
                "tier": "task",
                "tags": ["github", "pull-requests", "create", "write"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "title": {"type": "string", "minLength": 1},
                        "head": {"type": "string", "minLength": 1},
                        "base": {"type": "string", "minLength": 1},
                        "body": {"type": "string"},
                        "draft": {"type": "boolean", "default": False},
                    },
                    "required": ["owner", "repo", "title", "head", "base"],
                },
            },
            {
                "id": "github.search.repos",
                "title": "Search Repositories",
                "description": "Search GitHub repositories by query",
                "capability": "github.search.repos",
                "risk": "read",
                "typical_latency_ms": 600,
                "tier": "task",
                "tags": ["github", "search", "repositories"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "minLength": 1},
                        "sort": {"type": "string", "enum": ["stars", "forks", "updated"]},
                        "order": {"type": "string", "enum": ["asc", "desc"]},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["q"],
                },
            },
            {
                "id": "github.search.code",
                "title": "Search Code",
                "description": "Search code across GitHub (requires a token)",
                "capability": "github.search.code",
                "risk": "read",
                "typical_latency_ms": 800,
                "tier": "task",
                "tags": ["github", "search", "code"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "string", "minLength": 1},
                        "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                        "page": {"type": "integer", "minimum": 1, "default": 1},
                    },
                    "required": ["q"],
                },
            },
            {
                "id": "github.contents.update",
                "title": "Create or Update File",
                "description": "Create or update a file in a repository (requires a token)",
                "capability": "github.contents.update",
                "risk": "write",
                "typical_latency_ms": 600,
                "tier": "task",
                "tags": ["github", "contents", "write", "file"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "owner": {"type": "string", "minLength": 1},
                        "repo": {"type": "string", "minLength": 1},
                        "path": {"type": "string", "minLength": 1},
                        "message": {"type": "string", "minLength": 1},
                        "content": {"type": "string"},
                        "sha": {"type": "string"},
                        "branch": {"type": "string"},
                    },
                    "required": ["owner", "repo", "path", "message", "content"],
                },
            },
        ]

    # ------------------------------------------------------------------ tool impls
    def tool_rate_limit(self) -> dict[str, Any]:
        """Get the current rate-limit window."""
        return self._envelope(self._api("GET", "/rate_limit"))

    def tool_user_get(self, username: str) -> dict[str, Any]:
        """Get a GitHub user profile."""
        return self._envelope(self._api("GET", f"/users/{_seg(username)}"))

    def tool_repo_get(self, owner: str, repo: str) -> dict[str, Any]:
        """Get a repository."""
        return self._envelope(self._api("GET", f"/repos/{_seg(owner)}/{_seg(repo)}"))

    def tool_repo_list(
        self,
        owner: str,
        per_page: int = 30,
        page: int = 1,
        sort: str | None = None,
        direction: str | None = None,
    ) -> dict[str, Any]:
        """List repositories for a user or organization."""
        resp = self._api(
            "GET",
            f"/users/{_seg(owner)}/repos",
            query={"per_page": per_page, "page": page, "sort": sort, "direction": direction},
        )
        return self._paged(resp, page, per_page)

    def tool_repo_contents(
        self,
        owner: str,
        repo: str,
        path: str = "",
        ref: str | None = None,
    ) -> dict[str, Any]:
        """Get file or directory contents."""
        suffix = f"/{_seg(path)}" if path else ""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/contents{suffix}",
            query={"ref": ref},
        )
        return self._envelope(resp)

    def tool_repo_tags(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """List repository tags."""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/tags",
            query={"per_page": per_page, "page": page},
        )
        return self._paged(resp, page, per_page)

    def tool_repo_branches(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """List repository branches."""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/branches",
            query={"per_page": per_page, "page": page},
        )
        return self._paged(resp, page, per_page)

    def tool_releases_list(
        self,
        owner: str,
        repo: str,
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """List repository releases."""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/releases",
            query={"per_page": per_page, "page": page},
        )
        return self._paged(resp, page, per_page)

    def tool_issues_list(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """List repository issues."""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/issues",
            query={"state": state, "per_page": per_page, "page": page},
        )
        return self._paged(resp, page, per_page)

    def tool_issues_get(self, owner: str, repo: str, issue_number: int) -> dict[str, Any]:
        """Get a single issue."""
        return self._envelope(
            self._api("GET", f"/repos/{_seg(owner)}/{_seg(repo)}/issues/{issue_number}")
        )

    def tool_issues_create(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Open a new issue."""
        self._require_token()
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        return self._envelope(
            self._api("POST", f"/repos/{_seg(owner)}/{_seg(repo)}/issues", body=payload)
        )

    def tool_issues_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> dict[str, Any]:
        """Comment on an issue or pull request."""
        self._require_token()
        return self._envelope(
            self._api(
                "POST",
                f"/repos/{_seg(owner)}/{_seg(repo)}/issues/{issue_number}/comments",
                body={"body": body},
            )
        )

    def tool_pulls_list(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """List pull requests."""
        resp = self._api(
            "GET",
            f"/repos/{_seg(owner)}/{_seg(repo)}/pulls",
            query={"state": state, "per_page": per_page, "page": page},
        )
        return self._paged(resp, page, per_page)

    def tool_pulls_get(self, owner: str, repo: str, pull_number: int) -> dict[str, Any]:
        """Get a single pull request."""
        return self._envelope(
            self._api("GET", f"/repos/{_seg(owner)}/{_seg(repo)}/pulls/{pull_number}")
        )

    def tool_pulls_create(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str | None = None,
        draft: bool = False,
    ) -> dict[str, Any]:
        """Open a new pull request."""
        self._require_token()
        payload: dict[str, Any] = {"title": title, "head": head, "base": base}
        if body:
            payload["body"] = body
        if draft:
            payload["draft"] = True
        return self._envelope(
            self._api("POST", f"/repos/{_seg(owner)}/{_seg(repo)}/pulls", body=payload)
        )

    def tool_search_repos(
        self,
        q: str,
        sort: str | None = None,
        order: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search repositories."""
        resp = self._api(
            "GET",
            "/search/repositories",
            query={"q": q, "sort": sort, "order": order, "per_page": per_page, "page": page},
        )
        result = self._paged(resp, page, per_page)
        data = resp["data"]
        if isinstance(data, dict) and "total_count" in data:
            result["total_count"] = data["total_count"]
        return result

    def tool_search_code(
        self,
        q: str,
        per_page: int = 30,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search code (requires a token)."""
        self._require_token()
        resp = self._api(
            "GET",
            "/search/code",
            query={"q": q, "per_page": per_page, "page": page},
        )
        result = self._paged(resp, page, per_page)
        data = resp["data"]
        if isinstance(data, dict) and "total_count" in data:
            result["total_count"] = data["total_count"]
        return result

    def tool_contents_update(
        self,
        owner: str,
        repo: str,
        path: str,
        message: str,
        content: str,
        sha: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a file (content is base64-encoded for the API)."""
        self._require_token()
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        if branch:
            payload["branch"] = branch
        return self._envelope(
            self._api(
                "PUT",
                f"/repos/{_seg(owner)}/{_seg(repo)}/contents/{_seg(path)}",
                body=payload,
            )
        )


def _seg(value: str) -> str:
    """URL-encode a single path segment."""
    return urllib.parse.quote(str(value), safe="")
