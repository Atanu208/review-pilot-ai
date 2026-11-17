"""Per-model tokens-per-minute budgeting and cooldowns, shared across workers via Redis.

Free tiers (e.g. Groq: ~8k tokens/minute per model) are easy to exceed with parallel
reviewers. Before each call we reserve the estimated tokens in the current minute window;
if the window is full we either wait for the next one or let the router try another model.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)


class TokenBudget:
    def __init__(self, redis: Redis | None, tokens_per_minute: int) -> None:
        self._redis = redis
        self._tpm = tokens_per_minute

    @property
    def enabled(self) -> bool:
        return self._redis is not None and self._tpm > 0

    async def try_reserve(self, model: str, tokens: int) -> float:
        """Reserve ``tokens`` for ``model``; return 0 on success, else seconds to wait."""
        if not self.enabled:
            return 0.0
        if self._redis is None:
            raise RuntimeError("self._redis is missing")
        now = time.time()
        if await self._redis.exists(f"llm:cooldown:{model}"):
            ttl = await self._redis.pttl(f"llm:cooldown:{model}")
            return max(ttl, 0) / 1000 or 1.0
        window = int(now // 60)
        key = f"llm:tpm:{model}:{window}"
        used = await self._redis.incrby(key, tokens)
        await self._redis.expire(key, 120)
        if used > self._tpm and used != tokens:  # a single oversize request is still allowed
            await self._redis.decrby(key, tokens)
            return 60 - (now % 60) + 0.5
        return 0.0

    async def cooldown(self, model: str, seconds: float) -> None:
        if self._redis is None:
            return
        await self._redis.set(f"llm:cooldown:{model}", "1", px=max(int(seconds * 1000), 1000))
        logger.info("llm_model_cooldown", model=model, seconds=round(seconds, 1))
