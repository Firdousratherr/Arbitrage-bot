import asyncio

import pytest

from arbitrage_terminal.exchanges.base import ExchangeError
from arbitrage_terminal.exchanges.self_healing import SelfHealingAdapter


class FakeAdapter:
    name = 'fake'
    def __init__(self): self.failures_left = 0; self.repairs = 0; self.probes = 0; self.closes = 0
    async def health_check(self): self.probes += 1
    async def repair(self): self.repairs += 1; self.failures_left = 0
    async def get_markets(self):
        if self.failures_left:
            self.failures_left -= 1
            raise ExchangeError('temporary outage', 'network')
        return ['ok']
    async def close(self): self.closes += 1

@pytest.mark.asyncio
async def test_transient_failure_retries_without_repairing_healthy_exchange():
    adapter = FakeAdapter(); adapter.failures_left = 1; wrapped = SelfHealingAdapter(adapter, failure_threshold=3)
    assert await wrapped.get_markets() == ['ok']; assert adapter.repairs == 0; assert wrapped.health.consecutive_failures == 0

@pytest.mark.asyncio
async def test_repeated_failures_trigger_repair_and_health_probe():
    adapter = FakeAdapter(); adapter.failures_left = 10; wrapped = SelfHealingAdapter(adapter, failure_threshold=1, quarantine_seconds=1)
    assert await wrapped.get_markets() == ['ok']; assert adapter.repairs == 1; assert adapter.probes == 1; assert wrapped.health.total_repairs == 1; assert wrapped.health.total_recoveries == 1; assert wrapped.health.state == 'healthy'

@pytest.mark.asyncio
async def test_concurrent_failures_share_one_recovery():
    class SlowRepair(FakeAdapter):
        async def repair(self):
            self.repairs += 1; await asyncio.sleep(0.03); self.failures_left = 0
    adapter = SlowRepair(); adapter.failures_left = 20; wrapped = SelfHealingAdapter(adapter, failure_threshold=1)
    results = await asyncio.gather(*(wrapped.get_markets() for _ in range(10)))
    assert results == [['ok']] * 10; assert adapter.repairs == 1; assert adapter.probes == 1; assert wrapped.health.total_recoveries == 1

@pytest.mark.asyncio
async def test_lifecycle_close_bypasses_self_healing():
    adapter = FakeAdapter(); wrapped = SelfHealingAdapter(adapter, failure_threshold=1)
    await wrapped.close()
    assert adapter.closes == 1; assert adapter.repairs == 0; assert adapter.probes == 0

@pytest.mark.asyncio
async def test_authentication_errors_are_not_self_repaired():
    class AuthAdapter(FakeAdapter):
        async def get_markets(self): raise ExchangeError('bad credentials', 'authentication', 401)
    adapter = AuthAdapter(); wrapped = SelfHealingAdapter(adapter, failure_threshold=1)
    with pytest.raises(ExchangeError): await wrapped.get_markets()
    assert adapter.repairs == 0

@pytest.mark.asyncio
async def test_repair_failure_quarantines_exchange():
    class BrokenRepair(FakeAdapter):
        async def repair(self): self.repairs += 1; raise RuntimeError('cannot recreate client')
    adapter = BrokenRepair(); adapter.failures_left = 1; wrapped = SelfHealingAdapter(adapter, failure_threshold=1, quarantine_seconds=10)
    with pytest.raises(ExchangeError): await wrapped.get_markets()
    assert adapter.repairs == 2; assert wrapped.health.state == 'quarantined'; assert not wrapped.health.available

@pytest.mark.asyncio
async def test_ai_retry_can_restore_transient_exchange_without_trade_access():
    class Unreliable(FakeAdapter):
        async def repair(self): self.repairs += 1; raise RuntimeError('client recreation unavailable')
    class Advisor:
        async def advise(self, snapshot):
            assert snapshot['exchange'] == 'fake'
            assert 'api_key' not in snapshot and 'secret' not in snapshot
            return type('Decision', (), {'safe_to_auto_repair': True, 'recommended_action': 'retry', 'retry_delay_seconds': 0})()
    adapter = Unreliable(); adapter.failures_left = 1
    wrapped = SelfHealingAdapter(adapter, failure_threshold=1, recovery_advisor=Advisor())
    assert await wrapped.get_markets() == ['ok']
    assert adapter.repairs == 2
