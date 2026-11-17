"""Repository management, settings and review rules (organization-scoped)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import AdminMember, DbSession, Meta, ViewerMember, get_app_api
from app.integrations.github.app_api import GitHubAppAPI
from app.schemas.common import Page, PageParams, page_params
from app.schemas.repository import (
    RULE_KEY_PATTERN,
    AvailableRepository,
    RepositoryConnect,
    RepositoryOut,
    RepositorySettingsUpdate,
    ReviewRuleOut,
    ReviewRuleUpsert,
)
from app.services.repositories import RepositoryService

router = APIRouter(prefix="/organizations/{org_id}/repositories", tags=["repositories"])
AppAPI = Annotated[GitHubAppAPI, Depends(get_app_api)]
RuleKey = Annotated[str, Path(pattern=RULE_KEY_PATTERN)]


@router.get("", response_model=Page[RepositoryOut], summary="List repositories")
async def list_repositories(
    member: ViewerMember,
    db: DbSession,
    page: Annotated[PageParams, Depends(page_params)],
    include_disconnected: bool = Query(False),
) -> Page[RepositoryOut]:
    rows, total = await RepositoryService(db).list_repositories(
        member.organization_id,
        connected_only=not include_disconnected,
        limit=page.limit,
        offset=page.offset,
    )
    return Page(
        items=[RepositoryOut.model_validate(r) for r in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/available",
    response_model=list[AvailableRepository],
    summary="Repositories the organization's GitHub App installations can access (admin)",
)
async def available_repositories(
    member: AdminMember, db: DbSession, app_api: AppAPI
) -> list[AvailableRepository]:
    return await RepositoryService(db, app_api).available(member.organization_id)


@router.post(
    "",
    response_model=RepositoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a repository (admin)",
)
async def connect_repository(
    body: RepositoryConnect, member: AdminMember, db: DbSession, app_api: AppAPI, meta: Meta
) -> RepositoryOut:
    repo = await RepositoryService(db, app_api).connect(member, body.github_repo_id, meta)
    return RepositoryOut.model_validate(repo)


@router.get("/{repo_id}", response_model=RepositoryOut, summary="Repository details")
async def get_repository(repo_id: uuid.UUID, member: ViewerMember, db: DbSession) -> RepositoryOut:
    return RepositoryOut.model_validate(
        await RepositoryService(db).get(member.organization_id, repo_id)
    )


@router.delete(
    "/{repo_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Disconnect a repository (admin)"
)
async def disconnect_repository(
    repo_id: uuid.UUID, member: AdminMember, db: DbSession, meta: Meta
) -> Response:
    await RepositoryService(db).disconnect(member, repo_id, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{repo_id}/settings", response_model=RepositoryOut, summary="Update review settings (admin)"
)
async def update_settings(
    repo_id: uuid.UUID,
    body: RepositorySettingsUpdate,
    member: AdminMember,
    db: DbSession,
    meta: Meta,
) -> RepositoryOut:
    repo = await RepositoryService(db).update_settings(member, repo_id, body, meta)
    return RepositoryOut.model_validate(repo)


@router.get(
    "/{repo_id}/rules",
    response_model=list[ReviewRuleOut],
    summary="Review rules (repository + organization-wide)",
)
async def list_rules(
    repo_id: uuid.UUID, member: ViewerMember, db: DbSession
) -> list[ReviewRuleOut]:
    rules = await RepositoryService(db).list_rules(member.organization_id, repo_id)
    return [ReviewRuleOut.model_validate(r) for r in rules]


@router.put(
    "/{repo_id}/rules/{rule_key}",
    response_model=ReviewRuleOut,
    summary="Create or update a repository review rule (admin)",
)
async def upsert_rule(
    repo_id: uuid.UUID,
    rule_key: RuleKey,
    body: ReviewRuleUpsert,
    member: AdminMember,
    db: DbSession,
    meta: Meta,
) -> ReviewRuleOut:
    rule = await RepositoryService(db).upsert_rule(member, repo_id, rule_key, body, meta)
    return ReviewRuleOut.model_validate(rule)


@router.delete(
    "/{repo_id}/rules/{rule_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a repository review rule (admin)",
)
async def delete_rule(
    repo_id: uuid.UUID, rule_key: RuleKey, member: AdminMember, db: DbSession, meta: Meta
) -> Response:
    await RepositoryService(db).delete_rule(member, repo_id, rule_key, meta)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
