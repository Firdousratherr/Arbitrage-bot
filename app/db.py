from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

DEFAULT_FILTERS = {
    "min_profit": 0.5,
    "max_profit": 100.0,
    "min_spread": 0.0,
    "max_spread": 100.0,
    "min_volume": 10000.0,
    "min_trade_size": 10.0,
    "max_trade_size": 1000.0,
    "max_slippage": 2.0,
    "fee_adjusted": True,
    "network_fee": 0.0,
    "quote_currency": "USDT",
    "watchlist": [],
    "blacklist": [],
    "alert_cooldown": 300,
    "daily_cap": 50,
    "paused": False,
    "loose_mode": False,
    "max_exchanges": 16,
}


def now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA foreign_keys=ON;
            PRAGMA cache_size=-4000;
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY, username TEXT, email TEXT,
                selected_exchanges TEXT NOT NULL DEFAULT '[]', registration_date TEXT,
                vip_status TEXT NOT NULL DEFAULT 'pending', vip_expiry TEXT,
                vip_key_used TEXT, last_active TEXT, filters TEXT NOT NULL,
                banned INTEGER NOT NULL DEFAULT 0, ban_reason TEXT,
                leaderboard_hidden INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS vip_keys (
                key TEXT PRIMARY KEY, created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
                redeemed_by INTEGER, redeemed_at TEXT, expiry_date TEXT,
                status TEXT NOT NULL DEFAULT 'unused'
            );
            CREATE TABLE IF NOT EXISTS user_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                action TEXT NOT NULL, details TEXT, timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL,
                action TEXT NOT NULL, details TEXT, timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opportunities (
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, buy_exchange TEXT NOT NULL,
                sell_exchange TEXT NOT NULL, buy_price REAL NOT NULL, sell_price REAL NOT NULL,
                raw_spread REAL NOT NULL, net_profit REAL NOT NULL, volume_buy REAL,
                volume_sell REAL, verified INTEGER NOT NULL DEFAULT 0, loose_mode INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                opportunity_id TEXT NOT NULL, size REAL NOT NULL, profit REAL NOT NULL,
                created_at TEXT NOT NULL, period TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS exchange_overrides (
                exchange TEXT NOT NULL, currency TEXT NOT NULL, network TEXT NOT NULL,
                contract_address TEXT, deposit_enabled INTEGER, withdrawal_enabled INTEGER,
                PRIMARY KEY (exchange, currency, network)
            );
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()

    def _db(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def get_user(self, telegram_id: int) -> aiosqlite.Row | None:
        cursor = await self._db().execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return await cursor.fetchone()

    async def find_user(self, value: str) -> aiosqlite.Row | None:
        cursor = await self._db().execute("SELECT * FROM users WHERE telegram_id = ? OR lower(username) = lower(?)", (value, value.lstrip("@")))
        return await cursor.fetchone()

    async def upsert_user(self, telegram_id: int, username: str | None, email: str, exchanges: list[str]) -> None:
        timestamp = now()
        await self._db().execute(
            """INSERT INTO users (telegram_id, username, email, selected_exchanges, registration_date, last_active, filters)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET
            username=excluded.username, email=excluded.email, selected_exchanges=excluded.selected_exchanges, last_active=excluded.last_active""",
            (telegram_id, username, email, json.dumps(exchanges), timestamp, timestamp, json.dumps(DEFAULT_FILTERS)),
        )
        await self._db().commit()

    async def touch(self, user_id: int) -> None:
        await self._db().execute("UPDATE users SET last_active = ? WHERE telegram_id = ?", (now(), user_id))
        await self._db().commit()

    async def set_user(self, user_id: int, **fields: Any) -> None:
        allowed = {"selected_exchanges", "vip_status", "vip_expiry", "vip_key_used", "filters", "banned", "ban_reason", "leaderboard_hidden"}
        fields = {key: (json.dumps(value) if key in {"selected_exchanges", "filters"} else value) for key, value in fields.items() if key in allowed}
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            await self._db().execute(f"UPDATE users SET {assignments} WHERE telegram_id = ?", (*fields.values(), user_id))
            await self._db().commit()

    async def log_action(self, user_id: int, action: str, details: str = "") -> None:
        db = self._db()
        await db.execute("INSERT INTO user_actions(user_id, action, details, timestamp) VALUES (?, ?, ?, ?)", (user_id, action, details, now()))
        await db.execute("DELETE FROM user_actions WHERE user_id = ? AND id NOT IN (SELECT id FROM user_actions WHERE user_id = ? ORDER BY id DESC LIMIT 20)", (user_id, user_id))
        await db.commit()

    async def log_admin_action(self, admin_id: int, action: str, details: str = "") -> None:
        await self._db().execute("INSERT INTO admin_actions(admin_id, action, details, timestamp) VALUES (?, ?, ?, ?)", (admin_id, action, details, now()))
        await self._db().commit()

    async def create_vip_key(self, admin_id: int, duration: str) -> str:
        key = "VIP-" + "-".join(secrets.token_hex(2).upper() for _ in range(3))
        expiry = None if duration.lower() in {"lifetime", "life", "0"} else (datetime.now(UTC) + timedelta(days=int(duration))).isoformat()
        await self._db().execute("INSERT INTO vip_keys(key, created_by, created_at, expiry_date) VALUES (?, ?, ?, ?)", (key, admin_id, now(), expiry))
        await self._db().commit()
        return key

    async def list_vip_keys(self, status: str | None = None) -> list[aiosqlite.Row]:
        if status and status not in {"unused", "active", "expired", "revoked"}:
            raise ValueError("invalid key status")
        query = "SELECT * FROM vip_keys"
        args: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            args = (status,)
        cursor = await self._db().execute(query + " ORDER BY created_at DESC", args)
        return await cursor.fetchall()

    async def extend_vip(self, user_id: int, days: int) -> bool:
        if days <= 0:
            raise ValueError("days must be positive")
        user = await self.get_user(user_id)
        if not user:
            return False
        base = datetime.fromisoformat(user["vip_expiry"]) if user["vip_expiry"] else datetime.now(UTC)
        expiry = max(base, datetime.now(UTC)) + timedelta(days=days)
        await self._db().execute("UPDATE users SET vip_status='active', vip_expiry=? WHERE telegram_id=?", (expiry.isoformat(), user_id))
        await self._db().commit()
        return True

    async def redeem_vip_key(self, user_id: int, key: str) -> tuple[bool, str]:
        db = self._db()
        cursor = await db.execute("SELECT * FROM vip_keys WHERE key = ?", (key.strip().upper(),))
        record = await cursor.fetchone()
        if not record or record["status"] != "unused":
            return False, "That VIP key is invalid, already used, or revoked."
        if record["expiry_date"] and record["expiry_date"] <= now():
            await db.execute("UPDATE vip_keys SET status = 'expired' WHERE key = ?", (key.strip().upper(),))
            await db.commit()
            return False, "That VIP key has expired."
        expiry = record["expiry_date"]
        await db.execute("UPDATE vip_keys SET status='active', redeemed_by=?, redeemed_at=? WHERE key=?", (user_id, now(), record["key"]))
        await db.execute("UPDATE users SET vip_status='active', vip_expiry=?, vip_key_used=? WHERE telegram_id=?", (expiry, record["key"], user_id))
        await db.commit()
        return True, "VIP access activated."

    async def active_vip(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        if not user or user["banned"] or user["vip_status"] != "active":
            return False
        return not user["vip_expiry"] or user["vip_expiry"] > now()

    async def list_users(self, filter_name: str = "all") -> list[aiosqlite.Row]:
        where = {"vip": "vip_status='active'", "banned": "banned=1", "pending": "vip_status='pending'"}.get(filter_name, "1=1")
        cursor = await self._db().execute(f"SELECT * FROM users WHERE {where} ORDER BY telegram_id")
        return await cursor.fetchall()

    async def user_actions(self, user_id: int) -> list[aiosqlite.Row]:
        cursor = await self._db().execute("SELECT * FROM user_actions WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,))
        return await cursor.fetchall()

    async def export_users(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        keys = ["telegram_id", "username", "email", "vip_status", "vip_expiry", "selected_exchanges", "banned", "last_active"]
        writer.writerow(keys)
        for user in await self.list_users():
            writer.writerow([user[key] for key in keys])
        return output.getvalue()

    async def save_opportunity(self, opportunity_id: str, opportunity: Any) -> None:
        await self._db().execute(
            """INSERT OR REPLACE INTO opportunities
            (id, symbol, buy_exchange, sell_exchange, buy_price, sell_price, raw_spread,
             net_profit, volume_buy, volume_sell, verified, loose_mode, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (opportunity_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange,
             opportunity.buy_price, opportunity.sell_price, opportunity.raw_spread, opportunity.net_profit,
             opportunity.volume_buy, opportunity.volume_sell, int(opportunity.verified), int(opportunity.loose_mode),
             now(), json.dumps(opportunity.metadata)),
        )
        await self._db().commit()

    async def get_opportunity(self, opportunity_id: str) -> aiosqlite.Row | None:
        cursor = await self._db().execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,))
        return await cursor.fetchone()

    async def increment_stat(self, key: str, amount: int = 1) -> None:
        await self._db().execute("INSERT INTO stats(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value", (key, amount))
        await self._db().commit()

    async def stat(self, key: str) -> int:
        cursor = await self._db().execute("SELECT value FROM stats WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return int(row["value"]) if row else 0
