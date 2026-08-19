from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class Ticker:
    exchange: str
    symbol: str
    bid: float
    ask: float
    quote_volume: float = 0.0


@dataclass(slots=True)
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    raw_spread: float
    net_profit: float
    volume_buy: float
    volume_sell: float
    verified: bool = False
    loose_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class ExchangeAdapter(Protocol):
    name: str
    async def fetch_tickers(self, symbols: list[str] | None = None) -> list[Ticker]: ...
    async def fetch_order_book(self, symbol: str, limit: int = 10) -> dict[str, Any]: ...
    async def verify_transfer(self, symbol: str) -> tuple[bool, dict[str, Any]]: ...
    async def close(self) -> None: ...
