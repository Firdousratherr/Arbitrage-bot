import asyncio

import pytest

from app.exchanges.base import Ticker
from app.scanner import Scanner


class FakeDB:
    async def purge_expired_opportunities(self):
        return 0

    async def increment_stat(self, name):
        return None

    async def list_users(self, scope):
        return []


class FakeExchange:
    def __init__(self, name, ask, bid, *, fail_markets=False, fail_fetch=False):
        self.name = name
        self.ask = ask
        self.bid = bid
        self.fail_markets = fail_markets
        self.fail_fetch = fail_fetch
        self.last_fetch_symbols = {}

    async def get_active_spot_symbols(self):
        if self.fail_markets:
            raise RuntimeError("market API unavailable")
        return {"BTC/USDT"}

    async def fetch_tickers(self, symbols=None):
        if self.fail_fetch:
            raise RuntimeError("ticker API unavailable")
        return [Ticker(self.name, "BTC/USDT", self.bid, self.ask, 1_000_000)]

    async def get_taker_fees(self, symbols):
        return {symbol: 0.0 for symbol in symbols}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_scanner_finds_gap_between_healthy_exchanges():
    scanner = Scanner(
        FakeDB(),
        {
            "buy": FakeExchange("buy", ask=100, bid=99),
            "sell": FakeExchange("sell", ask=105, bid=104),
        },
        interval=30,
        concurrency=4,
    )

    results = await scanner.run_cycle(
        require_matching_user=False,
        exchange_names={"buy", "sell"},
    )

    assert len(results) == 1
    assert results[0].buy_exchange == "buy"
    assert results[0].sell_exchange == "sell"
    assert results[0].raw_spread == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_failed_exchange_does_not_erase_healthy_opportunity():
    scanner = Scanner(
        FakeDB(),
        {
            "buy": FakeExchange("buy", ask=100, bid=99),
            "sell": FakeExchange("sell", ask=105, bid=104),
            "broken": FakeExchange("broken", ask=1, bid=1, fail_fetch=True),
        },
        interval=30,
        concurrency=4,
    )

    results = await scanner.run_cycle(
        require_matching_user=False,
        exchange_names={"buy", "sell", "broken"},
    )

    assert results
    assert any(
        item.buy_exchange == "buy" and item.sell_exchange == "sell"
        for item in results
    )


@pytest.mark.asyncio
async def test_failed_market_discovery_does_not_block_healthy_exchanges():
    scanner = Scanner(
        FakeDB(),
        {
            "buy": FakeExchange("buy", ask=100, bid=99),
            "sell": FakeExchange("sell", ask=105, bid=104),
            "broken": FakeExchange("broken", ask=1, bid=1, fail_markets=True),
        },
        interval=30,
        concurrency=4,
    )

    results = await scanner.run_cycle(
        require_matching_user=False,
        exchange_names={"buy", "sell", "broken"},
    )

    assert results
    assert any(
        item.buy_exchange == "buy" and item.sell_exchange == "sell"
        for item in results
    )


def test_ticker_merge_deduplicates_exchange_entries():
    first = Ticker("buy", "BTC/USDT", 99, 100, 1000)
    duplicate = Ticker("buy", "BTC/USDT", 98, 101, 2000)
    other = Ticker("sell", "BTC/USDT", 104, 105, 1000)

    grouped = {}
    added = Scanner._merge_tickers(grouped, [first, duplicate, other])

    assert added == 2
    assert len(grouped["BTC/USDT"]) == 2
