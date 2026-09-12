from __future__ import annotations

import asyncio
import html
import time

from .handlers import kb
from .ui import DIVIDER, scan_status

SPINNER = ('◐', '◓', '◑', '◒')


def _progress_percent(stage, data, selected):
    if stage == 'start': return 5
    if stage == 'exchange':
        total = max(1, int(data.get('total', selected) or selected or 1)); completed = max(0, min(total, int(data.get('completed', 0) or 0)))
        return 10 + int(35 * completed / total)
    if stage == 'markets': return 50
    if stage == 'fees': return 65
    if stage == 'candidates': return 80
    if stage == 'orderbook':
        total = max(1, int(data.get('total', 1) or 1)); validated = max(0, min(total, int(data.get('validated', 0) or 0)))
        return 80 + int(10 * validated / total)
    if stage in {'recovery', 'exchange_recovery'}: return max(10, min(90, int(data.get('percent', 50) or 50)))
    if stage == 'network':
        total = max(1, int(data.get('total', 1) or 1)); validated = max(0, min(total, int(data.get('validated', 0) or 0)))
        return 90 + int(5 * validated / total)
    if stage == 'opportunity': return 95
    if stage == 'complete': return 100
    return 50


def _progress_bar(percent):
    filled = max(0, min(10, int(percent / 10)))
    return '█' * filled + '░' * (10 - filled)


def _window(stage, selected, states, comparisons=0, opportunities=0, best=None, detail='', frame=0, percent=None, elapsed=None):
    spinner = SPINNER[frame % len(SPINNER)]; percent = 50 if percent is None else percent
    lines = ['⚡ <b>CRYPTO ARBITRAGE SCANNER</b>', f'<code>{DIVIDER}</code>', '', f'⚡ <b>{html.escape(stage)}</b> {spinner}', f'[{_progress_bar(percent)}] <b>{percent}%</b>']
    if elapsed is not None: lines.append(f'⏱ <b>Elapsed:</b> {elapsed:.0f}s')
    lines += ['', f'🏦 <b>Exchanges:</b> {selected}']
    for name, status in states.items():
        icon = '🟢' if status == 'healthy' else '🔴' if status == 'failed' else '🟡'
        lines.append(f'{icon} {html.escape(name.title())} · {html.escape(status)}')
    lines += ['', f'🔄 <b>Comparisons:</b> {comparisons:,}', f'🔥 <b>Opportunities:</b> {opportunities:,}']
    if best:
        lines += ['', f'💎 <b>Best so far:</b> {html.escape(best["symbol"])} +{best["gap"]:.3f}%', f'   🟢 {html.escape(best["buy"])} → 🔴 {html.escape(best["sell"])}']
    if detail: lines += ['', f'ℹ️ {html.escape(detail)}']
    return '\n'.join(lines)


async def live_scan_callback(update, context):
    q = update.callback_query; svc = context.application.bot_data['service']; uid = q.from_user.id
    await q.answer('🚀 Scan started')
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await q.edit_message_text('🔐 <b>VIP ACCESS REQUIRED</b>\n\nEnter your VIP key to activate access.', parse_mode='HTML', reply_markup=kb([[('🔑 Enter VIP Key', 'vip:enter')]])); return
    row = await svc.get_user(uid)
    try:
        import json; selected = json.loads(row['exchanges'] or '[]')
    except Exception: selected = []
    states = {n: 'waiting' for n in selected}; frame = 0; started = time.monotonic(); edit_lock = asyncio.Lock(); last_text = ''; last_edit = 0.0
    current_stage = 'Starting scan'; current_percent = 5; current_comparisons = 0; current_opportunities = 0; current_best = None; current_detail = ''

    async def safe_edit(text, force=False):
        nonlocal last_text, last_edit
        if text == last_text: return
        now = time.monotonic()
        if not force and now - last_edit < 0.8: return
        async with edit_lock:
            if text == last_text: return
            try: await q.edit_message_text(text, parse_mode='HTML'); last_text = text; last_edit = time.monotonic()
            except Exception: pass

    await safe_edit(_window('Starting scan', len(selected), states, frame=frame, percent=5, elapsed=0), force=True)

    async def heartbeat():
        nonlocal frame
        while True:
            await asyncio.sleep(5); frame += 1; elapsed = time.monotonic() - started
            await safe_edit(_window(current_stage, len(selected), states, current_comparisons, current_opportunities, current_best, current_detail or f'No new stage update · {elapsed:.0f}s elapsed', frame, current_percent, elapsed))

    heartbeat_task = asyncio.create_task(heartbeat())

    async def progress(stage, data):
        nonlocal frame, current_stage, current_percent, current_comparisons, current_opportunities, current_best, current_detail
        frame += 1
        if stage == 'exchange' and data.get('exchange'): states[data['exchange']] = data.get('status', 'waiting')
        labels = {
            'start': '🔌 Connecting to exchanges…', 'exchange': '📡 Exchange response received', 'markets': '📊 Markets loaded · building comparison set…',
            'fees': '💸 Fee data loaded · evaluating gaps…', 'candidates': '🔎 Comparing prices before order-book validation…',
            'orderbook': '📚 Validating executable order books…', 'network': '🌐 Validating transfer networks…',
            'recovery': '🛠️ Exchange error detected · fixing automatically…', 'exchange_recovery': '🛠️ Exchange recovery in progress…',
            'opportunity': '💎 Live opportunity found!', 'complete': '✅ Scan complete',
        }
        label = labels.get(stage, stage.replace('_', ' ').title()); percent = _progress_percent(stage, data, len(selected)); completed = data.get('completed'); total = data.get('total', len(selected))
        if stage == 'exchange' and completed is not None: label = f'📡 Exchange responses · {completed}/{total}'
        elif stage == 'exchange' and data.get('status') == 'loading': label = f'⏳ Waiting for {str(data.get("exchange", "exchange")).title()}…'
        elif stage == 'candidates' and data.get('comparisons') is not None: label = f'🔎 Comparing prices · {int(data.get("comparisons", 0)):,} comparisons'
        elif stage == 'orderbook' and data.get('total') is not None: label = f'📚 Order-book validation · {int(data.get("validated", 0))}/{int(data.get("total", 0))}'
        elif stage == 'network' and data.get('total') is not None: label = f'🌐 Network validation · {int(data.get("validated", 0))}/{int(data.get("total", 0))}'
        elif stage in {'recovery', 'exchange_recovery'}:
            exchange = str(data.get('exchange', 'exchange')).title(); action = str(data.get('action', 'repair')).replace('_', ' '); label = f'🛠️ Fixing {exchange} · {action}…'
        best = None
        if stage == 'opportunity' and data.get('symbol'): best = {'symbol': data['symbol'], 'gap': data.get('gap', 0), 'buy': data.get('buy', '?'), 'sell': data.get('sell', '?')}
        current_stage = label; current_percent = percent; current_comparisons = int(data.get('comparisons', current_comparisons) or current_comparisons); current_opportunities = int(data.get('count', data.get('opportunities', current_opportunities)) or current_opportunities); current_best = best or current_best; current_detail = str(data.get('message', '') or '')
        await safe_edit(_window(label, len(selected), states, current_comparisons, current_opportunities, current_best, current_detail, frame, percent, time.monotonic() - started), force=stage in {'complete', 'opportunity', 'recovery', 'exchange_recovery'})

    try:
        snap = await svc.run_scan(uid, progress=progress); p = snap.to_dict(); scan_id = p['scan_id']
        await q.edit_message_text(scan_status(p), parse_mode='HTML', reply_markup=kb([
            [('🔥 Best Results', f'page:{scan_id}:0:best'), ('📋 All Results', f'page:{scan_id}:0:all')],
            [('📡 Diagnostics', f'rdiag:{scan_id}:0:all'), ('🔎 Debug Coin', f'rdebug:{scan_id}:0:all')],
            [('🧠 AI Analysis', f'raian:{scan_id}:0:all'), ('🔄 Scan Again', 'scan')],
            [('👨‍💻 Contact Developer', 'contact:open')],
        ]))
    except Exception as exc:
        await q.edit_message_text(f'❌ <b>SCAN FAILED</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:250])}', parse_mode='HTML', reply_markup=kb([[('🔄 Try Again', 'scan'), ('🏠 Dashboard', 'home')]]))
    finally:
        heartbeat_task.cancel(); await asyncio.gather(heartbeat_task, return_exceptions=True)
