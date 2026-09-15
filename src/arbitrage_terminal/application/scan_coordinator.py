from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass
class _ScanJob:
    task: asyncio.Task | None = None
    listeners: set[ProgressCallback] = field(default_factory=set)


class ConcurrentScanCoordinator:
    """Coalesce identical scans while keeping Telegram users independent."""

    def __init__(self):
        self._jobs: dict[str, _ScanJob] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, producer: Callable[[ProgressCallback | None], Awaitable[Any]], progress: ProgressCallback | None = None) -> Any:
        async with self._lock:
            job = self._jobs.get(key)
            if job is None or job.task is None or job.task.done():
                listeners: set[ProgressCallback] = set()
                job = _ScanJob(listeners=listeners)
                # Register the first listener BEFORE starting the producer. This
                # avoids a race where a very-fast producer emits its first event
                # before the caller has been attached.
                if progress is not None:
                    listeners.add(progress)
                job.task = asyncio.create_task(self._produce(producer, listeners))
                self._jobs[key] = job
            elif progress is not None:
                job.listeners.add(progress)
            task = job.task

        try:
            return await asyncio.shield(task)
        finally:
            async with self._lock:
                current = self._jobs.get(key)
                if current is job and progress is not None:
                    current.listeners.discard(progress)
                if current is job and current.task is not None and current.task.done() and not current.listeners:
                    self._jobs.pop(key, None)

    async def _produce(self, producer, listeners: set[ProgressCallback]):
        async def broadcast(stage: str, data: dict[str, Any]):
            for callback in tuple(listeners):
                asyncio.create_task(self._safe_progress(callback, stage, data))
        return await producer(broadcast)

    @staticmethod
    async def _safe_progress(callback, stage, data):
        try:
            await asyncio.wait_for(callback(stage, data), timeout=1.0)
        except Exception:
            pass

    async def close(self):
        async with self._lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            if job.task is not None and not job.task.done():
                job.task.cancel()
        if jobs:
            await asyncio.gather(*(job.task for job in jobs if job.task is not None), return_exceptions=True)
