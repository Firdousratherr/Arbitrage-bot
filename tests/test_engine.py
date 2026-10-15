from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.arbitrage.engine import pair_opportunity

def t(ex,p,asset_identity=None):
    return Ticker(ex,'BTC/USDT','BTC','USDT',p-1,p,100000,datetime.now(timezone.utc),asset_identity)

def test_net_profit_separate():
    o=pair_opportunity(t('a',100),t('b',102),.1,.1);assert o.raw_gap>0;assert o.estimated_net_profit<o.raw_gap

def test_rejects_mismatched_explicit_asset_identity():
    assert pair_opportunity(t('a',100,'BTC|contract:0xaaa'),t('b',102,'BTC|contract:0xbbb'),.1,.1) is None

def test_rejects_identity_present_on_only_one_side():
    assert pair_opportunity(t('a',100,'BTC|contract:0xaaa'),t('b',102),.1,.1) is None

def test_rejects_extreme_unverified_gap():
    assert pair_opportunity(t('a',1),t('b',20),.1,.1) is None

def test_allows_matching_explicit_asset_identity():
    o=pair_opportunity(t('a',100,'BTC|contract:0xaaa'),t('b',102,'BTC|contract:0xaaa'),.1,.1)
    assert o is not None
    assert o.metadata['asset_identity_verified'] is True
