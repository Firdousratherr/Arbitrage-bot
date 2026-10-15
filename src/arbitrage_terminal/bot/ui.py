from html import escape
import json


BANNER = '⚡ <b>CRYPTO ARBITRAGE SCANNER</b>'
DIVIDER = '━━━━━━━━━━━━━━━━━━━━'


def dashboard(row):
    exchanges = len(json.loads(row['exchanges'] or '[]'))
    ai_mode = escape((row['result_mode'] or 'off').upper())
    return (
        f'{BANNER}\n'
        f'<code>{DIVIDER}</code>\n\n'
        '🚀 <b>READY TO SCAN</b>\n'
        'Find cross-exchange price discrepancies with deterministic filters, '
        'validation and optional AI analysis.\n\n'
        f'🏦 <b>Exchanges</b>   {exchanges} selected\n'
        f'🧠 <b>AI Mode</b>      {ai_mode}\n'
        '🛡️ <b>Execution</b>   Read-only / simulation\n\n'
        '<i>Market data only • No automatic trades</i>\n'
        f'<code>{DIVIDER}</code>\n'
        '💡 <b>Tip:</b> Start with <b>Scan Arbitrage</b> to begin.'
    )


def scan_status(s):
    state = str(s['state']).lower()
    icon = '✅' if state == 'success' else '⚠️' if state == 'partial' else '❌'
    return (
        f'{icon} <b>SCAN {state.upper()}</b>\n'
        f'<code>{DIVIDER}</code>\n\n'
        f'🏦 <b>Exchanges</b>  {len(s["selected_exchanges"])} selected\n'
        f'🟢 <b>Healthy</b>    {len(s["healthy_exchanges"])}\n'
        f'🔴 <b>Failed</b>     {len(s["failed_exchanges"])}\n\n'
        f'📊 <b>Markets</b>     {s["markets_discovered"]:,}\n'
        f'🔄 <b>Comparisons</b> {s["candidates_evaluated"]:,}\n'
        f'🔥 <b>Opportunities</b> {s["opportunities_found"]:,}\n\n'
        f'🆔 <code>{escape(s["scan_id"])}</code>'
    )


def card(o, rank):
    metadata = o.get('metadata') or {}
    quote = escape(str(metadata.get('net_profit_quote') or o.get('symbol', 'USDT/USDT').split('/')[-1]))
    roi = metadata.get('net_profit_roi', o.get('estimated_net_profit'))
    amount = metadata.get('net_profit_amount')
    if amount is None and roi is not None and metadata.get('trade_size'):
        amount = float(roi) * float(metadata['trade_size']) / 100.0
    if roi is None:
        profit = 'Unknown — fee data unavailable'
    else:
        profit = f'+{float(amount):,.2f} {quote}' if amount is not None else f'+{float(roi):.3f}%'
    roi_text = f' · ROI <b>+{float(roi):.3f}%</b>' if roi is not None else ''
    size = metadata.get('trade_size')
    size_text = f'{float(size):,.2f} {quote}' if size else 'N/A'
    verification = '⚠️ Unverified transfer/network' if not metadata.get('transfer_verified', False) else '✅ Transfer verified'
    return (
        f'💎 <b>#{rank} {escape(o["symbol"])}</b>\n'
        f'<code>{DIVIDER}</code>\n'
        f'🟢 <b>BUY</b>  {escape(o["buy_exchange"])}  <code>${o["buy_price"]:,.6f}</code>\n'
        f'🔴 <b>SELL</b> {escape(o["sell_exchange"])}  <code>${o["sell_price"]:,.6f}</code>\n\n'
        f'📈 Gap <b>+{o["raw_gap"]:.3f}%</b> · 💰 Net <b>{profit}</b>{roi_text}\n'
        f'💵 Capital <b>{size_text}</b>\n'
        f'💧 Liquidity <b>${min(o["buy_volume"], o["sell_volume"]):,.0f}</b>\n'
        f'🎯 Confidence <b>{o["confidence"]:.0f}/100</b> · ⏱ <b>{o["data_age_seconds"]:.1f}s</b>\n'
        f'{verification}'
    )
