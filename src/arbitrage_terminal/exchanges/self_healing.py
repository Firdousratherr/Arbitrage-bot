from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .base import ExchangeError

logger = logging.getLogger(__name__)
TRANSIENT = {"network", "rate_limit", "exchange_api", "timeout", "circuit_open"}
FATAL = {"authentication", "invalid_request"}
LIFECYCLE_METHODS = {"close", "disconnect"}

@dataclass(slots=True)
class ExchangeHealth:
    exchange: str
    state: str = "healthy"
    consecutive_failures: int = 0
    total_failures: int = 0
    total_repairs: int = 0
    total_recoveries: int = 0
    last_error: str | None = None
    last_error_type: str | None = None
    last_error_at: float | None = None
    last_success_at: float | None = None
    last_repair_at: float | None = None
    quarantined_until: float = 0.0
    recent_latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    @property
    def available(self) -> bool:
        return self.state != "quarantined" or time.monotonic() >= self.quarantined_until
    @property
    def score(self) -> float:
        latency = sum(self.recent_latencies_ms) / len(self.recent_latencies_ms) if self.recent_latencies_ms else 0.0
        score = 100.0 - min(45.0, self.consecutive_failures * 15.0) - min(25.0, self.total_failures / max(1, self.total_failures + self.total_repairs) * 25.0)
        if latency > 500: score -= min(20.0, (latency - 500) / 100.0)
        return max(0.0, min(100.0, round(score, 1)))

class SelfHealingAdapter:
    """Transparent adapter supervisor with bounded, serialized recovery."""
    def __init__(self, adapter: Any, repair: Callable[[], Awaitable[Any]] | None = None, failure_threshold: int = 3, quarantine_seconds: float = 30.0, max_repair_attempts: int = 2, recovery_advisor: Any = None):
        self._adapter = adapter
        self._repair = repair
        self.recovery_advisor = recovery_advisor
        self.failure_threshold = max(1, failure_threshold)
        self.quarantine_seconds = max(1.0, quarantine_seconds)
        self.max_repair_attempts = max(1, max_repair_attempts)
        self.health = ExchangeHealth(getattr(adapter, "name", "exchange"))
        self._recovery_lock = asyncio.Lock()
    @property
    def name(self): return self._adapter.name
    @property
    def adapter(self): return self._adapter
    async def _do_repair(self) -> bool:
        for attempt in range(self.max_repair_attempts):
            try:
                result = self._repair() if self._repair is not None else self._adapter.repair()
                if inspect.isawaitable(result): await result
                self.health.total_repairs += 1
                self.health.last_repair_at = time.monotonic()
                return True
            except Exception as exc:
                logger.warning("exchange repair failed", extra={"exchange": self.name, "attempt": attempt + 1, "error": str(exc)[:300]})
        return False
    async def _perform_deterministic_recovery(self) -> bool:
        """Run one recovery while the caller owns _recovery_lock."""
        self.health.state = "recovering"
        if not await self._do_repair():
            self.health.state = "quarantined"
            self.health.quarantined_until = time.monotonic() + self.quarantine_seconds
            return False
        try:
            await asyncio.wait_for(self._adapter.health_check(), timeout=min(10.0, self.quarantine_seconds))
        except Exception as exc:
            self.health.state = "quarantined"
            self.health.quarantined_until = time.monotonic() + self.quarantine_seconds
            self.health.last_error = str(exc)[:500]
            self.health.last_error_type = type(exc).__name__
            self.health.last_error_at = time.monotonic()
            return False
        self.health.state = "healthy"
        self.health.consecutive_failures = 0
        self.health.total_recoveries += 1
        self.health.last_success_at = time.monotonic()
        return True
    async def _deterministic_recover(self) -> bool:
        async with self._recovery_lock:
            if self.health.state == "healthy": return True
            return await self._perform_deterministic_recovery()
    async def _ai_recover(self, original_error: Exception) -> bool:
        if self.recovery_advisor is None: return False
        try:
            decision = await self.recovery_advisor.advise({"exchange": self.name, "error_type": getattr(original_error, "error_type", type(original_error).__name__), "error": str(original_error)[:500], "consecutive_failures": self.health.consecutive_failures, "total_failures": self.health.total_failures, "state": self.health.state, "recent_latency_ms": [round(x, 1) for x in self.health.recent_latencies_ms]})
            if decision is None or not decision.safe_to_auto_repair: return False
            if decision.retry_delay_seconds: await asyncio.sleep(decision.retry_delay_seconds)
            if decision.recommended_action == "retry":
                self.health.state = "healthy"
                return True
            if decision.recommended_action == "quarantine":
                self.health.state = "quarantined"
                self.health.quarantined_until = time.monotonic() + self.quarantine_seconds
                return False
            if decision.recommended_action in {"repair", "reload_markets", "invalidate_cache"}:
                return await self._deterministic_recover()
            return False
        except Exception:
            logger.exception("AI exchange recovery advisor failed", extra={"exchange": self.name}); return False
    async def _recover(self, original_error: Exception | None = None) -> bool:
        """Serialize recovery and let concurrent callers reuse one successful repair."""
        async with self._recovery_lock:
            if self.health.state == "healthy":
                if original_error is None or self.health.consecutive_failures == 0:
                    return True
            deterministic_ok = await self._perform_deterministic_recovery()
            if deterministic_ok:
                return True
        return original_error is not None and await self._ai_recover(original_error)
    async def _call(self, method_name: str, method: Callable[..., Any], *args, **kwargs):
        if self.health.state == "quarantined":
            if time.monotonic() < self.health.quarantined_until: raise ExchangeError("Exchange quarantined while self-healing is in progress.", "circuit_open")
            if not await self._recover(): raise ExchangeError("Exchange recovery probe failed; still quarantined.", "circuit_open")
        for attempt in range(2):
            started = time.perf_counter()
            try:
                result = method(*args, **kwargs)
                if inspect.isawaitable(result): result = await result
                self.health.recent_latencies_ms.append((time.perf_counter() - started) * 1000); self.health.consecutive_failures = 0; self.health.last_success_at = time.monotonic(); self.health.state = "healthy"; return result
            except Exception as exc:
                self.health.recent_latencies_ms.append((time.perf_counter() - started) * 1000); self.health.total_failures += 1; self.health.consecutive_failures += 1; self.health.last_error = str(exc)[:500]; self.health.last_error_type = getattr(exc, "error_type", type(exc).__name__); self.health.last_error_at = time.monotonic()
                error_type = self.health.last_error_type
                if not (isinstance(exc, asyncio.TimeoutError) or error_type in TRANSIENT) or error_type in FATAL: raise
                if self.health.consecutive_failures >= self.failure_threshold:
                    if not await self._recover(exc): raise ExchangeError("Exchange recovery failed; exchange quarantined.", "circuit_open") from exc
                    if attempt + 1 < 2: continue
                if attempt + 1 >= 2: raise
                await asyncio.sleep(0.2 * (2 ** attempt))
    def __getattr__(self, name: str):
        target = getattr(self._adapter, name)
        if not callable(target) or name in {"repair", "health", *LIFECYCLE_METHODS}: return target
        async def wrapped(*args, **kwargs): return await self._call(name, target, *args, **kwargs)
        return wrapped
    async def repair(self) -> bool: return await self._recover()
    def health_snapshot(self) -> dict[str, Any]:
        h = self.health
        return {"exchange": h.exchange, "state": h.state, "score": h.score, "available": h.available, "consecutive_failures": h.consecutive_failures, "total_failures": h.total_failures, "total_repairs": h.total_repairs, "total_recoveries": h.total_recoveries, "last_error": h.last_error, "last_error_type": h.last_error_type, "last_success_at": h.last_success_at, "last_repair_at": h.last_repair_at, "quarantined_until": h.quarantined_until}

class ExchangeSelfHealingSupervisor:
    def __init__(self, adapters: dict[str, Any], failure_threshold: int = 3, quarantine_seconds: float = 30.0, recovery_advisor: Any = None):
        self.adapters = {name: adapter if isinstance(adapter, SelfHealingAdapter) else SelfHealingAdapter(adapter, failure_threshold=failure_threshold, quarantine_seconds=quarantine_seconds, recovery_advisor=recovery_advisor) for name, adapter in adapters.items()}
    def health(self) -> dict[str, dict[str, Any]]: return {name: adapter.health_snapshot() for name, adapter in self.adapters.items()}
    async def recover(self, name: str) -> bool:
        adapter = self.adapters.get(name); return False if adapter is None else await adapter.repair()
