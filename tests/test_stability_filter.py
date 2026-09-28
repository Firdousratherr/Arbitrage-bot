from app.exchanges.base import Opportunity
from app.filters import matches


def _opportunity(history):
    return Opportunity(
        "BTC/USDT",
        "buy",
        "sell",
        100.0,
        101.0,
        1.0,
        0.8,
        1_000_000.0,
        1_000_000.0,
        metadata={"history": history},
    )


def test_default_stability_allows_first_observation():
    assert matches(_opportunity([{"spread": 1.0, "net": 0.8}]), {"min_profit": 0.5, "max_profit": 100, "min_spread": 0, "max_spread": 100, "min_volume": 10000, "trade_size": 1000, "network_fee": 0, "fee_adjusted": True, "watchlist": [], "blacklist": [], "quote_currency": "USDT", "min_stable_observations": 1})


def test_stability_filter_rejects_insufficient_history():
    filters = {
        "min_profit": 0.5,
        "max_profit": 100,
        "min_spread": 0,
        "max_spread": 100,
        "min_volume": 10000,
        "trade_size": 1000,
        "network_fee": 0,
        "fee_adjusted": True,
        "watchlist": [],
        "blacklist": [],
        "quote_currency": "USDT",
        "min_stable_observations": 3,
    }
    result = matches(_opportunity([{"spread": 1.0, "net": 0.8}]), filters)
    assert result is False


def test_stability_filter_allows_required_history():
    filters = {
        "min_profit": 0.5,
        "max_profit": 100,
        "min_spread": 0,
        "max_spread": 100,
        "min_volume": 10000,
        "trade_size": 1000,
        "network_fee": 0,
        "fee_adjusted": True,
        "watchlist": [],
        "blacklist": [],
        "quote_currency": "USDT",
        "min_stable_observations": 3,
    }
    history = [
        {"spread": 1.0, "net": 0.8},
        {"spread": 1.1, "net": 0.9},
        {"spread": 1.05, "net": 0.85},
    ]
    assert matches(_opportunity(history), filters)
