"""GitHub sign-in, server-side sessions and GitHub user-token lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, UnauthorizedError
from app.core.logging import get_logger
from app.integrations.github.oauth import GitHubOAuthClient, GitHubOAuthError, OAuthTokens
from app.models import User, UserSession
from app.repositories.users import SessionRepository, UserRepository
from app.security.encryption import EncryptionError, decrypt, encrypt
from app.security.tokens import (
    InvalidStateError,
    hash_token,
    new_token,
    safe_redirect_path,
    sign_state,
    verify_state,
)
from app.services.audit import AuditService, RequestMeta

logger = get_logger(__name__)
OAUTH_STATE_PURPOSE = "oauth_login"


class OAuthFlowError(AppError):
    code = "oauth_failed"


class GitHubReauthRequired(AppError):
    status_code = 401
    code = "github_reauth_required"


@dataclass(slots=True, frozen=True)
class LoginStart:
    redirect_url: str
    state_cookie: str


@dataclass(slots=True, frozen=True)
class LoginResult:
    session_token: str
    redirect_path: str
    user: User


class AuthService:
    def __init__(
        self, session: AsyncSession, settings: Settings, oauth: GitHubOAuthClient | None = None
    ) -> None:
        self._session = session
        self._settings = settings
        self._oauth = oauth
        self._users = UserRepository(session)
        self._sessions = SessionRepository(session)
        self._audit = AuditService(session)

    @property
    def oauth(self) -> GitHubOAuthClient:
        if self._oauth is None:
            raise RuntimeError("OAuth client not provided")
        return self._oauth

    # ---- Login flow ------------------------------------------------------------------

    def start_login(self, next_path: str | None) -> LoginStart:
        nonce = new_token()
        state = sign_state(
            OAUTH_STATE_PURPOSE,
            {"n": nonce, "next": safe_redirect_path(next_path)},
            self._settings.oauth_state_ttl_seconds,
        )
        # The cookie binds the state to this browser (login-CSRF protection).
        return LoginStart(redirect_url=self.oauth.authorize_url(state), state_cookie=nonce)

    async def complete_login(
        self, *, code: str, state: str, state_cookie: str | None, meta: RequestMeta
    ) -> LoginResult:
        try:
            data = verify_state(state, OAUTH_STATE_PURPOSE)
        except InvalidStateError as exc:
            raise OAuthFlowError(
                "Sign-in link is invalid or expired", code="invalid_state"
            ) from exc
        if not state_cookie or state_cookie != data.get("n"):
            raise OAuthFlowError("Sign-in session mismatch", code="invalid_state")

        try:
            tokens = await self.oauth.exchange_code(code)
            gh_user = await self.oauth.get_user(tokens.access_token)
        except GitHubOAuthError as exc:
            raise OAuthFlowError("GitHub rejected the sign-in", code="github_denied") from exc

        user = await self._users.get_by_github_id(gh_user.id)
        if user is None:
            user = User(github_id=gh_user.id, login=gh_user.login)
            self._session.add(user)
        elif not user.is_active:
            raise OAuthFlowError("This account is disabled", code="account_disabled")
        user.login = gh_user.login
        user.name = gh_user.name
        user.email = gh_user.email
        user.avatar_url = gh_user.avatar_url
        user.last_login_at = datetime.now(UTC)
        self._store_tokens(user, tokens)
        await self._session.flush()

        session_token = new_token()
        await self._sessions.create(
            user.id,
            hash_token(session_token),
            datetime.now(UTC) + timedelta(seconds=self._settings.session_ttl_seconds),
            meta.ip_address,
            meta.user_agent,
        )
        self._audit.record(
            "auth.login", "user", resource_id=user.id, actor_user_id=user.id, meta=meta
        )
        await self._session.commit()
        logger.info("user_logged_in", user_id=str(user.id))
        return LoginResult(
            session_token=session_token, redirect_path=str(data.get("next")), user=user
        )

    # ---- Sessions ----------------------------------------------------------------------

    async def resolve_session(self, token: str | None) -> UserSession:
        if not token or len(token) > 128:
            raise UnauthorizedError("Authentication required")
        row = await self._sessions.get_active(hash_token(token))
        if row is None or not row.user.is_active:
            raise UnauthorizedError("Session expired or invalid")
        return row

    async def logout(self, token: str, user: User, meta: RequestMeta) -> None:
        await self._sessions.revoke(hash_token(token))
        self._audit.record(
            "auth.logout", "user", resource_id=user.id, actor_user_id=user.id, meta=meta
        )
        await self._session.commit()

    # ---- GitHub user token -----------------------------------------------------------

    @staticmethod
    def _store_tokens(user: User, tokens: OAuthTokens) -> None:
        user.github_token_encrypted = encrypt(tokens.access_token)
        user.github_token_expires_at = tokens.expires_at
        user.github_refresh_token_encrypted = (
            encrypt(tokens.refresh_token) if tokens.refresh_token else None
        )
        user.github_refresh_token_expires_at = tokens.refresh_token_expires_at

    async def get_user_github_token(self, user: User) -> str:
        """Return a valid user-to-server token, refreshing it if it expired."""
        now = datetime.now(UTC) + timedelta(minutes=2)
        try:
            if user.github_token_encrypted and (
                user.github_token_expires_at is None or user.github_token_expires_at > now
            ):
                return decrypt(user.github_token_encrypted)
            if user.github_refresh_token_encrypted and (
                user.github_refresh_token_expires_at is None
                or user.github_refresh_token_expires_at > now
            ):
                tokens = await self.oauth.refresh(decrypt(user.github_refresh_token_encrypted))
                self._store_tokens(user, tokens)
                await self._session.flush()
                return tokens.access_token
        except (GitHubOAuthError, EncryptionError) as exc:
            logger.warning("github_token_refresh_failed", error_type=type(exc).__name__)
        raise GitHubReauthRequired("Please sign in with GitHub again")
