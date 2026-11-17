"""Anthropic Claude provider via the official ``anthropic`` SDK.

* Structured output: ``output_config.format`` with a JSON schema guarantees valid JSON.
* Claude Opus 5.5 always uses adaptive thinking; depth is set with ``output_config.effort``.
* Server-side refusal fallbacks (``fallbacks: "default"``) are enabled so a request a
  safety classifier declines is re-run on a suitable fallback model within the same call.
* The static system prompt is cached (``cache_control``) since every review reuses it.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from app.ai.types import (
    LLMBadOutputError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured outputs need ``additionalProperties: false`` on every object."""

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: fix(v) for k, v in node.items()}
            if node.get("type") == "object":
                node.setdefault("additionalProperties", False)
            return node
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node

    return fix(schema)  # type: ignore[no-any-return]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, effort: str = "high", timeout: float = 90.0) -> None:
        # The SDK retries 408/409/429/5xx itself; keep it short so the router can fall back.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._effort = effort

    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        started = time.perf_counter()
        try:
            params: dict[str, Any] = {
                "model": model,
                # Thinking tokens count toward max_tokens; leave room for both.
                "max_tokens": max(request.max_output_tokens, 16000),
                "system": [
                    {
                        "type": "text",
                        "text": request.system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                "messages": [{"role": "user", "content": request.user}],
                "output_config": {
                    "effort": self._effort,
                    "format": {
                        "type": "json_schema",
                        "schema": _strict_schema(request.json_schema),
                    },
                },
                "betas": [FALLBACK_BETA],
                "fallbacks": "default",
            }
            response = await self._client.beta.messages.create(**params)
        except anthropic.RateLimitError as exc:
            retry_after = float(exc.response.headers.get("retry-after", "30") or 30)
            raise LLMRateLimitError(
                str(exc), retry_after=retry_after, provider=self.name, model=model
            ) from exc
        except (
            anthropic.AuthenticationError,
            anthropic.PermissionDeniedError,
            anthropic.NotFoundError,
        ) as exc:
            raise LLMConfigError(str(exc), provider=self.name, model=model) from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Bad request: {exc.message}", provider=self.name, model=model) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409):
                raise LLMTransientError(
                    f"Anthropic error {exc.status_code}", provider=self.name, model=model
                ) from exc
            raise LLMError(
                f"Anthropic error {exc.status_code}: {exc.message}", provider=self.name, model=model
            ) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise LLMTransientError(
                f"Anthropic unreachable: {type(exc).__name__}", provider=self.name, model=model
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            category = (
                getattr(response.stop_details, "category", None) if response.stop_details else None
            )
            raise LLMRefusalError(
                f"Model declined (category: {category})", provider=self.name, model=model
            )
        if response.stop_reason == "max_tokens":
            raise LLMBadOutputError(
                "Response truncated (max_tokens)", provider=self.name, model=model
            )
        text = next((b.text for b in response.content if b.type == "text"), "")
        usage = response.usage
        return LLMResponse(
            text=text,
            provider=self.name,
            model=response.model,  # may be the fallback model if one served the request
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cached_input_tokens=usage.cache_read_input_tokens or 0,
            cache_write_tokens=usage.cache_creation_input_tokens or 0,
            latency_ms=latency_ms,
            finish_reason=response.stop_reason,
        )
