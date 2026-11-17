from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, model_repr, str_enum
from app.models.enums import PullRequestState

if TYPE_CHECKING:
    from app.models.repository import Repository
    from app.models.review import ReviewJob


class PullRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        Index("uq_pull_requests_repository_number", "repository_id", "number", unique=True),
        Index(None, "organization_id", "updated_at"),
    )

    # organization_id is intentionally denormalised from repository: every tenant-scoped
    # query filters on it directly, which keeps isolation checks simple and index-backed.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    github_pr_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    author_login: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    author_avatar_url: Mapped[str | None] = mapped_column(String(1024))
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    head_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[PullRequestState] = mapped_column(
        str_enum(PullRequestState, "pull_request_state"),
        default=PullRequestState.OPEN,
        nullable=False,
    )
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    html_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="pull_requests")
    review_jobs: Mapped[list[ReviewJob]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        order_by="ReviewJob.created_at.desc()",
    )

    def __repr__(self) -> str:
        return model_repr(self, "number", "head_sha")
