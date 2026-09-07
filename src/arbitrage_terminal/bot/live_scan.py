from __future__ import annotations
import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .handlers import kb, card
from .ui import scan_status


def _window(stage, selected, states, comparisons=0, opportunities=0, best=None, detail=''):
    lines=['⚡ <b>LIVE ARBITRAGE SCAN</b>','',f'🏦 Exchanges: <b>{selected}</b>']
    for name,status in states.items():
        icon='🟢' if status=='healthy' else '🔴' if status=='failed' else '🟡'
        lines.append(f'{icon} {html.escape(name.title())} · {status}')
    lines += ['',f'🔄 <b>{html.escape(stage)}</b>',f'📊 Comparisons: <b>{comparisons:,}</b>',f'🔥 Opportunities: <b>{opportunities:,}</b>']
    if best: lines.append(f'💎 Best: <b>{html.escape(best["symbol"])}</b> +{best["gap"]:.3f}% · {html.escape(best["buy"])} → {html.escape(best["sell"])}')
    if detail: lines += ['',f'ℹ️ {html.escape(detail)}']
    return '\n'.join(lines)

async def live_scan_callback(update, context):
    q=update.callback_query;svc=context.application.bot_data['service'];uid=q.from_user.id
    await q.answer()
    if svc.settings.require_vip and not await svc.repo.vip_active(uid):
        await q.edit_message_text('🔒 <b>Active VIP access is required.</b>',parse_mode='HTML',reply_markup=kb([[('🔑 Enter VIP Key','vip:enter')]]));return
    row=await svc.get_user(uid);selected=[]
    try:
        import json
        selected=json.loads(row['exchanges'] or '[]')
    except Exception: pass
    states={n:'waiting' for n in selected}
    await q.edit_message_text(_window('Connecting to exchanges…',len(selected),states),parse_mode='HTML')
    last_text=''
    async def progress(stage,data):
        nonlocal last_text
        if stage=='exchange':states[data['exchange']]=data['status']
        labels={'start':'Connecting to exchanges…','exchange':'Exchange response received','markets':'Markets loaded · building comparison set…','fees':'Fee data loaded · evaluating gaps…','candidates':'Evaluating price gaps before network validation…','opportunity':'Live opportunity found','complete':'Scan complete'}
        label=labels.get(stage,stage.replace('_',' ').title())
        best=None
        if stage=='opportunity':best={'symbol':data['symbol'],'gap':data['gap'],'buy':data['buy'],'sell':data['sell']}
        text=_window(label,len(selected),states,data.get('comparisons',0),data.get('count',data.get('opportunities',0)),best,data.get('message',''))
        if text==last_text:return
        last_text=text
        try:await q.edit_message_text(text,parse_mode='HTML')
        except Exception:pass
    try:
        snap=await svc.run_scan(uid,progress=progress);p=snap.to_dict()
        await q.edit_message_text(scan_status(p),parse_mode='HTML',reply_markup=kb([[('🔥 Best Results',f'page:{p["scan_id"]}:0'),('📋 All Results',f'page:{p["scan_id"]}:0')],[('📡 Diagnostics',f'diag:{p["scan_id"]}'),('🔎 Debug Coin',f'debug:{p["scan_id"]}')],[('🧠 AI Analysis',f'aian:{p["scan_id"]}'),('🔄 Scan Again','scan')]]))
    except Exception as exc:
        await q.edit_message_text(f'❌ <b>Scan failed</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:250])}',parse_mode='HTML',reply_markup=kb([[('🔄 Try Again','scan'),('🏠 Dashboard','home')]]))
