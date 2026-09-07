from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from arbitrage_terminal.exchanges.ccxt_adapter import CcxtAdapter
from arbitrage_terminal.exchanges.lbank import LBankAdapter
from arbitrage_terminal.exchanges.xt import XTAdapter


def _base_adapter_state(adapter, name, markets):
    adapter.name = name
    adapter._markets = markets
    adapter.last_market_symbols = set(markets) if '/' in next(iter(markets), '') else {
        'BTC/USDT', 'ETH/USDT'
    }
    adapter.last_ticker_symbols = set()
    adapter.last_ticker_count = 0
    adapter.last_diagnostics = {}


@pytest.mark.asyncio
async def test_lbank_uses_book_ticker_for_requested_symbols():
    adapter = object.__new__(LBankAdapter)
    _base_adapter_state(adapter, 'lbank', {
        'btc_usdt': {'active': True, 'spot': True, 'type': 'spot'},
        'eth_usdt': {'active': True, 'spot': True, 'type': 'spot'},
    })
    calls = []

    async def fake_book_ticker(params):
        calls.append(params['symbol'])
        return {
            'data': {'symbol': params['symbol'], 'bidPrice': '100.0', 'askPrice': '101.0'},
            'ts': 1700000000000,
        }

    adapter.client = SimpleNamespace(
        spotPublicGetSupplementTickerBookTicker=fake_book_ticker,
    )

    result = await adapter.get_tickers({'BTC/USDT'})

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert calls == ['btc_usdt']


@pytest.mark.asyncio
async def test_xt_uses_batch_best_bid_ask_endpoint():
    adapter = object.__new__(XTAdapter)
    _base_adapter_state(adapter, 'xt', {
        'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'},
        'ETH/USDT': {'active': True, 'spot': True, 'type': 'spot'},
    })
    calls = []

    async def fake_bids_asks(symbols):
        calls.append(symbols)
        return {
            'BTC/USDT': {
                'symbol': 'BTC/USDT',
                'bid': 100.0,
                'ask': 101.0,
                'timestamp': 1700000000000,
            }
        }

    adapter.client = SimpleNamespace(fetch_bids_asks=fake_bids_asks)

    result = await adapter.get_tickers({'BTC/USDT'})

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert set(calls[0]) == {'BTC/USDT'}
    assert adapter.last_diagnostics['bids_asks']['latency_ms'] >= 0


@pytest.mark.asyncio
async def test_generic_ccxt_uses_fetch_bids_asks_when_supported():
    adapter = object.__new__(CcxtAdapter)
    _base_adapter_state(adapter, 'mexc', {
        'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'},
    })
    adapter.last_ticker_source = ''

    adapter.client = SimpleNamespace(
        has={'fetchBidsAsks': True},
        fetch_bids_asks=AsyncMock(return_value={
            'BTC/USDT': {
                'bid': '100.0',
                'ask': '101.0',
                'quoteVolume': '50000',
                'timestamp': 1700000000000,
            }
        }),
    )

    result = await adapter.get_tickers({'BTC/USDT'})

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert adapter.last_ticker_source == 'fetch_bids_asks'
    adapter.client.fetch_bids_asks.assert_awaited_once_with(['BTC/USDT'])


@pytest.mark.asyncio
async def test_generic_ccxt_falls_back_to_symbol_scoped_fetch_tickers():
    adapter = object.__new__(CcxtAdapter)
    _base_adapter_state(adapter, 'kraken', {
        'BTC/USDT': {'active': True, 'spot': True, 'type': 'spot'},
    })
    adapter.last_ticker_source = ''

    adapter.client = SimpleNamespace(
        has={'fetchBidsAsks': False},
        fetch_tickers=AsyncMock(return_value={
            'BTC/USDT': {
                'bid': '100.0',
                'ask': '101.0',
                'quoteVolume': '50000',
                'timestamp': 1700000000000,
            }
        }),
    )

    result = await adapter.get_tickers({'BTC/USDT'})

    assert [ticker.symbol for ticker in result] == ['BTC/USDT']
    assert result[0].bid == 100.0
    assert result[0].ask == 101.0
    assert adapter.last_ticker_source == 'fetch_tickers'
    adapter.client.fetch_tickers.assert_awaited_once_with(['BTC/USDT'])
