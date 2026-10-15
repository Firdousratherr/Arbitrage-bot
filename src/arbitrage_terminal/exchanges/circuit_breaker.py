from __future__ import annotations
import time


class CircuitBreaker:
    def __init__(self, failures: int = 3, cooldown: float = 30.0):
        self.failure_limit = failures
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = 0.0

    @property
    def available(self) -> bool:
        if not self.opened_at:
            return True
        if time.monotonic() - self.opened_at >= self.cooldown:
            self.opened_at = 0.0
            self.failures = 0
            return True
        return False

    def success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_limit:
            self.opened_at = time.monotonic()
