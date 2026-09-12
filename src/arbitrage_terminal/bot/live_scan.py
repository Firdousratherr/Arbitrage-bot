from __future__ import annotations

import html
import time

from .handlers import kb
from .ui import DIVIDER, scan_status


SPINNER = ('◐', '◓', '◑', '◒')


def _progress_percent(stage, data, selected):
    """Return a truthful progress value for both stage names and UI labels."""
    if stage == 'start':
        return 5
    if stage == 'exchange':
        total = max(1, int(data.get('total', selected) or selected or 1))
        completed = max(0, min(total, int(data.get('completed', 0) or 0)))
        return 10 + int(35 * completed / total)
    if stage == 'markets':
        return 50
    if stage == 'fees':
        return 65
    if stage == 'candidates':
        return 80
    if stage == 'opportunity':
        return 90
    if stage == 'complete':
        return 100
    return 50


def _progress_bar(percent):
    filled = max(0, min(10, int(percent / 10)))
    return '█' * filled + '░' * (10 - filled)


def _window(stage, selected, states, comparisons=0, opportunities=0, best=None, detail='', frame=0, percent=None, elapsed=None):
    spinner = SPINNER[frame % len(SPINNER)]
    if percent is None:
        percent = 50
    lines = [
        '⚡ <b>CRYPTO ARBITRAGE SCANNER</b>',
        f'<code>{DIVIDER}</code>',
        '',
        f'⚡ <b>{html.escape(stage)}</b> {spinner}',
        f'[{_progress_bar(percent)}] <b>{percent}%</b>',
    ]
    if elapsed is not None:
        lines.append(f'⏱ <b>Elapsed:</b> {elapsed:.0f}s')
    lines += [
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
    started = time.monotonic()
    await q.edit_message_text(_window('Starting scan', len(selected), states, frame=frame, percent=5, elapsed=0), parse_mode='HTML')
    last_text = ''
    last_edit = 0.0

    async def progress(stage, data):
        nonlocal last_text, frame, last_edit
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
        percent = _progress_percent(stage, data, len(selected))
        completed = data.get('completed')
        total = data.get('total', len(selected))
        if stage == 'exchange' and completed is not None:
            label = f'Exchange responses · {completed}/{total}'
        elif stage == 'exchange' and data.get('status') == 'loading':
            label = f'Waiting for {html.escape(str(data.get("exchange", "exchange")).title())}…'
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
            percent,
            time.monotonic() - started,
        )
        now = time.monotonic()
        if text == last_text:
            return
        # Telegram edit throttling: allow fast terminal updates but avoid hammering
        # the API when several exchanges report in rapid succession.
        if stage not in {'complete', 'opportunity'} and now - last_edit < 0.8:
            return
        last_text = text
        last_edit = now
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
