from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.domain.normalization import normalize_symbol
from .ccxt_adapter import CcxtAdapter


class LBankAdapter(CcxtAdapter):
    """LBank-specific adapter using its best-bid/ask endpoint for spot tickers."""

    async def get_tickers(self, symbols=None):
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        if not self._markets:
            await self.get_markets()

        wanted = {s.upper() for s in (symbols or self.last_market_symbols)}
        if not wanted:
            return []

        by_symbol = {}
        for raw_symbol, market in self._markets.items():
            if not self._spot(market):
                continue
            try:
                normalized, base, quote, _ = normalize_symbol(raw_symbol)
            except ValueError:
                continue
            if normalized.upper() in wanted:
                api_symbol = str(market.get('id') or raw_symbol).lower()
                by_symbol[normalized] = (
                    api_symbol,
                    base,
                    quote,
                    self.last_market_asset_identities.get(normalized),
                )

        semaphore = asyncio.Semaphore(12)

        async def fetch_one(symbol, api_symbol, base, quote, asset_identity):
            async with semaphore:
                response = await self._call(
                    f"book_ticker:{symbol}",
                    self.client.spotPublicGetSupplementTickerBookTicker,
                    {"symbol": api_symbol},
                )
            data = response.get("data") or {}
            try:
                bid = float(data.get("bidPrice"))
                ask = float(data.get("askPrice"))
            except (TypeError, ValueError):
                return None
            if bid <= 0 or ask <= 0:
                return None
            stamp = response.get("ts") or response.get("timestamp")
            timestamp = datetime.fromtimestamp(float(stamp) / 1000, timezone.utc) if stamp else datetime.now(timezone.utc)
            return Ticker(self.name, symbol, base, quote, bid, ask, 0.0, timestamp, asset_identity)

        results = await asyncio.gather(
            *(fetch_one(symbol, *details) for symbol, details in by_symbol.items()),
            return_exceptions=True,
        )
        out = [item for item in results if isinstance(item, Ticker)]
        self.last_ticker_symbols = {t.symbol for t in out}
        self.last_ticker_count = len(out)
        return out
