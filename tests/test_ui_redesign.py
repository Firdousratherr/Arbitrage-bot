from app.ui import (
    format_opportunity_card,
    format_error,
    format_scan_count,
    format_status_message,
    format_filters_message,
    format_leaderboard,
    format_portfolio,
)
from app.exchanges.base import Opportunity


def test_opportunity_card_formatting():
    """Test the unified opportunity card format."""
    opp = Opportunity(
        symbol="BTC/USDT",
        buy_exchange="KuCoin",
        sell_exchange="Gate.io",
        buy_price=50000,
        sell_price=52500,
        raw_spread=5.0,
        net_profit=4.8,
        volume_buy=1000000,
        volume_sell=500000,
    )
    
    # Test with card number (scan result)
    card = format_opportunity_card(opp, "test-id", card_number=1, trade_size=1000)
    assert "1️⃣ 🔍 SCAN RESULT" in card, "Scan result should have number tag"
    assert "BTC/USDT" in card, "Should contain symbol"
    assert "KuCoin" in card, "Should contain buy exchange"
    assert "Gate.io" in card, "Should contain sell exchange"
    assert "━━━━━━━━━━━━━━" in card, "Should have separators"
    assert "📊 Profit Breakdown" in card, "Should have profit section"
    
    # Test live alert (no number)
    card = format_opportunity_card(opp, "test-id", card_number=None)
    assert "🔴 LIVE ARBITRAGE" in card or "🚨 HIGH-MARGIN ARBITRAGE" in card, "Live alert should have proper tag"
    
    # Test high-margin alert
    opp_high = Opportunity(
        symbol="ETH/USDT",
        buy_exchange="KuCoin",
        sell_exchange="Gate.io",
        buy_price=3000,
        sell_price=3150,
        raw_spread=5.0,
        net_profit=4.8,
        volume_buy=500000,
        volume_sell=500000,
    )
    card = format_opportunity_card(opp_high, "test-id", card_number=None)
    assert "🚨 HIGH-MARGIN ARBITRAGE" in card, "Should show high-margin tag"
    
    print("✅ Opportunity card formatting test passed")


def test_error_message_format():
    """Test error message format (Bug 4)."""
    msg = format_error("Something went wrong", "Try again in a moment")
    assert msg.startswith("❌"), "Error should start with cross mark"
    assert "🔧" in msg, "Error should have tool emoji"
    assert "Something went wrong" in msg, "Error should contain message"
    assert "Try again" in msg, "Error should contain action"
    assert "\n" in msg, "Error should be multi-line"
    lines = msg.split("\n")
    assert len(lines) == 2, "Error should be exactly 2 lines (no wall of text)"
    print("✅ Error message format test passed")


def test_scan_count_message():
    """Test scan count message (Bug 6)."""
    msg_empty = format_scan_count(0)
    assert "No opportunities found" in msg_empty, "Should say no opportunities when empty"
    
    msg_some = format_scan_count(5)
    assert "5" in msg_some, "Should show count of opportunities"
    assert "Found" in msg_some, "Should use 'Found' verb"
    print("✅ Scan count message test passed")


def test_status_message_format():
    """Test account status message."""
    msg = format_status_message(
        vip_status="active",
        vip_expiry="2025-12-31",
        exchanges=["KuCoin", "Gate.io"],
        loose_mode=False,
        paused=False,
        filters={"min_profit": 0.5, "max_profit": 100, "min_spread": 0, "max_spread": 100, "min_volume": 10000, "alert_cooldown": 300}
    )
    assert "👤 ACCOUNT" in msg, "Should have account header"
    assert "💎 VIP" in msg, "Should show VIP status"
    assert "🌐 Exchanges" in msg, "Should show exchanges"
    assert "active" in msg, "Should show active status"
    assert "━━━━━━━━━━━━━━" in msg, "Should have separator"
    print("✅ Status message format test passed")


def test_filters_message_format():
    """Test filter display message."""
    filters = {
        "min_profit": 0.5,
        "max_profit": 100,
        "min_spread": 0.5,
        "max_spread": 10,
        "min_volume": 10000,
        "watchlist": ["BTC/USDT", "ETH/USDT"],
        "blacklist": ["DOGE/USDT"],
        "alert_cooldown": 300,
        "max_results": 10,
        "paused": False,
        "loose_mode": False,
    }
    msg = format_filters_message(filters)
    assert "🎛 YOUR FILTERS" in msg, "Should have filters header"
    assert "📈 Profit range" in msg, "Should show profit range"
    assert "📊 Spread range" in msg, "Should show spread range"
    assert "BTC/USDT" in msg, "Should show watchlist items"
    assert "DOGE/USDT" in msg, "Should show blacklist items"
    print("✅ Filters message format test passed")


def test_leaderboard_format():
    """Test leaderboard display."""
    rows = [
        {"username": "alice", "telegram_id": 123, "total": 100.5},
        {"username": "bob", "telegram_id": 456, "total": 95.2},
        {"username": "charlie", "telegram_id": 789, "total": 82.1},
    ]
    msg = format_leaderboard(rows, "Weekly", 2, 95.2)
    assert "🏆 LEADERBOARD" in msg, "Should have leaderboard header"
    assert "🥇" in msg, "Should have first place emoji"
    assert "🥈" in msg, "Should have second place emoji"
    assert "🥉" in msg, "Should have third place emoji"
    assert "alice" in msg, "Should list top traders"
    assert "Your rank: #2" in msg, "Should show user rank"
    print("✅ Leaderboard format test passed")


def test_portfolio_format():
    """Test portfolio display (Bug 5)."""
    trades = [
        {"symbol": "BTC/USDT", "size": 1000, "profit": 50, "created_at": "2025-01-01T10:00:00"},
        {"symbol": "ETH/USDT", "size": 500, "profit": 25, "created_at": "2025-01-02T10:00:00"},
    ]
    msg = format_portfolio(trades, 10075, 100000)
    assert "📊 YOUR PORTFOLIO" in msg, "Should have portfolio header"
    assert "Simulated Balance" in msg, "Should show balance"
    assert "BTC/USDT" in msg, "Should list trades"
    assert "Recent Trades" in msg, "Should show recent trades section"
    print("✅ Portfolio format test passed")


if __name__ == "__main__":
    test_opportunity_card_formatting()
    test_error_message_format()
    test_scan_count_message()
    test_status_message_format()
    test_filters_message_format()
    test_leaderboard_format()
    test_portfolio_format()
    print("\n✅ All UI redesign tests passed!")
