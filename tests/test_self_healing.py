import asyncio

import pytest

from arbitrage_terminal.exchanges.base import ExchangeError
from arbitrage_terminal.exchanges.self_healing import SelfHealingAdapter


class FakeAdapter:
    name = 'fake'

    def __init__(self):
        self.failures_left = 0
        self.repairs = 0
        self.probes = 0

    async def health_check(self):
        self.probes += 1

    async def repair(self):
        self.repairs += 1
        self.failures_left = 0

    async def get_markets(self):
        if self.failures_left:
            self.failures_left -= 1
            raise ExchangeError('temporary outage', 'network')
        return ['ok']

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_transient_failure_retries_without_repairing_healthy_exchange():
    adapter = FakeAdapter()
    adapter.failures_left = 1
    wrapped = SelfHealingAdapter(adapter, failure_threshold=3)

    assert await wrapped.get_markets() == ['ok']
    assert adapter.repairs == 0
    assert wrapped.health.consecutive_failures == 0


@pytest.mark.asyncio
async def test_repeated_failures_trigger_repair_and_health_probe():
    adapter = FakeAdapter()
    adapter.failures_left = 10
    wrapped = SelfHealingAdapter(adapter, failure_threshold=1, quarantine_seconds=1)

    with pytest.raises(ExchangeError):
        await wrapped.get_markets()

    assert adapter.repairs == 1
    assert adapter.probes == 1
    assert wrapped.health.total_repairs == 1
    assert wrapped.health.total_recoveries == 1
    assert wrapped.health.state == 'healthy'


@pytest.mark.asyncio
async def test_authentication_errors_are_not_self_repaired():
    class AuthAdapter(FakeAdapter):
        async def get_markets(self):
            raise ExchangeError('bad credentials', 'authentication', 401)

    adapter = AuthAdapter()
    wrapped = SelfHealingAdapter(adapter, failure_threshold=1)

    with pytest.raises(ExchangeError):
        await wrapped.get_markets()

    assert adapter.repairs == 0


@pytest.mark.asyncio
async def test_repair_failure_quarantines_exchange():
    class BrokenRepair(FakeAdapter):
        async def repair(self):
            self.repairs += 1
            raise RuntimeError('cannot recreate client')

    adapter = BrokenRepair()
    wrapped = SelfHealingAdapter(adapter, failure_threshold=1, quarantine_seconds=10)

    with pytest.raises(ExchangeError):
        await wrapped.get_markets()

    assert wrapped.health.state == 'quarantined'
    assert not wrapped.health.available
