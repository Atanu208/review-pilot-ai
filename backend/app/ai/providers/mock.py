"""Deterministic mock provider: no network, no keys. Used by tests, evals and demo mode.

A ``responder`` callable decides the JSON for each request (by ``request.stage``). The
default produces a clean review: no findings, every candidate confirmed, a generic summary.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.ai.types import LLMError, LLMRequest, LLMResponse

Responder = Callable[[LLMRequest, str], dict[str, Any] | str | Exception]


def default_responder(request: LLMRequest, model: str) -> dict[str, Any]:
    if request.stage.startswith("review."):
        return {"findings": []}
    if request.stage == "validate":
        count = int(request.metadata.get("candidate_count", 0))
        return {
            "verdicts": [
                {"index": i, "verdict": "confirm", "confidence": 0.9, "reason": "ok"}
                for i in range(count)
            ]
        }
    if request.stage == "summary":
        return {"summary": "Automated review completed.", "risk_level": "low", "highlights": []}
    return {}


class MockProvider:
    name = "mock"

    def __init__(self, responder: Responder | None = None) -> None:
        self.responder = responder or default_responder
        self.requests: list[tuple[LLMRequest, str]] = []

    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        self.requests.append((request, model))
        result = self.responder(request, model)
        if isinstance(result, Exception):
            raise result if isinstance(result, LLMError) else LLMError(str(result))
        text = result if isinstance(result, str) else json.dumps(result)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            input_tokens=(len(request.system) + len(request.user)) // 4,
            output_tokens=len(text) // 4,
            latency_ms=1,
            finish_reason="stop",
        )
