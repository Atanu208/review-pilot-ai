"""Audit trail for privileged actions (written in the caller's transaction)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.repositories.operations import AuditRepository


@dataclass(slots=True, frozen=True)
class RequestMeta:
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None

    @classmethod
    def from_request(cls, request: Request) -> RequestMeta:
        return cls(
            ip_address=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:512] or None,
            request_id=getattr(request.state, "request_id", None),
        )


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditRepository(session)

    def record(
        self,
        action: str,
        resource_type: str,
        *,
        resource_id: uuid.UUID | str | None = None,
        organization_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        meta: RequestMeta | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        meta = meta or RequestMeta()
        self._repo.add(
            AuditLog(
                organization_id=organization_id,
                actor_user_id=actor_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id) if resource_id is not None else None,
                ip_address=meta.ip_address,
                user_agent=meta.user_agent,
                request_id=meta.request_id,
                details=details or {},
            )
        )
