"""Model routing: pick a model, enforce structured output, retry, fall back, account usage."""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.pricing import cost_usd
from app.ai.providers.base import LLMProvider
from app.ai.rate_limit import TokenBudget
from app.ai.types import (
    LLMBadOutputError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
)
from app.core.logging import get_logger

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

MAX_WAIT_SECONDS = 75.0
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass(slots=True)
class UsageRecord:
    stage: str
    provider: str
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    priced: bool = False
    latency_ms: int = 0
    success: bool = True
    error_type: str | None = None


@dataclass(slots=True)
class RouterStats:
    calls: int = 0
    failures: int = 0
    fallbacks: int = 0
    usage: list[UsageRecord] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token, +10% margin) for budgeting before a call."""
    return int(len(text) / 4 * 1.1) + 1


def parse_json_output[M: BaseModel](text: str, schema: type[M]) -> M:
    cleaned = _FENCE.sub("", text.strip())
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LLMBadOutputError("No JSON object in model output")
        cleaned = cleaned[start : end + 1]
    try:
        return schema.model_validate(json.loads(cleaned))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise LLMBadOutputError(f"Output failed schema validation: {str(exc)[:300]}") from exc


class LLMRouter:
    """Routes structured calls over a pool of models on one provider.

    * Spreads calls across the pool (round-robin start) to use each model's quota.
    * On rate limit / transient error / bad output, tries the next model; waits for a
      budget window only when every model is saturated (bounded by MAX_WAIT_SECONDS).
    * Every attempt, successful or not, becomes a ``UsageRecord``.
    """

    def __init__(
        self,
        provider: LLMProvider,
        models: Sequence[str],
        *,
        budget: TokenBudget | None = None,
        prompt_version: str,
        fast_model: str | None = None,
    ) -> None:
        if not models:
            raise ValueError("at least one model is required")
        self.provider = provider
        self.models = list(dict.fromkeys(models))
        self.fast_model = fast_model or self.models[0]
        self.budget = budget or TokenBudget(None, 0)
        self.prompt_version = prompt_version
        self.stats = RouterStats()
        self._rr = itertools.count()

    def _order(self, prefer: str | None) -> list[str]:
        start = next(self._rr) % len(self.models)
        ordered = self.models[start:] + self.models[:start]
        if prefer and prefer in ordered:
            ordered.remove(prefer)
            ordered.insert(0, prefer)
        return ordered

    def _record(
        self, request: LLMRequest, model: str, resp: LLMResponse | None, error: Exception | None
    ) -> None:
        cost, priced = (Decimal(0), False)
        if resp is not None:
            cost, priced = cost_usd(
                resp.model,
                resp.input_tokens,
                resp.output_tokens,
                resp.cached_input_tokens,
                resp.cache_write_tokens,
            )
        self.stats.usage.append(
            UsageRecord(
                stage=request.stage,
                provider=self.provider.name,
                model=resp.model if resp else model,
                prompt_version=self.prompt_version,
                input_tokens=resp.input_tokens if resp else 0,
                output_tokens=resp.output_tokens if resp else 0,
                cached_input_tokens=resp.cached_input_tokens if resp else 0,
                cost_usd=cost,
                priced=priced,
                latency_ms=resp.latency_ms if resp else 0,
                success=error is None,
                error_type=type(error).__name__ if error else None,
            )
        )

    async def call(self, request: LLMRequest, schema: type[T], *, prefer: str | None = None) -> T:
        estimate = estimate_tokens(request.system + request.user) + request.max_output_tokens // 2
        last_error: Exception | None = None
        waited = 0.0
        while True:
            min_wait: float | None = None
            for index, model in enumerate(self._order(prefer)):
                wait = await self.budget.try_reserve(model, estimate)
                if wait > 0:
                    min_wait = wait if min_wait is None else min(min_wait, wait)
                    continue
                if index > 0 or last_error is not None:
                    self.stats.fallbacks += 1
                result = await self._attempt(request, schema, model)
                if isinstance(result, BaseModel):
                    return result
                last_error = result
                if isinstance(result, LLMConfigError):
                    raise result  # credentials/model invalid: no point trying further
                if isinstance(result, LLMRateLimitError):
                    await self.budget.cooldown(model, result.retry_after)
            if min_wait is None or waited + min_wait > MAX_WAIT_SECONDS:
                break
            logger.info("llm_waiting_for_budget", seconds=round(min_wait, 1), stage=request.stage)
            await asyncio.sleep(min_wait)
            waited += min_wait
        raise last_error or LLMError("All models are rate limited", provider=self.provider.name)

    async def _attempt(self, request: LLMRequest, schema: type[T], model: str) -> T | LLMError:
        self.stats.calls += 1
        try:
            response = await self.provider.complete(request, model)
        except LLMError as exc:
            self.stats.failures += 1
            self._record(request, model, None, exc)
            logger.warning(
                "llm_call_failed", stage=request.stage, model=model, error=type(exc).__name__
            )
            return exc
        try:
            parsed = parse_json_output(response.text, schema)
        except LLMBadOutputError as exc:
            self.stats.failures += 1
            self._record(request, model, response, exc)
            logger.warning("llm_bad_output", stage=request.stage, model=model)
            return exc
        self._record(request, model, response, None)
        return parsed

    @property
    def total_cost(self) -> Decimal:
        return sum((u.cost_usd for u in self.stats.usage), Decimal(0))
