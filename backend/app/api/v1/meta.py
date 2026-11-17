"""Public, non-sensitive service metadata consumed by the frontend at startup."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import __version__
from app.core.config import Settings, get_settings

router = APIRouter(prefix="/meta", tags=["meta"])


class Features(BaseModel):
    github_oauth: bool
    github_app: bool
    llm_provider: str
    demo_mode: bool


class MetaResponse(BaseModel):
    name: str
    version: str
    environment: str
    features: Features


@router.get("", response_model=MetaResponse, summary="Service metadata and enabled features")
async def get_meta(settings: Annotated[Settings, Depends(get_settings)]) -> MetaResponse:
    return MetaResponse(
        name=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        features=Features(
            github_oauth=settings.github_oauth_configured,
            github_app=settings.github_app_configured,
            llm_provider=settings.llm_provider,
            demo_mode=settings.demo_mode,
        ),
    )
