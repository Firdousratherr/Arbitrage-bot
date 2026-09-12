from __future__ import annotations

import html

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(context, user_id: int) -> bool:
    return user_id in context.application.bot_data['settings'].admin_ids


def _advisor(context):
    return context.application.bot_data.get('recovery_advisor')


def _recovery_enabled(context) -> bool:
    advisor = _advisor(context)
    return bool(advisor and advisor.enabled)


def _settings_markup(context, uid, f):
    enabled = _recovery_enabled(context)
    rows = [[('🛡️ Strict', 'val:strict'), ('🔓 Loose', 'val:loose')], [('🧠 AI Mode', 'ai')]]
    if _is_admin(context, uid):
        rows.append([(f'🤖 Exchange AI Recovery: {"ON" if enabled else "OFF"}', 'ai_recovery:open')])
    rows.append([('🏠 Dashboard', 'home')])
    return kb(rows)


def _settings_text(row, f, context, uid):
    enabled = _recovery_enabled(context)
    return (
        '⚙️ <b>ARBITRAGE TERMINAL SETTINGS</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>\n'
        f'🤖 Exchange AI Recovery: <b>{"ON" if enabled else "OFF"}</b>\n'
        '📡 Operation: <b>READ-ONLY SCANNER</b>\n\n'
        '🛡️ <b>Strict</b> requires compatible transfer networks and contract/address matching.\n'
        '🔓 <b>Loose</b> bypasses those two checks and marks results as unverified.'
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    await update.effective_message.reply_text(_settings_text(row, f, context, uid), parse_mode='HTML', reply_markup=_settings_markup(context, uid, f))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    await q.edit_message_text(_settings_text(row, f, context, uid), parse_mode='HTML', reply_markup=_settings_markup(context, uid, f))


async def validation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    mode = q.data.split(':', 1)[1].lower()
    if mode not in {'strict', 'loose'}:
        await q.answer('Invalid validation mode.', show_alert=True)
        return
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await q.answer('Active VIP access is required.', show_alert=True)
        return
    await q.answer(f'{mode.title()} mode selected')
    await svc.repo.set_filters(uid, {'validation_mode': mode})
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    await q.edit_message_text(
        f'✅ Validation mode changed to <b>{html.escape(mode.upper())}</b>.\n\n' + _settings_text(row, f, context, uid),
        parse_mode='HTML', reply_markup=_settings_markup(context, uid, f),
    )


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
