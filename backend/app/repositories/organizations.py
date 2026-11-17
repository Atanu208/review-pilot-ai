from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload

from app.models import Organization, OrganizationInvitation, OrganizationMember, User
from app.models.enums import InvitationStatus, OrgRole
from app.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository):
    async def slug_exists(self, slug: str) -> bool:
        return (
            await self.session.scalar(select(Organization.id).where(Organization.slug == slug))
        ) is not None

    async def get_membership(
        self, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMember | None:
        return await self.session.scalar(
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.organization))
            .where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
        )

    async def list_memberships_for_user(self, user_id: uuid.UUID) -> list[OrganizationMember]:
        rows = await self.session.scalars(
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.organization))
            .where(OrganizationMember.user_id == user_id)
            .order_by(OrganizationMember.created_at)
        )
        return list(rows)

    async def list_members(self, org_id: uuid.UUID) -> list[OrganizationMember]:
        rows = await self.session.scalars(
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(OrganizationMember.organization_id == org_id)
            .order_by(OrganizationMember.created_at)
        )
        return list(rows)

    async def get_member(
        self, org_id: uuid.UUID, member_id: uuid.UUID
    ) -> OrganizationMember | None:
        return await self.session.scalar(
            select(OrganizationMember)
            .options(joinedload(OrganizationMember.user))
            .where(OrganizationMember.organization_id == org_id, OrganizationMember.id == member_id)
        )

    async def count_owners(self, org_id: uuid.UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(OrganizationMember)
                .where(
                    OrganizationMember.organization_id == org_id,
                    OrganizationMember.role == OrgRole.OWNER,
                )
            )
            or 0
        )

    async def lock_owners(self, org_id: uuid.UUID) -> None:
        """Serialise concurrent owner changes (prevents removing the last owner in a race)."""
        await self.session.execute(
            select(OrganizationMember.id)
            .where(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.role == OrgRole.OWNER,
            )
            .with_for_update()
        )


class InvitationRepository(BaseRepository):
    async def list_pending(self, org_id: uuid.UUID) -> list[OrganizationInvitation]:
        rows = await self.session.scalars(
            select(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == org_id,
                OrganizationInvitation.status == InvitationStatus.PENDING,
                OrganizationInvitation.expires_at > datetime.now(UTC),
            )
            .order_by(OrganizationInvitation.created_at.desc())
        )
        return list(rows)

    async def get_pending_by_login(
        self, org_id: uuid.UUID, login: str
    ) -> OrganizationInvitation | None:
        return await self.session.scalar(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org_id,
                func.lower(OrganizationInvitation.github_login) == login.lower(),
                OrganizationInvitation.status == InvitationStatus.PENDING,
            )
        )

    async def get_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        return await self.session.scalar(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.token_hash == token_hash)
            .with_for_update()
        )

    async def get(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> OrganizationInvitation | None:
        return await self.session.scalar(
            select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == org_id,
                OrganizationInvitation.id == invitation_id,
            )
        )

    async def expire_stale(self, org_id: uuid.UUID) -> None:
        await self.session.execute(
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == org_id,
                OrganizationInvitation.status == InvitationStatus.PENDING,
                OrganizationInvitation.expires_at <= datetime.now(UTC),
            )
            .values(status=InvitationStatus.EXPIRED)
        )


__all__ = ["InvitationRepository", "OrganizationRepository", "User"]
