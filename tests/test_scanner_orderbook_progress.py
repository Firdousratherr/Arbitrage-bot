import asyncio
import time
from datetime import datetime, timezone

import pytest

from arbitrage_terminal.arbitrage.scanner import ArbitrageScanner
from arbitrage_terminal.domain.filters import ScanFilters
from arbitrage_terminal.domain.models import Market, MarketType, Ticker
from arbitrage_terminal.exchanges.base import ExchangeAdapter


class SlowOrderbookExchange(ExchangeAdapter):
    def __init__(self, name, price, delay=0.25):
        self.name = name
        self.price = price
        self.delay = delay
        self.active_orderbooks = 0
        self.max_active_orderbooks = 0

    async def health_check(self):
        return None

    async def get_markets(self):
        return [Market(self.name, 'BTC/USDT', 'BTC', 'USDT', MarketType.SPOT)]

    async def get_tickers(self, symbols=None):
        return [Ticker(self.name, 'BTC/USDT', 'BTC', 'USDT', self.price - 1, self.price, 100000, datetime.now(timezone.utc))]

    async def get_orderbook(self, symbol, limit=10):
        self.active_orderbooks += 1
        self.max_active_orderbooks = max(self.max_active_orderbooks, self.active_orderbooks)
        try:
            await asyncio.sleep(self.delay)
            return {'bids': [[self.price, 20]], 'asks': [[self.price - 2, 20]]}
        finally:
            self.active_orderbooks -= 1

    async def get_trading_fees(self, symbols=None):
        return {'BTC/USDT': 0.1}

    async def get_transfer_info(self, asset):
        return {'available': True, 'networks': [{'network': 'ERC20', 'deposit': True, 'withdraw': True, 'fee': 0.0, 'contract_address': '0xabc'}]}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_orderbook_validation_is_concurrent_and_reports_progress():
    a = SlowOrderbookExchange('a', 100)
    b = SlowOrderbookExchange('b', 102)
    scanner = ArbitrageScanner({'a': a, 'b': b})
    filters = ScanFilters(min_gap=0, min_net_profit=0, min_volume=0, min_liquidity=0, trade_size=1000, require_orderbook=True)
    events = []

    async def progress(stage, data):
        events.append((stage, dict(data)))

    started = time.monotonic()
    snapshot = await scanner.scan(1, ['a', 'b'], filters, progress=progress)
    elapsed = time.monotonic() - started

    assert snapshot.opportunities_found == 1
    assert a.max_active_orderbooks + b.max_active_orderbooks >= 2
    assert elapsed < 0.70
    assert any(stage == 'candidates' and data.get('comparisons') == 1 for stage, data in events)
    # One arbitrage candidate is validated using two exchange order-book
    # requests; progress counts completed candidate validations.
    assert any(stage == 'orderbook' and data.get('validated') == 1 and data.get('total') == 1 for stage, data in events)
