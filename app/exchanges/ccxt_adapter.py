from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt.async_support as ccxt

from .base import Ticker

logger = logging.getLogger(__name__)


class CcxtExchangeAdapter:
    # Exchanges whose bulk fetch_tickers() endpoint is known/observed to not populate
    # bid/ask reliably (their summary endpoint returns last/volume but not top-of-book).
    # For these we fall back to per-symbol order-book lookups for a bounded set of
    # candidate symbols instead of silently returning zero usable tickers every cycle.
    BULK_BIDASK_UNRELIABLE = {"lbank", "xt"}
    FALLBACK_MAX_SYMBOLS = 40
    FALLBACK_CONCURRENCY = 8

    def __init__(self, name: str, credentials: dict[str, str] | None = None, public_name: str | None = None):
        self._exchange_id = name
        self.name = public_name or name
        exchange_class = getattr(ccxt, name)
        self.client = exchange_class({"enableRateLimit": True, **(credentials or {})})
        # Diagnostics from the most recent fetch_tickers() call, so callers (e.g. an admin
        # /exchangestats command) can tell "exchange returned nothing because every ticker was
        # missing bid/ask" apart from "exchange request failed" apart from "exchange is fine but
        # has no overlapping symbols" - all three look identical from the outside otherwise.
        self.last_fetch_stats: dict[str, int] = {"raw": 0, "dropped_bid_ask": 0, "usable": 0, "fallback_used": 0}
        self.last_fetch_error: str | None = None

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        try:
            data = await self.client.fetch_tickers(symbols)
            result = []
            dropped = 0
            for symbol, ticker in data.items():
                bid, ask = ticker.get("bid"), ticker.get("ask")
                if bid and ask and bid > 0 and ask > 0:
                    result.append(Ticker(self.name, symbol, float(bid), float(ask), float(ticker.get("quoteVolume") or 0)))
                else:
                    dropped += 1

            fallback_used = 0
            if data and not result and self._exchange_id in self.BULK_BIDASK_UNRELIABLE:
                logger.info(
                    "%s bulk tickers missing bid/ask on every symbol; falling back to per-symbol "
                    "order books for up to %s candidates",
                    self.name, self.FALLBACK_MAX_SYMBOLS,
                )
                result = await self._fallback_top_of_book(data, symbols)
                fallback_used = len(result)

            self.last_fetch_stats = {
                "raw": len(data),
                "dropped_bid_ask": dropped,
                "usable": len(result),
                "fallback_used": fallback_used,
            }
            self.last_fetch_error = None
            if data and not result:
                logger.warning(
                    "%s returned %s tickers but all were dropped for missing/zero bid-ask "
                    "(fallback yielded nothing usable); this exchange's bulk ticker endpoint "
                    "may not populate bid/ask reliably",
                    self.name, len(data),
                )
            return result
        except Exception as exc:
            self.last_fetch_stats = {"raw": 0, "dropped_bid_ask": 0, "usable": 0, "fallback_used": 0}
            self.last_fetch_error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s ticker fetch skipped: %s: %s", self.name, type(exc).__name__, exc)
            return []

    async def _fallback_top_of_book(self, data: dict[str, Any], symbols: list[str] | None) -> list[Ticker]:
        """Per-symbol order-book fallback for exchanges whose bulk ticker endpoint doesn't
        reliably return bid/ask. Bounded to FALLBACK_MAX_SYMBOLS candidates (chosen by highest
        reported quote/base volume when no explicit symbol list was requested) so a single bad
        exchange can't blow up API call volume or RAM on a 500MB free-tier server."""
        if symbols:
            candidates = [symbol for symbol in symbols if symbol in data][: self.FALLBACK_MAX_SYMBOLS]
        else:
            ranked = sorted(
                data.items(),
                key=lambda item: float(item[1].get("quoteVolume") or item[1].get("baseVolume") or 0),
                reverse=True,
            )
            candidates = [symbol for symbol, _ in ranked[: self.FALLBACK_MAX_SYMBOLS]]

        semaphore = asyncio.Semaphore(self.FALLBACK_CONCURRENCY)
        results: list[Ticker] = []

        async def _fetch_one(symbol: str) -> None:
            async with semaphore:
                try:
                    book = await self.client.fetch_order_book(symbol, limit=1)
                    bids, asks = book.get("bids") or [], book.get("asks") or []
                    if not bids or not asks:
                        return
                    bid, ask = float(bids[0][0]), float(asks[0][0])
                    if bid > 0 and ask > 0:
                        quote_volume = float(data.get(symbol, {}).get("quoteVolume") or 0)
                        results.append(Ticker(self.name, symbol, bid, ask, quote_volume))
                except Exception as exc:
                    logger.debug("%s fallback order-book fetch failed for %s: %s", self.name, symbol, exc)

        await asyncio.gather(*(_fetch_one(symbol) for symbol in candidates))
        return results

    async def fetch_order_book(self, symbol: str, limit: int = 10) -> dict[str, Any]:
        return await self.client.fetch_order_book(symbol, limit)

    async def get_taker_fee(self, symbol: str) -> float:
        try:
            await self.client.load_markets()
            market = self.client.market(symbol)
            fee = market.get("taker")
            if fee is None:
                fee = self.client.fees.get("trading", {}).get("taker")
            return float(fee if fee is not None else 0.001)
        except Exception as exc:
            logger.info("%s fee metadata unavailable for %s: %s", self.name, symbol, exc)
            return 0.001

    async def verify_transfer(self, symbol: str) -> tuple[bool, dict[str, Any]]:
        currency = symbol.split("/")[0]
        try:
            currencies = await self.client.fetch_currencies()
            info = currencies.get(currency, {})
            networks = info.get("networks", {}) or {}
            available = []
            for key, value in networks.items():
                value = value or {}
                info = value.get("info", {}) or {}
                available.append({
                    "network": key,
                    "contract": value.get("contract") or info.get("contractAddress") or info.get("contract_address"),
                    "deposit": value.get("deposit") is not False,
                    "withdraw": value.get("withdraw") is not False,
                })
            return bool(available), {"currency": currency, "networks": available}
        except Exception as exc:
            logger.info("%s transfer metadata unavailable for %s: %s", self.name, currency, exc)
            return False, {"currency": currency, "networks": [], "unavailable": True}

    async def close(self) -> None:
        await self.client.close()
