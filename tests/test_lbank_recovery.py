import asyncio
from unittest.mock import AsyncMock, Mock

from app.exchanges.ccxt_adapter import CcxtExchangeAdapter


def _adapter_with_mock_client(markets, books):
    adapter = object.__new__(CcxtExchangeAdapter)
    adapter._exchange_id = "lbank"
    adapter.name = "lbank"
    adapter.last_fetch_stats = {"usable": 0, "targeted_recovery_used": 0}
    adapter.last_fetch_symbols = {symbol: "missing/zero bid-ask" for symbol in markets}

    client = Mock()
    client.has = {"fetchBidsAsks": False}
    client.markets = markets
    client.load_markets = AsyncMock()

    async def fetch_order_book(symbol, limit=1):
        return books.get(symbol, {"bids": [], "asks": []})

    client.fetch_order_book = AsyncMock(side_effect=fetch_order_book)
    client.fetch_ticker = AsyncMock(return_value={"bid": None, "ask": None})
    adapter.client = client
    return adapter


async def test_lbank_order_book_recovery_covers_all_missing_symbols():
    symbols = ["OG/USDT", "AAVE/USDT", "1INCH/USDT"]
    markets = {
        symbol: {"active": True, "spot": True, "type": "spot"}
        for symbol in symbols
    }
    books = {
        "OG/USDT": {"bids": [[0.10, 100]], "asks": [[0.11, 100]]},
        "AAVE/USDT": {"bids": [[100, 1]], "asks": [[101, 1]]},
        "1INCH/USDT": {"bids": [[0.20, 100]], "asks": [[0.21, 100]]},
    }
    adapter = _adapter_with_mock_client(markets, books)

    recovered = await adapter.recover_symbols(symbols)

    assert {ticker.symbol for ticker in recovered} == set(symbols)
    assert len(recovered) == len(symbols)
    assert adapter.last_fetch_symbols == {}
    assert adapter.last_fetch_stats["targeted_recovery_used"] == len(symbols)
    assert adapter.client.fetch_order_book.await_count == len(symbols)


async def test_lbank_recovery_keeps_reason_for_unrecoverable_symbol():
    symbols = ["OG/USDT", "BROKEN/USDT"]
    markets = {
        "OG/USDT": {"active": True, "spot": True, "type": "spot"},
        "BROKEN/USDT": {"active": True, "spot": True, "type": "spot"},
    }
    books = {
        "OG/USDT": {"bids": [[0.10, 100]], "asks": [[0.11, 100]]},
        "BROKEN/USDT": {"bids": [], "asks": []},
    }
    adapter = _adapter_with_mock_client(markets, books)

    recovered = await adapter.recover_symbols(symbols)

    assert {ticker.symbol for ticker in recovered} == {"OG/USDT"}
    assert adapter.last_fetch_symbols["BROKEN/USDT"] == "missing/zero bid-ask"
    assert adapter.last_fetch_stats["targeted_recovery_used"] == 1


if __name__ == "__main__":
    asyncio.run(test_lbank_order_book_recovery_covers_all_missing_symbols())
    asyncio.run(test_lbank_recovery_keeps_reason_for_unrecoverable_symbol())
    print("2 LBank recovery tests passed")
