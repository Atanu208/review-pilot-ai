"""Low-level GitHub REST client: auth headers, retries, rate limits, pagination, SSRF guard."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = "ReviewPilot-AI"
_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')

Sleep = Callable[[float], Awaitable[None]]


class GitHubError(Exception):
    """Base error for GitHub API failures."""


class GitHubAPIError(GitHubError):
    def __init__(self, status_code: int, message: str, path: str) -> None:
        super().__init__(f"GitHub API {status_code} on {path}: {message}")
        self.status_code = status_code
        self.message = message
        self.path = path

    @property
    def is_transient(self) -> bool:
        return self.status_code >= 500 or self.status_code in (408, 429)


class GitHubRateLimitError(GitHubError):
    def __init__(self, reset_in_seconds: float) -> None:
        super().__init__(f"GitHub rate limit exceeded; resets in {reset_in_seconds:.0f}s")
        self.reset_in_seconds = reset_in_seconds


class GitHubHTTP:
    """Thin async wrapper over ``httpx.AsyncClient`` for one GitHub base URL.

    * Retries connection errors and 5xx with exponential backoff.
    * Honours primary/secondary rate limits (``Retry-After`` / ``X-RateLimit-Reset``),
      sleeping up to ``max_rate_limit_wait`` seconds before giving up.
    * Refuses absolute URLs outside ``base_url`` (pagination links are attacker-adjacent).
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        max_rate_limit_wait: float = 60.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_rate_limit_wait = max_rate_limit_wait
        self._sleep = sleep

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            if not path_or_url.startswith(self.base_url + "/"):
                raise GitHubError(f"Refusing request outside {self.base_url}")
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    @staticmethod
    def _rate_limit_wait(response: httpx.Response) -> float | None:
        if response.status_code not in (403, 429):
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = response.headers.get("x-ratelimit-reset")
            if reset and reset.isdigit():
                return max(0.0, float(reset) - time.time()) + 1
            return 60.0
        if response.status_code == 429:
            return 60.0
        return None

    async def request(
        self,
        method: str,
        path_or_url: str,
        *,
        token: str | None = None,
        auth_scheme: str = "Bearer",
        json: Any = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> httpx.Response:
        url = self._url(path_or_url)
        headers = {
            "Accept": accept,
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            headers["Authorization"] = f"{auth_scheme} {token}"

        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.request(
                    method, url, headers=headers, json=json, params=params, data=data
                )
            except httpx.TransportError as exc:
                if attempt > self._max_retries:
                    logger.warning("github_request_failed", url=url, error_type=type(exc).__name__)
                    raise GitHubError(f"GitHub unreachable: {type(exc).__name__}") from exc
                await self._sleep(self._backoff_base * 2 ** (attempt - 1))
                continue

            wait = self._rate_limit_wait(response)
            if wait is not None:
                if wait > self._max_rate_limit_wait or attempt > self._max_retries:
                    logger.warning("github_rate_limited", url=url, reset_in=wait)
                    raise GitHubRateLimitError(wait)
                await self._sleep(wait)
                continue

            if response.status_code >= 500 and attempt <= self._max_retries:
                await self._sleep(self._backoff_base * 2 ** (attempt - 1))
                continue

            if response.status_code >= 400:
                message = _error_message(response)
                logger.warning(
                    "github_api_error", status=response.status_code, url=url, message=message
                )
                raise GitHubAPIError(response.status_code, message, url)
            return response

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        return (await self.request("GET", path, **kwargs)).json()

    async def paginate(
        self,
        path: str,
        *,
        token: str | None = None,
        auth_scheme: str = "Bearer",
        params: dict[str, Any] | None = None,
        item_key: str | None = None,
        max_pages: int = 50,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield items across pages, following ``Link: rel="next"``."""
        url: str | None = path
        query: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        for _ in range(max_pages):
            if url is None:
                return
            response = await self.request(
                "GET", url, token=token, auth_scheme=auth_scheme, params=query
            )
            body = response.json()
            items = body[item_key] if item_key else body
            for item in items:
                yield item
            match = _LINK_NEXT.search(response.headers.get("link", ""))
            url = match.group(1) if match else None
            query = None  # the "next" URL already carries the query string
        logger.warning("github_pagination_truncated", path=path, max_pages=max_pages)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or "error"
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error_description") or body.get("error"))
    return "error"
