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


def _escape_html(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_error(message: str, action: str = "Try again in a moment") -> str:
    """Error message template - 2 lines max."""
    return f"❌ {_safe_text(message)[:200]}\n🔧 {_safe_text(action)[:200]}"


def format_success(message: str) -> str:
    """Success confirmation - single line."""
    return f"✅ {_safe_text(message)[:200]}"


def format_scan_count(count: int) -> str:
    """Simple count message for scan results."""
    if count == 0:
        return "🔍 No opportunities found matching your filters"
    return f"🔍 Found {count} opportunities"


def format_opportunity_card(
    opportunity,
    identifier: str,
    card_number: int | None = None,
    tag: str | None = None,
    trade_size: float | None = None,
) -> str:
    """
    Unified opportunity card template for scans/alerts/loose/verified.
    
    Args:
        opportunity: Opportunity object with symbol, exchanges, prices, spread, net_profit, volumes, metadata
        identifier: Unique ID for this opportunity
        card_number: Optional card number (1-10 for emoji, then #4+)
        tag: Card tag - auto-determined if None
        trade_size: Optional trade size for profit calcs
    """
    # Determine tag if not provided
    if tag is None:
        if card_number is not None:
            if card_number <= 10:
                emoji_map = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
                tag = f"{emoji_map[card_number - 1]} 🔍 SCAN RESULT"
            else:
                tag = f"#{card_number} 🔍 SCAN RESULT"
        elif getattr(opportunity, "loose_mode", False):
            tag = "⚠️ LOOSE MODE ARBITRAGE"
        elif getattr(opportunity, "net_profit", 0) >= 3.0:  # High margin threshold
            tag = "🚨 HIGH-MARGIN ARBITRAGE"
        else:
            tag = "🔴 LIVE ARBITRAGE"
    
    # Extract metadata
    metadata = getattr(opportunity, "metadata", {}) or {}
    buy_transfer = metadata.get("buy_transfer", {})
    sell_transfer = metadata.get("sell_transfer", {})
    transfer_verify = metadata.get("transfer_verification")
    
    # Build transfer line
    if transfer_verify == "loose_mode":
        transfer_line = "⚠️ Transfer checks skipped — verify manually"
    elif transfer_verify == "not_verified":
        transfer_line = "🛡️ ⚠️ Unverified (manual check needed)"
    else:
        matching_net = metadata.get("matching_network", "")
        buy_nets = [n.get("network") for n in buy_transfer.get("networks", []) if n.get("deposit")]
        sell_nets = [n.get("network") for n in sell_transfer.get("networks", []) if n.get("withdraw")]
        if matching_net and buy_nets and sell_nets:
            transfer_line = f"✅ Verified ({matching_net})"
        else:
            transfer_line = "⚠️ Verification pending"
    
    # Calculate profit breakdown if trade_size provided
    gross_profit_val = ""
    fees_val = ""
    network_fee_val = ""
    if trade_size and trade_size > 0:
        gross = trade_size * (opportunity.raw_spread / 100)
        fees = trade_size * ((opportunity.raw_spread - opportunity.net_profit) / 100)
        net = trade_size * (opportunity.net_profit / 100)
        gross_profit_val = f"${_compact_number(gross, 4)}"
        fees_val = f"${_compact_number(fees, 4)}"
        net_val = f"${_compact_number(net, 4)}"
    else:
        gross_profit_val = "%"
        fees_val = "%"
        net_val = "%"
    
    lines = [
        tag,
        "",
        f"Pair: <b>{_escape_html(opportunity.symbol)}</b>",
        SECTION_SEPARATOR,
        f"🟢 BUY",
        f"   Exchange : {_escape_html(opportunity.buy_exchange)}",
        f"   Price    : ${_compact_number(opportunity.buy_price, 8)}",
        "",
        f"🔴 SELL",
        f"   Exchange : {_escape_html(opportunity.sell_exchange)}",
        f"   Price    : ${_compact_number(opportunity.sell_price, 8)}",
        SECTION_SEPARATOR,
        f"📊 Profit Breakdown",
        f"• Gross Profit : {gross_profit_val if trade_size else f'{opportunity.raw_spread:.2f}%'}",
        f"• Trading Fees : - {fees_val if trade_size else f'{opportunity.raw_spread - opportunity.net_profit:.2f}%'}",
        f"• Net Profit   : {net_val if trade_size else f'{opportunity.net_profit:.2f}%'}",
        f"• Net Spread   : {opportunity.net_profit:.2f}%",
        "",
        f"📋 Extra Details",
        f"• Trade Size   : {_compact_number(trade_size or 1000, 4)}",
        f"• Coin Amount  : {_compact_number(trade_size or 1000 / opportunity.buy_price if opportunity.buy_price > 0 else 0, 6)} {opportunity.symbol.split('/')[0]}",
        f"• {transfer_line}",
    ]
    
    return "\n".join(lines)


def opportunity_buttons(identifier: str) -> InlineKeyboardMarkup:
    """Buttons for opportunity cards."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 View Order Book", callback_data=f"details:{identifier}")],
        [InlineKeyboardButton("🎮 Paper Trade This!", callback_data=f"paper:{identifier}")],
    ])


def format_background_alert(opportunity, identifier: str) -> str:
    """Format a live background alert (no numbering)."""
    return format_opportunity_card(opportunity, identifier, card_number=None)


def format_scan_summary(opportunities: Sequence[object], *, exchange_count: int, opportunities_found: int, matching_selected: int, results_shown: int) -> str:
    """
    DEPRECATED - use format_scan_count instead.
    Kept for backwards compatibility but will be removed.
    """
    if not opportunities:
        return "🔍 No opportunities found matching your filters"
    return f"🔍 Found {len(opportunities)} opportunities"


def format_order_book(levels: Iterable[Sequence[float]], *, title: str) -> str:
    """Format order book display."""
    rows = []
    for level in list(levels)[:5]:
        if len(level) >= 2:
            rows.append(f"{float(level[0]):.8f} x {float(level[1]):.6f}")
    body = " • ".join(rows) if rows else "unavailable"
    return f"{title}{body}" if title else body


def format_opportunity_details(row: dict, buy_fill: float, sell_fill: float, buy_fee: float, sell_fee: float, gross_profit: float, net_profit: float, buy_slippage: float, sell_slippage: float, transfer_text: str, buy_book: Sequence[Sequence[float]], sell_book: Sequence[Sequence[float]]) -> str:
    """Format detailed opportunity view with order book."""
    lines = [
        f"📖 ORDER BOOK • <b>{_escape_html(row['symbol'])}</b>",
        SECTION_SEPARATOR,
        f"<b>🟢 {_escape_html(row['buy_exchange'])} asks:</b>",
        format_order_book(buy_book, title=""),
        "",
        f"<b>🔴 {_escape_html(row['sell_exchange'])} bids:</b>",
        format_order_book(sell_book, title=""),
        SECTION_SEPARATOR,
        f"📊 Analysis",
        f"• Gross Profit  : ${_compact_number(gross_profit, 4)}",
        f"• Buy Fee       : {buy_fee * 100:.4f}%",
        f"• Sell Fee      : {sell_fee * 100:.4f}%",
        f"• Net Profit    : ${_compact_number(net_profit, 4)}",
        f"• Buy Slippage  : {buy_slippage:.2f}%",
        f"• Sell Slippage : {sell_slippage:.2f}%",
        f"• Volume (buy)  : {_compact_number(row['volume_buy'])}",
        f"• Volume (sell) : {_compact_number(row['volume_sell'])}",
        SECTION_SEPARATOR,
        f"🛡️ Transfer Status",
        transfer_text.replace("\n", " • "),
    ]
    return "\n".join(lines)


def format_paper_trade(opportunity, *, buy_price: float, sell_price: float, size: float, expected_gross: float, estimated_net: float, profit: float) -> str:
    """Format paper trade confirmation."""
    lines = [
        "🎮 PAPER TRADE OPENED",
        SECTION_SEPARATOR,
        f"Pair: <b>{_escape_html(opportunity.symbol)}</b>",
        f"🟢 {_escape_html(opportunity.buy_exchange)} ${_compact_number(buy_price, 8)} → 🔴 {_escape_html(opportunity.sell_exchange)} ${_compact_number(sell_price, 8)}",
        "",
        f"💵 Size        : ${_compact_number(size, 6)}",
        f"📈 Gross       : ${_compact_number(expected_gross, 6)}",
        f"💰 Net P/L     : ${_compact_number(estimated_net, 6)}",
        SECTION_SEPARATOR,
        "⚠️ Simulation only — no real funds used",
    ]
    return "\n".join(lines)


def format_status_message(vip_status: str, vip_expiry: str | None, exchanges: list[str], loose_mode: bool, paused: bool, filters: dict) -> str:
    """Format account status display."""
    expiry_text = ""
    if vip_expiry:
        expiry_text = f" (until {vip_expiry[:10]})" if len(vip_expiry) > 10 else f" ({vip_expiry})"
    
    lines = [
        "👤 ACCOUNT",
        SECTION_SEPARATOR,
        f"💎 VIP       : {vip_status}{expiry_text}",
        f"🌐 Exchanges : {', '.join(exchanges) if exchanges else 'none selected'}",
        f"⏸ Paused    : {'Yes' if paused else 'No'}",
        f"⚠️ Loose Mode: {'On' if loose_mode else 'Off'}",
        "",
        "📈 Filters",
        f"• Profit  : {filters.get('min_profit', 0)}% – {filters.get('max_profit', 100)}%",
        f"• Spread  : {filters.get('min_spread', 0)}% – {filters.get('max_spread', 100)}%",
        f"• Volume  : ≥ ${_compact_number(filters.get('min_volume', 10000))}",
        f"• Cooldown: {filters.get('alert_cooldown', 300)} sec",
    ]
    return "\n".join(lines)


def format_filters_message(filters: dict) -> str:
    """Format filters display."""
    watchlist_text = ", ".join(filters.get("watchlist", [])) if filters.get("watchlist") else "none — scanning all pairs"
    blacklist_text = ", ".join(filters.get("blacklist", [])) if filters.get("blacklist") else "none"
    
    lines = [
        "🎛 YOUR FILTERS",
        SECTION_SEPARATOR,
        f"📈 Profit range  : {filters.get('min_profit', 0)}% – {filters.get('max_profit', 100)}%",
        f"📊 Spread range  : {filters.get('min_spread', 0)}% – {filters.get('max_spread', 100)}%",
        f"💧 Min volume    : ${_compact_number(filters.get('min_volume', 10000))}",
        f"👁 Watchlist     : {watchlist_text}",
        f"🚫 Blacklist     : {blacklist_text}",
        f"🕒 Alert cooldown: {filters.get('alert_cooldown', 300)} sec",
        f"📋 Max results   : {filters.get('max_results', 10)}",
        f"⏸ Paused        : {'Yes' if filters.get('paused') else 'No'}",
        f"⚠️ Loose mode    : {'On' if filters.get('loose_mode') else 'Off'}",
        "",
        "Use /setminprofit, /setmaxprofit, /setminspread, /setmaxspread,",
        "/setminvolume, /watchlist, /blacklist, /setalertfreq to change these.",
    ]
    return "\n".join(lines)


def format_leaderboard(rows: list, period: str, user_rank: int | None, user_profit: float | None) -> str:
    """Format leaderboard display."""
    emoji_ranks = ["🥇", "🥈", "🥉"]
    
    if not rows:
        return "🏆 LEADERBOARD\n" + SECTION_SEPARATOR + "\n📭 No trades yet"
    
    lines = [
        f"🏆 LEADERBOARD — {period.upper()}",
        SECTION_SEPARATOR,
    ]
    
    for index, row in enumerate(rows[:10], 1):
        emoji = emoji_ranks[index - 1] if index <= 3 else f"{index}."
        username = row.get("username") or f"User{row.get('telegram_id')}"
        profit = row.get("total", 0)
        lines.append(f"{emoji} {username}   ${_compact_number(profit, 4)}")
    
    if user_rank is not None and user_profit is not None:
        lines.append("")
        lines.append(f"Your rank: #{user_rank}  •  ${_compact_number(user_profit, 4)}")
    
    lines.append(SECTION_SEPARATOR)
    lines.append("/leaderboard hide — remove yourself from public view")
    
    return "\n".join(lines)


def format_portfolio(user_trades: list, total_balance: float, vip_limit: float) -> str:
    """Format portfolio display."""
    total_pnl = sum(t.get("profit", 0) for t in user_trades)
    
    lines = [
        "📊 YOUR PORTFOLIO",
        SECTION_SEPARATOR,
        f"💰 Simulated Balance: ${_compact_number(total_balance, 4)}",
        f"💵 Total P/L: ${_compact_number(total_pnl, 4)}",
        f"📈 Open Positions: {len(user_trades)}",
        f"🎯 VIP Limit: ${_compact_number(vip_limit, 4)}",
        SECTION_SEPARATOR,
    ]
    
    if user_trades:
        lines.append("Recent Trades:")
        for trade in user_trades[:5]:
            symbol = trade.get("symbol", "unknown")
            size = trade.get("size", 0)
            pnl = trade.get("profit", 0)
            date = trade.get("created_at", "")[:10] if trade.get("created_at") else "unknown"
            lines.append(f"• {symbol} {_compact_number(size, 4)} @ {date}: ${_compact_number(pnl, 4)}")
        if len(user_trades) > 5:
            lines.append(f"... and {len(user_trades) - 5} more trades")
    else:
        lines.append("No trades yet. Use Paper Trade button on opportunities to start!")
    
    return "\n".join(lines)
