from __future__ import annotations

import asyncio
import html
import json

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(context, user_id):
    return user_id in context.application.bot_data['settings'].admin_ids


async def repair_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        if not _is_admin(context, q.from_user.id):
            await q.edit_message_text('⛔ Admin access required.')
            return
        await q.edit_message_text('🛠️ <b>EXCHANGE RECOVERY</b>\n\nPress the button below to repair and health-check all selected exchanges.', parse_mode='HTML', reply_markup=kb([[('🛠️ Fix Selected Exchanges','repair:run')],[('⬅️ Settings','settings'),('🏠 Dashboard','home')]]))
        return
    if not _is_admin(context, update.effective_user.id):
        await update.effective_message.reply_text('⛔ Admin access required.')
        return
    await update.effective_message.reply_text('🛠️ <b>EXCHANGE RECOVERY</b>\n\nPress the button below to repair and health-check all selected exchanges.', parse_mode='HTML', reply_markup=kb([[('🛠️ Fix Selected Exchanges','repair:run')],[('🏠 Dashboard','home')]]))


async def repair_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        await q.answer('Admin access required.', show_alert=True)
        return
    await q.answer('Recovery started')
    svc = context.application.bot_data['service']
    row = await svc.get_user(q.from_user.id)
    try:
        selected = json.loads(row['exchanges'] or '[]')
    except Exception:
        selected = []
    if not selected:
        await q.edit_message_text('⚠️ No exchanges are selected.', reply_markup=kb([[('🏦 Exchanges','exchanges'),('🏠 Dashboard','home')]]))
        return
    exchanges = context.application.bot_data['exchanges']
    await q.edit_message_text('🛠️ <b>FIXING EXCHANGES…</b>\n\nStarting deterministic repair and health checks. Please wait.', parse_mode='HTML')

    async def repair_one(name):
        adapter = exchanges.get(name)
        if adapter is None:
            return name, False, 'not loaded'
        try:
            ok = await asyncio.wait_for(adapter.repair(), timeout=25.0)
            health = getattr(adapter, 'health_snapshot', lambda: {})()
            state = health.get('state', 'unknown') if isinstance(health, dict) else 'unknown'
            return name, bool(ok), state
        except Exception as exc:
            return name, False, f'{type(exc).__name__}: {str(exc)[:120]}'

    results = await asyncio.gather(*(repair_one(name) for name in selected), return_exceptions=False)
    lines = ['🛠️ <b>EXCHANGE RECOVERY RESULT</b>', '']
    for name, ok, detail in results:
        lines.append(f'{"🟢" if ok else "🔴"} <b>{html.escape(str(name).title())}</b> · {"healthy" if ok else html.escape(str(detail))}')
    lines += ['', 'Recovery uses deterministic repair only. Authentication/invalid-request failures are not automatically bypassed.']
    await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=kb([[('🛠️ Fix Again','repair:run'),('⚙️ Settings','settings')],[('🏠 Dashboard','home')]]))


async def repair_callback(update, context):
    data = update.callback_query.data
    if data == 'repair:open': await repair_open(update, context)
    elif data == 'repair:run': await repair_run(update, context)
