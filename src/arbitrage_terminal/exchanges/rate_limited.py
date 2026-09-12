from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class RateLimitedExchangeAdapter:
    """Bound exchange I/O and coalesce very-fresh read-only market data."""

    DEFAULT_CONCURRENCY = 3
    MARKET_TTL = 10.0
    TICKER_TTL = 2.0

    def __init__(self, adapter: Any, concurrency: int | None = None):
        self._adapter = adapter
        self._semaphore = asyncio.Semaphore(max(1, concurrency or self.DEFAULT_CONCURRENCY))
        self._cache_lock = asyncio.Lock()
        self._cache: dict[tuple[str, Any], tuple[float, Any]] = {}
        self._inflight: dict[tuple[str, Any], asyncio.Task] = {}

    def __getattr__(self, name: str):
        return getattr(self._adapter, name)

    async def _cached_call(self, key: tuple[str, Any], ttl: float, fn: Callable[[], Awaitable[Any]]):
        now = time.monotonic()
        async with self._cache_lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._run_and_cache(key, ttl, fn))
                self._inflight[key] = task
        return await asyncio.shield(task)

    async def _run_and_cache(self, key, ttl, fn):
        try:
            async with self._semaphore:
                value = await fn()
            async with self._cache_lock:
                self._cache[key] = (time.monotonic() + ttl, value)
            return value
        finally:
            async with self._cache_lock:
                self._inflight.pop(key, None)

    async def get_markets(self):
        return await self._cached_call(('markets', None), self.MARKET_TTL, self._adapter.get_markets)

    async def get_tickers(self, symbols):
        normalized = tuple(sorted(set(symbols)))
        return await self._cached_call(('tickers', normalized), self.TICKER_TTL, lambda: self._adapter.get_tickers(set(normalized)))

    async def repair(self):
        repair = getattr(self._adapter, 'repair', None)
        if repair is None:
            raise AttributeError('Underlying exchange adapter does not support repair')
        result = repair()
        if asyncio.iscoroutine(result):
            await result
        await self.invalidate_market_data_cache()

    async def invalidate_market_data_cache(self):
        async with self._cache_lock:
            self._cache.clear()

    async def close(self):
        close = getattr(self._adapter, 'close', None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result
