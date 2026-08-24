"""Reusable Telegram UI animation helpers.

Animations edit a single message instead of sending a stream of messages, keeping
chats clean and avoiding extra scanner/API work.
"""
from __future__ import annotations

import asyncio
import logging

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


async def animate_message(message, frames=LOADING_FRAMES, *, interval: float = 0.65) -> None:
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


async def animate_scan_progress(message) -> None:
    await animate_message(message, SCAN_FRAMES, interval=0.7)
