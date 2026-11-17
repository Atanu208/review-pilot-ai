"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import health
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import dispose_engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis
from app.integrations.github.factory import close_httpx_client

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info("startup", environment=settings.environment.value, version=__version__)
    try:
        yield
    finally:
        await close_redis()
        await close_httpx_client()
        await dispose_engine()
        logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        description="Intelligent Code Review and Automated Pull Request Analysis.",
        lifespan=lifespan,
        # Interactive docs are disabled in production-like environments.
        docs_url=None if settings.is_production_like else "/docs",
        redoc_url=None if settings.is_production_like else "/redoc",
        openapi_url=None if settings.is_production_like else "/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    # Added last => outermost: request IDs exist for every response, including CORS errors.
    app.add_middleware(RequestContextMiddleware, hsts=settings.is_production_like)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
