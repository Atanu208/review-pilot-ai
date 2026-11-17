"""GitHub App user authorization (OAuth web flow) and user-to-server API calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.integrations.github.http import GitHubError, GitHubHTTP


class GitHubOAuthError(GitHubError):
    """OAuth exchange rejected by GitHub (bad/expired code, revoked refresh token, ...)."""


@dataclass(slots=True, frozen=True)
class OAuthTokens:
    access_token: str
    expires_at: datetime | None
    refresh_token: str | None
    refresh_token_expires_at: datetime | None


@dataclass(slots=True, frozen=True)
class GitHubUser:
    id: int
    login: str
    name: str | None
    email: str | None
    avatar_url: str | None


def _expiry(seconds: Any) -> datetime | None:
    if seconds in (None, ""):
        return None
    return datetime.now(UTC) + timedelta(seconds=int(seconds))


class GitHubOAuthClient:
    def __init__(
        self,
        web: GitHubHTTP,
        api: GitHubHTTP,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> None:
        self._web = web
        self._api = api
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {"client_id": self._client_id, "redirect_uri": self._redirect_uri, "state": state}
        )
        return f"{self._web.base_url}/login/oauth/authorize?{query}"

    async def _token_request(self, data: dict[str, str]) -> OAuthTokens:
        response = await self._web.request(
            "POST",
            "/login/oauth/access_token",
            data={"client_id": self._client_id, "client_secret": self._client_secret, **data},
            accept="application/json",
        )
        body = response.json()
        # GitHub reports OAuth errors with HTTP 200 and an "error" field.
        if "error" in body or "access_token" not in body:
            raise GitHubOAuthError(str(body.get("error_description") or body.get("error")))
        return OAuthTokens(
            access_token=body["access_token"],
            expires_at=_expiry(body.get("expires_in")),
            refresh_token=body.get("refresh_token"),
            refresh_token_expires_at=_expiry(body.get("refresh_token_expires_in")),
        )

    async def exchange_code(self, code: str) -> OAuthTokens:
        return await self._token_request({"code": code, "redirect_uri": self._redirect_uri})

    async def refresh(self, refresh_token: str) -> OAuthTokens:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def get_user(self, access_token: str) -> GitHubUser:
        body = await self._api.get_json("/user", token=access_token)
        return GitHubUser(
            id=int(body["id"]),
            login=str(body["login"]),
            name=body.get("name"),
            email=body.get("email"),
            avatar_url=body.get("avatar_url"),
        )

    async def user_installation_ids(self, access_token: str) -> set[int]:
        """Installations of *this* GitHub App that the user can access."""
        ids: set[int] = set()
        async for item in self._api.paginate(
            "/user/installations", token=access_token, item_key="installations"
        ):
            ids.add(int(item["id"]))
        return ids
