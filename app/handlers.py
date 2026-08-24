from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters as telegram_filters)

from .db import DEFAULT_FILTERS, Database
from .exchanges.base import Opportunity
from .filters import matches, parse_float, user_filters
from .maintenance import MaintenanceAssistant
from .scanner import opportunity_id
from .ui import (
    format_error,
    format_opportunity_card,
    format_opportunity_details,
    format_paper_trade,
    format_scan_count,
    format_status_message,
    format_filters_message,
    format_leaderboard,
    format_portfolio,
    opportunity_buttons,
)

logger = logging.getLogger(__name__)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_STAGE, EXCHANGES_STAGE, VIP_STAGE = range(3)


def build_handlers(db: Database, admin_ids: set[int], exchange_names: list[str], admin_secret_key: str):
    registration = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            EMAIL_STAGE: [MessageHandler(telegram_filters.TEXT & ~telegram_filters.COMMAND, capture_email)],
            EXCHANGES_STAGE: [CallbackQueryHandler(exchange_toggle, pattern=r"^exchange:"), CallbackQueryHandler(exchange_confirm, pattern=r"^exchange_done$")],
            VIP_STAGE: [MessageHandler(telegram_filters.TEXT & ~telegram_filters.COMMAND, redeem_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    commands = [
        CommandHandler("admin", admin_access),
        CommandHandler("help", help_command), CommandHandler("status", status),
        CommandHandler("vipkey", redeem_vip_key_command),
        CommandHandler("scan", scan_command),
        CommandHandler("exchanges", exchanges), CommandHandler("setexchanges", exchanges),
        CommandHandler("filters", filters_menu), CommandHandler("myfilters", myfilters), CommandHandler("settings", myfilters),
        CommandHandler("resetfilters", resetfilters), CommandHandler("loosemode", loosemode),
        CommandHandler("pause", pause), CommandHandler("resume", resume),
        CommandHandler("setminprofit", numeric_filter("min_profit")), CommandHandler("setmaxprofit", numeric_filter("max_profit")),
        CommandHandler("setminspread", numeric_filter("min_spread")), CommandHandler("setmaxspread", numeric_filter("max_spread")),
        CommandHandler("setminvolume", numeric_filter("min_volume")), CommandHandler("settradesize", numeric_filter("trade_size")),
        CommandHandler("setalertfreq", integer_filter("alert_cooldown")), CommandHandler("setmaxresults", positive_integer_filter("max_results")),
        CommandHandler("setquotecurrency", quote_currency), CommandHandler("setemail", setemail),
        CommandHandler("watchlist", list_filter("watchlist")), CommandHandler("blacklist", list_filter("blacklist")),
        CommandHandler("papertrade", papertrade), CommandHandler("paperstats", paperstats), CommandHandler("portfolio", portfolio),
        CommandHandler("leaderboard", leaderboard), CommandHandler("setfeeadjusted", fee_adjusted),
    ]
    commands.append(CommandHandler("aistatus", admin_only(db, admin_ids, aistatus)))
    admin_commands = [
        CommandHandler("genkey", admin_only(db, admin_ids, genkey)), CommandHandler("revokekey", admin_only(db, admin_ids, revoke_key)),
        CommandHandler("listkeys", admin_only(db, admin_ids, listkeys)), CommandHandler("extendvip", admin_only(db, admin_ids, extend_vip)),
        CommandHandler("grantvip", admin_only(db, admin_ids, grant_vip)), CommandHandler("revokevip", admin_only(db, admin_ids, revoke_vip)),
        CommandHandler("userinfo", admin_only(db, admin_ids, userinfo)), CommandHandler("listusers", admin_only(db, admin_ids, listusers)),
        CommandHandler("ban", admin_only(db, admin_ids, ban)), CommandHandler("unban", admin_only(db, admin_ids, unban)),
        CommandHandler("broadcast", admin_only(db, admin_ids, broadcast)), CommandHandler("stats", admin_only(db, admin_ids, stats)),
        CommandHandler("exportusers", admin_only(db, admin_ids, exportusers)), CommandHandler("memstatus", admin_only(db, admin_ids, memstatus)),
        CommandHandler("health", admin_only(db, admin_ids, health)),
        CommandHandler("exchangestats", admin_only(db, admin_ids, exchangestats)),
        CommandHandler("diagnose", admin_only(db, admin_ids, diagnose)),
        CommandHandler("aiprobe", admin_only(db, admin_ids, aiprobe)),
        CommandHandler("fixerror", admin_only(db, admin_ids, fixerror)),
        CommandHandler("patchstatus", admin_only(db, admin_ids, patchstatus)),
        CommandHandler("validatefix", admin_only(db, admin_ids, validatefix)),
        CommandHandler("rejectfix", admin_only(db, admin_ids, rejectfix)),
        CommandHandler("approvefix", admin_only(db, admin_ids, approvefix)),
    ]
    callbacks = [
        CallbackQueryHandler(exchange_toggle, pattern=r"^exchange:"),
        CallbackQueryHandler(exchange_confirm, pattern=r"^exchange_done$"),
        CallbackQueryHandler(opportunity_details, pattern=r"^details:"),
        CallbackQueryHandler(paper_trade_callback, pattern=r"^paper:"),
        CallbackQueryHandler(opportunity_back, pattern=r"^back:"),
        CallbackQueryHandler(leaderboard_callback, pattern=r"^leaderboard:"),
        CallbackQueryHandler(maintenance_callback, pattern=r"^maintenance:"),
    ]
    return [registration, *commands, *admin_commands, *callbacks]


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    existing = await get_db(context).get_user(user.id)
    if existing and existing["email"]:
        await update.message.reply_text("You are registered. Use /status to view access and /help for commands.")
        return ConversationHandler.END
    await update.message.reply_text("Welcome. What email address should be associated with your Telegram account?")
    return EMAIL_STAGE


async def capture_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.message.text.strip()
    if not EMAIL.match(email):
        await update.message.reply_text("Please enter a valid email address, or /cancel to stop.")
        return EMAIL_STAGE
    context.user_data["email"] = email
    context.user_data["selected_exchanges"] = []
    await update.message.reply_text("🌐 Choose your exchanges\nTap to toggle, then press Done.", reply_markup=exchange_keyboard(context))
    return EXCHANGES_STAGE


def exchange_keyboard(context) -> InlineKeyboardMarkup:
    selected = set(context.user_data.get("selected_exchanges", []))
    configured = context.application.bot_data.get("exchange_names", [])
    names = list(dict.fromkeys(configured))
    rows = []
    for index in range(0, len(names), 2):
        row = []
        for name in names[index:index + 2]:
            mark = "[x]" if name in selected else "[ ]"
            row.append(InlineKeyboardButton(f"{mark} {name}", callback_data=f"exchange:{name}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("Done", callback_data="exchange_done")])
    return InlineKeyboardMarkup(rows)


async def exchange_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    selected = set(context.user_data.get("selected_exchanges", []))
    selected.symmetric_difference_update({name})
    context.user_data["selected_exchanges"] = list(selected)
    await query.edit_message_reply_markup(exchange_keyboard(context))
    return EXCHANGES_STAGE


async def exchange_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    selected = context.user_data.get("selected_exchanges", [])
    if len(selected) < 2:
        await query.answer("Select at least two exchanges.", show_alert=True)
        return EXCHANGES_STAGE
    await query.answer()
    db = get_db(context)
    existing = await db.get_user(query.from_user.id)
    if existing and existing["email"]:
        await db.set_user(query.from_user.id, selected_exchanges=selected)
        await db.log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
        await query.edit_message_text(f"✅ Exchanges saved\n\n🌐 {', '.join(selected)}")
        return ConversationHandler.END
    await db.upsert_user(
        query.from_user.id,
        query.from_user.username,
        context.user_data["email"],
        selected,
    )
    await db.log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
    await query.edit_message_text("✅ Exchanges saved\n\n🔐 Enter your VIP key, or type NONE if you do not have one yet.")
    return VIP_STAGE


async def redeem_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    db = get_db(context)
    user = update.effective_user
    email = context.user_data["email"]
    selected = context.user_data["selected_exchanges"]
    await db.upsert_user(user.id, user.username, email, selected)
    key = update.message.text.strip()
    message = "Registration saved. VIP access is pending administrator approval."
    if key.upper() != "NONE":
        _, message = await db.redeem_vip_key(user.id, key)
    else:
        for admin_id in context.application.bot_data["admin_ids"]:
            try:
                await context.bot.send_message(admin_id, f"VIP access pending for @{user.username or user.id} ({user.id}).")
            except Exception:
                logger.info("could not notify admin %s", admin_id)
    await db.log_action(user.id, "registered", f"exchanges={','.join(selected)}")
    await update.message.reply_text(f"🎉 {message}\n\n🌐 Exchanges: {', '.join(selected)}\n📋 Use /status to review your account.")
    # Clear stale session data after successful registration
    context.user_data.pop("email", None)
    context.user_data.pop("selected_exchanges", None)
    return ConversationHandler.END


async def redeem_vip_key_command(update: Update, context) -> None:
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /vipkey YOUR_KEY")
        return
    user = await get_db(context).get_user(update.effective_user.id)
    if not user or not user["email"]:
        await update.effective_message.reply_text("Register first with /start, then use /vipkey YOUR_KEY.")
        return
    _, message = await get_db(context).redeem_vip_key(update.effective_user.id, context.args[0].strip())
    await get_db(context).log_action(update.effective_user.id, "redeemed_vip_key", context.args[0].strip())
    await update.effective_message.reply_text(f"🔐 {message}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🛑 Registration cancelled. Use /start when ready.")
    # Clear stale session data on cancel
    context.user_data.pop("email", None)
    context.user_data.pop("selected_exchanges", None)
    return ConversationHandler.END


async def require_vip(update: Update, context) -> bool:
    db = get_db(context)
    if not await db.active_vip(update.effective_user.id):
        await update.effective_message.reply_text("🔒 This feature requires active VIP access. Register with /start and redeem a VIP key.")
        return False
    await db.touch(update.effective_user.id)
    return True


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db(context)
    is_admin = update.effective_user.id in context.application.bot_data["admin_ids"]
    lines = [
        "🤖 CRYPTO ARBITRAGE SCANNER",
        "━━━━━━━━━━━━━━",
        "📌 Getting Started",
        "/start      — register",
        "/exchanges  — pick exchanges",
        "/status     — your account",
        "",
        "🔍 Scanning",
        "/scan       — run a scan now",
        "/myfilters  — view active filters",
        "/setminprofit PERCENT    — minimum profit filter",
        "/setmaxprofit PERCENT    — maximum profit filter",
        "/setminspread PERCENT    — minimum spread filter",
        "/setmaxspread PERCENT    — maximum spread filter",
        "/setminvolume AMOUNT     — minimum 24h volume",
        "/setalertfreq SECONDS    — alert cooldown",
        "/setmaxresults N         — show at most N results",
        "/settradesize AMOUNT     — trade size for paper trading",
        "/setquotecurrency USDT|USDC|BTC — quote currency",
        "/watchlist add|remove SYMBOL — limit symbols",
        "/blacklist add|remove SYMBOL — ignore symbols",
        "/loosemode on|off        — skip transfer verification",
        "/setfeeadjusted on|off   — use fee-adjusted filtering",
        "/pause and /resume       — pause or resume alerts",
        "",
        "🏆 Fun",
        "/papertrade ID SIZE      — record a simulated trade",
        "/paperstats              — view simulated trading results",
        "/portfolio               — view your simulated portfolio",
        "/leaderboard [alltime]   — view paper-trade rankings",
    ]
    if is_admin:
        lines.extend([
            "",
            "🛡️ Admin Tools",
            "/admin 8767 — unlock admin tools for this session",
            "/genkey KEY DAYS|lifetime — create a VIP key",
            "/listkeys [status]   — list VIP keys",
            "/revokekey KEY       — revoke a key",
            "/extendvip USER_ID DAYS — extend VIP access",
            "/grantvip USER_ID [DAYS] — grant VIP access",
            "/revokevip USER_ID   — remove VIP access",
            "/userinfo USER_ID_OR_USERNAME — inspect a user",
            "/listusers [all|vip|pending|banned] — list users",
            "/ban USER_ID REASON  — ban a user",
            "/unban USER_ID       — remove a ban",
            "/broadcast MESSAGE   — message VIP users",
            "/stats — view bot statistics",
            "/health — check exchange connectivity",
            "/exchangestats — per-exchange ticker counts from the last scan",
            "/exportusers — download CSV export",
            "/memstatus — view process memory",
            "/diagnose — summarize recent errors with AI",
            "/aiprobe — raw HTTP check of the AI provider (bypasses SDK, shows real status/headers/body)",
            "/fixerror ISSUE — propose a patch",
            "/patchstatus — list pending patches",
            "/validatefix PATCH_ID — validate patch",
            "/approvefix PATCH_ID — apply validated patch",
            "/rejectfix PATCH_ID — reject patch",
        ])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_db(context).get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.")
        return
    filters = user_filters(user)
    exchanges = json.loads(user["selected_exchanges"] or "[]")
    message = format_status_message(
        vip_status=user["vip_status"],
        vip_expiry=user["vip_expiry"],
        exchanges=exchanges,
        loose_mode=filters.get("loose_mode", False),
        paused=filters.get("paused", False),
        filters=filters,
    )
    await update.message.reply_text(message, parse_mode="HTML")


async def exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    context.user_data["selected_exchanges"] = json.loads((await get_db(context).get_user(update.effective_user.id))["selected_exchanges"])
    await update.message.reply_text("🌐 EXCHANGE SELECTION\nTap an exchange to toggle it. Select at least two, then tap Done.", reply_markup=exchange_keyboard(context))


async def filters_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    await update.message.reply_text("🎛️ FILTER GUIDE\n\nUse /myfilters to view current values.\n/setminprofit 1\n/setmaxprofit 50\n/setminspread 0.5\n/setmaxspread 20\n/setminvolume 50000\n/setmaxresults 10\n/setquotecurrency USDT\n/watchlist add BTC/USDT\n/blacklist add DOGE/USDT\n\nUse /resetfilters to restore defaults. Values affect future scans and alerts.")


async def myfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    user = await get_db(context).get_user(update.effective_user.id)
    filters = user_filters(user)
    message = format_filters_message(filters)
    await update.message.reply_text(message, parse_mode="HTML")


async def resetfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    db = get_db(context)
    await db.set_user(update.effective_user.id, filters=DEFAULT_FILTERS)
    await db.log_action(update.effective_user.id, "reset_filters")
    await update.message.reply_text("♻️ Filters reset to defaults.")


def numeric_filter(name: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await require_vip(update, context): return
        if len(context.args) != 1:
            await update.message.reply_text(f"Usage: /{update.message.text.split()[0][1:]} <number>"); return
        try: value = parse_float(context.args[0])
        except ValueError: await update.message.reply_text("Enter a non-negative number."); return
        await update_filter(update, context, name, value)
    return handler


def integer_filter(name: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await require_vip(update, context): return
        if len(context.args) != 1:
            await update.message.reply_text(f"Usage: /{update.message.text.split()[0][1:]} WHOLE_NUMBER")
            return
        try: value = int(context.args[0])
        except ValueError: await update.message.reply_text("❌ Enter a whole number."); return
        if value < 0:
            await update.message.reply_text("❌ Enter zero or a positive whole number.")
            return
        await update_filter(update, context, name, value)
    return handler


def positive_integer_filter(name: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await require_vip(update, context): return
        if len(context.args) != 1:
            await update.message.reply_text(f"Usage: /{update.message.text.split()[0][1:]} POSITIVE_WHOLE_NUMBER")
            return
        try: value = int(context.args[0])
        except ValueError: await update.message.reply_text("Enter a positive whole number."); return
        if value <= 0:
            await update.message.reply_text("Enter a positive whole number.")
            return
        await update_filter(update, context, name, value)
    return handler


async def update_filter(update, context, name, value):
    db = get_db(context)
    user = await db.get_user(update.effective_user.id)
    preferences = user_filters(user)
    preferences[name] = value
    await db.set_user(update.effective_user.id, filters=preferences)
    await db.log_action(update.effective_user.id, "changed_filter", f"{name}={value}")
    saved = user_filters(await db.get_user(update.effective_user.id))
    await update.message.reply_text(
        f"✅ Saved `{name}` = `{saved[name]}`\n\n"
        f"Use /settings to view all current values.\n"
        f"🕒 Updated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        parse_mode="Markdown",
    )


async def fee_adjusted(update, context):
    if not await require_vip(update, context):
        return
    if not context.args or context.args[0].lower() not in {"on", "off"}:
        await update.message.reply_text("Usage: /setfeeadjusted on|off")
        return
    await update_filter(update, context, "fee_adjusted", context.args[0].lower() == "on")


async def quote_currency(update, context):
    if not await require_vip(update, context): return
    value = context.args[0].upper() if context.args else ""
    if value not in {"USDT", "USDC", "BTC"}:
        await update.message.reply_text("Usage: /setquotecurrency USDT|USDC|BTC")
        return
    await update_filter(update, context, "quote_currency", value)


def list_filter(name):
    async def handler(update, context):
        if not await require_vip(update, context): return
        if len(context.args) != 2 or context.args[0].lower() not in {"add", "remove"}: await update.message.reply_text(f"Usage: /{name} add|remove SYMBOL"); return
        db = get_db(context); user = await db.get_user(update.effective_user.id); preferences = user_filters(user); values = set(preferences[name]); symbol = context.args[1].upper()
        values.add(symbol) if context.args[0].lower() == "add" else values.discard(symbol); preferences[name] = sorted(values)
        await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, name, f"{context.args[0]} {symbol}"); await update.message.reply_text(f"✅ {name.title()} updated.")
    return handler


async def setemail(update, context):
    if not await require_vip(update, context): return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /setemail your@email.com")
        return
    email = context.args[0].strip()
    if not EMAIL.match(email):
        await update.message.reply_text("❌ Please enter a valid email address.")
        return
    db = get_db(context)
    await db.set_user(update.effective_user.id, email=email)
    await db.log_action(update.effective_user.id, "email_updated", email)
    await update.message.reply_text(f"✅ Email updated to {email}")


async def portfolio(update, context):
    if not await require_vip(update, context): return
    db = get_db(context)
    user_id = update.effective_user.id
    
    # Fetch all trades for user with opportunity details
    cursor = await db._db().execute(
        """SELECT p.id, p.opportunity_id, p.size, p.profit, p.created_at, 
                  o.symbol FROM paper_trades p 
           LEFT JOIN opportunities o ON o.id = p.opportunity_id 
           WHERE p.user_id = ? ORDER BY p.created_at DESC""",
        (user_id,)
    )
    trades = await cursor.fetchall()
    
    # Calculate totals
    total_pnl = sum(t["profit"] for t in trades)
    starting_balance = 10000.0  # Default starting balance for paper trading
    current_balance = starting_balance + total_pnl
    vip_limit = 100000.0  # Max position size for VIP
    
    # Format trades for display
    trade_dicts = []
    for t in trades:
        trade_dicts.append({
            "symbol": t["symbol"] or "unknown",
            "size": t["size"],
            "profit": t["profit"],
            "created_at": t["created_at"],
        })
    
    message = format_portfolio(trade_dicts, current_balance, vip_limit)
    await update.message.reply_text(message, parse_mode="HTML")


async def loosemode(update, context):
    if not await require_vip(update, context): return
    if not context.args or context.args[0].lower() not in {"on", "off"}: await update.message.reply_text("Usage: /loosemode on|off"); return
    db = get_db(context); user = await db.get_user(update.effective_user.id); preferences = user_filters(user); preferences["loose_mode"] = context.args[0].lower() == "on"
    await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, "loose_mode", context.args[0].upper()); await update.message.reply_text("Loose mode enabled: alerts are unverified." if preferences["loose_mode"] else "Loose mode disabled: transfer checks are required.")


async def pause(update, context):
    await toggle_pause(update, context, True)


async def resume(update, context):
    await toggle_pause(update, context, False)


async def toggle_pause(update, context, value):
    if not await require_vip(update, context): return
    db = get_db(context); user = await db.get_user(update.effective_user.id); preferences = user_filters(user); preferences["paused"] = value
    await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, "alerts_paused" if value else "alerts_resumed"); await update.message.reply_text("Alerts paused." if value else "Alerts resumed.")


async def opportunity_details(update, context):
    query = update.callback_query
    await query.answer()
    db = get_db(context)
    if not await db.active_vip(query.from_user.id):
        await query.edit_message_text("This feature requires active VIP access.")
        return
    await query.edit_message_text("⏳ Loading order book…")
    row = await db.get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.edit_message_text("⚠️ Opportunity expired\nRun /scan for fresh data.")
        return
    exchanges = context.application.bot_data.get("exchanges", {})
    buy_exchange = exchanges.get(row["buy_exchange"])
    sell_exchange = exchanges.get(row["sell_exchange"])
    if not buy_exchange or not sell_exchange:
        await query.edit_message_text("Live exchange data is unavailable right now.")
        return
    try:
        books = await asyncio.gather(
            buy_exchange.fetch_order_book(row["symbol"], 10),
            sell_exchange.fetch_order_book(row["symbol"], 10),
        )
        user = await db.get_user(query.from_user.id)
        size = user_filters(user)["trade_size"]
        buy_fill, buy_slippage = _book_fill(books[0].get("asks", []), size, ascending=True)
        sell_fill, sell_slippage = _book_fill(books[1].get("bids", []), size, ascending=False)
        fee_rates = await asyncio.gather(
            buy_exchange.get_taker_fee(row["symbol"]),
            sell_exchange.get_taker_fee(row["symbol"]),
        )
        gross_profit = max(0.0, (sell_fill - buy_fill) * size)
        fee_cost = (buy_fill * size * fee_rates[0]) + (sell_fill * size * fee_rates[1])
        net_profit = gross_profit - fee_cost
        transfer_text = _format_transfer_checks(json.loads(row["payload"] or "{}"))
        message = format_opportunity_details(
            row,
            buy_fill,
            sell_fill,
            fee_rates[0],
            fee_rates[1],
            gross_profit,
            net_profit,
            buy_slippage,
            sell_slippage,
            transfer_text,
            books[0].get("asks", []),
            books[1].get("bids", []),
        )
    except Exception:
        logger.exception("live details failed for %s", row["id"])
        message = "⚠️ Live order-book data is temporarily unavailable. Re-check this opportunity later."
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"details:{row['id']}"), InlineKeyboardButton("📄 Paper Trade", callback_data=f"paper:{row['id']}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"back:{row['id']}")],
        ]),
        parse_mode="HTML"
    )


def _opportunity_from_row(row) -> Opportunity:
    return Opportunity(
        symbol=row["symbol"],
        buy_exchange=row["buy_exchange"],
        sell_exchange=row["sell_exchange"],
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        raw_spread=row["raw_spread"],
        net_profit=row["net_profit"],
        volume_buy=row["volume_buy"] or 0,
        volume_sell=row["volume_sell"] or 0,
        verified=bool(row["verified"]),
        loose_mode=bool(row["loose_mode"]),
        metadata=json.loads(row["payload"] or "{}"),
    )


async def opportunity_back(update, context):
    query = update.callback_query
    await query.answer()
    row = await get_db(context).get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.edit_message_text("⚠️ Opportunity expired\nRun /scan for fresh data.")
        return
    user = await get_db(context).get_user(query.from_user.id)
    identifier = row["id"]
    trade_size = user_filters(user).get("trade_size", 1000) if user else 1000
    await query.edit_message_text(
        format_opportunity_card(_opportunity_from_row(row), identifier, trade_size=trade_size),
        reply_markup=opportunity_buttons(identifier),
        parse_mode="HTML"
    )


def _format_transfer_checks(metadata: dict) -> str:
    if metadata.get("transfer_verification") == "loose_mode":
        return "⚠️ Transfer checks skipped (loose mode)"
    buy = metadata.get("buy_transfer", {})
    sell = metadata.get("sell_transfer", {})
    matching = metadata.get("matching_network")
    buy_status = _transfer_status(buy, "deposit")
    sell_status = _transfer_status(sell, "withdraw")
    route = f"✅ Matching route: {matching}" if matching else "⚠️ No matching deposit/withdrawal network"
    return f"{route}\n🟢 Buy deposit: {buy_status}\n🔴 Sell withdrawal: {sell_status}"


def _transfer_status(metadata: dict, action: str) -> str:
    networks = metadata.get("networks", [])
    available = [item.get("network", "unknown") for item in networks if item.get(action)]
    if metadata.get("unavailable"):
        return "verification pending"
    return ", ".join(available[:4]) if available else "not available"


async def paper_trade_callback(update, context):
    query = update.callback_query
    await query.answer()
    db = get_db(context)
    if not await db.active_vip(query.from_user.id):
        await query.answer("Active VIP access required.", show_alert=True)
        return
    await query.edit_message_text("⏳ Preparing paper trade…")
    row = await db.get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.edit_message_text("⚠️ Opportunity expired\nRun /scan for fresh data.")
        return
    user = await db.get_user(query.from_user.id)
    size = user_filters(user)["trade_size"]
    expected_gross = size * (row["raw_spread"] / 100)
    profit = size * (row["net_profit"] / 100)
    period = datetime.now(UTC).strftime("%G-%V")
    await db._db().execute(
        "INSERT INTO paper_trades(user_id, opportunity_id, size, profit, created_at, period) VALUES (?, ?, ?, ?, ?, ?)",
        (query.from_user.id, row["id"], size, profit, datetime.now(UTC).isoformat(), period),
    )
    await db._db().commit()
    message = format_paper_trade(
        _opportunity_from_row(row),
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        size=size,
        expected_gross=expected_gross,
        estimated_net=row["net_profit"],
        profit=profit,
    )
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"back:{row['id']}")]]),
        parse_mode="HTML"
    )


def _book_fill(levels, size: float, *, ascending: bool) -> tuple[float, float]:
    """Return a size-weighted fill price and slippage against the first level."""
    usable = [(float(level[0]), float(level[1])) for level in levels if len(level) >= 2 and level[0] and level[1]]
    if not usable:
        raise ValueError("empty order book")
    reference = usable[0][0]
    remaining = size
    notional = 0.0
    filled = 0.0
    for price, amount in usable:
        take = min(remaining, amount)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if not filled:
        raise ValueError("order book has no liquidity")
    average = notional / filled
    slippage = ((average - reference) / reference) * 100
    return average, abs(slippage)


def _format_order_book(levels, side: str) -> str:
    rows = []
    for level in levels[:5]:
        if len(level) >= 2:
            rows.append(f"{float(level[0]):.8f} x {float(level[1]):.6f}")
    return "\n".join(rows) or "unavailable"


async def papertrade(update, context):
    if not await require_vip(update, context): return
    if len(context.args) != 2: await update.message.reply_text("🧪 Usage: /papertrade OPPORTUNITY_ID SIZE"); return
    db = get_db(context); row = await db.get_opportunity(context.args[0])
    if not row: await update.message.reply_text("Opportunity not found or expired."); return
    try:
        size = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ SIZE must be a positive number, for example: /papertrade ID123 100")
        return
    if size <= 0:
        await update.message.reply_text("❌ SIZE must be greater than zero.")
        return
    expected_gross = size * (row["raw_spread"] / 100)
    profit = size * (row["net_profit"] / 100); period = datetime.now(UTC).strftime("%G-%V")
    await db._db().execute("INSERT INTO paper_trades(user_id, opportunity_id, size, profit, created_at, period) VALUES (?, ?, ?, ?, ?, ?)", (update.effective_user.id, context.args[0], size, profit, datetime.now(UTC).isoformat(), period)); await db._db().commit()
    message = format_paper_trade(
        type("OpportunityShim", (), {"symbol": row["symbol"], "buy_exchange": row["buy_exchange"], "sell_exchange": row["sell_exchange"]})(),
        buy_price=row["buy_price"],
        sell_price=row["sell_price"],
        size=size,
        expected_gross=expected_gross,
        estimated_net=row["net_profit"],
        profit=profit,
    )
    await update.message.reply_text(message)


async def paperstats(update, context):
    if not await require_vip(update, context): return
    cursor = await get_db(context)._db().execute("SELECT COUNT(*) count, COALESCE(SUM(profit), 0) total, COALESCE(MAX(profit), 0) best, COALESCE(AVG(profit > 0), 0) win_rate FROM paper_trades WHERE user_id=?", (update.effective_user.id,)); row = await cursor.fetchone()
    await update.message.reply_text(f"📊 PAPER TRADE STATS\n\n🧪 Trades: {row['count']}\n🏆 Win rate: {row['win_rate'] * 100:.1f}%\n💰 Total P&L: {row['total']:.4f}\n⭐ Best trade: {row['best']:.4f}")


async def leaderboard(update, context):
    if not await require_vip(update, context): return
    if context.args and context.args[0].lower() != "alltime":
        await update.message.reply_text("Usage: /leaderboard or /leaderboard alltime")
        return
    period = "alltime" if context.args and context.args[0].lower() == "alltime" else datetime.now(UTC).strftime("%G-%V")
    where = "1=1" if period == "alltime" else "period=?"; args = () if period == "alltime" else (period,)
    cursor = await get_db(context)._db().execute(f"SELECT u.username, u.telegram_id, SUM(p.profit) total FROM paper_trades p JOIN users u ON u.telegram_id=p.user_id WHERE u.leaderboard_hidden=0 AND {where} GROUP BY p.user_id ORDER BY total DESC LIMIT 10", args); rows = await cursor.fetchall()
    
    # Check if user is in top 10
    user_rank = None
    user_profit = None
    cursor = await get_db(context)._db().execute(f"SELECT ROW_NUMBER() OVER (ORDER BY total DESC) rank, COALESCE(SUM(p.profit), 0) total FROM paper_trades p WHERE p.user_id = ? AND {where}", (update.effective_user.id, *args))
    user_row = await cursor.fetchone()
    if user_row and user_row["total"]:
        user_rank = user_row["rank"]
        user_profit = user_row["total"]
    
    period_name = "All-Time" if period == "alltime" else "Weekly"
    message = format_leaderboard(list(rows), period_name, user_rank, user_profit)
    await update.message.reply_text(message, parse_mode="HTML")


async def leaderboard_callback(update, context):
    await update.callback_query.answer()


async def admin_access(update, context):
    if update.effective_user.id not in context.application.bot_data["admin_ids"]:
        await update.effective_message.reply_text("🛡️ Admin access required.")
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /admin 8767")
        return
    if context.args[0] != context.application.bot_data["admin_secret_key"]:
        await update.effective_message.reply_text("❌ Invalid admin secret key.")
        return
    context.user_data["admin_unlocked"] = True
    await update.effective_message.reply_text("Admin settings unlocked for this session.")


def maintenance_service(context) -> MaintenanceAssistant:
    return context.application.bot_data["maintenance"]


async def diagnose(update, context):
    service = maintenance_service(context)
    try:
        result = await service.diagnose()
    except Exception as exc:
        logger.exception("AI diagnosis failed")
        result = f"❌ AI diagnosis failed: {type(exc).__name__}: {exc}"
    await update.effective_message.reply_text(result[:3900])


async def aistatus(update, context):
    service = maintenance_service(context)
    diagnostics = await service.provider_diagnostics() if service.api_url and service.api_key else {"status": "not configured"}
    if service.configured:
        status_line = diagnostics.get("status", "ok: provider connectivity and model access are working for the primary model.")
        await update.effective_message.reply_text(
            f"✅ AI maintenance is configured.\nEndpoint: {service.api_url}\nModel: {service.model}"
            + (f"\nFallback: {service.fallback_model}" if service.fallback_model else "")
            + f"\nStatus: {status_line}"
        )
        return
    missing = ", ".join(service.missing_settings) or "unknown configuration error"
    await update.effective_message.reply_text(
        f"❌ AI maintenance is not configured.\nMissing or invalid: {missing}\n\n"
        "Set these values in the project .env file, then restart the bot."
    )


async def aiprobe(update, context):
    """Bypass the Groq SDK's error classification and show the literal HTTP status,
    headers, and response body from a direct request, so a Cloudflare block page can
    be confirmed (or ruled out) without needing shell access to the host."""
    service = maintenance_service(context)
    try:
        result = await service.raw_connectivity_probe()
    except Exception as exc:
        logger.exception("AI raw connectivity probe failed")
        result = f"❌ Raw probe failed unexpectedly: {type(exc).__name__}: {exc}"
    await update.effective_message.reply_text(result[:3900])


async def fixerror(update, context):
    service = maintenance_service(context)
    issue = " ".join(context.args).strip()
    try:
        proposal_id, result = await service.propose_fix(issue)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Validate", callback_data=f"maintenance:validate:{proposal_id}"),
            InlineKeyboardButton("Approve", callback_data=f"maintenance:approve:{proposal_id}"),
            InlineKeyboardButton("Reject", callback_data=f"maintenance:reject:{proposal_id}"),
        ]])
    except Exception as exc:
        logger.exception("AI fix proposal failed")
        result = f"❌ AI fix proposal failed: {type(exc).__name__}: {exc}"
        keyboard = None
    await update.effective_message.reply_text(result[:3900], reply_markup=keyboard)


async def patchstatus(update, context):
    await update.effective_message.reply_text(maintenance_service(context).status())


async def validatefix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /validatefix PATCH_ID")
        return
    await update.effective_message.reply_text(maintenance_service(context).validate(context.args[0]))


async def rejectfix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /rejectfix PATCH_ID")
        return
    await update.effective_message.reply_text(maintenance_service(context).reject(context.args[0]))


async def approvefix(update, context):
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /approvefix PATCH_ID\nThis applies a previously validated patch.")
        return
    await update.effective_message.reply_text(maintenance_service(context).approve(context.args[0]))


async def maintenance_callback(update, context):
    query = update.callback_query
    if query.from_user.id not in context.application.bot_data["admin_ids"] or not context.user_data.get("admin_unlocked"):
        await query.answer("Admin access required.", show_alert=True)
        return
    await query.answer()
    _, action, proposal_id = query.data.split(":", 2)
    service = maintenance_service(context)
    actions = {"validate": service.validate, "approve": service.approve, "reject": service.reject}
    if action not in actions:
        await query.edit_message_text("Unknown maintenance action.")
        return
    await query.edit_message_text(actions[action](proposal_id))


async def scan_command(update, context):
    if not await require_vip(update, context):
        return
    scanner = context.application.bot_data.get("scanner")
    if not scanner:
        await update.effective_message.reply_text("Scanner is still starting. Try again shortly.")
        return
    
    # Send progress message
    progress_msg = await update.effective_message.reply_text("🔍 Scanning exchanges…")
    
    user = await get_db(context).get_user(update.effective_user.id)
    preferences = user_filters(user)
    selected = set(json.loads(user["selected_exchanges"] or "[]"))
    active_selected = selected & set(scanner.exchanges)
    
    if len(active_selected) < 2:
        await update.effective_message.reply_text(
            format_error(
                "Scan needs at least two active selected exchanges.",
                f"Your selection: {', '.join(sorted(selected)) or 'none'}. Use /exchanges."
            ),
            parse_mode="HTML"
        )
        return
    
    # Run scan
    opportunities = await scanner.run_cycle(require_matching_user=False, exchange_names=active_selected)
    selected_candidates = [
        opportunity for opportunity in opportunities
        if opportunity.buy_exchange in selected and opportunity.sell_exchange in selected
    ]
    visible = [opportunity for opportunity in selected_candidates if matches(opportunity, preferences)]
    visible = sorted(visible, key=lambda opportunity: opportunity.net_profit, reverse=True)[:preferences["max_results"]]
    
    # Delete progress message and send count
    try:
        await context.bot.delete_message(update.effective_user.id, progress_msg.message_id)
    except Exception:
        pass
    
    # Send count message
    count_msg = format_scan_count(len(visible))
    await update.effective_message.reply_text(count_msg, parse_mode="HTML")
    
    # Send opportunity cards
    db = get_db(context)
    for index, item in enumerate(visible, 1):
        identifier = opportunity_id(item)
        await db.save_opportunity(identifier, item)
        message = format_opportunity_card(item, identifier, card_number=index, trade_size=preferences.get("trade_size", 1000))
        await update.effective_message.reply_text(
            message,
            reply_markup=opportunity_buttons(identifier),
            parse_mode="HTML"
        )


def admin_only(db, admin_ids, handler):
    async def wrapped(update, context):
        if update.effective_user.id not in admin_ids:
            await update.effective_message.reply_text("Admin access required.")
            return
        if not context.user_data.get("admin_unlocked"):
            await update.effective_message.reply_text("Use /admin SECRET_KEY first.")
            return
        await db.log_admin_action(update.effective_user.id, update.message.text.split()[0], " ".join(context.args)); return await handler(update, context)
    return wrapped


async def genkey(update, context):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /genkey YOUR_KEY DAYS_OR_LIFETIME\nExample: /genkey VIP2026 30")
        return
    key, duration = context.args
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", key):
        await update.message.reply_text("Key must be 4-64 characters using only letters, numbers, hyphens, or underscores.")
        return
    duration = duration.lower()
    if duration not in {"lifetime", "life", "0"}:
        try:
            if int(duration) <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Duration must be a positive number of days or lifetime.")
            return
    try:
        key = await get_db(context).create_vip_key(update.effective_user.id, key, duration)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"VIP key created: {key}")


async def listkeys(update, context):
    status = context.args[0].lower() if context.args else None
    try:
        rows = await get_db(context).list_vip_keys(status)
    except ValueError:
        await update.message.reply_text("Status must be unused, active, expired, or revoked.")
        return
    text = "\n".join(f"🔑 {row['key']} · {row['status']} · expiry={row['expiry_date'] or 'lifetime'}" for row in rows)
    await update.message.reply_text(text or "📭 No VIP keys found.")


async def extend_vip(update, context):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /extendvip USER_ID DAYS")
        return
    try:
        user_id, days = int(context.args[0]), int(context.args[1])
        updated = await get_db(context).extend_vip(user_id, days)
    except ValueError:
        await update.message.reply_text("❌ USER_ID and DAYS must be whole numbers; DAYS must be positive.")
        return
    await update.message.reply_text("VIP extended." if updated else "User not found.")


async def health(update, context):
    results = []
    for name, exchange in context.application.bot_data.get("exchanges", {}).items():
        try:
            await exchange.fetch_tickers(["BTC/USDT"])
            results.append(f"{name}: ok")
        except Exception as exc:
            results.append(f"{name}: unavailable ({type(exc).__name__})")
    await update.message.reply_text("Exchange health\n" + "\n".join(results))

async def exchangestats(update, context):
    """Show per-exchange results from the most recent scan cycle so a "silent empty" exchange
    (request failing vs. every ticker missing bid/ask vs. genuinely no data) is a one-command
    check instead of a guessing game."""
    exchanges = context.application.bot_data.get("exchanges", {})
    if not exchanges:
        await update.message.reply_text("No exchanges configured.")
        return
    lines = ["Exchange stats (most recent scan cycle)"]
    for name, exchange in exchanges.items():
        stats = getattr(exchange, "last_fetch_stats", None)
        error = getattr(exchange, "last_fetch_error", None)
        if error:
            lines.append(f"{name}: ❌ fetch failed — {error}")
        elif stats is None:
            lines.append(f"{name}: no data yet (scan hasn't run)")
        elif stats["raw"] == 0:
            lines.append(f"{name}: ⚠️ 0 tickers received")
        elif stats["usable"] == 0:
            lines.append(f"{name}: ⚠️ {stats['raw']} tickers received, all {stats['dropped_bid_ask']} dropped for missing/zero bid-ask")
        elif stats.get("fallback_used"):
            lines.append(
                f"{name}: ✅ {stats['usable']} usable via order-book fallback "
                f"(bulk endpoint dropped all {stats['raw']} tickers for missing bid-ask)"
            )
        else:
            lines.append(f"{name}: ✅ {stats['usable']}/{stats['raw']} usable (dropped {stats['dropped_bid_ask']} for missing bid-ask)")
    await update.message.reply_text("\n".join(lines))


async def revoke_key(update, context):
    if len(context.args) != 1: await update.message.reply_text("Usage: /revokekey KEY"); return
    await get_db(context)._db().execute("UPDATE vip_keys SET status='revoked' WHERE key=?", (context.args[0].upper(),)); await get_db(context)._db().commit(); await update.message.reply_text("Key revoked.")


async def grant_vip(update, context):
    if len(context.args) not in {1, 2}: await update.message.reply_text("Usage: /grantvip USER_ID [DAYS]"); return
    try:
        user_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) == 2 else None
        if days is not None and days <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be a number and DAYS must be positive.")
        return
    expiry = None if days is None else (datetime.now(UTC) + timedelta(days=days)).isoformat()
    await get_db(context).set_user(user_id, vip_status="active", vip_expiry=expiry)
    await update.message.reply_text("✅ VIP access granted." if await get_db(context).get_user(user_id) else "❌ User not found.")


async def revoke_vip(update, context):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /revokevip USER_ID")
        return
    try: user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be a number.")
        return
    await get_db(context).set_user(user_id, vip_status="revoked")
    await update.message.reply_text("✅ VIP revoked.")


async def userinfo(update, context):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /userinfo USER_ID_OR_USERNAME")
        return
    row = await get_db(context).find_user(context.args[0]);
    if not row: await update.message.reply_text("User not found."); return
    actions = await get_db(context).user_actions(row["telegram_id"]); await update.message.reply_text(json.dumps(dict(row), indent=2) + "\nActions:\n" + "\n".join(f"{a['timestamp']} {a['action']} {a['details']}" for a in actions))


async def listusers(update, context):
    status = context.args[0].lower() if context.args else "all"
    if status not in {"all", "vip", "pending", "banned"}:
        await update.message.reply_text("Usage: /listusers [all|vip|pending|banned]")
        return
    rows = await get_db(context).list_users(status)
    text = "\n".join(
        f"👤 {row['telegram_id']} · @{row['username'] or '-'} · VIP={row['vip_status']} · banned={bool(row['banned'])}"
        for row in rows
    )
    await update.message.reply_text(f"👥 USERS ({status})\n\n{text}" if text else f"📭 No {status} users found.")


async def ban(update, context):
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /ban USER_ID REASON")
        return
    try: user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be a number.")
        return
    await get_db(context).set_user(user_id, banned=1, ban_reason=" ".join(context.args[1:]) or "No reason provided")
    await update.message.reply_text("✅ User banned.")


async def unban(update, context):
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /unban USER_ID")
        return
    try: user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ USER_ID must be a number.")
        return
    await get_db(context).set_user(user_id, banned=0, ban_reason=None)
    await update.message.reply_text("✅ User unbanned.")


async def broadcast(update, context):
    if not context.args:
        await update.message.reply_text("Usage: /broadcast YOUR_MESSAGE")
        return
    message = " ".join(context.args); sent = 0
    for row in await get_db(context).list_users("vip"):
        try: await context.bot.send_message(row["telegram_id"], message); sent += 1
        except Exception: logger.info("broadcast skipped for %s", row["telegram_id"])
    await update.message.reply_text(f"Broadcast sent to {sent} users.")


async def stats(update, context):
    db = get_db(context); users = await db.list_users(); vip = await db.list_users("vip"); banned = await db.list_users("banned"); await update.message.reply_text(f"Users: {len(users)}\nActive VIP: {len(vip)}\nBanned: {len(banned)}\nScans: {await db.stat('scans_run')}\nAlerts: {await db.stat('alerts_sent')}")


async def exportusers(update, context):
    payload = await get_db(context).export_users(); await update.message.reply_document(InputFile(payload.encode(), filename="users.csv"))


async def memstatus(update, context):
    try:
        import psutil
        rss = psutil.Process().memory_info().rss / 1024 / 1024
        await update.message.reply_text(f"RSS: {rss:.1f} MB")
    except ImportError: await update.message.reply_text("psutil is not installed.")
