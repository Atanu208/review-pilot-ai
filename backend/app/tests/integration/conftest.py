"""Integration fixtures: real PostgreSQL (migrated with Alembic) and Redis."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND_ROOT = Path(__file__).resolve().parents[3]


async def _postgres_reachable(url: str) -> bool:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect():
            return True
    except Exception:
        return False
    finally:
        await engine.dispose()


async def _redis_reachable(url: str) -> bool:
    from redis.asyncio import Redis

    client = Redis.from_url(url)
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()


def _unavailable(reason: str) -> None:
    if os.environ.get("REQUIRE_INTEGRATION") == "1":
        pytest.fail(f"Integration dependency unavailable: {reason}")
    pytest.skip(
        f"Integration dependency unavailable: {reason} (docker compose up -d postgres redis)"
    )


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ["TEST_DATABASE_URL"]
    if not asyncio.run(_postgres_reachable(url)):
        _unavailable("PostgreSQL")
    return url


@pytest.fixture(scope="session")
def redis_url() -> str:
    url = os.environ["TEST_REDIS_URL"]
    if not asyncio.run(_redis_reachable(url)):
        _unavailable("Redis")
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"dburl={database_url}"]})()
    return cfg


@pytest.fixture(scope="session")
def migrated_database(alembic_config: Config, database_url: str) -> Iterator[str]:
    """Exercise the full migration path from an empty schema, once per test session."""
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    yield database_url


@pytest.fixture
async def db_session(migrated_database: str) -> AsyncIterator[AsyncSession]:
    """Session wrapped in an outer transaction that is rolled back after each test.

    Code under test may call ``commit()``: it commits a SAVEPOINT, not the outer transaction.
    """
    engine = create_async_engine(migrated_database, poolclass=NullPool)
    async with engine.connect() as conn:
        trans = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
    await engine.dispose()


# ---- API test harness ----------------------------------------------------------------

import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.api.deps import get_app_api, get_oauth_client  # noqa: E402
from app.api.v1.pull_requests import get_review_enqueuer  # noqa: E402
from app.api.v1.webhooks import get_webhook_enqueuer  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import get_db_session  # noqa: E402
from app.core.redis import close_redis, get_redis  # noqa: E402
from app.integrations.github.app_api import GitHubAppAPI  # noqa: E402
from app.integrations.github.factory import build_app_api, build_oauth_client  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Organization, OrganizationMember, User, UserSession  # noqa: E402
from app.models.enums import OrgRole  # noqa: E402
from app.security.tokens import csrf_token_for, hash_token, new_token  # noqa: E402
from app.tests.fakes.github import FakeGitHub  # noqa: E402

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture(scope="session")
def rsa_key_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    path = tmp_path_factory.mktemp("keys") / "app.pem"
    path.write_bytes(pem)
    return path


@pytest.fixture
def github_env(monkeypatch: pytest.MonkeyPatch, rsa_key_file: Path) -> None:
    for key, value in {
        "GITHUB_APP_ID": "12345",
        "GITHUB_APP_SLUG": "reviewpilot-test",
        "GITHUB_APP_PRIVATE_KEY_PATH": str(rsa_key_file),
        "GITHUB_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "GITHUB_CLIENT_ID": "Iv1.test",
        "GITHUB_CLIENT_SECRET": "test-client-secret",
        "RATE_LIMIT_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
async def clean_redis(redis_url: str) -> AsyncIterator[None]:
    await get_redis().flushdb()
    yield
    await get_redis().flushdb()
    await close_redis()


@dataclass
class ApiHarness:
    app: FastAPI
    client: httpx.AsyncClient
    db: AsyncSession
    github: FakeGitHub
    gh_client: httpx.AsyncClient
    enqueued: list[str] = field(default_factory=list)
    reviews_enqueued: list[str] = field(default_factory=list)

    def app_api(self) -> GitHubAppAPI:
        return build_app_api(get_settings(), self.gh_client)

    async def login(self, user: User) -> dict[str, str]:
        """Create a session for ``user``; returns headers carrying the CSRF token."""
        token = new_token()
        self.db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await self.db.flush()
        self.client.cookies.set("rp_session", token)
        return {"X-CSRF-Token": csrf_token_for(hash_token(token))}

    def logout_client(self) -> None:
        self.client.cookies.clear()


@pytest.fixture
async def api(
    github_env: None, db_session: AsyncSession, clean_redis: None
) -> AsyncIterator[ApiHarness]:
    settings = get_settings()
    fake = FakeGitHub()
    gh_client = httpx.AsyncClient(transport=fake.transport())
    app = create_app(settings)
    harness = ApiHarness(
        app=app,
        client=None,  # type: ignore[arg-type]
        db=db_session,
        github=fake,
        gh_client=gh_client,
    )

    async def _db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_oauth_client] = lambda: build_oauth_client(settings, gh_client)
    app.dependency_overrides[get_app_api] = lambda: build_app_api(settings, gh_client)
    app.dependency_overrides[get_webhook_enqueuer] = lambda: harness.enqueued.append
    app.dependency_overrides[get_review_enqueuer] = lambda: harness.reviews_enqueued.append

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        harness.client = client
        yield harness
    await gh_client.aclose()


async def make_user(db: AsyncSession, login: str | None = None) -> User:
    login = login or f"user-{uuid.uuid4().hex[:8]}"
    user = User(github_id=uuid.uuid4().int % 10**12, login=login)
    db.add(user)
    await db.flush()
    return user


async def make_org(db: AsyncSession, owner: User, name: str = "Acme") -> Organization:
    org = Organization(name=name, slug=f"acme-{uuid.uuid4().hex[:8]}", created_by_id=owner.id)
    db.add(org)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=owner.id, role=OrgRole.OWNER))
    await db.flush()
    return org


async def add_member(
    db: AsyncSession, org: Organization, user: User, role: OrgRole
) -> OrganizationMember:
    member = OrganizationMember(organization_id=org.id, user_id=user.id, role=role)
    db.add(member)
    await db.flush()
    return member
