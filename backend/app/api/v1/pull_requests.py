"""Pull requests and their reviews (organization-scoped)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import AppSettings, DbSession, DeveloperMember, Meta, ViewerMember
from app.models.enums import PullRequestState
from app.schemas.common import Page, PageParams, page_params
from app.schemas.review import (
    PullRequestDetail,
    PullRequestOut,
    ReviewOut,
    ReviewSummaryOut,
)
from app.services.pull_requests import PullRequestService
from app.services.rate_limit import rate_limit
from app.services.reviews import Enqueue, ReviewService, default_review_enqueuer

router = APIRouter(prefix="/organizations/{org_id}", tags=["pull-requests", "reviews"])


def get_review_enqueuer() -> Enqueue:
    return default_review_enqueuer()


ReviewEnqueue = Annotated[Enqueue, Depends(get_review_enqueuer)]


def get_publish_enqueuer() -> Enqueue:
    from app.workers.tasks.github import enqueue_publish

    return enqueue_publish


PublishEnqueue = Annotated[Enqueue, Depends(get_publish_enqueuer)]
# Shared budget for actions that start (re)analysis or publication.
REVIEW_ACTION_LIMIT = Depends(
    rate_limit(
        "review_actions", lambda s: s.rate_limit_review_actions_per_minute, identity="session"
    )
)


@router.post(
    "/reviews/{review_id}/publish",
    response_model=ReviewOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Approve a review awaiting approval, or retry a failed publication (developer)",
    dependencies=[REVIEW_ACTION_LIMIT],
)
async def publish_review(
    review_id: uuid.UUID,
    member: DeveloperMember,
    db: DbSession,
    meta: Meta,
    settings: AppSettings,
    enqueue: PublishEnqueue,
) -> ReviewOut:
    job = await ReviewService(db).publish(
        member,
        review_id,
        meta,
        publish_enabled=settings.github_publish_enabled,
        publish_enqueue=enqueue,
    )
    return ReviewOut.model_validate(job)


@router.get("/pull-requests", response_model=Page[PullRequestOut], summary="List pull requests")
async def list_pull_requests(
    member: ViewerMember,
    db: DbSession,
    page: Annotated[PageParams, Depends(page_params)],
    repository_id: uuid.UUID | None = Query(None),
    state: PullRequestState | None = Query(None),
) -> Page[PullRequestOut]:
    rows, total, latest = await PullRequestService(db).list_pull_requests(
        member.organization_id,
        repository_id=repository_id,
        state=state,
        limit=page.limit,
        offset=page.offset,
    )
    items = []
    for row in rows:
        item = PullRequestOut.model_validate(row)
        job = latest.get(row.id)
        item.latest_review = ReviewSummaryOut.model_validate(job) if job else None
        items.append(item)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/pull-requests/{pr_id}", response_model=PullRequestDetail, summary="Pull request details"
)
async def get_pull_request(
    pr_id: uuid.UUID, member: ViewerMember, db: DbSession
) -> PullRequestDetail:
    service = PullRequestService(db)
    pr = await service.get(member.organization_id, pr_id)
    reviews = [ReviewSummaryOut.model_validate(j) for j in await service.reviews(pr.id)]
    return PullRequestDetail(
        **PullRequestOut.model_validate(pr).model_dump(exclude={"latest_review"}),
        latest_review=reviews[0] if reviews else None,
        reviews=reviews,
    )


@router.post(
    "/pull-requests/{pr_id}/reviews",
    response_model=ReviewOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a review of the current head commit (developer)",
    dependencies=[REVIEW_ACTION_LIMIT],
)
async def trigger_review(
    pr_id: uuid.UUID, member: DeveloperMember, db: DbSession, meta: Meta, enqueue: ReviewEnqueue
) -> ReviewOut:
    pr = await PullRequestService(db).get(member.organization_id, pr_id)
    job = await ReviewService(db, enqueue).trigger_manual(member, pr, meta)
    return ReviewOut.model_validate(job)


@router.get("/reviews/{review_id}", response_model=ReviewOut, summary="Review details and status")
async def get_review(review_id: uuid.UUID, member: ViewerMember, db: DbSession) -> ReviewOut:
    job = await ReviewService(db).get_job(member.organization_id, review_id)
    return ReviewOut.model_validate(job)


@router.post(
    "/reviews/{review_id}/retry",
    response_model=ReviewOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed, cancelled or skipped review (developer)",
    dependencies=[REVIEW_ACTION_LIMIT],
)
async def retry_review(
    review_id: uuid.UUID, member: DeveloperMember, db: DbSession, meta: Meta, enqueue: ReviewEnqueue
) -> ReviewOut:
    job = await ReviewService(db, enqueue).retry(member, review_id, meta)
    return ReviewOut.model_validate(job)


@router.post(
    "/reviews/{review_id}/cancel",
    response_model=ReviewOut,
    summary="Cancel a queued or running review (developer)",
)
async def cancel_review(
    review_id: uuid.UUID, member: DeveloperMember, db: DbSession, meta: Meta
) -> ReviewOut:
    job = await ReviewService(db).cancel(member, review_id, meta)
    return ReviewOut.model_validate(job)
