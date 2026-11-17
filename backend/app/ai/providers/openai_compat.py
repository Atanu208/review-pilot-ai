"""OpenAI-compatible Chat Completions provider (OpenAI, Groq, Ollama, Gemini, OpenRouter...).

Uses JSON mode (``response_format: json_object``), which every major compatible server
supports; the router validates the result against the Pydantic schema afterwards.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.ai.types import (
    LLMBadOutputError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMTransientError,
)


def _retry_after(response: httpx.Response) -> float:
    for header in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        value = response.headers.get(header)
        if not value:
            continue
        try:
            return float(value)
        except ValueError:
            # Groq style durations: "7.66s", "1m26.4s", "120ms".
            seconds = 0.0
            number = ""
            i = 0
            while i < len(value):
                ch = value[i]
                if ch.isdigit() or ch == ".":
                    number += ch
                elif value.startswith("ms", i):
                    seconds += float(number or 0) / 1000
                    number = ""
                    i += 1
                elif ch in "hms":
                    seconds += float(number or 0) * {"h": 3600, "m": 60, "s": 1}[ch]
                    number = ""
                i += 1
            if seconds:
                return seconds
    return 20.0


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = client
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key

    async def complete(self, request: LLMRequest, model: str) -> LLMResponse:
        system = (
            f"{request.system}\n\nRespond with a single JSON object that conforms to this "
            f"JSON Schema (no prose, no markdown fences):\n{json.dumps(request.json_schema)}"
        )
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": request.max_output_tokens,
            "temperature": 0.1,
        }
        started = time.perf_counter()
        try:
            response = await self._client.post(
                self._url, json=body, headers={"Authorization": f"Bearer {self._api_key}"}
            )
        except httpx.TimeoutException as exc:
            raise LLMTransientError(
                "LLM request timed out", provider=self.name, model=model
            ) from exc
        except httpx.TransportError as exc:
            raise LLMTransientError(
                f"LLM unreachable: {type(exc).__name__}", provider=self.name, model=model
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 429:
            raise LLMRateLimitError(
                "Rate limited", retry_after=_retry_after(response), provider=self.name, model=model
            )
        if response.status_code in (401, 403, 404):
            raise LLMConfigError(
                f"LLM endpoint rejected the request ({response.status_code}): {_message(response)}",
                provider=self.name,
                model=model,
            )
        if response.status_code >= 500 or response.status_code == 408:
            raise LLMTransientError(
                f"LLM server error {response.status_code}", provider=self.name, model=model
            )
        if response.status_code >= 400:
            message = _message(response)
            # Groq returns 400 json_validate_failed when the model emitted invalid JSON.
            if "json" in message.lower():
                raise LLMBadOutputError(message, provider=self.name, model=model)
            raise LLMError(
                f"LLM request failed ({response.status_code}): {message}",
                provider=self.name,
                model=model,
            )

        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
        cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
        result = LLMResponse(
            text=text,
            provider=self.name,
            model=str(data.get("model") or model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_input_tokens=int(cached),
            latency_ms=latency_ms,
            finish_reason=finish,
        )
        if finish == "length":
            raise LLMBadOutputError(
                "Response truncated (max tokens)", provider=self.name, model=model
            )
        return result


def _message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or err)[:300]
    return str(body)[:300]
