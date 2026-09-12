from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.domain.normalization import normalize_symbol
from .ccxt_adapter import CcxtAdapter


class LBankAdapter(CcxtAdapter):
    """LBank-specific adapter using its best-bid/ask endpoint for spot prices."""

    async def get_tickers(self, symbols=None):
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        self.last_ticker_source = 'book_ticker'
        if not self._markets:
            await self.get_markets()

        wanted = {s.upper() for s in (symbols or self.last_market_symbols)}
        if not wanted:
            return []

        identities = getattr(self, 'last_market_asset_identities', {})
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
                by_symbol[normalized] = (api_symbol, base, quote, identities.get(normalized))

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

        # The best-bid/ask endpoint is fast and reliable for prices but does not
        # consistently expose 24h volume. Enrich the already-collected quotes
        # with one bulk CCXT ticker request when supported. This avoids an
        # additional request per symbol and never makes volume up when it is
        # genuinely unavailable.
        if out and hasattr(self.client, 'fetch_tickers'):
            try:
                bulk = await self._call('volume_tickers', self.client.fetch_tickers, sorted(wanted))
                volume_by_symbol = {}
                for raw_symbol, ticker in (bulk or {}).items():
                    try:
                        normalized, *_ = normalize_symbol(raw_symbol)
                        volume = float((ticker or {}).get('quoteVolume'))
                    except (ValueError, TypeError, AttributeError):
                        continue
                    if volume >= 0:
                        volume_by_symbol[normalized.upper()] = volume
                if volume_by_symbol:
                    out = [
                        Ticker(t.exchange, t.symbol, t.base, t.quote, t.bid, t.ask,
                               volume_by_symbol.get(t.symbol.upper(), t.quote_volume),
                               t.timestamp, t.asset_identity)
                        for t in out
                    ]
                    self.last_ticker_source = 'book_ticker+bulk_volume'
            except Exception:
                # Price discovery must remain available if the optional volume
                # enrichment endpoint is unsupported or temporarily unhealthy.
                self.last_ticker_source = 'book_ticker'

        self.last_ticker_symbols = {t.symbol for t in out}
        self.last_ticker_count = len(out)
        return out
