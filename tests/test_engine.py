from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.arbitrage.engine import pair_opportunity, withdrawal_cost_pct

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

def test_withdrawal_cost_pct_uses_base_asset_fee_and_chooses_cheapest_route():
    info={'networks': {'TRC20': {'fee': '1.5'}, 'ERC20': {'fee': '0.01'}}}
    assert withdrawal_cost_pct(info, ['TRC20', 'ERC20'], 10.0) == 0.1

def test_withdrawal_cost_pct_returns_none_when_all_usable_fees_unknown():
    info={'networks': {'TRC20': {'fee': None}}}
    assert withdrawal_cost_pct(info, ['TRC20'], 10.0) is None
