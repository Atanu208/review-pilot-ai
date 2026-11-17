"""Periodic self-healing: re-enqueue lost work and fail jobs orphaned by dead workers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import ReviewJob, WebhookDelivery
from app.models.enums import ReviewStatus, WebhookStatus

logger = get_logger(__name__)

# A delivery still "received" after this long was never picked up (broker outage at ingest).
STALE_DELIVERY_AFTER = timedelta(minutes=2)
# A job still "queued" after this long lost its Celery message.
STALE_QUEUED_AFTER = timedelta(minutes=10)
# Beyond Celery's hard time limit (15 min) a "running" job's worker is certainly gone.
ORPHANED_RUNNING_AFTER = timedelta(minutes=20)
BATCH = 100


@dataclass(slots=True)
class SweepResult:
    deliveries_to_requeue: list[uuid.UUID] = field(default_factory=list)
    jobs_to_requeue: list[uuid.UUID] = field(default_factory=list)
    jobs_failed: list[uuid.UUID] = field(default_factory=list)
    publications_to_requeue: list[uuid.UUID] = field(default_factory=list)


class MaintenanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sweep(self, now: datetime | None = None) -> SweepResult:
        now = now or datetime.now(UTC)
        result = SweepResult()

        result.deliveries_to_requeue = list(
            await self._session.scalars(
                select(WebhookDelivery.id)
                .where(
                    WebhookDelivery.status == WebhookStatus.RECEIVED,
                    WebhookDelivery.created_at < now - STALE_DELIVERY_AFTER,
                )
                .order_by(WebhookDelivery.created_at)
                .limit(BATCH)
            )
        )
        result.jobs_to_requeue = list(
            await self._session.scalars(
                select(ReviewJob.id)
                .where(
                    ReviewJob.status == ReviewStatus.QUEUED,
                    ReviewJob.updated_at < now - STALE_QUEUED_AFTER,
                )
                .order_by(ReviewJob.created_at)
                .limit(BATCH)
            )
        )
        # Publishing is idempotent (row-locked, status-checked), so re-enqueueing is safe.
        result.publications_to_requeue = list(
            await self._session.scalars(
                select(ReviewJob.id)
                .where(
                    ReviewJob.status == ReviewStatus.PUBLISHING,
                    ReviewJob.updated_at < now - STALE_QUEUED_AFTER,
                )
                .order_by(ReviewJob.created_at)
                .limit(BATCH)
            )
        )
        orphaned = list(
            await self._session.scalars(
                select(ReviewJob)
                .where(
                    ReviewJob.status == ReviewStatus.RUNNING,
                    ReviewJob.updated_at < now - ORPHANED_RUNNING_AFTER,
                )
                .with_for_update(skip_locked=True)
                .limit(BATCH)
            )
        )
        for job in orphaned:
            job.status = ReviewStatus.FAILED
            job.status_reason = "worker_lost"
            job.error_message = "The worker running this review stopped responding"
            job.completed_at = now
            result.jobs_failed.append(job.id)
        await self._session.commit()

        if (
            result.deliveries_to_requeue
            or result.jobs_to_requeue
            or result.jobs_failed
            or result.publications_to_requeue
        ):
            logger.warning(
                "maintenance_sweep",
                deliveries_requeued=len(result.deliveries_to_requeue),
                jobs_requeued=len(result.jobs_to_requeue),
                publications_requeued=len(result.publications_to_requeue),
                jobs_failed=len(result.jobs_failed),
            )
        return result
