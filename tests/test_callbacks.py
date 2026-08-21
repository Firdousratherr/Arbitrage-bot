import asyncio
from types import SimpleNamespace

from app.db import Database
from app.exchanges.base import Opportunity
from app.handlers import opportunity_details, paper_trade_callback


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.from_user = SimpleNamespace(id=7)
        self.edits = []
        self.answers = []
        self.message = self

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


class FakeExchange:
    async def fetch_order_book(self, symbol, limit):
        return {"asks": [[1.0, 100.0]], "bids": [[1.2, 100.0]]}

    async def get_taker_fee(self, symbol):
        return 0.001


def _opportunity():
    return Opportunity("ZEC/USDT", "buy", "sell", 1.0, 1.2, 20.0, 19.0, 1000.0, 500.0)


def test_details_callback_edits_existing_message(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "details.sqlite3"))
        await db.connect()
        await db.upsert_user(7, "user", "user@example.test", ["buy", "sell"])
        await db.set_user(7, vip_status="active")
        await db.save_opportunity("stable-id", _opportunity())
        query = FakeQuery("details:stable-id")
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"db": db, "exchanges": {"buy": FakeExchange(), "sell": FakeExchange()}}))

        await opportunity_details(SimpleNamespace(callback_query=query), context)

        assert query.answers
        assert "DETAILS" in query.edits[-1][0]
        assert query.edits[-1][1]["reply_markup"].inline_keyboard[0][1].callback_data == "paper:stable-id"
        await db.close()

    asyncio.run(scenario())


def test_paper_trade_callback_edits_existing_message(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "paper.sqlite3"))
        await db.connect()
        await db.upsert_user(7, "user", "user@example.test", ["buy", "sell"])
        await db.set_user(7, vip_status="active")
        await db.save_opportunity("stable-id", _opportunity())
        query = FakeQuery("paper:stable-id")
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"db": db}))

        await paper_trade_callback(SimpleNamespace(callback_query=query), context)

        assert "PAPER TRADE" in query.edits[-1][0]
        assert not hasattr(query, "reply_text")
        await db.close()

    asyncio.run(scenario())
