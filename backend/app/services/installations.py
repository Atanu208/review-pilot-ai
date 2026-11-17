"""GitHub App installation flow: link installations to organizations securely."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, ForbiddenError, ServiceUnavailableError
from app.core.logging import get_logger
from app.integrations.github.app_api import GitHubAppAPI, InstallationInfo
from app.models import GitHubInstallation, OrganizationMember, User
from app.models.enums import OrgRole
from app.repositories.github import InstallationRepository
from app.repositories.organizations import OrganizationRepository
from app.security.tokens import InvalidStateError, new_token, sign_state, verify_state
from app.services.audit import AuditService, RequestMeta
from app.services.auth import AuthService

logger = get_logger(__name__)
INSTALL_STATE_PURPOSE = "gh_install"
INSTALL_STATE_TTL_SECONDS = 3600


class InstallationFlowError(AppError):
    code = "installation_failed"


def apply_installation_info(row: GitHubInstallation, info: InstallationInfo) -> None:
    row.account_id = info.account_id
    row.account_login = info.account_login
    row.account_type = info.account_type
    row.repository_selection = info.repository_selection
    row.permissions = info.permissions
    row.events = info.events
    row.suspended_at = (row.suspended_at or datetime.now(UTC)) if info.suspended else None
    row.deleted_at = None


class InstallationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        app_api: GitHubAppAPI,
        auth: AuthService,
    ) -> None:
        self._session = session
        self._settings = settings
        self._app_api = app_api
        self._auth = auth
        self._installs = InstallationRepository(session)
        self._orgs = OrganizationRepository(session)
        self._audit = AuditService(session)

    def install_url(self, membership: OrganizationMember) -> str:
        if not self._settings.github_app_slug:
            raise ServiceUnavailableError("GITHUB_APP_SLUG is not configured")
        state = sign_state(
            INSTALL_STATE_PURPOSE,
            {
                "org": str(membership.organization_id),
                "uid": str(membership.user_id),
                "n": new_token()[:16],
            },
            INSTALL_STATE_TTL_SECONDS,
        )
        return (
            f"{self._settings.github_web_url}/apps/{self._settings.github_app_slug}"
            f"/installations/new?state={state}"
        )

    async def complete_setup(
        self, user: User, installation_id: int, state: str | None, meta: RequestMeta
    ) -> uuid.UUID:
        """Link ``installation_id`` to the organization encoded in ``state``.

        Security: the signed state binds the flow to the user and organization that started
        it, and the user's own GitHub token must be able to see the installation; otherwise
        anyone could claim another account's installation by guessing its numeric id.
        """
        try:
            data = verify_state(state, INSTALL_STATE_PURPOSE)
        except InvalidStateError as exc:
            raise InstallationFlowError(
                "Installation link is invalid or expired", code="invalid_state"
            ) from exc
        if data.get("uid") != str(user.id):
            raise InstallationFlowError(
                "Installation was started by another user", code="invalid_state"
            )
        org_id = uuid.UUID(str(data["org"]))

        membership = await self._orgs.get_membership(org_id, user.id)
        if membership is None or not membership.role.at_least(OrgRole.ADMIN):
            raise ForbiddenError("Only organization admins can connect GitHub")

        user_token = await self._auth.get_user_github_token(user)
        visible = await self._auth.oauth.user_installation_ids(user_token)
        if installation_id not in visible:
            raise ForbiddenError(
                "You do not have access to this GitHub installation",
                code="installation_not_accessible",
            )

        info = await self._app_api.get_installation(installation_id)
        row = await self._installs.get_by_installation_id(installation_id)
        if row is None:
            row = GitHubInstallation(
                installation_id=installation_id,
                account_id=info.account_id,
                account_login=info.account_login,
                account_type=info.account_type,
            )
            self._session.add(row)
        elif row.organization_id not in (None, org_id) and row.deleted_at is None:
            raise ConflictError(
                "This GitHub installation is linked to another organization",
                code="installation_linked_elsewhere",
            )
        apply_installation_info(row, info)
        row.organization_id = org_id
        await self._session.flush()
        self._audit.record(
            "github.installation_link",
            "github_installation",
            resource_id=row.id,
            organization_id=org_id,
            actor_user_id=user.id,
            meta=meta,
            details={"installation_id": installation_id, "account": info.account_login},
        )
        await self._session.commit()
        logger.info("installation_linked", installation_id=installation_id, org_id=str(org_id))
        return org_id

    async def list_for_org(self, org_id: uuid.UUID) -> list[GitHubInstallation]:
        return await self._installs.list_active_for_org(org_id)
