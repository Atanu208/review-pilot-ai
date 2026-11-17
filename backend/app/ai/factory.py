"""Build the configured LLM provider and router."""

from __future__ import annotations

from app.ai import prompts
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.base import LLMProvider
from app.ai.providers.mock import MockProvider
from app.ai.providers.openai_compat import OpenAICompatibleProvider
from app.ai.rate_limit import TokenBudget
from app.ai.router import LLMRouter
from app.core.config import Settings
from app.core.redis import get_redis
from app.integrations.github.factory import get_httpx_client


class AINotConfiguredError(Exception):
    pass


def build_provider(settings: Settings, provider: str | None = None) -> tuple[LLMProvider, str]:
    """Return (provider, primary model) for ``provider`` (default: settings.llm_provider)."""
    name = provider or settings.llm_provider
    if name == "mock":
        return MockProvider(), "mock-model"
    if name == "anthropic":
        if settings.anthropic_api_key is None:
            raise AINotConfiguredError("ANTHROPIC_API_KEY is not set")
        return (
            AnthropicProvider(
                settings.anthropic_api_key.get_secret_value(),
                effort=settings.anthropic_effort,
                timeout=settings.llm_request_timeout_seconds,
            ),
            settings.anthropic_model,
        )
    if name == "openai":
        if settings.openai_api_key is None:
            raise AINotConfiguredError("OPENAI_API_KEY is not set")
        return (
            OpenAICompatibleProvider(
                get_httpx_client(),
                settings.openai_base_url,
                settings.openai_api_key.get_secret_value(),
            ),
            settings.openai_model,
        )
    raise AINotConfiguredError(f"Unknown LLM provider {name!r}")


def build_router(
    settings: Settings,
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    provider: LLMProvider | None = None,
) -> LLMRouter:
    if provider is None:
        provider, primary = build_provider(settings, provider_override)
    else:
        primary = settings.openai_model if provider.name == "openai" else "mock-model"
    primary = model_override or primary
    same_provider = provider_override in (None, settings.llm_provider)
    models = [primary, *(settings.llm_fallback_models if same_provider else [])]
    budget = TokenBudget(
        get_redis() if settings.llm_tokens_per_minute else None, settings.llm_tokens_per_minute
    )
    return LLMRouter(
        provider,
        models,
        budget=budget,
        prompt_version=prompts.PROMPT_VERSION,
        fast_model=(settings.llm_fast_model if same_provider else None) or primary,
    )
