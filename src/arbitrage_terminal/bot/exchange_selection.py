from __future__ import annotations

import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _markup(names, selected):
    rows = []
    for i in range(0, len(names), 2):
        rows.append([
            InlineKeyboardButton(f"{'🟢' if n in selected else '⚪'} {n.title()}", callback_data=f"ex:{n}")
            for n in names[i:i + 2]
        ])
    rows.append([InlineKeyboardButton('Select All', callback_data='ex:all'), InlineKeyboardButton('Clear All', callback_data='ex:none')])
    rows.append([InlineKeyboardButton(f'✅ Save Selection ({len(selected)})', callback_data='ex:save')])
    rows.append([InlineKeyboardButton('🏠 Dashboard', callback_data='home')])
    return InlineKeyboardMarkup(rows)


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
                await q.answer('Select at least two exchanges.', show_alert=True)
                return
            await svc.set_exchanges(uid, list(selected))
            await q.answer('Selection saved.')
            await q.edit_message_text(
                '✅ <b>Exchange selection saved.</b>\n\n'
                f'🏦 {len(selected)} exchanges selected.\n\n'
                'All selected exchanges have equal priority.',
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
        await q.answer('Updated.')
        await q.edit_message_text(
            '🏦 <b>SELECT EXCHANGES</b>\n\n'
            'All selected exchanges are equal. No priority.\n\n'
            f'Selected: <b>{len(selected)}</b> / {len(names)}',
            parse_mode='HTML',
            reply_markup=_markup(names, selected),
        )
    except Exception as exc:
        context.application.logger.exception('exchange selection callback failed')
        try:
            await q.answer('Exchange selection failed. Please try again.', show_alert=True)
        except Exception:
            pass
        try:
            await q.edit_message_text(
                f'⚠️ <b>Exchange selection error</b>\n\n{type(exc).__name__}: {str(exc)[:180]}',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Try Again', callback_data='exchanges')]])
            )
        except Exception:
            pass
