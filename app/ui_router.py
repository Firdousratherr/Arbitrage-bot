from __future__ import annotations

import json
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from .db import DEFAULT_FILTERS, Database
from .filters import user_filters
from .handlers import EMAIL_STAGE, EXCHANGES_STAGE, VIP_STAGE, redeem_key
from .feature_handlers import enhanced_scan_command
from .ui import format_status_message, format_filters_message
from .ui_theme import dashboard, welcome, exchange_picker, settings_menu, screen, nav

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _premium_exchange_keyboard(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    selected = set(context.user_data.get("selected_exchanges", []))
    names = list(dict.fromkeys(context.application.bot_data.get("exchange_names", [])))
    rows = []
    for name in names:
        mark = "✅" if name in selected else "▫️"
        rows.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"ui:exchange:{name}")])
    rows.append([InlineKeyboardButton("✨ Done", callback_data="ui:exchange:done")])
    rows.append([InlineKeyboardButton("↩️ Back", callback_data="ui:dashboard")])
    return InlineKeyboardMarkup(rows)


async def premium_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    existing = await _db(context).get_user(user.id)
    if existing and existing["email"]:
        text, keyboard = dashboard()
        await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        return ConversationHandler.END
    text, keyboard = welcome()
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    return EMAIL_STAGE


async def premium_begin_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📧 <b>ACCOUNT SETUP</b>\n\nEnter the email address associated with your account.", parse_mode="HTML")
    return EMAIL_STAGE


async def premium_capture_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = update.effective_message.text.strip()
    if not EMAIL.match(email):
        await update.effective_message.reply_text("❌ Please enter a valid email address, or /cancel to stop.")
        return EMAIL_STAGE
    context.user_data["email"] = email
    context.user_data["selected_exchanges"] = []
    text, _ = exchange_picker([], context.application.bot_data.get("exchange_names", []))
    await update.effective_message.reply_text(text, reply_markup=_premium_exchange_keyboard(context), parse_mode="HTML")
    return EXCHANGES_STAGE


async def premium_exchange_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 2)[2]
    selected = set(context.user_data.get("selected_exchanges", []))
    if name in selected:
        selected.remove(name)
    else:
        selected.add(name)
    context.user_data["selected_exchanges"] = sorted(selected)
    text, _ = exchange_picker(sorted(selected), context.application.bot_data.get("exchange_names", []))
    await query.edit_message_text(text, reply_markup=_premium_exchange_keyboard(context), parse_mode="HTML")
    return EXCHANGES_STAGE


async def premium_exchange_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    selected = list(context.user_data.get("selected_exchanges", []))
    if len(selected) < 2:
        await query.answer("Select at least two exchanges.", show_alert=True)
        return EXCHANGES_STAGE
    await query.answer()
    db = _db(context)
    existing = await db.get_user(query.from_user.id)
    if existing and existing["email"]:
        await db.set_user(query.from_user.id, selected_exchanges=selected)
        await db.log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
        text, keyboard = dashboard()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return ConversationHandler.END
    await db.upsert_user(query.from_user.id, query.from_user.username, context.user_data["email"], selected)
    await db.log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
    await query.edit_message_text("🔐 <b>VIP ACCESS</b>\n\nEnter your VIP key, or type <code>NONE</code> if you do not have one yet.", parse_mode="HTML")
    return VIP_STAGE


async def premium_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("email", None)
    context.user_data.pop("selected_exchanges", None)
    await update.effective_message.reply_text("🛑 Setup cancelled. Use /start whenever you're ready.")
    return ConversationHandler.END


async def _show_dashboard(query) -> None:
    text, keyboard = dashboard()
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")


async def ui_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action = query.data
    db = _db(context)
    user = await db.get_user(query.from_user.id)
    if action == "ui:dashboard":
        text, keyboard = dashboard() if user else welcome()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return
    if action == "ui:help":
        text = screen("ℹ️ HOW IT WORKS", "A simple arbitrage workflow", ["1️⃣ Select at least two exchanges.", "2️⃣ Scanner compares live bid/ask prices.", "3️⃣ Filters remove weak signals.", "4️⃣ Transfer routes are verified when possible.", "5️⃣ Open Order Book for execution analysis.", "6️⃣ Use Paper Trade to simulate the result."])
        await query.edit_message_text(text, reply_markup=nav(("↩️ Dashboard", "ui:dashboard"), columns=1), parse_mode="HTML")
        return
    if action == "ui:exchanges":
        if not user:
            await query.edit_message_text("Register first with /start.")
            return
        selected = json.loads(user["selected_exchanges"] or "[]")
        context.user_data["selected_exchanges"] = selected
        text, _ = exchange_picker(selected, context.application.bot_data.get("exchange_names", []))
        await query.edit_message_text(text, reply_markup=_premium_exchange_keyboard(context), parse_mode="HTML")
        return
    if action.startswith("ui:exchange:"):
        if action.endswith(":done"):
            selected = list(context.user_data.get("selected_exchanges", []))
            if len(selected) < 2:
                await query.answer("Select at least two exchanges.", show_alert=True)
                return
            await db.set_user(query.from_user.id, selected_exchanges=selected)
            await db.log_action(query.from_user.id, "changed_exchanges", ",".join(selected))
            await _show_dashboard(query)
            return
        name = action.split(":", 2)[2]
        selected = set(context.user_data.get("selected_exchanges", []))
        if name in selected:
            selected.remove(name)
        else:
            selected.add(name)
        context.user_data["selected_exchanges"] = sorted(selected)
        text, _ = exchange_picker(sorted(selected), context.application.bot_data.get("exchange_names", []))
        await query.edit_message_text(text, reply_markup=_premium_exchange_keyboard(context), parse_mode="HTML")
        return
    if action in {"ui:filters", "ui:settings"}:
        text, keyboard = settings_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        return
    if action in {"ui:profit", "ui:volume", "ui:watchlist", "ui:blacklist", "ui:alerts", "ui:loose"}:
        if not user:
            await query.edit_message_text("Register first with /start.")
            return
        text = format_filters_message(user_filters(user))
        await query.edit_message_text(text, reply_markup=nav(("↩️ Controls", "ui:filters"), ("🏠 Dashboard", "ui:dashboard")), parse_mode="HTML")
        return
    if action == "ui:reset":
        if not user:
            await query.edit_message_text("Register first with /start.")
            return
        await db.set_user(query.from_user.id, filters=DEFAULT_FILTERS)
        await db.log_action(query.from_user.id, "reset_filters")
        text, keyboard = settings_menu()
        await query.edit_message_text("♻️ <b>Filters reset</b>\n\n" + text, reply_markup=keyboard, parse_mode="HTML")
        return
    if action == "ui:status":
        if not user:
            await query.edit_message_text("Register first with /start.")
            return
        preferences = user_filters(user)
        selected = json.loads(user["selected_exchanges"] or "[]")
        text = format_status_message(user["vip_status"], user["vip_expiry"], selected, preferences.get("loose_mode", False), preferences.get("paused", False), preferences)
        await query.edit_message_text(text, reply_markup=nav(("🎛️ Controls", "ui:filters"), ("🏠 Dashboard", "ui:dashboard")), parse_mode="HTML")
        return
    if action == "ui:scan":
        await enhanced_scan_command(update, context)
        return
    if action == "ui:portfolio":
        if not user:
            await query.edit_message_text("Register first with /start.")
            return
        cursor = await db._db().execute("SELECT COUNT(*) count, COALESCE(SUM(profit), 0) total, COALESCE(MAX(profit), 0) best FROM paper_trades WHERE user_id=?", (query.from_user.id,))
        row = await cursor.fetchone()
        text = screen("📊 PAPER PORTFOLIO", "Your simulated trading dashboard", [f"💰 Balance  <b>${10000 + row['total']:.2f}</b>", f"📈 Total P/L <b>${row['total']:.4f}</b>", f"🧪 Trades   <b>{row['count']}</b>", f"⭐ Best     <b>${row['best']:.4f}</b>"])
        await query.edit_message_text(text, reply_markup=nav(("🏠 Dashboard", "ui:dashboard"), ("🔎 Scan", "ui:scan")), parse_mode="HTML")
        return
    if action == "ui:leaderboard":
        cursor = await db._db().execute("SELECT u.username, SUM(p.profit) total FROM paper_trades p JOIN users u ON u.telegram_id=p.user_id WHERE u.leaderboard_hidden=0 GROUP BY p.user_id ORDER BY total DESC LIMIT 10")
        rows = await cursor.fetchall()
        body = [f"{i}. <b>{r['username'] or 'User'}</b>  ${r['total']:.4f}" for i, r in enumerate(rows, 1)] or ["📭 No paper trades yet."]
        text = screen("🏆 LEADERBOARD", "Paper-trading rankings", body)
        await query.edit_message_text(text, reply_markup=nav(("🏠 Dashboard", "ui:dashboard"), ("📊 Portfolio", "ui:portfolio")), parse_mode="HTML")
        return


def build_ui_handlers(db: Database, admin_ids: set[int], exchange_names: list[str], admin_secret_key: str):
    registration = ConversationHandler(
        entry_points=[CommandHandler("start", premium_start)],
        states={
            EMAIL_STAGE: [CallbackQueryHandler(premium_begin_setup, pattern=r"^ui:start$"), MessageHandler(filters.TEXT & ~filters.COMMAND, premium_capture_email)],
            EXCHANGES_STAGE: [CallbackQueryHandler(premium_exchange_toggle, pattern=r"^ui:exchange:(?!done$)[^:]+$"), CallbackQueryHandler(premium_exchange_done, pattern=r"^ui:exchange:done$")],
            VIP_STAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, redeem_key)],
        },
        fallbacks=[CommandHandler("cancel", premium_cancel)],
        allow_reentry=True,
    )
    return [registration, CommandHandler("menu", premium_menu_command), CallbackQueryHandler(ui_callback, pattern=r"^ui:")]


async def premium_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _db(context).get_user(update.effective_user.id)
    text, keyboard = dashboard() if user else welcome()
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
