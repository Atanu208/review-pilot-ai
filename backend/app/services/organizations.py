"""Organizations, memberships, role management and invitations."""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models import (
    GitHubInstallation,
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    Repository,
    User,
)
from app.models.enums import InvitationStatus, OrgRole
from app.repositories.organizations import InvitationRepository, OrganizationRepository
from app.security.tokens import hash_token, new_token
from app.services.audit import AuditService, RequestMeta


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "org"


def can_manage(actor: OrgRole, target_current: OrgRole, target_new: OrgRole | None = None) -> bool:
    """Admins manage developers/viewers/admins; only owners touch owners or grant ownership."""
    if not actor.at_least(OrgRole.ADMIN):
        return False
    if actor == OrgRole.OWNER:
        return True
    if target_current == OrgRole.OWNER or target_new == OrgRole.OWNER:
        return False
    return True


class OrganizationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._orgs = OrganizationRepository(session)
        self._invites = InvitationRepository(session)
        self._audit = AuditService(session)

    # ---- Organizations ---------------------------------------------------------------

    async def create(self, user: User, name: str, meta: RequestMeta) -> Organization:
        base = slugify(name)
        slug = base
        while await self._orgs.slug_exists(slug):
            slug = f"{base[:52]}-{secrets.token_hex(3)}"
        org = Organization(name=name, slug=slug, created_by_id=user.id)
        self._session.add(org)
        await self._session.flush()
        self._session.add(
            OrganizationMember(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER)
        )
        self._audit.record(
            "organization.create",
            "organization",
            resource_id=org.id,
            organization_id=org.id,
            actor_user_id=user.id,
            meta=meta,
        )
        await self._session.commit()
        return org

    async def counts(self, org_id: uuid.UUID) -> tuple[int, int, int]:
        members = await self._session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == org_id)
        )
        installations = await self._session.scalar(
            select(func.count())
            .select_from(GitHubInstallation)
            .where(
                GitHubInstallation.organization_id == org_id,
                GitHubInstallation.deleted_at.is_(None),
            )
        )
        repos = await self._session.scalar(
            select(func.count())
            .select_from(Repository)
            .where(Repository.organization_id == org_id, Repository.is_connected.is_(True))
        )
        return int(members or 0), int(installations or 0), int(repos or 0)

    # ---- Members ------------------------------------------------------------------------

    async def list_members(self, org_id: uuid.UUID) -> list[OrganizationMember]:
        return await self._orgs.list_members(org_id)

    async def update_role(
        self, actor: OrganizationMember, member_id: uuid.UUID, new_role: OrgRole, meta: RequestMeta
    ) -> OrganizationMember:
        member = await self._orgs.get_member(actor.organization_id, member_id)
        if member is None:
            raise NotFoundError("Member not found")
        if member.role == new_role:
            return member
        if not can_manage(actor.role, member.role, new_role):
            raise ForbiddenError("You cannot change this member's role")
        if member.role == OrgRole.OWNER:
            await self._ensure_not_last_owner(actor.organization_id)
        old_role = member.role
        member.role = new_role
        self._audit.record(
            "member.role_update",
            "organization_member",
            resource_id=member.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"from": old_role.value, "to": new_role.value, "user_id": str(member.user_id)},
        )
        await self._session.commit()
        return member

    async def remove_member(
        self, actor: OrganizationMember, member_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        member = await self._orgs.get_member(actor.organization_id, member_id)
        if member is None:
            raise NotFoundError("Member not found")
        is_self = member.user_id == actor.user_id
        if not is_self and not can_manage(actor.role, member.role):
            raise ForbiddenError("You cannot remove this member")
        if member.role == OrgRole.OWNER:
            await self._ensure_not_last_owner(actor.organization_id)
        await self._session.delete(member)
        self._audit.record(
            "member.remove",
            "organization_member",
            resource_id=member.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"user_id": str(member.user_id), "self": is_self},
        )
        await self._session.commit()

    async def _ensure_not_last_owner(self, org_id: uuid.UUID) -> None:
        await self._orgs.lock_owners(org_id)
        if await self._orgs.count_owners(org_id) <= 1:
            raise ConflictError("An organization must keep at least one owner", code="last_owner")

    # ---- Invitations ------------------------------------------------------------------

    async def list_invitations(self, org_id: uuid.UUID) -> list[OrganizationInvitation]:
        await self._invites.expire_stale(org_id)
        return await self._invites.list_pending(org_id)

    async def invite(
        self, actor: OrganizationMember, github_login: str, role: OrgRole, meta: RequestMeta
    ) -> tuple[OrganizationInvitation, str]:
        if not can_manage(actor.role, role, role):
            raise ForbiddenError("You cannot invite members with this role")
        org_id = actor.organization_id
        await self._invites.expire_stale(org_id)
        existing_members = await self._orgs.list_members(org_id)
        if any(m.user.login.lower() == github_login.lower() for m in existing_members):
            raise ConflictError("This user is already a member", code="already_member")
        if await self._invites.get_pending_by_login(org_id, github_login):
            raise ConflictError(
                "An invitation is already pending for this user", code="already_invited"
            )

        token = new_token()
        invitation = OrganizationInvitation(
            organization_id=org_id,
            github_login=github_login,
            role=role,
            token_hash=hash_token(token),
            invited_by_id=actor.user_id,
            expires_at=datetime.now(UTC) + timedelta(days=self._settings.invitation_ttl_days),
        )
        self._session.add(invitation)
        await self._session.flush()
        self._audit.record(
            "invitation.create",
            "organization_invitation",
            resource_id=invitation.id,
            organization_id=org_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"github_login": github_login, "role": role.value},
        )
        await self._session.commit()
        return invitation, token

    async def revoke_invitation(
        self, actor: OrganizationMember, invitation_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        invitation = await self._invites.get(actor.organization_id, invitation_id)
        if invitation is None or invitation.status != InvitationStatus.PENDING:
            raise NotFoundError("Invitation not found")
        if not can_manage(actor.role, invitation.role, invitation.role):
            raise ForbiddenError("You cannot revoke this invitation")
        invitation.status = InvitationStatus.REVOKED
        self._audit.record(
            "invitation.revoke",
            "organization_invitation",
            resource_id=invitation.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
        )
        await self._session.commit()

    async def accept_invitation(
        self, user: User, token: str, meta: RequestMeta
    ) -> OrganizationMember:
        invitation = await self._invites.get_by_token_hash(hash_token(token))
        # Same error for unknown/expired/foreign invitations: no information leak.
        if (
            invitation is None
            or invitation.status != InvitationStatus.PENDING
            or invitation.expires_at <= datetime.now(UTC)
            or invitation.github_login.lower() != user.login.lower()
        ):
            raise NotFoundError("Invitation not found or no longer valid")
        if await self._orgs.get_membership(invitation.organization_id, user.id):
            invitation.status = InvitationStatus.ACCEPTED
            await self._session.commit()
            raise ConflictError(
                "You are already a member of this organization", code="already_member"
            )

        member = OrganizationMember(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
            invited_by_id=invitation.invited_by_id,
        )
        self._session.add(member)
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_at = datetime.now(UTC)
        self._audit.record(
            "invitation.accept",
            "organization_invitation",
            resource_id=invitation.id,
            organization_id=invitation.organization_id,
            actor_user_id=user.id,
            meta=meta,
        )
        await self._session.commit()
        return member
