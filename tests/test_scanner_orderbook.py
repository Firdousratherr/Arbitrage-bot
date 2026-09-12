import pytest
from datetime import datetime, timezone

from arbitrage_terminal.arbitrage.scanner import ArbitrageScanner
from arbitrage_terminal.domain.filters import ScanFilters
from arbitrage_terminal.domain.models import Market, MarketType, ScanState, Ticker
from arbitrage_terminal.exchanges.base import ExchangeAdapter


class DepthFake(ExchangeAdapter):
    def __init__(self, name, ask, bid):
        self.name, self.ask, self.bid = name, ask, bid

    async def health_check(self): pass
    async def get_markets(self):
        return [Market(self.name, 'BTC/USDT', 'BTC', 'USDT', MarketType.SPOT)]
    async def get_tickers(self, symbols=None):
        return [Ticker(self.name, 'BTC/USDT', 'BTC', 'USDT', self.bid, self.ask, 1_000_000, datetime.now(timezone.utc))]
    async def get_orderbook(self, symbol, limit=10):
        return {'bids': [[self.bid, 20]], 'asks': [[self.ask, 20]]}
    async def get_trading_fees(self, symbols=None): return {'BTC/USDT': 0.0}
    async def get_transfer_info(self, asset):
        return {'available': True, 'networks': [{'network': 'ERC20', 'deposit': True, 'withdraw': True, 'contract_address': '0xabc'}]}
    async def close(self): pass


@pytest.mark.asyncio
async def test_strict_orderbook_validation_uses_executable_prices():
    scanner = ArbitrageScanner({'buy': DepthFake('buy', 100, 99), 'sell': DepthFake('sell', 105, 104)})
    result = await scanner.scan(
        1,
        ['buy', 'sell'],
        ScanFilters(min_gap=0, min_net_profit=0, min_volume=0, min_liquidity=0, trade_size=1000, require_orderbook=True),
    )
    assert result.state == ScanState.SUCCESS
    assert result.opportunities_found == 1
    opportunity = result.opportunities[0]
    assert opportunity.metadata['orderbook_validated'] is True
    assert opportunity.metadata['executable_gap_pct'] > 0
    assert opportunity.metadata['executable_quote_size'] == pytest.approx(1000)


@pytest.mark.asyncio
async def test_orderbook_validation_rejects_insufficient_depth():
    scanner = ArbitrageScanner({'buy': DepthFake('buy', 100, 99), 'sell': DepthFake('sell', 105, 104)})
    result = await scanner.scan(
        1,
        ['buy', 'sell'],
        ScanFilters(min_gap=0, min_net_profit=0, min_volume=0, min_liquidity=0, trade_size=5000, require_orderbook=True),
    )
    assert result.opportunities_found == 0
    assert any('order-book' in r['reason'] or 'depth' in r['reason'] for r in result.filter_rejections)
