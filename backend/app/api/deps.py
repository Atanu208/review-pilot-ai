"""Shared FastAPI dependencies: DB session, authentication, CSRF, organization RBAC."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.errors import ForbiddenError, NotFoundError
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.factory import build_app_api, build_oauth_client
from app.integrations.github.oauth import GitHubOAuthClient
from app.models import OrganizationMember, User, UserSession
from app.models.enums import OrgRole
from app.repositories.organizations import OrganizationRepository
from app.security.tokens import verify_csrf
from app.services.audit import RequestMeta
from app.services.auth import AuthService

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_HEADER = "x-csrf-token"


def get_oauth_client() -> GitHubOAuthClient:
    return build_oauth_client()


def get_app_api() -> GitHubAppAPI:
    return build_app_api()


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta.from_request(request)


Meta = Annotated[RequestMeta, Depends(get_request_meta)]


@dataclass(slots=True)
class AuthContext:
    user: User
    session: UserSession
    token: str


async def get_auth_context(request: Request, db: DbSession, settings: AppSettings) -> AuthContext:
    """Authenticate via the session cookie and enforce CSRF on state-changing methods."""
    token = request.cookies.get(settings.session_cookie_name)
    row = await AuthService(db, settings).resolve_session(token)
    if request.method not in SAFE_METHODS and not verify_csrf(
        row.token_hash, request.headers.get(CSRF_HEADER)
    ):
        raise ForbiddenError("Missing or invalid CSRF token", code="csrf_failed")
    structlog.contextvars.bind_contextvars(user_id=str(row.user_id))
    return AuthContext(user=row.user, session=row, token=str(token))


Auth = Annotated[AuthContext, Depends(get_auth_context)]


async def get_current_user(ctx: Auth) -> User:
    return ctx.user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_org_role(
    minimum: OrgRole,
) -> Callable[..., Awaitable[OrganizationMember]]:
    """Resolve ``org_id`` from the path and require membership with at least ``minimum`` role.

    Non-members get 404 (not 403) so organization ids cannot be enumerated.
    """

    async def dependency(
        org_id: Annotated[uuid.UUID, Path()], user: CurrentUser, db: DbSession
    ) -> OrganizationMember:
        membership = await OrganizationRepository(db).get_membership(org_id, user.id)
        if membership is None:
            raise NotFoundError("Organization not found")
        if not membership.role.at_least(minimum):
            raise ForbiddenError(f"Requires the {minimum.value} role or higher")
        structlog.contextvars.bind_contextvars(organization_id=str(org_id))
        return membership

    return dependency


ViewerMember = Annotated[OrganizationMember, Depends(require_org_role(OrgRole.VIEWER))]
DeveloperMember = Annotated[OrganizationMember, Depends(require_org_role(OrgRole.DEVELOPER))]
AdminMember = Annotated[OrganizationMember, Depends(require_org_role(OrgRole.ADMIN))]
OwnerMember = Annotated[OrganizationMember, Depends(require_org_role(OrgRole.OWNER))]
