from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class DependencyStatus(BaseModel):
    name: str
    status: Literal["ok", "error"]
    latency_ms: float
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: list[DependencyStatus]
