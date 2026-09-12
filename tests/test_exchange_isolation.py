import pytest
from arbitrage_terminal.exchanges.circuit_breaker import CircuitBreaker


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(failures=2, cooldown=60)
    assert cb.available
    cb.failure()
    assert cb.available
    cb.failure()
    assert not cb.available


def test_circuit_breaker_success_resets():
    cb = CircuitBreaker(failures=1, cooldown=60)
    cb.failure()
    assert not cb.available
    cb.success()
    assert cb.available
