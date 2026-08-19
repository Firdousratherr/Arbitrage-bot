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
from .filters import parse_float, user_filters
from .scanner import opportunity_id

logger = logging.getLogger(__name__)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_STAGE, EXCHANGES_STAGE, VIP_STAGE = range(3)


def build_handlers(db: Database, admin_ids: set[int], exchange_names: list[str]):
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
        CommandHandler("help", help_command), CommandHandler("status", status),
        CommandHandler("exchanges", exchanges), CommandHandler("setexchanges", exchanges),
        CommandHandler("filters", filters_menu), CommandHandler("myfilters", myfilters),
        CommandHandler("resetfilters", resetfilters), CommandHandler("loosemode", loosemode),
        CommandHandler("pause", pause), CommandHandler("resume", resume),
        CommandHandler("setminprofit", numeric_filter("min_profit")), CommandHandler("setmaxprofit", numeric_filter("max_profit")),
        CommandHandler("setminspread", numeric_filter("min_spread")), CommandHandler("setmaxspread", numeric_filter("max_spread")),
        CommandHandler("setminvolume", numeric_filter("min_volume")), CommandHandler("setmintradesize", numeric_filter("min_trade_size")),
        CommandHandler("setmaxtradesize", numeric_filter("max_trade_size")), CommandHandler("setmaxslippage", numeric_filter("max_slippage")),
        CommandHandler("setnetworkfee", numeric_filter("network_fee")), CommandHandler("setalertfreq", integer_filter("alert_cooldown")),
        CommandHandler("setdailycap", integer_filter("daily_cap")), CommandHandler("setquotecurrency", quote_currency),
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
    callbacks = [CallbackQueryHandler(opportunity_details, pattern=r"^details:"), CallbackQueryHandler(leaderboard_callback, pattern=r"^leaderboard:")]
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
    await update.message.reply_text("Choose exchanges. Tap to toggle, then press Done.", reply_markup=exchange_keyboard(context))
    return EXCHANGES_STAGE


def exchange_keyboard(context) -> InlineKeyboardMarkup:
    selected = set(context.user_data.get("selected_exchanges", []))
    names = context.application.bot_data["exchange_names"]
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
    await query.edit_message_text("Enter your VIP key, or type NONE if you do not have one yet.")
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
    await update.message.reply_text(f"{message}\nSelected exchanges: {', '.join(selected)}\nUse /status to review your account.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Registration cancelled. Use /start when ready.")
    return ConversationHandler.END


async def require_vip(update: Update, context) -> bool:
    db = get_db(context)
    if not await db.active_vip(update.effective_user.id):
        await update.effective_message.reply_text("This feature requires active VIP access. Register with /start and redeem a VIP key.")
        return False
    await db.touch(update.effective_user.id)
    return True


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = get_db(context)
    lines = ["Basic: /start /status /help"]
    if await db.active_vip(update.effective_user.id):
        lines.append("VIP: /exchanges /filters /myfilters /loosemode /pause /resume /papertrade /paperstats /leaderboard")
    if update.effective_user.id in context.application.bot_data["admin_ids"]:
        lines.append("Admin: /genkey /grantvip /revokevip /userinfo /listusers /ban /unban /broadcast /stats /exportusers /memstatus")
    await update.message.reply_text("\n".join(lines))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await get_db(context).get_user(update.effective_user.id)
    if not user:
        await update.message.reply_text("You are not registered. Use /start.")
        return
    filters = user_filters(user)
    expiry = user["vip_expiry"] or "lifetime"
    await update.message.reply_text(f"VIP: {user['vip_status']} ({expiry})\nExchanges: {', '.join(json.loads(user['selected_exchanges']))}\nLoose mode: {filters['loose_mode']}\nPaused: {filters['paused']}\nMin profit: {filters['min_profit']}%")


async def exchanges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    context.user_data["selected_exchanges"] = json.loads((await get_db(context).get_user(update.effective_user.id))["selected_exchanges"])
    await update.message.reply_text("Update your exchange selection.", reply_markup=exchange_keyboard(context))


async def filters_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    await update.message.reply_text("Set filters with commands such as /setminprofit 1.0, /setminvolume 50000, /watchlist add BTC/USDT. View with /myfilters.")


async def myfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    user = await get_db(context).get_user(update.effective_user.id)
    await update.message.reply_text(json.dumps(user_filters(user), indent=2))


async def resetfilters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_vip(update, context): return
    db = get_db(context)
    await db.set_user(update.effective_user.id, filters=DEFAULT_FILTERS)
    await db.log_action(update.effective_user.id, "reset_filters")
    await update.message.reply_text("Filters reset to defaults.")


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
        try: value = int(context.args[0])
        except (IndexError, ValueError): await update.message.reply_text("Enter a whole number."); return
        await update_filter(update, context, name, value)
    return handler


async def update_filter(update, context, name, value):
    db = get_db(context); user = await db.get_user(update.effective_user.id); preferences = user_filters(user); preferences[name] = value
    await db.set_user(update.effective_user.id, filters=preferences); await db.log_action(update.effective_user.id, "changed_filter", f"{name}={value}")
    await update.message.reply_text(f"Updated {name} to {value}.")


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
    if value not in {"USDT", "USDC", "BTC"}: await update.message.reply_text("Supported quote currencies: USDT, USDC, BTC"); return
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
        message = (
            f"{row['symbol']} live details\n"
            f"Buy {row['buy_exchange']}: {buy_fill:.8f} (scan {row['buy_price']})\n"
            f"Sell {row['sell_exchange']}: {sell_fill:.8f} (scan {row['sell_price']})\n"
            f"Estimated slippage: buy {buy_slippage:.3f}% / sell {sell_slippage:.3f}%\n"
            f"Raw spread: {row['raw_spread']:.3f}%\n"
            f"24h volume: {row['volume_buy']:.0f} / {row['volume_sell']:.0f}\n"
            f"{'⚠️ LOOSE MODE - unverified' if row['loose_mode'] else '✅ Transfer verification passed'}\n"
            "Live order books fetched now. Data may be stale; re-check before trading."
        )
    except Exception:
        logger.exception("live details failed for %s", row["id"])
        message = "Live order book data is temporarily unavailable. Re-check the opportunity later."
    await query.edit_message_text(message)


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


async def papertrade(update, context):
    if not await require_vip(update, context): return
    if len(context.args) != 2: await update.message.reply_text("Usage: /papertrade OPPORTUNITY_ID SIZE"); return
    db = get_db(context); row = await db.get_opportunity(context.args[0])
    if not row: await update.message.reply_text("Opportunity not found or expired."); return
    size = float(context.args[1]); profit = size * (row["net_profit"] / 100); period = datetime.now(UTC).strftime("%G-%V")
    await db._db().execute("INSERT INTO paper_trades(user_id, opportunity_id, size, profit, created_at, period) VALUES (?, ?, ?, ?, ?, ?)", (update.effective_user.id, context.args[0], size, profit, datetime.now(UTC).isoformat(), period)); await db._db().commit()
    await update.message.reply_text(f"SIMULATED PAPER TRADE\nSize: {size}\nEstimated P&L: {profit:.4f}\nThis did not place a real trade.")


async def paperstats(update, context):
    if not await require_vip(update, context): return
    cursor = await get_db(context)._db().execute("SELECT COUNT(*) count, COALESCE(SUM(profit), 0) total, COALESCE(MAX(profit), 0) best, COALESCE(AVG(profit > 0), 0) win_rate FROM paper_trades WHERE user_id=?", (update.effective_user.id,)); row = await cursor.fetchone()
    await update.message.reply_text(f"SIMULATED PAPER STATS\nTrades: {row['count']}\nWin rate: {row['win_rate'] * 100:.1f}%\nTotal P&L: {row['total']:.4f}\nBest trade: {row['best']:.4f}")


async def leaderboard(update, context):
    if not await require_vip(update, context): return
    period = "alltime" if context.args and context.args[0].lower() == "alltime" else datetime.now(UTC).strftime("%G-%V")
    where = "1=1" if period == "alltime" else "period=?"; args = () if period == "alltime" else (period,)
    cursor = await get_db(context)._db().execute(f"SELECT u.username, SUM(p.profit) total FROM paper_trades p JOIN users u ON u.telegram_id=p.user_id WHERE u.leaderboard_hidden=0 AND {where} GROUP BY p.user_id ORDER BY total DESC LIMIT 10", args); rows = await cursor.fetchall()
    text = "LEADERBOARD\n" + "\n".join(f"{index}. @{row['username'] or row['total']} {row['total']:.4f}" for index, row in enumerate(rows, 1))
    await update.message.reply_text(text or "No paper trades yet.")


async def leaderboard_callback(update, context):
    await update.callback_query.answer()


def admin_only(db, admin_ids, handler):
    async def wrapped(update, context):
        if update.effective_user.id not in admin_ids: await update.effective_message.reply_text("Admin access required."); return
        await db.log_admin_action(update.effective_user.id, update.message.text.split()[0], " ".join(context.args)); return await handler(update, context)
    return wrapped


async def genkey(update, context):
    duration = context.args[0] if context.args else "30"; key = await get_db(context).create_vip_key(update.effective_user.id, duration); await update.message.reply_text(f"Generated: {key}")


async def listkeys(update, context):
    status = context.args[0].lower() if context.args else None
    try:
        rows = await get_db(context).list_vip_keys(status)
    except ValueError:
        await update.message.reply_text("Status must be unused, active, expired, or revoked.")
        return
    text = "\n".join(f"{row['key']} {row['status']} expiry={row['expiry_date'] or 'lifetime'}" for row in rows)
    await update.message.reply_text(text or "No VIP keys found.")


async def extend_vip(update, context):
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /extendvip USER_ID DAYS")
        return
    try:
        user_id, days = int(context.args[0]), int(context.args[1])
        updated = await get_db(context).extend_vip(user_id, days)
    except ValueError:
        await update.message.reply_text("USER_ID and DAYS must be whole numbers; DAYS must be positive.")
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
    if not context.args: await update.message.reply_text("Usage: /revokekey KEY"); return
    await get_db(context)._db().execute("UPDATE vip_keys SET status='revoked' WHERE key=?", (context.args[0].upper(),)); await get_db(context)._db().commit(); await update.message.reply_text("Key revoked.")


async def grant_vip(update, context):
    if not context.args: await update.message.reply_text("Usage: /grantvip USER_ID [days]"); return
    expiry = None if len(context.args) < 2 else (datetime.now(UTC) + timedelta(days=int(context.args[1]))).isoformat(); await get_db(context).set_user(int(context.args[0]), vip_status="active", vip_expiry=expiry); await update.message.reply_text("VIP granted.")


async def revoke_vip(update, context):
    await get_db(context).set_user(int(context.args[0]), vip_status="revoked"); await update.message.reply_text("VIP revoked.")


async def userinfo(update, context):
    row = await get_db(context).find_user(context.args[0]);
    if not row: await update.message.reply_text("User not found."); return
    actions = await get_db(context).user_actions(row["telegram_id"]); await update.message.reply_text(json.dumps(dict(row), indent=2) + "\nActions:\n" + "\n".join(f"{a['timestamp']} {a['action']} {a['details']}" for a in actions))


async def listusers(update, context):
    rows = await get_db(context).list_users(context.args[0] if context.args else "all"); await update.message.reply_text("\n".join(f"{row['telegram_id']} @{row['username']} {row['vip_status']} banned={row['banned']}" for row in rows) or "No users.")


async def ban(update, context):
    await get_db(context).set_user(int(context.args[0]), banned=1, ban_reason=" ".join(context.args[1:])); await update.message.reply_text("User banned.")


async def unban(update, context):
    await get_db(context).set_user(int(context.args[0]), banned=0, ban_reason=None); await update.message.reply_text("User unbanned.")


async def broadcast(update, context):
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
