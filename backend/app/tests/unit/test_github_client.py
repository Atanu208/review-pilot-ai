from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.integrations.github.app_auth import create_app_jwt
from app.integrations.github.http import (
    GitHubAPIError,
    GitHubError,
    GitHubHTTP,
    GitHubRateLimitError,
)
from app.integrations.github.oauth import GitHubOAuthClient, GitHubOAuthError

API = "https://api.github.com"
Handler = Callable[[httpx.Request], httpx.Response]


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_http(handler: Handler, sleeps: Sleeps | None = None, **kw: float) -> GitHubHTTP:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubHTTP(client, API, sleep=sleeps or Sleeps(), **kw)  # type: ignore[arg-type]


async def test_paginate_follows_link_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(
                200,
                json={"repositories": [{"id": 1}, {"id": 2}]},
                headers={"link": f'<{API}/installation/repositories?page=2>; rel="next"'},
            )
        return httpx.Response(200, json={"repositories": [{"id": 3}]})

    http = make_http(handler)
    items = [i async for i in http.paginate("/installation/repositories", item_key="repositories")]
    assert [i["id"] for i in items] == [1, 2, 3]


async def test_paginate_refuses_link_to_foreign_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"id": 1}], headers={"link": '<https://evil.example/steal>; rel="next"'}
        )

    http = make_http(handler)
    with pytest.raises(GitHubError, match="Refusing request outside"):
        _ = [i async for i in http.paginate("/user/repos")]


async def test_absolute_url_with_same_prefix_but_other_host_rejected() -> None:
    http = make_http(lambda r: httpx.Response(200, json={}))
    with pytest.raises(GitHubError):
        await http.request("GET", "https://api.github.com.evil.example/x")


async def test_retries_server_errors_with_backoff() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502) if calls["n"] < 3 else httpx.Response(200, json={"ok": True})

    sleeps = Sleeps()
    http = make_http(handler, sleeps)
    assert (await http.get_json("/meta")) == {"ok": True}
    assert sleeps.calls == [0.5, 1.0]


async def test_retries_transport_errors_then_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    sleeps = Sleeps()
    http = make_http(handler, sleeps)
    with pytest.raises(GitHubError, match="unreachable"):
        await http.get_json("/meta")
    assert len(sleeps.calls) == 3


async def test_waits_for_retry_after_on_secondary_rate_limit() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, headers={"retry-after": "7"}, json={"message": "slow down"})
        return httpx.Response(200, json={"ok": True})

    sleeps = Sleeps()
    http = make_http(handler, sleeps)
    assert (await http.get_json("/x")) == {"ok": True}
    assert sleeps.calls == [7.0]


async def test_primary_rate_limit_beyond_max_wait_raises() -> None:
    reset = str(int(time.time()) + 3600)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, json={}
        )

    http = make_http(handler)
    with pytest.raises(GitHubRateLimitError) as info:
        await http.get_json("/x")
    assert info.value.reset_in_seconds > 3000


async def test_plain_403_is_not_treated_as_rate_limit() -> None:
    http = make_http(lambda r: httpx.Response(403, json={"message": "Resource not accessible"}))
    with pytest.raises(GitHubAPIError) as info:
        await http.get_json("/x")
    assert info.value.status_code == 403
    assert not info.value.is_transient


async def test_sends_versioned_headers_and_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={})

    await make_http(handler).get_json("/x", token="abc")
    assert seen["authorization"] == "Bearer abc"
    assert seen["x-github-api-version"] == "2022-11-28"
    assert seen["user-agent"] == "ReviewPilot-AI"


def test_app_jwt_is_rs256_with_short_expiry() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    now = time.time()
    token = create_app_jwt("42", pem, now=now)
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == "42"
    assert claims["iat"] <= now - 59
    assert claims["exp"] - claims["iat"] <= 600


async def test_oauth_error_in_200_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_verification_code"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    oauth = GitHubOAuthClient(
        GitHubHTTP(client, "https://github.com"), GitHubHTTP(client, API), "id", "secret", "cb"
    )
    with pytest.raises(GitHubOAuthError, match="bad_verification_code"):
        await oauth.exchange_code("nope")


def test_authorize_url_contains_state_and_redirect() -> None:
    client = httpx.AsyncClient()
    oauth = GitHubOAuthClient(
        GitHubHTTP(client, "https://github.com"),
        GitHubHTTP(client, API),
        "Iv1.abc",
        "secret",
        "http://localhost:8000/api/v1/auth/github/callback",
    )
    url = httpx.URL(oauth.authorize_url("st"))
    assert url.host == "github.com"
    assert url.params["client_id"] == "Iv1.abc"
    assert url.params["state"] == "st"
    assert url.params["redirect_uri"].endswith("/auth/github/callback")
