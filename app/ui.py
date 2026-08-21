from __future__ import annotations

from typing import Iterable, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


SECTION_SEPARATOR = "━━━━━━━━━━━━━━"


def _compact_number(value: float | int, digits: int = 8) -> str:
    if abs(float(value)) >= 1000000:
        return f"{float(value):,.0f}"
    if abs(float(value)) >= 1000:
        return f"{float(value):,.2f}"
    return format(float(value), f".{digits}f").rstrip("0").rstrip(".")


def _safe_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _transfer_status_text(metadata: dict | None, mode: str) -> str:
    if not metadata:
        return "❌"
    networks = []
    for item in metadata.get("networks", []) or []:
        if item.get(mode):
            network = item.get("network") or "unknown"
            networks.append(str(network))
    if not networks:
        return "❌"
    return "✅ " + ", ".join(networks[:2])


def _transfer_row(opportunity) -> str:
    metadata = getattr(opportunity, "metadata", {}) or {}
    if metadata.get("transfer_verification") == "loose_mode":
        return "⚠️ Transfer checks skipped"
    buy = _transfer_status_text(metadata.get("buy_transfer"), "deposit")
    sell = _transfer_status_text(metadata.get("sell_transfer"), "withdraw")
    return f"⚠️ Transfer: Deposit {buy} • Withdrawal {sell}"


def format_opportunity_card(opportunity, identifier: str, title: str = "🚨 ARBITRAGE OPPORTUNITY") -> str:
    buy_label = opportunity.buy_exchange or "buy"
    sell_label = opportunity.sell_exchange or "sell"
    lines = [
        f"{title} • {opportunity.symbol}",
        f"🟢 BUY {buy_label} {_compact_number(opportunity.buy_price, 8)}",
        f"🔴 SELL {sell_label} {_compact_number(opportunity.sell_price, 8)}",
        f"📈 Spread {float(opportunity.raw_spread):+.2f}% • 💰 Net {float(opportunity.net_profit):+.2f}%",
        f"💸 Fees live • 📊 Vol {_compact_number(opportunity.volume_buy)} / {_compact_number(opportunity.volume_sell)}",
        _transfer_row(opportunity),
    ]
    return "\n".join(lines)


def opportunity_buttons(identifier: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔎 Details + Order Book", callback_data=f"details:{identifier}"),
            InlineKeyboardButton("🧪 Paper Trade", callback_data=f"paper:{identifier}"),
        ]
    ])


def format_background_alert(opportunity, identifier: str) -> str:
    return format_opportunity_card(opportunity, identifier, "🚨 NEW ARBITRAGE")


def format_scan_summary(opportunities: Sequence[object], *, exchange_count: int, opportunities_found: int, matching_selected: int, results_shown: int) -> str:
    visible = list(opportunities)[:3]
    lines = [
        "🔎 SCAN COMPLETE",
        f"🌐 {exchange_count} exchanges • 🎯 {opportunities_found} found",
        f"🎯 {matching_selected} matched • 📋 {results_shown} shown" if matching_selected != opportunities_found else f"📋 {results_shown} results shown",
        "🏆 TOP OPPORTUNITIES",
    ]
    for index, opportunity in enumerate(visible, 1):
        label = ["1️⃣", "2️⃣", "3️⃣"][index - 1]
        lines.extend([
            f"{label} {opportunity.symbol}  📈 {float(opportunity.raw_spread):+.2f}%",
            f"   {opportunity.buy_exchange} → {opportunity.sell_exchange}",
        ])
    return "\n".join(lines)


def format_order_book(levels: Iterable[Sequence[float]], *, title: str) -> str:
    rows = []
    for level in list(levels)[:5]:
        if len(level) >= 2:
            rows.append(f"{float(level[0]):.8f} x {float(level[1]):.6f}")
    body = " • ".join(rows) if rows else "unavailable"
    return f"{title}{body}" if title else body


def format_opportunity_details(row: dict, buy_fill: float, sell_fill: float, buy_fee: float, sell_fee: float, gross_profit: float, net_profit: float, buy_slippage: float, sell_slippage: float, transfer_text: str, buy_book: Sequence[Sequence[float]], sell_book: Sequence[Sequence[float]]) -> str:
    lines = [
        f"🔎 DETAILS • {row['symbol']}",
        f"🔄 {row['buy_exchange']} → {row['sell_exchange']}",
        f"💰 Buy {_compact_number(buy_fill, 8)} • Sell {_compact_number(sell_fill, 8)}",
        f"📈 Spread {float(row['raw_spread']):+.2f}% • 💰 Net {float(net_profit):+.2f}%",
        f"💸 Fees {buy_fee:.4f} / {sell_fee:.4f} • Slippage {buy_slippage:.2f}% / {sell_slippage:.2f}%",
        f"📊 Volume {_compact_number(row['volume_buy'])} / {_compact_number(row['volume_sell'])}",
        "📖 ORDER BOOK",
        f"🟢 Ask: {format_order_book(buy_book, title='')}",
        f"🔴 Bid: {format_order_book(sell_book, title='')}",
        f"📊 Depth {len(buy_book)} / {len(sell_book)} levels • Gross {_compact_number(gross_profit, 4)}",
        "🛡 TRANSFER",
        transfer_text.replace("\n", " • "),
    ]
    return "\n".join(lines)


def format_paper_trade(opportunity, *, buy_price: float, sell_price: float, size: float, expected_gross: float, estimated_net: float, profit: float) -> str:
    lines = [
        f"🧪 PAPER TRADE • {opportunity.symbol}",
        f"🟢 {opportunity.buy_exchange} {_compact_number(buy_price, 8)} → 🔴 {opportunity.sell_exchange} {_compact_number(sell_price, 8)}",
        f"💵 Size {_compact_number(size, 6)} • 📈 Gross {_compact_number(expected_gross, 6)}",
        f"💰 Net {_compact_number(estimated_net, 6)} • 📊 P/L {_compact_number(profit, 6)}",
        "⚠️ Simulation only • no real funds used",
    ]
    return "\n".join(lines)


def format_error(message: str, *, action: str = "Review the details and try again.") -> str:
    lines = [
        "❌ OPERATION FAILED",
        "",
        SECTION_SEPARATOR,
        "",
        _safe_text(message)[:1200],
        "",
        "🔧 Suggested action:",
        _safe_text(action),
    ]
    return "\n".join(lines)


def format_success(message: str) -> str:
    return "✅ SUCCESS\n\n" + _safe_text(message)
