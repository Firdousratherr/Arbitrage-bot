from __future__ import annotations

import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
from .ui import dashboard, card


def kb(rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, url=d) if str(d).startswith(('https://','tg://')) else InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows if row])


def vip_prompt(): return '🔐 <b>VIP ACCESS REQUIRED</b>\n\nEnter your VIP key to activate access.', kb([[('🔑 Enter VIP Key','vip:enter')]])


async def start(update, context):
    svc = context.application.bot_data['service']; await svc.ensure_user(update.effective_user); row = await svc.get_user(update.effective_user.id)
    if not row['email']:
        context.user_data['await_email'] = True; await update.effective_message.reply_text('⚡ <b>Welcome to Arbitrage Terminal</b>\n\nSend your email to continue.', parse_mode='HTML'); return
    if svc.settings.require_vip and not await svc.repo.vip_active(update.effective_user.id):
        txt, markup = vip_prompt(); await update.effective_message.reply_text(txt, parse_mode='HTML', reply_markup=markup); return
    await update.effective_message.reply_text(dashboard(row), parse_mode='HTML', reply_markup=kb([[('🔎 Scan Arbitrage','scan'),('🏦 Exchanges','exchanges')],[('📊 Filters','filters'),('🧠 AI','ai')],[('📡 Status','status'),('📋 History','history')],[('⚙️ Settings','settings'),('❓ Help','help')],[('👨‍💻 Contact Developer','contact:open')]]))


async def text(update, context):
    from .contact import contact_text
    if await contact_text(update, context): return
    if context.user_data.pop('await_email', False):
        email = update.effective_message.text.strip()
        if '@' not in email or '.' not in email:
            context.user_data['await_email'] = True; await update.effective_message.reply_text('Please send a valid email address.'); return
        await context.application.bot_data['service'].ensure_user(update.effective_user, email)
        row = await context.application.bot_data['service'].get_user(update.effective_user.id)
        await update.effective_message.reply_text('Email saved. Choose exchanges.', reply_markup=await exchange_markup(context, row)); return
    if context.user_data.pop('await_vip_key', False):
        key = update.effective_message.text.strip(); ok, msg = await context.application.bot_data['repo'].redeem_vip_key(update.effective_user.id, key)
        if not ok:
            context.user_data['await_vip_key'] = True; await update.effective_message.reply_text('⚠️ ' + msg); return
        row = await context.application.bot_data['service'].get_user(update.effective_user.id); await update.effective_message.reply_text('🔐 ' + msg + '\n\nChoose your exchanges.', reply_markup=await exchange_markup(context, row)); return
    if context.application.bot_data['settings'].ai_code_repair_enabled:
        from .ai_workbench import aichat_text
        await aichat_text(update, context)


async def exchange_markup(context, row):
    selected = set(json.loads(row['exchanges'] or '[]')); names = context.application.bot_data['exchange_names']; rows = []
    for i in range(0, len(names), 2): rows.append([(f"{'🟢' if n in selected else '⚪'} {n.title()}", f'ex:{n}') for n in names[i:i+2]])
    rows += [[('Select All','ex:all'),('Clear All','ex:none')], [('✅ Save Selection','ex:save')]]; return kb(rows)


async def vipkey(update, context):
    if not context.args:
        context.user_data['await_vip_key'] = True; await update.effective_message.reply_text('🔐 Send your VIP key now.'); return
    ok, msg = await context.application.bot_data['repo'].redeem_vip_key(update.effective_user.id, context.args[0])
    if ok:
        row = await context.application.bot_data['service'].get_user(update.effective_user.id); await update.effective_message.reply_text('🔐 ' + msg + '\n\nChoose your exchanges.', reply_markup=await exchange_markup(context, row))
    else: await update.effective_message.reply_text('⚠️ ' + msg)


async def genkey(update, context):
    if update.effective_user.id not in context.application.bot_data['settings'].admin_ids: await update.effective_message.reply_text('Not authorized.'); return
    if len(context.args) != 2: await update.effective_message.reply_text('Usage: /genkey KEY DAYS|lifetime'); return
    try:
        key = await context.application.bot_data['repo'].create_vip_key(update.effective_user.id, context.args[0], context.args[1]); await update.effective_message.reply_text(f'🔑 Created VIP key: <code>{key}</code>', parse_mode='HTML')
    except Exception as e: await update.effective_message.reply_text(f'⚠️ Could not create key: {type(e).__name__}')


async def scan(update, context): await update.effective_message.reply_text('Use the dashboard Scan button.', reply_markup=kb([[('🔎 Scan Arbitrage','scan')]))


async def results_cmd(update, context):
    rows = await context.application.bot_data['service'].history(update.effective_user.id)
    if not rows: await update.effective_message.reply_text('📋 No scan history yet. Use /scan.'); return
    await update.effective_message.reply_text('📋 <b>RECENT SCANS</b>', parse_mode='HTML', reply_markup=kb([[(f"{r['started_at'][:16]} · {r['opportunities_found']}", f"page:{r['scan_id']}:0:all")] for r in rows]))


async def ai_cmd(update, context):
    row = await context.application.bot_data['service'].get_user(update.effective_user.id); await update.effective_message.reply_text(f"🧠 <b>AI RESULT MODE</b>\nCurrent: {(row['result_mode'] or 'off').upper()}", parse_mode='HTML', reply_markup=kb([[('🟢 OFF','aim:off'),('🟡 ASSIST','aim:assist')],[('🔵 ENHANCED','aim:enhanced')]]))


async def aiprobe(update, context):
    if update.effective_user.id not in context.application.bot_data['settings'].admin_ids: await update.effective_message.reply_text('Not authorized.'); return
    await update.effective_message.reply_text('🧠 <b>AI PROBE</b>\n\nAI recovery diagnostics are admin-only.', parse_mode='HTML')


async def callbacks(update, context):
    q = update.callback_query; await q.answer(); data = q.data
    if data == 'vip:enter': context.user_data['await_vip_key'] = True; await q.edit_message_text('🔐 <b>VIP KEY</b>\n\nSend your VIP key as a message.', parse_mode='HTML')
    elif data == 'home': await start(update, context)
    elif data.startswith('aim:'): await context.application.bot_data['service'].set_ai_mode(q.from_user.id, data.split(':',1)[1]); await q.edit_message_text('✅ AI result mode saved.')


def build_handlers():
    return [CommandHandler('start',start), CommandHandler('vipkey',vipkey), CommandHandler('genkey',genkey), CommandHandler('scan',scan), CommandHandler('results',results_cmd), CommandHandler('ai',ai_cmd), CommandHandler('aiprobe',aiprobe), CommandHandler('aiprobestatus',aiprobe), CommandHandler('aiprobelogs',aiprobe), CommandHandler('aiproberepair',aiprobe), CallbackQueryHandler(callbacks), MessageHandler(filters.TEXT & ~filters.COMMAND, text)]
