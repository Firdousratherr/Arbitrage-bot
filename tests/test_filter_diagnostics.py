from app.exchanges.base import Opportunity
from app.filters import clear_filter_rejections, get_filter_rejections, matches


def _filters(**overrides):
    values = {
        "min_profit": 0.5,
        "max_profit": 100.0,
        "min_spread": 0.0,
        "max_spread": 100.0,
        "min_volume": 10000.0,
        "watchlist": [],
        "blacklist": [],
        "quote_currency": "USDT",
        "fee_adjusted": True,
    }
    values.update(overrides)
    return values


def _opportunity(symbol="TEST/USDT", raw_spread=2.0, net_profit=0.25, volume_buy=100000, volume_sell=100000):
    return Opportunity(
        symbol=symbol,
        buy_exchange="lbank",
        sell_exchange="xt",
        buy_price=1.0,
        sell_price=1.02,
        raw_spread=raw_spread,
        net_profit=net_profit,
        volume_buy=volume_buy,
        volume_sell=volume_sell,
    )


def test_exact_filter_rejection_reason_is_captured_with_gap():
    clear_filter_rejections()
    opportunity = _opportunity()
    assert not matches(opportunity, _filters(min_profit=0.5))
    reason = get_filter_rejections()["TEST/USDT"]
    assert "gap 2.00% (lbank→xt)" in reason
    assert "net 0.25%" in reason
    assert "rejected: net profit 0.25%" in reason


def test_volume_rejection_is_captured_with_gap():
    clear_filter_rejections()
    opportunity = _opportunity(net_profit=2.0, volume_buy=100, volume_sell=200)
    assert not matches(opportunity, _filters(min_volume=10000))
    reason = get_filter_rejections()["TEST/USDT"]
    assert "gap 2.00% (lbank→xt)" in reason
    assert "volume" in reason
