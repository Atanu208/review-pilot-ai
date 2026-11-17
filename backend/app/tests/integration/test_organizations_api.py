"""Organizations, RBAC, multi-tenant isolation and invitations."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import OrganizationMember
from app.models.enums import OrgRole
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user

pytestmark = pytest.mark.integration


async def test_create_organization_makes_caller_owner(api: ApiHarness) -> None:
    user = await make_user(api.db)
    headers = await api.login(user)
    resp = await api.client.post(
        "/api/v1/organizations", json={"name": "  Acme   Corp "}, headers=headers
    )
    assert resp.status_code == 201
    org = resp.json()
    assert org["name"] == "Acme Corp"
    assert org["slug"].startswith("acme-corp")

    listing = (await api.client.get("/api/v1/organizations")).json()
    assert [o["id"] for o in listing] == [org["id"]]
    assert listing[0]["role"] == "owner"
    assert listing[0]["member_count"] == 1


async def test_duplicate_names_get_unique_slugs(api: ApiHarness) -> None:
    headers = await api.login(await make_user(api.db))
    a = (
        await api.client.post("/api/v1/organizations", json={"name": "Same"}, headers=headers)
    ).json()
    b = (
        await api.client.post("/api/v1/organizations", json={"name": "Same"}, headers=headers)
    ).json()
    assert a["slug"] != b["slug"]


async def test_validation_error_envelope(api: ApiHarness) -> None:
    headers = await api.login(await make_user(api.db))
    resp = await api.client.post("/api/v1/organizations", json={"name": "x"}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_tenant_isolation_non_members_get_404(api: ApiHarness) -> None:
    alice, mallory = await make_user(api.db), await make_user(api.db)
    org = await make_org(api.db, alice)
    headers = await api.login(mallory)
    for path in (
        f"/api/v1/organizations/{org.id}",
        f"/api/v1/organizations/{org.id}/members",
        f"/api/v1/organizations/{org.id}/repositories",
        f"/api/v1/organizations/{org.id}/invitations",
        f"/api/v1/organizations/{org.id}/github/installations",
    ):
        resp = await api.client.get(path)
        assert resp.status_code == 404, path
    resp = await api.client.post(
        f"/api/v1/organizations/{org.id}/invitations",
        json={"github_login": "mallory-friend"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert (await api.client.get("/api/v1/organizations")).json() == []


@pytest.mark.parametrize(
    ("role", "status"),
    [(OrgRole.VIEWER, 403), (OrgRole.DEVELOPER, 403), (OrgRole.ADMIN, 201)],
)
async def test_only_admins_can_invite(api: ApiHarness, role: OrgRole, status: int) -> None:
    owner, member = await make_user(api.db), await make_user(api.db)
    org = await make_org(api.db, owner)
    await add_member(api.db, org, member, role)
    headers = await api.login(member)
    resp = await api.client.post(
        f"/api/v1/organizations/{org.id}/invitations",
        json={"github_login": "newbie", "role": "developer"},
        headers=headers,
    )
    assert resp.status_code == status


async def test_admin_cannot_grant_or_touch_owner(api: ApiHarness) -> None:
    owner, admin, dev = await make_user(api.db), await make_user(api.db), await make_user(api.db)
    org = await make_org(api.db, owner)
    admin_m = await add_member(api.db, org, admin, OrgRole.ADMIN)
    dev_m = await add_member(api.db, org, dev, OrgRole.DEVELOPER)
    owner_m = await api.db.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == owner.id)
    )
    assert owner_m is not None
    headers = await api.login(admin)
    base = f"/api/v1/organizations/{org.id}/members"

    promote = await api.client.patch(f"{base}/{dev_m.id}", json={"role": "owner"}, headers=headers)
    assert promote.status_code == 403
    demote_owner = await api.client.patch(
        f"{base}/{owner_m.id}", json={"role": "viewer"}, headers=headers
    )
    assert demote_owner.status_code == 403
    self_promote = await api.client.patch(
        f"{base}/{admin_m.id}", json={"role": "owner"}, headers=headers
    )
    assert self_promote.status_code == 403
    ok = await api.client.patch(f"{base}/{dev_m.id}", json={"role": "viewer"}, headers=headers)
    assert ok.status_code == 200 and ok.json()["role"] == "viewer"


async def test_last_owner_cannot_leave_or_be_demoted(api: ApiHarness) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    owner_m = await api.db.scalar(
        select(OrganizationMember).where(OrganizationMember.user_id == owner.id)
    )
    assert owner_m is not None
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org.id}/members/{owner_m.id}"
    demote = await api.client.patch(base, json={"role": "admin"}, headers=headers)
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "last_owner"
    leave = await api.client.delete(base, headers=headers)
    assert leave.status_code == 409

    second = await make_user(api.db)
    await add_member(api.db, org, second, OrgRole.OWNER)
    assert (await api.client.delete(base, headers=headers)).status_code == 204


async def test_member_can_leave_and_viewer_cannot_remove_others(api: ApiHarness) -> None:
    owner, viewer, dev = await make_user(api.db), await make_user(api.db), await make_user(api.db)
    org = await make_org(api.db, owner)
    viewer_m = await add_member(api.db, org, viewer, OrgRole.VIEWER)
    dev_m = await add_member(api.db, org, dev, OrgRole.DEVELOPER)
    headers = await api.login(viewer)
    base = f"/api/v1/organizations/{org.id}/members"
    assert (await api.client.delete(f"{base}/{dev_m.id}", headers=headers)).status_code == 403
    assert (await api.client.delete(f"{base}/{viewer_m.id}", headers=headers)).status_code == 204


async def test_invitation_lifecycle(api: ApiHarness) -> None:
    owner, invitee, stranger = (
        await make_user(api.db, "boss"),
        await make_user(api.db, "Invitee-1"),
        await make_user(api.db, "stranger"),
    )
    org = await make_org(api.db, owner)
    headers = await api.login(owner)
    created = await api.client.post(
        f"/api/v1/organizations/{org.id}/invitations",
        json={"github_login": "invitee-1", "role": "admin"},
        headers=headers,
    )
    assert created.status_code == 201
    invite_url = created.json()["invite_url"]
    token = invite_url.rsplit("/", 1)[-1]
    assert invite_url.startswith("http://localhost:3000/invitations/")

    dup = await api.client.post(
        f"/api/v1/organizations/{org.id}/invitations",
        json={"github_login": "INVITEE-1"},
        headers=headers,
    )
    assert dup.status_code == 409
    pending = (await api.client.get(f"/api/v1/organizations/{org.id}/invitations")).json()
    assert [p["github_login"] for p in pending] == ["invitee-1"]
    assert "token" not in str(pending)

    # A different GitHub user cannot redeem the link.
    headers = await api.login(stranger)
    wrong = await api.client.post(f"/api/v1/invitations/{token}/accept", headers=headers)
    assert wrong.status_code == 404

    headers = await api.login(invitee)
    accepted = await api.client.post(f"/api/v1/invitations/{token}/accept", headers=headers)
    assert accepted.status_code == 200
    assert accepted.json() == {"organization_id": str(org.id), "role": "admin"}
    again = await api.client.post(f"/api/v1/invitations/{token}/accept", headers=headers)
    assert again.status_code == 404

    members = (await api.client.get(f"/api/v1/organizations/{org.id}/members")).json()
    assert {m["user"]["login"]: m["role"] for m in members} == {
        "boss": "owner",
        "Invitee-1": "admin",
    }


async def test_cannot_invite_existing_member_and_can_revoke(api: ApiHarness) -> None:
    owner, dev = await make_user(api.db, "own"), await make_user(api.db, "devvy")
    org = await make_org(api.db, owner)
    await add_member(api.db, org, dev, OrgRole.DEVELOPER)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org.id}/invitations"
    existing = await api.client.post(base, json={"github_login": "devvy"}, headers=headers)
    assert existing.status_code == 409
    inv = (await api.client.post(base, json={"github_login": "later"}, headers=headers)).json()
    assert (await api.client.delete(f"{base}/{inv['id']}", headers=headers)).status_code == 204
    assert (await api.client.get(base)).json() == []
