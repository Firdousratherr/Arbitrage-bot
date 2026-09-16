from __future__ import annotations

import asyncio
import html
import json
import time

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb

USER_REPAIR_COOLDOWN = 90.0
EXCHANGE_REPAIR_COOLDOWN = 45.0
AI_REPAIR_TIMEOUT = 75.0
REPAIR_SCAN_WAIT = 2.0


def _is_admin(context, user_id):
    return user_id in context.application.bot_data['settings'].admin_ids


def _menu(context, user_id):
    rows = [
        [('🩺 Exchange Health', 'repair:health'), ('📡 Test Exchange APIs', 'repair:probe')],
        [('🔬 Deep Diagnose', 'repair:diagnose')],
        [('🤖 AI Auto Repair', 'repair:ai')],
    ]
    if _is_admin(context, user_id):
        rows += [
            [('🛠️ Fix Selected Exchanges', 'repair:run')],
            [('📊 Recovery Stats', 'repair:stats')],
            [('⚠️ Fix Unhealthy Only', 'repair:unhealthy'), ('🛠️ Fix All Loaded', 'repair:all')],
        ]
    rows.append([('⬅️ Settings', 'settings'), ('🏠 Dashboard', 'home')])
    return kb(rows)


async def repair_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id if q else update.effective_user.id
    if q:
        await q.answer()
        text = (
            '🛠️ <b>EXCHANGE TOOLS</b>\n\n'
            'Check exchange health, test public market APIs, diagnose market/ticker availability, or explicitly ask AI to repair an exchange.\n\n'
            '🤖 <b>AI Auto Repair</b> is user-triggered only. It does not run automatically during scans.\n'
            '🔒 Repair is limited to predefined safe recovery actions; credentials, secrets, source code, and raw internal errors are never exposed.'
        )
        await q.edit_message_text(text, parse_mode='HTML', reply_markup=_menu(context, uid))
        return
    text = (
        '🛠️ <b>EXCHANGE TOOLS</b>\n\n'
        'Check exchange health, test public market APIs, diagnose market/ticker availability, or explicitly ask AI to repair an exchange.\n\n'
        '🤖 AI Auto Repair is user-triggered and limited to safe recovery actions.'
    )
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=_menu(context, uid))


async def _selected(context, user_id):
    svc = context.application.bot_data['service']
    row = await svc.get_user(user_id)
    try:
        return json.loads(row['exchanges'] or '[]')
    except Exception:
        return []


def _scan_active(context):
    service = context.application.bot_data.get('service')
    locks = getattr(service, '_scan_locks', {}) if service else {}
    return any(lock.locked() for lock in locks.values())


async def _wait_for_scan_idle(context, timeout=REPAIR_SCAN_WAIT):
    deadline = time.monotonic() + timeout
    while _scan_active(context) and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
    return not _scan_active(context)


async def _verify_one(name, adapter, deep=False):
    try:
        health = getattr(adapter, 'health_snapshot', lambda: {})()
        markets = await asyncio.wait_for(adapter.get_markets(), timeout=20.0)
        symbols = [m.symbol for m in markets]
        if not symbols:
            return False, 'no markets returned'
        sample = set(symbols[:8])
        tickers = await asyncio.wait_for(adapter.get_tickers(sample), timeout=20.0)
        usable = [t for t in tickers if getattr(t, 'bid', 0) > 0 and getattr(t, 'ask', 0) > 0]
        if not usable:
            return False, f'{len(markets)} markets but no usable bid/ask prices'
        if deep:
            await asyncio.wait_for(adapter.health_check(), timeout=15.0)
        state = health.get('state', 'unknown') if isinstance(health, dict) else 'unknown'
        return True, f'{len(markets)} markets · {len(usable)} live prices · state={state}'
    except Exception as exc:
        return False, f'{type(exc).__name__}: {str(exc)[:140]}'


async def _render_results(q, title, results, footer='', public=False, context=None):
    lines = [title, '']
    for name, ok, detail in results:
        if public and not ok:
            detail = 'API test could not complete'
        lines.append(f'{"🟢" if ok else "🔴"} <b>{html.escape(str(name).title())}</b> · {html.escape(str(detail))}')
    if footer:
        lines += ['', html.escape(footer)]
    uid = q.from_user.id
    await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=_menu(context, uid))


async def _run_one_repair(name, adapter, attempts=3):
    last_error = 'verification failed'
    for attempt in range(1, attempts + 1):
        try:
            ok = await asyncio.wait_for(adapter.repair(), timeout=25.0)
            if not ok:
                last_error = 'repair did not report healthy'
                continue
            verified, detail = await _verify_one(name, adapter, deep=True)
            if verified:
                return True, f'verified · {detail}'
            last_error = detail
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {str(exc)[:120]}'
        if attempt < attempts:
            await asyncio.sleep(min(1.0, 0.25 * attempt))
    return False, f'{last_error} ({attempts} recovery attempts)'


async def repair_run(update: Update, context: ContextTypes.DEFAULT_TYPE, names=None):
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        await q.answer('Admin access required', show_alert=True)
        return
    await q.answer('Recovery started')
    if _scan_active(context) and not await _wait_for_scan_idle(context):
        await q.edit_message_text('⏳ A scan is currently running. Please run exchange repair after the scan finishes.', reply_markup=_menu(context, q.from_user.id))
        return
    selected = list(names) if names is not None else await _selected(context, q.from_user.id)
    if not selected:
        await q.edit_message_text('⚠️ No exchanges are selected.', reply_markup=kb([[('🏦 Exchanges', 'exchanges'), ('🏠 Dashboard', 'home')]]))
        return
    exchanges = context.application.bot_data['exchanges']
    locks = context.application.bot_data.setdefault('exchange_repair_locks', {})
    await q.edit_message_text('🛠️ <b>FIXING EXCHANGES…</b>\n\nRepairing clients and verifying live market data.', parse_mode='HTML')

    async def repair_one(name):
        adapter = exchanges.get(name)
        if adapter is None:
            return name, False, 'not loaded'
        lock = locks.setdefault(str(name).lower(), asyncio.Lock())
        async with lock:
            if _scan_active(context):
                return name, False, 'a scan is active; repair skipped to protect shared exchange state'
            ok, detail = await _run_one_repair(name, adapter)
            return name, ok, detail

    results = await asyncio.gather(*(repair_one(name) for name in selected))
    await _render_results(q, '🛠️ <b>EXCHANGE RECOVERY RESULT</b>', results,
                          'Authentication/invalid-request failures are never bypassed.', context=context)


async def _ai_repair_one(name, adapter, advisor, lock, context):
    async with lock:
        if _scan_active(context):
            return name, False, 'a scan is active; repair skipped to protect shared exchange state'
        verified, detail = await _verify_one(name, adapter, deep=False)
        if verified:
            return name, True, f'already healthy · {detail}'
        health = getattr(adapter, 'health_snapshot', lambda: {})()
        state = health.get('state', 'unknown') if isinstance(health, dict) else 'unknown'
        snapshot = {
            'exchange': str(name),
            'state': str(state),
            'verification_ok': False,
            'verification_detail': str(detail)[:300],
            'consecutive_failures': int(health.get('consecutive_failures', 0)) if isinstance(health, dict) else 0,
            'total_failures': int(health.get('total_failures', 0)) if isinstance(health, dict) else 0,
            'last_error_type': str(health.get('last_error_type', 'unknown'))[:80] if isinstance(health, dict) else 'unknown',
            'recent_failure': str(health.get('last_error', ''))[:300] if isinstance(health, dict) else '',
        }
        decision = await advisor.advise(snapshot)
        if decision is None:
            return name, False, 'AI could not produce a sufficiently confident safe repair decision'
        if not decision.safe_to_auto_repair:
            return name, False, f'AI recommends no automatic repair · {decision.reason[:180]}'
        action = decision.recommended_action
        if action == 'quarantine':
            return name, False, f'AI recommends quarantine; no repair performed · {decision.reason[:180]}'
        if action not in {'retry', 'repair'}:
            return name, False, f'AI selected unsupported action {action}; no repair performed'
        if decision.retry_delay_seconds:
            await asyncio.sleep(min(float(decision.retry_delay_seconds), 10.0))
        try:
            if action == 'retry':
                await asyncio.wait_for(adapter.health_check(), timeout=15.0)
            else:
                ok = await asyncio.wait_for(adapter.repair(), timeout=25.0)
                if not ok:
                    return name, False, 'AI selected repair, but deterministic repair did not report success'
        except Exception:
            return name, False, f'AI selected {action}, but the safe repair operation failed'
        final_ok, final_detail = await _verify_one(name, adapter, deep=True)
        if final_ok:
            return name, True, f'AI {action} repair verified · {final_detail}'
        return name, False, f'AI {action} completed but verification failed'


async def repair_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer('AI repair started')
    selected = await _selected(context, uid)
    exchanges = context.application.bot_data['exchanges']
    advisor = context.application.bot_data.get('manual_recovery_advisor')
    if advisor is None or not getattr(advisor, 'enabled', False):
        await q.edit_message_text('⚠️ AI Auto Repair is not available right now.', reply_markup=_menu(context, uid))
        return
    if not getattr(advisor.ai, 'configured', False):
        await q.edit_message_text('⚠️ AI provider is not configured. Use Exchange Health/API Test instead.', reply_markup=_menu(context, uid))
        return
    if not selected:
        await q.edit_message_text('⚠️ No exchanges are selected. Use Exchanges first.', reply_markup=_menu(context, uid))
        return

    now = time.monotonic()
    user_last = context.application.bot_data.setdefault('exchange_repair_user_last', {})
    exchange_last = context.application.bot_data.setdefault('exchange_repair_last', {})
    last_user = user_last.get(uid, 0.0)
    if now - last_user < USER_REPAIR_COOLDOWN:
        wait = max(1, int(USER_REPAIR_COOLDOWN - (now - last_user)))
        await q.edit_message_text(f'⏳ AI Auto Repair was used recently. Please wait about {wait}s before trying again.', reply_markup=_menu(context, uid))
        return
    if _scan_active(context) and not await _wait_for_scan_idle(context):
        await q.edit_message_text('⏳ A scan is currently running. Please wait for it to finish before using AI Auto Repair.', reply_markup=_menu(context, uid))
        return

    await q.edit_message_text(
        '🤖 <b>AI AUTO REPAIR</b>\n\nDiagnosing selected exchanges and applying only allowlisted safe recovery actions.\n\n'
        'This is an explicit repair action and is not part of normal scanning.', parse_mode='HTML')
    locks = context.application.bot_data.setdefault('exchange_repair_locks', {})
    results = []
    deadline = time.monotonic() + AI_REPAIR_TIMEOUT
    for name in selected:
        if time.monotonic() >= deadline:
            results.append((name, False, 'overall AI repair time budget exhausted'))
            continue
        adapter = exchanges.get(name)
        if adapter is None:
            results.append((name, False, 'exchange is not loaded'))
            continue
        key = str(name).lower()
        if time.monotonic() - exchange_last.get(key, 0.0) < EXCHANGE_REPAIR_COOLDOWN:
            results.append((name, False, 'exchange was repaired recently; cooldown active'))
            continue
        lock = locks.setdefault(key, asyncio.Lock())
        try:
            result = await asyncio.wait_for(_ai_repair_one(name, adapter, advisor, lock, context), max(1.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            result = (name, False, 'AI repair time budget exhausted')
        results.append(result)
        if result[1] and not result[2].startswith('already healthy'):
            exchange_last[key] = time.monotonic()
    user_last[uid] = time.monotonic()
    await _render_results(q, '🤖 <b>AI AUTO REPAIR RESULT</b>', results,
                          'AI cannot change credentials/configuration or execute arbitrary code. Authentication and invalid-request failures are never auto-repaired.',
                          public=True, context=context)


async def repair_health(update, context):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    selected = await _selected(context, uid)
    exchanges = context.application.bot_data['exchanges']
    admin = _is_admin(context, uid)
    lines = ['🩺 <b>EXCHANGE HEALTH</b>', '']
    for name in selected:
        adapter = exchanges.get(name)
        if adapter is None:
            lines.append(f'🔴 <b>{html.escape(str(name).title())}</b> · OFFLINE'); continue
        h = adapter.health_snapshot() if hasattr(adapter, 'health_snapshot') else {}
        if admin:
            lines.append(
                f'🟢 <b>{html.escape(str(name).title())}</b> · state={html.escape(str(h.get("state", "unknown")))} · '
                f'score={h.get("score", "?")} · failures={h.get("consecutive_failures", 0)} · '
                f'repairs={h.get("total_repairs", 0)} · recoveries={h.get("total_recoveries", 0)}')
            if h.get('last_error'):
                lines.append(f'   Last error: {html.escape(str(h["last_error"])[:180])}')
        else:
            state = str(h.get('state', 'unknown')).lower()
            label = 'ONLINE' if state == 'healthy' else ('DEGRADED' if state in {'degraded', 'recovering'} else 'OFFLINE')
            icon = '🟢' if label == 'ONLINE' else ('🟠' if label == 'DEGRADED' else '🔴')
            lines.append(f'{icon} <b>{html.escape(str(name).title())}</b> · {label}')
    if not selected:
        lines.append('⚠️ No exchanges selected. Use Exchanges.')
    if not admin:
        lines += ['', '🔒 Detailed recovery counters and internal errors are admin-only.']
    await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=_menu(context, uid))


async def repair_probe(update, context, deep=False):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer('Testing APIs')
    selected = await _selected(context, uid)
    exchanges = context.application.bot_data['exchanges']
    results = []
    for name in selected:
        adapter = exchanges.get(name)
        if adapter is None:
            results.append((name, False, 'not loaded')); continue
        results.append((name, *(await _verify_one(name, adapter, deep=deep))))
    title = '🔬 <b>DEEP EXCHANGE DIAGNOSIS</b>' if deep else '📡 <b>EXCHANGE API TEST</b>'
    await _render_results(q, title, results,
                          'Checks public market discovery and a bounded live bid/ask sample.', public=not _is_admin(context, uid), context=context)


async def repair_stats(update, context):
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        await q.answer('Admin access required', show_alert=True); return
    await q.answer()
    selected = await _selected(context, q.from_user.id)
    exchanges = context.application.bot_data['exchanges']
    lines = ['📊 <b>RECOVERY STATISTICS</b>', '']
    for name in selected:
        adapter = exchanges.get(name)
        h = adapter.health_snapshot() if adapter and hasattr(adapter, 'health_snapshot') else {}
        lines.append(f'• <b>{html.escape(str(name).title())}</b>: failures={h.get("total_failures", 0)}, repairs={h.get("total_repairs", 0)}, recoveries={h.get("total_recoveries", 0)}, score={h.get("score", "?")}')
    await q.edit_message_text('\n'.join(lines), parse_mode='HTML', reply_markup=_menu(context, q.from_user.id))


async def _names_by_health(context, user_id):
    selected = await _selected(context, user_id)
    exchanges = context.application.bot_data['exchanges']
    names = []
    for name in selected:
        adapter = exchanges.get(name)
        if adapter is None:
            names.append(name); continue
        h = adapter.health_snapshot() if hasattr(adapter, 'health_snapshot') else {}
        if h.get('state') not in {'healthy'} or float(h.get('score', 100)) < 70:
            names.append(name)
    return names


async def repair_callback(update, context):
    data = update.callback_query.data
    if data == 'repair:open':
        await repair_open(update, context)
    elif data == 'repair:ai':
        await repair_ai(update, context)
    elif data == 'repair:run':
        await repair_run(update, context)
    elif data == 'repair:health':
        await repair_health(update, context)
    elif data == 'repair:probe':
        await repair_probe(update, context, deep=False)
    elif data == 'repair:diagnose':
        await repair_probe(update, context, deep=True)
    elif data == 'repair:stats':
        await repair_stats(update, context)
    elif data == 'repair:unhealthy':
        if not _is_admin(context, update.callback_query.from_user.id):
            await update.callback_query.answer('Admin access required', show_alert=True); return
        names = await _names_by_health(context, update.callback_query.from_user.id)
        await repair_run(update, context, names=names)
    elif data == 'repair:all':
        if not _is_admin(context, update.callback_query.from_user.id):
            await update.callback_query.answer('Admin access required', show_alert=True); return
        await repair_run(update, context, names=list(context.application.bot_data['exchanges'].keys()))