"""Opaque tokens, CSRF tokens and signed short-lived state tokens.

* Session / invitation tokens: 256-bit random, only their SHA-256 is persisted.
* CSRF token: HMAC(secret, session-token-hash). Stateless, bound to one session, and
  readable only via the authenticated, CORS-protected ``/auth/me`` response.
* State tokens: HMAC-signed JSON with purpose + expiry (OAuth state, install state).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from app.core.config import get_settings


class InvalidStateError(Exception):
    """Raised when a signed state token is malformed, tampered with, expired or misused."""


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _secret() -> bytes:
    return get_settings().secret_key.get_secret_value().encode()


def csrf_token_for(session_token_hash: str) -> str:
    return hmac.new(_secret(), f"csrf:{session_token_hash}".encode(), hashlib.sha256).hexdigest()


def verify_csrf(session_token_hash: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(csrf_token_for(session_token_hash), provided)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_state(purpose: str, data: dict[str, Any], ttl_seconds: int) -> str:
    payload = {"p": purpose, "exp": int(time.time()) + ttl_seconds, "d": data}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(_secret(), f"state:{body}".encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state(token: str | None, purpose: str) -> dict[str, Any]:
    if not token or token.count(".") != 1:
        raise InvalidStateError("malformed state")
    body, sig = token.split(".")
    expected = _b64(hmac.new(_secret(), f"state:{body}".encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise InvalidStateError("bad signature")
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidStateError("malformed state") from exc
    if payload.get("p") != purpose:
        raise InvalidStateError("wrong purpose")
    if int(payload.get("exp", 0)) < int(time.time()):
        raise InvalidStateError("expired")
    data = payload.get("d")
    if not isinstance(data, dict):
        raise InvalidStateError("malformed state")
    return data


def safe_redirect_path(value: str | None, default: str = "/dashboard") -> str:
    """Allow only same-origin relative paths (prevents open redirects)."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if any(ch in value for ch in ("\r", "\n", "\t")):
        return default
    return value
