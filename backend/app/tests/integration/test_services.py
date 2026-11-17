"""Readiness against real Postgres/Redis, and Celery task execution."""

from __future__ import annotations

import httpx
import pytest

from app.core.database import dispose_engine
from app.core.redis import close_redis

pytestmark = pytest.mark.integration


async def test_readiness_with_real_dependencies(
    migrated_database: str, redis_url: str, client: httpx.AsyncClient
) -> None:
    try:
        resp = await client.get("/health/ready")
    finally:
        await close_redis()
        await dispose_engine()
    assert resp.status_code == 200, resp.text
    checks = {c["name"]: c["status"] for c in resp.json()["checks"]}
    assert checks == {"database": "ok", "redis": "ok"}


def test_celery_ping_task_roundtrip_through_redis(redis_url: str) -> None:
    """Runs a real worker thread against Redis to prove broker + result backend wiring."""
    from celery.contrib.testing.worker import start_worker

    from app.workers.celery_app import celery_app
    from app.workers.tasks.system import ping

    celery_app.conf.update(broker_url=redis_url, result_backend=redis_url)
    with start_worker(celery_app, perform_ping_check=False, queues=["default"], pool="solo"):
        result = ping.delay().get(timeout=20)
    assert result["status"] == "pong"
