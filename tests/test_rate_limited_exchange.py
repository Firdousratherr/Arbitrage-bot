import asyncio

import pytest

from arbitrage_terminal.exchanges.rate_limited import RateLimitedExchangeAdapter


class FakeAdapter:
    def __init__(self):
        self.market_calls = 0
        self.ticker_calls = 0
        self.active = 0
        self.max_active = 0

    async def get_markets(self):
        self.market_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return []

    async def get_tickers(self, symbols):
        self.ticker_calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return []

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_identical_market_reads_are_coalesced():
    raw = FakeAdapter()
    adapter = RateLimitedExchangeAdapter(raw)

    await asyncio.gather(*(adapter.get_markets() for _ in range(20)))

    assert raw.market_calls == 1


@pytest.mark.asyncio
async def test_identical_ticker_reads_are_coalesced():
    raw = FakeAdapter()
    adapter = RateLimitedExchangeAdapter(raw)

    await asyncio.gather(*(adapter.get_tickers({'BTC/USDT', 'ETH/USDT'}) for _ in range(20)))

    assert raw.ticker_calls == 1


@pytest.mark.asyncio
async def test_different_requests_respect_exchange_concurrency_limit():
    raw = FakeAdapter()
    adapter = RateLimitedExchangeAdapter(raw, concurrency=2)

    await asyncio.gather(
        *(adapter.get_tickers({f'COIN{i}/USDT'}) for i in range(8))
    )

    assert raw.max_active <= 2
    assert raw.ticker_calls == 8
