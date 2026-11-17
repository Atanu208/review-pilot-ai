"""Authorization matrix over *every* API route, derived from the OpenAPI schema.

New endpoints are picked up automatically: an unclassified state-changing route, or a route
that becomes reachable anonymously, fails these tests until it is reviewed and listed here.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from app.models.enums import OrgRole
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user

pytestmark = pytest.mark.integration

# Routes that must work without a session. Everything else must answer 401.
PUBLIC = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("GET", "/api/v1/meta"),
    ("GET", "/api/v1/auth/github/login"),
    ("GET", "/api/v1/auth/github/callback"),
    ("GET", "/api/v1/github/setup"),  # validates its own signed state; redirects
    ("POST", "/api/v1/webhooks/github"),  # authenticated by HMAC signature instead
}

# Minimum organization role for every state-changing organization route.
MIN_ROLE: dict[tuple[str, str], OrgRole] = {
    ("PATCH", "/api/v1/organizations/{org_id}/members/{member_id}"): OrgRole.ADMIN,
    # Anyone may leave; removing someone else is checked in the service (can_manage).
    ("DELETE", "/api/v1/organizations/{org_id}/members/{member_id}"): OrgRole.VIEWER,
    ("POST", "/api/v1/organizations/{org_id}/invitations"): OrgRole.ADMIN,
    ("DELETE", "/api/v1/organizations/{org_id}/invitations/{invitation_id}"): OrgRole.ADMIN,
    ("POST", "/api/v1/organizations/{org_id}/repositories"): OrgRole.ADMIN,
    ("DELETE", "/api/v1/organizations/{org_id}/repositories/{repo_id}"): OrgRole.ADMIN,
    ("PATCH", "/api/v1/organizations/{org_id}/repositories/{repo_id}/settings"): OrgRole.ADMIN,
    (
        "PUT",
        "/api/v1/organizations/{org_id}/repositories/{repo_id}/rules/{rule_key}",
    ): OrgRole.ADMIN,
    (
        "DELETE",
        "/api/v1/organizations/{org_id}/repositories/{repo_id}/rules/{rule_key}",
    ): OrgRole.ADMIN,
    ("POST", "/api/v1/organizations/{org_id}/pull-requests/{pr_id}/reviews"): OrgRole.DEVELOPER,
    ("POST", "/api/v1/organizations/{org_id}/reviews/{review_id}/retry"): OrgRole.DEVELOPER,
    ("POST", "/api/v1/organizations/{org_id}/reviews/{review_id}/cancel"): OrgRole.DEVELOPER,
    ("POST", "/api/v1/organizations/{org_id}/reviews/{review_id}/publish"): OrgRole.DEVELOPER,
    ("PATCH", "/api/v1/organizations/{org_id}/findings/{finding_id}"): OrgRole.DEVELOPER,
    ("PUT", "/api/v1/organizations/{org_id}/findings/{finding_id}/feedback"): OrgRole.VIEWER,
}
# State-changing routes that are not organization-scoped (still need a session + CSRF).
USER_WRITES = {
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/organizations"),
    ("POST", "/api/v1/invitations/{token}/accept"),
}
RANK = [OrgRole.VIEWER, OrgRole.DEVELOPER, OrgRole.ADMIN, OrgRole.OWNER]
SAFE = {"GET", "HEAD", "OPTIONS"}


def _routes(api: ApiHarness) -> list[tuple[str, str]]:
    schema = api.app.openapi()
    return sorted((m.upper(), p) for p, ops in schema["paths"].items() for m in ops)


def _fill(path: str, org_id: uuid.UUID) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "org_id":
            return str(org_id)
        if name == "rule_key":
            return "reviewpilot.test-rule"
        if name == "token":
            return "x" * 43
        return str(uuid.uuid4())

    return re.sub(r"\{(\w+)\}", value, path)


async def _call(
    api: ApiHarness, method: str, url: str, headers: dict[str, str] | None = None
) -> Any:
    kwargs: dict[str, Any] = {"headers": headers or {}}
    if method not in SAFE:
        kwargs["json"] = {}
    return await api.client.request(method, url, **kwargs)


def test_every_write_route_is_classified(api: ApiHarness) -> None:
    writes = {r for r in _routes(api) if r[0] not in SAFE} - PUBLIC
    unclassified = writes - set(MIN_ROLE) - USER_WRITES
    assert not unclassified, f"classify these routes in MIN_ROLE/USER_WRITES: {unclassified}"
    assert set(MIN_ROLE) <= writes, "MIN_ROLE lists routes that no longer exist"


async def test_anonymous_requests_are_rejected_everywhere_but_public_routes(
    api: ApiHarness,
) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    reachable = []
    for method, path in _routes(api):
        if (method, path) in PUBLIC:
            continue
        resp = await _call(api, method, _fill(path, org.id))
        if resp.status_code != 401:
            reachable.append((method, path, resp.status_code))
    assert reachable == []


async def test_outsiders_get_404_on_every_organization_route(api: ApiHarness) -> None:
    owner = await make_user(api.db)
    victim = await make_org(api.db, owner, name="Victim")
    outsider = await make_user(api.db)
    await make_org(api.db, outsider, name="Other")
    headers = await api.login(outsider)

    leaks = []
    for method, path in _routes(api):
        if "{org_id}" not in path:
            continue
        resp = await _call(api, method, _fill(path, victim.id), headers)
        body = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        # 404 without revealing anything; a redirect would also leak existence.
        if resp.status_code != 404 or "Victim" in resp.text:
            leaks.append((method, path, resp.status_code, body.get("error", {}).get("code")))
    assert leaks == []


async def test_state_changing_routes_require_csrf_token(api: ApiHarness) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    await api.login(owner)  # session cookie set, but we deliberately omit the CSRF header

    missing = []
    for method, path in sorted(set(MIN_ROLE) | USER_WRITES):
        resp = await _call(api, method, _fill(path, org.id), headers={"X-CSRF-Token": "forged"})
        code = resp.json().get("error", {}).get("code") if resp.status_code == 403 else None
        if code != "csrf_failed":
            missing.append((method, path, resp.status_code))
    assert missing == []
    # Reads never need the token.
    assert (await api.client.get(f"/api/v1/organizations/{org.id}")).status_code == 200


@pytest.mark.parametrize("role", [OrgRole.VIEWER, OrgRole.DEVELOPER, OrgRole.ADMIN])
async def test_role_matrix(api: ApiHarness, role: OrgRole) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    member = await make_user(api.db)
    await add_member(api.db, org, member, role)
    headers = await api.login(member)

    wrong: list[tuple[object, ...]] = []
    for (method, path), minimum in sorted(MIN_ROLE.items()):
        resp = await _call(api, method, _fill(path, org.id), headers)
        allowed = RANK.index(role) >= RANK.index(minimum)
        # Allowed roles get past authorization (404 for the random resource ids, or 422 for
        # the empty body); insufficient roles must get 403 before anything else happens.
        if allowed and resp.status_code == 403:
            wrong.append(("denied", method, path))
        if not allowed and resp.status_code != 403:
            wrong.append(("allowed", method, path, resp.status_code))
    assert wrong == []


async def test_every_organization_read_is_open_to_viewers(api: ApiHarness) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    viewer = await make_user(api.db)
    await add_member(api.db, org, viewer, OrgRole.VIEWER)
    await api.login(viewer)

    denied = []
    for method, path in _routes(api):
        if method != "GET" or "{org_id}" not in path:
            continue
        resp = await _call(api, method, _fill(path, org.id))
        if resp.status_code in (401, 403):
            denied.append((path, resp.status_code))
    # Deliberately admin-only reads: pending invitation links, repositories that could be
    # connected, and starting the GitHub App installation (a GET redirect).
    assert sorted(denied) == [
        ("/api/v1/organizations/{org_id}/github/install", 403),
        ("/api/v1/organizations/{org_id}/invitations", 403),
        ("/api/v1/organizations/{org_id}/repositories/available", 403),
    ]


async def test_members_cannot_remove_higher_or_equal_roles(api: ApiHarness) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    dev = await make_user(api.db)
    viewer = await make_user(api.db)
    await add_member(api.db, org, dev, OrgRole.DEVELOPER)
    viewer_member = await add_member(api.db, org, viewer, OrgRole.VIEWER)
    headers = await api.login(dev)
    url = f"/api/v1/organizations/{org.id}/members/{viewer_member.id}"
    assert (await api.client.delete(url, headers=headers)).status_code == 403
    # ...but anyone can leave.
    headers = await api.login(viewer)
    assert (await api.client.delete(url, headers=headers)).status_code == 204


async def test_expired_and_revoked_sessions_are_rejected(api: ApiHarness) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.models import UserSession

    user = await make_user(api.db)
    await api.login(user)
    assert (await api.client.get("/api/v1/auth/me")).status_code == 200

    await api.db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401

    await api.login(user)
    await api.db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id)
        .values(revoked_at=datetime.now(UTC))
    )
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401

    api.client.cookies.set("rp_session", "not-a-real-token")
    assert (await api.client.get("/api/v1/auth/me")).status_code == 401
