from __future__ import annotations

from collections import Counter
import html
from telegram.error import BadRequest
from .handlers import kb, card

PAGE_SIZE = 5


def _rejection_summary(items):
    counts = Counter(str(x.get('reason', 'unknown')).split(' ')[0] for x in items)
    labels = {
        'gap': 'Gap', 'net': 'Net profit', 'volume': 'Volume', 'liquidity': 'Liquidity',
        'data': 'Data age', 'coin': 'Coin', 'quote': 'Quote', 'deposit/withdrawal': 'Network',
        'contract/address': 'Contract', 'fee': 'Fees'
    }
    return '\n'.join(
        f'• {html.escape(labels.get(key, key.title()))}: <b>{count:,}</b>'
        for key, count in counts.most_common(8)
    )


def _items_for_mode(items, mode):
    if mode == 'best':
        return sorted(items, key=lambda o: (float(o.get('estimated_net_profit') or 0), float(o.get('raw_gap') or 0)), reverse=True)
    return list(items)


def _page_markup(scan_id, page, total, mode, items):
    nav = []
    if page > 0:
        nav.append(('⬅️ Previous', f'page:{scan_id}:{page - 1}:{mode}'))
    if page < total - 1:
        nav.append(('Next ➡️', f'page:{scan_id}:{page + 1}:{mode}'))
    rows = []
    start = page * PAGE_SIZE
    for i, _ in enumerate(items):
        index = start + i
        rows.append([(f'📖 Orders #{index + 1}', f'rorder:{scan_id}:{index}:{page}:{mode}')])
    if nav:
        rows.append(nav)
    rows.append([('🔥 Best', f'page:{scan_id}:0:best'), ('📋 All', f'page:{scan_id}:0:all')])
    rows.append([('📡 Diagnostics', f'rdiag:{scan_id}:{page}:{mode}'), ('🔎 Debug Coin', f'rdebug:{scan_id}:{page}:{mode}')])
    rows.append([('🧠 AI Analysis', f'raian:{scan_id}:{page}:{mode}'), ('🏠 Dashboard', 'home')])
    return kb(rows)


def _empty_text(p, validation_mode):
    rejected = p.get('filter_rejections', [])
    txt = (
        '🔍 <b>NO OPPORTUNITIES FOUND</b>\n\n'
        f'🏦 {len(p.get("healthy_exchanges", []))} healthy · {len(p.get("failed_exchanges", []))} failed\n'
        f'📊 Markets: <b>{p.get("markets_discovered", 0):,}</b>\n'
        f'🔄 Comparisons: <b>{p.get("candidates_evaluated", 0):,}</b>\n'
        '🔥 Opportunities: <b>0</b>\n'
        f'🛡️ Validation: <b>{html.escape(str(validation_mode).upper())}</b>'
    )
    if rejected:
        txt += f'\n\n<b>Why candidates were rejected</b>\n{_rejection_summary(rejected)}'
    if p.get('warnings'):
        txt += '\n\n⚠️ ' + '\n⚠️ '.join(html.escape(str(x)) for x in p['warnings'][:3])
    return txt


async def _safe_edit(q, text, **kwargs):
    """Edit a Telegram message, silently treating an identical edit as success."""
    try:
        return await q.edit_message_text(text, **kwargs)
    except BadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            return None
        raise


async def results_page_callback(update, context):
    q = update.callback_query
    await q.answer()
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    try:
        parts = q.data.split(':')
        if len(parts) == 3:
            _, scan_id, page_raw = parts
            page = max(0, int(page_raw))
            mode = 'all'
        elif len(parts) == 4:
            _, scan_id, page_raw, mode = parts
            page = max(0, int(page_raw))
            mode = mode if mode in {'best', 'all'} else 'all'
        else:
            raise ValueError('Invalid results callback')
        p = await svc.scan(uid, scan_id)
        if not p:
            await _safe_edit(q, '⚠️ Scan snapshot not found.', reply_markup=kb([[('🏠 Dashboard', 'home'), ('🔄 Scan Again', 'scan')]]))
            return
        raw_items = p.get('opportunities', [])
        items = _items_for_mode(raw_items, mode)
        total = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, total - 1)
        current = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if current:
            label = 'BEST RESULTS' if mode == 'best' else 'ALL RESULTS'
            txt = (
                f'⚡ <b>{label}</b> · Page <b>{page + 1}/{total}</b>\n'
                f'📊 Showing {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + len(current)} of {len(items)}\n\n'
                + '\n\n'.join(card(o, page * PAGE_SIZE + i + 1) for i, o in enumerate(current))
            )
        else:
            row = await svc.get_user(uid)
            filters = svc.repo.filters_from_row(row)
            txt = _empty_text(p, filters.validation_mode)
        await _safe_edit(q, txt, parse_mode='HTML', reply_markup=_page_markup(scan_id, page, total, mode, current))
    except Exception as exc:
        try:
            await _safe_edit(q, f'⚠️ <b>Results error</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:220])}', parse_mode='HTML', reply_markup=kb([[('🏠 Dashboard', 'home'), ('🔄 Scan Again', 'scan')]]))
        except BadRequest:
            pass


def _detail_back(scan_id, page, mode):
    return kb([[('⬅️ Back to Scan Results', f'page:{scan_id}:{page}:{mode}')], [('🏠 Dashboard', 'home')]])


def _order_text(route):
    def levels(book, key):
        if isinstance(book, Exception):
            return f'⚠️ {type(book).__name__}'
        vals = (book or {}).get(key, [])[:3]
        if not vals:
            return 'Unavailable'
        return '\n'.join(f'  ${float(x[0]):,.6f} × {float(x[1]):,.6f}' for x in vals)
    return (
        f"📖 <b>{html.escape(route['symbol'])} ORDER ROUTE</b>\n\n"
        f"🟢 <b>BUY on {html.escape(route['buy_exchange'])}</b> — required <b>ASKS</b>\n{levels(route['buy'], 'asks')}\n\n"
        f"🔴 <b>SELL on {html.escape(route['sell_exchange'])}</b> — required <b>BIDS</b>\n{levels(route['sell'], 'bids')}\n\n"
        'ℹ️ Only the order-book sides required for this arbitrage route are shown. Prices are live market data, not placed orders.'
    )


async def results_detail_callback(update, context):
    q = update.callback_query
    await q.answer()
    svc = context.application.bot_data['service']
    uid = q.from_user.id
    try:
        parts = q.data.split(':')
        kind = parts[0]
        scan_id = parts[1]
        page = max(0, int(parts[-2]))
        mode = parts[-1] if parts[-1] in {'best', 'all'} else 'all'
        p = await svc.scan(uid, scan_id)
        if not p:
            await _safe_edit(q, '⚠️ Scan snapshot not found.', reply_markup=kb([[('🏠 Dashboard', 'home')]]))
            return
        if kind == 'rorder':
            index = int(parts[2])
            route = await svc.order_route(uid, scan_id, index)
            await _safe_edit(q, _order_text(route), parse_mode='HTML', reply_markup=_detail_back(scan_id, page, mode))
            return
        if kind == 'rdiag':
            lines = ['📡 <b>SCAN DIAGNOSTICS</b>', f'🆔 <code>{html.escape(scan_id)}</code>', '']
            for d in p.get('diagnostics', []):
                icon = '🟢' if d.get('status') == 'ok' else '🔴'
                latency = d.get('latency_ms')
                latency_text = f' · {float(latency):.0f}ms' if latency is not None else ''
                lines.append(f"{icon} {html.escape(str(d.get('exchange', '?')))} · {html.escape(str(d.get('status', '?')))}{latency_text}")
            await _safe_edit(q, '\n'.join(lines), parse_mode='HTML', reply_markup=_detail_back(scan_id, page, mode))
            return
        if kind == 'rdebug':
            items = _items_for_mode(p.get('opportunities', []), mode)
            rejections = p.get('filter_rejections', [])
            symbol = next((str(o.get('symbol')) for o in items), None)
            if not symbol and rejections:
                symbol = str(rejections[0].get('symbol', 'BTC/USDT'))
            symbol = symbol or 'BTC/USDT'
            lines = [f'🔎 <b>{html.escape(symbol)} ANALYSIS</b>', '']
            matching = [r for r in rejections if r.get('symbol') == symbol][:12]
            if matching:
                lines.extend(f"{html.escape(str(r.get('buy', '?')))} → {html.escape(str(r.get('sell', '?')))} · {html.escape(str(r.get('reason', 'rejected')))}" for r in matching)
            else:
                lines.append('No rejection records were stored for this symbol.')
            await _safe_edit(q, '\n'.join(lines), parse_mode='HTML', reply_markup=_detail_back(scan_id, page, mode))
            return
        if kind == 'raian':
            r = await svc.ai_scan_analysis(uid, p)
            text = r['text'] if r and 'text' in r else (r['error'] if r and 'error' in r else 'AI is OFF or unavailable. Deterministic scan completed normally.')
            await _safe_edit(q, '🧠 <b>AI SCAN ANALYSIS</b>\n\n' + html.escape(str(text)), parse_mode='HTML', reply_markup=_detail_back(scan_id, page, mode))
            return
        raise ValueError('Unknown results action')
    except Exception as exc:
        try:
            await _safe_edit(q, f'⚠️ <b>Details unavailable</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:220])}', parse_mode='HTML', reply_markup=_detail_back(scan_id, page, mode))
        except BadRequest:
            pass
