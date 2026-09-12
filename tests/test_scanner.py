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
    s=ArbitrageScanner({'a':Fake('a',100),'b':Fake('b',102),'c':Fake('c',100,True)});x=await s.scan(1,['a','b','c'],ScanFilters(min_gap=0,min_net_profit=0,min_volume=0,min_liquidity=0));assert x.state==ScanState.PARTIAL and x.opportunities_found>0 and 'c' in x.failed_exchanges
@pytest.mark.asyncio
async def test_zero_result():
    s=ArbitrageScanner({'a':Fake('a',100),'b':Fake('b',100)});x=await s.scan(1,['a','b'],ScanFilters(min_gap=1,min_net_profit=1,min_volume=0,min_liquidity=0));assert x.state==ScanState.SUCCESS and x.opportunities_found==0
