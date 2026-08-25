from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from .db import DEFAULT_FILTERS
from .scan_diagnostics import set_filter_rejections

_filter_rejections: ContextVar[dict[str, str]] = ContextVar("filter_rejections", default={})


def clear_filter_rejections() -> None:
    _filter_rejections.set({})
    set_filter_rejections({})


def get_filter_rejections() -> dict[str, str]:
    return dict(_filter_rejections.get())


def _record_rejection(opportunity: Any, reason: str) -> None:
    current = dict(_filter_rejections.get())
    symbol = str(getattr(opportunity, "symbol", "unknown"))
    if symbol not in current:
        current[symbol] = reason
    _filter_rejections.set(current)
    set_filter_rejections(current)


def user_filters(user: Any) -> dict[str, Any]:
    stored = json.loads(user["filters"] or "{}")
    return {**DEFAULT_FILTERS, **stored}


def parse_float(value: str, minimum: float = 0.0) -> float:
    parsed = float(value)
    if parsed < minimum:
        raise ValueError("value is outside the allowed range")
    return parsed


def _effective_profit(opportunity, filters: dict[str, Any]) -> float:
    return float(opportunity.net_profit if filters.get("fee_adjusted", True) else opportunity.raw_spread)


def match_reason(opportunity, filters: dict[str, Any]) -> str | None:
    raw = float(opportunity.raw_spread)
    profit = _effective_profit(opportunity, filters)
    if not filters["min_profit"] <= profit <= filters["max_profit"]:
        metric = "net profit" if filters.get("fee_adjusted", True) else "spread"
        reason = f"{metric} {profit:.2f}% outside {filters['min_profit']:.2f}%–{filters['max_profit']:.2f}%"
        _record_rejection(opportunity, reason)
        return reason
    if not filters["min_spread"] <= raw <= filters["max_spread"]:
        reason = f"spread {raw:.2f}% outside {filters['min_spread']:.2f}%–{filters['max_spread']:.2f}%"
        _record_rejection(opportunity, reason)
        return reason
    volume = min(float(opportunity.volume_buy or 0), float(opportunity.volume_sell or 0))
    if volume < filters["min_volume"]:
        reason = f"volume ${volume:,.0f} below ${filters['min_volume']:,.0f} minimum"
        _record_rejection(opportunity, reason)
        return reason
    symbol = opportunity.symbol.upper()
    watchlist = {item.upper() for item in filters["watchlist"]}
    if watchlist and symbol not in watchlist:
        reason = "not in watchlist"
        _record_rejection(opportunity, reason)
        return reason
    if symbol in {item.upper() for item in filters["blacklist"]}:
        reason = "blacklisted symbol"
        _record_rejection(opportunity, reason)
        return reason
    quote_currency = str(filters.get("quote_currency") or "").upper()
    if quote_currency:
        quote = symbol.split("/", 1)[1].split(":", 1)[0] if "/" in symbol else ""
        if quote and quote != quote_currency:
            reason = f"quote currency {quote} != {quote_currency}"
            _record_rejection(opportunity, reason)
            return reason
    return None


def matches(opportunity, filters: dict[str, Any]) -> bool:
    return match_reason(opportunity, filters) is None
