from __future__ import annotations

import html

from .handlers import kb
from .ui import DIVIDER, scan_status


SPINNER = ('◐', '◓', '◑', '◒')
STAGE_ICONS = {
    'start': '🚀',
    'exchange': '🔌',
    'markets': '📊',
    'fees': '💸',
    'candidates': '🔎',
    'opportunity': '💎',
    'complete': '✅',
}
STAGE_PROGRESS = {
    'start': 10,
    'exchange': 25,
    'markets': 45,
    'fees': 60,
    'candidates': 80,
    'opportunity': 90,
    'complete': 100,
}


def _progress_bar(percent):
    filled = max(0, min(10, int(percent / 10)))
    return '█' * filled + '░' * (10 - filled)


def _window(stage, selected, states, comparisons=0, opportunities=0, best=None, detail='', frame=0):
    icon = STAGE_ICONS.get(stage, '⚡')
    percent = STAGE_PROGRESS.get(stage, 50)
    spinner = SPINNER[frame % len(SPINNER)]
    lines = [
        '⚡ <b>CRYPTO ARBITRAGE SCANNER</b>',
        f'<code>{DIVIDER}</code>',
        '',
        f'{icon} <b>{html.escape(stage)}</b> {spinner}',
        f'[{_progress_bar(percent)}] <b>{percent}%</b>',
        '',
        f'🏦 <b>Exchanges:</b> {selected}',
    ]
    for name, status in states.items():
        icon = '🟢' if status == 'healthy' else '🔴' if status == 'failed' else '🟡'
        lines.append(f'{icon} {html.escape(name.title())} · {html.escape(status)}')
    lines += [
        '',
        f'🔄 <b>Comparisons:</b> {comparisons:,}',
        f'🔥 <b>Opportunities:</b> {opportunities:,}',
    ]
    if best:
        lines += ['', f'💎 <b>Best so far:</b> {html.escape(best["symbol"])} +{best["gap"]:.3f}%']
        lines.append(f'   🟢 {html.escape(best["buy"])} → 🔴 {html.escape(best["sell"])}')
    if detail:
        lines += ['', f'ℹ️ {html.escape(detail)}']
    return '\n'.join(lines)


async def live_scan_callback(update, context):
    q = update.callback_query
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    await q.answer()
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await q.edit_message_text(
            '🔐 <b>VIP ACCESS REQUIRED</b>\n\nEnter your VIP key to activate access.',
            parse_mode='HTML',
            reply_markup=kb([[('🔑 Enter VIP Key', 'vip:enter')]]),
        )
        return
    row = await svc.get_user(uid)
    selected = []
    try:
        import json
        selected = json.loads(row['exchanges'] or '[]')
    except Exception:
        pass
    states = {n: 'waiting' for n in selected}
    frame = 0
    await q.edit_message_text(_window('start', len(selected), states, frame=frame), parse_mode='HTML')
    last_text = ''

    async def progress(stage, data):
        nonlocal last_text, frame
        frame += 1
        if stage == 'exchange':
            states[data['exchange']] = data['status']
        labels = {
            'start': 'Connecting to exchanges…',
            'exchange': 'Exchange response received',
            'markets': 'Markets loaded · building comparison set…',
            'fees': 'Fee data loaded · evaluating gaps…',
            'candidates': 'Evaluating price gaps before network validation…',
            'opportunity': 'Live opportunity found',
            'complete': 'Scan complete',
        }
        label = labels.get(stage, stage.replace('_', ' ').title())
        best = None
        if stage == 'opportunity':
            best = {
                'symbol': data['symbol'],
                'gap': data['gap'],
                'buy': data['buy'],
                'sell': data['sell'],
            }
        text = _window(
            label,
            len(selected),
            states,
            data.get('comparisons', 0),
            data.get('count', data.get('opportunities', 0)),
            best,
            data.get('message', ''),
            frame,
        )
        if text == last_text:
            return
        last_text = text
        try:
            await q.edit_message_text(text, parse_mode='HTML')
        except Exception:
            pass

    try:
        snap = await svc.run_scan(uid, progress=progress)
        p = snap.to_dict()
        scan_id = p['scan_id']
        await q.edit_message_text(
            scan_status(p),
            parse_mode='HTML',
            reply_markup=kb([
                [('🔥 Best Results', f'page:{scan_id}:0:best'), ('📋 All Results', f'page:{scan_id}:0:all')],
                [('📡 Diagnostics', f'rdiag:{scan_id}:0:all'), ('🔎 Debug Coin', f'rdebug:{scan_id}:0:all')],
                [('🧠 AI Analysis', f'raian:{scan_id}:0:all'), ('🔄 Scan Again', 'scan')],
                [('👨‍💻 Contact Developer', 'https://t.me/firdousratherr')],
            ]),
        )
    except Exception as exc:
        await q.edit_message_text(
            f'❌ <b>SCAN FAILED</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:250])}',
            parse_mode='HTML',
            reply_markup=kb([[('🔄 Try Again', 'scan'), ('🏠 Dashboard', 'home')]]),
        )
