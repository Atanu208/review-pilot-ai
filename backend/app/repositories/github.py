from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.models import GitHubInstallation, PullRequest, Repository
from app.repositories.base import BaseRepository


class InstallationRepository(BaseRepository):
    async def get_by_installation_id(self, installation_id: int) -> GitHubInstallation | None:
        return await self.session.scalar(
            select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
        )

    async def list_active_for_org(self, org_id: uuid.UUID) -> list[GitHubInstallation]:
        rows = await self.session.scalars(
            select(GitHubInstallation)
            .where(
                GitHubInstallation.organization_id == org_id,
                GitHubInstallation.deleted_at.is_(None),
            )
            .order_by(GitHubInstallation.created_at)
        )
        return list(rows)


class RepositoryRepository(BaseRepository):
    async def list_for_org(
        self, org_id: uuid.UUID, *, connected_only: bool, limit: int, offset: int
    ) -> tuple[list[Repository], int]:
        conditions = [Repository.organization_id == org_id]
        if connected_only:
            conditions.append(Repository.is_connected.is_(True))
        total = await self.session.scalar(
            select(func.count()).select_from(Repository).where(*conditions)
        )
        rows = await self.session.scalars(
            select(Repository)
            .options(selectinload(Repository.settings))
            .where(*conditions)
            .order_by(Repository.full_name)
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)

    async def get_for_org(self, org_id: uuid.UUID, repo_id: uuid.UUID) -> Repository | None:
        return await self.session.scalar(
            select(Repository)
            .options(selectinload(Repository.settings))
            .where(Repository.organization_id == org_id, Repository.id == repo_id)
        )

    async def get_by_github_id(self, github_repo_id: int) -> Repository | None:
        return await self.session.scalar(
            select(Repository)
            .options(selectinload(Repository.settings))
            .where(Repository.github_repo_id == github_repo_id)
        )

    async def connected_github_ids(self, org_id: uuid.UUID) -> set[int]:
        rows = await self.session.scalars(
            select(Repository.github_repo_id).where(
                Repository.organization_id == org_id, Repository.is_connected.is_(True)
            )
        )
        return set(rows)

    async def disconnect_by_github_ids(self, github_repo_ids: Sequence[int]) -> int:
        if not github_repo_ids:
            return 0
        result = await self.session.execute(
            update(Repository)
            .where(
                Repository.github_repo_id.in_(github_repo_ids), Repository.is_connected.is_(True)
            )
            .values(is_connected=False, disconnected_at=func.now())
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def disconnect_for_installation(self, installation_pk: uuid.UUID) -> int:
        result = await self.session.execute(
            update(Repository)
            .where(Repository.installation_id == installation_pk, Repository.is_connected.is_(True))
            .values(is_connected=False, disconnected_at=func.now())
        )
        return int(getattr(result, "rowcount", 0) or 0)


class PullRequestRepository(BaseRepository):
    async def get_by_number(self, repository_id: uuid.UUID, number: int) -> PullRequest | None:
        return await self.session.scalar(
            select(PullRequest).where(
                PullRequest.repository_id == repository_id, PullRequest.number == number
            )
        )
