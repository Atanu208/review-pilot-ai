"""Organizations, members and invitations."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import (
    AdminMember,
    AppSettings,
    CurrentUser,
    DbSession,
    Meta,
    ViewerMember,
)
from app.models.enums import OrgRole
from app.repositories.organizations import OrganizationRepository
from app.schemas.organization import (
    InvitationAccepted,
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    MemberOut,
    MemberRoleUpdate,
    OrganizationCreate,
    OrganizationDetail,
    OrganizationOut,
)
from app.services.organizations import OrganizationService
from app.services.rate_limit import rate_limit

router = APIRouter(tags=["organizations"])


@router.post(
    "/organizations",
    response_model=OrganizationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization (caller becomes owner)",
    dependencies=[Depends(rate_limit("org_create", limit=10))],
)
async def create_organization(
    body: OrganizationCreate, user: CurrentUser, db: DbSession, settings: AppSettings, meta: Meta
) -> OrganizationOut:
    org = await OrganizationService(db, settings).create(user, body.name, meta)
    return OrganizationOut.model_validate(org)


@router.get("/organizations", response_model=list[OrganizationDetail], summary="My organizations")
async def list_organizations(
    user: CurrentUser, db: DbSession, settings: AppSettings
) -> list[OrganizationDetail]:
    service = OrganizationService(db, settings)
    result = []
    for m in await OrganizationRepository(db).list_memberships_for_user(user.id):
        members, installs, repos = await service.counts(m.organization_id)
        result.append(
            OrganizationDetail(
                id=m.organization.id,
                name=m.organization.name,
                slug=m.organization.slug,
                created_at=m.organization.created_at,
                role=m.role,
                member_count=members,
                installation_count=installs,
                connected_repository_count=repos,
            )
        )
    return result


@router.get(
    "/organizations/{org_id}", response_model=OrganizationDetail, summary="Organization details"
)
async def get_organization(
    member: ViewerMember, db: DbSession, settings: AppSettings
) -> OrganizationDetail:
    org = member.organization
    members, installs, repos = await OrganizationService(db, settings).counts(org.id)
    return OrganizationDetail(
        id=org.id,
        name=org.name,
        slug=org.slug,
        created_at=org.created_at,
        role=member.role,
        member_count=members,
        installation_count=installs,
        connected_repository_count=repos,
    )


@router.get(
    "/organizations/{org_id}/members", response_model=list[MemberOut], summary="List members"
)
async def list_members(
    member: ViewerMember, db: DbSession, settings: AppSettings
) -> list[MemberOut]:
    rows = await OrganizationService(db, settings).list_members(member.organization_id)
    return [MemberOut.model_validate(r) for r in rows]


@router.patch(
    "/organizations/{org_id}/members/{member_id}",
    response_model=MemberOut,
    summary="Change a member's role",
)
async def update_member_role(
    member_id: uuid.UUID,
    body: MemberRoleUpdate,
    actor: AdminMember,
    db: DbSession,
    settings: AppSettings,
    meta: Meta,
) -> MemberOut:
    updated = await OrganizationService(db, settings).update_role(actor, member_id, body.role, meta)
    return MemberOut.model_validate(updated)


@router.delete(
    "/organizations/{org_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member (or leave, when removing yourself)",
)
async def remove_member(
    member_id: uuid.UUID, actor: ViewerMember, db: DbSession, settings: AppSettings, meta: Meta
) -> Response:
    await OrganizationService(db, settings).remove_member(actor, member_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/organizations/{org_id}/invitations",
    response_model=list[InvitationOut],
    summary="Pending invitations",
)
async def list_invitations(
    actor: AdminMember, db: DbSession, settings: AppSettings
) -> list[InvitationOut]:
    rows = await OrganizationService(db, settings).list_invitations(actor.organization_id)
    await db.commit()  # persist any invitations just marked expired
    return [InvitationOut.model_validate(r) for r in rows]


@router.post(
    "/organizations/{org_id}/invitations",
    response_model=InvitationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a GitHub user",
    dependencies=[Depends(rate_limit("invite", limit=30))],
)
async def create_invitation(
    body: InvitationCreate, actor: AdminMember, db: DbSession, settings: AppSettings, meta: Meta
) -> InvitationCreated:
    invitation, token = await OrganizationService(db, settings).invite(
        actor, body.github_login, body.role, meta
    )
    return InvitationCreated(
        **InvitationOut.model_validate(invitation).model_dump(),
        invite_url=f"{settings.frontend_url}/invitations/{token}",
    )


@router.delete(
    "/organizations/{org_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an invitation",
)
async def revoke_invitation(
    invitation_id: uuid.UUID, actor: AdminMember, db: DbSession, settings: AppSettings, meta: Meta
) -> Response:
    await OrganizationService(db, settings).revoke_invitation(actor, invitation_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/invitations/{token}/accept",
    response_model=InvitationAccepted,
    summary="Accept an invitation addressed to your GitHub login",
    dependencies=[Depends(rate_limit("invite_accept", limit=20))],
)
async def accept_invitation(
    token: str, user: CurrentUser, db: DbSession, settings: AppSettings, meta: Meta
) -> InvitationAccepted:
    member = await OrganizationService(db, settings).accept_invitation(user, token[:128], meta)
    return InvitationAccepted(organization_id=member.organization_id, role=OrgRole(member.role))
