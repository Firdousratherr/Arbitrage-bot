from __future__ import annotations

import html

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(context, user_id: int) -> bool:
    return user_id in context.application.bot_data['settings'].admin_ids


def _settings_markup(context, uid, f):
    rows = [[('🛡️ Strict', 'val:strict'), ('🔓 Loose', 'val:loose')], [('🧠 AI Mode', 'ai')], [('🛠️ Exchange Tools', 'repair:open')]]
    rows.append([('🏠 Dashboard', 'home')])
    return kb(rows)


def _settings_text(row, f, context, uid):
    return (
        '⚙️ <b>ARBITRAGE TERMINAL SETTINGS</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>\n'
        '🛠️ Automatic exchange self-healing: <b>OFF</b>\n'
        '📡 Operation: <b>READ-ONLY SCANNER</b>\n\n'
        'Automatic recovery is disabled during scans to avoid adding latency.\n'
        'Use <b>Exchange Tools</b> for manual diagnostics; administrator-only repair controls are hidden from regular users.\n\n'
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


async def ai_recovery_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(context, update.effective_user.id):
        await update.effective_message.reply_text('⛔ Admin access required.')
        return
    await update.effective_message.reply_text(
        '🛠️ <b>AUTOMATIC EXCHANGE SELF-HEALING</b>\n\n'
        'Automatic exchange self-healing has been disabled to keep scans fast and predictable.\n\n'
        'Use <b>Exchange Tools</b> for manual diagnostics and administrator-only repair.',
        parse_mode='HTML', reply_markup=kb([[('🛠️ Exchange Tools', 'repair:open')], [('🏠 Dashboard', 'home')]]),
    )


async def ai_recovery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not _is_admin(context, q.from_user.id):
        await q.edit_message_text('⛔ Admin access required.')
        return
    await q.edit_message_text(
        '🛠️ <b>AUTOMATIC EXCHANGE SELF-HEALING</b>\n\n'
        'Automatic exchange self-healing is disabled for scan performance.\n\n'
        'Use <b>Exchange Tools</b> for manual diagnostics and administrator-only repair.',
        parse_mode='HTML', reply_markup=kb([[('🛠️ Exchange Tools', 'repair:open')], [('⬅️ Settings', 'settings')]]),
    )