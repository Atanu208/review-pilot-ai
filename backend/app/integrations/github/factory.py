"""Construction of GitHub clients from settings (overridable in tests)."""

from __future__ import annotations

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError
from app.core.redis import get_redis
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.app_auth import InstallationTokenProvider
from app.integrations.github.http import GitHubHTTP
from app.integrations.github.oauth import GitHubOAuthClient

_client: httpx.AsyncClient | None = None


def get_httpx_client() -> httpx.AsyncClient:
    """Process-wide pooled client. ``follow_redirects`` is off to avoid redirect-based SSRF."""
    global _client
    if _client is None or _client.is_closed:
        settings = get_settings()
        _client = httpx.AsyncClient(
            timeout=settings.github_http_timeout_seconds, follow_redirects=False
        )
    return _client


async def close_httpx_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


def build_oauth_client(
    settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> GitHubOAuthClient:
    settings = settings or get_settings()
    if not settings.github_oauth_configured or settings.github_client_secret is None:
        raise ServiceUnavailableError(
            "GitHub sign-in is not configured", code="github_not_configured"
        )
    client = client or get_httpx_client()
    return GitHubOAuthClient(
        web=GitHubHTTP(client, settings.github_web_url),
        api=GitHubHTTP(client, settings.github_api_url),
        client_id=str(settings.github_client_id),
        client_secret=settings.github_client_secret.get_secret_value(),
        redirect_uri=settings.oauth_callback_url,
    )


def build_app_api(
    settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> GitHubAppAPI:
    settings = settings or get_settings()
    if not settings.github_app_configured:
        raise ServiceUnavailableError("GitHub App is not configured", code="github_not_configured")
    http = GitHubHTTP(client or get_httpx_client(), settings.github_api_url)
    tokens = InstallationTokenProvider(
        http, get_redis(), str(settings.github_app_id), settings.load_github_private_key()
    )
    return GitHubAppAPI(http, tokens)
