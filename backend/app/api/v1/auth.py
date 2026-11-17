"""GitHub sign-in, current user and logout."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings, Auth, DbSession, Meta, get_oauth_client
from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.github.oauth import GitHubOAuthClient
from app.repositories.organizations import OrganizationRepository
from app.schemas.auth import MembershipSummary, MeResponse, UserOut
from app.security.tokens import csrf_token_for
from app.services.auth import AuthService, OAuthFlowError
from app.services.rate_limit import rate_limit

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
OAuth = Annotated[GitHubOAuthClient, Depends(get_oauth_client)]


def _frontend_redirect(settings: Settings, path: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.frontend_url}{path}", status_code=status.HTTP_302_FOUND)


def _set_cookie(
    response: Response, settings: Settings, name: str, value: str, max_age: int
) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


@router.get(
    "/github/login",
    summary="Start GitHub sign-in (browser redirect)",
    response_class=RedirectResponse,
    status_code=302,
    dependencies=[Depends(rate_limit("auth_login", limit=30))],
)
async def github_login(
    settings: AppSettings,
    db: DbSession,
    oauth: OAuth,
    next: str | None = Query(None, max_length=512),
) -> RedirectResponse:
    start = AuthService(db, settings, oauth).start_login(next)
    response = RedirectResponse(start.redirect_url, status_code=status.HTTP_302_FOUND)
    _set_cookie(
        response,
        settings,
        settings.oauth_state_cookie_name,
        start.state_cookie,
        settings.oauth_state_ttl_seconds,
    )
    return response


@router.get(
    "/github/callback",
    summary="GitHub OAuth callback (browser redirect)",
    response_class=RedirectResponse,
    status_code=302,
    dependencies=[Depends(rate_limit("auth_callback", limit=30))],
)
async def github_callback(
    request: Request,
    settings: AppSettings,
    db: DbSession,
    oauth: OAuth,
    meta: Meta,
    code: str | None = Query(None, max_length=512),
    state: str | None = Query(None, max_length=2048),
    error: str | None = Query(None, max_length=128),
) -> RedirectResponse:
    if error or not code or not state:
        return _frontend_redirect(settings, "/login?error=github_denied")
    try:
        result = await AuthService(db, settings, oauth).complete_login(
            code=code,
            state=state,
            state_cookie=request.cookies.get(settings.oauth_state_cookie_name),
            meta=meta,
        )
    except OAuthFlowError as exc:
        logger.warning("oauth_callback_failed", reason=exc.code)
        response = _frontend_redirect(settings, f"/login?error={exc.code}")
        response.delete_cookie(settings.oauth_state_cookie_name, path="/")
        return response

    response = _frontend_redirect(settings, result.redirect_path)
    response.delete_cookie(settings.oauth_state_cookie_name, path="/")
    _set_cookie(
        response,
        settings,
        settings.session_cookie_name,
        result.session_token,
        settings.session_ttl_seconds,
    )
    return response


@router.get("/me", response_model=MeResponse, summary="Current user, memberships and CSRF token")
async def me(ctx: Auth, db: DbSession) -> MeResponse:
    memberships = await OrganizationRepository(db).list_memberships_for_user(ctx.user.id)
    return MeResponse(
        user=UserOut.model_validate(ctx.user),
        organizations=[
            MembershipSummary(
                organization_id=m.organization_id,
                organization_name=m.organization.name,
                organization_slug=m.organization.slug,
                role=m.role,
            )
            for m in memberships
        ],
        csrf_token=csrf_token_for(ctx.session.token_hash),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke current session")
async def logout(ctx: Auth, db: DbSession, settings: AppSettings, meta: Meta) -> Response:
    await AuthService(db, settings).logout(ctx.token, ctx.user, meta)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response
