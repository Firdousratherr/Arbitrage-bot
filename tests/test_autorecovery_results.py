from datetime import datetime, timezone

import pytest

from arbitrage_terminal.arbitrage.engine import pair_opportunity
from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.exchanges.self_healing import SelfHealingAdapter


class EmptyThenHealthyAdapter:
    name = 'fake'

    def __init__(self):
        self.repaired = 0
        self.ticker_calls = 0

    async def repair(self):
        self.repaired += 1

    async def health_check(self):
        return True

    async def get_tickers(self, symbols):
        self.ticker_calls += 1
        if self.repaired == 0:
            return []
        now = datetime.now(timezone.utc)
        return [Ticker('fake', 'BTC/USDT', 'BTC', 'USDT', 100.0, 100.5, 100000.0, now)]


@pytest.mark.asyncio
async def test_empty_ticker_payload_triggers_repair_and_retry():
    raw = EmptyThenHealthyAdapter()
    adapter = SelfHealingAdapter(raw)

    tickers = await adapter.get_tickers({'BTC/USDT'})

    assert len(tickers) == 1
    assert raw.repaired >= 1
    assert raw.ticker_calls >= 2
    assert adapter.health.state == 'healthy'


def test_asset_identity_conflict_does_not_hide_price_candidate():
    now = datetime.now(timezone.utc)
    buy = Ticker('a', 'ABC/USDT', 'ABC', 'USDT', 100.0, 100.0, 100000.0, now, 'ABC|contract:one')
    sell = Ticker('b', 'ABC/USDT', 'ABC', 'USDT', 102.0, 102.0, 100000.0, now, 'ABC|contract:two')

    opportunity = pair_opportunity(buy, sell, 0.1, 0.1, max_age=10)

    assert opportunity is not None
    assert opportunity.metadata['asset_identity_conflict'] is True
