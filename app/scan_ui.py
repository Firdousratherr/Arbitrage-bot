from __future__ import annotations

import asyncio
import json
from html import escape

from . import handlers


def _exchange_line(exchanges: list[str]) -> str:
    return " ↔ ".join(escape(name.upper()) for name in exchanges) or "SELECTED EXCHANGES"


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
        await update.effective_message.reply_text(handlers.format_scan_count(len(visible)), parse_mode="HTML")
        db = handlers.get_db(context)
        for index, item in enumerate(visible, 1):
            identifier = handlers.opportunity_id(item)
            await db.save_opportunity(identifier, item)
            message = handlers.format_opportunity_card(item, identifier, card_number=index, trade_size=preferences.get("trade_size", 1000))
            await update.effective_message.reply_text(message, reply_markup=handlers.opportunity_buttons(identifier), parse_mode="HTML")

    handlers.scan_command = scan_command
