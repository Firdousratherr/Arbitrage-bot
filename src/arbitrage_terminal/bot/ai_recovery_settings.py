from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(context, user_id: int) -> bool:
    settings = context.application.bot_data['settings']
    return user_id in settings.admin_ids


def _advisor(context):
    return context.application.bot_data.get('recovery_advisor')


def _screen(context) -> tuple[str, object]:
    advisor = _advisor(context)
    enabled = bool(advisor and advisor.enabled)
    state = '🟢 ON' if enabled else '🔴 OFF'
    return (
        '🤖 <b>EXCHANGE AI RECOVERY</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        f'Status: <b>{state}</b>\n\n'
        'AI recovery is used only after deterministic exchange/CCXT recovery fails.\n'
        'It cannot trade, change credentials, or modify production code.\n\n'
        'This control applies to the running bot instance and does not require an environment change.',
        kb([
            [('🟢 Enable' if not enabled else '🔴 Disable', 'ai_recovery:toggle')],
            [('⬅️ Back to Settings', 'ai_recovery:back')],
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
    if data == 'ai_recovery:back':
        # Keep this screen self-contained; the main Settings button can be used
        # to reopen the regular settings view.
        await q.edit_message_text('⚙️ <b>SETTINGS</b>\n\nUse the buttons below to manage validation and AI recovery.', parse_mode='HTML', reply_markup=kb([[('🤖 Exchange AI Recovery', 'ai_recovery:open')], [('🏠 Dashboard', 'home')]]))
        return
    if data == 'ai_recovery:open':
        text, markup = _screen(context)
        await q.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
