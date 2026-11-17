from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from app.models import User, UserSession
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_github_id(self, github_id: int) -> User | None:
        return await self.session.scalar(select(User).where(User.github_id == github_id))

    async def get_by_login(self, login: str) -> User | None:
        from sqlalchemy import func

        return await self.session.scalar(
            select(User).where(func.lower(User.login) == login.lower())
        )


class SessionRepository(BaseRepository):
    async def create(
        self,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        ip: str | None,
        user_agent: str | None,
    ) -> UserSession:
        row = UserSession(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            ip_address=ip,
            user_agent=(user_agent or "")[:512] or None,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_active(self, token_hash: str) -> UserSession | None:
        return await self.session.scalar(
            select(UserSession)
            .options(joinedload(UserSession.user))
            .where(
                UserSession.token_hash == token_hash,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > datetime.now(UTC),
            )
        )

    async def revoke(self, token_hash: str) -> None:
        await self.session.execute(
            update(UserSession)
            .where(UserSession.token_hash == token_hash, UserSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
