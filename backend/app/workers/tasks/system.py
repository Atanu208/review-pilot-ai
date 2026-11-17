"""Operational tasks (worker health checks)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.system.ping")
def ping() -> dict[str, str]:
    """Round-trip check that a worker is consuming from the broker."""
    now = datetime.now(UTC).isoformat()
    logger.info("worker_ping", at=now)
    return {"status": "pong", "at": now}
