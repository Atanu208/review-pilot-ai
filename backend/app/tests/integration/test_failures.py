"""Failure scenarios at the API boundary: internal errors, database loss, broker loss.

(LLM, Semgrep, GitHub and Redis failures are covered next to their modules.)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import exc as sa_exc
from sqlalchemy import select

from app.api.v1.webhooks import get_webhook_enqueuer
from app.models import WebhookDelivery
from app.models.enums import WebhookStatus
from app.security.webhooks import compute_signature
from app.tests.integration.conftest import WEBHOOK_SECRET, ApiHarness, make_user

pytestmark = pytest.mark.integration

SECRETISH = "postgresql://admin:hunter2@db.internal:5432"


@pytest.fixture
async def raw_client(api: ApiHarness) -> AsyncIterator[httpx.AsyncClient]:
    """Client that returns 500 responses instead of re-raising server exceptions."""

    async def boom() -> None:
        raise RuntimeError(f"cannot connect to {SECRETISH}")

    async def db_down() -> None:
        raise sa_exc.OperationalError(
            "SELECT * FROM users", {}, Exception(f"connection refused {SECRETISH}")
        )

    async def pool_timeout() -> None:
        raise sa_exc.TimeoutError("QueuePool limit reached")

    api.app.add_api_route("/__test/boom", boom)
    api.app.add_api_route("/__test/db-down", db_down)
    api.app.add_api_route("/__test/pool", pool_timeout)
    transport = httpx.ASGITransport(app=api.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_unhandled_error_is_generic_and_leaks_nothing(raw_client: httpx.AsyncClient) -> None:
    resp = await raw_client.get("/__test/boom", headers={"X-Request-ID": "req-failure-01"})
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "internal_error"
    assert error["request_id"] == "req-failure-01"
    assert "hunter2" not in resp.text and "db.internal" not in resp.text
    assert "Traceback" not in resp.text and "RuntimeError" not in resp.text
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/__test/db-down", "/__test/pool"])
async def test_database_outage_is_a_retryable_503(raw_client: httpx.AsyncClient, path: str) -> None:
    resp = await raw_client.get(path)
    assert resp.status_code == 503
    assert resp.headers["retry-after"] == "5"
    assert resp.json()["error"]["code"] == "service_unavailable"
    assert "hunter2" not in resp.text and "SELECT" not in resp.text


async def test_webhook_is_still_accepted_when_the_broker_is_down(api: ApiHarness) -> None:
    def broker_down(_: str) -> None:
        raise ConnectionError("redis broker unreachable")

    api.app.dependency_overrides[get_webhook_enqueuer] = lambda: broker_down
    body = json.dumps(
        {"action": "created", "installation": {"id": 31337, "account": {"id": 1, "login": "x"}}}
    ).encode()
    delivery = str(uuid.uuid4())
    resp = await api.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "installation",
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": compute_signature(WEBHOOK_SECRET, body),
            "Content-Type": "application/json",
        },
    )
    # GitHub must not see an error (it would not retry automatically); the row is durable
    # and the maintenance sweep re-enqueues it once the broker is back.
    assert resp.status_code == 202
    row = await api.db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery)
    )
    assert row is not None and row.status == WebhookStatus.RECEIVED


async def test_malformed_json_bodies_are_rejected_cleanly(api: ApiHarness) -> None:
    from app.tests.integration.conftest import make_org

    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    headers = await api.login(owner)
    resp = await api.client.patch(
        f"/api/v1/organizations/{org.id}/findings/{uuid.uuid4()}",
        content=b"{not json",
        headers={**headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_oversized_and_hostile_inputs_are_rejected(api: ApiHarness) -> None:
    from app.tests.integration.conftest import make_org

    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{org.id}"
    # Overlong / injection-shaped values are validated before reaching SQL or GitHub.
    resp = await api.client.post(
        f"{base}/invitations", json={"github_login": "a" * 500, "role": "viewer"}, headers=headers
    )
    assert resp.status_code == 422
    resp = await api.client.post(
        f"{base}/invitations",
        json={"github_login": "x'; DROP TABLE users;--", "role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 422
    resp = await api.client.get(f"{base}/findings", params={"severity": "critical' OR 1=1--"})
    assert resp.status_code == 422
    resp = await api.client.get(f"{base}/findings", params={"limit": 100000})
    assert resp.status_code == 422
    resp = await api.client.put(
        f"{base}/repositories/{uuid.uuid4()}/rules/..%2F..%2Fetc",
        json={"engine": "semgrep"},
        headers=headers,
    )
    assert resp.status_code in (404, 422)
    # Path traversal / odd globs in ignored paths are refused.
    resp = await api.client.patch(
        f"{base}/repositories/{uuid.uuid4()}/settings",
        json={"ignored_paths": ["../../secrets/*"]},
        headers=headers,
    )
    assert resp.status_code == 422
