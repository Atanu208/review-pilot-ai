"""Redis fixed-window rate limiting.

Identities:
* ``ip``      - the client address (anonymous endpoints: login, webhooks).
* ``session`` - a hash of the session cookie, falling back to the IP when absent, so users
  behind one NAT do not share a budget and a stolen cookie cannot exhaust someone else's.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

Identity = Literal["ip", "session"]
Limit = int | Callable[[Settings], int]
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, bucket: str, identity: str, limit: int, window_seconds: int) -> int:
        """Record a hit; return seconds until reset if over the limit, else 0."""
        window = int(time.time()) // window_seconds
        key = f"rl:{bucket}:{identity}:{window}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds + 1)
        count, _ = await pipe.execute()
        if int(count) > limit:
            return window_seconds - int(time.time()) % window_seconds
        return 0


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers / --forwarded-allow-ips so
    # request.client is the real client (never trust X-Forwarded-For directly here).
    return request.client.host if request.client else "unknown"


def request_identity(request: Request, settings: Settings, identity: Identity) -> str:
    if identity == "session":
        token = request.cookies.get(settings.session_cookie_name)
        if token:
            return "s:" + hashlib.sha256(token.encode()).hexdigest()[:32]
    return "ip:" + client_ip(request)


async def enforce(
    request: Request, bucket: str, limit: Limit, window_seconds: int, identity: Identity
) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    max_hits = limit(settings) if callable(limit) else limit
    if max_hits <= 0:  # 0 disables this particular limit
        return
    try:
        retry_after = await RateLimiter(get_redis()).hit(
            bucket, request_identity(request, settings, identity), max_hits, window_seconds
        )
    except Exception as exc:  # fail open: a Redis outage must not take the API down
        logger.warning("rate_limiter_unavailable", error_type=type(exc).__name__)
        return
    if retry_after:
        logger.warning("rate_limited", bucket=bucket, identity_kind=identity)
        raise RateLimitedError(
            "Too many requests, please retry later",
            details={"retry_after_seconds": retry_after},
        )


def rate_limit(
    bucket: str, limit: Limit, window_seconds: int = 60, identity: Identity = "ip"
) -> Callable[[Request], Awaitable[None]]:
    """FastAPI dependency factory limiting requests per identity."""

    async def dependency(request: Request) -> None:
        await enforce(request, bucket, limit, window_seconds, identity)

    return dependency


async def write_rate_limit(request: Request) -> None:
    """Router-wide cap on state-changing requests per session (reads are not counted)."""
    if request.method in SAFE_METHODS:
        return
    await enforce(request, "api_write", lambda s: s.rate_limit_writes_per_minute, 60, "session")
