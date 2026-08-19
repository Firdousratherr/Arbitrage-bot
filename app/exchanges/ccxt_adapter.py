from __future__ import annotations

import logging
from typing import Any

import ccxt.async_support as ccxt

from .base import Ticker

logger = logging.getLogger(__name__)


class CcxtExchangeAdapter:
    def __init__(self, name: str, credentials: dict[str, str] | None = None):
        self.name = name
        exchange_class = getattr(ccxt, name)
        self.client = exchange_class({"enableRateLimit": True, **(credentials or {})})

    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        try:
            data = await self.client.fetch_tickers(symbols)
            result = []
            for symbol, ticker in data.items():
                bid, ask = ticker.get("bid"), ticker.get("ask")
                if bid and ask and bid > 0 and ask > 0:
                    result.append(Ticker(self.name, symbol, float(bid), float(ask), float(ticker.get("quoteVolume") or 0)))
            return result
        except Exception as exc:
            logger.warning("%s ticker fetch skipped: %s", self.name, exc)
            return []

    async def fetch_order_book(self, symbol: str, limit: int = 10) -> dict[str, Any]:
        return await self.client.fetch_order_book(symbol, limit)

    async def verify_transfer(self, symbol: str) -> tuple[bool, dict[str, Any]]:
        currency = symbol.split("/")[0]
        try:
            currencies = await self.client.fetch_currencies()
            info = currencies.get(currency, {})
            networks = info.get("networks", {}) or {}
            available = [
                {"network": key, "contract": value.get("contract"), "deposit": value.get("deposit", True), "withdraw": value.get("withdraw", True)}
                for key, value in networks.items()
            ]
            return bool(available), {"currency": currency, "networks": available}
        except Exception as exc:
            logger.info("%s transfer metadata unavailable for %s: %s", self.name, currency, exc)
            return False, {"currency": currency, "networks": [], "unavailable": True}

    async def close(self) -> None:
        await self.client.close()
