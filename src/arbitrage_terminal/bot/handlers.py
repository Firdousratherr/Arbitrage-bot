from __future__ import annotations
import json,asyncio
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import CommandHandler,CallbackQueryHandler,MessageHandler,ContextTypes,filters
from .ui import dashboard,scan_status,card

def kb(rows):return InlineKeyboardMarkup([[InlineKeyboardButton(t,callback_data=d) for t,d in row] for row in rows])
async def start(update,context):
    svc=context.application.bot_data['service'];await svc.ensure_user(update.effective_user);row=await svc.get_user(update.effective_user.id)
    if not row['email']:context.user_data['await_email']=True;await update.effective_message.reply_text('⚡ <b>Welcome to Arbitrage Terminal</b>\n\nSend your email to continue.',parse_mode='HTML');return
    await update.effective_message.reply_text(dashboard(row),parse_mode='HTML',reply_markup=kb([[('🔎 Scan Arbitrage','scan'),('🏦 Exchanges','exchanges')],[('📊 Filters','filters'),('🧠 AI','ai')],[('📡 Status','status'),('📋 History','history')],[('⚙️ Settings','settings'),('❓ Help','help')]]))
async def text(update,context):
    if context.user_data.pop('await_email',False):
        email=update.effective_message.text.strip()
        if '@' not in email or '.' not in email:context.user_data['await_email']=True;await update.effective_message.reply_text('Please send a valid email address.');return
        await context.application.bot_data['service'].ensure_user(update.effective_user,email);row=await context.application.bot_data['service'].get_user(update.effective_user.id);await update.effective_message.reply_text('Email saved. Choose exchanges.',reply_markup=await exchange_markup(context,row))
async def exchange_markup(context,row):
    selected=set(json.loads(row['exchanges'] or '[]'));names=context.application.bot_data['exchange_names'];rows=[]
    for i in range(0,len(names),2):rows.append([(f"{'🟢' if n in selected else '⚪'} {n.title()}",f'ex:{n}') for n in names[i:i+2]])
    rows.append([('Select All','ex:all'),('Clear All','ex:none')]);rows.append([('✅ Save Selection','ex:save')]);return kb(rows)
async def exchanges(update,context):
    row=await context.application.bot_data['service'].get_user(update.effective_user.id);await update.effective_message.reply_text('🏦 <b>SELECT EXCHANGES</b>\nAll selected exchanges are equal. No priority.',parse_mode='HTML',reply_markup=await exchange_markup(context,row))
async def callbacks(update,context):
    q=update.callback_query;await q.answer();svc=context.application.bot_data['service'];uid=q.from_user.id;data=q.data
    if data=='scan':
        row=await svc.get_user(uid)
        if svc.settings.require_vip and row['vip_status']!='active':await q.answer('Active VIP access is required.',show_alert=True);return
        await q.edit_message_text('⚡ Starting scan...\n🔄 Connecting to exchanges...');await asyncio.sleep(.1);await q.edit_message_text('🔍 Scanning markets...\n📊 Comparing markets...');snap=await svc.run_scan(uid);p=snap.to_dict();context.user_data['last_scan']=p
        await q.edit_message_text(scan_status(p),parse_mode='HTML',reply_markup=kb([[('🔥 Best Results',f'page:{p["scan_id"]}:0'),('📋 All Results',f'page:{p["scan_id"]}:0')],[('📡 Diagnostics',f'diag:{p["scan_id"]}'),('🔎 Debug Coin',f'debug:{p["scan_id"]}')],[('🧠 AI Analysis',f'aian:{p["scan_id"]}'),('🔄 Scan Again','scan')]]));return
    if data.startswith('ex:'):
        row=await svc.get_user(uid);selected=set(json.loads(row['exchanges'] or '[]'));name=data[3:]
        if name=='all':selected=set(context.application.bot_data['exchange_names'])
        elif name=='none':selected=set()
        elif name=='save':
            if len(selected)<2:await q.answer('Select at least two exchanges.',show_alert=True);return
            await svc.set_exchanges(uid,list(selected));await q.edit_message_text('✅ Exchange selection saved.');return
        else:selected.symmetric_difference_update({name})
        await svc.set_exchanges(uid,list(selected));await q.edit_message_reply_markup(await exchange_markup(context,await svc.get_user(uid)));return
    if data=='help':await q.edit_message_text('<b>COMMANDS</b>\n/scan /results /exchanges /filters /settings /status /diagnostics /ai /aiprobe /aiprobestatus /aiprobelogs /aiproberepair',parse_mode='HTML');return
    if data=='filters':
        f=svc.repo.filters_from_row(await svc.get_user(uid));await q.edit_message_text(f'📊 <b>SCAN FILTERS</b>\n\n📈 Gap ≥ {f.min_gap:.2f}%\n💰 Net ≥ {f.min_net_profit:.2f}%\n💧 Volume ≥ ${f.min_volume:,.0f}\n⏱ Max age {f.max_data_age:.1f}s',parse_mode='HTML');return
    if data=='settings':await q.edit_message_text('⚙️ <b>SETTINGS</b>\n\nUse Exchanges, Filters and AI screens to change configuration.',parse_mode='HTML');return
    if data.startswith('page:'):
        _,scan_id,page=data.split(':');p=await svc.scan(uid,scan_id);page=int(page);size=5;items=p['opportunities'];total=max(1,(len(items)+size-1)//size);page=min(page,total-1);text=f'⚡ <b>ALL RESULTS</b> · Page {page+1}/{total}\n\n'+('\\n\\n'.join(card(o,page*size+i+1) for i,o in enumerate(items[page*size:(page+1)*size])) if items else '🔍 <b>NO RESULTS</b>\n\nNo opportunities match your filters.');nav=[]
        if page:nav.append(('⬅️ Previous',f'page:{scan_id}:{page-1}'))
        if page<total-1:nav.append(('Next ➡️',f'page:{scan_id}:{page+1}'))
        await q.edit_message_text(text,parse_mode='HTML',reply_markup=kb([nav,[('🏠 Dashboard','home'),('🔄 Scan Again','scan')]]));return
    if data.startswith('diag:'):
        p=await svc.scan(uid,data.split(':',1)[1]);lines=['📡 <b>SCAN DIAGNOSTICS</b>']+[f"{'🟢' if d['status']=='ok' else '🔴'} {d['exchange']} · {d['status']} · {d['latency_ms']:.0f}ms" for d in p['diagnostics']];await q.edit_message_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb([[('⬅️ Back',f'page:{p["scan_id"]}:0')]]));return
    if data.startswith('debug:'):
        p=await svc.scan(uid,data.split(':',1)[1]);sym=next((o['symbol'] for o in p['opportunities']),'BTC/USDT');lines=[f'🔎 <b>{sym} ANALYSIS</b>']+[f"{r['buy']} → {r['sell']} · {r['reason']}" for r in p.get('filter_rejections',[]) if r['symbol']==sym][:10]+[f"{o['buy_exchange']} ✓ ${o['buy_price']:,.6f} → {o['sell_exchange']} ✓ ${o['sell_price']:,.6f} · +{o['raw_gap']:.3f}%" for o in p['opportunities'] if o['symbol']==sym][:10];await q.edit_message_text('\n'.join(lines),parse_mode='HTML',reply_markup=kb([[('⬅️ Back',f'page:{p["scan_id"]}:0')]]));return
    if data.startswith('aian:'):
        p=await svc.scan(uid,data.split(':',1)[1]);r=await svc.ai_scan_analysis(uid,p);txt=r['text'] if r and 'text' in r else (r['error'] if r and 'error' in r else 'AI is OFF or unavailable. Deterministic scan completed normally.');await q.edit_message_text('🧠 <b>AI SCAN ANALYSIS</b>\n\n'+txt,parse_mode='HTML',reply_markup=kb([[('⬅️ Back',f'page:{p["scan_id"]}:0')]]));return
    if data=='ai':await ai_cmd(update,context);return
    if data.startswith('aim:'):await svc.set_ai_mode(uid,data.split(':',1)[1]);await q.edit_message_text('✅ AI result mode saved.');return
    if data=='status':
        row=await svc.get_user(uid);await q.edit_message_text('📡 <b>EXCHANGE STATUS</b>\n\n'+'\n'.join(('🟢' if n in context.application.bot_data['exchanges'] else '🔴')+' '+n.title() for n in json.loads(row['exchanges'] or '[]')),parse_mode='HTML');return
    if data=='history':
        rows=await svc.history(uid);await q.edit_message_text('📋 <b>SCAN HISTORY</b>\n\n'+'\n'.join(f"{r['started_at'][:16]} · {r['opportunities_found']} opportunities · {r['state']}" for r in rows) or 'No scans yet.',parse_mode='HTML');return
    if data=='home':await start(update,context)
async def scan(update,context):
    row=await context.application.bot_data['service'].get_user(update.effective_user.id)
    if context.application.bot_data['settings'].require_vip and row['vip_status']!='active':await update.effective_message.reply_text('🔒 Active VIP access is required.');return
    await update.effective_message.reply_text('Use the dashboard Scan button.',reply_markup=kb([[('🔎 Scan Arbitrage','scan')]]))
async def status_cmd(update,context):await start(update,context)
async def results_cmd(update,context):
    rows=await context.application.bot_data['service'].history(update.effective_user.id)
    if not rows:await update.effective_message.reply_text('📋 No scan history yet. Use /scan.');return
    await update.effective_message.reply_text('📋 <b>RECENT SCANS</b>',parse_mode='HTML',reply_markup=kb([[(f"{r['started_at'][:16]} · {r['opportunities_found']}",f"page:{r['scan_id']}:0")] for r in rows]))
async def ai_cmd(update,context):
    row=await context.application.bot_data['service'].get_user(update.effective_user.id);await update.effective_message.reply_text(f"🧠 <b>AI RESULT MODE</b>\nCurrent: {row['result_mode'].upper()}",parse_mode='HTML',reply_markup=kb([[('🟢 OFF','aim:off'),('🟡 ASSIST','aim:assist')],[('🔵 ENHANCED','aim:enhanced')]]))
async def aiprobe(update,context):
    if update.effective_user.id not in context.application.bot_data['settings'].admin_ids:await update.effective_message.reply_text('Not authorized.');return
    await update.effective_message.reply_text('🧠 <b>AI PROBE</b>\n\n'+json.dumps(await context.application.bot_data['ai'].probe(),indent=2)[:3500],parse_mode='HTML')
def build_handlers():return [CommandHandler('start',start),CommandHandler('scan',scan),CommandHandler('results',results_cmd),CommandHandler('exchanges',exchanges),CommandHandler('status',status_cmd),CommandHandler('ai',ai_cmd),CommandHandler('aiprobe',aiprobe),CommandHandler('aiprobestatus',aiprobe),CommandHandler('aiprobelogs',aiprobe),CommandHandler('aiproberepair',aiprobe),CommandHandler('diagnostics',status_cmd),CommandHandler('filters',start),CommandHandler('settings',start),CommandHandler('help',start),CallbackQueryHandler(callbacks),MessageHandler(filters.TEXT & ~filters.COMMAND,text)]
