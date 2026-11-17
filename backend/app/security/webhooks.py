"""GitHub webhook signature verification (HMAC-SHA256, constant-time compare)."""

from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Return True only if ``signature_header`` is a valid ``X-Hub-Signature-256`` for ``body``."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = compute_signature(secret, body)
    return hmac.compare_digest(expected, signature_header)
