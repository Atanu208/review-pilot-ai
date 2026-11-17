"""Schema, constraint and cascade tests against a real, Alembic-migrated PostgreSQL."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Base,
    GitHubInstallation,
    Organization,
    OrganizationMember,
    PullRequest,
    Repository,
    ReviewFinding,
    ReviewJob,
    User,
    WebhookDelivery,
)
from app.models.enums import (
    FindingCategory,
    FindingSource,
    OrgRole,
    ReviewStatus,
    ReviewTrigger,
    Severity,
)

pytestmark = pytest.mark.integration


async def _tenant_graph(session: AsyncSession) -> tuple[Organization, PullRequest]:
    suffix = uuid.uuid4().hex[:8]
    user = User(github_id=int(uuid.uuid4().int % 10**12), login=f"dev-{suffix}")
    org = Organization(name=f"Acme {suffix}", slug=f"acme-{suffix}")
    session.add_all([user, org])
    await session.flush()
    session.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER))
    inst = GitHubInstallation(
        organization_id=org.id,
        installation_id=int(uuid.uuid4().int % 10**12),
        account_id=1,
        account_login="acme",
        account_type="Organization",
    )
    session.add(inst)
    await session.flush()
    repo = Repository(
        organization_id=org.id,
        installation_id=inst.id,
        github_repo_id=int(uuid.uuid4().int % 10**12),
        full_name=f"acme/api-{suffix}",
    )
    session.add(repo)
    await session.flush()
    pr = PullRequest(
        organization_id=org.id,
        repository_id=repo.id,
        github_pr_id=1,
        number=7,
        title="Add login",
        author_login="dev",
        base_ref="main",
        head_ref="feature",
        base_sha="a" * 40,
        head_sha="b" * 40,
        html_url="https://github.com/acme/api/pull/7",
    )
    session.add(pr)
    await session.flush()
    return org, pr


def _finding(org: Organization, pr: PullRequest, job: ReviewJob, **kw: object) -> ReviewFinding:
    data: dict[str, object] = {
        "organization_id": org.id,
        "pull_request_id": pr.id,
        "review_job_id": job.id,
        "title": "Potential SQL Injection",
        "category": FindingCategory.SECURITY,
        "severity": Severity.CRITICAL,
        "confidence": Decimal("0.96"),
        "file_path": "app/repository.py",
        "start_line": 15,
        "end_line": 15,
        "description": "User input interpolated into SQL.",
        "source": FindingSource.AI,
        "fingerprint": uuid.uuid4().hex,
    }
    data.update(kw)
    return ReviewFinding(**data)


def test_migrations_at_head_and_match_models(
    alembic_config: Config, migrated_database: str
) -> None:
    # `alembic check` raises if autogenerate would produce any operations (model drift).
    command.check(alembic_config)
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    assert head is not None


async def test_all_tables_created(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert set(Base.metadata.tables) <= tables
    version = await conn.run_sync(lambda c: MigrationContext.configure(c).get_current_revision())
    assert version is not None


async def test_full_tenant_graph_persists(db_session: AsyncSession) -> None:
    org, pr = await _tenant_graph(db_session)
    job = ReviewJob(
        organization_id=org.id,
        pull_request_id=pr.id,
        head_sha=pr.head_sha,
        trigger=ReviewTrigger.WEBHOOK,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(_finding(org, pr, job))
    await db_session.commit()
    assert job.status == ReviewStatus.QUEUED
    assert job.created_at is not None and job.updated_at is not None


async def test_only_one_active_review_per_head_sha(db_session: AsyncSession) -> None:
    org, pr = await _tenant_graph(db_session)
    common = {"organization_id": org.id, "pull_request_id": pr.id, "head_sha": pr.head_sha}
    db_session.add(ReviewJob(**common, trigger=ReviewTrigger.WEBHOOK))
    await db_session.commit()

    # A finished job for the same SHA does not block a new one...
    db_session.add(ReviewJob(**common, trigger=ReviewTrigger.MANUAL, status=ReviewStatus.COMPLETED))
    await db_session.commit()

    # ...but a second active job does.
    db_session.add(ReviewJob(**common, trigger=ReviewTrigger.WEBHOOK))
    with pytest.raises(IntegrityError, match="uq_review_jobs_active_per_head_sha"):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"confidence": Decimal("1.5")}, "confidence_range"),
        ({"start_line": 10, "end_line": 5}, "line_range"),
        ({"start_line": 0, "end_line": 0}, "line_range"),
    ],
)
async def test_finding_check_constraints(
    db_session: AsyncSession, overrides: dict[str, object], constraint: str
) -> None:
    org, pr = await _tenant_graph(db_session)
    job = ReviewJob(
        organization_id=org.id,
        pull_request_id=pr.id,
        head_sha=pr.head_sha,
        trigger=ReviewTrigger.WEBHOOK,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(_finding(org, pr, job, **overrides))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()
    await db_session.rollback()


async def test_enum_values_enforced_by_database(db_session: AsyncSession) -> None:
    org, _ = await _tenant_graph(db_session)
    conn = await db_session.connection()
    from sqlalchemy import text

    with pytest.raises(IntegrityError, match="org_role"):
        await conn.execute(
            text(
                "INSERT INTO organization_members (id, organization_id, user_id, role) "
                "SELECT gen_random_uuid(), :org, id, 'superuser' FROM users LIMIT 1"
            ),
            {"org": org.id},
        )
    await db_session.rollback()


async def test_deleting_organization_cascades_tenant_data(db_session: AsyncSession) -> None:
    org, pr = await _tenant_graph(db_session)
    job = ReviewJob(
        organization_id=org.id,
        pull_request_id=pr.id,
        head_sha=pr.head_sha,
        trigger=ReviewTrigger.WEBHOOK,
    )
    db_session.add(job)
    await db_session.flush()
    db_session.add(_finding(org, pr, job))
    await db_session.commit()

    pr_id = pr.id
    await db_session.delete(org)
    await db_session.commit()
    db_session.expunge_all()

    for model, col in (
        (PullRequest, PullRequest.id),
        (ReviewJob, ReviewJob.pull_request_id),
        (ReviewFinding, ReviewFinding.pull_request_id),
    ):
        count = await db_session.scalar(select(func.count()).select_from(model).where(col == pr_id))
        assert count == 0, f"{model.__name__} rows survived organization deletion"


async def test_webhook_delivery_id_is_unique(db_session: AsyncSession) -> None:
    def delivery() -> WebhookDelivery:
        return WebhookDelivery(
            delivery_id="d-123", event="pull_request", payload_sha256="0" * 64, payload={}
        )

    db_session.add(delivery())
    await db_session.commit()
    db_session.add(delivery())
    with pytest.raises(IntegrityError, match="uq_webhook_deliveries_delivery_id"):
        await db_session.commit()
    await db_session.rollback()
