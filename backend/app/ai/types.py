"""Provider-neutral LLM request/response types and errors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class LLMRequest:
    system: str
    user: str
    # JSON Schema the response must satisfy (providers enforce natively where possible;
    # the router always re-validates with Pydantic).
    json_schema: dict[str, Any]
    schema_name: str
    max_output_tokens: int
    # Which pipeline step issued the call (for usage accounting and mock scripting).
    stage: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None


class LLMError(Exception):
    """Base error. ``transient`` errors may succeed on retry or another model."""

    transient: bool = False

    def __init__(self, message: str, *, provider: str = "", model: str = "") -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class LLMTransientError(LLMError):
    transient = True


class LLMRateLimitError(LLMTransientError):
    def __init__(
        self, message: str, *, retry_after: float, provider: str = "", model: str = ""
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.retry_after = retry_after


class LLMRefusalError(LLMError):
    """The model declined (e.g. a safety classifier). Another model may still answer."""

    transient = True


class LLMBadOutputError(LLMError):
    """Output was truncated, not JSON, or failed schema validation."""

    transient = True


class LLMConfigError(LLMError):
    """Missing/invalid credentials or model: retrying will not help."""
