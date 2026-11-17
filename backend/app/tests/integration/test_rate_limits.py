"""Per-session write budget and the tighter budget for review actions."""

from __future__ import annotations

import uuid

import pytest

from app.models.enums import OrgRole
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user

pytestmark = pytest.mark.integration


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_WRITES_PER_MINUTE", "5")
    monkeypatch.setenv("RATE_LIMIT_REVIEW_ACTIONS_PER_MINUTE", "2")
    get_settings.cache_clear()


async def test_review_actions_have_their_own_tight_budget(api: ApiHarness, limits: None) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    headers = await api.login(owner)
    url = f"/api/v1/organizations/{org.id}/pull-requests/{uuid.uuid4()}/reviews"
    statuses = [(await api.client.post(url, headers=headers)).status_code for _ in range(3)]
    assert statuses == [404, 404, 429]
    body = (await api.client.post(url, headers=headers)).json()
    assert body["error"]["code"] == "rate_limited"
    assert 0 < body["error"]["details"]["retry_after_seconds"] <= 60


async def test_write_budget_is_per_session_and_reads_are_free(
    api: ApiHarness, limits: None
) -> None:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    other = await make_user(api.db)
    await add_member(api.db, org, other, OrgRole.DEVELOPER)
    url = f"/api/v1/organizations/{org.id}/findings/{uuid.uuid4()}/feedback"

    headers = await api.login(owner)
    for _ in range(10):  # reads never count
        assert (await api.client.get(f"/api/v1/organizations/{org.id}")).status_code == 200
    statuses = [
        (await api.client.put(url, json={"rating": "helpful"}, headers=headers)).status_code
        for _ in range(6)
    ]
    assert statuses == [404] * 5 + [429]

    # Another user (same IP in tests) has an independent budget.
    headers = await api.login(other)
    assert (
        await api.client.put(url, json={"rating": "helpful"}, headers=headers)
    ).status_code == 404


async def test_rate_limiter_fails_open_when_redis_is_down(
    api: ApiHarness, limits: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import rate_limit

    class Broken:
        def pipeline(self) -> None:
            raise ConnectionError("redis down")

    monkeypatch.setattr(rate_limit, "get_redis", lambda: Broken())
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    headers = await api.login(owner)
    url = f"/api/v1/organizations/{org.id}/pull-requests/{uuid.uuid4()}/reviews"
    assert [(await api.client.post(url, headers=headers)).status_code for _ in range(4)] == [
        404
    ] * 4
