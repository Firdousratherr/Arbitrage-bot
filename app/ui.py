from __future__ import annotations

from typing import Iterable, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


SECTION_SEPARATOR = "━━━━━━━━━━━━━━━━━━"


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
        return "❌ Not available"
    networks = []
    for item in metadata.get("networks", []) or []:
        if item.get(mode):
            network = item.get("network") or "unknown"
            networks.append(str(network))
    if not networks:
        return "❌ Not available"
    return "✅ Available (" + ", ".join(networks[:3]) + ")"


def format_opportunity_card(opportunity, identifier: str, title: str = "🚨 ARBITRAGE OPPORTUNITY") -> str:
    buy_label = opportunity.buy_exchange or "buy"
    sell_label = opportunity.sell_exchange or "sell"
    transfer_status = "⚠️ TRANSFER NOT VERIFIED" if not getattr(opportunity, "verified", False) else "✅ TRANSFER ROUTE VERIFIED"
    buy_status = _transfer_status_text(getattr(opportunity, "metadata", {}).get("buy_transfer"), "deposit")
    sell_status = _transfer_status_text(getattr(opportunity, "metadata", {}).get("sell_transfer"), "withdraw")

    lines = [
        f"{title}",
        "",
        f"🪙 {opportunity.symbol}",
        f"🆔 {identifier}",
        "",
        SECTION_SEPARATOR,
        "",
        "🟢 BUY",
        f"   {buy_label}",
        f"   {_compact_number(opportunity.buy_price, 8)}",
        "",
        "🔴 SELL",
        f"   {sell_label}",
        f"   {_compact_number(opportunity.sell_price, 8)}",
        "",
        SECTION_SEPARATOR,
        "",
        "📈 Gross spread",
        f"   {float(opportunity.raw_spread):.3f}%",
        "",
        "💰 Estimated net",
        f"   {float(opportunity.net_profit):.3f}%",
        "",
        "💸 Fees",
        "   Open Details for live taker rates",
        "",
        "📊 24h volume",
        f"   {_compact_number(opportunity.volume_buy)} / {_compact_number(opportunity.volume_sell)}",
        "",
        SECTION_SEPARATOR,
        "",
        transfer_status,
        f"🟢 Deposit on {buy_label}: {buy_status}",
        f"🔴 Withdrawal on {sell_label}: {sell_status}",
        "",
        "🔎 Review Details before acting.",
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
    return format_opportunity_card(opportunity, identifier, "🚨 NEW ARBITRAGE OPPORTUNITY")


def format_scan_summary(opportunities: Sequence[object], *, exchange_count: int, opportunities_found: int, matching_selected: int, results_shown: int) -> str:
    visible = list(opportunities)[:3]
    lines = [
        "🔎 ARBITRAGE SCAN",
        "",
        SECTION_SEPARATOR,
        "",
        "✅ SCAN COMPLETE",
        "",
        f"🌐 Exchanges checked: {exchange_count}",
        f"🔎 Opportunities found: {opportunities_found}",
        f"🎯 Matching selected exchanges: {matching_selected}",
        f"📋 Results shown: {results_shown}",
        "",
        SECTION_SEPARATOR,
        "",
        "🏆 TOP OPPORTUNITIES",
    ]
    for index, opportunity in enumerate(visible, 1):
        label = ["1️⃣", "2️⃣", "3️⃣"][index - 1]
        lines.extend([
            "",
            f"{label} 🪙 {opportunity.symbol}",
            f"   📈 {float(opportunity.raw_spread):.3f}%",
            f"   {opportunity.buy_exchange} → {opportunity.sell_exchange}",
        ])
    return "\n".join(lines)


def format_order_book(levels: Iterable[Sequence[float]], *, title: str) -> str:
    rows = []
    for level in list(levels)[:5]:
        if len(level) >= 2:
            rows.append(f"{float(level[0]):.8f} x {float(level[1]):.6f}")
    return f"{title}\n" + ("\n".join(rows) if rows else "unavailable")


def format_opportunity_details(row: dict, buy_fill: float, sell_fill: float, buy_fee: float, sell_fee: float, gross_profit: float, net_profit: float, buy_slippage: float, sell_slippage: float, transfer_text: str, buy_book: Sequence[Sequence[float]], sell_book: Sequence[Sequence[float]]) -> str:
    lines = [
        "🔎 OPPORTUNITY DETAILS",
        "",
        f"🪙 {row['symbol']}",
        "",
        SECTION_SEPARATOR,
        "",
        "🌐 EXCHANGES",
        "",
        "🟢 BUY",
        f"{row['buy_exchange']}",
        f"Price: {_compact_number(buy_fill, 8)}",
        "",
        "🔴 SELL",
        f"{row['sell_exchange']}",
        f"Price: {_compact_number(sell_fill, 8)}",
        "",
        SECTION_SEPARATOR,
        "",
        "📖 ORDER BOOK",
        "",
        "BUY SIDE",
        format_order_book(buy_book, title=""),
        "",
        "SELL SIDE",
        format_order_book(sell_book, title=""),
        "",
        SECTION_SEPARATOR,
        "",
        "💸 FEES",
        f"Exchange fees: {buy_fee:.4f} / {sell_fee:.4f}",
        "",
        "📈 PROFIT ANALYSIS",
        f"Gross: {gross_profit:.4f}",
        f"Estimated net: {net_profit:.4f}",
        "",
        "⚠️ Risk / transfer verification:",
        transfer_text,
    ]
    return "\n".join(lines)


def format_paper_trade(opportunity, *, buy_price: float, sell_price: float, size: float, expected_gross: float, estimated_net: float, profit: float) -> str:
    lines = [
        "🧪 PAPER TRADE",
        "",
        SECTION_SEPARATOR,
        "",
        f"🪙 {opportunity.symbol}",
        "",
        "🟢 BUY",
        f"{opportunity.buy_exchange} @ {_compact_number(buy_price, 8)}",
        "",
        "🔴 SELL",
        f"{opportunity.sell_exchange} @ {_compact_number(sell_price, 8)}",
        "",
        "💵 Simulated capital:",
        f"{_compact_number(size, 6)}",
        "",
        "📈 Expected gross:",
        f"{_compact_number(expected_gross, 6)}",
        "",
        "💰 Estimated net:",
        f"{_compact_number(estimated_net, 6)}",
        "",
        "📊 Estimated P/L:",
        f"{_compact_number(profit, 6)}",
        "",
        "⚠️ This is a simulation. No real funds are used.",
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
