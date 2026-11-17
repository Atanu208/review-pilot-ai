from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def test_defaults_are_safe_for_local() -> None:
    s = Settings(_env_file=None, environment=Environment.LOCAL)
    assert s.llm_provider == "mock"
    assert s.demo_mode is False
    assert s.github_app_configured is False


def test_cors_origins_parsed_from_comma_separated_string() -> None:
    s = Settings(_env_file=None, cors_origins="https://a.example, https://b.example")
    assert s.cors_origins == ["https://a.example", "https://b.example"]


def test_broker_falls_back_to_redis_url() -> None:
    s = Settings(_env_file=None, redis_url="redis://cache:6379/2")
    assert s.broker_url == "redis://cache:6379/2"
    assert s.result_backend == "redis://cache:6379/2"


@pytest.mark.parametrize("env", [Environment.PRODUCTION, Environment.STAGING])
def test_production_rejects_default_secret(env: Environment) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None, environment=env)


def test_production_requires_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        Settings(_env_file=None, environment=Environment.PRODUCTION, secret_key="x" * 40)


def test_production_rejects_demo_mode() -> None:
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            secret_key="x" * 40,
            encryption_key="k",
            demo_mode=True,
        )


_FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfake\n-----END RSA PRIVATE KEY-----\n"


def test_private_key_loaded_from_path(tmp_path: Path) -> None:
    key = tmp_path / "app.pem"
    key.write_text(_FAKE_PEM)
    s = Settings(_env_file=None, github_app_id="1", github_app_private_key_path=key)
    assert s.github_app_configured
    assert s.load_github_private_key() == _FAKE_PEM


def test_private_key_inline_with_escaped_newlines() -> None:
    inline = _FAKE_PEM.strip().replace("\n", "\\n")
    s = Settings(_env_file=None, github_app_id="1", github_app_private_key=inline)
    assert s.load_github_private_key() == _FAKE_PEM


def test_missing_private_key_file_means_not_configured(tmp_path: Path) -> None:
    s = Settings(
        _env_file=None, github_app_id="1", github_app_private_key_path=tmp_path / "missing.pem"
    )
    assert not s.github_app_configured
    with pytest.raises(ValueError, match="not readable"):
        s.load_github_private_key()


def test_non_pem_private_key_rejected(tmp_path: Path) -> None:
    key = tmp_path / "app.pem"
    key.write_text("not a key")
    s = Settings(_env_file=None, github_app_id="1", github_app_private_key_path=key)
    with pytest.raises(ValueError, match="not a PEM"):
        s.load_github_private_key()


def test_production_accepts_complete_configuration() -> None:
    s = Settings(
        _env_file=None,
        environment=Environment.PRODUCTION,
        secret_key="x" * 40,
        encryption_key="k",
    )
    assert s.is_production_like
