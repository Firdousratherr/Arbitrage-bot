from __future__ import annotations

import asyncio
import logging
from typing import Any

import ccxt.async_support as ccxt

from .base import Ticker

logger = logging.getLogger(__name__)


class CcxtExchangeAdapter:
    BULK_BIDASK_UNRELIABLE = {"lbank", "xt"}
    BULK_SYMBOL_FILTER_IGNORED = {"lbank"}
    TICKER_SYMBOL_BATCH_SIZE = 100
    FALLBACK_MAX_SYMBOLS = 40
    FALLBACK_CONCURRENCY = 8
    TARGETED_RECOVERY_MAX_SYMBOLS = 30
    TARGETED_RECOVERY_CONCURRENCY = 8
    TARGETED_SINGLE_TICKER_FALLBACK_MAX = 8

    def __init__(self, name: str, credentials: dict[str, str] | None = None, public_name: str | None = None):
        self._exchange_id = name
        self.name = public_name or name
        exchange_class = getattr(ccxt, name)
        self.client = exchange_class({"enableRateLimit": True, **(credentials or {})})
        self.last_fetch_stats: dict[str, int] = {
            "raw": 0,
            "dropped_bid_ask": 0,
            "usable": 0,
            "fallback_used": 0,
            "targeted_recovery_used": 0,
            "requested_symbols": 0,
        }
        self.last_fetch_error: str | None = None
        self.last_fetch_symbols: dict[str, str] = {}

    async def get_active_spot_symbols(self) -> list[str]:
        """Return the exchange's complete active spot market list.

        This is deliberately separate from fetch_tickers(): several exchanges
        cap or otherwise limit their all-tickers endpoint, which can make two
        healthy exchanges appear to have almost no markets in common.
        """
        await self.client.load_markets()
        symbols: list[str] = []
        for symbol, market in (self.client.markets or {}).items():
            if market.get("spot") is True and market.get("active", True) is not False:
                symbols.append(symbol)
        return symbols

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        try:
            requested = list(dict.fromkeys(symbols or []))
            requested_set = set(requested)
            if requested and self._exchange_id in self.BULK_SYMBOL_FILTER_IGNORED:
                # LBank's spot implementation accepts a symbols argument but
                # still requests symbol=all. Fetch once, then filter locally.
                data = await self.client.fetch_tickers()
            elif requested:
                # Keep request URLs bounded on exchanges that accept a symbol
                # list. Merge all batches into one result.
                data = {}
                for start in range(0, len(requested), self.TICKER_SYMBOL_BATCH_SIZE):
                    batch = requested[start:start + self.TICKER_SYMBOL_BATCH_SIZE]
                    response = await self.client.fetch_tickers(batch)
                    data.update(response or {})
            else:
                data = await self.client.fetch_tickers()

            result: list[Ticker] = []
            dropped = 0
            missing_symbols: dict[str, str] = {}
            for symbol, ticker in data.items():
                if requested_set and symbol not in requested_set:
                    continue
                bid, ask = ticker.get("bid"), ticker.get("ask")
                if bid and ask and bid > 0 and ask > 0:
                    result.append(Ticker(self.name, symbol, float(bid), float(ask), float(ticker.get("quoteVolume") or 0)))
                else:
                    dropped += 1
                    missing_symbols[symbol] = "missing/zero bid-ask"

            if requested_set:
                returned = {ticker.symbol for ticker in result} | set(missing_symbols)
                for symbol in requested_set - returned:
                    missing_symbols[symbol] = "ticker not returned"

            fallback_used = 0
            if missing_symbols and self._exchange_id in self.BULK_BIDASK_UNRELIABLE:
                logger.info(
                    "%s tickers missing bid/ask for %s symbols; falling back to per-symbol order books for up to %s candidates",
                    self.name, len(missing_symbols), self.FALLBACK_MAX_SYMBOLS,
                )
                fallback = await self._fallback_top_of_book(data, list(missing_symbols))
                if fallback:
                    recovered_symbols = {ticker.symbol for ticker in fallback}
                    result.extend(fallback)
                    fallback_used = len(fallback)
                    for symbol in recovered_symbols:
                        missing_symbols.pop(symbol, None)

            self.last_fetch_symbols = missing_symbols
            self.last_fetch_stats = {
                "raw": len(data),
                "dropped_bid_ask": dropped,
                "usable": len(result),
                "fallback_used": fallback_used,
                "targeted_recovery_used": 0,
                "requested_symbols": len(requested),
            }
            self.last_fetch_error = None

            if data and not result:
                logger.warning("%s returned %s tickers but none matched the requested markets with usable bid/ask", self.name, len(data))
            elif missing_symbols:
                logger.warning("%s still has %s requested symbols without usable ticker data", self.name, len(missing_symbols))
            return result
        except Exception as exc:
            self.last_fetch_stats = {
                "raw": 0, "dropped_bid_ask": 0, "usable": 0,
                "fallback_used": 0, "targeted_recovery_used": 0, "requested_symbols": len(symbols or []),
            }
            self.last_fetch_error = f"{type(exc).__name__}: {exc}"
            self.last_fetch_symbols = {symbol: self.last_fetch_error for symbol in (symbols or [])}
            logger.warning("%s ticker fetch skipped: %s: %s", self.name, type(exc).__name__, exc)
            return []

    async def recover_symbols(self, symbols: list[str] | set[str], max_symbols: int | None = None) -> list[Ticker]:
        limit = max_symbols or self.TARGETED_RECOVERY_MAX_SYMBOLS
        candidates = list(dict.fromkeys(symbols))[:limit]
        if not candidates:
            return []

        try:
            await self.client.load_markets()
            available = set(self.client.markets or {})
            listed = [symbol for symbol in candidates if symbol in available]
            not_listed = [symbol for symbol in candidates if symbol not in available]
            for symbol in not_listed:
                self.last_fetch_symbols[symbol] = "not listed on exchange"
            candidates = listed
            logger.info("%s targeted recovery market filter: %s listed, %s not listed", self.name, len(listed), len(not_listed))
        except Exception as exc:
            logger.info("%s could not load markets before targeted recovery: %s: %s", self.name, type(exc).__name__, exc)

        if not candidates:
            return []

        recovered: dict[str, Ticker] = {}
        unresolved = candidates
        batch_supported = bool(self.client.has.get("fetchBidsAsks"))

        if batch_supported:
            try:
                batch = await asyncio.wait_for(self.client.fetch_bids_asks(candidates), timeout=8)
                for symbol, ticker in (batch or {}).items():
                    bid, ask = ticker.get("bid"), ticker.get("ask")
                    if bid and ask and bid > 0 and ask > 0:
                        recovered[symbol] = Ticker(self.name, symbol, float(bid), float(ask), float(ticker.get("quoteVolume") or 0))
                unresolved = [symbol for symbol in candidates if symbol not in recovered]
                logger.info("%s targeted bid/ask recovery: %s/%s symbols recovered in batch", self.name, len(recovered), len(candidates))
            except Exception as exc:
                logger.info("%s targeted bid/ask batch recovery unavailable: %s: %s", self.name, type(exc).__name__, exc)

        unresolved = unresolved[: self.TARGETED_SINGLE_TICKER_FALLBACK_MAX]
        semaphore = asyncio.Semaphore(self.TARGETED_RECOVERY_CONCURRENCY)

        async def _fetch_one(symbol: str) -> None:
            async with semaphore:
                try:
                    ticker = await asyncio.wait_for(self.client.fetch_ticker(symbol), timeout=6)
                    bid, ask = ticker.get("bid"), ticker.get("ask")
                    if bid and ask and bid > 0 and ask > 0:
                        recovered[symbol] = Ticker(self.name, symbol, float(bid), float(ask), float(ticker.get("quoteVolume") or 0))
                except Exception as exc:
                    logger.debug("%s targeted ticker recovery failed for %s: %s", self.name, symbol, exc)

        if unresolved:
            await asyncio.gather(*(_fetch_one(symbol) for symbol in unresolved))

        if recovered:
            for symbol in recovered:
                self.last_fetch_symbols.pop(symbol, None)
            self.last_fetch_stats["usable"] = self.last_fetch_stats.get("usable", 0) + len(recovered)
            self.last_fetch_stats["targeted_recovery_used"] = len(recovered)

        logger.info("%s targeted symbol recovery complete: %s/%s recovered", self.name, len(recovered), len(candidates))
        return list(recovered.values())

    async def _fallback_top_of_book(self, data: dict[str, Any], symbols: list[str] | None) -> list[Ticker]:
        if symbols:
            candidates = [symbol for symbol in symbols if symbol in data][: self.FALLBACK_MAX_SYMBOLS]
        else:
            ranked = sorted(data.items(), key=lambda item: float(item[1].get("quoteVolume") or item[1].get("baseVolume") or 0), reverse=True)
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
