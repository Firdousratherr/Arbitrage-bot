import pytest

pytest.importorskip("fastapi")

from types import SimpleNamespace

from mobile_api.app import _opportunity_payload


def test_opportunity_payload_does_not_claim_verification():
    opportunity = SimpleNamespace(
        symbol="BTC/USDT",
        buy_exchange="binance",
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
