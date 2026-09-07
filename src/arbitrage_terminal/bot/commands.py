from __future__ import annotations

import html
import json

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb, start
from arbitrage_terminal.infrastructure.repository import DEFAULT_FILTERS


def _filter_text(f):
    coins = ', '.join(sorted(f.selected_coins)) if f.selected_coins else 'All'
    return (
        '📊 <b>SCAN FILTERS</b>\n\n'
        f'📈 Minimum gap: <b>{f.min_gap:.2f}%</b>\n'
        f'💰 Minimum net profit: <b>{f.min_net_profit:.2f}%</b>\n'
        f'💧 Minimum volume: <b>${f.min_volume:,.0f}</b>\n'
        f'💦 Minimum liquidity: <b>${f.min_liquidity:,.0f}</b>\n'
        f'⏱ Maximum data age: <b>{f.max_data_age:.1f}s</b>\n'
        f'💱 Quote: <b>{html.escape(f.quote_currency)}</b>\n'
        f'🪙 Coins: <b>{html.escape(coins)}</b>\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'💸 Require fees: <b>{"YES" if f.require_fees else "NO"}'
    )


def _filter_keyboard(f):
    fees = '💸 Fees: ON' if f.require_fees else '💸 Fees: OFF'
    validation = '🛡️ Strict' if f.validation_mode == 'strict' else '🔓 Loose'
    return kb([
        [('✏️ Edit Filters', 'filter:help'), ('🔄 Reset Filters', 'filter:reset')],
        [(validation, 'filter:toggle_validation'), (fees, 'filter:toggle_fees')],
        [('⚙️ Settings', 'settings'), ('🏠 Dashboard', 'home')],
    ])


async def dashboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await update.effective_message.reply_text('🔒 Active VIP access is required.')
        return
    row = await svc.get_user(uid)
    selected = json.loads(row['exchanges'] or '[]')
    available = context.application.bot_data['exchanges']
    lines = ['📡 <b>EXCHANGE STATUS</b>', '']
    for name in selected:
        lines.append(f"{'🟢' if name in available else '🔴'} {html.escape(name.title())} · {'available' if name in available else 'unavailable'}")
    if not selected:
        lines.append('⚠️ No exchanges selected. Use /exchanges.')
    rows = await svc.history(uid, limit=1)
    if rows:
        r = rows[0]
        lines += ['', f"Last scan: <b>{html.escape(str(r['state']).upper())}</b> · {r['opportunities_found']} opportunities"]
    await update.effective_message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=kb([[('🏠 Dashboard', 'home'), ('🏦 Exchanges', 'exchanges')]]))


async def diagnostics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    rows = await svc.history(uid, limit=1)
    if not rows:
        await update.effective_message.reply_text('📡 No scan diagnostics yet. Run /scan first.')
        return
    scan_id = rows[0]['scan_id']
    p = await svc.scan(uid, scan_id)
    if not p:
        await update.effective_message.reply_text('⚠️ Latest scan snapshot is unavailable.')
        return
    lines = ['📡 <b>LATEST SCAN DIAGNOSTICS</b>', f"🆔 <code>{html.escape(scan_id)}</code>", '']
    for d in p.get('diagnostics', []):
        status = str(d.get('status', 'unknown'))
        icon = '🟢' if status == 'ok' else '🟡' if status == 'degraded' else '🔴'
        latency = d.get('latency_ms')
        latency_text = f" · {float(latency):.0f}ms" if latency is not None else ''
        detail = d.get('error_type') or d.get('detail') or ''
        suffix = f" · {html.escape(str(detail)[:100])}" if detail else ''
        lines.append(f"{icon} {html.escape(str(d.get('exchange','?')))} · {html.escape(status)}{latency_text}{suffix}")
    if p.get('warnings'):
        lines += ['', '<b>Warnings</b>'] + [f"⚠️ {html.escape(str(x))}" for x in p['warnings'][:5]]
    await update.effective_message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=kb([[('⬅️ Results', f"page:{scan_id}:0:all"), ('🏠 Dashboard', 'home')]]))


async def filters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    row = await svc.get_user(update.effective_user.id)
    f = svc.repo.filters_from_row(row)
    await update.effective_message.reply_text(_filter_text(f), parse_mode='HTML', reply_markup=_filter_keyboard(f))


async def _save_filter_patch(svc, uid, patch):
    row = await svc.get_user(uid)
    raw = json.loads(row['filters'] or '{}')
    if not isinstance(raw, dict):
        raw = {}
    raw.update(patch)
    # Validate the merged configuration before persisting it.
    svc.repo.filters_from_row({**dict(row), 'filters': json.dumps(raw)})
    await svc.repo.set_filters(uid, raw)


async def setfilter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await update.effective_message.reply_text('🔒 Active VIP access is required.')
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text(
            '📊 <b>SET FILTER</b>\n\n'
            'Usage:\n'
            '<code>/setfilter gap 1.0</code>\n'
            '<code>/setfilter net 0.5</code>\n'
            '<code>/setfilter volume 50000</code>\n'
            '<code>/setfilter liquidity 5000</code>\n'
            '<code>/setfilter age 10</code>\n'
            '<code>/setfilter quote USDT</code>\n'
            '<code>/setfilter coins BTC,ETH,SOL</code>\n'
            '<code>/setfilter fees on</code>\n'
            '<code>/setfilter validation strict</code>\n\n'
            'Use <code>coins all</code> to remove the coin restriction.',
            parse_mode='HTML'
        )
        return
    key = context.args[0].lower().strip()
    value = ' '.join(context.args[1:]).strip()
    aliases = {
        'gap': 'min_gap', 'min_gap': 'min_gap',
        'net': 'min_net_profit', 'min_net_profit': 'min_net_profit',
        'volume': 'min_volume', 'min_volume': 'min_volume',
        'liquidity': 'min_liquidity', 'min_liquidity': 'min_liquidity',
        'age': 'max_data_age', 'max_data_age': 'max_data_age',
        'quote': 'quote_currency', 'coins': 'selected_coins',
        'fees': 'require_fees', 'require_fees': 'require_fees',
        'validation': 'validation_mode', 'validation_mode': 'validation_mode',
    }
    field = aliases.get(key)
    if not field:
        await update.effective_message.reply_text('⚠️ Unknown filter. Use /setfilter to see supported filters.')
        return
    try:
        if field in {'min_gap', 'min_net_profit', 'min_volume', 'min_liquidity'}:
            number = float(value)
            if number < 0:
                raise ValueError('must be non-negative')
            parsed = number
        elif field == 'max_data_age':
            parsed = float(value)
            if not 1 <= parsed <= 120:
                raise ValueError('age must be between 1 and 120 seconds')
        elif field == 'quote_currency':
            parsed = value.upper()
            if not 2 <= len(parsed) <= 10 or not parsed.isalnum():
                raise ValueError('quote must be a 2-10 character currency code')
        elif field == 'selected_coins':
            if value.lower() == 'all':
                parsed = []
            else:
                parsed = [x.strip().upper() for x in value.split(',') if x.strip()]
                if not parsed or len(parsed) > 50 or any(not x.isalnum() for x in parsed):
                    raise ValueError('use up to 50 comma-separated coin symbols')
        elif field == 'require_fees':
            parsed = {'on': True, 'true': True, 'yes': True, '1': True, 'off': False, 'false': False, 'no': False, '0': False}[value.lower()]
        else:
            parsed = value.lower()
            if parsed not in {'strict', 'loose'}:
                raise ValueError('validation must be strict or loose')
        await _save_filter_patch(svc, uid, {field: parsed})
        f = svc.repo.filters_from_row(await svc.get_user(uid))
        await update.effective_message.reply_text(
            f'✅ <b>{field}</b> set to <b>{html.escape(str(parsed))}</b>.\n\n{_filter_text(f)}',
            parse_mode='HTML', reply_markup=_filter_keyboard(f)
        )
    except (ValueError, KeyError, TypeError) as exc:
        await update.effective_message.reply_text(f'⚠️ Invalid value: {html.escape(str(exc))}')


async def resetfilters_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    uid = update.effective_user.id
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await update.effective_message.reply_text('🔒 Active VIP access is required.')
        return
    await svc.repo.set_filters(uid, dict(DEFAULT_FILTERS))
    f = svc.repo.filters_from_row(await svc.get_user(uid))
    await update.effective_message.reply_text('🔄 <b>Filters reset to defaults.</b>\n\n' + _filter_text(f), parse_mode='HTML', reply_markup=_filter_keyboard(f))


async def filter_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await q.edit_message_text('🔒 <b>Active VIP access is required.</b>', parse_mode='HTML')
        return
    data = q.data
    if data == 'filter:help':
        await q.edit_message_text(
            '✏️ <b>FILTER COMMANDS</b>\n\n'
            '<code>/setfilter gap 1.0</code>\n'
            '<code>/setfilter net 0.5</code>\n'
            '<code>/setfilter volume 50000</code>\n'
            '<code>/setfilter liquidity 5000</code>\n'
            '<code>/setfilter age 10</code>\n'
            '<code>/setfilter quote USDT</code>\n'
            '<code>/setfilter coins BTC,ETH,SOL</code>\n'
            '<code>/setfilter fees on</code>\n'
            '<code>/setfilter validation strict</code>\n\n'
            'Use <code>/setfilter coins all</code> to scan all coins.',
            parse_mode='HTML', reply_markup=kb([[('⬅️ Filters', 'filter:back')]])
        )
    elif data == 'filter:back':
        f = svc.repo.filters_from_row(await svc.get_user(uid))
        await q.edit_message_text(_filter_text(f), parse_mode='HTML', reply_markup=_filter_keyboard(f))
    elif data == 'filter:reset':
        await svc.repo.set_filters(uid, dict(DEFAULT_FILTERS))
        f = svc.repo.filters_from_row(await svc.get_user(uid))
        await q.edit_message_text('🔄 <b>Filters reset to defaults.</b>\n\n' + _filter_text(f), parse_mode='HTML', reply_markup=_filter_keyboard(f))
    elif data in {'filter:toggle_fees', 'filter:toggle_validation'}:
        f = svc.repo.filters_from_row(await svc.get_user(uid))
        if data.endswith('fees'):
            await _save_filter_patch(svc, uid, {'require_fees': not f.require_fees})
        else:
            await _save_filter_patch(svc, uid, {'validation_mode': 'loose' if f.validation_mode == 'strict' else 'strict'})
        f = svc.repo.filters_from_row(await svc.get_user(uid))
        await q.edit_message_text('✅ <b>Filter updated.</b>\n\n' + _filter_text(f), parse_mode='HTML', reply_markup=_filter_keyboard(f))


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    svc = context.application.bot_data['service']
    row = await svc.get_user(update.effective_user.id)
    f = svc.repo.filters_from_row(row)
    text = (
        '⚙️ <b>SETTINGS</b>\n\n'
        f'🔐 Validation mode: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>\n'
        f'🧪 Simulation: <b>ON</b>\n\n'
        'Strict validation requires compatible transfer networks and contract/address matching.\n'
        'Loose validation bypasses those two checks and marks results as unverified.'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=kb([
        [('🛡️ Strict', 'val:strict'), ('🔓 Loose', 'val:loose')],
        [('🧠 AI Mode', 'ai'), ('🏠 Dashboard', 'home')],
    ]))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        '❓ <b>ARBITRAGE TERMINAL COMMANDS</b>\n\n'
        '/dashboard — open dashboard\n'
        '/scan — open scanner\n'
        '/results — scan history/results\n'
        '/exchanges — select exchanges\n'
        '/filters — view and edit scan filters\n'
        '/setfilter KEY VALUE — change a filter\n'
        '/resetfilters — restore default filters\n'
        '/settings — validation and AI settings\n'
        '/status — exchange/runtime status\n'
        '/diagnostics — latest scan diagnostics\n'
        '/ai — AI result mode\n'
        '/vipkey — activate VIP access\n\n'
        'Admin: /genkey KEY DAYS|lifetime, /aiprobe'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=kb([[('📊 Filters', 'filters'), ('🏠 Dashboard', 'home')]]))
