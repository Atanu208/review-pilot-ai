"""GitHub App authentication: App JWT and cached installation access tokens."""

from __future__ import annotations

import time
from datetime import datetime

import jwt
from redis.asyncio import Redis

from app.core.logging import get_logger
from app.integrations.github.http import GitHubHTTP
from app.security.encryption import decrypt, encrypt

logger = get_logger(__name__)

# Redis key prefix, not a credential.
TOKEN_CACHE_PREFIX = "gh:inst_token:"  # noqa: S105  # nosec B105
TOKEN_REFRESH_MARGIN_SECONDS = 300


def create_app_jwt(app_id: str, private_key_pem: str, *, now: float | None = None) -> str:
    """RS256 JWT valid for 9 minutes (GitHub max is 10), backdated 60s for clock skew."""
    issued = int(now if now is not None else time.time()) - 60
    payload = {"iat": issued, "exp": issued + 60 + 9 * 60, "iss": app_id}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


class InstallationTokenProvider:
    """Mints installation tokens and caches them (encrypted) in Redis until near expiry."""

    def __init__(self, http: GitHubHTTP, redis: Redis, app_id: str, private_key_pem: str) -> None:
        self._http = http
        self._redis = redis
        self._app_id = app_id
        self._private_key = private_key_pem

    def app_jwt(self) -> str:
        return create_app_jwt(self._app_id, self._private_key)

    async def get_token(self, installation_id: int) -> str:
        key = f"{TOKEN_CACHE_PREFIX}{installation_id}"
        cached = await self._redis.get(key)
        if cached:
            return decrypt(cached if isinstance(cached, str) else cached.decode())

        response = await self._http.request(
            "POST", f"/app/installations/{installation_id}/access_tokens", token=self.app_jwt()
        )
        body = response.json()
        token: str = body["token"]
        expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        ttl = int(expires_at.timestamp() - time.time()) - TOKEN_REFRESH_MARGIN_SECONDS
        if ttl > 0:
            await self._redis.set(key, encrypt(token), ex=ttl)
        return token

    async def invalidate(self, installation_id: int) -> None:
        await self._redis.delete(f"{TOKEN_CACHE_PREFIX}{installation_id}")
