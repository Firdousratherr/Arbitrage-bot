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

    async def alert_opportunities(opportunities) -> None:
        sent_counts: dict[int, int] = {}
        for opportunity in sorted(opportunities, key=lambda item: item.net_profit, reverse=True):
            base_identifier = opportunity_id(opportunity)
            loose_identifier = f"{base_identifier}-loose"
            verified_identifier = f"{base_identifier}-verified"
            normal_users = []
            for user in await db.list_users("vip"):
                selected = json.loads(user["selected_exchanges"] or "[]")
                preferences = user_filters(user)
                user_id = user["telegram_id"]
                if opportunity.buy_exchange not in selected or opportunity.sell_exchange not in selected or preferences["paused"] or not matches(opportunity, preferences):
                    continue
                if sent_counts.get(user_id, 0) >= preferences["max_results"]:
                    continue
                if preferences["loose_mode"]:
                    loose_opportunity = replace(opportunity, loose_mode=True, verified=False)
                    await _send_alert(db, user_id, loose_opportunity, loose_identifier, context.application)
                    sent_counts[user_id] = sent_counts.get(user_id, 0) + 1
                    continue
                normal_users.append(user)

            if not normal_users:
                continue
            buy_adapter = exchanges.get(opportunity.buy_exchange)
            sell_adapter = exchanges.get(opportunity.sell_exchange)
            if not buy_adapter or not sell_adapter:
                continue
            buy_available, buy_meta = await buy_adapter.verify_transfer(opportunity.symbol)
            sell_available, sell_meta = await sell_adapter.verify_transfer(opportunity.symbol)
            verification_ok = buy_available and sell_available and _matching_network_exists(buy_meta, sell_meta)
            if not verification_ok:
                unverified_identifier = f"{base_identifier}-not-verified"
                unverified_opportunity = replace(
                    opportunity,
                    verified=False,
                    metadata={
                        **opportunity.metadata,
                        "transfer_verification": "not_verified",
                        "buy_transfer": buy_meta,
                        "sell_transfer": sell_meta,
                    },
                )
                for user in normal_users:
                    user_id = user["telegram_id"]
                    await _send_alert(db, user_id, unverified_opportunity, unverified_identifier, context.application)
                    sent_counts[user_id] = sent_counts.get(user_id, 0) + 1
                continue
            verified_opportunity = replace(
                opportunity,
                verified=True,
                metadata={**opportunity.metadata, "buy_transfer": buy_meta, "sell_transfer": sell_meta},
            )
            for user in normal_users:
                user_id = user["telegram_id"]
                await _send_alert(db, user_id, verified_opportunity, verified_identifier, context.application)
                sent_counts[user_id] = sent_counts.get(user_id, 0) + 1

    async def post_init(application: Application) -> None:
        nonlocal scanner
        await db.connect()
        exchanges.update(build_exchanges(settings.exchange_names, settings.exchange_credentials))
        active_exchange_names = list(exchanges)
        scanner = Scanner(db, exchanges, settings.scan_interval_seconds, settings.max_exchange_concurrency)
        application.bot_data.update({"db": db, "admin_ids": settings.admin_id_set, "admin_secret_key": settings.admin_secret_key, "exchange_names": active_exchange_names, "exchanges": exchanges, "scanner": scanner})
        scanner.task = asyncio.create_task(scanner.loop(alert_opportunities))
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
    verification_pending = opportunity.metadata.get("transfer_verification") in {
        "not_verified",
        "unavailable_or_no_matching_network",
    }
    if opportunity.loose_mode:
        loose_label = "⚠️ Unverified transfer route - use caution"
    elif verification_pending:
        loose_label = "⚠️ Transfer route not verified - review Details before acting"
    else:
        loose_label = "✅ Transfer route verified"
    message = (
        f"🚨 ARBITRAGE OPPORTUNITY\n"
        f"🪙 {opportunity.symbol}  ·  {identifier}\n\n"
        f"🟢 BUY  {opportunity.buy_exchange}: {opportunity.buy_price:.8f}\n"
        f"🔴 SELL {opportunity.sell_exchange}: {opportunity.sell_price:.8f}\n\n"
        f"📈 Gross spread: {opportunity.raw_spread:.3f}%\n"
        f"💸 Fees: open Details for live taker rates\n"
        f"💰 Est. net before live fees: {opportunity.net_profit:.3f}%\n"
        f"📊 24h volume: {opportunity.volume_buy:.0f} / {opportunity.volume_sell:.0f}\n"
        f"{loose_label}\n\n"
        "🔎 Open Details for fills, order books, fees, and net profit."
    )
    try:
        await application.bot.send_message(user_id, message, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Details + Order Book", callback_data=f"details:{identifier}"), InlineKeyboardButton("Paper Trade", callback_data=f"paper:{identifier}")]]))
        await db.save_opportunity(identifier, opportunity)
        await db.increment_stat("alerts_sent")
    except Exception:
        logger.exception("failed to alert user %s", user_id)


def run() -> None:
    run_app()


if __name__ == "__main__":
    run()
