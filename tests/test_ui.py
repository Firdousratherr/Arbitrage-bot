from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.exchanges.base import Opportunity
from app.ui import (
    format_opportunity_card,
    format_opportunity_details,
    format_paper_trade,
    format_scan_summary,
    opportunity_buttons,
)


def _opp(symbol="ENA3L/USDT", buy="bitrue", sell="gateio", buy_price=0.00256917, sell_price=0.00481470, raw_spread=87.403, net_profit=15.25, volume_buy=15431764.0, volume_sell=3678169.0):
    return Opportunity(symbol=symbol, buy_exchange=buy, sell_exchange=sell, buy_price=buy_price, sell_price=sell_price, raw_spread=raw_spread, net_profit=net_profit, volume_buy=volume_buy, volume_sell=volume_sell, verified=False, metadata={})


def test_format_opportunity_card_has_key_sections():
    card = format_opportunity_card(_opp(), identifier="abc123", title="🚨 ARBITRAGE OPPORTUNITY")
    assert "ARBITRAGE OPPORTUNITY" in card
    assert "ENA3L/USDT" in card
    assert "BUY" in card
    assert "SELL" in card
    assert "Spread" in card
    assert "Net" in card
    assert "Transfer" in card
    assert "\n\n" not in card


def test_format_scan_summary_includes_summary_and_top_items():
    summary = format_scan_summary([
        _opp(symbol="MSFT3L/USDT", raw_spread=99.108, net_profit=24.5),
        _opp(symbol="ENA3L/USDT", raw_spread=87.946, net_profit=19.1),
    ], exchange_count=15, opportunities_found=644, matching_selected=644, results_shown=10)
    assert "SCAN COMPLETE" in summary
    assert "15 exchanges" in summary
    assert "644 found" in summary
    assert "MSFT3L/USDT" in summary
    assert "1️⃣" in summary


def test_format_paper_trade_uses_simulation_notice():
    trade = format_paper_trade(_opp(), buy_price=0.00256917, sell_price=0.00481470, size=100.0, expected_gross=12.44, estimated_net=10.82, profit=10.82)
    assert "PAPER TRADE" in trade
    assert "Simulation only" in trade or "No real funds" in trade
    assert "ENA3L/USDT" in trade


def test_opportunity_buttons_keep_callback_ids():
    buttons = opportunity_buttons("abc123")
    assert isinstance(buttons, InlineKeyboardMarkup)
    data = [item.callback_data for row in buttons.inline_keyboard for item in row]
    assert "details:abc123" in data
    assert "paper:abc123" in data


def test_details_and_background_alerts_are_compact():
    opportunity = _opp()
    details = format_opportunity_details(
        {"symbol": opportunity.symbol, "buy_exchange": opportunity.buy_exchange, "sell_exchange": opportunity.sell_exchange,
         "raw_spread": opportunity.raw_spread, "volume_buy": opportunity.volume_buy, "volume_sell": opportunity.volume_sell},
        0.0025, 0.0048, 0.001, 0.001, 2.3, 2.1, 0.1, 0.2,
        "✅ Matching route: ERC20\n🟢 Buy deposit: ERC20\n🔴 Sell withdrawal: ERC20",
        [[0.0025, 100]], [[0.0048, 100]],
    )
    alert = format_opportunity_card(opportunity, "stable-id", "🚨 NEW ARBITRAGE")
    assert "ORDER BOOK" in details
    assert "TRANSFER" in details
    assert "\n\n" not in details
    assert "stable-id" not in alert
