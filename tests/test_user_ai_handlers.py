import asyncio
from types import SimpleNamespace

import pytest

from app.handlers import user_ai_cancel


class _Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class _TaskUpdate:
    def __init__(self, user_id):
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = _Message()


@pytest.mark.asyncio
async def test_user_ai_cancel_cancels_running_request():
    pending = asyncio.create_task(asyncio.sleep(60))
    update = _TaskUpdate(123)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"user_ai_tasks": {123: pending}}
        )
    )

    await user_ai_cancel(update, context)

    await asyncio.sleep(0)
    assert pending.cancelled()
    assert 123 not in context.application.bot_data["user_ai_tasks"]
    assert "cancelled" in update.effective_message.replies[-1].lower()


@pytest.mark.asyncio
async def test_user_ai_cancel_reports_no_running_request():
    update = _TaskUpdate(123)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"user_ai_tasks": {}}
        )
    )

    await user_ai_cancel(update, context)

    assert "no running AI request" in update.effective_message.replies[-1]
