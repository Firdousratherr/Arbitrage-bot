from __future__ import annotations

import asyncio
import logging
import os

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .application.service import TerminalService
from .ai import AIAssistant
from .arbitrage import ArbitrageScanner
from .bot import build_handlers
from .bot.admin import admin_callback, admin_cmd, adminstats_cmd, ban_cmd, givevip_cmd, init_admin_storage, revokevip_cmd, unban_cmd, useractions_cmd, userinfo_cmd, users_cmd, vipkeys_cmd
from .bot.code_repair import CodeRepairManager, aifix_callback, aifix_cancel_cmd, aifix_cmd, aifix_history_cmd, aifix_status_cmd
from .bot.commands import (
    dashboard_cmd, diagnostics_cmd, filters_callback, filters_cmd, filter_settings_callback,
    help_cmd, resetfilters_cmd, setfilter_cmd, settings_cmd, status_cmd,
)
from .bot.exchange_selection import dashboard_exchanges_callback, exchange_selection_callback
from .bot.live_scan import live_scan_callback
from .bot.results import results_page_callback, results_detail_callback
from .exchanges.registry import build_exchanges
from .infrastructure.config import get_settings
from .infrastructure.logging import configure
from .infrastructure.repository import Repository


PUBLIC_COMMANDS = [
    BotCommand('start', 'Start the Arbitrage Terminal'),
    BotCommand('dashboard', 'Open the main dashboard'),
    BotCommand('scan', 'Scan for arbitrage opportunities'),
    BotCommand('results', 'View scan history and results'),
    BotCommand('exchanges', 'Select exchanges'),
    BotCommand('filters', 'View and edit scan filters'),
    BotCommand('setfilter', 'Change a scan filter'),
    BotCommand('resetfilters', 'Reset filters to defaults'),
    BotCommand('settings', 'Validation and AI settings'),
    BotCommand('status', 'Exchange and runtime status'),
    BotCommand('diagnostics', 'View latest scan diagnostics'),
    BotCommand('ai', 'Configure AI result mode'),
    BotCommand('vipkey', 'Activate VIP access'),
    BotCommand('help', 'Show help and commands'),
]


async def build_runtime():
    settings = get_settings()
    configure(settings.log_level)
    repo = Repository(settings.database_path)
    await repo.connect()
    await init_admin_storage(repo)

    def creds(name):
        p = name.upper()
        return {
            k: v for k, v in {
                'apiKey': os.getenv(f'{p}_API_KEY', ''),
                'secret': os.getenv(f'{p}_SECRET', ''),
                'password': os.getenv(f'{p}_PASSWORD', ''),
            }.items() if v
        }

    exchange_diagnostics = []
    exchanges = build_exchanges(settings.exchanges, creds, exchange_diagnostics)
    scanner = ArbitrageScanner(exchanges, settings.exchange_concurrency, settings.scan_timeout_seconds)
    ai = AIAssistant(settings.ai_api_url, settings.ai_api_key, settings.ai_model, settings.ai_timeout_seconds)
    await ai.start()
    code_repair = CodeRepairManager(ai, settings)
    return settings, repo, exchanges, scanner, ai, code_repair, TerminalService(repo, scanner, ai, settings), exchange_diagnostics


def run():
    asyncio.run(_run())


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.getLogger(__name__).exception('telegram handler failed', exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try:
            await update.callback_query.answer('Something went wrong. Please try again.', show_alert=True)
        except Exception:
            pass
    elif isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text('⚠️ Something went wrong. Please try again.')
        except Exception:
            pass


async def _run():
    settings, repo, exchanges, scanner, ai, code_repair, service, exchange_diagnostics = await build_runtime()
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data.update({
        'settings': settings,
        'repo': repo,
        'exchanges': exchanges,
        'exchange_names': [n for n in settings.exchanges if n in exchanges],
        'scanner': scanner,
        'ai': ai,
        'code_repair': code_repair,
        'service': service,
        'exchange_diagnostics': exchange_diagnostics,
    })

    app.add_handler(CallbackQueryHandler(dashboard_exchanges_callback, pattern=r'^exchanges$'))
    app.add_handler(CallbackQueryHandler(exchange_selection_callback, pattern=r'^ex:'))
    app.add_handler(CallbackQueryHandler(live_scan_callback, pattern=r'^scan$'))
    app.add_handler(CallbackQueryHandler(results_page_callback, pattern=r'^page:'))
    app.add_handler(CallbackQueryHandler(results_detail_callback, pattern=r'^r(?:diag|debug|aian|order):'))
    app.add_handler(CallbackQueryHandler(filters_callback, pattern=r'^filters$'))
    app.add_handler(CallbackQueryHandler(filter_settings_callback, pattern=r'^filter:'))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r'^admin:'))
    if settings.ai_code_repair_enabled:
        app.add_handler(CallbackQueryHandler(aifix_callback, pattern=r'^aifix:'))

    app.add_handler(CommandHandler('dashboard', dashboard_cmd))
    app.add_handler(CommandHandler('status', status_cmd))
    app.add_handler(CommandHandler('diagnostics', diagnostics_cmd))
    app.add_handler(CommandHandler('filters', filters_cmd))
    app.add_handler(CommandHandler('setfilter', setfilter_cmd))
    app.add_handler(CommandHandler('resetfilters', resetfilters_cmd))
    app.add_handler(CommandHandler('settings', settings_cmd))
    app.add_handler(CommandHandler('help', help_cmd))
    app.add_handler(CommandHandler('admin', admin_cmd))
    if settings.ai_code_repair_enabled:
        app.add_handler(CommandHandler('aifix', aifix_cmd))
        app.add_handler(CommandHandler('aifixstatus', aifix_status_cmd))
        app.add_handler(CommandHandler('aifixhistory', aifix_history_cmd))
        app.add_handler(CommandHandler('aifixcancel', aifix_cancel_cmd))
    app.add_handler(CommandHandler('users', users_cmd))
    app.add_handler(CommandHandler('userinfo', userinfo_cmd))
    app.add_handler(CommandHandler('givevip', givevip_cmd))
    app.add_handler(CommandHandler('revokevip', revokevip_cmd))
    app.add_handler(CommandHandler('ban', ban_cmd))
    app.add_handler(CommandHandler('unban', unban_cmd))
    app.add_handler(CommandHandler('useractions', useractions_cmd))
    app.add_handler(CommandHandler('vipkeys', vipkeys_cmd))
    app.add_handler(CommandHandler('adminstats', adminstats_cmd))

    [app.add_handler(h) for h in build_handlers()]
    app.add_error_handler(_error_handler)

    await app.initialize()
    try:
        await app.bot.set_my_commands(PUBLIC_COMMANDS)
        logging.getLogger(__name__).info('Telegram public command menu refreshed (%d commands)', len(PUBLIC_COMMANDS))
    except Exception:
        logging.getLogger(__name__).exception('Failed to refresh Telegram public command menu')
    await app.start()
    await app.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        await asyncio.gather(*(x.close() for x in exchanges.values()), return_exceptions=True)
        await ai.close()
        await repo.close()


if __name__ == '__main__':
    run()
