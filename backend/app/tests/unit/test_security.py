from __future__ import annotations

import pytest

from app.core.logging import REDACTED, redact_processor, redact_text
from app.security import encryption
from app.security.webhooks import compute_signature, verify_github_signature

# ---- Webhook signatures --------------------------------------------------------------


def test_valid_signature_is_accepted() -> None:
    body = b'{"action":"opened"}'
    sig = compute_signature("s3cret", body)
    assert verify_github_signature("s3cret", body, sig)


@pytest.mark.parametrize(
    "header",
    [None, "", "sha1=abc", "sha256=deadbeef", "sha256="],
)
def test_invalid_signatures_are_rejected(header: str | None) -> None:
    assert not verify_github_signature("s3cret", b"{}", header)


def test_signature_with_wrong_secret_is_rejected() -> None:
    body = b"{}"
    assert not verify_github_signature("s3cret", body, compute_signature("other", body))


def test_tampered_body_is_rejected() -> None:
    sig = compute_signature("s3cret", b'{"a":1}')
    assert not verify_github_signature("s3cret", b'{"a":2}', sig)


def test_empty_secret_never_validates() -> None:
    body = b"{}"
    assert not verify_github_signature("", body, compute_signature("", body))


# ---- Encryption ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_cipher() -> None:
    encryption.reset_cipher_cache()


def test_encrypt_roundtrip_and_non_deterministic() -> None:
    a = encryption.encrypt("gho_token_value")
    b = encryption.encrypt("gho_token_value")
    assert a != b
    assert "gho_token_value" not in a
    assert encryption.decrypt(a) == "gho_token_value"


def test_key_rotation(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    old, new = encryption.generate_key(), encryption.generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", old)
    get_settings.cache_clear()
    encryption.reset_cipher_cache()
    ciphertext = encryption.encrypt("value")

    monkeypatch.setenv("ENCRYPTION_KEY", f"{new},{old}")
    get_settings.cache_clear()
    encryption.reset_cipher_cache()
    assert encryption.decrypt(ciphertext) == "value"

    monkeypatch.setenv("ENCRYPTION_KEY", new)
    get_settings.cache_clear()
    encryption.reset_cipher_cache()
    with pytest.raises(encryption.EncryptionError):
        encryption.decrypt(ciphertext)


# ---- Secret redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 40,
        "sk-ant-api03-" + "c" * 40,
        "sk-" + "d" * 40,
        "gsk_" + "e" * 52,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "AIza" + "f" * 35,
        "xoxb-" + "1234567890-abcdef",
        "sk_live_" + "g" * 24,
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redact_text_removes_known_secret_formats(secret: str) -> None:
    out = redact_text(f"token was {secret} here")
    assert secret not in out
    assert REDACTED in out


def test_redact_processor_masks_sensitive_keys_recursively() -> None:
    event = {
        "event": "x",
        "access_token": "plain",
        "nested": {"client_secret": "abc", "safe": "ok"},
        "items": ["ghp_" + "z" * 36],
    }
    out = redact_processor(None, "info", event)
    assert out["access_token"] == REDACTED
    assert out["nested"]["client_secret"] == REDACTED
    assert out["nested"]["safe"] == "ok"
    assert out["items"] == [REDACTED]
