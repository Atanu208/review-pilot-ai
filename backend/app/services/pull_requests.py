"""Pull request metadata and organization-scoped queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.errors import NotFoundError
from app.models import PullRequest, Repository, ReviewJob
from app.models.enums import PullRequestState


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def apply_pr_payload(row: PullRequest, pr: dict[str, Any]) -> None:
    """Copy GitHub's pull request representation (webhook or REST) onto ``row``."""
    state = PullRequestState.OPEN
    if pr.get("state") == "closed":
        state = (
            PullRequestState.MERGED
            if pr.get("merged") or pr.get("merged_at")
            else PullRequestState.CLOSED
        )
    user = pr.get("user") or {}
    row.github_pr_id = int(pr["id"])
    row.number = int(pr["number"])
    row.title = str(pr.get("title") or "")[:1024]
    row.author_login = str(user.get("login") or "unknown")[:100]
    row.author_avatar_url = user.get("avatar_url")
    row.base_ref = str(pr["base"]["ref"])[:255]
    row.head_ref = str(pr["head"]["ref"])[:255]
    row.base_sha = str(pr["base"]["sha"])
    row.head_sha = str(pr["head"]["sha"])
    row.state = state
    row.is_draft = bool(pr.get("draft", False))
    row.html_url = str(pr.get("html_url") or "")
    # Webhook payloads for some actions omit counters; keep previous values in that case.
    for field in ("additions", "deletions", "changed_files"):
        if pr.get(field) is not None:
            setattr(row, field, int(pr[field]))
        elif getattr(row, field) is None:
            setattr(row, field, 0)
    row.opened_at = _parse_dt(pr.get("created_at")) or row.opened_at
    row.closed_at = _parse_dt(pr.get("closed_at"))
    row.merged_at = _parse_dt(pr.get("merged_at"))


async def upsert_pull_request(
    session: AsyncSession, repo: Repository, pr: dict[str, Any]
) -> PullRequest:
    row = await session.scalar(
        select(PullRequest).where(
            PullRequest.repository_id == repo.id, PullRequest.number == int(pr["number"])
        )
    )
    if row is None:
        row = PullRequest(
            organization_id=repo.organization_id, repository_id=repo.id, number=int(pr["number"])
        )
        session.add(row)
    apply_pr_payload(row, pr)
    await session.flush()
    return row


class PullRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_pull_requests(
        self,
        org_id: uuid.UUID,
        *,
        repository_id: uuid.UUID | None,
        state: PullRequestState | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PullRequest], int, dict[uuid.UUID, ReviewJob]]:
        conditions = [PullRequest.organization_id == org_id]
        if repository_id is not None:
            conditions.append(PullRequest.repository_id == repository_id)
        if state is not None:
            conditions.append(PullRequest.state == state)
        total = await self._session.scalar(
            select(func.count()).select_from(PullRequest).where(*conditions)
        )
        rows = list(
            await self._session.scalars(
                select(PullRequest)
                .options(joinedload(PullRequest.repository))
                .where(*conditions)
                .order_by(PullRequest.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return rows, int(total or 0), await self.latest_jobs([r.id for r in rows])

    async def latest_jobs(self, pr_ids: list[uuid.UUID]) -> dict[uuid.UUID, ReviewJob]:
        if not pr_ids:
            return {}
        jobs = await self._session.scalars(
            select(ReviewJob)
            .where(ReviewJob.pull_request_id.in_(pr_ids))
            .order_by(ReviewJob.pull_request_id, ReviewJob.created_at.desc())
            .distinct(ReviewJob.pull_request_id)
        )
        return {j.pull_request_id: j for j in jobs}

    async def get(self, org_id: uuid.UUID, pr_id: uuid.UUID) -> PullRequest:
        row = await self._session.scalar(
            select(PullRequest)
            .options(joinedload(PullRequest.repository))
            .where(PullRequest.organization_id == org_id, PullRequest.id == pr_id)
        )
        if row is None:
            raise NotFoundError("Pull request not found")
        return row

    async def reviews(self, pr_id: uuid.UUID, limit: int = 20) -> list[ReviewJob]:
        return list(
            await self._session.scalars(
                select(ReviewJob)
                .where(ReviewJob.pull_request_id == pr_id)
                .order_by(ReviewJob.created_at.desc())
                .limit(limit)
            )
        )
