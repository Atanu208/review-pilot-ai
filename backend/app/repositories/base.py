from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Data-access object bound to one session. Never commits: services own transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
