from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import PullRequestState, ReviewStatus, ReviewTrigger
from app.schemas.common import ORMModel


class TimelineEntry(BaseModel):
    stage: str
    status: str
    started_at: datetime
    duration_ms: int
    detail: str | None = None


class ReviewSummaryOut(ORMModel):
    id: uuid.UUID
    status: ReviewStatus
    status_reason: str | None
    trigger: ReviewTrigger
    head_sha: str
    attempt: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None


class ReviewOut(ReviewSummaryOut):
    pull_request_id: uuid.UUID
    summary: str | None
    error_message: str | None
    stats: dict[str, Any]
    timeline: list[TimelineEntry]


class RepositoryRef(ORMModel):
    id: uuid.UUID
    full_name: str
    html_url: str | None


class PullRequestOut(ORMModel):
    id: uuid.UUID
    number: int
    title: str
    author_login: str
    author_avatar_url: str | None
    base_ref: str
    head_ref: str
    head_sha: str
    state: PullRequestState
    is_draft: bool
    html_url: str
    additions: int
    deletions: int
    changed_files: int
    opened_at: datetime | None
    updated_at: datetime
    repository: RepositoryRef
    latest_review: ReviewSummaryOut | None = None


class PullRequestDetail(PullRequestOut):
    reviews: list[ReviewSummaryOut]
