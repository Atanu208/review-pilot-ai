"""Model prices (USD per 1M tokens) for cost tracking.

Anthropic prices are the first-party API rates (cache reads ~0.1x input, cache writes
~1.25x input). OpenAI-compatible hosts (Groq, Ollama, ...) vary and free tiers bill
nothing: unknown models are recorded with cost 0 and ``priced=False``. Override or extend
via ``LLM_PRICING`` (JSON: {"model": {"input": x, "output": y}}).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Price:
    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None


def _p(i: str, o: str, cache_read: str | None = None) -> Price:
    inp = Decimal(i)
    return Price(
        inp,
        Decimal(o),
        Decimal(cache_read) if cache_read else inp * Decimal("0.1"),
        inp * Decimal("1.25"),
    )


PRICES: dict[str, Price] = {
    "claude-opus-5-5": _p("4.00", "20.00", "0.20"),
    "claude-opus-5": _p("5.00", "25.00"),
    "claude-sonnet-5-5": _p("2.00", "10.00", "0.10"),
    "claude-sonnet-5": _p("2.00", "10.00"),
    "claude-haiku-5-5": _p("0.10", "0.50"),
    "claude-fable-5-2": _p("8.00", "40.00", "0.20"),
}


def _overrides() -> dict[str, Price]:
    raw = os.environ.get("LLM_PRICING")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {m: _p(str(v["input"]), str(v["output"])) for m, v in data.items()}
    except (ValueError, KeyError, TypeError):
        return {}


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[Decimal, bool]:
    """Return (cost, priced). ``input_tokens`` excludes cache reads/writes (Anthropic semantics)."""
    price = _overrides().get(model) or PRICES.get(model)
    if price is None:
        return Decimal(0), False
    million = Decimal(1_000_000)
    total = (
        Decimal(input_tokens) * price.input
        + Decimal(output_tokens) * price.output
        + Decimal(cached_input_tokens) * (price.cache_read or price.input)
        + Decimal(cache_write_tokens) * (price.cache_write or price.input)
    ) / million
    return total.quantize(Decimal("0.000001")), True
