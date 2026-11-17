"""Symmetric encryption for secrets stored at rest (OAuth tokens, provider API keys).

Uses Fernet (AES-128-CBC + HMAC-SHA256). Supports key rotation via ``MultiFernet``:
``ENCRYPTION_KEY`` may hold several comma-separated keys; the first encrypts, all decrypt.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class EncryptionError(Exception):
    """Raised when a ciphertext cannot be decrypted with any configured key."""


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _derive_dev_key(secret: str) -> bytes:
    # Only used in local/test when ENCRYPTION_KEY is absent (production refuses to start).
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


@lru_cache
def _cipher() -> MultiFernet:
    settings = get_settings()
    if settings.encryption_key is not None:
        keys = [k.strip() for k in settings.encryption_key.get_secret_value().split(",")]
        fernets = [Fernet(k.encode()) for k in keys if k]
    else:
        fernets = [Fernet(_derive_dev_key(settings.secret_key.get_secret_value()))]
    return MultiFernet(fernets)


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError("Unable to decrypt value with configured keys") from exc


def reset_cipher_cache() -> None:
    _cipher.cache_clear()
