"""Read-only view of the server's AI and publishing configuration (never any secrets)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.ai import prompts
from app.api.deps import AppSettings, ViewerMember

router = APIRouter(prefix="/organizations/{org_id}/ai", tags=["ai"])


@router.get("/config", summary="AI provider, models, limits and prompt version")
async def ai_config(member: ViewerMember, settings: AppSettings) -> dict[str, Any]:
    provider = settings.llm_provider
    primary = {
        "anthropic": settings.anthropic_model,
        "openai": settings.openai_model,
        "mock": "mock-model",
    }[provider]
    return {
        "provider": provider,
        "configured": settings.llm_configured,
        "enabled": settings.ai_review_enabled,
        # Only the host is shown for OpenAI-compatible endpoints, never credentials.
        "endpoint": settings.openai_base_url if provider == "openai" else None,
        "primary_model": primary,
        "fallback_models": settings.llm_fallback_models if provider != "anthropic" else [],
        "fast_model": settings.llm_fast_model or primary,
        "effort": settings.anthropic_effort if provider == "anthropic" else None,
        "tokens_per_minute": settings.llm_tokens_per_minute,
        "reviewers": settings.ai_reviewers,
        "verify_findings": settings.ai_verify_findings,
        "min_confidence_floor": settings.ai_min_confidence_floor,
        "max_input_tokens": settings.ai_max_input_tokens,
        "max_batches_per_reviewer": settings.ai_max_batches_per_reviewer,
        "prompt_version": prompts.PROMPT_VERSION,
        "prompt_versions": list(prompts.PROMPT_HISTORY),
        "publishing": {
            "enabled": settings.github_publish_enabled,
            "max_inline_comments": settings.publish_max_inline_comments,
        },
    }
