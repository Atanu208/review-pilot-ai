"""Findings produced by reviews (organization-scoped)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, DeveloperMember, Meta, ViewerMember
from app.models.enums import FindingCategory, FindingSource, FindingStatus, Severity
from app.schemas.common import Page, PageParams, page_params
from app.schemas.finding import FeedbackIn, FindingOut, FindingStatusUpdate
from app.services.findings import FeedbackService, FindingService
from app.services.reviews import ReviewService

router = APIRouter(prefix="/organizations/{org_id}", tags=["findings"])


@router.get(
    "/reviews/{review_id}/findings",
    response_model=list[FindingOut],
    summary="Findings of one review, most severe first",
)
async def review_findings(
    review_id: uuid.UUID, member: ViewerMember, db: DbSession
) -> list[FindingOut]:
    await ReviewService(db).get_job(member.organization_id, review_id)  # 404 if foreign
    rows = await FindingService(db).for_review(member.organization_id, review_id)
    return [FindingOut.model_validate(r) for r in rows]


@router.get("/findings", response_model=Page[FindingOut], summary="Search findings")
async def list_findings(
    member: ViewerMember,
    db: DbSession,
    page: Annotated[PageParams, Depends(page_params)],
    severity: Severity | None = Query(None),
    category: FindingCategory | None = Query(None),
    status: FindingStatus | None = Query(None),
    source: FindingSource | None = Query(None),
    repository_id: uuid.UUID | None = Query(None),
    pull_request_id: uuid.UUID | None = Query(None),
) -> Page[FindingOut]:
    rows, total = await FindingService(db).search(
        member.organization_id,
        severity=severity,
        category=category,
        status=status,
        source=source,
        repository_id=repository_id,
        pull_request_id=pull_request_id,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[FindingOut.model_validate(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/findings/{finding_id}", response_model=FindingOut, summary="Finding details")
async def get_finding(finding_id: uuid.UUID, member: ViewerMember, db: DbSession) -> FindingOut:
    return FindingOut.model_validate(
        await FindingService(db).get(member.organization_id, finding_id)
    )


@router.patch(
    "/findings/{finding_id}",
    response_model=FindingOut,
    summary="Mark a finding open, resolved, ignored or false positive (developer)",
)
async def update_finding(
    finding_id: uuid.UUID,
    body: FindingStatusUpdate,
    member: DeveloperMember,
    db: DbSession,
    meta: Meta,
) -> FindingOut:
    row = await FindingService(db).set_status(member, finding_id, body.status, meta)
    return FindingOut.model_validate(row)


@router.get("/findings/{finding_id}/feedback", summary="Feedback counts and your own rating")
async def get_feedback(
    finding_id: uuid.UUID, member: ViewerMember, db: DbSession
) -> dict[str, object]:
    finding = await FindingService(db).get(member.organization_id, finding_id)
    return await FeedbackService(db).summary(finding.id, member.user_id)


@router.put(
    "/findings/{finding_id}/feedback",
    summary="Rate a finding: helpful, not_helpful or false_positive (any member)",
)
async def put_feedback(
    finding_id: uuid.UUID,
    body: FeedbackIn,
    member: ViewerMember,
    db: DbSession,
    meta: Meta,
) -> dict[str, object]:
    finding = await FindingService(db).get(member.organization_id, finding_id)
    return await FeedbackService(db).submit(member, finding, body.rating, body.comment, meta)
