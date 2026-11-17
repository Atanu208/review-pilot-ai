"""Process stored GitHub webhook deliveries."""

from __future__ import annotations

import uuid

from celery import Task

from app.core.database import session_scope
from app.core.logging import get_logger
from app.services.webhooks import WebhookProcessor
from app.workers.async_runner import run_async
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _process(row_id: uuid.UUID) -> str | None:
    async with session_scope() as session:
        status = await WebhookProcessor(session).process(row_id)
        return status.value if status else None


@celery_app.task(
    bind=True,
    name="app.workers.tasks.webhooks.process_github_webhook",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=5,
)
def process_github_webhook(self: Task, row_id: str) -> str | None:
    logger.info("webhook_task_started", webhook_row_id=row_id, attempt=self.request.retries + 1)
    return run_async(_process(uuid.UUID(row_id)))
