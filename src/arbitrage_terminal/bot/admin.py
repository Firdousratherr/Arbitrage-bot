from __future__ import annotations

import html
import json
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _is_admin(update, context):
    return update.effective_user and update.effective_user.id in context.application.bot_data['settings'].admin_ids


def _menu():
    return kb([
        [('👥 Users', 'admin:users'), ('📊 Stats', 'admin:stats')],
        [('🕒 Recent Actions', 'admin:actions'), ('🔑 VIP Keys', 'admin:keys')],
        [('🔎 User Info Help', 'admin:userhelp')],
        [('🏠 Dashboard', 'home')],
    ])


async def init_admin_storage(repo):
    await repo.db.executescript('''
    CREATE TABLE IF NOT EXISTS user_activity(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_user_activity_time ON user_activity(user_id, created_at DESC);
    CREATE TRIGGER IF NOT EXISTS trg_activity_scan AFTER INSERT ON scan_snapshots
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.user_id,'scan',NEW.state || ' · ' || NEW.opportunities_found || ' opportunities',NEW.started_at); END;
    CREATE TRIGGER IF NOT EXISTS trg_activity_ai AFTER INSERT ON ai_analyses
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.user_id,'ai_analysis',COALESCE(NEW.kind,'analysis'),NEW.created_at); END;
    CREATE TRIGGER IF NOT EXISTS trg_activity_filters AFTER UPDATE OF filters ON user_scanner_config
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.user_id,'filters_updated','scanner filters changed',datetime('now')); END;
    CREATE TRIGGER IF NOT EXISTS trg_activity_exchanges AFTER UPDATE OF exchanges ON user_exchange_config
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.user_id,'exchanges_updated','exchange selection changed',datetime('now')); END;
    CREATE TRIGGER IF NOT EXISTS trg_activity_ai_mode AFTER UPDATE OF result_mode ON user_ai_config
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.user_id,'ai_mode','mode=' || NEW.result_mode,datetime('now')); END;
    CREATE TRIGGER IF NOT EXISTS trg_activity_vip AFTER UPDATE OF vip_status,vip_expiry ON users
    BEGIN INSERT INTO user_activity(user_id,action,detail,created_at) VALUES(NEW.telegram_id,'vip_status','status=' || NEW.vip_status,datetime('now')); END;
    ''')
    await repo.db.commit()


async def _users(repo, limit=20):
    return await (await repo.db.execute('SELECT telegram_id,username,vip_status,vip_expiry,banned,created_at,last_active FROM users ORDER BY last_active DESC LIMIT ?', (limit,))).fetchall()


async def _actions(repo, user_id=None, limit=20):
    if user_id is None:
        return await (await repo.db.execute('SELECT a.*,u.username FROM user_activity a LEFT JOIN users u ON u.telegram_id=a.user_id ORDER BY a.created_at DESC LIMIT ?', (limit,))).fetchall()
    return await (await repo.db.execute('SELECT a.*,u.username FROM user_activity a LEFT JOIN users u ON u.telegram_id=a.user_id WHERE a.user_id=? ORDER BY a.created_at DESC LIMIT ?', (user_id,limit))).fetchall()


def _fmt_time(value):
    if not value:
        return '—'
    return str(value).replace('T',' ')[:19]


def _user_text(row):
    exchanges = []
    try:
        exchanges = json.loads(row['exchanges'] or '[]')
    except Exception:
        pass
    coins = 'All'
    try:
        raw = json.loads(row['filters'] or '{}')
        selected = raw.get('selected_coins') or []
        coins = ', '.join(selected) if selected else 'All'
    except Exception:
        pass
    return (
        '👤 <b>USER INFO</b>\n\n'
        f'🆔 ID: <code>{row["telegram_id"]}</code>\n'
        f'👨‍💻 Username: <b>{html.escape(row["username"] or "—")}</b>\n'
        f'📧 Email: <b>{html.escape(row["email"] or "—")}</b>\n'
        f'🔐 VIP: <b>{html.escape(row["vip_status"].upper())}</b>\n'
        f'⏳ VIP expiry: <b>{_fmt_time(row["vip_expiry"])}</b>\n'
        f'🚫 Banned: <b>{"YES" if row["banned"] else "NO"}</b>\n'
        f'📅 Joined: <b>{_fmt_time(row["created_at"])}</b>\n'
        f'🕒 Last active: <b>{_fmt_time(row["last_active"])}</b>\n'
        f'🏦 Exchanges: <b>{html.escape(", ".join(exchanges) if exchanges else "None")}</b>\n'
        f'🪙 Coins: <b>{html.escape(coins)}</b>\n'
        f'🧠 AI mode: <b>{html.escape((row["result_mode"] or "off").upper())}</b>'
    )


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.')
        return
    await init_admin_storage(context.application.bot_data['repo'])
    await update.effective_message.reply_text('🛠️ <b>ADMIN CONTROL PANEL</b>\n\nManage users, VIP access, activity, keys and runtime statistics.', parse_mode='HTML', reply_markup=_menu())


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    rows = await _users(context.application.bot_data['repo'], 20)
    lines = ['👥 <b>USERS · LAST 20 ACTIVE</b>', '']
    for r in rows:
        vip = '🟢' if r['vip_status'] == 'active' else '⚪'
        ban = ' 🚫' if r['banned'] else ''
        name = html.escape('@'+r['username'] if r['username'] else 'no_username')
        lines.append(f'{vip} <code>{r["telegram_id"]}</code> {name}{ban} · {r["vip_status"]}')
    if not rows: lines.append('No users yet.')
    await update.effective_message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=_menu())


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    if not context.args or not context.args[0].lstrip('-').isdigit():
        await update.effective_message.reply_text('Usage: /userinfo USER_ID'); return
    uid = int(context.args[0]); repo = context.application.bot_data['repo']; row = await repo.user(uid)
    if not row:
        await update.effective_message.reply_text('⚠️ User not found.'); return
    actions = await _actions(repo, uid, 20)
    text = _user_text(row) + '\n\n🕒 <b>LAST 20 ACTIONS</b>\n'
    text += '\n'.join(f'• {_fmt_time(a["created_at"])} · {html.escape(a["action"])} · {html.escape(a["detail"] or "")}' for a in actions) or 'No recorded actions.'
    await update.effective_message.reply_text(text, parse_mode='HTML', reply_markup=_menu())


async def givevip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    if len(context.args) != 2 or not context.args[0].lstrip('-').isdigit():
        await update.effective_message.reply_text('Usage: /givevip USER_ID DAYS|lifetime'); return
    uid = int(context.args[0]); days = context.args[1].lower(); repo = context.application.bot_data['repo']
    if not await repo.user(uid):
        await update.effective_message.reply_text('⚠️ User not found.'); return
    if days == 'lifetime': expiry = None
    else:
        try:
            n = int(days)
            if n <= 0 or n > 3650: raise ValueError
            expiry = (datetime.now(timezone.utc) + timedelta(days=n)).isoformat()
        except ValueError:
            await update.effective_message.reply_text('⚠️ DAYS must be 1-3650 or lifetime.'); return
    await repo.db.execute("UPDATE users SET vip_status='active',vip_expiry=? WHERE telegram_id=?", (expiry,uid)); await repo.db.commit()
    await update.effective_message.reply_text(f'✅ VIP granted to <code>{uid}</code> · <b>{"lifetime" if expiry is None else days+" days"}</b>', parse_mode='HTML')


async def revokevip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    if not context.args or not context.args[0].lstrip('-').isdigit():
        await update.effective_message.reply_text('Usage: /revokevip USER_ID'); return
    uid = int(context.args[0]); repo = context.application.bot_data['repo']
    cur = await repo.db.execute("UPDATE users SET vip_status='revoked',vip_expiry=NULL WHERE telegram_id=?", (uid,)); await repo.db.commit()
    await update.effective_message.reply_text('✅ VIP revoked.' if cur.rowcount else '⚠️ User not found.')


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    if not context.args or not context.args[0].lstrip('-').isdigit():
        await update.effective_message.reply_text('Usage: /ban USER_ID'); return
    uid = int(context.args[0]); repo = context.application.bot_data['repo']; cur = await repo.db.execute('UPDATE users SET banned=1 WHERE telegram_id=?',(uid,)); await repo.db.commit()
    await update.effective_message.reply_text('✅ User banned.' if cur.rowcount else '⚠️ User not found.')


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    if not context.args or not context.args[0].lstrip('-').isdigit():
        await update.effective_message.reply_text('Usage: /unban USER_ID'); return
    uid = int(context.args[0]); repo = context.application.bot_data['repo']; cur = await repo.db.execute('UPDATE users SET banned=0 WHERE telegram_id=?',(uid,)); await repo.db.commit()
    await update.effective_message.reply_text('✅ User unbanned.' if cur.rowcount else '⚠️ User not found.')


async def useractions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    repo = context.application.bot_data['repo']; uid = int(context.args[0]) if context.args and context.args[0].lstrip('-').isdigit() else None
    rows = await _actions(repo, uid, 20)
    title = f'USER {uid}' if uid is not None else 'GLOBAL'
    lines = [f'🕒 <b>LAST 20 ACTIONS · {title}</b>', '']
    for a in rows:
        name = html.escape('@'+a['username']) if a['username'] else str(a['user_id'])
        lines.append(f'• {_fmt_time(a["created_at"])} · <code>{a["user_id"]}</code> {name} · {html.escape(a["action"])} · {html.escape(a["detail"] or "")}')
    await update.effective_message.reply_text('\n'.join(lines) if rows else 'No recorded actions.', parse_mode='HTML', reply_markup=_menu())


async def vipkeys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    rows = await (await context.application.bot_data['repo'].db.execute('SELECT key,status,created_at,redeemed_by,redeemed_at,expiry_date FROM vip_keys ORDER BY created_at DESC LIMIT 20')).fetchall()
    lines=['🔑 <b>LAST 20 VIP KEYS</b>','']
    for r in rows:
        key = html.escape(r['key']); exp = 'lifetime' if not r['expiry_date'] else _fmt_time(r['expiry_date'])
        lines.append(f'• <code>{key}</code> · {r["status"]} · exp {exp} · user {r["redeemed_by"] or "—"}')
    await update.effective_message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=_menu())


async def adminstats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update, context):
        await update.effective_message.reply_text('⛔ Admin access only.'); return
    repo=context.application.bot_data['repo']
    total=(await (await repo.db.execute('SELECT COUNT(*) c FROM users')).fetchone())['c']
    vip=(await (await repo.db.execute("SELECT COUNT(*) c FROM users WHERE vip_status='active'")).fetchone())['c']
    banned=(await (await repo.db.execute('SELECT COUNT(*) c FROM users WHERE banned=1')).fetchone())['c']
    scans=(await (await repo.db.execute('SELECT COUNT(*) c FROM scan_snapshots')).fetchone())['c']
    opp=(await (await repo.db.execute('SELECT COALESCE(SUM(opportunities_found),0) c FROM scan_snapshots')).fetchone())['c']
    keys=(await (await repo.db.execute("SELECT COUNT(*) c FROM vip_keys WHERE status='unused'")).fetchone())['c']
    await update.effective_message.reply_text(f'📊 <b>ADMIN STATS</b>\n\n👥 Users: <b>{total}</b>\n🟢 Active VIP: <b>{vip}</b>\n🚫 Banned: <b>{banned}</b>\n🔎 Total scans: <b>{scans}</b>\n🔥 Opportunities found: <b>{opp}</b>\n🔑 Unused VIP keys: <b>{keys}</b>',parse_mode='HTML',reply_markup=_menu())


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if not _is_admin(update,context): await q.answer('Admin access only.',show_alert=True); return
    await q.answer(); data=q.data; repo=context.application.bot_data['repo']; await init_admin_storage(repo)
    if data=='admin:users':
        rows=await _users(repo,20); lines=['👥 <b>USERS · LAST 20 ACTIVE</b>','']+[f'{"🟢" if r["vip_status"]=="active" else "⚪"} <code>{r["telegram_id"]}</code> {html.escape("@"+r["username"] if r["username"] else "no_username")} · {r["vip_status"]}{" · 🚫" if r["banned"] else ""}' for r in rows]; await q.edit_message_text('\n'.join(lines) if rows else 'No users yet.',parse_mode='HTML',reply_markup=_menu())
    elif data=='admin:stats':
        total=(await (await repo.db.execute('SELECT COUNT(*) c FROM users')).fetchone())['c']; vip=(await (await repo.db.execute("SELECT COUNT(*) c FROM users WHERE vip_status='active'")).fetchone())['c']; scans=(await (await repo.db.execute('SELECT COUNT(*) c FROM scan_snapshots')).fetchone())['c']; await q.edit_message_text(f'📊 <b>ADMIN STATS</b>\n\n👥 Users: <b>{total}</b>\n🟢 Active VIP: <b>{vip}</b>\n🔎 Scans: <b>{scans}</b>',parse_mode='HTML',reply_markup=_menu())
    elif data=='admin:actions':
        rows=await _actions(repo,None,20); lines=['🕒 <b>LAST 20 ACTIONS</b>','']+[f'• {_fmt_time(a["created_at"])} · <code>{a["user_id"]}</code> · {html.escape(a["action"])} · {html.escape(a["detail"] or "")}' for a in rows]; await q.edit_message_text('\n'.join(lines) if rows else 'No recorded actions.',parse_mode='HTML',reply_markup=_menu())
    elif data=='admin:keys':
        rows=await (await repo.db.execute('SELECT key,status,expiry_date,redeemed_by FROM vip_keys ORDER BY created_at DESC LIMIT 20')).fetchall(); lines=['🔑 <b>LAST 20 VIP KEYS</b>','']+[f'• <code>{html.escape(r["key"])}</code> · {r["status"]} · {"lifetime" if not r["expiry_date"] else _fmt_time(r["expiry_date"])} · {r["redeemed_by"] or "—"}' for r in rows]; await q.edit_message_text('\n'.join(lines) if rows else 'No VIP keys.',parse_mode='HTML',reply_markup=_menu())
    elif data=='admin:userhelp':
        await q.edit_message_text('🔎 <b>USER MANAGEMENT</b>\n\n<code>/userinfo USER_ID</code>\n<code>/givevip USER_ID 30</code>\n<code>/givevip USER_ID lifetime</code>\n<code>/revokevip USER_ID</code>\n<code>/ban USER_ID</code>\n<code>/unban USER_ID</code>\n<code>/useractions USER_ID</code>\n\nUse <code>/users</code> for the latest 20 active users.',parse_mode='HTML',reply_markup=_menu())
