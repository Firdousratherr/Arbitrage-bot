from __future__ import annotations

from typing import Sequence


def consume_asks_for_quote(
    asks: Sequence[Sequence[float]], quote_budget: float
) -> dict[str, float | bool]:
    """Estimate base quantity bought for a quote-currency budget from ask levels."""
    if quote_budget <= 0:
        raise ValueError("quote_budget must be greater than zero")

    remaining = quote_budget
    base_amount = 0.0
    quote_spent = 0.0
    for level in asks:
        if len(level) < 2:
            continue
        price, available_base = float(level[0]), float(level[1])
        if price <= 0 or available_base <= 0:
            continue
        level_quote = price * available_base
        take_quote = min(remaining, level_quote)
        take_base = take_quote / price
        base_amount += take_base
        quote_spent += take_quote
        remaining -= take_quote
        if remaining <= max(1e-9, quote_budget * 1e-9):
            break

    complete = remaining <= max(1e-9, quote_budget * 1e-9)
    return {
        "base_amount": base_amount,
        "quote_spent": quote_spent,
        "average_price": quote_spent / base_amount if base_amount else 0.0,
        "complete": complete,
        "unfilled_quote": max(0.0, remaining),
    }


def consume_bids_for_base(
    bids: Sequence[Sequence[float]], base_amount: float
) -> dict[str, float | bool]:
    """Estimate quote proceeds from selling a base amount into bid levels."""
    if base_amount < 0:
        raise ValueError("base_amount cannot be negative")

    remaining = base_amount
    base_sold = 0.0
    quote_received = 0.0
    for level in bids:
        if len(level) < 2:
            continue
        price, available_base = float(level[0]), float(level[1])
        if price <= 0 or available_base <= 0:
            continue
        take_base = min(remaining, available_base)
        base_sold += take_base
        quote_received += take_base * price
        remaining -= take_base
        if remaining <= max(1e-12, base_amount * 1e-9):
            break

    complete = remaining <= max(1e-12, base_amount * 1e-9)
    return {
        "base_sold": base_sold,
        "quote_received": quote_received,
        "average_price": quote_received / base_sold if base_sold else 0.0,
        "complete": complete,
        "unfilled_base": max(0.0, remaining),
    }


def trading_fee_rate(exchange, symbol: str) -> tuple[float | None, str]:
    """Return an explicitly published market/global taker fee, never a guessed default."""
    try:
        market = exchange.market(symbol)
        fee = market.get("taker") if market else None
        if fee is not None:
            rate = float(fee)
            if 0 <= rate < 1:
                return rate, "market"
    except Exception:
        pass

    try:
        fee = (getattr(exchange, "fees", {}) or {}).get("trading", {}).get("taker")
        if fee is not None:
            rate = float(fee)
            if 0 <= rate < 1:
                return rate, "exchange_default"
    except (TypeError, ValueError, AttributeError):
        pass
    return None, "unknown"
