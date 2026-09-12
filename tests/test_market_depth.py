from arbitrage_terminal.arbitrage.market_depth import execute_buy, execute_sell, round_trip


def test_execute_buy_consumes_multiple_ask_levels():
    result = execute_buy({'asks': [[10, 20], [11, 20]]}, 300)
    assert result.complete
    assert result.base_amount == 29.090909090909093
    assert result.quote_amount == 300
    assert result.average_price > 10


def test_execute_buy_marks_insufficient_depth():
    result = execute_buy({'asks': [[10, 5]]}, 100)
    assert not result.complete
    assert result.base_amount == 5
    assert result.quote_amount == 50


def test_execute_sell_consumes_multiple_bid_levels():
    result = execute_sell({'bids': [[12, 10], [11, 10]]}, 15)
    assert result.complete
    assert result.base_amount == 15
    assert result.quote_amount == 175


def test_round_trip_exposes_executable_gap_not_ticker_gap():
    result = round_trip(
        {'asks': [[10, 10], [10.5, 100]]},
        {'bids': [[11, 5], [10.7, 100]]},
        100,
    )
    assert result['complete']
    assert result['effective_gap_pct'] < 10


def test_round_trip_rejects_incomplete_sell_depth():
    result = round_trip({'asks': [[10, 10]]}, {'bids': [[11, 5]]}, 100)
    assert not result['complete']
