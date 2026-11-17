"""Webhook receiver (signature, idempotency, replay) and event processing."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.models import GitHubInstallation, PullRequest, Repository, WebhookDelivery
from app.models.enums import PullRequestState, WebhookStatus
from app.security.webhooks import compute_signature
from app.services.webhooks import WebhookProcessor
from app.tests.integration.conftest import WEBHOOK_SECRET, ApiHarness, make_org, make_user

pytestmark = pytest.mark.integration


async def _post(
    api: ApiHarness,
    event: str,
    payload: dict[str, Any],
    *,
    delivery: str | None = None,
    secret: str = WEBHOOK_SECRET,
) -> Any:
    body = json.dumps(payload).encode()
    return await api.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery or str(uuid.uuid4()),
            "X-Hub-Signature-256": compute_signature(secret, body),
            "Content-Type": "application/json",
        },
    )


def _installation_payload(action: str, inst_id: int = 555) -> dict[str, Any]:
    return {
        "action": action,
        "installation": {
            "id": inst_id,
            "account": {"id": 1, "login": "acme", "type": "Organization"},
            "repository_selection": "all",
            "permissions": {"pull_requests": "write"},
            "events": ["pull_request"],
        },
        "sender": {"login": "octocat"},
    }


def _pr_payload(action: str, repo_id: int, number: int = 7, **pr: Any) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "repository": {"id": repo_id, "full_name": "acme/api"},
        "installation": {"id": 555},
        "pull_request": {
            "id": 99_000 + number,
            "number": number,
            "title": "Add login",
            "state": "open",
            "draft": False,
            "user": {"login": "dev"},
            "base": {"ref": "main", "sha": "a" * 40},
            "head": {"ref": "feature", "sha": "b" * 40},
            "html_url": f"https://github.com/acme/api/pull/{number}",
            "created_at": "2026-11-15T10:00:00Z",
            **pr,
        },
    }


async def _process(api: ApiHarness, row_id: str) -> WebhookStatus | None:
    return await WebhookProcessor(api.db, api.reviews_enqueued.append).process(uuid.UUID(row_id))


async def test_rejects_bad_signature(api: ApiHarness) -> None:
    resp = await _post(api, "installation", _installation_payload("created"), secret="wrong")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_signature"
    assert api.enqueued == []
    assert await api.db.scalar(select(WebhookDelivery)) is None


async def test_rejects_unsigned_request(api: ApiHarness) -> None:
    resp = await api.client.post(
        "/api/v1/webhooks/github",
        content=b"{}",
        headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


async def test_rejects_malformed_delivery_headers(api: ApiHarness) -> None:
    resp = await _post(api, "installation", {}, delivery="bad id with spaces")
    assert resp.status_code == 400


async def test_rejects_oversized_payload(api: ApiHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("WEBHOOK_MAX_BODY_BYTES", "100")
    get_settings.cache_clear()
    resp = await _post(api, "installation", {"padding": "x" * 500})
    assert resp.status_code == 413


async def test_ping_is_acknowledged_without_storing(api: ApiHarness) -> None:
    resp = await _post(api, "ping", {"zen": "Keep it logically awesome."})
    assert resp.status_code == 200 and resp.json() == {"status": "pong"}
    assert await api.db.scalar(select(WebhookDelivery)) is None


async def test_valid_delivery_is_stored_and_enqueued(api: ApiHarness) -> None:
    resp = await _post(api, "installation", _installation_payload("created"), delivery="abc-12345")
    assert resp.status_code == 202
    row = await api.db.scalar(
        select(WebhookDelivery).where(WebhookDelivery.delivery_id == "abc-12345")
    )
    assert row is not None
    assert row.status == WebhookStatus.RECEIVED
    assert row.installation_id == 555 and row.action == "created"
    assert api.enqueued == [str(row.id)]


async def test_duplicate_delivery_is_not_processed_twice(api: ApiHarness) -> None:
    payload = _installation_payload("created")
    first = await _post(api, "installation", payload, delivery="dup-00001")
    assert first.status_code == 202
    assert await _process(api, api.enqueued[0]) == WebhookStatus.PROCESSED

    replay = await _post(api, "installation", payload, delivery="dup-00001")
    assert replay.status_code == 200
    assert replay.json() == {"status": "duplicate", "requeued": False}
    assert len(api.enqueued) == 1
    # Even if a stale task runs again, processing is a no-op.
    assert await _process(api, api.enqueued[0]) == WebhookStatus.PROCESSED


async def test_redelivery_of_unprocessed_delivery_is_requeued(api: ApiHarness) -> None:
    payload = _installation_payload("created")
    await _post(api, "installation", payload, delivery="lost-0001")  # task lost / broker down
    replay = await _post(api, "installation", payload, delivery="lost-0001")
    assert replay.json() == {"status": "duplicate", "requeued": True}
    assert len(api.enqueued) == 2 and api.enqueued[0] == api.enqueued[1]


async def test_installation_created_and_deleted(api: ApiHarness) -> None:
    await _post(api, "installation", _installation_payload("created"))
    await _process(api, api.enqueued[-1])
    inst = await api.db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 555)
    )
    assert inst is not None and inst.account_login == "acme" and inst.organization_id is None

    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    inst.organization_id = org.id
    repo = Repository(
        organization_id=org.id, installation_id=inst.id, github_repo_id=4242, full_name="acme/api"
    )
    api.db.add(repo)
    await api.db.flush()

    await _post(api, "installation", _installation_payload("deleted"))
    assert await _process(api, api.enqueued[-1]) == WebhookStatus.PROCESSED
    await api.db.refresh(inst)
    await api.db.refresh(repo)
    assert inst.deleted_at is not None and inst.organization_id is None
    assert repo.is_connected is False


async def test_installation_suspend_and_unsuspend(api: ApiHarness) -> None:
    await _post(api, "installation", _installation_payload("created"))
    await _process(api, api.enqueued[-1])
    await _post(api, "installation", _installation_payload("suspend"))
    await _process(api, api.enqueued[-1])
    inst = await api.db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == 555)
    )
    assert inst is not None and inst.suspended_at is not None
    await _post(api, "installation", _installation_payload("unsuspend"))
    await _process(api, api.enqueued[-1])
    await api.db.refresh(inst)
    assert inst.suspended_at is None


async def _connected_repo(api: ApiHarness, github_repo_id: int = 4242) -> Repository:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    inst = GitHubInstallation(
        organization_id=org.id,
        installation_id=555,
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    api.db.add(inst)
    await api.db.flush()
    repo = Repository(
        organization_id=org.id,
        installation_id=inst.id,
        github_repo_id=github_repo_id,
        full_name="acme/api",
    )
    api.db.add(repo)
    await api.db.flush()
    return repo


async def test_repositories_removed_from_installation_are_disconnected(api: ApiHarness) -> None:
    repo = await _connected_repo(api)
    payload = {**_installation_payload("removed"), "repositories_removed": [{"id": 4242}]}
    await _post(api, "installation_repositories", payload)
    assert await _process(api, api.enqueued[-1]) == WebhookStatus.PROCESSED
    await api.db.refresh(repo)
    assert repo.is_connected is False


async def test_pull_request_events_upsert_pr_for_connected_repo(api: ApiHarness) -> None:
    repo = await _connected_repo(api)
    await _post(api, "pull_request", _pr_payload("opened", 4242, additions=10, changed_files=2))
    assert await _process(api, api.enqueued[-1]) == WebhookStatus.PROCESSED
    pr = await api.db.scalar(select(PullRequest).where(PullRequest.repository_id == repo.id))
    assert pr is not None
    assert (pr.number, pr.state, pr.head_sha, pr.additions) == (
        7,
        PullRequestState.OPEN,
        "b" * 40,
        10,
    )
    assert pr.organization_id == repo.organization_id

    await _post(
        api,
        "pull_request",
        _pr_payload("synchronize", 4242, head={"ref": "feature", "sha": "c" * 40}),
    )
    await _process(api, api.enqueued[-1])
    await _post(
        api,
        "pull_request",
        _pr_payload(
            "closed",
            4242,
            state="closed",
            merged=True,
            merged_at="2026-11-16T09:00:00Z",
            head={"ref": "feature", "sha": "c" * 40},
        ),
    )
    await _process(api, api.enqueued[-1])
    rows = list(
        await api.db.scalars(select(PullRequest).where(PullRequest.repository_id == repo.id))
    )
    assert len(rows) == 1
    await api.db.refresh(rows[0])
    assert rows[0].head_sha == "c" * 40
    assert rows[0].state == PullRequestState.MERGED and rows[0].merged_at is not None


async def test_pull_request_for_unconnected_repo_is_ignored(api: ApiHarness) -> None:
    await _post(api, "pull_request", _pr_payload("opened", 31337))
    assert await _process(api, api.enqueued[-1]) == WebhookStatus.IGNORED
    assert await api.db.scalar(select(PullRequest)) is None


async def test_unhandled_event_is_ignored(api: ApiHarness) -> None:
    await _post(api, "star", {"action": "created"})
    assert await _process(api, api.enqueued[-1]) == WebhookStatus.IGNORED


async def test_processing_failure_marks_failed_and_allows_retry(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _post(api, "installation", _installation_payload("created"), delivery="fail-0001")
    row_id = api.enqueued[-1]

    async def boom(self: WebhookProcessor, delivery: WebhookDelivery) -> bool:
        raise RuntimeError("transient")

    monkeypatch.setattr(WebhookProcessor, "_dispatch", boom)
    with pytest.raises(RuntimeError):
        await _process(api, row_id)
    row = await api.db.get(WebhookDelivery, uuid.UUID(row_id))
    assert row is not None
    await api.db.refresh(row)
    assert row.status == WebhookStatus.FAILED and "transient" in (row.error_message or "")

    monkeypatch.undo()
    assert await _process(api, row_id) == WebhookStatus.PROCESSED


async def test_replayed_body_under_new_delivery_id_is_ignored(api: ApiHarness) -> None:
    """GitHub signs only the body: a captured delivery re-sent with a fresh id must not run."""
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    inst = GitHubInstallation(
        organization_id=org.id,
        installation_id=555,
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    api.db.add(inst)
    await api.db.flush()
    api.db.add(
        Repository(
            organization_id=org.id,
            installation_id=inst.id,
            github_repo_id=4242,
            full_name="acme/api",
            is_connected=True,
        )
    )
    await api.db.flush()

    payload = _pr_payload("synchronize", 4242)
    first = await _post(api, "pull_request", payload, delivery="orig-0001")
    assert first.status_code == 202
    replay = await _post(api, "pull_request", payload, delivery="evil-0002")
    assert replay.status_code == 200
    assert replay.json() == {"status": "replay_ignored"}
    assert len(api.enqueued) == 1
    stored = (await api.db.scalars(select(WebhookDelivery.delivery_id))).all()
    assert "evil-0002" not in stored

    # A genuinely new event (different body) is still accepted.
    newer = await _post(api, "pull_request", _pr_payload("synchronize", 4242, title="v2"))
    assert newer.status_code == 202 and len(api.enqueued) == 2


async def test_replay_window_expires(api: ApiHarness) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    payload = _installation_payload("created")
    await _post(api, "installation", payload, delivery="old-00001")
    await api.db.execute(
        update(WebhookDelivery).values(created_at=datetime.now(UTC) - timedelta(hours=100))
    )
    resp = await _post(api, "installation", payload, delivery="new-00002")
    assert resp.status_code == 202


async def test_webhook_flood_is_rate_limited_per_ip(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_WEBHOOKS_PER_MINUTE", "3")
    get_settings.cache_clear()
    statuses = [
        (await _post(api, "installation", _installation_payload("created", 900 + i))).status_code
        for i in range(4)
    ]
    assert statuses == [202, 202, 202, 429]
    # Unsigned floods are throttled too (before any signature or database work).
    bad = await _post(api, "installation", _installation_payload("created"), secret="wrong")
    assert bad.status_code == 429
