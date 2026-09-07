from __future__ import annotations

from collections import Counter
import html
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from .handlers import kb, card


def _rejection_summary(items):
    counts = Counter(str(x.get('reason', 'unknown')).split(' ')[0] for x in items)
    labels = {
        'gap': 'Gap', 'net': 'Net profit', 'volume': 'Volume', 'liquidity': 'Liquidity',
        'data': 'Data age', 'coin': 'Coin', 'quote': 'Quote', 'deposit/withdrawal': 'Network',
        'contract/address': 'Contract', 'fee': 'Fees'
    }
    rows=[]
    for key,count in counts.most_common(8):
        rows.append(f'• {html.escape(labels.get(key, key.title()))}: <b>{count:,}</b>')
    return '\n'.join(rows)


async def results_page_callback(update, context):
    q=update.callback_query
    await q.answer()
    svc=context.application.bot_data['service'];uid=q.from_user.id
    try:
        _,scan_id,page_raw=q.data.split(':',2);page=max(0,int(page_raw));p=await svc.scan(uid,scan_id)
        if not p:
            await q.edit_message_text('⚠️ Scan snapshot not found.',reply_markup=kb([[('🏠 Dashboard','home'),('🔄 Scan Again','scan')]]));return
        items=p.get('opportunities',[]);size=5;total=max(1,(len(items)+size-1)//size);page=min(page,total-1)
        current=items[page*size:(page+1)*size]
        if current:
            txt=f'⚡ <b>ALL RESULTS</b> · Page {page+1}/{total}\n\n'+'\n\n'.join(card(o,page*size+i+1) for i,o in enumerate(current))
        else:
            rejected=p.get('filter_rejections',[])
            txt=(f'🔍 <b>NO OPPORTUNITIES FOUND</b>\n\n'
                 f'🏦 {len(p.get("healthy_exchanges",[]))} healthy · {len(p.get("failed_exchanges",[]))} failed\n'
                 f'📊 Markets: <b>{p.get("markets_discovered",0):,}</b>\n'
                 f'🔄 Comparisons: <b>{p.get("candidates_evaluated",0):,}</b>\n'
                 f'🔥 Opportunities: <b>0</b>\n'
                 f'🛡️ Validation: <b>{html.escape(str(context.application.bot_data["service"].repo.filters_from_row(await svc.get_user(uid)).validation_mode).upper())}</b>')
            if rejected:
                txt += f'\n\n<b>Why candidates were rejected</b>\n{_rejection_summary(rejected)}'
            if p.get('warnings'):
                txt += '\n\n⚠️ ' + '\n⚠️ '.join(html.escape(str(x)) for x in p['warnings'][:3])
            txt += '\n\nUse Diagnostics/Debug to inspect the scan, or switch to Loose validation if strict network checks are filtering every route.'
        nav=[]
        if page:nav.append(('⬅️ Previous',f'page:{scan_id}:{page-1}'))
        if page<total-1:nav.append(('Next ➡️',f'page:{scan_id}:{page+1}'))
        buttons=[]
        if not items:
            buttons.append([('📡 Diagnostics',f'diag:{scan_id}'),('🔎 Debug Coin',f'debug:{scan_id}')])
            buttons.append([('🔓 Try Loose Validation','val:loose')])
        buttons.append(nav)
        buttons.append([('🏠 Dashboard','home'),('🔄 Scan Again','scan')])
        await q.edit_message_text(txt,parse_mode='HTML',reply_markup=kb(buttons))
    except Exception as exc:
        await q.edit_message_text(f'⚠️ <b>Results error</b>\n\n{type(exc).__name__}: {html.escape(str(exc)[:220])}',parse_mode='HTML',reply_markup=kb([[('🏠 Dashboard','home'),('🔄 Scan Again','scan')]]))
