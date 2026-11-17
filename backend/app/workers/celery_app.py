"""Celery application. Queues: ``reviews`` (AI pipeline), ``github`` (publishing), ``default``."""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, task_postrun, task_prerun

from app.core.config import get_settings
from app.core.logging import configure_logging

# Task modules are imported lazily by the worker (avoids circular imports with this module).
TASK_MODULES = [
    "app.workers.tasks.system",
    "app.workers.tasks.webhooks",
    "app.workers.tasks.review",
    "app.workers.tasks.maintenance",
    "app.workers.tasks.github",
]


def create_celery() -> Celery:
    settings = get_settings()
    celery = Celery(
        "reviewpilot",
        broker=settings.broker_url,
        backend=settings.result_backend,
        include=TASK_MODULES,
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],  # never pickle: payloads may originate from webhooks
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,  # re-deliver if a worker dies mid-review
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,  # long AI tasks: avoid hoarding
        task_track_started=True,
        task_time_limit=15 * 60,
        task_soft_time_limit=13 * 60,
        result_expires=24 * 60 * 60,
        broker_connection_retry_on_startup=True,
        task_default_queue="default",
        task_routes={
            "app.workers.tasks.review.*": {"queue": "reviews"},
            "app.workers.tasks.github.*": {"queue": "github"},
        },
        beat_schedule={
            "maintenance-sweep": {
                "task": "app.workers.tasks.maintenance.sweep",
                "schedule": 120.0,
                "options": {"expires": 110},
            },
        },
    )
    return celery


celery_app = create_celery()


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)


@task_prerun.connect
def _bind_task_context(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    import structlog

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(task_id=task_id, task_name=getattr(task, "name", None))


@task_postrun.connect
def _clear_task_context(**_: Any) -> None:
    import structlog

    structlog.contextvars.clear_contextvars()
