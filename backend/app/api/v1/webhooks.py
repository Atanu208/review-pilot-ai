"""GitHub webhook receiver: verify → record (idempotent) → enqueue → 202. No slow work here."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse

from app.api.deps import AppSettings, DbSession
from app.core.errors import AppError, ServiceUnavailableError, UnauthorizedError
from app.core.logging import get_logger
from app.security.webhooks import verify_github_signature
from app.services.rate_limit import rate_limit
from app.services.webhooks import WebhookIngestService

logger = get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_DELIVERY_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")
_EVENT = re.compile(r"^[a-z_]{1,64}$")

Enqueue = Callable[[str], Any]


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


def get_webhook_enqueuer() -> Enqueue:
    from app.workers.tasks.webhooks import process_github_webhook

    return lambda row_id: process_github_webhook.delay(row_id)


async def _read_limited(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError("Webhook payload too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise PayloadTooLargeError("Webhook payload too large")
    return bytes(body)


@router.post(
    "/github",
    status_code=status.HTTP_202_ACCEPTED,
    summary="GitHub App webhook receiver",
    responses={200: {"description": "Duplicate delivery or ping"}, 401: {}, 413: {}, 429: {}},
    dependencies=[Depends(rate_limit("webhook", lambda s: s.rate_limit_webhooks_per_minute))],
)
async def github_webhook(
    request: Request,
    db: DbSession,
    settings: AppSettings,
    enqueue: Annotated[Enqueue, Depends(get_webhook_enqueuer)],
) -> JSONResponse:
    if settings.github_webhook_secret is None:
        raise ServiceUnavailableError("Webhook secret not configured", code="github_not_configured")

    body = await _read_limited(request, settings.webhook_max_body_bytes)
    if not verify_github_signature(
        settings.github_webhook_secret.get_secret_value(),
        body,
        request.headers.get("x-hub-signature-256"),
    ):
        logger.warning("webhook_signature_invalid")
        raise UnauthorizedError("Invalid webhook signature", code="invalid_signature")

    delivery_id = request.headers.get("x-github-delivery", "")
    event = request.headers.get("x-github-event", "")
    if not _DELIVERY_ID.match(delivery_id) or not _EVENT.match(event):
        raise AppError("Missing or malformed GitHub delivery headers", code="bad_headers")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise AppError("Payload is not valid JSON", code="invalid_json") from exc
    if not isinstance(payload, dict):
        raise AppError("Payload must be a JSON object", code="invalid_json")

    if event == "ping":
        return JSONResponse({"status": "pong"}, status_code=status.HTTP_200_OK)

    result = await WebhookIngestService(db).ingest(
        delivery_id=delivery_id,
        event=event,
        payload=payload,
        raw_body=body,
        replay_window_hours=settings.webhook_replay_window_hours,
    )
    if result.replay:
        return JSONResponse({"status": "replay_ignored"}, status_code=status.HTTP_200_OK)
    if result.should_enqueue and result.row_id is not None:
        try:
            enqueue(str(result.row_id))
        except Exception as exc:  # broker down: row stays "received" and is re-enqueued later
            logger.error(
                "webhook_enqueue_failed", delivery_id=delivery_id, error_type=type(exc).__name__
            )
    logger.info(
        "webhook_received",
        delivery_id=delivery_id,
        gh_event=event,
        action=payload.get("action"),
        duplicate=result.duplicate,
    )
    if result.duplicate:
        return JSONResponse(
            {"status": "duplicate", "requeued": result.should_enqueue},
            status_code=status.HTTP_200_OK,
        )
    return JSONResponse({"status": "accepted"}, status_code=status.HTTP_202_ACCEPTED)
