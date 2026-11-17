"""Shared pytest fixtures.

Unit tests need nothing external. Tests marked ``integration`` need PostgreSQL + Redis
(``docker compose up -d postgres redis``). They are skipped when those are unreachable,
unless ``REQUIRE_INTEGRATION=1`` (set in CI) in which case they fail loudly instead.
"""

from __future__ import annotations

import os

# Must be set before any app module reads settings.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Dry-run by default: tests never post to GitHub unless a test enables publishing explicitly.
os.environ["GITHUB_PUBLISH_ENABLED"] = "false"
os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://reviewpilot:reviewpilot@localhost:5432/reviewpilot_test",
)
os.environ.setdefault("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings, get_settings

# Tests must be hermetic: never read the developer's .env (real API keys, GitHub secrets).
Settings.model_config["env_file"] = None
# Never call a real LLM provider from the test suite, even if the shell exports one.
os.environ["LLM_PROVIDER"] = "mock"

from app.main import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return get_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
