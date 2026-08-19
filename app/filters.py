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


def matches(opportunity, filters: dict[str, Any]) -> bool:
    raw = opportunity.raw_spread
    profit = opportunity.net_profit
    if not filters["min_profit"] <= profit <= filters["max_profit"]:
        return False
    if not filters["min_spread"] <= raw <= filters["max_spread"]:
        return False
    if min(opportunity.volume_buy, opportunity.volume_sell) < filters["min_volume"]:
        return False
    symbol = opportunity.symbol.upper()
    if filters["watchlist"] and symbol not in {item.upper() for item in filters["watchlist"]}:
        return False
    if symbol in {item.upper() for item in filters["blacklist"]}:
        return False
    return True
