from __future__ import annotations

from datetime import datetime, timezone

from arbitrage_terminal.domain.models import Ticker
from arbitrage_terminal.domain.normalization import normalize_symbol
from .ccxt_adapter import CcxtAdapter


class XTAdapter(CcxtAdapter):
    """XT-specific adapter using the exchange best-bid/ask endpoint."""

    async def get_tickers(self, symbols=None):
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        if not self._markets:
            await self.get_markets()

        wanted = {s.upper() for s in (symbols or self.last_market_symbols)}
        if not wanted:
            return []

        response = await self._call(
            'bids_asks',
            self.client.fetch_bids_asks,
            list(wanted),
        )
        out = []
        for raw_symbol, ticker in (response or {}).items():
            try:
                symbol, base, quote, _ = normalize_symbol(raw_symbol)
                bid = float(ticker.get('bid'))
                ask = float(ticker.get('ask'))
                volume = float(ticker.get('quoteVolume') or ticker.get('baseVolume') or 0)
            except (ValueError, TypeError, AttributeError):
                continue
            if symbol.upper() not in wanted or bid <= 0 or ask <= 0:
                continue
            ts = ticker.get('timestamp')
            stamp = datetime.fromtimestamp(float(ts) / 1000, timezone.utc) if ts else datetime.now(timezone.utc)
            out.append(Ticker(self.name, symbol, base, quote, bid, ask, max(0.0, volume), stamp))

        self.last_ticker_symbols = {t.symbol for t in out}
        self.last_ticker_count = len(out)
        return out
