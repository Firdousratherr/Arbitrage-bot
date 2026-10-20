import pytest

from app.arbitrage_features import calculate_executable_trade


def test_orderbook_validation_uses_executable_prices_and_depth():
    result = calculate_executable_trade(
        asks=[[100.0, 5.0], [101.0, 10.0]],
        bids=[[105.0, 5.0], [104.0, 10.0]],
        quote_size=500.0,
        buy_fee_rate=0.001,
        sell_fee_rate=0.001,
    )
    assert result.complete is True
    assert result.spent_quote == pytest.approx(500.0)
    assert result.base_amount > 0
    assert result.sell_proceeds > result.spent_quote
    assert result.net_profit < result.gross_profit
    assert result.buy_slippage_pct >= 0
    assert result.sell_slippage_pct >= 0


def test_orderbook_validation_rejects_insufficient_depth():
    result = calculate_executable_trade(
        asks=[[100.0, 20.0]],
        bids=[[105.0, 2.0]],
        quote_size=500.0,
    )
    assert result.complete is False
    assert result.base_amount == pytest.approx(2.0)


def test_orderbook_validation_rejects_empty_or_invalid_books():
    with pytest.raises(ValueError, match="usable levels"):
        calculate_executable_trade(asks=[[0.0, 5.0]], bids=[[105.0, 5.0]], quote_size=100.0)
