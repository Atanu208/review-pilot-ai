"""GitHub App (server-to-server) API operations used by ReviewPilot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.integrations.github.app_auth import InstallationTokenProvider
from app.integrations.github.http import GitHubAPIError, GitHubHTTP


@dataclass(slots=True, frozen=True)
class InstallationInfo:
    id: int
    account_id: int
    account_login: str
    account_type: str
    repository_selection: str | None
    permissions: dict[str, Any]
    events: list[str]
    suspended: bool


@dataclass(slots=True, frozen=True)
class RepoInfo:
    id: int
    full_name: str
    private: bool
    default_branch: str
    language: str | None
    html_url: str | None


def installation_from_payload(body: dict[str, Any]) -> InstallationInfo:
    account = body.get("account") or {}
    return InstallationInfo(
        id=int(body["id"]),
        account_id=int(account.get("id", 0)),
        account_login=str(account.get("login", "")),
        account_type=str(account.get("type", "User")),
        repository_selection=body.get("repository_selection"),
        permissions=dict(body.get("permissions") or {}),
        events=list(body.get("events") or []),
        suspended=body.get("suspended_at") is not None,
    )


def repo_from_payload(body: dict[str, Any]) -> RepoInfo:
    return RepoInfo(
        id=int(body["id"]),
        full_name=str(body["full_name"]),
        private=bool(body.get("private", True)),
        default_branch=str(body.get("default_branch") or "main"),
        language=body.get("language"),
        html_url=body.get("html_url"),
    )


class GitHubAppAPI:
    def __init__(self, http: GitHubHTTP, tokens: InstallationTokenProvider) -> None:
        self.http = http
        self.tokens = tokens

    async def get_installation(self, installation_id: int) -> InstallationInfo:
        body = await self.http.get_json(
            f"/app/installations/{installation_id}", token=self.tokens.app_jwt()
        )
        return installation_from_payload(body)

    async def list_installation_repositories(self, installation_id: int) -> list[RepoInfo]:
        token = await self.tokens.get_token(installation_id)
        return [
            repo_from_payload(item)
            async for item in self.http.paginate(
                "/installation/repositories", token=token, item_key="repositories"
            )
        ]

    async def get_repository(self, installation_id: int, github_repo_id: int) -> RepoInfo:
        """Fails with 404 unless the installation can access the repository."""
        token = await self.tokens.get_token(installation_id)
        return repo_from_payload(
            await self.http.get_json(f"/repositories/{github_repo_id}", token=token)
        )

    async def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> dict[str, Any]:
        token = await self.tokens.get_token(installation_id)
        body: dict[str, Any] = await self.http.get_json(
            f"/repos/{_repo_path(full_name)}/pulls/{int(number)}", token=token
        )
        return body

    async def create_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_id: str,
        body: str,
        comments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Submit a COMMENT review (never APPROVE/REQUEST_CHANGES) with inline comments."""
        token = await self.tokens.get_token(installation_id)
        response = await self.http.request(
            "POST",
            f"/repos/{_repo_path(full_name)}/pulls/{int(number)}/reviews",
            token=token,
            json={"commit_id": commit_id, "body": body, "event": "COMMENT", "comments": comments},
        )
        result: dict[str, Any] = response.json()
        return result

    async def update_review_body(
        self, installation_id: int, full_name: str, number: int, review_id: int, body: str
    ) -> None:
        token = await self.tokens.get_token(installation_id)
        await self.http.request(
            "PUT",
            f"/repos/{_repo_path(full_name)}/pulls/{int(number)}/reviews/{int(review_id)}",
            token=token,
            json={"body": body},
        )

    async def list_review_comments(
        self, installation_id: int, full_name: str, number: int, review_id: int
    ) -> list[dict[str, Any]]:
        token = await self.tokens.get_token(installation_id)
        return [
            item
            async for item in self.http.paginate(
                f"/repos/{_repo_path(full_name)}/pulls/{int(number)}/reviews/{int(review_id)}/comments",
                token=token,
            )
        ]

    async def list_pull_request_files(
        self, installation_id: int, full_name: str, number: int, *, max_files: int = 3000
    ) -> list[dict[str, Any]]:
        """Changed files with unified-diff ``patch`` (absent for binary/very large files).

        GitHub caps this endpoint at 3000 files (30 pages of 100).
        """
        token = await self.tokens.get_token(installation_id)
        files: list[dict[str, Any]] = []
        async for item in self.http.paginate(
            f"/repos/{_repo_path(full_name)}/pulls/{int(number)}/files",
            token=token,
            max_pages=30,
        ):
            files.append(item)
            if len(files) >= max_files:
                break
        return files

    async def get_file_content(
        self, installation_id: int, full_name: str, path: str, ref: str, *, max_bytes: int
    ) -> bytes | None:
        """Raw file bytes at ``ref``; None if missing or larger than ``max_bytes``."""
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            raise ValueError("ref must be a full commit SHA")
        if path.startswith("/") or any(part in ("", "..") for part in path.split("/")):
            raise ValueError(f"Invalid file path: {path!r}")
        token = await self.tokens.get_token(installation_id)
        try:
            response = await self.http.request(
                "GET",
                f"/repos/{_repo_path(full_name)}/contents/{quote(path, safe='/')}",
                token=token,
                params={"ref": ref},
                accept="application/vnd.github.raw+json",
            )
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return None
            raise
        content = response.content
        return None if len(content) > max_bytes else content


_REPO_FULL_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}$")


def _repo_path(full_name: str) -> str:
    """Validate ``owner/name`` before interpolating it into a URL path."""
    if not _REPO_FULL_NAME.match(full_name) or ".." in full_name:
        raise ValueError(f"Invalid repository name: {full_name!r}")
    return full_name
