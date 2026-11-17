"""Review job lifecycle: creation, superseding, cancellation, retry and manual triggers.

State machine (Phase 3)::

    queued ──▶ running ──▶ completed
       │          ├──────▶ failed ──(retry)──▶ new job
       │          └──────▶ skipped (head moved / nothing to review)
       └──────────┴──────▶ cancelled (user, superseded, PR closed)

At most one queued/running job exists per (pull request, head SHA), enforced by a partial
unique index; creation is therefore idempotent under concurrent webhook deliveries.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.models import OrganizationMember, PullRequest, Repository, RepositorySettings, ReviewJob
from app.models.enums import (
    ACTIVE_REVIEW_STATUSES,
    PullRequestState,
    ReviewStatus,
    ReviewTrigger,
)
from app.services.audit import AuditService, RequestMeta

logger = get_logger(__name__)
Enqueue = Callable[[str], Any]

CANCELLABLE = frozenset({ReviewStatus.QUEUED, ReviewStatus.RUNNING, ReviewStatus.AWAITING_APPROVAL})
RETRYABLE = frozenset({ReviewStatus.FAILED, ReviewStatus.CANCELLED, ReviewStatus.SKIPPED})


def default_review_enqueuer() -> Enqueue:
    from app.workers.tasks.review import run_review

    return lambda job_id: run_review.delay(job_id)


def effective_settings(repo: Repository) -> RepositorySettings:
    """Repository settings, falling back to model defaults when no row exists yet."""
    if repo.settings is not None:
        return repo.settings
    defaults = RepositorySettings(repository_id=repo.id)
    for column in RepositorySettings.__table__.columns:
        if getattr(defaults, column.key) is None and column.default is not None:
            arg = column.default.arg
            setattr(defaults, column.key, arg(None) if callable(arg) else arg)
    return defaults


class InvalidReviewStateError(AppError):
    status_code = 409
    code = "invalid_review_state"


class ReviewService:
    def __init__(self, session: AsyncSession, enqueue: Enqueue | None = None) -> None:
        self._session = session
        self._enqueue = enqueue
        self._audit = AuditService(session)

    def enqueue(self, job_id: uuid.UUID) -> None:
        if self._enqueue is None:
            self._enqueue = default_review_enqueuer()
        try:
            self._enqueue(str(job_id))
        except Exception as exc:  # broker down: the maintenance reaper re-enqueues queued jobs
            logger.error(
                "review_enqueue_failed", review_job_id=str(job_id), error_type=type(exc).__name__
            )

    # ---- creation (no commit: callers own the transaction) --------------------------

    async def create_job(
        self,
        pr: PullRequest,
        trigger: ReviewTrigger,
        *,
        triggered_by_id: uuid.UUID | None = None,
        delivery_id: str | None = None,
        attempt: int = 1,
    ) -> ReviewJob | None:
        """Queue a review for the PR's current head; returns None if one is already active."""
        await self.cancel_active(pr.id, reason="superseded", keep_head_sha=pr.head_sha)
        job = ReviewJob(
            organization_id=pr.organization_id,
            pull_request_id=pr.id,
            head_sha=pr.head_sha,
            trigger=trigger,
            status=ReviewStatus.QUEUED,
            attempt=attempt,
            triggered_by_id=triggered_by_id,
            webhook_delivery_id=delivery_id,
            stats={},
            timeline=[],
        )
        try:
            async with self._session.begin_nested():
                self._session.add(job)
                await self._session.flush()
        except IntegrityError:
            logger.info("review_already_active", pull_request_id=str(pr.id), head_sha=pr.head_sha)
            return None
        logger.info("review_queued", review_job_id=str(job.id), trigger=trigger.value)
        return job

    async def cancel_active(
        self, pr_id: uuid.UUID, *, reason: str, keep_head_sha: str | None = None
    ) -> list[ReviewJob]:
        """Cancel active jobs for a PR (except those for ``keep_head_sha``)."""
        jobs = list(
            await self._session.scalars(
                select(ReviewJob)
                .where(
                    ReviewJob.pull_request_id == pr_id,
                    ReviewJob.status.in_(list(CANCELLABLE)),
                )
                .with_for_update()
            )
        )
        cancelled = []
        for job in jobs:
            if keep_head_sha is not None and job.head_sha == keep_head_sha:
                continue
            self._mark_cancelled(job, reason)
            cancelled.append(job)
        return cancelled

    @staticmethod
    def _mark_cancelled(job: ReviewJob, reason: str) -> None:
        now = datetime.now(UTC)
        job.status = ReviewStatus.CANCELLED
        job.status_reason = reason
        job.completed_at = now
        if job.started_at is not None:
            job.duration_ms = int((now - job.started_at).total_seconds() * 1000)

    # ---- webhook entry point ----------------------------------------------------------

    async def on_pull_request_event(
        self, action: str, pr: PullRequest, repo: Repository, delivery_id: str | None
    ) -> ReviewJob | None:
        settings = effective_settings(repo)
        if action == "closed" or pr.state != PullRequestState.OPEN:
            await self.cancel_active(pr.id, reason="pr_closed")
            return None
        if action == "converted_to_draft" and not settings.review_drafts:
            await self.cancel_active(pr.id, reason="converted_to_draft")
            return None
        if action not in ("opened", "synchronize", "reopened", "ready_for_review"):
            return None
        if not settings.auto_review_enabled:
            return None
        if pr.is_draft and not settings.review_drafts:
            return None
        return await self.create_job(pr, ReviewTrigger.WEBHOOK, delivery_id=delivery_id)

    # ---- user actions (commit + enqueue) ----------------------------------------------

    async def get_job(self, org_id: uuid.UUID, job_id: uuid.UUID) -> ReviewJob:
        job = await self._session.scalar(
            select(ReviewJob)
            .options(joinedload(ReviewJob.pull_request).joinedload(PullRequest.repository))
            .where(ReviewJob.organization_id == org_id, ReviewJob.id == job_id)
        )
        if job is None:
            raise NotFoundError("Review not found")
        return job

    async def trigger_manual(
        self, actor: OrganizationMember, pr: PullRequest, meta: RequestMeta
    ) -> ReviewJob:
        if pr.state != PullRequestState.OPEN:
            raise InvalidReviewStateError("Only open pull requests can be reviewed")
        if not pr.repository.is_connected:
            raise InvalidReviewStateError("Repository is disconnected")
        job = await self.create_job(pr, ReviewTrigger.MANUAL, triggered_by_id=actor.user_id)
        if job is None:
            existing = await self._active_for_head(pr)
            raise ConflictError(
                "A review for this commit is already queued or running",
                code="review_already_active",
                details={"review_id": str(existing.id) if existing else None},
            )
        self._audit.record(
            "review.trigger",
            "review_job",
            resource_id=job.id,
            organization_id=pr.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"pull_request_id": str(pr.id)},
        )
        await self._session.commit()
        self.enqueue(job.id)
        return job

    async def retry(
        self, actor: OrganizationMember, job_id: uuid.UUID, meta: RequestMeta
    ) -> ReviewJob:
        previous = await self.get_job(actor.organization_id, job_id)
        if previous.status not in RETRYABLE:
            raise InvalidReviewStateError(
                f"Reviews in status '{previous.status.value}' cannot be retried"
            )
        pr = previous.pull_request
        if pr.state != PullRequestState.OPEN or not pr.repository.is_connected:
            raise InvalidReviewStateError("Pull request is closed or repository disconnected")
        job = await self.create_job(
            pr, ReviewTrigger.RETRY, triggered_by_id=actor.user_id, attempt=previous.attempt + 1
        )
        if job is None:
            raise ConflictError(
                "A review for this commit is already active", code="review_already_active"
            )
        self._audit.record(
            "review.retry",
            "review_job",
            resource_id=job.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"previous_review_id": str(previous.id)},
        )
        await self._session.commit()
        self.enqueue(job.id)
        return job

    async def publish(
        self,
        actor: OrganizationMember,
        job_id: uuid.UUID,
        meta: RequestMeta,
        *,
        publish_enabled: bool,
        publish_enqueue: Enqueue,
    ) -> ReviewJob:
        """Approve a review awaiting approval, or retry a failed publication."""
        if not publish_enabled:
            raise InvalidReviewStateError(
                "Publishing to GitHub is disabled on this server (GITHUB_PUBLISH_ENABLED=false)"
            )
        job = await self.get_job(actor.organization_id, job_id)
        locked = await self._session.scalar(
            select(ReviewJob).where(ReviewJob.id == job.id).with_for_update()
        )
        if locked is None:
            raise RuntimeError("locked is missing")
        approving = locked.status == ReviewStatus.AWAITING_APPROVAL
        retrying = locked.status == ReviewStatus.FAILED and locked.status_reason == "publish_failed"
        if not (approving or retrying):
            raise InvalidReviewStateError(
                f"Reviews in status '{locked.status.value}' cannot be published"
            )
        pr = job.pull_request
        if pr.state != PullRequestState.OPEN or pr.head_sha != locked.head_sha:
            raise InvalidReviewStateError(
                "The pull request changed since this review ran; run a new review instead",
                code="review_stale",
            )
        locked.status = ReviewStatus.PUBLISHING
        locked.status_reason = None
        locked.error_message = None
        if approving:
            locked.approved_by_id = actor.user_id
        locked.stats = {**(locked.stats or {}), "publication": {"status": "queued"}}
        self._audit.record(
            "review.approve_publish" if approving else "review.retry_publish",
            "review_job",
            resource_id=locked.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
        )
        await self._session.commit()
        try:
            publish_enqueue(str(locked.id))
        except Exception as exc:  # the maintenance sweep re-enqueues stuck publications
            logger.error("publish_enqueue_failed", error_type=type(exc).__name__)
        return locked

    async def cancel(
        self, actor: OrganizationMember, job_id: uuid.UUID, meta: RequestMeta
    ) -> ReviewJob:
        job = await self.get_job(actor.organization_id, job_id)
        locked = await self._session.scalar(
            select(ReviewJob).where(ReviewJob.id == job.id).with_for_update()
        )
        if locked is None:
            raise RuntimeError("locked is missing")
        if locked.status not in CANCELLABLE:
            raise InvalidReviewStateError(
                f"Reviews in status '{locked.status.value}' cannot be cancelled"
            )
        self._mark_cancelled(locked, "user_cancelled")
        self._audit.record(
            "review.cancel",
            "review_job",
            resource_id=job.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
        )
        await self._session.commit()
        return locked

    async def _active_for_head(self, pr: PullRequest) -> ReviewJob | None:
        job: ReviewJob | None = await self._session.scalar(
            select(ReviewJob).where(
                ReviewJob.pull_request_id == pr.id,
                ReviewJob.head_sha == pr.head_sha,
                ReviewJob.status.in_(list(ACTIVE_REVIEW_STATUSES)),
            )
        )
        return job
