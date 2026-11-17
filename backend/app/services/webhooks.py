"""GitHub webhook ingestion (API, fast path) and processing (worker).

Ingestion only verifies, records and enqueues. Processing is idempotent: the delivery row
is locked (``FOR UPDATE SKIP LOCKED``) and skipped unless it is ``received`` or ``failed``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.integrations.github.app_api import installation_from_payload
from app.models import GitHubInstallation, Repository, WebhookDelivery
from app.models.enums import WebhookStatus
from app.repositories.github import (
    InstallationRepository,
    RepositoryRepository,
)
from app.repositories.operations import WebhookDeliveryRepository
from app.services.installations import apply_installation_info
from app.services.pull_requests import upsert_pull_request
from app.services.reviews import Enqueue, ReviewService

logger = get_logger(__name__)

PULL_REQUEST_ACTIONS = frozenset(
    {
        "opened",
        "synchronize",
        "reopened",
        "closed",
        "edited",
        "ready_for_review",
        "converted_to_draft",
    }
)


@dataclass(slots=True, frozen=True)
class IngestResult:
    row_id: uuid.UUID | None
    duplicate: bool
    should_enqueue: bool
    replay: bool = False


class WebhookIngestService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._deliveries = WebhookDeliveryRepository(session)

    async def ingest(
        self,
        *,
        delivery_id: str,
        event: str,
        payload: dict[str, Any],
        raw_body: bytes,
        replay_window_hours: int = 72,
    ) -> IngestResult:
        payload_sha256 = hashlib.sha256(raw_body).hexdigest()
        # GitHub signs only the body, so a captured delivery can be re-sent with a new
        # X-GitHub-Delivery id and still verify. Genuine deliveries always differ in body
        # (GitHub's own "Redeliver" reuses the original id), so an identical body under a
        # different id is a replay: acknowledge it, never process it.
        if replay_window_hours > 0:
            original = await self._deliveries.find_replay(
                delivery_id=delivery_id,
                payload_sha256=payload_sha256,
                since=datetime.now(UTC) - timedelta(hours=replay_window_hours),
            )
            if original is not None:
                await self._session.commit()
                logger.warning(
                    "webhook_replay_rejected",
                    delivery_id=delivery_id,
                    original_delivery_id=original.delivery_id,
                    gh_event=event,
                )
                return IngestResult(row_id=None, duplicate=True, should_enqueue=False, replay=True)

        installation = payload.get("installation") or {}
        repository = payload.get("repository") or {}
        row_id = await self._deliveries.insert_if_new(
            delivery_id=delivery_id,
            event=event,
            action=payload.get("action") if isinstance(payload.get("action"), str) else None,
            installation_id=_int_or_none(installation.get("id")),
            github_repo_id=_int_or_none(repository.get("id")),
            payload_sha256=payload_sha256,
            payload=payload,
        )
        if row_id is not None:
            await self._session.commit()
            return IngestResult(row_id=row_id, duplicate=False, should_enqueue=True)

        # Redelivery of a known delivery: re-enqueue only if it never completed.
        existing = await self._deliveries.get_by_delivery_id(delivery_id)
        await self._session.commit()
        retryable = existing is not None and existing.status in (
            WebhookStatus.RECEIVED,
            WebhookStatus.FAILED,
        )
        return IngestResult(
            row_id=existing.id if existing else None, duplicate=True, should_enqueue=retryable
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class WebhookProcessor:
    def __init__(self, session: AsyncSession, enqueue_review: Enqueue | None = None) -> None:
        self._session = session
        self._reviews = ReviewService(session, enqueue_review)
        self._pending_reviews: list[uuid.UUID] = []
        self._current_delivery_id: str | None = None
        self._deliveries = WebhookDeliveryRepository(session)
        self._installs = InstallationRepository(session)
        self._repos = RepositoryRepository(session)

    async def process(self, row_id: uuid.UUID) -> WebhookStatus | None:
        delivery = await self._deliveries.lock_for_processing(row_id)
        if delivery is None:
            return None  # unknown, or another worker holds it
        if delivery.status not in (WebhookStatus.RECEIVED, WebhookStatus.FAILED):
            return delivery.status

        try:
            handled = await self._dispatch(delivery)
        except Exception as exc:
            await self._session.rollback()
            await self._mark(row_id, WebhookStatus.FAILED, f"{type(exc).__name__}: {exc}"[:2000])
            raise
        status = WebhookStatus.PROCESSED if handled else WebhookStatus.IGNORED
        delivery.status = status
        delivery.processed_at = datetime.now(UTC)
        delivery.error_message = None
        await self._session.commit()
        # Enqueue only after commit so workers never see a job that might roll back.
        for job_id in self._pending_reviews:
            self._reviews.enqueue(job_id)
        logger.info(
            "webhook_processed",
            delivery_id=delivery.delivery_id,
            gh_event=delivery.event,
            action=delivery.action,
            status=status.value,
        )
        return status

    async def _mark(self, row_id: uuid.UUID, status: WebhookStatus, error: str | None) -> None:
        row = await self._session.get(WebhookDelivery, row_id)
        if row is not None:
            row.status = status
            row.error_message = error
            row.processed_at = datetime.now(UTC)
            await self._session.commit()

    async def _dispatch(self, delivery: WebhookDelivery) -> bool:
        self._current_delivery_id = delivery.delivery_id
        payload = delivery.payload
        match delivery.event:
            case "installation":
                return await self._on_installation(delivery.action, payload)
            case "installation_repositories":
                return await self._on_installation_repositories(delivery.action, payload)
            case "pull_request":
                return await self._on_pull_request(delivery.action, payload)
            case _:
                return False

    async def _on_installation(self, action: str | None, payload: dict[str, Any]) -> bool:
        info = installation_from_payload(payload["installation"])
        row = await self._installs.get_by_installation_id(info.id)
        if action in ("created", "new_permissions_accepted", "unsuspend", "suspend"):
            if row is None:
                row = GitHubInstallation(
                    installation_id=info.id,
                    account_id=info.account_id,
                    account_login=info.account_login,
                    account_type=info.account_type,
                )
                self._session.add(row)
            apply_installation_info(row, info)
            if action == "suspend":
                row.suspended_at = row.suspended_at or datetime.now(UTC)
            elif action == "unsuspend":
                row.suspended_at = None
            return True
        if action == "deleted":
            if row is None:
                return False
            row.deleted_at = datetime.now(UTC)
            row.organization_id = None
            count = await self._repos.disconnect_for_installation(row.id)
            logger.info("installation_deleted", installation_id=info.id, repos_disconnected=count)
            return True
        return False

    async def _on_installation_repositories(
        self, action: str | None, payload: dict[str, Any]
    ) -> bool:
        if action != "removed":
            return False  # added repositories are connected explicitly by users
        ids = [int(r["id"]) for r in payload.get("repositories_removed") or []]
        await self._repos.disconnect_by_github_ids(ids)
        return True

    async def _on_pull_request(self, action: str | None, payload: dict[str, Any]) -> bool:
        if action not in PULL_REQUEST_ACTIONS:
            return False
        repo = await self._session.scalar(
            select(Repository)
            .options(selectinload(Repository.settings))
            .where(
                Repository.github_repo_id == int(payload["repository"]["id"]),
                Repository.is_connected.is_(True),
            )
        )
        if repo is None:
            return False
        pr = await upsert_pull_request(self._session, repo, payload["pull_request"])
        job = await self._reviews.on_pull_request_event(action, pr, repo, self._current_delivery_id)
        if job is not None:
            self._pending_reviews.append(job.id)
        return True
