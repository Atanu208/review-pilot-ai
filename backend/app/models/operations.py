"""Operational tables: LLM usage accounting, audit trail, webhook deliveries."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, model_repr, str_enum
from app.models.enums import WebhookStatus


class ModelUsage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_usage"
    __table_args__ = (
        Index(None, "organization_id", "created_at"),
        Index(None, "provider", "model"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    review_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_jobs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(128))


class AuditLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index(None, "organization_id", "created_at"),
        Index(None, "resource_type", "resource_id"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "repository.connect"
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    def __repr__(self) -> str:
        return model_repr(self, "action", "resource_type")


class WebhookDelivery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Every GitHub webhook delivery, keyed by ``X-GitHub-Delivery`` for idempotency."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index(None, "event", "created_at"),
        # Replay detection: the same signed body under a new delivery id.
        Index(None, "payload_sha256", "created_at"),
    )

    delivery_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    installation_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    github_repo_id: Mapped[int | None] = mapped_column(BigInteger)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookStatus] = mapped_column(
        str_enum(WebhookStatus, "webhook_status"), default=WebhookStatus.RECEIVED, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return model_repr(self, "delivery_id", "event", "action")
