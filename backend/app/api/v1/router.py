"""Aggregates every ``/api/v1`` router."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1 import (
    ai_config,
    analytics,
    auth,
    findings,
    github,
    meta,
    organizations,
    pull_requests,
    repositories,
    webhooks,
)
from app.services.rate_limit import write_rate_limit

# Every state-changing request from a browser counts against a per-session budget.
user_router = APIRouter(dependencies=[Depends(write_rate_limit)])
user_router.include_router(meta.router)
user_router.include_router(auth.router)
user_router.include_router(organizations.router)
user_router.include_router(github.router)
user_router.include_router(repositories.router)
user_router.include_router(pull_requests.router)
user_router.include_router(findings.router)
user_router.include_router(analytics.router)
user_router.include_router(ai_config.router)

api_router = APIRouter()
api_router.include_router(user_router)
# GitHub deliveries have no session; the receiver applies its own per-IP limit.
api_router.include_router(webhooks.router)
