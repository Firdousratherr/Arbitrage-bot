from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application

from .config import get_settings
from .db import Database
from .exchanges.registry import build_exchanges
from .filters import matches, user_filters
from .handlers import build_handlers
from .logging_setup import configure_logging
from .maintenance import MaintenanceAssistant
from .scanner import Scanner, opportunity_id
from .ui import format_background_alert, format_error, opportunity_buttons
from .ui_router import build_ui_handlers

logger = logging.getLogger(__name__)


def run_app() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.ai_max_log_entries)
    db = Database(settings.database_path, settings.opportunity_ttl_seconds)
    exchanges = {}
    scanner = None
    maintenance = MaintenanceAssistant(
        settings.ai_api_url,
        settings.ai_api_key,
        settings.ai_model,
        settings.ai_fallback_model,
        settings.ai_max_input_tokens,
        settings.maintenance_repo_path,
    )
    logger.info(
        "AI maintenance configured: %s%s",
        maintenance.configured,
        " (missing: " + ", ".join(maintenance.missing_settings) + ")" if not maintenance.configured else "",
    )
    last_alerts: dict[tuple[int, str, str, str], datetime] = {}
    LAST_ALERTS_MAX_AGE_SECONDS = 24 * 3600

    def _prune_last_alerts() -> None:
        cutoff = datetime.now(UTC).timestamp() - LAST_ALERTS_MAX_AGE_SECONDS
        stale_keys = [key for key, sent_at in last_alerts.items() if sent_at.timestamp() < cutoff]
        for key in stale_keys:
            del last_alerts[key]
        if stale_keys:
            logger.debug("pruned %s stale last_alerts entries", len(stale_keys))

    async def alert_opportunities(opportunities) -> None:
        _prune_last_alerts()
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
                alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                last_sent = last_alerts.get(alert_key)
                if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < preferences["alert_cooldown"]:
                    continue
                if preferences["loose_mode"]:
                    loose_opportunity = replace(opportunity, loose_mode=True, verified=False)
                    await _send_alert(db, user_id, loose_opportunity, loose_identifier, context.application)
                    last_alerts[alert_key] = datetime.now(UTC)
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
                unverified_opportunity = replace(opportunity, verified=False, metadata={**opportunity.metadata, "transfer_verification": "not_verified", "buy_transfer": buy_meta, "sell_transfer": sell_meta})
                for user in normal_users:
                    user_id = user["telegram_id"]
                    alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                    last_sent = last_alerts.get(alert_key)
                    if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < user_filters(user)["alert_cooldown"]:
                        continue
                    await _send_alert(db, user_id, unverified_opportunity, unverified_identifier, context.application)
                    last_alerts[alert_key] = datetime.now(UTC)
                    sent_counts[user_id] = sent_counts.get(user_id, 0) + 1
                continue
            verified_opportunity = replace(opportunity, verified=True, metadata={**opportunity.metadata, "buy_transfer": buy_meta, "sell_transfer": sell_meta, "matching_network": _matching_network(buy_meta, sell_meta)})
            for user in normal_users:
                user_id = user["telegram_id"]
                alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                last_sent = last_alerts.get(alert_key)
                if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < user_filters(user)["alert_cooldown"]:
                    continue
                await _send_alert(db, user_id, verified_opportunity, verified_identifier, context.application)
                last_alerts[alert_key] = datetime.now(UTC)
                sent_counts[user_id] = sent_counts.get(user_id, 0) + 1

    async def post_init(application: Application) -> None:
        nonlocal scanner
        await db.connect()
        cleaned_users = await db.remove_exchange_from_selections("bitmart")
        if cleaned_users:
            logger.info("removed disabled bitmart selection from %s users", cleaned_users)
        exchanges.update(build_exchanges(settings.exchange_names, settings.exchange_credentials))
        active_exchange_names = list(exchanges)
        scanner = Scanner(db, exchanges, settings.scan_interval_seconds, settings.max_exchange_concurrency)
        application.bot_data.update({"db": db, "admin_ids": settings.admin_id_set, "admin_secret_key": settings.admin_secret_key, "exchange_names": active_exchange_names, "exchanges": exchanges, "scanner": scanner, "maintenance": maintenance})
        scanner.task = asyncio.create_task(scanner.loop(alert_opportunities))
        if len(exchanges) < 2:
            logger.error("fewer than two exchanges are active; arbitrage results are impossible")
        logger.info("bot started with exchanges: %s", ", ".join(exchanges) or "none")

    async def post_shutdown(application: Application) -> None:
        if scanner:
            await scanner.stop()
        await db.close()
        logger.info("bot stopped")

    context = type("ScannerContext", (), {})()
    context.application = None

    async def post_init_with_context(application: Application) -> None:
        context.application = application
        await post_init(application)

    application = Application.builder().token(settings.telegram_bot_token).post_init(post_init_with_context).post_shutdown(post_shutdown).build()

    # Keep all existing commands/callbacks, but replace the old registration ConversationHandler
    # with the premium UI registration flow. This preserves every scanner/admin feature.
    existing_handlers = build_handlers(db, settings.admin_id_set, settings.exchange_names, settings.admin_secret_key)
    for handler in existing_handlers[1:]:
        application.add_handler(handler)
    for handler in build_ui_handlers(db, settings.admin_id_set, settings.exchange_names, settings.admin_secret_key):
        application.add_handler(handler)

    async def error_handler(update, context):
        logger.exception("exception in handler", exc_info=context.error)
        try:
            message = format_error("Something went wrong running that command", "Try again in a moment")
            if update and update.effective_message:
                await update.effective_message.reply_text(message, parse_mode="HTML")
        except Exception:
            logger.exception("failed to send error message")

    application.add_error_handler(error_handler)
    application.run_polling(close_loop=False)


def _matching_network_exists(buy_meta: dict, sell_meta: dict) -> bool:
    return _matching_network(buy_meta, sell_meta) is not None


def _network_key(value: object) -> str:
    normalized = "".join(character for character in str(value or "").lower() if character.isalnum())
    aliases = {"eth": "ethereum", "erc20": "ethereum", "ethereum": "ethereum", "bsc": "bsc", "bep20": "bsc", "binancesmartchain": "bsc", "matic": "polygon", "polygon": "polygon", "polygonpos": "polygon", "arb": "arbitrum", "arbitrum": "arbitrum", "op": "optimism", "optimism": "optimism", "trx": "tron", "trc20": "tron", "tron": "tron"}
    return aliases.get(normalized, normalized)


def _contract_key(value: object) -> str:
    return str(value or "").lower().removeprefix("0x").strip()


def _matching_network(buy_meta: dict, sell_meta: dict) -> str | None:
    buy_networks = {_network_key(item.get("network")): item for item in buy_meta.get("networks", []) if item.get("deposit")}
    sell_networks = {_network_key(item.get("network")): item for item in sell_meta.get("networks", []) if item.get("withdraw")}
    for network, buy in buy_networks.items():
        sell = sell_networks.get(network)
        if sell and _contract_key(buy.get("contract")) and _contract_key(buy.get("contract")) == _contract_key(sell.get("contract")):
            return buy.get("network") or network
    return None


async def _send_alert(db: Database, user_id: int, opportunity, identifier: str, application: Application) -> None:
    message = format_background_alert(opportunity, identifier)
    try:
        await db.save_opportunity(identifier, opportunity)
        await application.bot.send_message(user_id, message, reply_markup=opportunity_buttons(identifier), parse_mode="HTML")
        await db.increment_stat("alerts_sent")
    except Exception:
        logger.exception("failed to alert user %s", user_id)


def _transfer_summary(metadata: dict | None, action: str) -> str:
    if not metadata:
        return "verification pending"
    networks = [item.get("network", "unknown") for item in metadata.get("networks", []) if item.get(action)]
    return ", ".join(networks[:3]) if networks else "not available"


def run() -> None:
    run_app()


if __name__ == "__main__":
    run()
