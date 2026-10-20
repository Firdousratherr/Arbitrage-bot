import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from app.exchanges.base import Ticker
from mobile_api import app as mobile_api
from mobile_api.app import _opportunity_payload, _verified_routes
from mobile_api.market_math import consume_asks_for_quote, consume_bids_for_base, trading_fee_rate


def test_buy_depth_walks_multiple_ask_levels_and_fills_quote_budget():
    result = consume_asks_for_quote([[10.0, 3.0], [12.0, 5.0]], 48.0)

    assert result["complete"] is True
    assert result["quote_spent"] == pytest.approx(48.0)
    assert result["base_amount"] == pytest.approx(4.5)
    assert result["average_price"] == pytest.approx(48.0 / 4.5)


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


def test_depth_helpers_reject_invalid_trade_sizes():
    with pytest.raises(ValueError):
        consume_asks_for_quote([[10.0, 1.0]], 0)
    with pytest.raises(ValueError):
        consume_bids_for_base([[10.0, 1.0]], -1)


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


def test_transfer_route_does_not_accept_a_missing_contract():
    buy_networks = [{"network_id": "ERC20", "withdraw_enabled": True, "contract": None}]
    sell_networks = [{"network_id": "ERC20", "deposit_enabled": True, "contract": None}]

    assert _verified_routes(buy_networks, sell_networks) == []


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
        metadata={
            "observed_at": "2026-10-19T00:00:00+00:00",
            "coverage_complete": True,
            "provisional_fee_adjusted_spread_pct": None,
            "fee_data_complete": False,
        },
    )

    payload = _opportunity_payload(opportunity)

    assert payload["estimate_type"] == "headline_only"
    assert payload["transfer_verified"] is False
    assert payload["order_book_depth_verified"] is False
    assert payload["gross_spread_pct"] == 1.0
    assert payload["provisional_fee_adjusted_spread_pct"] is None
    assert payload["fee_data_complete"] is False


def test_mobile_scan_matches_pairs_even_when_not_listed_on_every_exchange(monkeypatch):
    class FeeClient:
        fees = {"trading": {"taker": 0.001}}

        def market(self, _symbol):
            return {"taker": 0.001}

    mobile_api.exchanges = {
        "venue_a": SimpleNamespace(client=FeeClient()),
        "venue_b": SimpleNamespace(client=FeeClient()),
        "venue_c": SimpleNamespace(client=FeeClient()),
    }
    samples = {
        "venue_a": [Ticker("venue_a", "BTC/USDT", 99.0, 100.0, 100_000.0)],
        "venue_b": [
            Ticker("venue_b", "BTC/USDT", 102.0, 103.0, 120_000.0),
            Ticker("venue_b", "ETH/USDT", 9.4, 9.5, 90_000.0),
        ],
        "venue_c": [Ticker("venue_c", "ETH/USDT", 11.0, 11.1, 80_000.0)],
    }

    async def fake_collect(name, _adapter, _semaphore):
        return name, samples[name], {
            "status": "ok",
            "market_count": len(samples[name]),
            "usable_tickers": len(samples[name]),
            "orderbook_recovered": 0,
            "error": None,
        }

    monkeypatch.setattr(mobile_api, "_collect_exchange", fake_collect)
    opportunities, diagnostics = asyncio.run(mobile_api._scan_cycle())

    assert {item.symbol for item in opportunities} == {"BTC/USDT", "ETH/USDT"}
    assert diagnostics["venue_a"]["status"] == "ok"
    assert next(item for item in opportunities if item.symbol == "BTC/USDT").buy_exchange == "venue_a"
    assert next(item for item in opportunities if item.symbol == "ETH/USDT").sell_exchange == "venue_c"
