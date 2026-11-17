"""The AI review stage inside the real review pipeline (scripted LLM, real database)."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from app.ai.providers.mock import MockProvider
from app.ai.router import LLMRouter
from app.ai.types import LLMRequest, LLMTransientError
from app.analysis.engine import StaticAnalysisEngine
from app.core.config import get_settings
from app.models import ModelUsage, Repository, ReviewFinding, ReviewJob, ReviewRule
from app.models.enums import ReviewStatus, RuleEngine
from app.services.review_runner import ReviewRunner
from app.tests.fakes.llm import noisy_reviewer
from app.tests.fakes.semgrep import FakeSemgrep, match
from app.tests.integration.conftest import ApiHarness
from app.tests.integration.test_reviews import FULL_NAME, SHA_1, _deliver, _pr_event, _world

pytestmark = pytest.mark.integration

# Same file the eval case "py-sql-injection" describes, so the scripted model "finds" it.
USERS = (
    "import sqlite3\n\n\ndef get_user(conn: sqlite3.Connection, user_id: str):\n"
    "    cursor = conn.cursor()\n"
    '    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
    "    return cursor.fetchone()\n"
)


async def _job(api: ApiHarness) -> ReviewJob:
    await _world(api)
    api.github.add_pull(
        FULL_NAME,
        1,
        head_sha=SHA_1,
        files=[api.github.source_file(FULL_NAME, SHA_1, "app/users.py", USERS)],
    )
    await _deliver(api, _pr_event("opened"))
    job = await api.db.scalar(select(ReviewJob))
    assert job is not None
    return job


async def _run(
    api: ApiHarness,
    job: ReviewJob,
    responder: Any = noisy_reviewer,
    semgrep: FakeSemgrep | None = None,
) -> ReviewJob:
    router = LLMRouter(MockProvider(responder), ["model-a", "model-b"], prompt_version="test-v")
    runner = ReviewRunner(
        api.db,
        api.app_api(),
        analysis=StaticAnalysisEngine(semgrep or FakeSemgrep()),
        router_factory=lambda repo: router,
    )
    job_id = job.id
    await runner.run(job_id, final_attempt=False, task_id="t")
    api.db.expire_all()
    refreshed = await api.db.get(ReviewJob, job_id)
    assert refreshed is not None
    return refreshed


async def _findings(api: ApiHarness, job_id: Any) -> list[ReviewFinding]:
    return list(
        await api.db.scalars(select(ReviewFinding).where(ReviewFinding.review_job_id == job_id))
    )


async def test_ai_findings_persisted_with_usage_and_summary(api: ApiHarness) -> None:
    job = await _job(api)
    job_id = job.id
    job = await _run(api, job)
    assert job.status == ReviewStatus.COMPLETED

    findings = await _findings(api, job_id)
    ai = [f for f in findings if f.source.value == "ai"]
    assert [(f.rule_id, f.start_line, f.category.value) for f in ai] == [
        ("ai.security", 6, "security")
    ]
    assert ai[0].evidence == '    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")'

    usage = list(await api.db.scalars(select(ModelUsage).where(ModelUsage.review_job_id == job_id)))
    assert usage and {u.prompt_version for u in usage} == {"test-v"}
    assert {u.stage for u in usage} >= {"review.security", "review.bugs", "validate", "summary"}
    assert all(u.organization_id == job.organization_id for u in usage)

    assert job.prompt_version is not None
    assert job.summary is not None and job.summary.endswith("Scripted summary.")
    ai_stats = job.stats["ai"]
    assert ai_stats["findings"] == 1 and ai_stats["risk_level"] == "medium"
    assert ai_stats["rejected_unknown_file"] >= 1
    assert any(t["stage"] == "ai.finding_validation" for t in job.timeline)


async def test_ai_duplicate_of_static_finding_is_dropped(api: ApiHarness) -> None:
    job = await _job(api)
    job_id = job.id
    semgrep = FakeSemgrep([match("python.sql-injection.string-built-query", "app/users.py", 6)])
    await _run(api, job, semgrep=semgrep)
    findings = await _findings(api, job_id)
    assert [f.source.value for f in findings] == ["semgrep"]


async def test_ai_reviewer_disabled_by_review_rule(api: ApiHarness) -> None:
    job = await _job(api)
    job_id = job.id
    org_id = job.organization_id
    api.db.add(
        ReviewRule(
            organization_id=org_id, rule_key="ai.security", engine=RuleEngine.AI, enabled=False
        )
    )
    await api.db.flush()
    calls: list[str] = []

    def responder(req: LLMRequest, model: str) -> Any:
        calls.append(req.stage)
        return noisy_reviewer(req, model)

    job = await _run(api, job, responder)
    assert "review.security" not in calls
    assert [f for f in await _findings(api, job_id) if f.source.value == "ai"] == []
    assert "security" not in job.stats["ai"]["reviewers_run"]


async def test_all_llm_calls_failing_degrades_but_completes(api: ApiHarness) -> None:
    job = await _job(api)
    job = await _run(api, job, lambda req, model: LLMTransientError("provider down"))
    assert job.status == ReviewStatus.COMPLETED
    stage = next(t for t in job.timeline if t["stage"] == "ai_review")
    assert stage["status"] == "degraded"
    assert job.summary == "Analyzed 1 file: no issues found."


async def test_unconfigured_provider_degrades(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _job(api)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    job_id = job.id
    runner = ReviewRunner(api.db, api.app_api(), analysis=StaticAnalysisEngine(FakeSemgrep()))
    await runner.run(job_id, final_attempt=False)
    api.db.expire_all()
    refreshed = await api.db.get(ReviewJob, job_id)
    assert refreshed is not None and refreshed.status == ReviewStatus.COMPLETED
    stage = next(t for t in refreshed.timeline if t["stage"] == "ai_review")
    assert stage["status"] == "degraded" and "not configured" in stage["detail"]


async def test_repository_model_override_is_used(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _job(api)
    repo = await api.db.scalar(select(Repository))
    assert repo is not None
    from app.models import RepositorySettings

    api.db.add(
        RepositorySettings(repository_id=repo.id, llm_provider="mock", llm_model="special-model")
    )
    await api.db.flush()
    job_id = job.id
    runner = ReviewRunner(api.db, api.app_api(), analysis=StaticAnalysisEngine(FakeSemgrep()))
    await runner.run(job_id, final_attempt=False)
    models = set(
        await api.db.scalars(select(ModelUsage.model).where(ModelUsage.review_job_id == job_id))
    )
    assert models == {"special-model"}


async def test_token_budget_and_cooldown_in_redis(clean_redis: None) -> None:
    from app.ai.rate_limit import TokenBudget
    from app.core.redis import get_redis

    budget = TokenBudget(get_redis(), tokens_per_minute=1000)
    assert await budget.try_reserve("m", 600) == 0.0
    wait = await budget.try_reserve("m", 600)  # would exceed 1000 in this window
    assert 0 < wait <= 61
    assert await budget.try_reserve("m", 300) == 0.0  # the refused reservation was released
    assert await budget.try_reserve("other", 900) == 0.0  # budgets are per model

    await budget.cooldown("m", 5)
    assert 0 < await budget.try_reserve("m", 1) <= 5

    unlimited = TokenBudget(get_redis(), tokens_per_minute=0)
    assert not unlimited.enabled and await unlimited.try_reserve("m", 10**6) == 0.0
