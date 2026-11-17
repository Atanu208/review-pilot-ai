from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, model_repr, str_enum
from app.models.enums import (
    CommentKind,
    CommentStatus,
    FeedbackRating,
    FindingCategory,
    FindingSource,
    FindingStatus,
    ReviewStatus,
    ReviewTrigger,
    Severity,
)

if TYPE_CHECKING:
    from app.models.pull_request import PullRequest


class ReviewJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_jobs"
    __table_args__ = (
        Index(None, "pull_request_id", "created_at"),
        Index(None, "organization_id", "status"),
        Index(None, "organization_id", "created_at"),
        # Idempotency: at most one queued/running review per PR head commit.
        Index(
            "uq_review_jobs_active_per_head_sha",
            "pull_request_id",
            "head_sha",
            unique=True,
            postgresql_where="status IN ('queued', 'running')",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger: Mapped[ReviewTrigger] = mapped_column(
        str_enum(ReviewTrigger, "review_trigger"), nullable=False
    )
    status: Mapped[ReviewStatus] = mapped_column(
        str_enum(ReviewStatus, "review_status"), default=ReviewStatus.QUEUED, nullable=False
    )
    # Machine-readable reason for cancelled/skipped/failed, e.g. "superseded", "pr_closed".
    status_reason: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    webhook_delivery_id: Mapped[str | None] = mapped_column(String(64))
    triggered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # Aggregate counters, e.g. {"files_reviewed": 12, "findings_by_severity": {...}}.
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # Execution timeline: [{"stage": "diff_analyzer", "status": "ok", "started_at": ..., "ms": 12}]
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)

    pull_request: Mapped[PullRequest] = relationship(back_populates="review_jobs")
    findings: Mapped[list[ReviewFinding]] = relationship(
        back_populates="review_job", cascade="all, delete-orphan"
    )
    comments: Mapped[list[ReviewComment]] = relationship(
        back_populates="review_job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return model_repr(self, "status", "head_sha")


class ReviewFinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_findings"
    __table_args__ = (
        Index(None, "organization_id", "severity", "created_at"),
        Index(None, "pull_request_id", "fingerprint"),
        UniqueConstraint("review_job_id", "fingerprint"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="line_range"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    review_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[FindingCategory] = mapped_column(
        str_enum(FindingCategory, "finding_category"), nullable=False
    )
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "severity"), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str | None] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    code_suggestion: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    source: Mapped[FindingSource] = mapped_column(
        str_enum(FindingSource, "finding_source"), nullable=False
    )
    rule_id: Mapped[str | None] = mapped_column(String(255))
    # Stable hash of (file, normalised title/rule, code context) for dedupe + history comparison.
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[FindingStatus] = mapped_column(
        str_enum(FindingStatus, "finding_status"), default=FindingStatus.OPEN, nullable=False
    )
    status_changed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    review_job: Mapped[ReviewJob] = relationship(back_populates="findings")
    feedback: Mapped[list[FindingFeedback]] = relationship(
        back_populates="finding", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return model_repr(self, "severity", "file_path", "start_line")


class FindingFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "finding_feedback"
    __table_args__ = (UniqueConstraint("finding_id", "user_id"),)

    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_findings.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        str_enum(FeedbackRating, "feedback_rating"), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(Text)

    finding: Mapped[ReviewFinding] = relationship(back_populates="feedback")


class ReviewComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A comment ReviewPilot published (or attempted to publish) on GitHub."""

    __tablename__ = "review_comments"

    review_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_jobs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_findings.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[CommentKind] = mapped_column(str_enum(CommentKind, "comment_kind"), nullable=False)
    status: Mapped[CommentStatus] = mapped_column(
        str_enum(CommentStatus, "comment_status"), default=CommentStatus.PENDING, nullable=False
    )
    github_review_id: Mapped[int | None] = mapped_column(BigInteger)
    github_comment_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    path: Mapped[str | None] = mapped_column(String(1024))
    line: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(8))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    review_job: Mapped[ReviewJob] = relationship(back_populates="comments")
