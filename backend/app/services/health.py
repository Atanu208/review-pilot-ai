"""Dependency health checks used by readiness probes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from app.core.database import check_database
from app.core.logging import get_logger
from app.core.redis import check_redis

logger = get_logger(__name__)

CheckFn = Callable[[], Awaitable[None]]
CHECK_TIMEOUT_SECONDS = 3.0


@dataclass(slots=True)
class CheckResult:
    name: str
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


class HealthService:
    def __init__(self, checks: dict[str, CheckFn] | None = None) -> None:
        self._checks: dict[str, CheckFn] = checks or {
            "database": check_database,
            "redis": check_redis,
        }

    async def _run(self, name: str, fn: CheckFn) -> CheckResult:
        start = time.perf_counter()
        try:
            await asyncio.wait_for(fn(), timeout=CHECK_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("health_check_failed", check=name, error_type=type(exc).__name__)
            return CheckResult(
                name=name,
                status="error",
                latency_ms=round((time.perf_counter() - start) * 1000, 2),
                # Only the exception type is exposed: messages can contain hostnames/DSNs.
                error=type(exc).__name__,
            )
        return CheckResult(
            name=name, status="ok", latency_ms=round((time.perf_counter() - start) * 1000, 2)
        )

    async def readiness(self) -> list[CheckResult]:
        return list(
            await asyncio.gather(*(self._run(name, fn) for name, fn in self._checks.items()))
        )
