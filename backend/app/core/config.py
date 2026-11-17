"""Application configuration loaded from environment variables.

All secrets come from the environment (or a local, git-ignored ``.env``). Nothing
sensitive has a usable default: production startup fails fast if secrets are missing.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env (shared with docker compose) then backend/.env; later wins.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---------------------------------------------------------------
    app_name: str = "ReviewPilot AI"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    api_v1_prefix: str = "/api/v1"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    demo_mode: bool = False

    # --- Security ------------------------------------------------------------------
    # Used to sign session cookies / state tokens.
    secret_key: SecretStr = SecretStr("insecure-dev-secret-change-me")
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt tokens at rest.
    encryption_key: SecretStr | None = None
    session_cookie_name: str = "rp_session"
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    oauth_state_cookie_name: str = "rp_oauth_state"
    oauth_state_ttl_seconds: int = 600
    invitation_ttl_days: int = 7
    rate_limit_enabled: bool = True
    # Per-limit knobs (0 disables that limit). Windows are one minute.
    rate_limit_writes_per_minute: int = 120
    rate_limit_review_actions_per_minute: int = 10
    rate_limit_webhooks_per_minute: int = 1000
    webhook_max_body_bytes: int = 5 * 1024 * 1024
    # A signed body seen under another delivery id within this window is a replay.
    webhook_replay_window_hours: int = 72

    # --- Database ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://reviewpilot:reviewpilot@localhost:5432/reviewpilot"
    database_pool_size: int = 10
    database_max_overflow: int = 10
    database_echo: bool = False

    # --- Redis / Celery ------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # --- GitHub App ------------------------------------------------------------------
    github_app_id: str | None = None
    github_app_slug: str | None = None
    github_app_private_key: SecretStr | None = None
    # Preferred: path to the downloaded .pem (mounted read-only in Docker).
    github_app_private_key_path: Path | None = None
    github_webhook_secret: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    github_http_timeout_seconds: float = 20.0
    # Master switch for posting reviews to GitHub. False = dry-run: reviews are computed and
    # shown in ReviewPilot but nothing is posted (demos, evaluation, the automated tests).
    github_publish_enabled: bool = True
    # Inline comments per GitHub review; extra findings are listed in the summary instead.
    publish_max_inline_comments: int = 25

    # --- Static analysis -------------------------------------------------------------
    # Semgrep runs from its own virtualenv (its pinned deps conflict with the app's).
    # Unset = look up "semgrep" on PATH; the Docker image sets /opt/semgrep/bin/semgrep.
    semgrep_bin: str | None = None
    semgrep_timeout_seconds: int = 120
    semgrep_max_memory_mb: int = 1536
    analysis_max_file_bytes: int = 512_000
    analysis_fetch_concurrency: int = 8

    # --- AI providers ----------------------------------------------------------------
    llm_provider: Literal["anthropic", "openai", "mock"] = "mock"
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    # Claude Opus 5.5 always thinks; effort controls depth (its default is "medium").
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # Extra models on the same provider: used for load spreading and as fallbacks.
    llm_fallback_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Smaller/faster model for the summary step (defaults to the primary model).
    llm_fast_model: str | None = None
    # Per-model tokens-per-minute budget (0 = unlimited). Groq free tier: ~8000.
    llm_tokens_per_minute: int = 0
    llm_request_timeout_seconds: float = 90.0

    # --- AI review engine --------------------------------------------------------------
    ai_review_enabled: bool = True
    ai_reviewers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["security", "bugs", "performance", "quality"]
    )
    ai_max_input_tokens: int = 3000  # per LLM call (keep under small free-tier TPM limits)
    ai_max_output_tokens: int = 2000  # reasoning models spend output tokens on thinking
    ai_max_batches_per_reviewer: int = 3
    ai_verify_findings: bool = True  # second-pass LLM validation of candidate findings
    # AI findings need at least this confidence (on top of repository min_confidence).
    # 0.75 chosen from live evals: it removed every speculative "might be null" finding
    # (all scored 0.70) without losing a single true positive (all scored >= 0.85).
    ai_min_confidence_floor: float = 0.75

    # --- Observability -----------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "reviewpilot-backend"

    @field_validator("cors_origins", "llm_fallback_models", "ai_reviewers", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> Settings:
        if self.environment in (Environment.PRODUCTION, Environment.STAGING):
            if self.secret_key.get_secret_value() == "insecure-dev-secret-change-me":
                raise ValueError("SECRET_KEY must be set outside local/test environments")
            if len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters")
            if self.encryption_key is None:
                raise ValueError("ENCRYPTION_KEY must be set outside local/test environments")
            if self.debug:
                raise ValueError("DEBUG must be false outside local/test environments")
            if self.demo_mode:
                raise ValueError("DEMO_MODE must be false outside local/test environments")
        return self

    @property
    def is_production_like(self) -> bool:
        return self.environment in (Environment.PRODUCTION, Environment.STAGING)

    @property
    def secure_cookies(self) -> bool:
        return self.is_production_like or self.backend_url.startswith("https://")

    @property
    def oauth_callback_url(self) -> str:
        return f"{self.backend_url}{self.api_v1_prefix}/auth/github/callback"

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "mock":
            return True
        if self.llm_provider == "anthropic":
            return self.anthropic_api_key is not None
        return self.openai_api_key is not None

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def github_app_configured(self) -> bool:
        has_key = self.github_app_private_key is not None or (
            self.github_app_private_key_path is not None
            and self.github_app_private_key_path.is_file()
        )
        return bool(self.github_app_id and has_key)

    def load_github_private_key(self) -> str:
        """Return the GitHub App private key PEM from the file path or the inline value.

        Inline values may use literal ``\\n`` sequences (single-line env vars).
        """
        if self.github_app_private_key_path is not None:
            try:
                pem = self.github_app_private_key_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ValueError("GITHUB_APP_PRIVATE_KEY_PATH is not readable") from exc
        elif self.github_app_private_key is not None:
            pem = self.github_app_private_key.get_secret_value().replace("\\n", "\n")
        else:
            raise ValueError("GitHub App private key is not configured")
        pem = pem.strip()
        if not (pem.startswith("-----BEGIN") and "PRIVATE KEY-----" in pem):
            raise ValueError("GitHub App private key is not a PEM private key")
        return pem + "\n"

    @property
    def github_oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
