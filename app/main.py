from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from .config import get_settings
from .db import Database
from .exchanges.registry import build_exchanges
from .filters import matches, user_filters
from .handlers import build_handlers
from .logging_setup import configure_logging
from .scanner import Scanner, opportunity_id

logger = logging.getLogger(__name__)


def run_app() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.database_path)
    exchanges = {}
    scanner = None

    async def alert_opportunity(opportunity) -> None:
        base_identifier = opportunity_id(opportunity)
        loose_identifier = f"{base_identifier}-loose"
        verified_identifier = f"{base_identifier}-verified"
        normal_users = []
        for user in await db.list_users("vip"):
            selected = json.loads(user["selected_exchanges"] or "[]")
            preferences = user_filters(user)
            if opportunity.buy_exchange not in selected or opportunity.sell_exchange not in selected or preferences["paused"] or not matches(opportunity, preferences):
                continue
            if preferences["loose_mode"]:
                loose_opportunity = replace(opportunity, loose_mode=True, verified=False)
                await _send_alert(db, user["telegram_id"], loose_opportunity, loose_identifier, context.application)
                continue
            normal_users.append(user)

        if not normal_users:
            return
        buy_adapter = exchanges.get(opportunity.buy_exchange)
        sell_adapter = exchanges.get(opportunity.sell_exchange)
        if not buy_adapter or not sell_adapter:
            return
        buy_available, buy_meta = await buy_adapter.verify_transfer(opportunity.symbol)
        sell_available, sell_meta = await sell_adapter.verify_transfer(opportunity.symbol)
        if not buy_available or not sell_available:
            return
        if not _matching_network_exists(buy_meta, sell_meta):
            return
        verified_opportunity = replace(
            opportunity,
            verified=True,
            metadata={**opportunity.metadata, "buy_transfer": buy_meta, "sell_transfer": sell_meta},
        )
        for user in normal_users:
            await _send_alert(db, user["telegram_id"], verified_opportunity, verified_identifier, context.application)

    async def post_init(application: Application) -> None:
        nonlocal scanner
        await db.connect()
        exchanges.update(build_exchanges(settings.exchange_names, settings.exchange_credentials))
        active_exchange_names = list(exchanges)
        scanner = Scanner(db, exchanges, settings.scan_interval_seconds, settings.max_exchange_concurrency)
        application.bot_data.update({"db": db, "admin_ids": settings.admin_id_set, "admin_secret_key": settings.admin_secret_key, "exchange_names": active_exchange_names, "exchanges": exchanges, "scanner": scanner})
        scanner.task = asyncio.create_task(scanner.loop(alert_opportunity))
        logger.info("bot started with exchanges: %s", ", ".join(exchanges))

    async def post_shutdown(application: Application) -> None:
        if scanner:
            await scanner.stop()
        await db.close()
        logger.info("bot stopped")

    # The callback needs the Application instance for Telegram sends.
    context = type("ScannerContext", (), {})()
    context.application = None

    async def post_init_with_context(application: Application) -> None:
        context.application = application
        await post_init(application)

    application = Application.builder().token(settings.telegram_bot_token).post_init(post_init_with_context).post_shutdown(post_shutdown).build()
    for handler in build_handlers(db, settings.admin_id_set, settings.exchange_names, settings.admin_secret_key):
        application.add_handler(handler)
    application.run_polling(close_loop=False)


def _matching_network_exists(buy_meta: dict, sell_meta: dict) -> bool:
    buy_networks = {item.get("network"): item.get("contract") for item in buy_meta.get("networks", []) if item.get("deposit")}
    sell_networks = {item.get("network"): item.get("contract") for item in sell_meta.get("networks", []) if item.get("withdraw")}
    return any(network in sell_networks and buy_networks[network] and buy_networks[network] == sell_networks[network] for network in buy_networks)


async def _send_alert(db: Database, user_id: int, opportunity, identifier: str, application: Application) -> None:
    loose_label = "⚠️ LOOSE MODE - unverified, may not be executable\n" if opportunity.loose_mode else "✅ Transfer and contract verification passed\n"
    message = f"Arbitrage opportunity {identifier}\n{opportunity.symbol}\nBuy {opportunity.buy_exchange}: {opportunity.buy_price}\nSell {opportunity.sell_exchange}: {opportunity.sell_price}\nRaw spread: {opportunity.raw_spread:.3f}%\nEstimated net: {opportunity.net_profit:.3f}%\n{loose_label}Data may be stale; re-check before trading."
    try:
        await application.bot.send_message(user_id, message, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Details", callback_data=f"details:{identifier}")]]))
        await db.save_opportunity(identifier, opportunity)
        await db.increment_stat("alerts_sent")
    except Exception:
        logger.exception("failed to alert user %s", user_id)


def run() -> None:
    run_app()


if __name__ == "__main__":
    run()
