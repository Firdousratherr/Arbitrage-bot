import asyncio
from types import SimpleNamespace

from arbitrage_terminal.exchanges.ccxt_adapter import CcxtAdapter
from arbitrage_terminal.exchanges.lbank import LBankAdapter
from arbitrage_terminal.exchanges.xt import XTAdapter


def test_lbank_uses_book_ticker_for_requested_symbols():
    adapter = object.__new__(LBankAdapter)
    adapter.name = 'lbank'
    adapter._markets = {
        'btc_usdt': {'active': True, 'spot': True, 'type': 'spot'},
        'eth_usdt': {'active': True, 'spot': True, 'type': 'spot'},
    }
    adapter.last_market_symbols = {'BTC/USDT', 'ETH/USDT'}
    adapter.last_ticker_symbols = set()
    adapter.last_ticker_count = 0
    calls = []

    async def fake_call(op, fn, *args, **kwargs):
        calls.append((op, args[0]['symbol']))
        return {
            'data': {
                'symbol': args[0]['symbol'],
                'bidPrice': '100.0',
                'askPrice': '101.0',
            },
            'ts': 1700000000000,
        }

    adapter._call = fake_call
    adapter.client = SimpleNamespace(spotPublicGetSupplementTickerBookTicker=lambda params: params)

    result = asyncio.run(adapter.get_tickers({'BTC/USDT'}))

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert calls == [('book_ticker:BTC/USDT', 'btc_usdt')]


def test_xt_uses_batch_best_bid_ask_endpoint():
    adapter = object.__new__(XTAdapter)
    adapter.name = 'xt'
    adapter._markets = {
        'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'},
        'ETH/USDT': {'active': True, 'spot': True, 'type': 'spot'},
    }
    adapter.last_market_symbols = {'BTC/USDT', 'ETH/USDT'}
    adapter.last_ticker_symbols = set()
    adapter.last_ticker_count = 0
    calls = []

    async def fake_call(op, fn, *args, **kwargs):
        calls.append((op, args[0]))
        return {
            'BTC/USDT': {
                'symbol': 'BTC/USDT',
                'bid': 100.0,
                'ask': 101.0,
                'timestamp': 1700000000000,
            }
        }

    adapter._call = fake_call
    adapter.client = SimpleNamespace(fetch_bids_asks=lambda symbols: symbols)

    result = asyncio.run(adapter.get_tickers({'BTC/USDT'}))

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert calls[0][0] == 'bids_asks'
    assert set(calls[0][1]) == {'BTC/USDT'}


def test_generic_ccxt_uses_fetch_bids_asks_when_supported():
    adapter = object.__new__(CcxtAdapter)
    adapter.name = 'mexc'
    adapter._markets = {'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'}}
    adapter.last_market_symbols = {'BTC/USDT'}
    adapter.last_ticker_symbols = set()
    adapter.last_ticker_count = 0
    adapter.last_ticker_source = ''
    calls = []

    async def fake_call(op, fn, *args, **kwargs):
        calls.append((op, args[0]))
        return {
            'BTC/USDT': {
                'bid': '100.0',
                'ask': '101.0',
                'quoteVolume': '50000',
                'timestamp': 1700000000000,
            }
        }

    adapter._call = fake_call
    adapter.client = SimpleNamespace(
        has={'fetchBidsAsks': True},
        fetch_bids_asks=lambda symbols: symbols,
    )

    result = asyncio.run(adapter.get_tickers({'BTC/USDT'}))

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert adapter.last_ticker_source == 'fetch_bids_asks'
    assert calls == [('bids_asks', ['BTC/USDT'])]


def test_generic_ccxt_falls_back_to_symbol_scoped_fetch_tickers():
    adapter = object.__new__(CcxtAdapter)
    adapter.name = 'kraken'
    adapter._markets = {'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'}}
    adapter.last_market_symbols = {'BTC/USDT'}
    adapter.last_ticker_symbols = set()
    adapter.last_ticker_count = 0
    adapter.last_ticker_source = ''
    calls = []

    async def fake_call(op, fn, *args, **kwargs):
        calls.append((op, args[0]))
        return {
            'BTC/USDT': {
                'bid': '100.0',
                'ask': '101.0',
                'quoteVolume': '50000',
                'timestamp': 1700000000000,
            }
        }

    adapter._call = fake_call
    adapter.client = SimpleNamespace(
        has={'fetchBidsAsks': False},
        fetch_tickers=lambda symbols: symbols,
    )

    result = asyncio.run(adapter.get_tickers({'BTC/USDT'}))

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert adapter.last_ticker_source == 'fetch_tickers'
    assert calls == [('tickers', ['BTC/USDT'])]
