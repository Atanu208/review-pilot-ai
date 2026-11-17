from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.api.health import get_health_service
from app.services.health import HealthService


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise ConnectionRefusedError("postgres://user:pw@db:5432 refused")


async def test_liveness(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"


async def test_readiness_ok_with_healthy_dependencies(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        {"database": _ok, "redis": _ok}
    )
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert {c["name"] for c in resp.json()["checks"]} == {"database", "redis"}


async def test_readiness_degraded_hides_connection_details(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    app.dependency_overrides[get_health_service] = lambda: HealthService(
        {"database": _boom, "redis": _ok}
    )
    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    db = next(c for c in body["checks"] if c["name"] == "database")
    assert db == {**db, "status": "error", "error": "ConnectionRefusedError"}
    assert "pw" not in resp.text


async def test_request_id_generated_and_security_headers(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert len(resp.headers["x-request-id"]) == 32
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_well_formed_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/live", headers={"X-Request-ID": "abc-123-def-456"})
    assert resp.headers["x-request-id"] == "abc-123-def-456"


async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health/live", headers={"X-Request-ID": "bad id\nInjected: log"})
    assert resp.headers["x-request-id"] != "bad id\nInjected: log"
    assert len(resp.headers["x-request-id"]) == 32


async def test_404_uses_structured_error_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "not_found"
    assert err["request_id"] == resp.headers["x-request-id"]


async def test_meta_endpoint_exposes_only_feature_flags(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["features"]["llm_provider"] == "mock"
    assert "secret" not in resp.text.lower()


async def test_cors_preflight_allows_configured_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/meta",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert resp.headers["access-control-allow-credentials"] == "true"


async def test_cors_rejects_unknown_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/meta",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in resp.headers


async def test_tests_do_not_load_developer_env_file(client: httpx.AsyncClient) -> None:
    from app.core.config import get_settings

    s = get_settings()
    assert s.openai_api_key is None
    assert s.github_client_secret is None
    assert s.llm_provider == "mock"


async def test_openapi_schema_available_outside_production(client: httpx.AsyncClient) -> None:
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/meta" in resp.json()["paths"]
