"""HTTP middleware: correlation IDs, access logging, security headers.

Implemented as pure ASGI middleware (not BaseHTTPMiddleware) to avoid buffering bodies
and to keep contextvars correctly scoped per request.
"""

from __future__ import annotations

import json
import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger("app.access")
REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-_.]{8,64}$")

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cross-origin-opener-policy": "same-origin",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = None
        for name, value in scope.get("headers", []):
            if name == REQUEST_ID_HEADER.encode():
                incoming = value.decode("latin-1")
                break
        # Only accept well-formed client IDs; otherwise mint our own (prevents log injection).
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for key, value in SECURITY_HEADERS.items():
                    headers.setdefault(key, value)
                if self.hsts:
                    headers.setdefault(
                        "strict-transport-security", "max-age=63072000; includeSubDomains"
                    )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Unhandled errors would otherwise be rendered by Starlette's outermost error
            # middleware, bypassing this one (no request id or security headers).
            if response_started:
                raise
            logger.exception("unhandled_exception", path=scope.get("path", ""))
            body = json.dumps(
                {
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred",
                        "details": None,
                        "request_id": request_id,
                    }
                }
            ).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            path = scope.get("path", "")
            log = logger.debug if path.startswith("/health") else logger.info
            log(
                "http_request",
                method=scope.get("method"),
                path=path,
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
