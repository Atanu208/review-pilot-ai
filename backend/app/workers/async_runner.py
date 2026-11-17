"""Run async service code from synchronous Celery tasks.

Each worker process keeps one event loop for its lifetime so that pooled resources bound to
a loop (asyncpg connections, the shared httpx client, redis.asyncio) stay valid across tasks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
    return _loop.run_until_complete(coro)
