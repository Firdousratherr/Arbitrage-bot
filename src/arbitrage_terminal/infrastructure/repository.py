from __future__ import annotations
import json,uuid
from datetime import datetime,timezone,timedelta
import aiosqlite
from arbitrage_terminal.domain.models import ScanSnapshot
from arbitrage_terminal.domain.filters import ScanFilters
DEFAULT_FILTERS={'min_gap':.50,'min_net_profit':.20,'min_volume':10000.,'min_liquidity':1000.,'max_data_age':10.,'require_network':False,'require_fees':False,'selected_coins':[],'quote_currency':'USDT','validation_mode':'strict'}
class Repository:
    def __init__(self,path):self.path=path;self.db=None
    async def connect(self):
        import pathlib;pathlib.Path(self.path).parent.mkdir(parents=True,exist_ok=True);self.db=await aiosqlite.connect(self.path);self.db.row_factory=aiosqlite.Row
        await self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA foreign_keys=ON;CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS users(telegram_id INTEGER PRIMARY KEY,username TEXT,email TEXT,vip_status TEXT NOT NULL DEFAULT 'pending',vip_expiry TEXT,banned INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,last_active TEXT NOT NULL);CREATE TABLE IF NOT EXISTS user_exchange_config(user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,exchanges TEXT NOT NULL DEFAULT '[]');CREATE TABLE IF NOT EXISTS user_scanner_config(user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,filters TEXT NOT NULL DEFAULT '{}');CREATE TABLE IF NOT EXISTS user_ai_config(user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,result_mode TEXT NOT NULL DEFAULT 'off',preferences TEXT NOT NULL DEFAULT '{}',maintenance_preferences TEXT NOT NULL DEFAULT '{}');CREATE TABLE IF NOT EXISTS scan_snapshots(scan_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,started_at TEXT NOT NULL,completed_at TEXT,selected_exchanges TEXT NOT NULL,healthy_exchanges TEXT NOT NULL,degraded_exchanges TEXT NOT NULL,failed_exchanges TEXT NOT NULL,state TEXT NOT NULL,markets_discovered INTEGER NOT NULL,markets_validated INTEGER NOT NULL,candidates_evaluated INTEGER NOT NULL,opportunities_found INTEGER NOT NULL,payload TEXT NOT NULL);CREATE INDEX IF NOT EXISTS idx_scan_user_time ON scan_snapshots(user_id,started_at DESC);CREATE TABLE IF NOT EXISTS vip_keys(key TEXT PRIMARY KEY,created_by INTEGER,created_at TEXT NOT NULL,redeemed_by INTEGER,redeemed_at TEXT,expiry_date TEXT,status TEXT NOT NULL DEFAULT 'unused');CREATE TABLE IF NOT EXISTS ai_analyses(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,scan_id TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);''')
        cols={r['name'] for r in await (await self.db.execute('PRAGMA table_info(users)')).fetchall()}
        for col in ('created_at','last_active'):
            if col not in cols:await self.db.execute(f'ALTER TABLE users ADD COLUMN {col} TEXT')
        if 'selected_exchanges' in cols:
            for r in await (await self.db.execute('SELECT telegram_id,selected_exchanges,filters FROM users')).fetchall():
                await self.db.execute('INSERT OR IGNORE INTO user_exchange_config(user_id,exchanges) VALUES (?,?)',(r['telegram_id'],r['selected_exchanges'] or '[]'));await self.db.execute('INSERT OR IGNORE INTO user_scanner_config(user_id,filters) VALUES (?,?)',(r['telegram_id'],r['filters'] or json.dumps(DEFAULT_FILTERS)))
        if 'registration_date' in cols:await self.db.execute('UPDATE users SET created_at=COALESCE(created_at,registration_date,?)',(datetime.now(timezone.utc).isoformat(),))
        await self.db.execute('UPDATE users SET last_active=COALESCE(last_active,created_at,?)',(datetime.now(timezone.utc).isoformat(),))
        if not (await (await self.db.execute('SELECT COUNT(*) c FROM schema_version')).fetchone()):await self.db.execute('INSERT INTO schema_version VALUES (1)')
        await self.db.commit()
    async def close(self):
        if self.db:await self.db.close()
    async def ensure_user(self,user_id,username,email=None):
        now=datetime.now(timezone.utc).isoformat();await self.db.execute('INSERT INTO users(telegram_id,username,email,created_at,last_active) VALUES(?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,last_active=excluded.last_active',(user_id,username,email,now,now));await self.db.execute('INSERT OR IGNORE INTO user_exchange_config(user_id) VALUES (?)',(user_id,));await self.db.execute('INSERT OR IGNORE INTO user_scanner_config(user_id,filters) VALUES (?,?)',(user_id,json.dumps(DEFAULT_FILTERS)));await self.db.execute('INSERT OR IGNORE INTO user_ai_config(user_id) VALUES (?)',(user_id,));await self.db.commit()
    async def user(self,user_id):return await (await self.db.execute('SELECT u.*,e.exchanges,c.filters,a.result_mode,a.preferences,a.maintenance_preferences FROM users u LEFT JOIN user_exchange_config e ON e.user_id=u.telegram_id LEFT JOIN user_scanner_config c ON c.user_id=u.telegram_id LEFT JOIN user_ai_config a ON a.user_id=u.telegram_id WHERE u.telegram_id=?',(user_id,))).fetchone()
    async def set_exchanges(self,user_id,exchanges):await self.db.execute('UPDATE user_exchange_config SET exchanges=? WHERE user_id=?',(json.dumps(list(dict.fromkeys(x.lower() for x in exchanges))),user_id));await self.db.commit()
    async def set_filters(self,user_id,filters):await self.db.execute('UPDATE user_scanner_config SET filters=? WHERE user_id=?',(json.dumps(filters),user_id));await self.db.commit()
    async def set_ai_mode(self,user_id,mode):await self.db.execute('UPDATE user_ai_config SET result_mode=? WHERE user_id=?',(mode,user_id));await self.db.commit()
    def filters_from_row(self,row):
        raw=json.loads(row['filters'] or '{}');return ScanFilters(**{**DEFAULT_FILTERS,**raw,'selected_coins':set(raw.get('selected_coins',[]))})
    async def save_scan(self,s):
        await self.db.execute('INSERT OR REPLACE INTO scan_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(s.scan_id,s.user_id,s.started_at,s.completed_at,json.dumps(s.selected_exchanges),json.dumps(s.healthy_exchanges),json.dumps(s.degraded_exchanges),json.dumps(s.failed_exchanges),s.state.value,s.markets_discovered,s.markets_validated,s.candidates_evaluated,s.opportunities_found,json.dumps(s.to_dict())));await self.db.commit()
    async def get_scan(self,user_id,scan_id):
        r=await (await self.db.execute('SELECT payload FROM scan_snapshots WHERE scan_id=? AND user_id=?',(scan_id,user_id))).fetchone();return json.loads(r['payload']) if r else None
    async def history(self,user_id,limit=20):return await (await self.db.execute('SELECT scan_id,started_at,state,opportunities_found FROM scan_snapshots WHERE user_id=? ORDER BY started_at DESC LIMIT ?',(user_id,limit))).fetchall()
    async def redeem_vip_key(self,user_id,key):
        key=key.strip().upper();r=await (await self.db.execute('SELECT * FROM vip_keys WHERE key=?',(key,))).fetchone()
        if not r or r['status']!='unused':return False,'That VIP key is invalid, already used, or revoked.'
        if r['expiry_date'] and r['expiry_date']<=datetime.now(timezone.utc).isoformat():return False,'That VIP key has expired.'
        await self.db.execute("UPDATE vip_keys SET status='active',redeemed_by=?,redeemed_at=? WHERE key=?",(user_id,datetime.now(timezone.utc).isoformat(),key));await self.db.execute("UPDATE users SET vip_status='active',vip_expiry=? WHERE telegram_id=?",(r['expiry_date'],user_id));await self.db.commit();return True,'VIP access activated.'
    async def create_vip_key(self,admin_id,key,days):
        expiry=None if str(days).lower() in ('lifetime','0') else (datetime.now(timezone.utc)+timedelta(days=int(days))).isoformat();await self.db.execute('INSERT INTO vip_keys(key,created_by,created_at,expiry_date) VALUES(?,?,?,?)',(key.strip().upper(),admin_id,datetime.now(timezone.utc).isoformat(),expiry));await self.db.commit();return key.strip().upper()
    async def save_ai(self,user_id,scan_id,kind,payload):await self.db.execute('INSERT INTO ai_analyses VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,user_id,scan_id,kind,json.dumps(payload),datetime.now(timezone.utc).isoformat()));await self.db.commit()
