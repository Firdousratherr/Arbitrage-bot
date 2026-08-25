import asyncio
from pathlib import Path
import tempfile

from app.db import Database
from app.exchanges.base import Opportunity
from app.filters import match_reason, matches, user_filters


def _opportunity(**overrides):
    values = {
        "symbol": "BTC/USDT",
        "buy_exchange": "KuCoin",
        "sell_exchange": "Gate.io",
        "buy_price": 50000,
        "sell_price": 52500,
        "raw_spread": 5.0,
        "net_profit": 4.8,
        "volume_buy": 1000000,
        "volume_sell": 1000000,
    }
    values.update(overrides)
    return Opportunity(**values)


def test_matches_applies_all_filters():
    opportunity = _opportunity()
    filters = {
        "min_profit": 3.0,
        "max_profit": 10.0,
        "min_spread": 4.0,
        "max_spread": 10.0,
        "min_volume": 500000,
        "watchlist": [],
        "blacklist": [],
    }
    assert matches(opportunity, filters)

    filters["min_profit"] = 5.0
    assert not matches(opportunity, filters)

    filters["min_profit"] = 3.0
    filters["max_profit"] = 4.0
    assert not matches(opportunity, filters)

    filters["max_profit"] = 10.0
    filters["min_spread"] = 6.0
    assert not matches(opportunity, filters)

    filters["min_spread"] = 4.0
    filters["blacklist"] = ["BTC/USDT"]
    assert not matches(opportunity, filters)

    filters["blacklist"] = []
    filters["watchlist"] = ["ETH/USDT", "ADA/USDT"]
    assert not matches(opportunity, filters)

    filters["watchlist"] = ["BTC/USDT"]
    assert matches(opportunity, filters)


def test_match_reason_identifies_the_actual_rejection():
    opportunity = _opportunity(net_profit=0.25)
    filters = {
        "min_profit": 0.5,
        "max_profit": 100.0,
        "min_spread": 0.0,
        "max_spread": 100.0,
        "min_volume": 10000.0,
        "watchlist": [],
        "blacklist": [],
    }
    reason = match_reason(opportunity, filters)
    assert reason is not None
    assert "net profit" in reason

    opportunity = _opportunity(volume_buy=100, volume_sell=100, net_profit=2.0)
    reason = match_reason(opportunity, filters)
    assert reason is not None
    assert "volume" in reason


def test_quote_currency_filter_is_respected():
    opportunity = _opportunity(symbol="BTC/USDC")
    filters = {
        "min_profit": 0.0,
        "max_profit": 100.0,
        "min_spread": 0.0,
        "max_spread": 100.0,
        "min_volume": 0.0,
        "watchlist": [],
        "blacklist": [],
        "quote_currency": "USDT",
    }
    assert not matches(opportunity, filters)
    assert "quote currency" in (match_reason(opportunity, filters) or "")


async def test_fee_adjusted_filters_are_used():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(str(Path(tmpdir) / "test.db"))
        await db.connect()
        await db.upsert_user(123, "testuser", "test@example.com", ["KuCoin", "Gate.io"])
        user = await db.get_user(123)
        filters = user_filters(user)
        assert filters["fee_adjusted"] is True
        await db.close()


if __name__ == "__main__":
    test_matches_applies_all_filters()
    test_match_reason_identifies_the_actual_rejection()
    test_quote_currency_filter_is_respected()
    asyncio.run(test_fee_adjusted_filters_are_used())
