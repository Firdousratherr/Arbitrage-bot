from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .handlers import kb


def _main():
    text = (
        '🤖 <b>ARBITRAGE TERMINAL HELP</b>\n\n'
        'A read-only cross-exchange scanner. It does not place trades or withdrawals.\n\n'
        '📚 <b>USER COMMANDS</b>\n'
        '/start — start the terminal\n'
        '/dashboard — open dashboard\n'
        '/scan — start a scan\n'
        '/results — view scan history/results\n'
        '/exchanges — select exchanges\n'
        '/filters — view current filters\n'
        '/setfilter — change a filter\n'
        '/resetfilters — restore filter defaults\n'
        '/settings — validation/AI settings\n'
        '/status — exchange/runtime status\n'
        '/diagnostics — latest scan diagnostics\n'
        '/ai — AI result mode\n'
        '/vipkey — activate VIP access\n'
        '/airecovery — AI exchange-recovery settings\n'
        '/help — this help page\n\n'
        '🔐 <b>ADMIN / MAINTENANCE</b>\n'
        '/genkey, /aiprobe, /aiprobestatus, /aiprobelogs, /aiproberepair and admin commands are restricted.\n\n'
        'Use <b>Full Instructions</b> below for feature-by-feature explanations.'
    )
    return text


def _instructions(page: str = 'scanner'):
    pages = {
        'scanner': ('🔎 <b>SCANNER</b>', 'The scanner loads market data from your selected exchanges, compares common symbols, applies your filters, validates executable order-book depth when enabled, and then validates transfer/network compatibility in strict mode.'),
        'exchanges': ('🏦 <b>EXCHANGES</b>', 'Select the exchanges you want included. The scanner only searches the selected set. Exchange recovery is isolated per exchange so one failure does not require restarting the whole scan.'),
        'filters': ('📊 <b>FILTERS</b>', 'Gap is the price discrepancy threshold. Net profit accounts for known fees and withdrawal cost when available. Volume/liquidity constrain market quality. Data age limits stale quotes. Trade size is the quote-currency amount used for executable profitability.'),
        'validation': ('🛡️ <b>VALIDATION</b>', 'Strict mode requires compatible transfer networks, contract/address matching and known withdrawal cost before a route is treated as verified. Loose mode can show unverified routes but they are explicitly marked.'),
        'recovery': ('🔧 <b>SELF-HEALING & RECOVERY</b>', 'Transient exchange failures are retried. Repeated failures trigger deterministic repair and health verification. Successful recovery strategies are remembered persistently per exchange and error type, then preferred on future matching failures. Authentication and invalid-request failures are never automatically repaired.'),
        'ai': ('🧠 <b>AI</b>', 'AI analysis receives sanitized deterministic scan evidence. AI exchange recovery is a last resort after deterministic recovery and may only recommend allowlisted safe actions. It cannot trade, access private exchange secrets or modify production code.'),
        'results': ('📋 <b>RESULTS & DIAGNOSTICS</b>', 'Results are stored per user and can be reopened from history. Diagnostics show exchange health, failures, degraded data and validation warnings so an empty result is not confused with a failed scan.'),
        'privacy': ('🔒 <b>CONTACT & PRIVACY</b>', 'Contact Developer opens an in-bot form for name, email, phone and message. The bot sends the submitted form privately to configured administrator IDs instead of opening the developer personal Telegram profile. Absolute anonymity cannot be guaranteed because Telegram and your bot infrastructure still process messages.'),
    }
    title, body = pages.get(page, pages['scanner'])
    return title + '\n\n' + body


def _keyboard(page='scanner'):
    return kb([
        [('🔎 Scanner', 'help:scanner'), ('🏦 Exchanges', 'help:exchanges')],
        [('📊 Filters', 'help:filters'), ('🛡️ Validation', 'help:validation')],
        [('🔧 Recovery', 'help:recovery'), ('🧠 AI', 'help:ai')],
        [('📋 Results', 'help:results'), ('🔒 Privacy', 'help:privacy')],
        [('⬅️ Help', 'help:main'), ('🏠 Dashboard', 'home')],
    ])


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(_main(), parse_mode='HTML', reply_markup=kb([[('📖 Full Instructions', 'help:instructions')], [('👨‍💻 Contact Developer', 'contact:open')], [('🏠 Dashboard', 'home')]]))


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == 'help:main':
        await q.edit_message_text(_main(), parse_mode='HTML', reply_markup=kb([[('📖 Full Instructions', 'help:instructions')], [('👨‍💻 Contact Developer', 'contact:open')], [('🏠 Dashboard', 'home')]]))
    elif q.data == 'help:instructions':
        await q.edit_message_text(_instructions(), parse_mode='HTML', reply_markup=_keyboard())
    else:
        await q.edit_message_text(_instructions(q.data.split(':', 1)[1]), parse_mode='HTML', reply_markup=_keyboard(q.data.split(':', 1)[1]))
