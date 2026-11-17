"""Run review jobs on the ``reviews`` queue."""

from __future__ import annotations

import uuid

import structlog
from celery import Task

from app.core.database import session_scope
from app.core.logging import get_logger
from app.integrations.github.factory import build_app_api
from app.services.review_runner import ReviewRunner, RunOutcome
from app.workers.async_runner import run_async
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
MAX_RETRIES = 5


async def _run(job_id: uuid.UUID, task_id: str | None, final_attempt: bool) -> RunOutcome:
    async with session_scope() as session:
        runner = ReviewRunner(session, build_app_api())
        return await runner.run(job_id, final_attempt=final_attempt, task_id=task_id)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.review.run_review",
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def run_review(self: Task, job_id: str) -> str | None:
    structlog.contextvars.bind_contextvars(review_job_id=job_id)
    final_attempt = self.request.retries >= MAX_RETRIES
    outcome = run_async(_run(uuid.UUID(job_id), self.request.id, final_attempt))
    if outcome.retry_in is not None:
        countdown = outcome.retry_in or min(300, 15 * 2**self.request.retries)
        logger.info("review_retry_scheduled", countdown=countdown, attempt=self.request.retries + 1)
        raise self.retry(countdown=countdown)
    return outcome.status.value if outcome.status else None
