from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, model_repr

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.repository import Repository


class GitHubInstallation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A GitHub App installation on a user or org account.

    Created from the ``installation.created`` webhook; linked to a ReviewPilot organization
    once an authorised user completes the installation flow.
    """

    __tablename__ = "github_installations"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), index=True
    )
    installation_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(100), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)  # User | Organization
    repository_selection: Mapped[str | None] = mapped_column(String(16))  # all | selected
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    events: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization | None] = relationship(back_populates="installations")
    repositories: Mapped[list[Repository]] = relationship(back_populates="installation")

    def __repr__(self) -> str:
        return model_repr(self, "installation_id", "account_login")
