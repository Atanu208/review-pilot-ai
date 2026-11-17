from __future__ import annotations

from typing import Protocol

from app.ai.types import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    """One backend (Anthropic, an OpenAI-compatible endpoint, or the mock)."""

    name: str

    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        """Return the model's raw text (expected to be JSON). Raise ``LLMError`` subclasses."""
        ...
