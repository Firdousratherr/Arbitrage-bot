from __future__ import annotations
import asyncio,os,logging
from telegram import Update
from telegram.ext import Application,CallbackQueryHandler,ContextTypes
from .application.service import TerminalService
from .ai import AIAssistant
from .arbitrage import ArbitrageScanner
from .bot import build_handlers
from .bot.exchange_selection import exchange_selection_callback
from .bot.live_scan import live_scan_callback
from .bot.results import results_page_callback, results_detail_callback
from .exchanges.registry import build_exchanges
from .infrastructure.config import get_settings
from .infrastructure.logging import configure
from .infrastructure.repository import Repository

async def build_runtime():
    settings=get_settings();configure(settings.log_level);repo=Repository(settings.database_path);await repo.connect()
    def creds(name):
        p=name.upper();return {k:v for k,v in {'apiKey':os.getenv(f'{p}_API_KEY',''),'secret':os.getenv(f'{p}_SECRET',''),'password':os.getenv(f'{p}_PASSWORD','')}.items() if v}
    exchange_diagnostics=[]
    exchanges=build_exchanges(settings.exchanges,creds,exchange_diagnostics)
    scanner=ArbitrageScanner(exchanges,settings.exchange_concurrency,settings.scan_timeout_seconds)
    ai=AIAssistant(settings.ai_api_url,settings.ai_api_key,settings.ai_model,settings.ai_timeout_seconds);await ai.start()
    return settings,repo,exchanges,scanner,ai,TerminalService(repo,scanner,ai,settings),exchange_diagnostics

def run():asyncio.run(_run())

async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.getLogger(__name__).exception('telegram handler failed', exc_info=context.error)
    if isinstance(update, Update) and update.callback_query:
        try: await update.callback_query.answer('Something went wrong. Please try again.', show_alert=True)
        except Exception: pass
    elif isinstance(update, Update) and update.effective_message:
        try: await update.effective_message.reply_text('⚠️ Something went wrong. Please try again.')
        except Exception: pass

async def _run():
    settings,repo,exchanges,scanner,ai,service,exchange_diagnostics=await build_runtime();app=Application.builder().token(settings.telegram_bot_token).build()
    app.bot_data.update({'settings':settings,'repo':repo,'exchanges':exchanges,'exchange_names':[n for n in settings.exchanges if n in exchanges],'scanner':scanner,'ai':ai,'service':service,'exchange_diagnostics':exchange_diagnostics})
    app.add_handler(CallbackQueryHandler(exchange_selection_callback,pattern=r'^ex:'))
    app.add_handler(CallbackQueryHandler(live_scan_callback,pattern=r'^scan$'))
    app.add_handler(CallbackQueryHandler(results_page_callback,pattern=r'^page:'))
    app.add_handler(CallbackQueryHandler(results_detail_callback,pattern=r'^r(?:diag|debug|aian|order):'))
    [app.add_handler(h) for h in build_handlers()]
    app.add_error_handler(_error_handler)
    await app.initialize();await app.start();await app.updater.start_polling()
    try:await asyncio.Event().wait()
    finally:
        await app.updater.stop();await app.stop();await app.shutdown();await asyncio.gather(*(x.close() for x in exchanges.values()),return_exceptions=True);await ai.close();await repo.close()

if __name__=='__main__':run()
