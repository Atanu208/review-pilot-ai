from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import AuditLog, WebhookDelivery
from app.repositories.base import BaseRepository


class WebhookDeliveryRepository(BaseRepository):
    async def insert_if_new(
        self,
        *,
        delivery_id: str,
        event: str,
        action: str | None,
        installation_id: int | None,
        github_repo_id: int | None,
        payload_sha256: str,
        payload: dict[str, Any],
    ) -> uuid.UUID | None:
        """Insert atomically; returns the new row id, or None if the delivery already exists."""
        stmt = (
            insert(WebhookDelivery)
            .values(
                id=uuid.uuid4(),
                delivery_id=delivery_id,
                event=event,
                action=action,
                installation_id=installation_id,
                github_repo_id=github_repo_id,
                payload_sha256=payload_sha256,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=[WebhookDelivery.delivery_id])
            .returning(WebhookDelivery.id)
        )
        return await self.session.scalar(stmt)

    async def find_replay(
        self, *, delivery_id: str, payload_sha256: str, since: datetime
    ) -> WebhookDelivery | None:
        """An earlier delivery with an identical body but a different delivery id."""
        return await self.session.scalar(
            select(WebhookDelivery)
            .where(
                WebhookDelivery.payload_sha256 == payload_sha256,
                WebhookDelivery.delivery_id != delivery_id,
                WebhookDelivery.created_at >= since,
            )
            .limit(1)
        )

    async def get_by_delivery_id(self, delivery_id: str) -> WebhookDelivery | None:
        return await self.session.scalar(
            select(WebhookDelivery).where(WebhookDelivery.delivery_id == delivery_id)
        )

    async def lock_for_processing(self, row_id: uuid.UUID) -> WebhookDelivery | None:
        """Row lock so concurrent workers never process the same delivery twice."""
        return await self.session.scalar(
            select(WebhookDelivery)
            .where(WebhookDelivery.id == row_id)
            .with_for_update(skip_locked=True)
        )


class AuditRepository(BaseRepository):
    def add(self, entry: AuditLog) -> None:
        self.session.add(entry)
