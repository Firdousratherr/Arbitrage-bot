from __future__ import annotations

import asyncio
import json
from html import escape

from . import handlers
from .scan_diagnostics import get_last_scan_snapshot


def _exchange_line(exchanges: list[str]) -> str:
    return " ↔ ".join(escape(name.upper()) for name in exchanges) or "SELECTED EXCHANGES"


def _filter_reasons(opportunity, filters: dict) -> list[str]:
    reasons: list[str] = []
    profit = float(opportunity.net_profit)
    spread = float(opportunity.raw_spread)
    min_profit = float(filters.get("min_profit", 0))
    max_profit = float(filters.get("max_profit", float("inf")))
    min_spread = float(filters.get("min_spread", 0))
    max_spread = float(filters.get("max_spread", float("inf")))
    min_volume = float(filters.get("min_volume", 0))
    volume = min(float(opportunity.volume_buy), float(opportunity.volume_sell))

    if profit < min_profit:
        reasons.append(f"Profit {profit:.2f}% < minimum {min_profit:.2f}%")
    elif profit > max_profit:
        reasons.append(f"Profit {profit:.2f}% > maximum {max_profit:.2f}%")
    if spread < min_spread:
        reasons.append(f"Spread {spread:.2f}% < minimum {min_spread:.2f}%")
    elif spread > max_spread:
        reasons.append(f"Spread {spread:.2f}% > maximum {max_spread:.2f}%")
    if volume < min_volume:
        reasons.append(f"Volume ${volume:,.2f} < minimum ${min_volume:,.2f}")

    symbol = str(opportunity.symbol).upper()
    watchlist = {str(item).upper() for item in filters.get("watchlist", [])}
    blacklist = {str(item).upper() for item in filters.get("blacklist", [])}
    if watchlist and symbol not in watchlist:
        reasons.append("Symbol is not in watchlist")
    if symbol in blacklist:
        reasons.append("Symbol is blacklisted")
    return reasons


def _format_filtered_candidates(candidates: list, filters: dict) -> list[str]:
    lines: list[str] = []
    for opportunity in candidates[:8]:
        reasons = _filter_reasons(opportunity, filters)
        if not reasons:
            continue
        symbol = escape(str(opportunity.symbol))
        detail = "; ".join(escape(reason) for reason in reasons)
        lines.append(f"• <b>{symbol}</b> — {detail}")
    if len(candidates) > 8:
        lines.append(f"• … and {len(candidates) - 8} more candidates")
    return lines


def _format_scan_result(count: int, candidates: list | None = None, filters: dict | None = None) -> str:
    data = get_last_scan_snapshot() or {}
    summary = data.get("summary", {}) if isinstance(data, dict) else {}
    gaps = data.get("gaps", []) if isinstance(data, dict) else []
    common = int(summary.get("common_markets", 0) or 0)
    positive = int(summary.get("positive_spreads", 0) or 0)
    detected = int(summary.get("opportunities_detected", summary.get("opportunities_before_filters", 0)) or 0)
    returned = summary.get("returned_by_exchange", {}) or {}
    exchange_status = summary.get("exchange_status", {}) or {}

    if count:
        lines = [f"🔍 <b>Found {count} opportunities</b>"]
    elif detected:
        lines = [
            "🔍 <b>No opportunities matched your filters</b>",
            f"📊 {detected} profitable candidate{'s' if detected != 1 else ''} were detected before your filters.",
        ]
        if candidates and filters:
            filtered_lines = _format_filtered_candidates(candidates, filters)
            if filtered_lines:
                lines.extend(["", "❌ <b>FILTERED OUT</b>", *filtered_lines])
    else:
        lines = ["🔍 <b>No arbitrage opportunities found</b>"]

    if exchange_status:
        failed = []
        partial = []
        for name, status in exchange_status.items():
            state = str((status or {}).get("status", "unknown"))
            if state == "fetch failed":
                failed.append((name, (status or {}).get("error", "unknown error")))
            elif state in {"partial", "no tickers returned"}:
                partial.append((name, state))
        if failed:
            lines.extend(["", "🚨 <b>EXCHANGE FETCH ERRORS</b>"])
            for name, error in failed:
                lines.append(f"• <b>{escape(str(name).upper())}</b> — {escape(str(error))[:180]}")
        if partial:
            lines.extend(["", "⚠️ <b>EXCHANGE COVERAGE</b>"])
            for name, state in partial:
                lines.append(f"• <b>{escape(str(name).upper())}</b> — {escape(str(state))}")

    if returned:
        coverage = "  •  ".join(f"{escape(str(name).upper())}: {value:,}" for name, value in returned.items())
        lines.extend(["", f"🌐 <b>Usable markets</b>  {coverage}"])
    lines.append(f"🔗 Common markets: <b>{common:,}</b>")
    lines.append(f"📈 Positive-spread candidates: <b>{positive:,}</b>")

    if gaps:
        lines.extend(["", "⚠️ <b>CROSS-EXCHANGE COVERAGE</b>"])
        for item in gaps[:8]:
            symbol = escape(str(item.get("symbol", "unknown")))
            gap_text = "; ".join(
                f"{escape(str(exchange))}: {escape(str(reason))[:100]}"
                for exchange, reason in (item.get("gaps", {}) or {}).items()
            )
            lines.append(f"• <b>{symbol}</b> — {gap_text}")
        if len(gaps) > 8:
            lines.append(f"• … and {len(gaps) - 8} more coverage differences")

    return "\n".join(lines)


async def _animate_scan_progress(message, exchanges: list[str]) -> None:
    exchange_line = _exchange_line(exchanges)
    frames = [
        f"🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n🌐 {exchange_line}\n\n📡 <i>Connecting to live markets…</i>",
        f"🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n🌐 {exchange_line}\n\n📡 <i>Fetching live market data…</i>",
        f"🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n🌐 {exchange_line}\n\n⚡ <i>Comparing prices…</i>",
        f"🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n🌐 {exchange_line}\n\n🧮 <i>Calculating spreads…</i>",
        f"🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n🌐 {exchange_line}\n\n💰 <i>Checking profitable opportunities…</i>",
    ]
    index = 0
    try:
        while True:
            await message.edit_text(frames[index % len(frames)], parse_mode="HTML")
            index += 1
            await asyncio.sleep(0.8)
    except asyncio.CancelledError:
        raise
    except Exception:
        handlers.logger.debug("scan progress animation stopped", exc_info=True)


def install() -> None:
    handlers._animate_scan_progress = _animate_scan_progress

    async def scan_command(update, context):
        if not await handlers.require_vip(update, context):
            return
        scanner = context.application.bot_data.get("scanner")
        if not scanner:
            await update.effective_message.reply_text("Scanner is still starting. Try again shortly.")
            return
        user = await handlers.get_db(context).get_user(update.effective_user.id)
        preferences = handlers.user_filters(user)
        selected = set(json.loads(user["selected_exchanges"] or "[]"))
        active_selected = selected & set(scanner.exchanges)
        if len(active_selected) < 2:
            await update.effective_message.reply_text(
                handlers.format_error(
                    "Scan needs at least two active selected exchanges.",
                    f"Your selection: {', '.join(sorted(selected)) or 'none'}. Use /exchanges.",
                ),
                parse_mode="HTML",
            )
            return
        selected_names = sorted(active_selected)
        progress_msg = await update.effective_message.reply_text(
            "🔍 <b>MARKET SCANNER</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"🌐 {_exchange_line(selected_names)}\n\n"
            "📡 <i>Fetching live market data…</i>",
            parse_mode="HTML",
        )
        animation_task = asyncio.create_task(_animate_scan_progress(progress_msg, selected_names))
        try:
            opportunities = await scanner.run_cycle(require_matching_user=False, exchange_names=active_selected)
            selected_candidates = [
                opportunity for opportunity in opportunities
                if opportunity.buy_exchange in selected and opportunity.sell_exchange in selected
            ]
            visible = [opportunity for opportunity in selected_candidates if handlers.matches(opportunity, preferences)]
            visible = sorted(visible, key=lambda opportunity: opportunity.net_profit, reverse=True)[:preferences["max_results"]]
        finally:
            animation_task.cancel()
            await asyncio.gather(animation_task, return_exceptions=True)
            try:
                await context.bot.delete_message(update.effective_user.id, progress_msg.message_id)
            except Exception:
                pass
        await update.effective_message.reply_text(
            _format_scan_result(len(visible), selected_candidates, preferences),
            parse_mode="HTML",
        )
        db = handlers.get_db(context)
        for index, item in enumerate(visible, 1):
            identifier = handlers.opportunity_id(item)
            await db.save_opportunity(identifier, item)
            message = handlers.format_opportunity_card(item, identifier, card_number=index, trade_size=preferences.get("trade_size", 1000))
            await update.effective_message.reply_text(message, reply_markup=handlers.opportunity_buttons(identifier), parse_mode="HTML")

    handlers.scan_command = scan_command
