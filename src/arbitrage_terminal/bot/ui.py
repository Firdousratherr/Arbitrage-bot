from html import escape
import json


def dashboard(row):
    return f"<b>╭──────────────────────────────╮</b>\n<b>│ ⚡ ARBITRAGE TERMINAL        │</b>\n<b>│                              │</b>\n<b>│ 🏦 Exchanges      {len(json.loads(row['exchanges'] or '[]'))} selected │</b>\n<b>│ 🧠 AI Mode        {escape((row['result_mode'] or 'off').upper())}          │</b>\n<b>│ 🛡️ Simulation     ON          │</b>\n<b>╰──────────────────────────────╯</b>\n\nReady to scan."


def scan_status(s):
    return f"{'✅' if s['state']=='success' else '⚠️' if s['state']=='partial' else '❌'} <b>SCAN {s['state'].upper()}</b>\n\n🏦 {len(s['selected_exchanges'])} selected · 🟢 {len(s['healthy_exchanges'])} healthy · 🔴 {len(s['failed_exchanges'])} failed\n📊 Markets {s['markets_discovered']:,} · Comparisons {s['candidates_evaluated']:,}\n🔥 Opportunities {s['opportunities_found']:,}\n⏱ <code>{escape(s['scan_id'])}</code>"


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
        profit = f"+{float(amount):,.2f} {quote}" if amount is not None else f"+{float(roi):.3f}%"
    roi_text = f" · ROI <b>+{float(roi):.3f}%</b>" if roi is not None else ''
    size_text = f" · Size {float(metadata['trade_size']):,.2f} {quote}" if metadata.get('trade_size') else ''
    verification = '⚠️ Transfer/network verification bypassed' if not metadata.get('transfer_verified', False) else '✅ Transfer verified'
    return f"#{rank} <b>{escape(o['symbol'])}</b>\n\n🟢 BUY <b>{escape(o['buy_exchange'])}</b> ${o['buy_price']:,.6f}\n🔴 SELL <b>{escape(o['sell_exchange'])}</b> ${o['sell_price']:,.6f}\n\n📈 Gap <b>+{o['raw_gap']:.3f}%</b> · 💰 Net <b>{profit}</b>{roi_text}\n💵 Trade <b>{size_text.strip(' ·')}</b>\n💧 Liquidity ${min(o['buy_volume'],o['sell_volume']):,.0f}\n🎯 Confidence {o['confidence']:.0f}/100\n⏱ Updated {o['data_age_seconds']:.1f}s ago\n{verification}"
