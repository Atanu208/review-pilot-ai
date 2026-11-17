"""Review job lifecycle, runner stages, review API and maintenance sweeps."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from app.analysis.engine import StaticAnalysisEngine
from app.models import (
    GitHubInstallation,
    Organization,
    PullRequest,
    Repository,
    RepositorySettings,
    ReviewJob,
    User,
    WebhookDelivery,
)
from app.models.enums import OrgRole, ReviewStatus, ReviewTrigger, WebhookStatus
from app.security.webhooks import compute_signature
from app.services.maintenance import MaintenanceService
from app.services.review_runner import ReviewRunner
from app.services.webhooks import WebhookProcessor
from app.tests.fakes.github import FakeGitHub
from app.tests.fakes.semgrep import FakeSemgrep
from app.tests.integration.conftest import (
    WEBHOOK_SECRET,
    ApiHarness,
    add_member,
    make_org,
    make_user,
)

pytestmark = pytest.mark.integration

FULL_NAME = "acme/api"
INSTALLATION = 4321
REPO_ID = 9090
SHA_1, SHA_2 = "1" * 40, "2" * 40


@dataclass
class World:
    owner: User
    org: Organization
    repo: Repository


async def _world(api: ApiHarness, *, settings: RepositorySettings | None = None) -> World:
    owner = await make_user(api.db)
    org = await make_org(api.db, owner)
    inst = GitHubInstallation(
        organization_id=org.id,
        installation_id=INSTALLATION,
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    api.db.add(inst)
    await api.db.flush()
    repo = Repository(
        organization_id=org.id, installation_id=inst.id, github_repo_id=REPO_ID, full_name=FULL_NAME
    )
    api.db.add(repo)
    await api.db.flush()
    if settings is not None:
        settings.repository_id = repo.id
        api.db.add(settings)
        await api.db.flush()
    api.github.add_installation(INSTALLATION, "acme", [FakeGitHub.repo(REPO_ID, FULL_NAME)])
    return World(owner, org, repo)


def _pr_event(action: str, number: int = 1, sha: str = SHA_1, **pr: Any) -> dict[str, Any]:
    return {
        "action": action,
        "repository": {"id": REPO_ID, "full_name": FULL_NAME},
        "installation": {"id": INSTALLATION},
        "pull_request": {
            "id": 50_000 + number,
            "number": number,
            "title": "Feature",
            "state": "open",
            "draft": False,
            "user": {"login": "dev"},
            "base": {"ref": "main", "sha": "a" * 40},
            "head": {"ref": "feature", "sha": sha},
            "html_url": f"https://github.com/{FULL_NAME}/pull/{number}",
            "created_at": "2026-11-15T10:00:00Z",
            **pr,
        },
    }


async def _deliver(api: ApiHarness, payload: dict[str, Any]) -> WebhookStatus | None:
    body = json.dumps(payload).encode()
    resp = await api.client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": str(uuid.uuid4()),
            "X-Hub-Signature-256": compute_signature(WEBHOOK_SECRET, body),
        },
    )
    assert resp.status_code == 202, resp.text
    return await WebhookProcessor(api.db, api.reviews_enqueued.append).process(
        uuid.UUID(api.enqueued[-1])
    )


async def _jobs(api: ApiHarness) -> list[ReviewJob]:
    api.db.expire_all()
    return list(await api.db.scalars(select(ReviewJob).order_by(ReviewJob.created_at)))


CLEAN_MAIN = "def handler(event):\n    return {'ok': True}\n"


def default_engine() -> StaticAnalysisEngine:
    return StaticAnalysisEngine(FakeSemgrep())


async def _run(  # type: ignore[no-untyped-def]
    api: ApiHarness,
    job_id: uuid.UUID,
    *,
    final_attempt: bool = False,
    engine: StaticAnalysisEngine | None = None,
):
    runner = ReviewRunner(api.db, api.app_api(), analysis=engine or default_engine())
    outcome = await runner.run(job_id, final_attempt=final_attempt, task_id="t-1")
    api.db.expire_all()
    job = await api.db.get(ReviewJob, job_id)
    assert job is not None
    return outcome, job


# ---- lifecycle from webhooks ----------------------------------------------------------


async def test_pr_opened_queues_review_and_enqueues_after_commit(api: ApiHarness) -> None:
    await _world(api)
    await _deliver(api, _pr_event("opened"))
    [job] = await _jobs(api)
    assert (job.status, job.trigger, job.head_sha) == (
        ReviewStatus.QUEUED,
        ReviewTrigger.WEBHOOK,
        SHA_1,
    )
    assert job.webhook_delivery_id is not None
    assert api.reviews_enqueued == [str(job.id)]


async def test_synchronize_supersedes_previous_head(api: ApiHarness) -> None:
    await _world(api)
    await _deliver(api, _pr_event("opened", sha=SHA_1))
    await _deliver(api, _pr_event("synchronize", sha=SHA_2))
    old, new = await _jobs(api)
    assert (old.status, old.status_reason) == (ReviewStatus.CANCELLED, "superseded")
    assert (new.status, new.head_sha) == (ReviewStatus.QUEUED, SHA_2)
    assert api.reviews_enqueued == [str(old.id), str(new.id)]


async def test_same_head_twice_creates_one_job(api: ApiHarness) -> None:
    await _world(api)
    await _deliver(api, _pr_event("opened", sha=SHA_1))
    await _deliver(api, _pr_event("reopened", sha=SHA_1))
    assert len(await _jobs(api)) == 1
    assert len(api.reviews_enqueued) == 1


async def test_closing_pr_cancels_active_review(api: ApiHarness) -> None:
    await _world(api)
    await _deliver(api, _pr_event("opened"))
    await _deliver(api, _pr_event("closed", state="closed"))
    [job] = await _jobs(api)
    assert (job.status, job.status_reason) == (ReviewStatus.CANCELLED, "pr_closed")


async def test_drafts_skipped_unless_enabled(api: ApiHarness) -> None:
    await _world(api)
    await _deliver(api, _pr_event("opened", draft=True))
    assert await _jobs(api) == []
    await _deliver(api, _pr_event("ready_for_review", draft=False))
    assert len(await _jobs(api)) == 1


async def test_auto_review_disabled_records_pr_without_job(api: ApiHarness) -> None:
    await _world(api, settings=RepositorySettings(auto_review_enabled=False))
    assert await _deliver(api, _pr_event("opened")) == WebhookStatus.PROCESSED
    assert await _jobs(api) == []
    assert await api.db.scalar(select(PullRequest)) is not None


# ---- runner ---------------------------------------------------------------------------


async def _queued_job(
    api: ApiHarness, files: list[dict[str, Any]] | None = None, **pull: Any
) -> ReviewJob:
    await _world(api)
    api.github.add_pull(
        FULL_NAME,
        1,
        head_sha=SHA_1,
        files=files
        if files is not None
        else [
            api.github.source_file(FULL_NAME, SHA_1, "app/main.py", CLEAN_MAIN),
            FakeGitHub.file("yarn.lock"),
        ],
        **pull,
    )
    await _deliver(api, _pr_event("opened"))
    [job] = await _jobs(api)
    return job


async def test_runner_completes_with_scope_and_timeline(api: ApiHarness) -> None:
    job = await _queued_job(api)
    outcome, job = await _run(api, job.id)
    assert outcome.status == ReviewStatus.COMPLETED and outcome.retry_in is None
    assert job.status == ReviewStatus.COMPLETED
    assert job.celery_task_id == "t-1"
    assert job.started_at and job.completed_at and job.duration_ms is not None
    top_level = [
        (t["stage"], t["status"]) for t in job.timeline if not t["stage"].startswith("ai.")
    ]
    assert top_level == [
        ("fetch_pull_request", "ok"),
        ("fetch_changed_files", "ok"),
        ("fetch_source_files", "ok"),
        ("semgrep", "ok"),
        ("tree_sitter", "ok"),
        ("ai_review", "ok"),
        ("persist_findings", "ok"),
    ]
    assert any(t["stage"] == "ai.security_reviewer" for t in job.timeline)
    scope = job.stats["scope"]
    assert (scope["files_total"], scope["files_in_scope"]) == (2, 1)
    assert scope["files_skipped"] == {"generated_or_vendored": 1}
    assert job.stats["sources"] == {"analyzed": 1, "by_language": {"python": 1}, "skipped": {}}
    assert job.summary == "Analyzed 1 file: no issues found.\n\nAutomated review completed."


async def test_runner_is_idempotent(api: ApiHarness) -> None:
    job = await _queued_job(api)
    _, first = await _run(api, job.id)
    entries = len(first.timeline)
    outcome, job = await _run(api, job.id)  # duplicate Celery delivery
    assert outcome.status == ReviewStatus.COMPLETED
    assert len(job.timeline) == entries


async def test_runner_skips_when_head_moved(api: ApiHarness) -> None:
    job = await _queued_job(api)
    api.github.pulls[(FULL_NAME, 1)]["head"]["sha"] = SHA_2
    _, job = await _run(api, job.id)
    assert (job.status, job.status_reason) == (ReviewStatus.SKIPPED, "head_moved")
    pr = await api.db.get(PullRequest, job.pull_request_id)
    assert pr is not None and pr.head_sha == SHA_2  # metadata refreshed from GitHub


async def test_runner_skips_when_nothing_in_scope(api: ApiHarness) -> None:
    job = await _queued_job(api, files=[FakeGitHub.file("package-lock.json")])
    _, job = await _run(api, job.id)
    assert (job.status, job.status_reason) == (ReviewStatus.SKIPPED, "nothing_to_review")


async def test_runner_fails_permanently_on_404(api: ApiHarness) -> None:
    job = await _queued_job(api)
    del api.github.pulls[(FULL_NAME, 1)]
    outcome, job = await _run(api, job.id)
    assert outcome.retry_in is None
    assert (job.status, job.status_reason) == (ReviewStatus.FAILED, "github_404")
    assert job.timeline[-1]["status"] == "failed"


async def test_runner_retries_transient_errors_then_fails_on_final_attempt(api: ApiHarness) -> None:
    job = await _queued_job(api)
    api.github.inject["/pulls/1"] = [httpx.Response(502)] * 8  # exhaust the HTTP-level retries
    outcome, job = await _run(api, job.id)
    assert outcome.retry_in == 0.0  # Celery applies exponential backoff
    assert job.status == ReviewStatus.QUEUED
    assert job.timeline[-1]["status"] == "error"

    api.github.inject["/pulls/1"] = [httpx.Response(502)] * 8
    outcome, job = await _run(api, job.id, final_attempt=True)
    assert (job.status, job.status_reason) == (ReviewStatus.FAILED, "retries_exhausted")


async def test_runner_waits_for_rate_limit_reset(api: ApiHarness) -> None:
    job = await _queued_job(api)
    api.github.inject["/pulls/1"] = [
        httpx.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": str(int(datetime.now(UTC).timestamp()) + 3600),
            },
            json={},
        )
    ]
    outcome, job = await _run(api, job.id)
    assert outcome.retry_in == 300  # capped
    assert job.status == ReviewStatus.QUEUED


async def test_runner_honours_cancellation_between_stages(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _queued_job(api)
    original = ReviewRunner.stage_fetch_pull_request

    async def fetch_then_user_cancels(self: ReviewRunner, ctx: Any) -> str:
        result = await original(self, ctx)
        await api.db.execute(
            update(ReviewJob)
            .where(ReviewJob.id == job.id)
            .values(status=ReviewStatus.CANCELLED, status_reason="user_cancelled")
        )
        return result

    monkeypatch.setattr(
        "app.services.review_runner.STAGES",
        [
            ("fetch_pull_request", fetch_then_user_cancels),
            ("fetch_changed_files", ReviewRunner.stage_fetch_changed_files),
        ],
    )
    outcome, job = await _run(api, job.id)
    assert outcome.status == ReviewStatus.CANCELLED
    assert job.status == ReviewStatus.CANCELLED
    assert [t["stage"] for t in job.timeline] == ["fetch_pull_request"]


async def test_runner_skips_disconnected_repository(api: ApiHarness) -> None:
    job = await _queued_job(api)
    repo = await api.db.scalar(select(Repository))
    assert repo is not None
    repo.is_connected = False
    await api.db.flush()
    _, job = await _run(api, job.id)
    assert (job.status, job.status_reason) == (ReviewStatus.SKIPPED, "repository_unavailable")


# ---- API -----------------------------------------------------------------------------


async def test_pull_request_list_and_detail(api: ApiHarness) -> None:
    job = await _queued_job(api)
    await _run(api, job.id)
    owner = await api.db.scalar(select(User))
    assert owner is not None
    await api.login(owner)
    org_id = job.organization_id

    listing = (await api.client.get(f"/api/v1/organizations/{org_id}/pull-requests")).json()
    assert listing["total"] == 1
    pr = listing["items"][0]
    assert pr["repository"]["full_name"] == FULL_NAME
    assert pr["latest_review"]["status"] == "completed"

    filtered = (
        await api.client.get(
            f"/api/v1/organizations/{org_id}/pull-requests", params={"state": "closed"}
        )
    ).json()
    assert filtered["total"] == 0

    detail = (
        await api.client.get(f"/api/v1/organizations/{org_id}/pull-requests/{pr['id']}")
    ).json()
    assert [r["id"] for r in detail["reviews"]] == [str(job.id)]

    review = (await api.client.get(f"/api/v1/organizations/{org_id}/reviews/{job.id}")).json()
    assert review["timeline"][0]["stage"] == "fetch_pull_request"
    assert review["stats"]["scope"]["files_in_scope"] == 1


async def test_manual_trigger_rbac_and_conflict(api: ApiHarness) -> None:
    w = await _world(api)
    api.github.add_pull(FULL_NAME, 1, head_sha=SHA_1, files=[FakeGitHub.file("a.py")])
    await _deliver(api, _pr_event("opened", draft=True))  # PR recorded, no job (draft)
    pr = await api.db.scalar(select(PullRequest))
    assert pr is not None
    url = f"/api/v1/organizations/{w.org.id}/pull-requests/{pr.id}/reviews"

    viewer = await make_user(api.db)
    await add_member(api.db, w.org, viewer, OrgRole.VIEWER)
    headers = await api.login(viewer)
    assert (await api.client.post(url, headers=headers)).status_code == 403

    dev = await make_user(api.db)
    await add_member(api.db, w.org, dev, OrgRole.DEVELOPER)
    headers = await api.login(dev)
    first = await api.client.post(url, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()["trigger"] == "manual" and first.json()["status"] == "queued"
    assert api.reviews_enqueued == [first.json()["id"]]

    again = await api.client.post(url, headers=headers)
    assert again.status_code == 409
    assert again.json()["error"]["details"]["review_id"] == first.json()["id"]


async def test_cancel_and_retry(api: ApiHarness) -> None:
    job = await _queued_job(api)
    owner = await api.db.scalar(select(User))
    assert owner is not None
    headers = await api.login(owner)
    base = f"/api/v1/organizations/{job.organization_id}/reviews"

    cancelled = await api.client.post(f"{base}/{job.id}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert (cancelled.json()["status"], cancelled.json()["status_reason"]) == (
        "cancelled",
        "user_cancelled",
    )
    assert (await api.client.post(f"{base}/{job.id}/cancel", headers=headers)).status_code == 409

    retried = await api.client.post(f"{base}/{job.id}/retry", headers=headers)
    assert retried.status_code == 202
    body = retried.json()
    assert (body["trigger"], body["attempt"], body["status"]) == ("retry", 2, "queued")

    # Completed reviews cannot be retried; a queued one cannot be retried either.
    assert (await api.client.post(f"{base}/{body['id']}/retry", headers=headers)).status_code == 409


async def test_reviews_are_tenant_isolated(api: ApiHarness) -> None:
    job = await _queued_job(api)
    outsider = await make_user(api.db)
    other_org = await make_org(api.db, outsider)
    headers = await api.login(outsider)
    for path in (
        f"/api/v1/organizations/{job.organization_id}/reviews/{job.id}",
        f"/api/v1/organizations/{other_org.id}/reviews/{job.id}",
    ):
        assert (await api.client.get(path)).status_code == 404
    resp = await api.client.post(
        f"/api/v1/organizations/{other_org.id}/reviews/{job.id}/cancel", headers=headers
    )
    assert resp.status_code == 404
    listing = (await api.client.get(f"/api/v1/organizations/{other_org.id}/pull-requests")).json()
    assert listing["total"] == 0


# ---- maintenance ---------------------------------------------------------------------


async def test_maintenance_sweep(api: ApiHarness) -> None:
    job = await _queued_job(api)
    now = datetime.now(UTC)
    stale_delivery = WebhookDelivery(
        delivery_id="stale-0001", event="pull_request", payload_sha256="0" * 64, payload={}
    )
    api.db.add(stale_delivery)
    await api.db.flush()
    orphan = ReviewJob(
        organization_id=job.organization_id,
        pull_request_id=job.pull_request_id,
        head_sha=SHA_2,
        trigger=ReviewTrigger.MANUAL,
        status=ReviewStatus.RUNNING,
        started_at=now - timedelta(hours=1),
    )
    api.db.add(orphan)
    await api.db.flush()
    await api.db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == stale_delivery.id)
        .values(created_at=now - timedelta(minutes=5))
    )
    await api.db.execute(
        update(ReviewJob)
        .where(ReviewJob.id == job.id)
        .values(updated_at=now - timedelta(minutes=30))
    )
    await api.db.execute(
        update(ReviewJob)
        .where(ReviewJob.id == orphan.id)
        .values(updated_at=now - timedelta(minutes=30))
    )

    orphan_id, job_id, delivery_id = orphan.id, job.id, stale_delivery.id
    result = await MaintenanceService(api.db).sweep(now)
    assert delivery_id in result.deliveries_to_requeue
    assert result.jobs_to_requeue == [job_id]
    assert result.jobs_failed == [orphan_id]
    api.db.expire_all()
    failed = await api.db.get(ReviewJob, orphan_id)
    assert failed is not None and (failed.status, failed.status_reason) == (
        ReviewStatus.FAILED,
        "worker_lost",
    )

    # Fresh work is left alone.
    assert (await MaintenanceService(api.db).sweep(now - timedelta(hours=2))).jobs_to_requeue == []


async def test_manual_review_follows_latest_commit_when_head_is_stale(api: ApiHarness) -> None:
    """A missed push webhook must not make 'Run review' useless: it reviews the latest commit."""
    w = await _world(api)
    files = [api.github.source_file(FULL_NAME, SHA_2, "app/main.py", CLEAN_MAIN)]
    api.github.add_pull(FULL_NAME, 1, head_sha=SHA_1, files=files)
    await _deliver(api, _pr_event("opened", draft=True))  # PR stored at SHA_1, no job (draft)
    api.github.pulls[(FULL_NAME, 1)]["head"]["sha"] = SHA_2  # pushed; webhook never arrived
    pr = await api.db.scalar(select(PullRequest))
    assert pr is not None
    headers = await api.login(w.owner)
    resp = await api.client.post(
        f"/api/v1/organizations/{w.org.id}/pull-requests/{pr.id}/reviews", headers=headers
    )
    assert resp.status_code == 202 and resp.json()["head_sha"] == SHA_1

    _, job = await _run(api, uuid.UUID(resp.json()["id"]))
    assert job.status == ReviewStatus.COMPLETED
    assert job.head_sha == SHA_2
    assert "moved from 1111111 to latest 2222222" in job.timeline[0]["detail"]
