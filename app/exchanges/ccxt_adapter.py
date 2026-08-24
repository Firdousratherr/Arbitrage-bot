from __future__ import annotations

import logging
from typing import Any

import ccxt.async_support as ccxt

from .base import Ticker

logger = logging.getLogger(__name__)


class CcxtExchangeAdapter:
    def __init__(self, name: str, credentials: dict[str, str] | None = None, public_name: str | None = None):
        self.name = public_name or name
        exchange_class = getattr(ccxt, name)
        self.client = exchange_class({"enableRateLimit": True, **(credentials or {})})
        # Diagnostics from the most recent fetch_tickers() call, so callers (e.g. an admin
        # /exchangestats command) can tell "exchange returned nothing because every ticker was
        # missing bid/ask" apart from "exchange request failed" apart from "exchange is fine but
        # has no overlapping symbols" - all three look identical from the outside otherwise.
        self.last_fetch_stats: dict[str, int] = {"raw": 0, "dropped_bid_ask": 0, "usable": 0}
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
            self.last_fetch_stats = {"raw": len(data), "dropped_bid_ask": dropped, "usable": len(result)}
            self.last_fetch_error = None
            if data and not result:
                logger.warning(
                    "%s returned %s tickers but all were dropped for missing/zero bid-ask; "
                    "this exchange's bulk ticker endpoint may not populate bid/ask reliably",
                    self.name, len(data),
                )
            return result
        except Exception as exc:
            self.last_fetch_stats = {"raw": 0, "dropped_bid_ask": 0, "usable": 0}
            self.last_fetch_error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s ticker fetch skipped: %s: %s", self.name, type(exc).__name__, exc)
            return []

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
