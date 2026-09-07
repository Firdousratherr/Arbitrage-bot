from __future__ import annotations

import html
import json

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import ai_cmd, exchanges, help as _unused  # type: ignore[attr-defined]
from .handlers import start
from .handlers import kb


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
    uid = update.effective_user.id
    row = await svc.get_user(uid)
    f = svc.repo.filters_from_row(row)
    coins = ', '.join(sorted(f.selected_coins)) if f.selected_coins else 'All'
    text = (
        '📊 <b>SCAN FILTERS</b>\n\n'
        f'📈 Minimum gap: <b>{f.min_gap:.2f}%</b>\n'
        f'💰 Minimum net profit: <b>{f.min_net_profit:.2f}%</b>\n'
        f'💧 Minimum volume: <b>${f.min_volume:,.0f}</b>\n'
        f'💦 Minimum liquidity: <b>${f.min_liquidity:,.0f}</b>\n'
        f'⏱ Maximum data age: <b>{f.max_data_age:.1f}s</b>\n'
        f'💱 Quote: <b>{html.escape(f.quote_currency)}</b>\n'
        f'🪙 Coins: <b>{html.escape(coins)}</b>\n'
        f'🛡️ Validation: <b>{html.escape(f.validation_mode.upper())}</b>\n'
        f'💸 Require fees: <b>{"YES" if f.require_fees else "NO"}</b>'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=kb([[('⚙️ Settings', 'settings'), ('🏠 Dashboard', 'home')]]))


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
        '/filters — view scan filters\n'
        '/settings — validation and AI settings\n'
        '/status — exchange/runtime status\n'
        '/diagnostics — latest scan diagnostics\n'
        '/ai — AI result mode\n'
        '/vipkey — activate VIP access\n\n'
        'Admin: /genkey KEY DAYS|lifetime, /aiprobe'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=kb([[('🏠 Dashboard', 'home'), ('🏦 Exchanges', 'exchanges')]]))
