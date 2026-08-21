import asyncio
from datetime import UTC, datetime, timedelta

from app.db import Database
from app.exchanges.base import Opportunity


def _opportunity() -> Opportunity:
    return Opportunity("ZEC/USDT", "poloniex", "gateio", 1.0, 1.2, 20.0, 19.0, 1000.0, 500.0)


def test_saved_opportunity_retrieves_with_stable_id_and_expires(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "bot.sqlite3"), opportunity_ttl_seconds=300)
        await db.connect()
        await db.save_opportunity("stable-id", _opportunity())
        current = await db.get_opportunity("stable-id")
        assert current is not None
        assert current["id"] == "stable-id"

        await db._db().execute(
            "UPDATE opportunities SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=301)).isoformat(), "stable-id"),
        )
        await db._db().commit()
        assert await db.get_opportunity("stable-id") is None
        await db.close()

    asyncio.run(scenario())