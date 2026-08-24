from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from .arbitrage_features import calculate_executable_trade, confidence_score, freshness_label, rank_score
from .db import Database
from .filters import matches, user_filters
from .scan_diagnostics import get_last_scan_snapshot
from .scanner import opportunity_id
from .ui import format_error, format_opportunity_card, format_opportunity_details, opportunity_buttons


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _opportunity_from_row(row):
    from .exchanges.base import Opportunity
    return Opportunity(
        symbol=row["symbol"], buy_exchange=row["buy_exchange"], sell_exchange=row["sell_exchange"],
        buy_price=row["buy_price"], sell_price=row["sell_price"], raw_spread=row["raw_spread"],
        net_profit=row["net_profit"], volume_buy=row["volume_buy"] or 0, volume_sell=row["volume_sell"] or 0,
        verified=bool(row["verified"]), loose_mode=bool(row["loose_mode"]), metadata=json.loads(row["payload"] or "{}"),
    )


def enhanced_alert_card(opportunity, identifier: str, trade_size: float = 1000.0, card_number: int | None = None) -> str:
    base = format_opportunity_card(opportunity, identifier, card_number=card_number, trade_size=trade_size)
    meta = getattr(opportunity, "metadata", {}) or {}
    confidence = int(meta.get("confidence", 0))
    observed_at = meta.get("observed_at")
    try:
        age = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(observed_at)).total_seconds()) if observed_at else 0.0
    except (TypeError, ValueError):
        age = 0.0
    history = meta.get("history", [])[-6:]
    history_text = " → ".join(f"{point.get('spread', 0):.2f}%" for point in history) if history else "new"
    coverage = "🟢 Complete" if meta.get("coverage_complete") else "🟡 Partial coverage"
    rank = float(meta.get("rank_score", 0.0))
    quality = [
        "🧠 <b>OPPORTUNITY QUALITY</b>",
        f"🎯 Confidence    <b>{confidence}/100</b>",
        f"🕐 Freshness     <b>{freshness_label(age)}</b>",
        f"📡 Coverage      {coverage}",
        f"🏆 Rank score    <b>{rank:.2f}</b>",
        f"📈 Gap history   {history_text}",
        "🔬 <i>Open Order Book for executable profit at your trade size.</i>",
    ]
    # Keep the quality block inside the visual card instead of after the closing border.
    if "╰────────────────────────╯" in base:
        base = base.replace("╰────────────────────────╯", "\n".join(quality) + "\n╰────────────────────────╯", 1)
    else:
        base += "\n" + "\n".join(quality)
    return base


async def enhanced_scan_command(update, context) -> None:
    from .handlers import require_vip, _animate_scan_progress

    if not await require_vip(update, context):
        return
    scanner = context.application.bot_data.get("scanner")
    if not scanner:
        await update.effective_message.reply_text("Scanner is still starting. Try again shortly.")
        return
    target = update.effective_message or update.callback_query.message
    progress = await target.reply_text("🔎 <b>Scanning all selected exchanges</b>…", parse_mode="HTML")
    animation = asyncio.create_task(_animate_scan_progress(progress))
    try:
        user = await _db(context).get_user(update.effective_user.id)
        preferences = user_filters(user)
        selected = set(json.loads(user["selected_exchanges"] or "[]"))
        active_selected = selected & set(scanner.exchanges)
        if len(active_selected) < 2:
            await target.reply_text(format_error("Scan needs at least two active selected exchanges.", f"Selection: {', '.join(sorted(selected)) or 'none'}. Use /exchanges."), parse_mode="HTML")
            return
        opportunities = await scanner.run_cycle(require_matching_user=False, exchange_names=active_selected)
        visible = [item for item in opportunities if item.buy_exchange in selected and item.sell_exchange in selected and matches(item, preferences)]
        visible.sort(key=lambda item: item.metadata.get("rank_score", item.net_profit), reverse=True)
        visible = visible[:preferences["max_results"]]

        snapshot = get_last_scan_snapshot() or {}
        summary = snapshot.get("summary", {})
        statuses = summary.get("exchange_status", {})
        lines = [
            "🔍 <b>ARBITRAGE SCAN COMPLETE</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 Selected        <b>{len(active_selected)} exchanges</b>",
            f"📡 Healthy         <b>{sum(1 for value in statuses.values() if value.get('status') in {'ok', 'partial'})}</b>/{len(active_selected)}",
            f"🪙 Common markets  <b>{summary.get('common_markets', 0)}</b>",
            f"⚡ Positive spreads <b>{summary.get('positive_spreads', 0)}</b>",
            f"✅ Opportunities    <b>{len(visible)}</b>",
            "",
            "📡 <b>EXCHANGE HEALTH</b>",
        ]
        for name in sorted(active_selected):
            status = statuses.get(name, {})
            state = status.get("status", "not returned")
            icon = "🟢" if state == "ok" else "🟡" if state == "partial" else "🔴"
            reason = status.get("error") or ("partial market coverage" if state == "partial" else state)
            lines.append(f"{icon} {name} — {reason}")
        gaps = snapshot.get("gaps", [])
        if gaps:
            lines.extend(["", f"⚠️ <b>{len(gaps)}</b> symbols had data gaps.", "<i>Missing markets are reported instead of silently disappearing.</i>"])
            for item in gaps[:5]:
                gap_text = "; ".join(f"{name}: {reason}" for name, reason in (item.get("gaps") or {}).items())
                lines.append(f"• <b>{item.get('symbol', 'unknown')}</b> — {gap_text}")
            if len(gaps) > 5:
                lines.append(f"• … and {len(gaps) - 5} more symbols with data gaps")
        else:
            lines.extend(["", "✅ No exchange coverage gaps were reported in this scan."])
        await target.reply_text("\n".join(lines), parse_mode="HTML")

        for index, item in enumerate(visible, 1):
            identifier = opportunity_id(item)
            await _db(context).save_opportunity(identifier, item)
            await target.reply_text(enhanced_alert_card(item, identifier, float(preferences.get("trade_size", 1000)), index), reply_markup=opportunity_buttons(identifier), parse_mode="HTML")
    finally:
        animation.cancel()
        await asyncio.gather(animation, return_exceptions=True)
        try:
            await context.bot.delete_message(update.effective_user.id, progress.message_id)
        except Exception:
            pass


async def enhanced_scan_callback(update, context) -> None:
    await update.callback_query.answer()
    await enhanced_scan_command(update, context)


async def enhanced_details(update, context) -> None:
    query = update.callback_query
    await query.answer()
    db = _db(context)
    if not await db.active_vip(query.from_user.id):
        await query.edit_message_text("🔒 Active VIP access is required.")
        return
    await query.edit_message_text("⏳ <b>Building executable trade model…</b>", parse_mode="HTML")
    row = await db.get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.edit_message_text("⚠️ Opportunity expired\nRun /scan for fresh data.")
        return
    exchanges = context.application.bot_data.get("exchanges", {})
    buy_exchange = exchanges.get(row["buy_exchange"])
    sell_exchange = exchanges.get(row["sell_exchange"])
    if not buy_exchange or not sell_exchange:
        await query.edit_message_text("❌ Live exchange data is unavailable right now.")
        return
    try:
        user = await db.get_user(query.from_user.id)
        trade_size = float(user_filters(user).get("trade_size", 1000.0))
        buy_book, sell_book = await asyncio.gather(
            buy_exchange.fetch_order_book(row["symbol"], 20),
            sell_exchange.fetch_order_book(row["symbol"], 20),
        )
        buy_fee, sell_fee = await asyncio.gather(
            buy_exchange.get_taker_fee(row["symbol"]),
            sell_exchange.get_taker_fee(row["symbol"]),
        )
        result = calculate_executable_trade(
            buy_book.get("asks", []), sell_book.get("bids", []), trade_size,
            buy_fee_rate=float(buy_fee), sell_fee_rate=float(sell_fee),
        )
        buy_fill = result.spent_quote / max(result.base_amount, 1e-12)
        sell_fill = result.sell_proceeds / max(result.base_amount, 1e-12)
        metadata = json.loads(row["payload"] or "{}")
        quality = confidence_score(
            net_profit_pct=(result.net_profit / max(trade_size, 1e-9)) * 100,
            buy_volume=row["volume_buy"] or 0,
            sell_volume=row["volume_sell"] or 0,
            trade_size=trade_size,
            freshness_seconds=0,
            transfer_verified=bool(row["verified"]),
            coverage_complete=bool(metadata.get("coverage_complete")),
            executable_complete=result.complete,
        )
        executable_pct = (result.net_profit / max(trade_size, 1e-9)) * 100
        transfer = metadata.get("matching_network")
        transfer_text = f"✅ Matching route: {transfer}" if transfer else "⚠️ Transfer route requires re-check"
        message = format_opportunity_details(
            row, buy_fill, sell_fill, float(buy_fee), float(sell_fee), result.gross_profit, result.net_profit,
            result.buy_slippage_pct, result.sell_slippage_pct, transfer_text,
            buy_book.get("asks", []), sell_book.get("bids", []),
        )
        message += "\n\n🧠 <b>EXECUTION QUALITY</b>\n"
        message += f"💰 Requested trade   <b>${trade_size:,.2f}</b>\n"
        message += f"🪙 Executable amount <b>{result.base_amount:,.8f}</b>\n"
        message += f"💵 Gross P/L         <b>${result.gross_profit:,.4f}</b>\n"
        message += f"✅ Net P/L           <b>${result.net_profit:,.4f}</b>\n"
        message += f"🎯 Confidence        <b>{quality}/100</b>\n"
        message += f"🏆 Execution rank    <b>{rank_score(row['net_profit'], quality, executable_pct):.2f}</b>\n"
        message += ("✅ Full requested size executable" if result.complete else "⚠️ Order-book depth cannot fill the full requested size")
    except Exception as exc:
        await query.edit_message_text(f"⚠️ Executable analysis unavailable: {type(exc).__name__}: {exc}")
        return
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"feature_details:{row['id']}"), InlineKeyboardButton("🎮 Paper Trade", callback_data=f"paper:{row['id']}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:{row['id']}")],
        ]),
        parse_mode="HTML",
    )


def build_feature_handlers():
    return [
        CommandHandler("scan", enhanced_scan_command),
        CallbackQueryHandler(enhanced_scan_callback, pattern=r"^ui:scan$"),
        CallbackQueryHandler(enhanced_details, pattern=r"^details:"),
        CallbackQueryHandler(enhanced_details, pattern=r"^feature_details:"),
    ]
