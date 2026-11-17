"""GitHub App installation flow (browser redirects)."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse

from app.api.deps import (
    AdminMember,
    AppSettings,
    DbSession,
    Meta,
    ViewerMember,
    get_app_api,
    get_oauth_client,
)
from app.core.errors import AppError, UnauthorizedError
from app.core.logging import get_logger
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.http import GitHubError
from app.integrations.github.oauth import GitHubOAuthClient
from app.schemas.repository import InstallationOut
from app.services.auth import AuthService
from app.services.installations import InstallationService

logger = get_logger(__name__)
router = APIRouter(tags=["github"])


@router.get(
    "/organizations/{org_id}/github/install",
    response_class=RedirectResponse,
    status_code=302,
    summary="Redirect to GitHub to install the App for this organization (admin)",
)
async def install(
    member: AdminMember,
    db: DbSession,
    settings: AppSettings,
    app_api: Annotated[GitHubAppAPI, Depends(get_app_api)],
    oauth: Annotated[GitHubOAuthClient, Depends(get_oauth_client)],
) -> RedirectResponse:
    service = InstallationService(db, settings, app_api, AuthService(db, settings, oauth))
    return RedirectResponse(service.install_url(member), status_code=status.HTTP_302_FOUND)


@router.get(
    "/github/setup",
    response_class=RedirectResponse,
    status_code=302,
    summary="GitHub App post-installation Setup URL",
)
async def setup(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    meta: Meta,
    app_api: Annotated[GitHubAppAPI, Depends(get_app_api)],
    oauth: Annotated[GitHubOAuthClient, Depends(get_oauth_client)],
    installation_id: int | None = Query(None, gt=0),
    setup_action: str | None = Query(None, max_length=32),
    state: str | None = Query(None, max_length=2048),
) -> RedirectResponse:
    def to(path: str) -> RedirectResponse:
        return RedirectResponse(f"{settings.frontend_url}{path}", status_code=status.HTTP_302_FOUND)

    auth = AuthService(db, settings, oauth)
    try:
        session = await auth.resolve_session(request.cookies.get(settings.session_cookie_name))
    except UnauthorizedError:
        return to("/login?next=" + quote("/repositories"))

    if installation_id is None:
        return to("/repositories?github_error=missing_installation")
    if setup_action == "update" and not state:
        # Repository selection changed on GitHub; webhooks keep our records in sync.
        return to("/repositories?github=updated")

    service = InstallationService(db, settings, app_api, auth)
    try:
        org_id = await service.complete_setup(session.user, installation_id, state, meta)
    except AppError as exc:
        logger.warning("installation_setup_failed", reason=exc.code)
        return to(f"/repositories?github_error={exc.code}")
    except GitHubError as exc:
        logger.warning("installation_setup_github_error", error=str(exc))
        return to("/repositories?github_error=github_unavailable")
    return to(f"/repositories?github=installed&org={org_id}")


@router.get(
    "/organizations/{org_id}/github/installations",
    response_model=list[InstallationOut],
    summary="GitHub App installations linked to this organization",
)
async def list_installations(member: ViewerMember, db: DbSession) -> list[InstallationOut]:
    from app.repositories.github import InstallationRepository

    rows = await InstallationRepository(db).list_active_for_org(member.organization_id)
    return [InstallationOut.model_validate(r) for r in rows]
