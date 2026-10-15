from __future__ import annotations

import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _markup(names, selected):
    rows = []
    for i in range(0, len(names), 2):
        rows.append([
            InlineKeyboardButton(f"{'🟢' if n in selected else '⚪'} {n.title()}", callback_data=f'ex:{n}')
            for n in names[i:i + 2]
        ])
    rows.append([InlineKeyboardButton('✨ Select All', callback_data='ex:all'), InlineKeyboardButton('🧹 Clear All', callback_data='ex:none')])
    rows.append([InlineKeyboardButton(f'✅ SAVE SELECTION · {len(selected)}', callback_data='ex:save')])
    rows.append([InlineKeyboardButton('🏠 Dashboard', callback_data='home')])
    return InlineKeyboardMarkup(rows)


def _selection_text(selected, names):
    return (
        '🏦 <b>EXCHANGE CONTROL CENTER</b>\n'
        '━━━━━━━━━━━━━━━━━━━━\n\n'
        '🎯 Choose the venues used for cross-exchange comparison.\n'
        '⚖️ All selected exchanges have equal priority.\n\n'
        f'🟢 <b>Selected:</b> {len(selected)} / {len(names)}\n\n'
        '💡 Tap an exchange to toggle it.'
    )


async def dashboard_exchanges_callback(update, context):
    q = update.callback_query
    try:
        svc = context.application.bot_data['service']
        uid = q.from_user.id
        if svc.settings.require_vip and not await svc.repo.vip_active(uid):
            await q.answer()
            await q.edit_message_text(
                '🔐 <b>VIP ACCESS REQUIRED</b>\n\nEnter your VIP key to activate access.',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔑 Enter VIP Key', callback_data='vip:enter')]]),
            )
            return
        row = await svc.get_user(uid)
        selected = set(json.loads(row['exchanges'] or '[]'))
        names = context.application.bot_data['exchange_names']
        await q.answer()
        await q.edit_message_text(_selection_text(selected, names), parse_mode='HTML', reply_markup=_markup(names, selected))
    except Exception as exc:
        context.application.logger.exception('dashboard exchange selector failed')
        try:
            await q.answer('Exchange selection failed. Please try again.', show_alert=True)
        except Exception:
            pass
        try:
            await q.edit_message_text(f'⚠️ <b>Exchange selection error</b>\n\n{type(exc).__name__}: {str(exc)[:180]}', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Try Again', callback_data='exchanges')]]))
        except Exception:
            pass


async def exchange_selection_callback(update, context):
    q = update.callback_query
    try:
        svc = context.application.bot_data['service']
        uid = q.from_user.id
        row = await svc.get_user(uid)
        selected = set(json.loads(row['exchanges'] or '[]'))
        names = context.application.bot_data['exchange_names']
        action = q.data[3:]
        if action == 'all':
            selected = set(names)
        elif action == 'none':
            selected.clear()
        elif action == 'save':
            if len(selected) < 2:
                await q.answer('⚠️ Select at least two exchanges.', show_alert=True)
                return
            await svc.set_exchanges(uid, list(selected))
            await q.answer('✅ Selection saved.')
            await q.edit_message_text(
                '✅ <b>EXCHANGE SELECTION SAVED</b>\n'
                '━━━━━━━━━━━━━━━━━━━━\n\n'
                f'🏦 <b>{len(selected)}</b> exchanges selected.\n'
                '⚖️ Equal priority across all selected venues.\n\n'
                '🚀 Ready for a new arbitrage scan.',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton('🔎 Scan Arbitrage', callback_data='scan')],
                    [InlineKeyboardButton('🏦 Change Exchanges', callback_data='exchanges')],
                    [InlineKeyboardButton('🏠 Dashboard', callback_data='home')],
                ]),
            )
            return
        else:
            if action not in names:
                await q.answer('Unknown exchange.', show_alert=True)
                return
            if action in selected:
                selected.remove(action)
            else:
                selected.add(action)
        await svc.set_exchanges(uid, list(selected))
        await q.answer('🔄 Updated.')
        await q.edit_message_text(_selection_text(selected, names), parse_mode='HTML', reply_markup=_markup(names, selected))
    except Exception as exc:
        context.application.logger.exception('exchange selection callback failed')
        try:
            await q.answer('Exchange selection failed. Please try again.', show_alert=True)
        except Exception:
            pass
        try:
            await q.edit_message_text(f'⚠️ <b>Exchange selection error</b>\n\n{type(exc).__name__}: {str(exc)[:180]}', parse_mode='HTML', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Try Again', callback_data='exchanges')]]))
        except Exception:
            pass
