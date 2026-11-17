"""Publish reviews to GitHub (``github`` queue)."""

from __future__ import annotations

import uuid

import structlog
from celery import Task

from app.core.config import get_settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.integrations.github.factory import build_app_api
from app.services.publisher import PublishOutcome, ReviewPublisher
from app.workers.async_runner import run_async
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
MAX_RETRIES = 5


async def _publish(job_id: uuid.UUID, final_attempt: bool) -> PublishOutcome:
    async with session_scope() as session:
        publisher = ReviewPublisher(session, build_app_api(), get_settings())
        return await publisher.publish(job_id, final_attempt=final_attempt)


@celery_app.task(
    bind=True,
    name="app.workers.tasks.github.publish_review",
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def publish_review(self: Task, job_id: str) -> str:
    structlog.contextvars.bind_contextvars(review_job_id=job_id)
    outcome = run_async(_publish(uuid.UUID(job_id), self.request.retries >= MAX_RETRIES))
    if outcome.status == "retry":
        raise self.retry(countdown=min(300, 15 * 2**self.request.retries))
    return outcome.status


def enqueue_publish(job_id: str) -> None:
    publish_review.delay(job_id)
