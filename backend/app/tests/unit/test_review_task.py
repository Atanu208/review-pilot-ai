from __future__ import annotations

import uuid

import pytest

from app.models.enums import ReviewStatus
from app.services.review_runner import RunOutcome
from app.workers.tasks import review as review_task


def test_task_retries_until_final_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def fake_run(job_id: uuid.UUID, task_id: str | None, final_attempt: bool) -> RunOutcome:
        calls.append(final_attempt)
        if final_attempt:
            return RunOutcome(status=ReviewStatus.FAILED)
        return RunOutcome(status=ReviewStatus.QUEUED, retry_in=0.0)

    monkeypatch.setattr(review_task, "_run", fake_run)
    result = review_task.run_review.apply(args=[str(uuid.uuid4())])
    assert result.get() == "failed"
    assert calls == [False] * review_task.MAX_RETRIES + [True]


def test_task_returns_terminal_status_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(job_id: uuid.UUID, task_id: str | None, final_attempt: bool) -> RunOutcome:
        return RunOutcome(status=ReviewStatus.COMPLETED)

    monkeypatch.setattr(review_task, "_run", fake_run)
    assert review_task.run_review.apply(args=[str(uuid.uuid4())]).get() == "completed"


def test_review_task_routes_to_reviews_queue() -> None:
    from app.workers.celery_app import celery_app

    route = celery_app.amqp.router.route({}, review_task.run_review.name)
    assert route["queue"].name == "reviews"
    assert "maintenance-sweep" in celery_app.conf.beat_schedule
