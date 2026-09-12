import pytest
from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Market,Ticker,MarketType,ScanState
from arbitrage_terminal.domain.filters import ScanFilters
from arbitrage_terminal.arbitrage.scanner import ArbitrageScanner
from arbitrage_terminal.exchanges.base import ExchangeAdapter,ExchangeError


class Fake(ExchangeAdapter):
    def __init__(self,name,price=100,fail=False):self.name=name;self.price=price;self.fail=fail
    async def health_check(self):
        if self.fail:raise ExchangeError('boom','network')
    async def get_markets(self):
        if self.fail:raise ExchangeError('boom','network')
        return [Market(self.name,'BTC/USDT','BTC','USDT',MarketType.SPOT)]
    async def get_tickers(self,symbols=None):
        if self.fail:raise ExchangeError('boom','network')
        return [Ticker(self.name,'BTC/USDT','BTC','USDT',self.price-1,self.price,100000,datetime.now(timezone.utc))]
    async def get_orderbook(self,symbol,limit=10):return {'bids':[[self.price-1,1]],'asks':[[self.price,1]]}
    async def get_trading_fees(self,symbols=None):return {'BTC/USDT':.1}
    async def get_transfer_info(self,asset):return {'available':True,'networks':[{'network':'ERC20','deposit':True,'withdraw':True,'contract_address':'0xabc'}]}
    async def close(self):pass


@pytest.mark.asyncio
async def test_partial_failure():
    s=ArbitrageScanner({'a':Fake('a',100),'b':Fake('b',102),'c':Fake('c',100,True)})
    x=await s.scan(1,['a','b','c'],ScanFilters(min_gap=0,min_net_profit=0,min_volume=0,min_liquidity=0))
    assert x.state==ScanState.PARTIAL and x.opportunities_found>0 and 'c' in x.failed_exchanges


@pytest.mark.asyncio
async def test_zero_result():
    s=ArbitrageScanner({'a':Fake('a',100),'b':Fake('b',100)})
    x=await s.scan(1,['a','b'],ScanFilters(min_gap=1,min_net_profit=1,min_volume=0,min_liquidity=0))
    assert x.state==ScanState.SUCCESS and x.opportunities_found==0


@pytest.mark.asyncio
async def test_orderbook_validation_preserves_ticker_liquidity_and_reprices_net_profit():
    class Deep(Fake):
        async def get_orderbook(self,symbol,limit=10):
            return {'bids':[[self.price,20]],'asks':[[self.price-2,20]]}

    s=ArbitrageScanner({'a':Deep('a',100),'b':Deep('b',102)})
    filters=ScanFilters(min_gap=0,min_net_profit=1,min_volume=10000,min_liquidity=1000,trade_size=1000,require_orderbook=True)
    x=await s.scan(1,['a','b'],filters)
    assert x.state==ScanState.SUCCESS
    assert x.opportunities_found==1
    o=x.opportunities[0]
    assert o.metadata['orderbook_validated'] is True
    assert o.buy_volume == 100000
    assert o.sell_volume == 100000
    assert o.estimated_net_profit is not None
    assert o.estimated_net_profit == pytest.approx(o.metadata['executable_gap_pct'] - .2)


@pytest.mark.asyncio
async def test_degraded_fee_data_marks_scan_partial():
    class NoFees(Fake):
        async def get_trading_fees(self,symbols=None):
            raise ExchangeError('fee endpoint unavailable','exchange_api')

    s=ArbitrageScanner({'a':NoFees('a',100),'b':Fake('b',102)})
    x=await s.scan(1,['a','b'],ScanFilters(min_gap=0,min_net_profit=0,min_volume=0,min_liquidity=0))
    assert x.state==ScanState.PARTIAL
    assert 'a' in x.degraded_exchanges
