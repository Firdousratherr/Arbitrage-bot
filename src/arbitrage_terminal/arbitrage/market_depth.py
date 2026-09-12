from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class DepthExecution:
    """Executable result for consuming one side of an order book.

    ``quote_amount`` is the amount of quote currency spent/received.  The
    calculation deliberately uses the supplied levels rather than ticker
    volume, so callers can distinguish nominal liquidity from executable
    liquidity at the configured trade size.
    """

    base_amount: float
    quote_amount: float
    average_price: float
    complete: bool


def _levels(raw: Iterable[Any] | None) -> list[tuple[float, float]]:
    levels: list[tuple[float, float]] = []
    for level in raw or ():
        try:
            price, amount = float(level[0]), float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if isfinite(price) and isfinite(amount) and price > 0 and amount > 0:
            levels.append((price, amount))
    return levels


def execute_buy(orderbook: dict[str, Any], quote_amount: float) -> DepthExecution:
    """Consume asks until ``quote_amount`` quote currency is spent."""
    target = float(quote_amount)
    if not isfinite(target) or target <= 0:
        return DepthExecution(0.0, 0.0, 0.0, False)

    spent = base = 0.0
    for price, available_base in _levels(orderbook.get("asks")):
        remaining_quote = target - spent
        take_base = min(available_base, remaining_quote / price)
        spent += take_base * price
        base += take_base
        if spent + 1e-12 >= target:
            break
    return DepthExecution(base, spent, spent / base if base else 0.0, spent + 1e-9 >= target)


def execute_sell(orderbook: dict[str, Any], base_amount: float) -> DepthExecution:
    """Consume bids until ``base_amount`` base currency is sold."""
    target = float(base_amount)
    if not isfinite(target) or target <= 0:
        return DepthExecution(0.0, 0.0, 0.0, False)

    sold = received = 0.0
    for price, available_base in _levels(orderbook.get("bids")):
        remaining_base = target - sold
        take_base = min(available_base, remaining_base)
        sold += take_base
        received += take_base * price
        if sold + 1e-12 >= target:
            break
    return DepthExecution(sold, received, received / sold if sold else 0.0, sold + 1e-9 >= target)


def round_trip(orderbook_buy: dict[str, Any], orderbook_sell: dict[str, Any], quote_amount: float) -> dict[str, float | bool]:
    """Calculate the executable gross spread for a quote-sized round trip."""
    buy = execute_buy(orderbook_buy, quote_amount)
    if not buy.complete:
        return {
            "complete": False,
            "quote_spent": buy.quote_amount,
            "base_acquired": buy.base_amount,
            "quote_received": 0.0,
            "effective_gap_pct": 0.0,
        }
    sell = execute_sell(orderbook_sell, buy.base_amount)
    if not sell.complete:
        return {
            "complete": False,
            "quote_spent": buy.quote_amount,
            "base_acquired": buy.base_amount,
            "quote_received": sell.quote_amount,
            "effective_gap_pct": 0.0,
        }
    gap = (sell.quote_amount - buy.quote_amount) / buy.quote_amount * 100.0
    return {
        "complete": True,
        "quote_spent": buy.quote_amount,
        "base_acquired": buy.base_amount,
        "quote_received": sell.quote_amount,
        "effective_gap_pct": gap,
        "buy_average_price": buy.average_price,
        "sell_average_price": sell.average_price,
    }
