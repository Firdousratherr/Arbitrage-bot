from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from time import monotonic
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class ExecutableTrade:
    requested_quote: float
    spent_quote: float
    base_amount: float
    sell_proceeds: float
    buy_fee: float
    sell_fee: float
    gross_profit: float
    net_profit: float
    buy_slippage_pct: float
    sell_slippage_pct: float
    complete: bool


def _levels(levels: Iterable[Sequence[float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for level in levels:
        if len(level) < 2:
            continue
        try:
            price = float(level[0])
            amount = float(level[1])
        except (TypeError, ValueError):
            continue
        if price > 0 and amount > 0:
            result.append((price, amount))
    return result


def calculate_executable_trade(
    asks: Iterable[Sequence[float]],
    bids: Iterable[Sequence[float]],
    quote_size: float,
    buy_fee_rate: float = 0.0,
    sell_fee_rate: float = 0.0,
) -> ExecutableTrade:
    """Simulate one market-to-market arbitrage using actual order-book levels.

    quote_size is the maximum quote currency budget on the buy side. The function
    consumes asks until the budget is exhausted (or the book runs out), then sells
    the acquired base asset into bids. Fee rates are decimal fractions (0.001 = 0.1%).
    """
    if quote_size <= 0:
        raise ValueError("quote_size must be positive")

    ask_levels = _levels(asks)
    bid_levels = _levels(bids)
    if not ask_levels or not bid_levels:
        raise ValueError("both order books require usable levels")

    first_ask = ask_levels[0][0]
    first_bid = bid_levels[0][0]

    remaining_quote = quote_size
    spent_quote = 0.0
    base_amount = 0.0
    for price, amount in ask_levels:
        max_base = remaining_quote / price
        take_base = min(amount, max_base)
        if take_base <= 0:
            continue
        cost = take_base * price
        spent_quote += cost
        base_amount += take_base
        remaining_quote -= cost
        if remaining_quote <= 1e-12:
            break

    complete_buy = remaining_quote <= 1e-9
    if base_amount <= 0:
        raise ValueError("buy-side order book has no executable liquidity")

    remaining_base = base_amount
    sell_proceeds = 0.0
    sold_base = 0.0
    for price, amount in bid_levels:
        take_base = min(amount, remaining_base)
        if take_base <= 0:
            continue
        sell_proceeds += take_base * price
        sold_base += take_base
        remaining_base -= take_base
        if remaining_base <= 1e-12:
            break

    complete_sell = remaining_base <= 1e-9
    complete = complete_buy and complete_sell
    if sold_base <= 0:
        raise ValueError("sell-side order book has no executable liquidity")

    # If the sell book is shallower than the acquired amount, evaluate only what
    # can actually be sold. This prevents overstating executable profit.
    buy_cost = (spent_quote / (1.0 - min(max(buy_fee_rate, 0.0), 0.99))) if buy_fee_rate else spent_quote
    buy_fee = max(0.0, buy_cost - spent_quote)
    sell_fee = sell_proceeds * max(0.0, sell_fee_rate)
    net_proceeds = sell_proceeds - sell_fee
    gross_profit = sell_proceeds - spent_quote
    net_profit = net_proceeds - buy_cost

    buy_avg = spent_quote / base_amount
    sold_avg = sell_proceeds / sold_base
    buy_slippage_pct = abs((buy_avg - first_ask) / first_ask) * 100
    sell_slippage_pct = abs((sold_avg - first_bid) / first_bid) * 100

    return ExecutableTrade(
        requested_quote=quote_size,
        spent_quote=spent_quote,
        base_amount=sold_base,
        sell_proceeds=sell_proceeds,
        buy_fee=buy_fee,
        sell_fee=sell_fee,
        gross_profit=gross_profit,
        net_profit=net_profit,
        buy_slippage_pct=buy_slippage_pct,
        sell_slippage_pct=sell_slippage_pct,
        complete=complete,
    )


def confidence_score(
    *,
    net_profit_pct: float,
    buy_volume: float,
    sell_volume: float,
    trade_size: float,
    freshness_seconds: float,
    transfer_verified: bool,
    coverage_complete: bool,
    executable_complete: bool,
) -> int:
    """Return a conservative 0-100 confidence score for an opportunity."""
    score = 0.0
    score += min(max(net_profit_pct, 0.0) * 4.0, 30.0)
    min_volume = min(max(buy_volume, 0.0), max(sell_volume, 0.0))
    if trade_size > 0:
        score += min(max(min_volume / trade_size, 0.0), 5.0) * 7.0
    if freshness_seconds <= 2:
        score += 15
    elif freshness_seconds <= 5:
        score += 12
    elif freshness_seconds <= 15:
        score += 8
    elif freshness_seconds <= 30:
        score += 4
    if transfer_verified:
        score += 15
    if coverage_complete:
        score += 5
    if executable_complete:
        score += 20
    return max(0, min(100, round(score)))


def freshness_seconds(created_monotonic: float | None) -> float:
    if created_monotonic is None:
        return 0.0
    return max(0.0, monotonic() - created_monotonic)


def freshness_label(seconds: float) -> str:
    if seconds < 2:
        return "LIVE"
    if seconds < 10:
        return f"{seconds:.1f}s old"
    if seconds < 60:
        return f"{int(seconds)}s old"
    return f"{int(seconds // 60)}m old"


def material_change(previous_spread: float | None, current_spread: float, threshold_pct_points: float = 0.25) -> bool:
    if previous_spread is None:
        return True
    return abs(current_spread - previous_spread) >= threshold_pct_points


class OpportunityHistory:
    """Bounded in-memory spread history keyed by symbol and exchange route."""

    def __init__(self, max_points: int = 12):
        self.max_points = max(3, max_points)
        self._data: dict[str, deque[tuple[float, float]]] = defaultdict(lambda: deque(maxlen=self.max_points))

    def add(self, key: str, spread_pct: float, net_profit_pct: float) -> list[dict[str, float]]:
        history = self._data[key]
        history.append((spread_pct, net_profit_pct))
        return [{"spread": spread, "net": net} for spread, net in history]

    def get(self, key: str) -> list[dict[str, float]]:
        return [{"spread": spread, "net": net} for spread, net in self._data.get(key, ())]


def rank_score(net_profit_pct: float, confidence: int, executable_net_pct: float | None) -> float:
    """Rank opportunities without letting headline spread dominate execution quality."""
    executable = max(executable_net_pct or 0.0, 0.0)
    return round(max(net_profit_pct, 0.0) * 0.4 + executable * 0.35 + confidence * 0.25, 4)
