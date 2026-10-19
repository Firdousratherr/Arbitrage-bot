from __future__ import annotations

import asyncio

from app.scanner import MarketScanner, _walk_buy, _walk_sell


class FakeExchange:
    def __init__(self, name, markets, tickers, books):
        self.id = name
        self.markets = markets
        self.fees = {"trading": {"taker": None}}
        self.urls = {"www": f"https://{name}.example"}
        self.tickers = tickers
        self.books = books
        self.closed = False

    async def load_markets(self):
        return self.markets

    async def fetch_tickers(self):
        return self.tickers

    async def fetch_order_book(self, symbol, limit=20):
        return self.books[symbol]

    async def close(self):
        self.closed = True


def market(symbol, base, quote, fee=0.001):
    return {"symbol": symbol, "base": base, "quote": quote, "spot": True, "type": "spot", "active": True, "taker": fee}


def ticker(bid, ask, volume=100000):
    return {"bid": bid, "ask": ask, "quoteVolume": volume}


def book(bid, ask, amount=1000):
    return {"bids": [[bid, amount]], "asks": [[ask, amount]]}


def make_scanner():
    a = FakeExchange(
        "alpha",
        {"AAA/USDT": market("AAA/USDT", "AAA", "USDT")},
        {"AAA/USDT": ticker(9.9, 10.0)},
        {"AAA/USDT": book(9.9, 10.0)},
    )
    b = FakeExchange(
        "beta",
        {"AAA/USDT": market("AAA/USDT", "AAA", "USDT")},
        {"AAA/USDT": ticker(11.0, 11.1)},
        {"AAA/USDT": book(11.0, 11.1)},
    )
    return MarketScanner(
        exchange_ids=["alpha", "beta"],
        quote_assets=["USDT"],
        min_24h_quote_volume=0,
        exchange_clients={"alpha": a, "beta": b},
    )


def test_order_book_walk_respects_budget_and_depth():
    base, spent = _walk_buy([[10, 2], [11, 2]], 25)
    assert base == 2.5
    assert spent == 25
    sold, proceeds = _walk_sell([[12, 1], [11, 2]], 2)
    assert sold == 2
    assert proceeds == 23


def test_refresh_uses_order_books_and_labels_transfer_cost_unknown():
    async def scenario():
        scanner = make_scanner()
        await scanner.load_markets()
        await scanner.refresh()
        assert scanner.opportunities
        item = scanner.opportunities[0]
        assert item["buy_exchange"] == "alpha"
        assert item["sell_exchange"] == "beta"
        assert item["spread_pct"] > 0
        assert item["estimated_profit_usd_before_transfer"] > 0
        assert item["withdrawal_fee_status"] == "unknown"
        assert item["verification_status"] == "needs_manual_check"
        await scanner.stop()

    asyncio.run(scenario())


def test_failed_exchange_isolated_from_successful_exchange():
    class BrokenExchange(FakeExchange):
        async def fetch_tickers(self):
            raise RuntimeError("temporary outage")

    good = FakeExchange(
        "good",
        {"AAA/USDT": market("AAA/USDT", "AAA", "USDT")},
        {"AAA/USDT": ticker(10, 10.1)},
        {"AAA/USDT": book(10, 10.1)},
    )
    broken = BrokenExchange("broken", {"AAA/USDT": market("AAA/USDT", "AAA", "USDT")}, {}, {})
    async def scenario():
        scanner = MarketScanner(
            exchange_ids=["good", "broken"], quote_assets=["USDT"],
            min_24h_quote_volume=0, exchange_clients={"good": good, "broken": broken},
        )
        await scanner.load_markets()
        await scanner.refresh()
        assert scanner.status_by_exchange["broken"]["status"] == "ticker-fetch-failed"
        await scanner.stop()
    asyncio.run(scenario())


def test_triangular_cycles_use_same_exchange_three_leg_route():
    markets = {
        "AAA/USDT": market("AAA/USDT", "AAA", "USDT", 0.001),
        "BBB/AAA": market("BBB/AAA", "BBB", "AAA", 0.001),
        "BBB/USDT": market("BBB/USDT", "BBB", "USDT", 0.001),
    }
    tickers = {
        "AAA/USDT": ticker(1.9, 2.0),
        "BBB/AAA": ticker(0.99, 1.0),
        "BBB/USDT": ticker(3.0, 3.1),
    }
    books = {symbol: book(item["bid"], item["ask"]) for symbol, item in tickers.items()}
    exchange = FakeExchange("alpha", markets, tickers, books)
    async def scenario():
        scanner = MarketScanner(
            exchange_ids=["alpha"], quote_assets=["USDT", "AAA"],
            min_24h_quote_volume=0, exchange_clients={"alpha": exchange},
        )
        await scanner.load_markets()
        await scanner.refresh()
        loops = scanner.list_triangular(min_profit_pct=0.01)
        assert any(item["exchange"] == "alpha" and "USDT" in item["route"] for item in loops)
        await scanner.stop()
    asyncio.run(scenario())
