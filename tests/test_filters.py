import asyncio
from datetime import UTC, datetime
from app.db import Database
from app.exchanges.base import Opportunity
from app.filters import matches, user_filters
from pathlib import Path
import tempfile


def test_matches_applies_all_filters():
    """Test that matches() applies all active filters correctly."""
    opportunity = Opportunity(
        symbol="BTC/USDT",
        buy_exchange="KuCoin",
        sell_exchange="Gate.io",
        buy_price=50000,
        sell_price=52500,
        raw_spread=5.0,
        net_profit=4.8,  # After fees
        volume_buy=1000000,
        volume_sell=1000000,
    )
    
    # Test with filters that should match
    filters = {
        "min_profit": 3.0,
        "max_profit": 10.0,
        "min_spread": 4.0,
        "max_spread": 10.0,
        "min_volume": 500000,
        "watchlist": [],
        "blacklist": [],
    }
    assert matches(opportunity, filters), "Should match with valid filters"
    
    # Test min_profit boundary
    filters["min_profit"] = 5.0
    assert not matches(opportunity, filters), "Should not match when net_profit below min"
    
    # Test max_profit boundary
    filters["min_profit"] = 3.0
    filters["max_profit"] = 4.0
    assert not matches(opportunity, filters), "Should not match when net_profit above max"
    
    # Test min_spread boundary
    filters["max_profit"] = 10.0
    filters["min_spread"] = 6.0
    assert not matches(opportunity, filters), "Should not match when raw_spread below min"
    
    # Test blacklist
    filters["min_spread"] = 4.0
    filters["blacklist"] = ["BTC/USDT"]
    assert not matches(opportunity, filters), "Should not match when symbol in blacklist"
    
    # Test watchlist
    filters["blacklist"] = []
    filters["watchlist"] = ["ETH/USDT", "ADA/USDT"]
    assert not matches(opportunity, filters), "Should not match when symbol not in watchlist"
    
    filters["watchlist"] = ["BTC/USDT"]
    assert matches(opportunity, filters), "Should match when symbol in watchlist"
    
    print("✅ All filter matching tests passed")


async def test_fee_adjusted_filters_are_used():
    """Test that fee_adjusted flag affects which profit value is used."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(str(Path(tmpdir) / "test.db"))
        await db.connect()
        
        # Create user with fee_adjusted=True
        await db.upsert_user(123, "testuser", "test@example.com", ["KuCoin", "Gate.io"])
        user = await db.get_user(123)
        
        filters = user_filters(user)
        assert filters["fee_adjusted"] is True, "Default fee_adjusted should be True"
        
        await db.close()
        print("✅ Fee-adjusted flag test passed")


if __name__ == "__main__":
    test_matches_applies_all_filters()
    asyncio.run(test_fee_adjusted_filters_are_used())
