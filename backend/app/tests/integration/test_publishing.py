"""Publishing reviews to GitHub: auto, approval, fallback, staleness, re-review, retry."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.analysis.engine import StaticAnalysisEngine
from app.core.config import get_settings
from app.models import RepositorySettings, ReviewComment, ReviewFinding, ReviewJob, User
from app.models.enums import CommentStatus, FindingStatus, OrgRole, ReviewStatus, Severity
from app.services.maintenance import MaintenanceService
from app.services.publisher import ReviewPublisher
from app.services.review_runner import ReviewRunner
from app.tests.fakes.semgrep import FakeSemgrep, match
from app.tests.integration.conftest import ApiHarness, add_member, make_user
from app.tests.integration.test_reviews import FULL_NAME, SHA_1, SHA_2, _deliver, _pr_event, _world
from app.tests.integration.test_static_analysis import SQL_RULE, VULNERABLE

pytestmark = pytest.mark.integration


@pytest.fixture
def publishing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_PUBLISH_ENABLED", "true")
    get_settings.cache_clear()


async def _analyzed(
    api: ApiHarness,
    *,
    settings: RepositorySettings | None = None,
    sha: str = SHA_1,
    deliver_action: str = "opened",
) -> tuple[uuid.UUID, list[str]]:
    """Run a review of VULNERABLE (lines 3-5 changed); returns (job id, publish queue)."""
    if deliver_action == "opened":
        await _world(api, settings=settings)
    files = [api.github.source_file(FULL_NAME, sha, "app/users.py", VULNERABLE, added={3, 4, 5})]
    api.github.add_pull(FULL_NAME, 1, head_sha=sha, files=files)
    await _deliver(api, _pr_event(deliver_action, sha=sha))
    job = await api.db.scalar(select(ReviewJob).where(ReviewJob.head_sha == sha))
    assert job is not None
    job_id = job.id
    queue: list[str] = []
    engine = StaticAnalysisEngine(FakeSemgrep([match(SQL_RULE, "app/users.py", 4)]))
    await ReviewRunner(api.db, api.app_api(), analysis=engine, publish_enqueue=queue.append).run(
        job_id, final_attempt=False
    )
    return job_id, queue


async def _publish(api: ApiHarness, job_id: uuid.UUID, *, final_attempt: bool = False) -> str:
    outcome = await ReviewPublisher(api.db, api.app_api(), get_settings()).publish(
        job_id, final_attempt=final_attempt
    )
    api.db.expire_all()
    return outcome.status


async def _job(api: ApiHarness, job_id: uuid.UUID) -> ReviewJob:
    api.db.expire_all()
    job = await api.db.get(ReviewJob, job_id)
    assert job is not None
    return job


async def _comments(api: ApiHarness, job_id: uuid.UUID) -> list[ReviewComment]:
    return list(
        await api.db.scalars(
            select(ReviewComment)
            .where(ReviewComment.review_job_id == job_id)
            .order_by(ReviewComment.line)
        )
    )


async def test_dry_run_is_default_and_posts_nothing(api: ApiHarness) -> None:
    job_id, queue = await _analyzed(api)
    job = await _job(api, job_id)
    assert job.status == ReviewStatus.COMPLETED
    assert job.stats["publication"] == {"status": "disabled", "reason": "dry_run"}
    assert queue == [] and api.github.reviews == {}


async def test_auto_publish_posts_inline_comments_and_summary(
    api: ApiHarness, publishing: None
) -> None:
    job_id, queue = await _analyzed(api)
    assert (await _job(api, job_id)).status == ReviewStatus.PUBLISHING
    assert queue == [str(job_id)]

    assert await _publish(api, job_id) == "published"
    [review] = api.github.reviews[(FULL_NAME, 1)]
    assert review["event"] == "COMMENT" and review["commit_id"] == SHA_1
    assert f"reviewpilot:review job={job_id}" in review["body"]
    assert sorted((c["path"], c["line"]) for c in review["comments"]) == [
        ("app/users.py", 3),
        ("app/users.py", 4),
    ]
    sql = next(c for c in review["comments"] if c["line"] == 4)
    assert sql["side"] == "RIGHT" and "Critical · security" in sql["body"]

    job = await _job(api, job_id)
    assert job.status == ReviewStatus.COMPLETED and job.status_reason is None
    pub = job.stats["publication"]
    assert pub["status"] == "published" and pub["inline_comments"] == 2 and pub["html_url"]
    assert job.timeline[-1]["stage"] == "publish"
    rows = await _comments(api, job_id)
    assert {r.kind.value for r in rows} == {"inline", "summary"}
    assert all(r.status == CommentStatus.PUBLISHED for r in rows)
    assert all(r.github_comment_id for r in rows if r.kind.value == "inline")

    assert await _publish(api, job_id) == "noop"  # idempotent: a second delivery posts nothing
    assert len(api.github.reviews[(FULL_NAME, 1)]) == 1


async def test_human_approval_gate(api: ApiHarness, publishing: None) -> None:
    job_id, queue = await _analyzed(api, settings=RepositorySettings(require_human_approval=True))
    job = await _job(api, job_id)
    assert job.status == ReviewStatus.AWAITING_APPROVAL and queue == []
    org_id = job.organization_id

    # A reviewer dismisses one finding before approving: it must not be posted.
    sql = await api.db.scalar(select(ReviewFinding).where(ReviewFinding.rule_id == SQL_RULE))
    assert sql is not None
    sql.status = FindingStatus.FALSE_POSITIVE
    await api.db.flush()

    from app.models import Organization

    org = await api.db.get(Organization, org_id)
    assert org is not None
    viewer = await make_user(api.db)
    await add_member(api.db, org, viewer, OrgRole.VIEWER)
    url = f"/api/v1/organizations/{org_id}/reviews/{job_id}/publish"
    headers = await api.login(viewer)
    assert (await api.client.post(url, headers=headers)).status_code == 403

    from app.api.v1.pull_requests import get_publish_enqueuer

    approved: list[str] = []
    api.app.dependency_overrides[get_publish_enqueuer] = lambda: approved.append
    owner = await api.db.scalar(select(User).where(User.id == org.created_by_id))
    assert owner is not None
    owner_id = owner.id
    headers = await api.login(owner)
    resp = await api.client.post(url, headers=headers)
    assert resp.status_code == 202 and resp.json()["status"] == "publishing"
    assert approved == [str(job_id)]
    assert (await api.client.post(url, headers=headers)).status_code == 409  # already publishing

    assert await _publish(api, job_id) == "published"
    [review] = api.github.reviews[(FULL_NAME, 1)]
    assert [c["line"] for c in review["comments"]] == [3]  # the false positive was not posted
    assert (await _job(api, job_id)).approved_by_id == owner_id


async def test_rejected_positions_fall_back_to_summary_only(
    api: ApiHarness, publishing: None
) -> None:
    job_id, _ = await _analyzed(api)
    api.github.reject_inline_comments = True
    assert await _publish(api, job_id) == "published"
    [review] = api.github.reviews[(FULL_NAME, 1)]
    assert review["comments"] == []
    assert "Findings not posted inline" in review["body"] and "app/users.py:4" in review["body"]
    job = await _job(api, job_id)
    assert job.stats["publication"]["inline_fallback"] is True
    inline = [r for r in await _comments(api, job_id) if r.kind.value == "inline"]
    assert inline and all(r.status == CommentStatus.SKIPPED for r in inline)


async def test_stale_review_is_not_posted(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(api)
    api.github.pulls[(FULL_NAME, 1)]["head"]["sha"] = SHA_2  # pushed meanwhile
    assert await _publish(api, job_id) == "skipped"
    job = await _job(api, job_id)
    assert (job.status, job.status_reason) == (ReviewStatus.COMPLETED, "publish_head_moved")
    assert api.github.reviews == {}


async def test_approval_rejected_when_pr_moved(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(api, settings=RepositorySettings(require_human_approval=True))
    job = await _job(api, job_id)
    from app.models import PullRequest

    await api.db.execute(update(PullRequest).values(head_sha=SHA_2))
    owner = await api.db.scalar(select(User))
    assert owner is not None
    headers = await api.login(owner)
    resp = await api.client.post(
        f"/api/v1/organizations/{job.organization_id}/reviews/{job_id}/publish", headers=headers
    )
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "review_stale"


async def test_re_review_does_not_repeat_findings_and_supersedes_summary(
    api: ApiHarness, publishing: None
) -> None:
    first, _ = await _analyzed(api)
    await _publish(api, first)
    second, _ = await _analyzed(api, sha=SHA_2, deliver_action="synchronize")
    assert await _publish(api, second) == "published"

    old, new = api.github.reviews[(FULL_NAME, 1)]
    assert new["comments"] == []  # same findings, already posted on this PR
    assert "2 issues from an earlier review" in new["body"]
    assert old["body"].startswith("> ⚠️ **Superseded**") and SHA_2[:7] in old["body"]
    pub = (await _job(api, second)).stats["publication"]
    assert (pub["already_reported"], pub["superseded_reviews"]) == (2, 1)


async def test_publish_failure_then_retry(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(api)
    api.github.inject["/reviews"] = [
        httpx.Response(403, json={"message": "Resource not accessible by integration"})
    ]
    assert await _publish(api, job_id) == "failed"
    job = await _job(api, job_id)
    assert (job.status, job.status_reason) == (ReviewStatus.FAILED, "publish_failed")
    assert "not accessible" in (job.error_message or "")

    owner = await api.db.scalar(select(User))
    assert owner is not None
    from app.api.v1.pull_requests import get_publish_enqueuer

    api.app.dependency_overrides[get_publish_enqueuer] = lambda: lambda _id: None
    headers = await api.login(owner)
    resp = await api.client.post(
        f"/api/v1/organizations/{job.organization_id}/reviews/{job_id}/publish", headers=headers
    )
    assert resp.status_code == 202
    assert await _publish(api, job_id) == "published"


async def test_transient_github_errors_are_retried(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(api)
    api.github.inject["/reviews"] = [httpx.Response(502)] * 8
    assert await _publish(api, job_id) == "retry"
    assert (await _job(api, job_id)).status == ReviewStatus.PUBLISHING


async def test_severity_threshold_limits_what_is_posted(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(
        api, settings=RepositorySettings(min_severity_to_publish=Severity.HIGH)
    )
    await _publish(api, job_id)
    [review] = api.github.reviews[(FULL_NAME, 1)]
    assert [c["line"] for c in review["comments"]] == [4]  # medium finding on line 3 held back
    assert "Medium" not in review["body"]


async def test_publishing_disabled_by_repository_settings(
    api: ApiHarness, publishing: None
) -> None:
    job_id, queue = await _analyzed(
        api, settings=RepositorySettings(publish_inline_comments=False, publish_summary=False)
    )
    job = await _job(api, job_id)
    assert job.status == ReviewStatus.COMPLETED and queue == []
    assert job.stats["publication"] == {"status": "disabled", "reason": "repository_settings"}


async def test_sweep_requeues_stuck_publications(api: ApiHarness, publishing: None) -> None:
    job_id, _ = await _analyzed(api)
    now = datetime.now(UTC)
    await api.db.execute(
        update(ReviewJob)
        .where(ReviewJob.id == job_id)
        .values(updated_at=now - timedelta(minutes=30))
    )
    result = await MaintenanceService(api.db).sweep(now)
    assert result.publications_to_requeue == [job_id]


def test_publish_task_retries_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.publisher import PublishOutcome
    from app.workers.tasks import github as github_task

    calls: list[bool] = []

    async def fake(job_id: uuid.UUID, final_attempt: bool) -> PublishOutcome:
        calls.append(final_attempt)
        return PublishOutcome("failed" if final_attempt else "retry")

    monkeypatch.setattr(github_task, "_publish", fake)
    result = github_task.publish_review.apply(args=[str(uuid.uuid4())])
    assert result.get() == "failed"
    assert calls == [False] * github_task.MAX_RETRIES + [True]
