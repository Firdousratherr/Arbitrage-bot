from __future__ import annotations

import time
from typing import Any


class RecoveryMemory:
    """Persistent, exchange-scoped memory of recovery actions that actually worked."""

    def __init__(self, repo: Any):
        self.repo = repo
        self._ready = False

    async def _ensure(self):
        if self._ready:
            return
        await self.repo.db.execute(
            """CREATE TABLE IF NOT EXISTS exchange_recovery_memory (
                exchange TEXT NOT NULL,
                error_type TEXT NOT NULL,
                action TEXT NOT NULL,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                last_success_at REAL,
                last_failure_at REAL,
                PRIMARY KEY(exchange, error_type, action)
            )"""
        )
        await self.repo.db.commit()
        self._ready = True

    async def preferred(self, exchange: str, error_type: str) -> str | None:
        await self._ensure()
        row = await (await self.repo.db.execute(
            """SELECT action FROM exchange_recovery_memory
               WHERE exchange=? AND error_type=? AND success_count>failure_count
               ORDER BY confidence DESC, success_count DESC LIMIT 1""",
            (exchange.lower(), error_type.lower()),
        )).fetchone()
        return row["action"] if row else None

    async def record(self, exchange: str, error_type: str, action: str, success: bool):
        await self._ensure()
        now = time.time()
        row = await (await self.repo.db.execute(
            "SELECT success_count,failure_count FROM exchange_recovery_memory WHERE exchange=? AND error_type=? AND action=?",
            (exchange.lower(), error_type.lower(), action),
        )).fetchone()
        if row:
            success_count = int(row["success_count"]) + int(success)
            failure_count = int(row["failure_count"]) + int(not success)
            confidence = success_count / max(1, success_count + failure_count)
            await self.repo.db.execute(
                """UPDATE exchange_recovery_memory SET success_count=?,failure_count=?,confidence=?,
                   last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,
                   last_failure_at=CASE WHEN ? THEN ? ELSE last_failure_at END
                   WHERE exchange=? AND error_type=? AND action=?""",
                (success_count, failure_count, confidence, success, now, not success, now, exchange.lower(), error_type.lower(), action),
            )
        else:
            await self.repo.db.execute(
                """INSERT INTO exchange_recovery_memory
                   (exchange,error_type,action,success_count,failure_count,confidence,last_success_at,last_failure_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (exchange.lower(), error_type.lower(), action, int(success), int(not success), 1.0 if success else 0.0, now if success else None, now if not success else None),
            )
        await self.repo.db.commit()

    async def summary(self, exchange: str | None = None) -> list[dict[str, Any]]:
        await self._ensure()
        if exchange:
            rows = await (await self.repo.db.execute(
                "SELECT exchange,error_type,action,success_count,failure_count,confidence,last_success_at FROM exchange_recovery_memory WHERE exchange=? ORDER BY confidence DESC",
                (exchange.lower(),),
            )).fetchall()
        else:
            rows = await (await self.repo.db.execute(
                "SELECT exchange,error_type,action,success_count,failure_count,confidence,last_success_at FROM exchange_recovery_memory ORDER BY confidence DESC",
            )).fetchall()
        return [dict(r) for r in rows]
