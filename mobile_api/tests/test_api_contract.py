import pytest

pytest.importorskip("fastapi")

from mobile_api.app import _opportunity_payload, _verified_routes
from mobile_api.market_math import consume_asks_for_quote, consume_bids_for_base, trading_fee_rate


def test_buy_depth_walks_multiple_ask_levels_and_fills_quote_budget():
    result = consume_asks_for_quote([[10.0, 3.0], [12.0, 5.0]], 48.0)

    assert result["complete"] is True
    assert result["quote_spent"] == pytest.approx(48.0)
    assert result["base_amount"] == pytest.approx(4.0)
    assert result["average_price"] == pytest.approx(12.0)


def test_buy_depth_marks_insufficient_liquidity():
    result = consume_asks_for_quote([[10.0, 1.0]], 20.0)

    assert result["complete"] is False
    assert result["quote_spent"] == pytest.approx(10.0)
    assert result["unfilled_quote"] == pytest.approx(10.0)


def test_sell_depth_walks_bids_and_marks_unfilled_base():
    result = consume_bids_for_base([[11.0, 1.0], [10.0, 2.0]], 2.0)

    assert result["complete"] is True
    assert result["base_sold"] == pytest.approx(2.0)
    assert result["quote_received"] == pytest.approx(21.0)


def test_fee_metadata_returns_unknown_instead_of_assuming_zero():
    exchange = type("Exchange", (), {
        "market": lambda self, symbol: {"taker": None},
        "fees": {"trading": {"taker": None}},
    })()

    rate, source = trading_fee_rate(exchange, "ABC/USDT")

    assert rate is None
    assert source == "unknown"


def test_transfer_route_needs_enabled_status_and_same_contract():
    buy_networks = [
        {"network_id": "ERC20", "withdraw_enabled": True, "contract": "0xabc", "withdrawal_fee": 2},
        {"network_id": "BEP20", "withdraw_enabled": None, "contract": "0xdef"},
    ]
    sell_networks = [
        {"network_id": "ERC20", "deposit_enabled": True, "contract": "0xABC"},
        {"network_id": "BEP20", "deposit_enabled": True, "contract": "0xdef"},
    ]

    routes = _verified_routes(buy_networks, sell_networks)

    assert len(routes) == 1
    assert routes[0]["network"] == "ERC20"
    assert routes[0]["verified"] is True


def test_opportunity_payload_does_not_claim_verification():
    from types import SimpleNamespace

    opportunity = SimpleNamespace(
        symbol="BTC/USDT",
        buy_exchange="kraken",
        sell_exchange="okx",
        buy_price=100.0,
        sell_price=101.0,
        raw_spread=1.0,
        net_profit=0.8,
        volume_buy=50000.0,
        volume_sell=45000.0,
        metadata={"observed_at": "2026-10-19T00:00:00+00:00", "coverage_complete": True},
    )

    payload = _opportunity_payload(opportunity)

    assert payload["estimate_type"] == "headline_only"
    assert payload["transfer_verified"] is False
    assert payload["order_book_depth_verified"] is False
    assert payload["gross_spread_pct"] == 1.0
    assert any("Withdrawal/network fees" in warning for warning in payload["warnings"])
