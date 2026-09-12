from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.arbitrage.engine import pair_opportunity
from arbitrage_terminal.domain.filters import ScanFilters

def ticker(exchange,price):
    return Ticker(exchange,'BTC/USDT','BTC','USDT',price-1,price,100000,datetime.now(timezone.utc))

def test_strict_requires_network_and_contract_match():
    o=pair_opportunity(ticker('buy',100),ticker('sell',102),.1,.1,transfer={'network_available':False,'contract_match':False,'networks':[]})
    assert ScanFilters(min_net_profit=.1,validation_mode='strict').check(o) == 'deposit/withdrawal or network validation unavailable'

def test_strict_accepts_verified_common_network():
    o=pair_opportunity(ticker('buy',100),ticker('sell',102),.1,.1,transfer={'network_available':True,'contract_match':True,'networks':['ERC20']})
    assert ScanFilters(min_net_profit=.1,validation_mode='strict').check(o) is None
    assert o.metadata['transfer_verified'] is True

def test_loose_accepts_unverified_transfer():
    o=pair_opportunity(ticker('buy',100),ticker('sell',102),.1,.1,transfer={'network_available':False,'contract_match':False,'networks':[]})
    assert ScanFilters(min_net_profit=.1,validation_mode='loose').check(o) is None
    assert o.metadata['transfer_verified'] is False

def test_missing_fee_never_becomes_zero_profit():
    o=pair_opportunity(ticker('buy',100),ticker('sell',102),None,.1,transfer={'network_available':True,'contract_match':True,'networks':['ERC20']})
    assert o.estimated_net_profit is None
    assert ScanFilters(min_net_profit=.1,validation_mode='loose').check(o).startswith('net profit unavailable')
