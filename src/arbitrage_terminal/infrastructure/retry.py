from __future__ import annotations
import asyncio, random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar('T')

async def retry_async(fn: Callable[[], Awaitable[T]], retries: int = 2, base_delay: float = 0.25) -> T:
    last = None
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as exc:
            last = exc
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt) + random.random() * 0.1)
    raise last
