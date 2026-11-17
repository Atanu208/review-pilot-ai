"""Liveness / readiness probes (unversioned, used by Docker, load balancers and k8s/ECS)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app import __version__
from app.core.config import Settings, get_settings
from app.schemas.health import DependencyStatus, LivenessResponse, ReadinessResponse
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["health"])


def get_health_service() -> HealthService:
    return HealthService()


@router.get("/live", response_model=LivenessResponse, summary="Liveness probe")
async def live(settings: Annotated[Settings, Depends(get_settings)]) -> LivenessResponse:
    return LivenessResponse(
        service=settings.app_name, version=__version__, environment=settings.environment.value
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe (database + redis)",
    responses={503: {"model": ReadinessResponse}},
)
async def ready(
    response: Response, service: Annotated[HealthService, Depends(get_health_service)]
) -> ReadinessResponse:
    results = await service.readiness()
    healthy = all(r.status == "ok" for r in results)
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if healthy else "degraded",
        checks=[
            DependencyStatus(name=r.name, status=r.status, latency_ms=r.latency_ms, error=r.error)
            for r in results
        ],
    )
