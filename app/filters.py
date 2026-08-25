from __future__ import annotations

import json
from typing import Any

from .db import DEFAULT_FILTERS


def user_filters(user: Any) -> dict[str, Any]:
    stored = json.loads(user["filters"] or "{}")
    return {**DEFAULT_FILTERS, **stored}


def parse_float(value: str, minimum: float = 0.0) -> float:
    parsed = float(value)
    if parsed < minimum:
        raise ValueError("value is outside the allowed range")
    return parsed


def match_reason(opportunity, filters: dict[str, Any]) -> str | None:
    """Return the first configured-filter reason that rejects an opportunity.

    A reason is deliberately returned separately from matches() so the scanner/UI
    can explain why positive spreads were not shown without weakening any filter.
    """
    raw = float(opportunity.raw_spread)
    profit = float(opportunity.net_profit)
    if not filters["min_profit"] <= profit <= filters["max_profit"]:
        return f"net profit {profit:.2f}% outside {filters['min_profit']:.2f}%–{filters['max_profit']:.2f}%"
    if not filters["min_spread"] <= raw <= filters["max_spread"]:
        return f"spread {raw:.2f}% outside {filters['min_spread']:.2f}%–{filters['max_spread']:.2f}%"
    volume = min(float(opportunity.volume_buy or 0), float(opportunity.volume_sell or 0))
    if volume < filters["min_volume"]:
        return f"volume ${volume:,.0f} below ${filters['min_volume']:,.0f} minimum"
    symbol = opportunity.symbol.upper()
    watchlist = {item.upper() for item in filters["watchlist"]}
    if watchlist and symbol not in watchlist:
        return "not in watchlist"
    if symbol in {item.upper() for item in filters["blacklist"]}:
        return "blacklisted symbol"
    quote_currency = str(filters.get("quote_currency") or "").upper()
    if quote_currency:
        quote = symbol.split("/", 1)[1].split(":", 1)[0] if "/" in symbol else ""
        if quote and quote != quote_currency:
            return f"quote currency {quote} != {quote_currency}"
    return None


def matches(opportunity, filters: dict[str, Any]) -> bool:
    return match_reason(opportunity, filters) is None
