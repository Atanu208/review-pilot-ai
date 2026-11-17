"""Repository connection, settings and review rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.integrations.github.app_api import GitHubAppAPI
from app.integrations.github.http import GitHubAPIError
from app.models import OrganizationMember, Repository, RepositorySettings, ReviewRule
from app.repositories.github import InstallationRepository, RepositoryRepository
from app.schemas.repository import AvailableRepository, RepositorySettingsUpdate, ReviewRuleUpsert
from app.services.audit import AuditService, RequestMeta


class RepositoryService:
    def __init__(self, session: AsyncSession, app_api: GitHubAppAPI | None = None) -> None:
        self._session = session
        self._app_api = app_api
        self._repos = RepositoryRepository(session)
        self._installs = InstallationRepository(session)
        self._audit = AuditService(session)

    @property
    def app_api(self) -> GitHubAppAPI:
        if self._app_api is None:
            raise RuntimeError("GitHub App API not provided")
        return self._app_api

    async def list_repositories(
        self, org_id: uuid.UUID, *, connected_only: bool, limit: int, offset: int
    ) -> tuple[list[Repository], int]:
        return await self._repos.list_for_org(
            org_id, connected_only=connected_only, limit=limit, offset=offset
        )

    async def get(self, org_id: uuid.UUID, repo_id: uuid.UUID) -> Repository:
        repo = await self._repos.get_for_org(org_id, repo_id)
        if repo is None:
            raise NotFoundError("Repository not found")
        return repo

    async def available(self, org_id: uuid.UUID) -> list[AvailableRepository]:
        connected = await self._repos.connected_github_ids(org_id)
        result: list[AvailableRepository] = []
        for inst in await self._installs.list_active_for_org(org_id):
            if inst.suspended_at is not None:
                continue
            for repo in await self.app_api.list_installation_repositories(inst.installation_id):
                result.append(
                    AvailableRepository(
                        github_repo_id=repo.id,
                        full_name=repo.full_name,
                        is_private=repo.private,
                        default_branch=repo.default_branch,
                        primary_language=repo.language,
                        html_url=repo.html_url,
                        installation_id=inst.installation_id,
                        connected=repo.id in connected,
                    )
                )
        return sorted(result, key=lambda r: r.full_name.lower())

    async def connect(
        self, actor: OrganizationMember, github_repo_id: int, meta: RequestMeta
    ) -> Repository:
        org_id = actor.organization_id
        # Verify through GitHub that one of *this org's* installations can access the repo.
        found = None
        for inst in await self._installs.list_active_for_org(org_id):
            if inst.suspended_at is not None:
                continue
            try:
                info = await self.app_api.get_repository(inst.installation_id, github_repo_id)
            except GitHubAPIError as exc:
                if exc.status_code in (403, 404):
                    continue
                raise
            found = (inst, info)
            break
        if found is None:
            raise NotFoundError(
                "Repository is not accessible to this organization's GitHub App installation",
                code="repository_not_accessible",
            )
        inst, info = found

        repo = await self._repos.get_by_github_id(github_repo_id)
        if repo is not None and repo.organization_id != org_id:
            raise ConflictError(
                "Repository is connected to another organization",
                code="repository_linked_elsewhere",
            )
        now = datetime.now(UTC)
        if repo is None:
            repo = Repository(
                organization_id=org_id, github_repo_id=github_repo_id, full_name=info.full_name
            )
            self._session.add(repo)
        repo.installation_id = inst.id
        repo.full_name = info.full_name
        repo.default_branch = info.default_branch
        repo.is_private = info.private
        repo.primary_language = info.language
        repo.html_url = info.html_url
        repo.is_connected = True
        repo.connected_at = now
        repo.disconnected_at = None
        await self._session.flush()
        if (
            await self._session.scalar(
                select(RepositorySettings.id).where(RepositorySettings.repository_id == repo.id)
            )
            is None
        ):
            self._session.add(RepositorySettings(repository_id=repo.id))
        self._audit.record(
            "repository.connect",
            "repository",
            resource_id=repo.id,
            organization_id=org_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"full_name": info.full_name},
        )
        await self._session.commit()
        return await self.get(org_id, repo.id)

    async def disconnect(
        self, actor: OrganizationMember, repo_id: uuid.UUID, meta: RequestMeta
    ) -> None:
        repo = await self.get(actor.organization_id, repo_id)
        if not repo.is_connected:
            return
        repo.is_connected = False
        repo.disconnected_at = datetime.now(UTC)
        self._audit.record(
            "repository.disconnect",
            "repository",
            resource_id=repo.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"full_name": repo.full_name},
        )
        await self._session.commit()

    async def update_settings(
        self,
        actor: OrganizationMember,
        repo_id: uuid.UUID,
        update: RepositorySettingsUpdate,
        meta: RequestMeta,
    ) -> Repository:
        repo = await self.get(actor.organization_id, repo_id)
        settings = repo.settings
        if settings is None:
            settings = RepositorySettings(repository_id=repo.id)
            self._session.add(settings)
        changes = update.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(settings, field, value)
        self._audit.record(
            "repository.settings_update",
            "repository",
            resource_id=repo.id,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"changed": sorted(changes)},
        )
        await self._session.commit()
        self._session.expire(repo)
        return await self.get(actor.organization_id, repo_id)

    # ---- Review rules ---------------------------------------------------------------

    async def list_rules(self, org_id: uuid.UUID, repo_id: uuid.UUID) -> list[ReviewRule]:
        await self.get(org_id, repo_id)
        rows = await self._session.scalars(
            select(ReviewRule)
            .where(
                ReviewRule.organization_id == org_id,
                or_(ReviewRule.repository_id == repo_id, ReviewRule.repository_id.is_(None)),
            )
            .order_by(ReviewRule.rule_key)
        )
        return list(rows)

    async def upsert_rule(
        self,
        actor: OrganizationMember,
        repo_id: uuid.UUID,
        rule_key: str,
        data: ReviewRuleUpsert,
        meta: RequestMeta,
    ) -> ReviewRule:
        await self.get(actor.organization_id, repo_id)
        rule = await self._session.scalar(
            select(ReviewRule).where(
                ReviewRule.organization_id == actor.organization_id,
                ReviewRule.repository_id == repo_id,
                ReviewRule.rule_key == rule_key,
            )
        )
        if rule is None:
            rule = ReviewRule(
                organization_id=actor.organization_id,
                repository_id=repo_id,
                rule_key=rule_key,
                engine=data.engine,
            )
            self._session.add(rule)
        rule.engine = data.engine
        rule.enabled = data.enabled
        rule.severity_override = data.severity_override
        rule.languages = list(data.languages)
        rule.description = data.description
        self._audit.record(
            "review_rule.upsert",
            "review_rule",
            resource_id=rule_key,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
            details={"repository_id": str(repo_id), "enabled": data.enabled},
        )
        await self._session.commit()
        await self._session.refresh(rule)
        return rule

    async def delete_rule(
        self, actor: OrganizationMember, repo_id: uuid.UUID, rule_key: str, meta: RequestMeta
    ) -> None:
        rule = await self._session.scalar(
            select(ReviewRule).where(
                ReviewRule.organization_id == actor.organization_id,
                ReviewRule.repository_id == repo_id,
                ReviewRule.rule_key == rule_key,
            )
        )
        if rule is None:
            raise NotFoundError("Rule not found")
        await self._session.delete(rule)
        self._audit.record(
            "review_rule.delete",
            "review_rule",
            resource_id=rule_key,
            organization_id=actor.organization_id,
            actor_user_id=actor.user_id,
            meta=meta,
        )
        await self._session.commit()
