from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from app.models.enums import RuleEngine, Severity

if TYPE_CHECKING:
    from app.models.github import GitHubInstallation
    from app.models.organization import Organization
    from app.models.pull_request import PullRequest


class Repository(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (Index(None, "organization_id", "is_connected"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("github_installations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    primary_language: Mapped[str | None] = mapped_column(String(64))
    html_url: Mapped[str | None] = mapped_column(String(1024))
    is_connected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(back_populates="repositories")
    installation: Mapped[GitHubInstallation] = relationship(back_populates="repositories")
    settings: Mapped[RepositorySettings | None] = relationship(
        back_populates="repository", cascade="all, delete-orphan", uselist=False
    )
    pull_requests: Mapped[list[PullRequest]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[-1]

    def __repr__(self) -> str:
        return model_repr(self, "full_name")


class RepositorySettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "repository_settings"
    __table_args__ = (
        CheckConstraint("min_confidence >= 0 AND min_confidence <= 1", name="min_confidence_range"),
        CheckConstraint("max_files > 0", name="max_files_positive"),
        CheckConstraint("max_diff_lines > 0", name="max_diff_lines_positive"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_drafts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    require_human_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    publish_inline_comments: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    publish_summary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_confidence: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), default=Decimal("0.700"), nullable=False
    )
    min_severity_to_publish: Mapped[Severity] = mapped_column(
        str_enum(Severity, "severity"), default=Severity.LOW, nullable=False
    )
    max_files: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    max_diff_lines: Mapped[int] = mapped_column(Integer, default=3000, nullable=False)
    enabled_languages: Mapped[list[str]] = mapped_column(
        JSONB, default=lambda: ["python", "java", "javascript", "typescript"], nullable=False
    )
    ignored_paths: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    language_settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    llm_provider: Mapped[str | None] = mapped_column(String(32))
    llm_model: Mapped[str | None] = mapped_column(String(128))

    repository: Mapped[Repository] = relationship(back_populates="settings")


class ReviewRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rule enable/disable + severity override. ``repository_id`` NULL = organization-wide."""

    __tablename__ = "review_rules"
    __table_args__ = (
        UniqueConstraint("organization_id", "repository_id", "rule_key"),
        Index(
            "uq_review_rules_org_wide_rule_key",
            "organization_id",
            "rule_key",
            unique=True,
            postgresql_where="repository_id IS NULL",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    rule_key: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[RuleEngine] = mapped_column(str_enum(RuleEngine, "rule_engine"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    severity_override: Mapped[Severity | None] = mapped_column(str_enum(Severity, "severity"))
    languages: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Custom AI rule instructions or Semgrep rule YAML (validated before use, never executed).
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
