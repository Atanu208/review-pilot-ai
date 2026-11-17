"""Periodic maintenance (scheduled by Celery beat)."""

from __future__ import annotations

from app.core.database import session_scope
from app.core.logging import get_logger
from app.services.maintenance import MaintenanceService, SweepResult
from app.workers.async_runner import run_async
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _sweep() -> SweepResult:
    async with session_scope() as session:
        return await MaintenanceService(session).sweep()


@celery_app.task(name="app.workers.tasks.maintenance.sweep")
def sweep() -> dict[str, int]:
    from app.workers.tasks.github import publish_review
    from app.workers.tasks.review import run_review
    from app.workers.tasks.webhooks import process_github_webhook

    result = run_async(_sweep())
    for delivery_id in result.deliveries_to_requeue:
        process_github_webhook.delay(str(delivery_id))
    for job_id in result.jobs_to_requeue:
        run_review.delay(str(job_id))
    for job_id in result.publications_to_requeue:
        publish_review.delay(str(job_id))
    return {
        "deliveries_requeued": len(result.deliveries_to_requeue),
        "jobs_requeued": len(result.jobs_to_requeue),
        "jobs_failed": len(result.jobs_failed),
        "publications_requeued": len(result.publications_to_requeue),
    }
