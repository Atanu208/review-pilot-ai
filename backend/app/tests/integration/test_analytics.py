"""Analytics, finding feedback and AI configuration endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update

from app.models import (
    FindingFeedback,
    GitHubInstallation,
    ModelUsage,
    Organization,
    PullRequest,
    Repository,
    ReviewFinding,
    ReviewJob,
    User,
)
from app.models.enums import (
    FeedbackRating,
    FindingCategory,
    FindingSource,
    FindingStatus,
    OrgRole,
    ReviewStatus,
    ReviewTrigger,
    Severity,
)
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user

pytestmark = pytest.mark.integration


async def _seed(api: ApiHarness) -> tuple[User, Organization, list[ReviewFinding]]:
    owner = await make_user(api.db, "owner")
    org = await make_org(api.db, owner)
    inst = GitHubInstallation(
        organization_id=org.id,
        installation_id=uuid.uuid4().int % 10**9,
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    api.db.add(inst)
    await api.db.flush()
    repo = Repository(
        organization_id=org.id,
        installation_id=inst.id,
        github_repo_id=uuid.uuid4().int % 10**9,
        full_name="acme/api",
    )
    api.db.add(repo)
    await api.db.flush()
    findings: list[ReviewFinding] = []
    for i, (author, status, duration) in enumerate(
        [
            ("alice", ReviewStatus.COMPLETED, 2000),
            ("alice", ReviewStatus.COMPLETED, 4000),
            ("bob", ReviewStatus.FAILED, None),
        ]
    ):
        pr = PullRequest(
            organization_id=org.id,
            repository_id=repo.id,
            github_pr_id=i,
            number=i + 1,
            title=f"PR {i + 1}",
            author_login=author,
            base_ref="main",
            head_ref=f"f{i}",
            base_sha="a" * 40,
            head_sha=f"{i}" * 40,
            html_url="https://github.com/x",
        )
        api.db.add(pr)
        await api.db.flush()
        job = ReviewJob(
            organization_id=org.id,
            pull_request_id=pr.id,
            head_sha=pr.head_sha,
            trigger=ReviewTrigger.WEBHOOK,
            status=status,
            duration_ms=duration,
            stats={"findings": {"total": 2 if status == ReviewStatus.COMPLETED else 0}},
        )
        api.db.add(job)
        await api.db.flush()
        if status != ReviewStatus.COMPLETED:
            continue
        for sev, cat, src in [
            (Severity.CRITICAL, FindingCategory.SECURITY, FindingSource.SEMGREP),
            (Severity.MEDIUM, FindingCategory.BUG, FindingSource.AI),
        ]:
            f = ReviewFinding(
                organization_id=org.id,
                review_job_id=job.id,
                pull_request_id=pr.id,
                title=f"{sev.value} issue",
                category=cat,
                severity=sev,
                confidence=Decimal("0.9"),
                file_path="app/x.py",
                start_line=4,
                end_line=4,
                description="d",
                source=src,
                fingerprint=uuid.uuid4().hex,
            )
            api.db.add(f)
            findings.append(f)
        api.db.add_all(
            [
                ModelUsage(
                    organization_id=org.id,
                    review_job_id=job.id,
                    provider="anthropic",
                    model="claude-opus-5-5",
                    stage="review.security",
                    input_tokens=1000,
                    output_tokens=200,
                    cost_usd=Decimal("0.008"),
                    latency_ms=2000,
                ),
                ModelUsage(
                    organization_id=org.id,
                    review_job_id=job.id,
                    provider="anthropic",
                    model="claude-opus-5-5",
                    stage="summary",
                    input_tokens=500,
                    output_tokens=100,
                    cost_usd=Decimal("0.004"),
                    latency_ms=1000,
                    success=False,
                    error_type="LLMTransientError",
                ),
            ]
        )
    await api.db.flush()
    findings[1].status = FindingStatus.FALSE_POSITIVE
    api.db.add(
        FindingFeedback(finding_id=findings[0].id, user_id=owner.id, rating=FeedbackRating.HELPFUL)
    )
    await api.db.flush()
    return owner, org, findings


async def test_overview(api: ApiHarness) -> None:
    owner, org, _ = await _seed(api)
    await api.login(owner)
    data = (await api.client.get(f"/api/v1/organizations/{org.id}/analytics/overview")).json()
    assert data["connected_repositories"] == 1
    assert (data["reviews_total"], data["pull_requests_reviewed"]) == (3, 2)
    assert data["findings_total"] == 4 and data["open_findings"] == 3
    assert data["findings_by_severity"] == {
        "critical": 2,
        "high": 0,
        "medium": 2,
        "low": 0,
        "info": 0,
    }
    assert data["avg_review_duration_ms"] == 3000
    assert (
        len(data["recent_reviews"]) == 3 and data["recent_reviews"][0]["repository"] == "acme/api"
    )
    assert {f["severity"] for f in data["recent_security_findings"]} == {"critical"}


async def test_timeseries_fills_every_day(api: ApiHarness) -> None:
    owner, org, _ = await _seed(api)
    await api.login(owner)
    data = (
        await api.client.get(
            f"/api/v1/organizations/{org.id}/analytics/timeseries", params={"days": 7}
        )
    ).json()
    assert len(data["reviews"]) == 7 and len(data["findings"]) == 7
    today = data["reviews"][-1]
    assert (today["completed"], today["failed"]) == (2, 1)
    assert data["findings"][-1]["critical"] == 2


async def test_old_data_excluded_by_window(api: ApiHarness) -> None:
    owner, org, _ = await _seed(api)
    old = datetime.now(UTC) - timedelta(days=60)
    await api.db.execute(
        update(ReviewJob).where(ReviewJob.organization_id == org.id).values(created_at=old)
    )
    await api.login(owner)
    data = (
        await api.client.get(
            f"/api/v1/organizations/{org.id}/analytics/overview", params={"days": 30}
        )
    ).json()
    assert data["reviews_total"] == 0


async def test_ai_usage(api: ApiHarness) -> None:
    owner, org, _ = await _seed(api)
    await api.login(owner)
    data = (await api.client.get(f"/api/v1/organizations/{org.id}/analytics/ai-usage")).json()
    totals = data["totals"]
    assert totals["calls"] == 4 and totals["reviews"] == 2
    assert totals["cost_usd"] == pytest.approx(0.024) and totals[
        "cost_per_review_usd"
    ] == pytest.approx(0.012)
    [model] = data["models"]
    assert (
        model["model"] == "claude-opus-5-5"
        and model["success_rate"] == 0.5
        and model["avg_latency_ms"] == 1500
    )
    assert {s["stage"] for s in data["stages"]} == {"review.security", "summary"}
    assert data["per_day"][-1]["cost_usd"] == pytest.approx(0.024)


async def test_quality_and_team(api: ApiHarness) -> None:
    owner, org, _ = await _seed(api)
    await api.login(owner)
    q = (await api.client.get(f"/api/v1/organizations/{org.id}/analytics/quality")).json()
    by_source = {s["source"]: s for s in q["sources"]}
    assert (
        by_source["semgrep"]["feedback"]["helpful"] == 1
        and by_source["semgrep"]["helpful_rate"] == 1.0
    )
    assert (
        by_source["ai"]["status"]["false_positive"] == 1
        and by_source["ai"]["false_positive_rate"] == 0.5
    )
    team = (await api.client.get(f"/api/v1/organizations/{org.id}/analytics/team")).json()
    authors = {a["author"]: a for a in team["authors"]}
    assert authors["alice"] == {
        "author": "alice",
        "pull_requests": 2,
        "reviews": 2,
        "findings": 4,
        "critical_or_high": 2,
    }
    assert authors["bob"]["findings"] == 0


async def test_analytics_tenant_isolation(api: ApiHarness) -> None:
    _, org, _ = await _seed(api)
    outsider = await make_user(api.db)
    other = await make_org(api.db, outsider)
    await api.login(outsider)
    assert (
        await api.client.get(f"/api/v1/organizations/{org.id}/analytics/overview")
    ).status_code == 404
    mine = (await api.client.get(f"/api/v1/organizations/{other.id}/analytics/overview")).json()
    assert mine["reviews_total"] == 0 and mine["recent_reviews"] == []
    assert (await api.client.get(f"/api/v1/organizations/{other.id}/analytics/ai-usage")).json()[
        "totals"
    ]["calls"] == 0


async def test_feedback_upsert_and_validation(api: ApiHarness) -> None:
    _owner, org, findings = await _seed(api)
    viewer = await make_user(api.db)
    await add_member(api.db, org, viewer, OrgRole.VIEWER)
    url = f"/api/v1/organizations/{org.id}/findings/{findings[0].id}/feedback"

    headers = await api.login(viewer)
    first = await api.client.put(
        url, json={"rating": "not_helpful", "comment": "Handled upstream"}, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["counts"] == {"helpful": 1, "not_helpful": 1, "false_positive": 0}
    again = await api.client.put(url, json={"rating": "false_positive"}, headers=headers)
    assert again.json()["counts"] == {"helpful": 1, "not_helpful": 0, "false_positive": 1}
    assert again.json()["mine"] == "false_positive"
    assert (await api.client.put(url, json={"rating": "meh"}, headers=headers)).status_code == 422
    assert (await api.client.get(url)).json()["mine"] == "false_positive"

    outsider = await make_user(api.db)
    other = await make_org(api.db, outsider)
    headers = await api.login(outsider)
    resp = await api.client.put(
        f"/api/v1/organizations/{other.id}/findings/{findings[0].id}/feedback",
        json={"rating": "helpful"},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_ai_config_never_exposes_secrets(
    api: ApiHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "gsk_supersecretvalue1234567890")
    monkeypatch.setenv("OPENAI_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "qwen/qwen3.8-27b")
    get_settings.cache_clear()
    owner, org, _ = await _seed(api)
    await api.login(owner)
    resp = await api.client.get(f"/api/v1/organizations/{org.id}/ai/config")
    data = resp.json()
    assert data["provider"] == "openai" and data["configured"] is True
    assert data["primary_model"] == "openai/gpt-oss-120b" and data["fallback_models"] == [
        "qwen/qwen3.8-27b"
    ]
    assert data["prompt_version"] and data["publishing"]["enabled"] is False
    assert "gsk_" not in resp.text and "secret" not in resp.text.lower()
