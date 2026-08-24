from __future__ import annotations

from typing import Iterable, Sequence

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .scan_diagnostics import get_last_scan_diagnostics

TOP = "╭────────────────────────╮"
BOTTOM = "╰────────────────────────╯"
SECTION_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
THIN_SEPARATOR = "────────────────────"


def _compact_number(value: float | int, digits: int = 8) -> str:
    if abs(float(value)) >= 1_000_000:
        return f"{float(value):,.0f}"
    if abs(float(value)) >= 1_000:
        return f"{float(value):,.2f}"
    return format(float(value), f".{digits}f").rstrip("0").rstrip(".")


def _safe_text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _panel(title: str, subtitle: str | None = None) -> list[str]:
    lines = [TOP, f"│  {title}"]
    if subtitle:
        lines.append(f"│  {subtitle}")
    lines.extend([BOTTOM, ""])
    return lines


def format_error(message: str, action: str = "Try again in a moment") -> str:
    # Keep the legacy leading marker while retaining the redesigned panel below it.
    return f"❌ {_safe_text(message)[:220]}\n🔧 {_safe_text(action)[:220]}\n\n" + "\n".join(_panel("⚠️ SOMETHING WENT WRONG", "The bot could not complete that action"))


def format_success(message: str) -> str:
    return f"╭─ ✅ SUCCESS\n│ {_safe_text(message)[:240]}\n╰────────────────────"


def format_scan_count(count: int) -> str:
    diagnostics = get_last_scan_diagnostics()
    lines = _panel("🔍 SCAN COMPLETE", "Live market comparison finished")
    if count:
        lines.append(f"✨ Found {count} opportunities")
        lines.append(f"✨ Opportunities found : <b>{count}</b>")
        lines.append("💡 Open a result below for full analysis.")
    else:
        lines.append("📭 No opportunities found")
        lines.append("📭 Opportunities found : <b>0</b>")
    if diagnostics:
        lines.extend(["", "⚠️ DATA QUALITY", THIN_SEPARATOR])
        for item in diagnostics[:6]:
            symbol = _escape_html(str(item.get("symbol", "unknown")))
            gaps = item.get("gaps", {}) or {}
            gap_text = "; ".join(f"{_escape_html(str(exchange))}: {_escape_html(str(reason))[:100]}" for exchange, reason in gaps.items())
            lines.append(f"• <b>{symbol}</b> — {gap_text}")
        if len(diagnostics) > 6:
            lines.append(f"• … +{len(diagnostics) - 6} more symbols with data gaps")
    return "\n".join(lines)


def format_opportunity_card(opportunity, identifier: str, card_number: int | str | None = None, tag: str | None = None, trade_size: float | None = None, title: str | None = None) -> str:
    # Backward-compatible title keyword/positional argument used by older callers/tests.
    if isinstance(card_number, str) and tag is None and title is None:
        title, card_number = card_number, None
    if title is not None:
        tag = title
    if tag is None:
        if card_number is not None:
            rank = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            number = int(card_number)
            tag = f"{rank[number - 1] if number <= 10 else f'#{number}'} 🔍 SCAN RESULT"
        elif getattr(opportunity, "loose_mode", False):
            tag = "⚠️ LOOSE-MODE OPPORTUNITY"
        elif getattr(opportunity, "net_profit", 0) >= 3.0:
            tag = "🚨 HIGH-MARGIN OPPORTUNITY"
        else:
            tag = "⚡ LIVE OPPORTUNITY"
    metadata = getattr(opportunity, "metadata", {}) or {}
    transfer_verify = metadata.get("transfer_verification")
    if transfer_verify == "loose_mode":
        transfer_line = "⚠️ Transfer checks skipped — verify manually"
    elif transfer_verify == "not_verified":
        transfer_line = "🛡️ Unverified — manual check recommended"
    else:
        matching_net = metadata.get("matching_network", "")
        buy_transfer = metadata.get("buy_transfer", {})
        sell_transfer = metadata.get("sell_transfer", {})
        buy_nets = [n.get("network") for n in buy_transfer.get("networks", []) if n.get("deposit")]
        sell_nets = [n.get("network") for n in sell_transfer.get("networks", []) if n.get("withdraw")]
        transfer_line = f"✅ Verified route • {matching_net}" if matching_net and buy_nets and sell_nets else "⏳ Transfer verification pending"
    if trade_size and trade_size > 0:
        gross = trade_size * opportunity.raw_spread / 100
        fees = trade_size * (opportunity.raw_spread - opportunity.net_profit) / 100
        net = trade_size * opportunity.net_profit / 100
        gross_text, fees_text, net_text, size_text = f"${_compact_number(gross, 4)}", f"${_compact_number(fees, 4)}", f"${_compact_number(net, 4)}", f"${_compact_number(trade_size, 4)}"
    else:
        gross_text, fees_text, net_text, size_text = f"{opportunity.raw_spread:.2f}%", f"{opportunity.raw_spread - opportunity.net_profit:.2f}%", f"{opportunity.net_profit:.2f}%", "$1,000"
    coin = opportunity.symbol.split("/")[0]
    coin_amount = trade_size / opportunity.buy_price if trade_size and opportunity.buy_price > 0 else 1000 / opportunity.buy_price if opportunity.buy_price > 0 else 0
    lines = _panel(tag, f"<b>{_escape_html(opportunity.symbol)}</b> • live market data")
    lines.extend(["🟢 <b>BUY HERE</b>", f"   🌐 {_escape_html(opportunity.buy_exchange)}  •  <b>${_compact_number(opportunity.buy_price, 8)}</b>", "", "🔴 <b>SELL HERE</b>", f"   🌐 {_escape_html(opportunity.sell_exchange)}  •  <b>${_compact_number(opportunity.sell_price, 8)}</b>", "", SECTION_SEPARATOR, "💰 <b>PROFIT SNAPSHOT</b>", f"   📈 Gross       {gross_text}", f"   💸 Fees        − {fees_text}", f"   🚀 <b>NET        {net_text}</b>", f"   🎯 Net Spread  <b>{opportunity.net_profit:.2f}%</b>", "", "📋 <b>TRADE DETAILS</b>", f"   💵 Size        {size_text}", f"   🪙 Amount      {_compact_number(coin_amount, 6)} {coin}", f"   🛡️ {transfer_line}", BOTTOM])
    return "\n".join(lines)


def opportunity_buttons(identifier: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("📖  Order Book & Analysis", callback_data=f"details:{identifier}")], [InlineKeyboardButton("🎮  Paper Trade", callback_data=f"paper:{identifier}")]])


def format_background_alert(opportunity, identifier: str) -> str:
    return format_opportunity_card(opportunity, identifier, card_number=None)


def format_scan_summary(opportunities: Sequence[object], *, exchange_count: int, opportunities_found: int, matching_selected: int, results_shown: int) -> str:
    if not opportunities:
        return f"🔍 <b>SCAN COMPLETE</b>\n\n📭 No opportunities found\n🌐 {exchange_count} exchanges checked.\n📊 {opportunities_found} found • {matching_selected} matched filters • {results_shown} shown."
    return f"🔍 <b>SCAN COMPLETE</b>\n\n✨ {len(opportunities)} opportunities ready to review.\n🌐 {exchange_count} exchanges checked.\n📊 {opportunities_found} found • {matching_selected} matched filters • {results_shown} shown."


def format_order_book(levels: Iterable[Sequence[float]], *, title: str) -> str:
    rows = [f"{float(level[0]):.8f} × {float(level[1]):.6f}" for level in list(levels)[:5] if len(level) >= 2]
    body = "\n".join(rows) if rows else "Unavailable"
    return f"{title}{body}" if title else body


def format_opportunity_details(row: dict, buy_fill: float, sell_fill: float, buy_fee: float, sell_fee: float, gross_profit: float, net_profit: float, buy_slippage: float, sell_slippage: float, transfer_text: str, buy_book: Sequence[Sequence[float]], sell_book: Sequence[Sequence[float]]) -> str:
    lines = _panel("📖 DETAILS", f"<b>{_escape_html(row['symbol'])}</b> • order book & execution analysis")
    lines.extend([f"🟢 <b>{_escape_html(row['buy_exchange'])} — ASKS</b>", format_order_book(buy_book, title=""), "", f"🔴 <b>{_escape_html(row['sell_exchange'])} — BIDS</b>", format_order_book(sell_book, title=""), "", SECTION_SEPARATOR, "🧮 <b>EXECUTION ANALYSIS</b>", f"   💵 Gross profit  ${_compact_number(gross_profit, 4)}", f"   💸 Buy fee       {buy_fee * 100:.4f}%", f"   💸 Sell fee      {sell_fee * 100:.4f}%", f"   🚀 <b>Net profit    ${_compact_number(net_profit, 4)}</b>", f"   📉 Buy slippage  {buy_slippage:.2f}%", f"   📈 Sell slippage {sell_slippage:.2f}%", f"   💧 Buy volume    {_compact_number(row['volume_buy'])}", f"   💧 Sell volume   {_compact_number(row['volume_sell'])}", "", SECTION_SEPARATOR, "🛡️ <b>TRANSFER STATUS</b>", transfer_text.replace("\n", " • "), BOTTOM])
    return "\n".join(lines)


def format_paper_trade(opportunity, *, buy_price: float, sell_price: float, size: float, expected_gross: float, estimated_net: float, profit: float) -> str:
    pnl_icon = "🟢" if profit >= 0 else "🔴"
    lines = _panel("🎮 PAPER TRADE OPENED", "Simulation only • no real funds used")
    lines.extend([f"🪙 <b>{_escape_html(opportunity.symbol)}</b>", f"🟢 {_escape_html(opportunity.buy_exchange)}  ${_compact_number(buy_price, 8)}", "        ↓  simulated route", f"🔴 {_escape_html(opportunity.sell_exchange)}  ${_compact_number(sell_price, 8)}", "", SECTION_SEPARATOR, f"💵 Position size  ${_compact_number(size, 6)}", f"📈 Gross result   ${_compact_number(expected_gross, 6)}", f"{pnl_icon} <b>Net P/L         ${_compact_number(estimated_net, 6)}</b>", BOTTOM])
    return "\n".join(lines)


def format_status_message(vip_status: str, vip_expiry: str | None, exchanges: list[str], loose_mode: bool, paused: bool, filters: dict) -> str:
    expiry = f" • until {vip_expiry[:10]}" if vip_expiry else ""
    lines = _panel("👤 ACCOUNT CENTER", "Your arbitrage workspace")
    lines.extend([f"💎 VIP       {vip_status}{expiry}", f"🌐 <b>Route</b>     {', '.join(exchanges) if exchanges else 'No exchanges selected'}", f"⏯ <b>Alerts</b>    {'PAUSED' if paused else 'LIVE'}", f"⚠️ <b>Loose mode</b> {'ON' if loose_mode else 'OFF'}", "", SECTION_SEPARATOR, "🎛️ <b>ACTIVE FILTERS</b>", f"📈 Profit       {filters.get('min_profit', 0)}% → {filters.get('max_profit', 100)}%", f"📊 Spread       {filters.get('min_spread', 0)}% → {filters.get('max_spread', 100)}%", f"💧 Min volume   ${_compact_number(filters.get('min_volume', 10000))}", f"⏱️ Cooldown     {filters.get('alert_cooldown', 300)}s", BOTTOM])
    return "\n".join(lines)


def format_filters_message(filters: dict) -> str:
    watchlist = ", ".join(filters.get("watchlist", [])) if filters.get("watchlist") else "All pairs"
    blacklist = ", ".join(filters.get("blacklist", [])) if filters.get("blacklist") else "None"
    lines = _panel("🎛 YOUR FILTERS", "Tune what counts as an opportunity")
    lines.extend([f"📈 <b>Profit</b>      {filters.get('min_profit', 0)}% → {filters.get('max_profit', 100)}%", f"📊 <b>Spread</b>      {filters.get('min_spread', 0)}% → {filters.get('max_spread', 100)}%", f"💧 <b>Volume</b>      ≥ ${_compact_number(filters.get('min_volume', 10000))}", f"👁 <b>Watchlist</b>   {watchlist}", f"🚫 <b>Blacklist</b>   {blacklist}", f"🕒 <b>Cooldown</b>    {filters.get('alert_cooldown', 300)} sec", f"📋 <b>Max results</b> {filters.get('max_results', 10)}", f"⏸ <b>Paused</b>      {'Yes' if filters.get('paused') else 'No'}", f"⚠️ <b>Loose mode</b> {'On' if filters.get('loose_mode') else 'Off'}", "", THIN_SEPARATOR, "Use /setminprofit, /setmaxprofit, /setminspread, /setmaxspread,", "/setminvolume, /watchlist, /blacklist and /setalertfreq.", BOTTOM])
    return "\n".join(lines)


def format_leaderboard(rows: list, period: str, user_rank: int | None, user_profit: float | None) -> str:
    if not rows:
        return "🏆 <b>LEADERBOARD</b>\n" + SECTION_SEPARATOR + "\n📭 No paper trades yet."
    ranks = ["🥇", "🥈", "🥉"]
    lines = _panel("🏆 LEADERBOARD", f"{period.upper()} • paper trading")
    for index, row in enumerate(rows[:10], 1):
        rank = ranks[index - 1] if index <= 3 else f"{index:02d}."
        username = _escape_html(str(row.get("username") or f"User{row.get('telegram_id')}"))
        lines.append(f"{rank} <b>{username}</b>   ${_compact_number(row.get('total', 0), 4)}")
    if user_rank is not None and user_profit is not None:
        lines.extend(["", THIN_SEPARATOR, f"👤 Your rank: #{user_rank} • ${_compact_number(user_profit, 4)}"])
    lines.extend(["", "🔒 Use /leaderboard hide to hide your ranking.", BOTTOM])
    return "\n".join(lines)


def format_portfolio(user_trades: list, total_balance: float, vip_limit: float) -> str:
    total_pnl = sum(t.get("profit", 0) for t in user_trades)
    pnl_icon = "🟢" if total_pnl >= 0 else "🔴"
    lines = _panel("📊 YOUR PORTFOLIO", "Your simulated trading dashboard")
    lines.extend([f"💰 <b>Balance</b>     ${_compact_number(total_balance, 4)}", f"{pnl_icon} <b>Total P/L</b>    ${_compact_number(total_pnl, 4)}", f"📈 <b>Trades</b>       {len(user_trades)}", f"🎯 <b>VIP limit</b>    ${_compact_number(vip_limit, 4)}", "", SECTION_SEPARATOR])
    if user_trades:
        lines.append("🧾 <b>RECENT TRADES</b>")
        for trade in user_trades[:5]:
            symbol = _escape_html(str(trade.get("symbol", "unknown")))
            pnl = trade.get("profit", 0)
            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{icon} {symbol} • ${_compact_number(trade.get('size', 0), 4)} • {str(trade.get('created_at', ''))[:10] or 'unknown'} • P/L ${_compact_number(pnl, 4)}")
        if len(user_trades) > 5:
            lines.append(f"… +{len(user_trades) - 5} more trades")
    else:
        lines.extend(["📭 No paper trades yet.", "💡 Use <b>🎮 Paper Trade</b> on an opportunity to begin."])
    lines.append(BOTTOM)
    return "\n".join(lines)
