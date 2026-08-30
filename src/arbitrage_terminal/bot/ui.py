from html import escape
import json
def dashboard(row):
    return f"<b>╭──────────────────────────────╮</b>\n<b>│ ⚡ ARBITRAGE TERMINAL        │</b>\n<b>│                              │</b>\n<b>│ 🏦 Exchanges      {len(json.loads(row['exchanges'] or '[]'))} selected │</b>\n<b>│ 🧠 AI Mode        {escape((row['result_mode'] or 'off').upper())}          │</b>\n<b>│ 🛡️ Simulation     ON          │</b>\n<b>╰──────────────────────────────╯</b>\n\nReady to scan."
def scan_status(s):return f"{'✅' if s['state']=='success' else '⚠️' if s['state']=='partial' else '❌'} <b>SCAN {s['state'].upper()}</b>\n\n🏦 {len(s['selected_exchanges'])} selected · 🟢 {len(s['healthy_exchanges'])} healthy · 🔴 {len(s['failed_exchanges'])} failed\n📊 Markets {s['markets_discovered']:,} · Comparisons {s['candidates_evaluated']:,}\n🔥 Opportunities {s['opportunities_found']:,}\n⏱ <code>{escape(s['scan_id'])}</code>"
def card(o,rank):
    net='Unknown — fee data unavailable' if o.get('estimated_net_profit') is None else f"+{o['estimated_net_profit']:.3f}%"
    verification='⚠️ Transfer/network verification bypassed' if not o.get('metadata',{}).get('transfer_verified',False) else '✅ Transfer verified'
    return f"#{rank} <b>{escape(o['symbol'])}</b>\n\n🟢 BUY <b>{escape(o['buy_exchange'])}</b> ${o['buy_price']:,.6f}\n🔴 SELL <b>{escape(o['sell_exchange'])}</b> ${o['sell_price']:,.6f}\n\n📈 Gap <b>+{o['raw_gap']:.3f}%</b> · 💰 Net <b>{net}</b>\n💧 Liquidity ${min(o['buy_volume'],o['sell_volume']):,.0f}\n🎯 Confidence {o['confidence']:.0f}/100\n⏱ Updated {o['data_age_seconds']:.1f}s ago\n{verification}"
