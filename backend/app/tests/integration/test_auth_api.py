"""GitHub sign-in, sessions, CSRF and logout against a real database (GitHub faked)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models import AuditLog, User, UserSession
from app.security.encryption import decrypt
from app.security.tokens import hash_token
from app.tests.fakes.github import FakeUser
from app.tests.integration.conftest import ApiHarness, make_user

pytestmark = pytest.mark.integration


async def _start_login(api: ApiHarness, next_path: str = "/repositories") -> tuple[str, str]:
    resp = await api.client.get("/api/v1/auth/github/login", params={"next": next_path})
    assert resp.status_code == 302
    location = httpx.URL(resp.headers["location"])
    assert location.host == "github.com"
    assert location.params["client_id"] == "Iv1.test"
    cookie = resp.cookies.get("rp_oauth_state")
    assert cookie
    return location.params["state"], cookie


async def test_full_login_flow_creates_user_session_and_encrypted_token(api: ApiHarness) -> None:
    api.github.add_user(FakeUser(id=9001, login="octocat", name="Octo Cat"), "code-1", "gho_secret")
    state, _ = await _start_login(api)

    resp = await api.client.get(
        "/api/v1/auth/github/callback", params={"code": "code-1", "state": state}
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "http://localhost:3000/repositories"
    set_cookie = resp.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("rp_session="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie

    user = await api.db.scalar(select(User).where(User.github_id == 9001))
    assert user is not None and user.login == "octocat"
    assert user.github_token_encrypted and "gho_secret" not in user.github_token_encrypted
    assert decrypt(user.github_token_encrypted) == "gho_secret"
    assert user.github_refresh_token_encrypted is not None
    assert await api.db.scalar(select(AuditLog).where(AuditLog.action == "auth.login"))

    me = await api.client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["login"] == "octocat"
    assert len(body["csrf_token"]) == 64
    assert "github_token" not in me.text and "gho_secret" not in me.text


async def test_callback_rejects_missing_state_cookie(api: ApiHarness) -> None:
    api.github.add_user(FakeUser(id=1, login="a"), "code-x", "gho_x")
    state, _ = await _start_login(api)
    api.client.cookies.clear()  # attacker-initiated callback in victim's browser
    resp = await api.client.get(
        "/api/v1/auth/github/callback", params={"code": "code-x", "state": state}
    )
    assert resp.headers["location"].endswith("/login?error=invalid_state")
    assert await api.db.scalar(select(User).where(User.github_id == 1)) is None


async def test_callback_rejects_forged_state(api: ApiHarness) -> None:
    await _start_login(api)
    resp = await api.client.get(
        "/api/v1/auth/github/callback", params={"code": "c", "state": "forged.state"}
    )
    assert resp.headers["location"].endswith("/login?error=invalid_state")


async def test_callback_with_invalid_code(api: ApiHarness) -> None:
    state, _ = await _start_login(api)
    resp = await api.client.get(
        "/api/v1/auth/github/callback", params={"code": "unknown", "state": state}
    )
    assert resp.headers["location"].endswith("/login?error=github_denied")


async def test_callback_user_denied_on_github(api: ApiHarness) -> None:
    resp = await api.client.get("/api/v1/auth/github/callback", params={"error": "access_denied"})
    assert resp.headers["location"].endswith("/login?error=github_denied")


async def test_open_redirect_in_next_is_neutralised(api: ApiHarness) -> None:
    api.github.add_user(FakeUser(id=2, login="b"), "code-b", "gho_b")
    state, _ = await _start_login(api, next_path="https://evil.example/")
    resp = await api.client.get(
        "/api/v1/auth/github/callback", params={"code": "code-b", "state": state}
    )
    assert resp.headers["location"] == "http://localhost:3000/dashboard"


async def test_me_requires_session(api: ApiHarness) -> None:
    resp = await api.client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_expired_and_revoked_sessions_rejected(api: ApiHarness) -> None:
    user = await make_user(api.db)
    await api.login(user)
    row = await api.db.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await api.db.flush()
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401


async def test_disabled_user_session_rejected(api: ApiHarness) -> None:
    user = await make_user(api.db)
    await api.login(user)
    user.is_active = False
    await api.db.flush()
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401


async def test_state_changing_request_requires_csrf(api: ApiHarness) -> None:
    user = await make_user(api.db)
    headers = await api.login(user)
    no_csrf = await api.client.post("/api/v1/organizations", json={"name": "Acme"})
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "csrf_failed"
    wrong = await api.client.post(
        "/api/v1/organizations", json={"name": "Acme"}, headers={"X-CSRF-Token": "0" * 64}
    )
    assert wrong.status_code == 403
    ok = await api.client.post("/api/v1/organizations", json={"name": "Acme"}, headers=headers)
    assert ok.status_code == 201


async def test_logout_revokes_session(api: ApiHarness) -> None:
    user = await make_user(api.db)
    headers = await api.login(user)
    token = api.client.cookies.get("rp_session")
    assert token
    resp = await api.client.post("/api/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    row = await api.db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    )
    assert row is not None and row.revoked_at is not None
    api.client.cookies.set("rp_session", token)  # replaying the old cookie must fail
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401


async def test_login_rate_limited(api: ApiHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    get_settings.cache_clear()
    statuses = [(await api.client.get("/api/v1/auth/github/login")).status_code for _ in range(32)]
    assert statuses.count(302) == 30
    assert statuses[-1] == 429
