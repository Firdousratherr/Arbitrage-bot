from __future__ import annotations

import html

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(context, user_id: int) -> bool:
    settings = context.application.bot_data['settings']
    return user_id in settings.admin_ids


def _advisor(context):
    return context.application.bot_data.get('recovery_advisor')


def _recovery_enabled(context) -> bool:
    advisor = _advisor(context)
    return bool(advisor and advisor.enabled)


def _screen(context) -> tuple[str, object]:
    enabled = _recovery_enabled(context)
    state = '🟢 ON' if enabled else '🔴 OFF'
    return (
        '🤖 <b>EXCHANGE AI RECOVERY</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'Status: <b>{state}</b>\n\n'
        'AI recovery is used only after deterministic exchange/CCXT recovery fails.\n'
        'It cannot trade, change credentials, or modify production code.\n\n'
        'This control changes the running bot immediately and does not require an environment change.',
        kb([
            [('🟢 Enable' if not enabled else '🔴 Disable', 'ai_recovery:toggle')],
            [('⬅️ Back to Settings', 'settings')],
        ]),
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    enabled = _recovery_enabled(context)
    rows = [[('🛡️ Strict', 'val:strict'), ('🔓 Loose', 'val:loose')], [('🧠 AI Mode', 'ai')]]
    if _is_admin(context, uid):
        rows.append([(f'🤖 Exchange AI Recovery: {"ON" if enabled else "OFF"}', 'ai_recovery:open')])
    rows.append([('🏠 Dashboard', 'home')])
    text = (
        '⚙️ <b>ARBITRAGE TERMINAL SETTINGS</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>\n'
        f'🤖 Exchange AI Recovery: <b>{"ON" if enabled else "OFF"}</b>\n'
        '📡 Operation: <b>READ-ONLY SCANNER</b>\n\n'
        'AI exchange recovery runs only after deterministic recovery fails.\n'
        'It cannot trade, change credentials, or modify production code.'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=kb(rows))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    enabled = _recovery_enabled(context)
    rows = [[('🛡️ Strict', 'val:strict'), ('🔓 Loose', 'val:loose')], [('🧠 AI Mode', 'ai')]]
    if _is_admin(context, uid):
        rows.append([(f'🤖 Exchange AI Recovery: {"ON" if enabled else "OFF"}', 'ai_recovery:open')])
    rows.append([('🏠 Dashboard', 'home')])
    text = (
        '⚙️ <b>ARBITRAGE TERMINAL SETTINGS</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>\n'
        f'🤖 Exchange AI Recovery: <b>{"ON" if enabled else "OFF"}</b>\n'
        '📡 Operation: <b>READ-ONLY SCANNER</b>\n\n'
        'AI exchange recovery runs only after deterministic recovery fails.\n'
        'It cannot trade, change credentials, or modify production code.'
    )
    await q.edit_message_text(text, parse_mode='HTML', reply_markup=kb(rows))


async def ai_recovery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(context, update.effective_user.id):
        await update.effective_message.reply_text('⛔ Admin access required.')
        return
    text, markup = _screen(context)
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=markup)


async def ai_recovery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(context, q.from_user.id):
        await q.edit_message_text('⛔ Admin access required.')
        return
    data = q.data
    if data == 'ai_recovery:toggle':
        advisor = _advisor(context)
        if advisor is None:
            await q.edit_message_text('⚠️ AI recovery advisor is unavailable.')
            return
        advisor.enabled = not advisor.enabled
        state = 'enabled' if advisor.enabled else 'disabled'
        text, markup = _screen(context)
        await q.edit_message_text(f'✅ Exchange AI recovery {state}.\n\n' + text, parse_mode='HTML', reply_markup=markup)
        return
    if data == 'ai_recovery:open':
        text, markup = _screen(context)
        await q.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
