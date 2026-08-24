"""Small Telegram UI animation helpers.

These helpers intentionally use message edits rather than sending multiple messages,
so animations do not spam chats or alter scanner semantics.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

SCAN_FRAMES = (
    "🔎 <b>SCANNER ONLINE</b>\n\n⠋ Connecting to exchanges…",
    "📡 <b>SCANNER ONLINE</b>\n\n⠙ Fetching live market data…",
    "⚡ <b>SCANNER ONLINE</b>\n\n⠹ Comparing cross-exchange prices…",
    "🧮 <b>SCANNER ONLINE</b>\n\n⠸ Applying profit & volume filters…",
    "🛡️ <b>SCANNER ONLINE</b>\n\n⠼ Checking transfer routes…",
)

LOADING_FRAMES = (
    "⏳ <b>Loading</b> · ░░░░",
    "⏳ <b>Loading</b> · ▓░░░",
    "⏳ <b>Loading</b> · ▓▓░░",
    "⏳ <b>Loading</b> · ▓▓▓░",
    "⏳ <b>Loading</b> · ▓▓▓▓",
)


async def animate_message(message, frames: tuple[str, ...], *, interval: float = 0.65) -> None:
    """Edit one Telegram message through a short animation until cancelled."""
    index = 0
    try:
        while True:
            await message.edit_text(frames[index % len(frames)], parse_mode="HTML")
            index += 1
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("UI animation stopped", exc_info=True)


async def with_loading(message, operation: Callable[[], Awaitable[object]], *, frames=LOADING_FRAMES):
    """Run an async operation while displaying a lightweight loading animation."""
    task = asyncio.create_task(animate_message(message, frames))
    try:
        return await operation()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
