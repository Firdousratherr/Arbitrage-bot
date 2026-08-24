from app.arbitrage_features import (
    OpportunityHistory,
    calculate_executable_trade,
    confidence_score,
    material_change,
    rank_score,
)


def test_executable_trade_uses_quote_budget_and_book_depth():
    result = calculate_executable_trade(
        asks=[[10.0, 50.0], [11.0, 100.0]],
        bids=[[12.0, 25.0], [11.5, 100.0]],
        quote_size=500.0,
        buy_fee_rate=0.001,
        sell_fee_rate=0.001,
    )
    assert result.spent_quote == 500.0
    assert result.base_amount > 0
    assert result.sell_proceeds > 0
    assert result.gross_profit > 0
    assert result.net_profit < result.gross_profit
    assert result.complete is True


def test_executable_trade_marks_shallow_book_incomplete():
    result = calculate_executable_trade(
        asks=[[10.0, 10.0]],
        bids=[[12.0, 5.0]],
        quote_size=100.0,
    )
    assert result.complete is False
    assert result.base_amount == 5.0


def test_confidence_score_stays_bounded():
    score = confidence_score(
        net_profit_pct=50,
        buy_volume=1_000_000,
        sell_volume=1_000_000,
        trade_size=1000,
        freshness_seconds=0,
        transfer_verified=True,
        coverage_complete=True,
        executable_complete=True,
    )
    assert score == 100


def test_material_change_requires_new_or_meaningful_spread_move():
    assert material_change(None, 2.0)
    assert not material_change(2.0, 2.1, 0.25)
    assert material_change(2.0, 2.3, 0.25)


def test_history_is_bounded_and_ordered():
    history = OpportunityHistory(max_points=3)
    history.add("BTC:buy:sell", 1.0, 0.8)
    history.add("BTC:buy:sell", 1.2, 1.0)
    history.add("BTC:buy:sell", 1.4, 1.2)
    history.add("BTC:buy:sell", 1.6, 1.4)
    values = history.get("BTC:buy:sell")
    assert len(values) == 3
    assert values[0]["spread"] == 1.2
    assert values[-1]["spread"] == 1.6


def test_rank_score_weights_execution_quality():
    high = rank_score(3.0, 90, 2.8)
    low = rank_score(5.0, 45, 0.5)
    assert high > low
