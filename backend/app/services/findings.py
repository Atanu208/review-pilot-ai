"""Querying findings and changing their triage status."""

from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import FindingFeedback, OrganizationMember, PullRequest, ReviewFinding
from app.models.enums import (
    FeedbackRating,
    FindingCategory,
    FindingSource,
    FindingStatus,
    Severity,
)
from app.services.audit import AuditService, RequestMeta

# Most severe first; used for ORDER BY so the API returns findings in triage order.
SEVERITY_ORDER = case({s.value: -s.rank for s in Severity}, value=ReviewFinding.severity, else_=0)


class FindingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_review(self, org_id: uuid.UUID, review_id: uuid.UUID) -> list[ReviewFinding]:
        return list(
            await self._session.scalars(
                select(ReviewFinding)
                .where(
                    ReviewFinding.organization_id == org_id,
                    ReviewFinding.review_job_id == review_id,
                )
                .order_by(SEVERITY_ORDER, ReviewFinding.file_path, ReviewFinding.start_line)
            )
        )

    async def search(
        self,
        org_id: uuid.UUID,
        *,
        severity: Severity | None = None,
        category: FindingCategory | None = None,
        status: FindingStatus | None = None,
        source: FindingSource | None = None,
        repository_id: uuid.UUID | None = None,
        pull_request_id: uuid.UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[ReviewFinding], int]:
        conditions = [ReviewFinding.organization_id == org_id]
        if severity is not None:
            conditions.append(ReviewFinding.severity == severity)
        if category is not None:
            conditions.append(ReviewFinding.category == category)
        if status is not None:
            conditions.append(ReviewFinding.status == status)
        if source is not None:
            conditions.append(ReviewFinding.source == source)
        if pull_request_id is not None:
            conditions.append(ReviewFinding.pull_request_id == pull_request_id)
        base = select(ReviewFinding).where(*conditions)
        if repository_id is not None:
            base = base.join(PullRequest, PullRequest.id == ReviewFinding.pull_request_id).where(
                PullRequest.repository_id == repository_id
            )
        total = await self._session.scalar(select(func.count()).select_from(base.subquery()))
        rows = await self._session.scalars(
            base.order_by(SEVERITY_ORDER, ReviewFinding.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def get(self, org_id: uuid.UUID, finding_id: uuid.UUID) -> ReviewFinding:
        row = await self._session.scalar(
            select(ReviewFinding).where(
                ReviewFinding.organization_id == org_id, ReviewFinding.id == finding_id
            )
        )
        if row is None:
            raise NotFoundError("Finding not found")
        return row

    async def set_status(
        self,
        actor: OrganizationMember,
        finding_id: uuid.UUID,
        status: FindingStatus,
        meta: RequestMeta,
    ) -> ReviewFinding:
        finding = await self.get(actor.organization_id, finding_id)
        previous = finding.status
        finding.status = status
        finding.status_changed_by_id = actor.user_id
        AuditService(self._session).record(
            "finding.status_update",
            "review_finding",
            resource_id=finding.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"from": previous.value, "to": status.value},
        )
        await self._session.commit()
        await self._session.refresh(finding)
        return finding


class FeedbackService:
    """One rating per user per finding (upsert): helpful, not helpful or false positive."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summary(self, finding_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, object]:
        rows = await self._session.execute(
            select(FindingFeedback.rating, func.count())
            .where(FindingFeedback.finding_id == finding_id)
            .group_by(FindingFeedback.rating)
        )
        counts = {r.value: 0 for r in FeedbackRating}
        for rating, n in rows:
            counts[rating.value] = n
        mine = await self._session.scalar(
            select(FindingFeedback).where(
                FindingFeedback.finding_id == finding_id, FindingFeedback.user_id == user_id
            )
        )
        return {
            "counts": counts,
            "mine": mine.rating.value if mine else None,
            "my_comment": mine.comment if mine else None,
        }

    async def submit(
        self,
        actor: OrganizationMember,
        finding: ReviewFinding,
        rating: FeedbackRating,
        comment: str | None,
        meta: RequestMeta,
    ) -> dict[str, object]:
        row = await self._session.scalar(
            select(FindingFeedback).where(
                FindingFeedback.finding_id == finding.id, FindingFeedback.user_id == actor.user_id
            )
        )
        if row is None:
            row = FindingFeedback(finding_id=finding.id, user_id=actor.user_id, rating=rating)
            self._session.add(row)
        row.rating = rating
        row.comment = comment
        AuditService(self._session).record(
            "finding.feedback",
            "review_finding",
            resource_id=finding.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"rating": rating.value},
        )
        await self._session.commit()
        return await self.summary(finding.id, actor.user_id)
