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
from .exchanges.registry import CCXT_NAMES
from .filters import matches, parse_float, user_filters
from .scanner import opportunity_id

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
        CommandHandler("filters", filters_menu), CommandHandler("myfilters", myfilters),
        CommandHandler("resetfilters", resetfilters), CommandHandler("loosemode", loosemode),
        CommandHandler("pause", pause), CommandHandler("resume", resume),
        CommandHandler("setminprofit", numeric_filter("min_profit")), CommandHandler("setmaxprofit", numeric_filter("max_profit")),
        CommandHandler("setminspread", numeric_filter("min_spread")), CommandHandler("setmaxspread", numeric_filter("max_spread")),
        CommandHandler("setminvolume", numeric_filter("min_volume")), CommandHandler("setmintradesize", numeric_filter("min_trade_size")),
        CommandHandler("setmaxtradesize", numeric_filter("max_trade_size")), CommandHandler("setmaxslippage", numeric_filter("max_slippage")),
        CommandHandler("setnetworkfee", numeric_filter("network_fee")), CommandHandler("setalertfreq", integer_filter("alert_cooldown")),
        CommandHandler("setdailycap", integer_filter("daily_cap")), CommandHandler("setmaxresults", positive_integer_filter("max_results")),
        CommandHandler("setquotecurrency", quote_currency),
        CommandHandler("watchlist", list_filter("watchlist")), CommandHandler("blacklist", list_filter("blacklist")),
        CommandHandler("papertrade", papertrade), CommandHandler("paperstats", paperstats),
        CommandHandler("leaderboard", leaderboard), CommandHandler("setfeeadjusted", fee_adjusted),
    ]
    admin_commands = [
        CommandHandler("genkey", admin_only(db, admin_ids, genkey)), CommandHandler("revokekey", admin_only(db, admin_ids, revoke_key)),
        CommandHandler("listkeys", admin_only(db, admin_ids, listkeys)), CommandHandler("extendvip", admin_only(db, admin_ids, extend_vip)),
        CommandHandler("grantvip", admin_only(db, admin_ids, grant_vip)), CommandHandler("revokevip", admin_only(db, admin_ids, revoke_vip)),
        CommandHandler("userinfo", admin_only(db, admin_ids, userinfo)), CommandHandler("listusers", admin_only(db, admin_ids, listusers)),
        CommandHandler("ban", admin_only(db, admin_ids, ban)), CommandHandler("unban", admin_only(db, admin_ids, unban)),
        CommandHandler("broadcast", admin_only(db, admin_ids, broadcast)), CommandHandler("stats", admin_only(db, admin_ids, stats)),
        CommandHandler("exportusers", admin_only(db, admin_ids, exportusers)), CommandHandler("memstatus", admin_only(db, admin_ids, memstatus)),
        CommandHandler("health", admin_only(db, admin_ids, health)),
    ]
    callbacks = [CallbackQueryHandler(opportunity_details, pattern=r"^details:"), CallbackQueryHandler(paper_trade_callback, pattern=r"^paper:"), CallbackQueryHandler(leaderboard_callback, pattern=r"^leaderboard:")]
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
    names = list(dict.fromkeys([*configured, *CCXT_NAMES]))
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
    user = await get_db(context).get_user(query.from_user.id)
    if user:
        await get_db(context).set_user(query.from_user.id, selected_exchanges=selected)
        await get_db(context).log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
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
    lines = [
        "📚 COMMAND GUIDE",
        "",
        "🆕 Getting started",
        "/start - register and choose at least two exchanges",
        "/vipkey YOUR_KEY - activate VIP after registration",
        "/status - view account, VIP, and exchange status",
        "/help - show this guide",
    ]
    if await db.active_vip(update.effective_user.id):
        lines.extend([
            "",
            "💎 VIP tools",
            "/scan - scan selected exchanges now",
            "/exchanges - change exchange selection",
            "/filters - see filter instructions",
            "/myfilters - view current filter values",
            "/setmaxresults N - show at most N results",
            "/setminprofit PERCENT - minimum profit filter",
            "/setmaxprofit PERCENT - maximum profit filter",
            "/setminspread PERCENT - minimum spread filter",
            "/setmaxspread PERCENT - maximum spread filter",
            "/setminvolume AMOUNT - minimum 24h volume",
            "/setmintradesize AMOUNT - minimum paper-trade size",
            "/setmaxtradesize AMOUNT - maximum paper-trade size",
            "/setmaxslippage PERCENT - maximum allowed slippage",
            "/setnetworkfee AMOUNT - network-fee estimate",
            "/setalertfreq SECONDS - alert cooldown",
            "/setdailycap COUNT - daily alert limit",
            "/setquotecurrency USDT|USDC|BTC - quote currency",
            "/watchlist add|remove SYMBOL - limit symbols",
            "/blacklist add|remove SYMBOL - ignore symbols",
            "/loosemode on|off - skip transfer verification",
            "/setfeeadjusted on|off - use fee-adjusted filtering",
            "/pause and /resume - pause or resume alerts",
            "/papertrade OPPORTUNITY_ID SIZE - record a simulation",
            "/paperstats - view simulated trading results",
            "/leaderboard [alltime] - view paper-trade rankings",
            "Use the Paper Trade button on a result for the safest workflow.",
        ])
    if update.effective_user.id in context.application.bot_data["admin_ids"]:
        lines.extend([
            "",
            "🛡️ Admin tools",
            "/admin 8767 - unlock admin tools for this session",
            "/genkey YOUR_KEY DAYS|lifetime - create your chosen VIP key",
            "/listkeys [unused|active|expired|revoked] - list keys",
            "/revokekey KEY - revoke a key",
            "/extendvip USER_ID DAYS - extend VIP access",
            "/grantvip USER_ID [DAYS] - grant VIP access",
            "/revokevip USER_ID - remove VIP access",
            "/userinfo USER_ID_OR_USERNAME - inspect a user",
            "/listusers [all|vip|pending|banned] - list users",
            "/ban USER_ID REASON - ban a user",
            "/unban USER_ID - remove a ban",
            "/broadcast MESSAGE - message active VIP users",
            "/stats - view bot statistics",
            "/health - check exchange connectivity",
            "/exportusers - download a CSV export",
            "/memstatus - view process memory",
        ])
    await update.message.reply_text("\n".join(lines))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_db(context).get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.")
        return
    filters = user_filters(user)
    expiry = user["vip_expiry"] or "lifetime"
    await update.message.reply_text(f"👤 ACCOUNT STATUS\n\n💎 VIP: {user['vip_status']} ({expiry})\n🌐 Exchanges: {', '.join(json.loads(user['selected_exchanges']))}\n⚠️ Loose mode: {filters['loose_mode']}\n⏸ Paused: {filters['paused']}\n📈 Profit range: {filters['min_profit']}% to {filters['max_profit']}%\n🎯 Max results: {filters['max_results']}")


async def exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    context.user_data["selected_exchanges"] = json.loads((await get_db(context).get_user(update.effective_user.id))["selected_exchanges"])
    await update.message.reply_text("🌐 EXCHANGE SELECTION\nTap an exchange to toggle it. Select at least two, then tap Done.", reply_markup=exchange_keyboard(context))


async def filters_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    await update.message.reply_text("🎛️ FILTER GUIDE\n\nUse /myfilters to view current values.\n/setminprofit 1\n/setminvolume 50000\n/setmaxresults 10\n/setquotecurrency USDT\n/watchlist add BTC/USDT\n/blacklist add DOGE/USDT\n\nUse /resetfilters to restore defaults. Values affect future scans and alerts.")


async def myfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    user = await get_db(context).get_user(update.effective_user.id)
    await update.message.reply_text("🎛️ YOUR FILTERS\n\n" + json.dumps(user_filters(user), indent=2))


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
        try: value = int(context.args[0])
        except (IndexError, ValueError): await update.message.reply_text("Enter a positive whole number."); return
        if value <= 0:
            await update.message.reply_text("Enter a positive whole number.")
            return
        await update_filter(update, context, name, value)
    return handler


async def update_filter(update, context, name, value):
    db = get_db(context); user = await db.get_user(update.effective_user.id); preferences = user_filters(user); preferences[name] = value
    await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, "changed_filter", f"{name}={value}")
    await update.message.reply_text(f"✅ Updated `{name}` to `{value}`.", parse_mode="Markdown")


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
        await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, name, f"{context.args[0]} {symbol}"); await update.message.reply_text(f"{name} updated.")
    return handler


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
    row = await db.get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.edit_message_text("Opportunity has expired.")
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
        size = user_filters(user)["max_trade_size"]
        buy_fill, buy_slippage = _book_fill(books[0].get("asks", []), size, ascending=True)
        sell_fill, sell_slippage = _book_fill(books[1].get("bids", []), size, ascending=False)
        fee_rates = await asyncio.gather(
            buy_exchange.get_taker_fee(row["symbol"]),
            sell_exchange.get_taker_fee(row["symbol"]),
        )
        gross_profit = max(0.0, (sell_fill - buy_fill) * size)
        fee_cost = (buy_fill * size * fee_rates[0]) + (sell_fill * size * fee_rates[1])
        net_profit = gross_profit - fee_cost
        message = (
            f"🪙 {row['symbol']}  ·  ARBITRAGE DETAILS\n\n"
            f"🟢 BUY {row['buy_exchange']}: {buy_fill:.8f} (scan {row['buy_price']:.8f})\n"
            f"🔴 SELL {row['sell_exchange']}: {sell_fill:.8f} (scan {row['sell_price']:.8f})\n"
            f"📦 Trade size: {size:.4f} base units\n\n"
            f"📈 Gross profit: {gross_profit:.4f} ({((sell_fill - buy_fill) / buy_fill) * 100:.3f}%)\n"
            f"💸 Buy fee: {fee_rates[0] * 100:.4f}% ({buy_fill * size * fee_rates[0]:.4f})\n"
            f"💸 Sell fee: {fee_rates[1] * 100:.4f}% ({sell_fill * size * fee_rates[1]:.4f})\n"
            f"🧾 Total fees: {fee_cost:.4f}\n"
            f"💰 Estimated net profit: {net_profit:.4f}\n"
            f"📉 Slippage: buy {buy_slippage:.3f}% / sell {sell_slippage:.3f}%\n"
            f"📊 Raw scan spread: {row['raw_spread']:.3f}%\n"
            f"💧 24h volume: {row['volume_buy']:.0f} / {row['volume_sell']:.0f}\n\n"
            f"🟩 BUY ORDER BOOK · {row['buy_exchange']}\n{_format_order_book(books[0].get('asks', []), 'asks')}\n\n"
            f"🟥 SELL ORDER BOOK · {row['sell_exchange']}\n{_format_order_book(books[1].get('bids', []), 'bids')}\n\n"
            f"{'⚠️ Transfer route unverified' if row['loose_mode'] else '✅ Transfer route verified'}\n"
            "⏱ Live order books fetched now. Re-check before trading."
        )
    except Exception:
        logger.exception("live details failed for %s", row["id"])
        message = "⚠️ Live order-book data is temporarily unavailable. Re-check this opportunity later."
    await query.edit_message_text(message, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Paper Trade", callback_data=f"paper:{row['id']}")]]))


async def paper_trade_callback(update, context):
    query = update.callback_query
    await query.answer()
    db = get_db(context)
    if not await db.active_vip(query.from_user.id):
        await query.answer("Active VIP access required.", show_alert=True)
        return
    row = await db.get_opportunity(query.data.split(":", 1)[1])
    if not row:
        await query.answer("Opportunity has expired.", show_alert=True)
        return
    user = await db.get_user(query.from_user.id)
    size = user_filters(user)["max_trade_size"]
    profit = size * (row["net_profit"] / 100)
    period = datetime.now(UTC).strftime("%G-%V")
    await db._db().execute(
        "INSERT INTO paper_trades(user_id, opportunity_id, size, profit, created_at, period) VALUES (?, ?, ?, ?, ?, ?)",
        (query.from_user.id, row["id"], size, profit, datetime.now(UTC).isoformat(), period),
    )
    await db._db().commit()
    await query.message.reply_text(f"🧪 PAPER TRADE RECORDED\n\n🪙 {row['symbol']}\n📦 Size: {size}\n💰 Estimated P&L: {profit:.4f}\n\n✅ Simulation only. No real order was placed.")


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
    profit = size * (row["net_profit"] / 100); period = datetime.now(UTC).strftime("%G-%V")
    await db._db().execute("INSERT INTO paper_trades(user_id, opportunity_id, size, profit, created_at, period) VALUES (?, ?, ?, ?, ?, ?)", (update.effective_user.id, context.args[0], size, profit, datetime.now(UTC).isoformat(), period)); await db._db().commit()
    await update.message.reply_text(f"🧪 PAPER TRADE RECORDED\n\n📦 Size: {size}\n💰 Estimated P&L: {profit:.4f}\n\n✅ Simulation only. No real order was placed.")


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
    text = "LEADERBOARD\n" + "\n".join(f"{index}. @{row['username'] or row['telegram_id']} {row['total']:.4f}" for index, row in enumerate(rows, 1))
    await update.message.reply_text(text or "📭 No paper trades yet.")


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


async def scan_command(update, context):
    if not await require_vip(update, context):
        return
    scanner = context.application.bot_data.get("scanner")
    if not scanner:
        await update.effective_message.reply_text("Scanner is still starting. Try again shortly.")
        return
    await update.effective_message.reply_text("🔎 Scanning active exchanges...")
    opportunities = await scanner.run_cycle()
    user = await get_db(context).get_user(update.effective_user.id)
    preferences = user_filters(user)
    selected = set(json.loads(user["selected_exchanges"] or "[]"))
    visible = [opportunity for opportunity in opportunities if opportunity.buy_exchange in selected and opportunity.sell_exchange in selected and matches(opportunity, preferences)]
    visible = sorted(visible, key=lambda opportunity: opportunity.net_profit, reverse=True)[:preferences["max_results"]]
    details = "\n".join(f"{item.symbol}: {item.net_profit:.3f}% ({item.buy_exchange} -> {item.sell_exchange})" for item in visible)
    await update.effective_message.reply_text(f"✅ SCAN COMPLETE\n\n🌐 Exchanges checked: {len(scanner.exchanges)}\n🎯 Results shown: {len(visible)} of {len(opportunities)}\n\n{details}".rstrip())


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
    rows = await get_db(context).list_users(context.args[0] if context.args else "all"); await update.message.reply_text("\n".join(f"{row['telegram_id']} @{row['username']} {row['vip_status']} banned={row['banned']}" for row in rows) or "No users.")


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
