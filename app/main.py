from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime

from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import Application

from .arbitrage_features import confidence_score, material_change, rank_score
from .config import get_settings
from .db import Database
from .exchanges.registry import build_exchanges
from .filters import matches, user_filters
from .handlers import build_handlers
from .logging_setup import configure_logging
from .maintenance import MaintenanceAssistant
from .user_ai import UserAIAssistant
from .scanner import Scanner, opportunity_id
from .ui import format_background_alert, format_error, opportunity_buttons
from .ui_router import build_ui_handlers
from .feature_handlers import _enhanced_card, build_feature_handlers

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
    user_ai = UserAIAssistant(maintenance)


    logger.info(
        "AI maintenance configured: %s%s",
        maintenance.configured,
        " (missing: " + ", ".join(maintenance.missing_settings) + ")" if not maintenance.configured else "",
    )
    last_alerts: dict[tuple[int, str, str, str], datetime] = {}
    last_alert_spreads: dict[tuple[int, str, str, str], float] = {}
    LAST_ALERTS_MAX_AGE_SECONDS = 24 * 3600

    def _prune_last_alerts() -> None:
        cutoff = datetime.now(UTC).timestamp() - LAST_ALERTS_MAX_AGE_SECONDS
        stale_keys = [key for key, sent_at in last_alerts.items() if sent_at.timestamp() < cutoff]
        for key in stale_keys:
            del last_alerts[key]
            last_alert_spreads.pop(key, None)
        if stale_keys:
            logger.debug("pruned %s stale last_alerts entries", len(stale_keys))

    async def alert_opportunities(opportunities) -> None:
        _prune_last_alerts()
        sent_counts: dict[int, int] = {}
        vip_users = await db.list_users("vip")
        prepared_users = []
        for user in vip_users:
            try:
                selected = set(json.loads(user["selected_exchanges"] or "[]"))
            except (TypeError, json.JSONDecodeError):
                selected = set()
            prepared_users.append((user, selected, user_filters(user)))

        transfer_cache: dict[tuple[str, str], tuple[bool, dict]] = {}
        persisted_opportunities: set[str] = set()


        async def _verify_transfer(exchange_name: str, symbol: str):
            key = (exchange_name, symbol)
            if key in transfer_cache:
                return transfer_cache[key]
            adapter = exchanges.get(exchange_name)
            result = (False, {"unavailable": True, "networks": []}) if not adapter else await adapter.verify_transfer(symbol)
            transfer_cache[key] = result
            return result

        for opportunity in sorted(opportunities, key=lambda item: item.metadata.get("rank_score", item.net_profit), reverse=True):
            base_identifier = opportunity_id(opportunity)
            loose_identifier = f"{base_identifier}-loose"
            verified_identifier = f"{base_identifier}-verified"
            normal_users = []
            for user, selected, preferences in prepared_users:
                user_id = user["telegram_id"]
                if opportunity.buy_exchange not in selected or opportunity.sell_exchange not in selected or preferences["paused"] or not matches(opportunity, preferences):
                    continue
                if sent_counts.get(user_id, 0) >= preferences["max_results"]:
                    continue
                alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                last_sent = last_alerts.get(alert_key)
                if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < preferences["alert_cooldown"]:
                    continue
                if last_sent and not material_change(last_alert_spreads.get(alert_key), opportunity.raw_spread):
                    continue
                if preferences["loose_mode"]:
                    loose_opportunity = replace(opportunity, loose_mode=True, verified=False)
                    await _send_alert(db, user_id, loose_opportunity, loose_identifier, context.application, user=user, preferences=preferences, persisted_ids=persisted_opportunities)
                    last_alerts[alert_key] = datetime.now(UTC)
                    last_alert_spreads[alert_key] = opportunity.raw_spread
                    sent_counts[user_id] = sent_counts.get(user_id, 0) + 1
                    continue
                normal_users.append((user, preferences))

            if not normal_users:
                continue
            buy_adapter = exchanges.get(opportunity.buy_exchange)
            sell_adapter = exchanges.get(opportunity.sell_exchange)
            if not buy_adapter or not sell_adapter:
                continue
            buy_result, sell_result = await asyncio.gather(
                _verify_transfer(opportunity.buy_exchange, opportunity.symbol),
                _verify_transfer(opportunity.sell_exchange, opportunity.symbol),
            )
            buy_available, buy_meta = buy_result
            sell_available, sell_meta = sell_result
            verification_ok = buy_available and sell_available and _matching_network_exists(buy_meta, sell_meta)
            if not verification_ok:
                unverified_identifier = f"{base_identifier}-not-verified"
                unverified_opportunity = replace(opportunity, verified=False, metadata={**opportunity.metadata, "transfer_verification": "not_verified", "buy_transfer": buy_meta, "sell_transfer": sell_meta})
                for user, preferences in normal_users:
                    user_id = user["telegram_id"]
                    alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                    last_sent = last_alerts.get(alert_key)
                    if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < preferences["alert_cooldown"]:
                        continue
                    if last_sent and not material_change(last_alert_spreads.get(alert_key), opportunity.raw_spread):
                        continue
                    await _send_alert(db, user_id, unverified_opportunity, unverified_identifier, context.application, user=user, preferences=preferences, persisted_ids=persisted_opportunities)
                    last_alerts[alert_key] = datetime.now(UTC)
                    last_alert_spreads[alert_key] = opportunity.raw_spread
                    sent_counts[user_id] = sent_counts.get(user_id, 0) + 1
                continue
            verified_opportunity = replace(opportunity, verified=True, metadata={**opportunity.metadata, "buy_transfer": buy_meta, "sell_transfer": sell_meta, "matching_network": _matching_network(buy_meta, sell_meta)})
            for user, preferences in normal_users:
                user_id = user["telegram_id"]
                alert_key = (user_id, opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
                last_sent = last_alerts.get(alert_key)
                if last_sent and (datetime.now(UTC) - last_sent).total_seconds() < preferences["alert_cooldown"]:
                    continue
                if last_sent and not material_change(last_alert_spreads.get(alert_key), opportunity.raw_spread):
                    continue
                await _send_alert(db, user_id, verified_opportunity, verified_identifier, context.application, user=user, preferences=preferences, persisted_ids=persisted_opportunities)
                last_alerts[alert_key] = datetime.now(UTC)
                last_alert_spreads[alert_key] = opportunity.raw_spread
                sent_counts[user_id] = sent_counts.get(user_id, 0) + 1

    async def post_init(application: Application) -> None:
        nonlocal scanner
        await db.connect()
        user_commands = [
            BotCommand("start", "Register or open your account"),
            BotCommand("menu", "Open the command center"),
            BotCommand("help", "Show available commands"),
            BotCommand("status", "View account status"),
            BotCommand("vipkey", "Redeem a VIP key"),
            BotCommand("scan", "Run a live arbitrage scan"),
            BotCommand("scaninfo", "View scan diagnostics"),
            BotCommand("exchanges", "Manage exchange selection"),
            BotCommand("filters", "View scanner settings"),
            BotCommand("pause", "Pause alerts"),
            BotCommand("resume", "Resume alerts"),
            BotCommand("papertrade", "Record a paper trade"),
            BotCommand("paperstats", "View paper-trade statistics"),
            BotCommand("portfolio", "View simulated portfolio"),
            BotCommand("leaderboard", "View paper-trading leaderboard"),
            BotCommand("aichat", "Chat with the AI assistant"),
            BotCommand("aifix", "Ask AI to investigate a problem"),
            BotCommand("setstability", "Require repeated opportunity observations"),
        ]
        await application.bot.set_my_commands(user_commands)
        admin_commands = user_commands + [
            BotCommand("admin", "Unlock admin tools"),
            BotCommand("genkey", "Create a VIP key"),
            BotCommand("listkeys", "List VIP keys"),
            BotCommand("revokekey", "Revoke a VIP key"),
            BotCommand("extendvip", "Extend VIP access"),
            BotCommand("grantvip", "Grant VIP access"),
            BotCommand("revokevip", "Revoke VIP access"),
            BotCommand("userinfo", "Inspect a user"),
            BotCommand("listusers", "List users"),
            BotCommand("ban", "Ban a user"),
            BotCommand("unban", "Unban a user"),
            BotCommand("broadcast", "Broadcast to VIP users"),
            BotCommand("stats", "View bot statistics"),
            BotCommand("health", "Check exchange health"),
            BotCommand("exchangestats", "View exchange scan stats"),
            BotCommand("exportusers", "Export users"),
            BotCommand("memstatus", "View memory status"),
            BotCommand("diagnose", "Diagnose recent errors"),
            BotCommand("aiprobe", "Probe AI provider"),
            BotCommand("fixerror", "Propose an AI fix"),
            BotCommand("patchstatus", "View AI patches"),
            BotCommand("validatefix", "Validate an AI patch"),
            BotCommand("approvefix", "Approve an AI patch"),
            BotCommand("rejectfix", "Reject an AI patch"),
        ]
        for admin_id in settings.admin_id_set:
            try:
                await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(admin_id))
            except Exception:
                logger.exception("failed to set admin command menu for %s", admin_id)
        cleaned_users = await db.remove_exchange_from_selections("bitmart")
        if cleaned_users:
            logger.info("removed disabled bitmart selection from %s users", cleaned_users)
        exchanges.update(build_exchanges(settings.exchange_names, settings.exchange_credentials))
        active_exchange_names = list(exchanges)
        scanner = Scanner(db, exchanges, settings.scan_interval_seconds, settings.max_exchange_concurrency)
        application.bot_data.update({"db": db, "admin_ids": settings.admin_id_set, "admin_secret_key": settings.admin_secret_key, "exchange_names": active_exchange_names, "exchanges": exchanges, "scanner": scanner, "maintenance": maintenance, "user_ai": user_ai})
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

    # Enhanced feature handlers own the live scan/details flows; legacy handlers keep
    # account, settings, admin, and paper-trading commands available.
    for handler in build_feature_handlers():
        application.add_handler(handler)
    existing_handlers = build_handlers(db, settings.admin_id_set, settings.exchange_names, settings.admin_secret_key)
    for handler in existing_handlers[1:]:
        application.add_handler(handler)
    for handler in build_ui_handlers(db, settings.admin_id_set, settings.exchange_names, settings.admin_secret_key):
        application.add_handler(handler)

    async def error_handler(update, context):
        error = context.error
        if error:
            logger.error("exception in handler: %s", error, exc_info=(type(error), error, error.__traceback__))
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


async def _send_alert(db: Database, user_id: int, opportunity, identifier: str, application: Application, *, user=None, preferences: dict | None = None, persisted_ids: set[str] | None = None) -> None:
    if user is None:
        user = await db.get_user(user_id)
    if preferences is None:
        preferences = user_filters(user) if user else {"trade_size": 1000.0}
    metadata = dict(getattr(opportunity, "metadata", {}) or {})
    observed_at = metadata.get("observed_at")
    try:
        age = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(observed_at)).total_seconds()) if observed_at else 0.0
    except (TypeError, ValueError):
        age = 0.0
    confidence = confidence_score(
        net_profit_pct=opportunity.net_profit,
        buy_volume=opportunity.volume_buy,
        sell_volume=opportunity.volume_sell,
        trade_size=float(preferences.get("trade_size", 1000.0)),
        freshness_seconds=age,
        transfer_verified=bool(opportunity.verified),
        coverage_complete=bool(metadata.get("coverage_complete")),
        executable_complete=False,
    )
    metadata["confidence"] = confidence
    metadata["rank_score"] = rank_score(opportunity.net_profit, confidence, None)
    opportunity = replace(opportunity, metadata=metadata)
    message = _enhanced_card(opportunity, identifier, float(preferences.get("trade_size", 1000.0)))
    try:
        if persisted_ids is None or identifier not in persisted_ids:
            await db.save_opportunity(identifier, opportunity)
            if persisted_ids is not None:
                persisted_ids.add(identifier)
        await application.bot.send_message(user_id, message, reply_markup=opportunity_buttons(identifier), parse_mode="HTML")
        await db.increment_stat("alerts_sent")
    except Exception:
        logger.exception("failed to alert user %s", user_id)


def run() -> None:
    run_app()


if __name__ == "__main__":
    run()
