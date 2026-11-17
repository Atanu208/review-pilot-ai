"""GitHub App installation flow and repository management (GitHub faked)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from app.models import GitHubInstallation, User
from app.models.enums import OrgRole
from app.security.encryption import encrypt
from app.tests.fakes.github import FakeGitHub
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user

pytestmark = pytest.mark.integration

INSTALLATION_ID = 777


async def _github_user(api: ApiHarness, login: str, token: str) -> User:
    user = await make_user(api.db, login)
    user.github_token_encrypted = encrypt(token)
    await api.db.flush()
    return user


async def _install_state(api: ApiHarness, org_id: object) -> str:
    resp = await api.client.get(f"/api/v1/organizations/{org_id}/github/install")
    assert resp.status_code == 302, resp.text
    url = httpx.URL(resp.headers["location"])
    assert url.path == "/apps/reviewpilot-test/installations/new"
    return url.params["state"]


async def _link_installation(api: ApiHarness) -> tuple[User, object]:
    owner = await _github_user(api, "owner1", "gho_owner")
    org = await make_org(api.db, owner)
    api.github.add_installation(
        INSTALLATION_ID,
        "acme",
        [FakeGitHub.repo(101, "acme/api"), FakeGitHub.repo(102, "acme/web", private=False)],
    )
    api.github.user_installations["gho_owner"] = [INSTALLATION_ID]
    await api.login(owner)
    state = await _install_state(api, org.id)
    resp = await api.client.get(
        "/api/v1/github/setup",
        params={"installation_id": INSTALLATION_ID, "setup_action": "install", "state": state},
    )
    assert (
        resp.headers["location"]
        == f"http://localhost:3000/repositories?github=installed&org={org.id}"
    )
    return owner, org.id


async def test_install_redirect_requires_admin(api: ApiHarness) -> None:
    owner, dev = await make_user(api.db), await make_user(api.db)
    org = await make_org(api.db, owner)
    await add_member(api.db, org, dev, OrgRole.DEVELOPER)
    await api.login(dev)
    assert (
        await api.client.get(f"/api/v1/organizations/{org.id}/github/install")
    ).status_code == 403


async def test_setup_links_installation_to_org(api: ApiHarness) -> None:
    _, org_id = await _link_installation(api)
    row = await api.db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == INSTALLATION_ID)
    )
    assert row is not None and row.organization_id == org_id
    assert row.account_login == "acme"
    assert row.permissions["pull_requests"] == "write"
    listed = (await api.client.get(f"/api/v1/organizations/{org_id}/github/installations")).json()
    assert [i["installation_id"] for i in listed] == [INSTALLATION_ID]


async def test_setup_rejects_installation_user_cannot_see(api: ApiHarness) -> None:
    """Claiming someone else's installation by guessing its id must fail."""
    attacker = await _github_user(api, "attacker", "gho_attacker")
    org = await make_org(api.db, attacker)
    api.github.add_installation(INSTALLATION_ID, "victim-co", [])
    api.github.user_installations["gho_attacker"] = []  # not visible to this user
    await api.login(attacker)
    state = await _install_state(api, org.id)
    resp = await api.client.get(
        "/api/v1/github/setup", params={"installation_id": INSTALLATION_ID, "state": state}
    )
    assert resp.headers["location"].endswith("github_error=installation_not_accessible")
    assert (
        await api.db.scalar(
            select(GitHubInstallation).where(GitHubInstallation.installation_id == INSTALLATION_ID)
        )
        is None
    )


async def test_setup_rejects_state_from_another_user(api: ApiHarness) -> None:
    alice = await _github_user(api, "alice", "gho_alice")
    bob = await _github_user(api, "bob", "gho_bob")
    org = await make_org(api.db, alice)
    await add_member(api.db, org, bob, OrgRole.ADMIN)
    api.github.add_installation(INSTALLATION_ID, "acme", [])
    api.github.user_installations["gho_bob"] = [INSTALLATION_ID]
    await api.login(alice)
    state = await _install_state(api, org.id)
    await api.login(bob)
    resp = await api.client.get(
        "/api/v1/github/setup", params={"installation_id": INSTALLATION_ID, "state": state}
    )
    assert resp.headers["location"].endswith("github_error=invalid_state")


async def test_setup_without_session_redirects_to_login(api: ApiHarness) -> None:
    resp = await api.client.get("/api/v1/github/setup", params={"installation_id": 1, "state": "x"})
    assert resp.headers["location"].startswith("http://localhost:3000/login?next=")


async def test_setup_requires_valid_github_token(api: ApiHarness) -> None:
    owner = await make_user(api.db, "no-token")  # never stored a GitHub token
    org = await make_org(api.db, owner)
    await api.login(owner)
    state = await _install_state(api, org.id)
    resp = await api.client.get(
        "/api/v1/github/setup", params={"installation_id": INSTALLATION_ID, "state": state}
    )
    assert resp.headers["location"].endswith("github_error=github_reauth_required")


async def test_available_connect_disconnect_repository(api: ApiHarness) -> None:
    owner, org_id = await _link_installation(api)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org_id}/repositories"

    available = (await api.client.get(f"{base}/available")).json()
    assert [(r["full_name"], r["connected"]) for r in available] == [
        ("acme/api", False),
        ("acme/web", False),
    ]

    created = await api.client.post(base, json={"github_repo_id": 101}, headers=headers)
    assert created.status_code == 201, created.text
    repo = created.json()
    assert repo["full_name"] == "acme/api" and repo["is_connected"] is True
    assert repo["settings"]["min_confidence"] == "0.700"
    assert repo["settings"]["enabled_languages"] == ["python", "java", "javascript", "typescript"]

    listing = (await api.client.get(base)).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == repo["id"]
    available = (await api.client.get(f"{base}/available")).json()
    assert available[0]["connected"] is True

    # Reconnecting is idempotent.
    again = await api.client.post(base, json={"github_repo_id": 101}, headers=headers)
    assert again.status_code == 201 and again.json()["id"] == repo["id"]

    assert (await api.client.delete(f"{base}/{repo['id']}", headers=headers)).status_code == 204
    assert (await api.client.get(base)).json()["total"] == 0
    all_repos = (await api.client.get(base, params={"include_disconnected": True})).json()
    assert all_repos["items"][0]["is_connected"] is False


async def test_cannot_connect_repository_outside_installation(api: ApiHarness) -> None:
    owner, org_id = await _link_installation(api)
    headers = await api.login(owner)
    resp = await api.client.post(
        f"/api/v1/organizations/{org_id}/repositories",
        json={"github_repo_id": 999},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "repository_not_accessible"


async def test_settings_update_and_validation(api: ApiHarness) -> None:
    owner, org_id = await _link_installation(api)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org_id}/repositories"
    repo = (await api.client.post(base, json={"github_repo_id": 101}, headers=headers)).json()
    url = f"{base}/{repo['id']}/settings"

    ok = await api.client.patch(
        url,
        json={
            "min_confidence": "0.85",
            "require_human_approval": True,
            "enabled_languages": ["python"],
            "ignored_paths": ["vendor/**", "*.min.js"],
        },
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    settings = ok.json()["settings"]
    assert settings["min_confidence"] == "0.850"
    assert settings["require_human_approval"] is True
    assert settings["enabled_languages"] == ["python"]
    assert settings["ignored_paths"] == ["vendor/**", "*.min.js"]
    assert settings["publish_summary"] is True  # untouched fields keep their values

    for bad in (
        {"min_confidence": "1.5"},
        {"enabled_languages": ["cobol"]},
        {"ignored_paths": ["../../etc/passwd"]},
        {"max_files": 0},
    ):
        resp = await api.client.patch(url, json=bad, headers=headers)
        assert resp.status_code == 422, bad


async def test_developer_cannot_change_settings_but_can_read(api: ApiHarness) -> None:
    owner, org_id = await _link_installation(api)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org_id}/repositories"
    repo = (await api.client.post(base, json={"github_repo_id": 101}, headers=headers)).json()

    dev = await make_user(api.db)
    from app.models import OrganizationMember

    api.db.add(OrganizationMember(organization_id=org_id, user_id=dev.id, role=OrgRole.DEVELOPER))
    await api.db.flush()
    headers = await api.login(dev)
    assert (await api.client.get(f"{base}/{repo['id']}")).status_code == 200
    resp = await api.client.patch(
        f"{base}/{repo['id']}/settings", json={"max_files": 10}, headers=headers
    )
    assert resp.status_code == 403
    assert (await api.client.get(f"{base}/available")).status_code == 403


async def test_review_rules_crud(api: ApiHarness) -> None:
    owner, org_id = await _link_installation(api)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org_id}/repositories"
    repo = (await api.client.post(base, json={"github_repo_id": 101}, headers=headers)).json()
    rules = f"{base}/{repo['id']}/rules"

    put = await api.client.put(
        f"{rules}/python.lang.security.audit.eval-detected",
        json={"engine": "semgrep", "enabled": False, "severity_override": "low"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["enabled"] is False
    updated = await api.client.put(
        f"{rules}/python.lang.security.audit.eval-detected",
        json={"engine": "semgrep", "enabled": True},
        headers=headers,
    )
    assert updated.json()["id"] == put.json()["id"] and updated.json()["enabled"] is True
    assert len((await api.client.get(rules)).json()) == 1
    bad_key = await api.client.put(f"{rules}/bad key!", json={"engine": "ai"}, headers=headers)
    assert bad_key.status_code == 422
    assert (
        await api.client.delete(
            f"{rules}/python.lang.security.audit.eval-detected", headers=headers
        )
    ).status_code == 204
    assert (await api.client.get(rules)).json() == []
