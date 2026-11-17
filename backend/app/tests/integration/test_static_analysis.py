"""Static analysis inside the review pipeline, persisted findings and the findings API."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update

from app.analysis.engine import StaticAnalysisEngine
from app.models import (
    RepositorySettings,
    ReviewFinding,
    ReviewJob,
    ReviewRule,
    User,
)
from app.models.enums import FindingStatus, OrgRole, ReviewStatus, RuleEngine, Severity
from app.tests.fakes.github import FakeGitHub
from app.tests.fakes.semgrep import FakeSemgrep, match
from app.tests.integration.conftest import ApiHarness, add_member, make_org, make_user
from app.tests.integration.test_reviews import FULL_NAME, SHA_1, _deliver, _pr_event, _run, _world

pytestmark = pytest.mark.integration

SQL_RULE = "python.sql-injection.string-built-query"
VULNERABLE = (
    "import os\n"  # 1  unchanged
    "\n"  # 2
    "def get_user(cursor, uid, cache={}):\n"  # 3  changed: mutable default
    '    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")\n'  # 4  changed: SQLi
    "    return cursor.fetchone()\n"  # 5  changed
    "\n"  # 6
    "def legacy(cursor, uid):\n"  # 7  unchanged
    '    cursor.execute("SELECT 1 WHERE x = " + uid)\n'  # 8  unchanged: must NOT be reported
)


async def _job_with_files(
    api: ApiHarness, files: list[dict[str, Any]], settings: RepositorySettings | None = None
) -> ReviewJob:
    await _world(api, settings=settings)
    api.github.add_pull(FULL_NAME, 1, head_sha=SHA_1, files=files)
    await _deliver(api, _pr_event("opened"))
    job = await api.db.scalar(select(ReviewJob))
    assert job is not None
    return job


def _vulnerable_file(api: ApiHarness) -> dict[str, Any]:
    return api.github.source_file(FULL_NAME, SHA_1, "app/users.py", VULNERABLE, added={3, 4, 5})


def _engine() -> StaticAnalysisEngine:
    return StaticAnalysisEngine(
        FakeSemgrep([match(SQL_RULE, "app/users.py", 4), match(SQL_RULE, "app/users.py", 8)])
    )


async def _findings(api: ApiHarness, job_id: uuid.UUID) -> list[ReviewFinding]:
    # populate_existing refreshes just these rows (expire_all would detach `job` attributes).
    return list(
        await api.db.scalars(
            select(ReviewFinding)
            .where(ReviewFinding.review_job_id == job_id)
            .order_by(ReviewFinding.start_line)
            .execution_options(populate_existing=True)
        )
    )


async def test_pipeline_persists_normalized_findings_on_changed_lines(api: ApiHarness) -> None:
    job = await _job_with_files(api, [_vulnerable_file(api)])
    outcome, job = await _run(api, job.id, engine=_engine())
    assert outcome.status == ReviewStatus.COMPLETED

    findings = await _findings(api, job.id)
    assert [(f.rule_id, f.start_line, f.severity) for f in findings] == [
        ("tree-sitter.python.mutable-default-argument", 3, Severity.MEDIUM),
        (SQL_RULE, 4, Severity.CRITICAL),
    ]
    sql = findings[1]
    assert sql.source.value == "semgrep" and sql.category.value == "security"
    assert sql.evidence == '    cursor.execute(f"SELECT * FROM users WHERE id = {uid}")'
    assert sql.file_path == "app/users.py" and float(sql.confidence) == 0.9
    assert len(sql.fingerprint) == 64 and sql.status == FindingStatus.OPEN

    assert job.summary is not None
    assert job.summary.startswith("Analyzed 1 file: 2 issues found (1 critical, 1 medium).")
    assert job.stats["findings"]["by_source"] == {"tree_sitter": 1, "semgrep": 1}
    assert job.stats["semgrep"]["raw_matches"] == 2  # the unchanged-line match was dropped


async def test_review_rules_disable_and_override_severity(api: ApiHarness) -> None:
    job = await _job_with_files(api, [_vulnerable_file(api)])
    api.db.add_all(
        [
            ReviewRule(
                organization_id=job.organization_id,
                rule_key="tree-sitter.python.mutable-default-argument",
                engine=RuleEngine.TREE_SITTER,
                enabled=False,
            ),
            ReviewRule(
                organization_id=job.organization_id,
                rule_key=SQL_RULE,
                engine=RuleEngine.SEMGREP,
                enabled=True,
                severity_override=Severity.HIGH,
            ),
        ]
    )
    await api.db.flush()
    _, job = await _run(api, job.id, engine=_engine())
    findings = await _findings(api, job.id)
    assert [(f.rule_id, f.severity) for f in findings] == [(SQL_RULE, Severity.HIGH)]
    assert job.stats["findings"]["suppressed_by_rules"] == 1


async def test_semgrep_unavailable_degrades_but_review_completes(api: ApiHarness) -> None:
    job = await _job_with_files(api, [_vulnerable_file(api)])
    _, job = await _run(api, job.id, engine=StaticAnalysisEngine(FakeSemgrep(status="unavailable")))
    assert job.status == ReviewStatus.COMPLETED
    stage = next(t for t in job.timeline if t["stage"] == "semgrep")
    assert stage["status"] == "degraded" and "not installed" in stage["detail"]
    assert [f.source.value for f in await _findings(api, job.id)] == ["tree_sitter"]


async def test_unsupported_and_disabled_languages_are_skipped(api: ApiHarness) -> None:
    files = [
        api.github.source_file(FULL_NAME, SHA_1, "docs/guide.md", "# Guide\n"),
        api.github.source_file(FULL_NAME, SHA_1, "web/app.js", "let x = 1;\n"),
    ]
    job = await _job_with_files(
        api, files, settings=RepositorySettings(enabled_languages=["python"])
    )
    _, job = await _run(api, job.id)
    assert (job.status, job.status_reason) == (ReviewStatus.SKIPPED, "no_supported_files")
    assert job.stats["sources"]["skipped"] == {"language_disabled": 1, "unsupported_language": 1}


async def test_file_missing_at_commit_is_skipped_not_fatal(api: ApiHarness) -> None:
    files = [_vulnerable_file(api), FakeGitHub.file("app/ghost.py")]  # no content registered
    job = await _job_with_files(api, files)
    _, job = await _run(api, job.id, engine=_engine())
    assert job.status == ReviewStatus.COMPLETED
    assert job.stats["sources"]["skipped"] == {"missing_or_too_large": 1}


async def test_rerun_replaces_findings_instead_of_duplicating(api: ApiHarness) -> None:
    job = await _job_with_files(api, [_vulnerable_file(api)])
    await _run(api, job.id, engine=_engine())
    await api.db.execute(
        update(ReviewJob).where(ReviewJob.id == job.id).values(status=ReviewStatus.QUEUED)
    )
    await _run(api, job.id, engine=_engine())  # simulates a Celery redelivery
    count = await api.db.scalar(
        select(func.count()).select_from(ReviewFinding).where(ReviewFinding.review_job_id == job.id)
    )
    assert count == 2


# ---- findings API ----------------------------------------------------------------------


async def _analyzed(api: ApiHarness) -> tuple[ReviewJob, User]:
    job = await _job_with_files(api, [_vulnerable_file(api)])
    _, job = await _run(api, job.id, engine=_engine())
    owner = await api.db.scalar(select(User))
    assert owner is not None
    return job, owner


async def test_review_findings_endpoint_sorted_by_severity(api: ApiHarness) -> None:
    job, owner = await _analyzed(api)
    await api.login(owner)
    resp = await api.client.get(
        f"/api/v1/organizations/{job.organization_id}/reviews/{job.id}/findings"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [f["severity"] for f in body] == ["critical", "medium"]
    first = body[0]
    for key in (
        "title",
        "category",
        "confidence",
        "file_path",
        "start_line",
        "end_line",
        "description",
        "impact",
        "suggested_fix",
        "code_suggestion",
        "source",
        "evidence",
    ):
        assert key in first
    assert first["title"] == "Potential SQL injection"


async def test_findings_search_filters(api: ApiHarness) -> None:
    job, owner = await _analyzed(api)
    await api.login(owner)
    base = f"/api/v1/organizations/{job.organization_id}/findings"
    assert (await api.client.get(base)).json()["total"] == 2
    crit = (await api.client.get(base, params={"severity": "critical"})).json()
    assert [f["rule_id"] for f in crit["items"]] == [SQL_RULE]
    ts = (await api.client.get(base, params={"source": "tree_sitter"})).json()
    assert ts["total"] == 1
    repo_id = str((await api.db.scalar(select(ReviewJob))).pull_request_id)  # type: ignore[union-attr]
    by_pr = (await api.client.get(base, params={"pull_request_id": repo_id})).json()
    assert by_pr["total"] == 2
    assert (await api.client.get(base, params={"severity": "bogus"})).status_code == 422


async def test_finding_status_update_rbac_and_audit(api: ApiHarness) -> None:
    job, owner = await _analyzed(api)
    org_id = job.organization_id
    finding = (await _findings(api, job.id))[1]
    url = f"/api/v1/organizations/{org_id}/findings/{finding.id}"

    viewer = await make_user(api.db)
    from app.models import Organization

    org = await api.db.get(Organization, org_id)
    assert org is not None
    await add_member(api.db, org, viewer, OrgRole.VIEWER)
    headers = await api.login(viewer)
    assert (
        await api.client.patch(url, json={"status": "resolved"}, headers=headers)
    ).status_code == 403

    headers = await api.login(owner)
    resp = await api.client.patch(url, json={"status": "false_positive"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "false_positive"
    open_only = (
        await api.client.get(f"/api/v1/organizations/{org_id}/findings", params={"status": "open"})
    ).json()
    assert open_only["total"] == 1
    assert (
        await api.client.patch(url, json={"status": "deleted"}, headers=headers)
    ).status_code == 422


async def test_findings_are_tenant_isolated(api: ApiHarness) -> None:
    job, _ = await _analyzed(api)
    finding = (await _findings(api, job.id))[0]
    outsider = await make_user(api.db)
    other = await make_org(api.db, outsider)
    headers = await api.login(outsider)
    for path in (
        f"/api/v1/organizations/{job.organization_id}/findings",
        f"/api/v1/organizations/{other.id}/findings/{finding.id}",
        f"/api/v1/organizations/{other.id}/reviews/{job.id}/findings",
    ):
        assert (await api.client.get(path)).status_code == 404, path
    resp = await api.client.patch(
        f"/api/v1/organizations/{other.id}/findings/{finding.id}",
        json={"status": "ignored"},
        headers=headers,
    )
    assert resp.status_code == 404
    assert (await api.client.get(f"/api/v1/organizations/{other.id}/findings")).json()["total"] == 0
