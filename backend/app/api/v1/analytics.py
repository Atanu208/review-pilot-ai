"""Analytics (organization-scoped, read-only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.api.deps import DbSession, ViewerMember
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/organizations/{org_id}/analytics", tags=["analytics"])
Days = Annotated[int, Query(ge=1, le=365, description="Look-back window in days")]


@router.get("/overview", summary="Totals, severity split, recent reviews and security issues")
async def overview(member: ViewerMember, db: DbSession, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(db, member.organization_id, days).overview()


@router.get("/timeseries", summary="Reviews and findings per day")
async def timeseries(member: ViewerMember, db: DbSession, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(db, member.organization_id, days).timeseries()


@router.get("/ai-usage", summary="LLM calls, tokens, latency and cost by model, stage and day")
async def ai_usage(member: ViewerMember, db: DbSession, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(db, member.organization_id, days).ai_usage()


@router.get("/quality", summary="Finding outcomes and feedback by source (false-positive rates)")
async def quality(member: ViewerMember, db: DbSession, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(db, member.organization_id, days).quality()


@router.get("/team", summary="Reviews and findings per pull-request author")
async def team(member: ViewerMember, db: DbSession, days: Days = 30) -> dict[str, Any]:
    return await AnalyticsService(db, member.organization_id, days).team()
