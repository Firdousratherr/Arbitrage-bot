"""Premium Telegram UI primitives used by the bot's interactive screens."""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

HEADER = "╭━━━━━━━━━━━━━━━━━━━━╮"
FOOTER = "╰━━━━━━━━━━━━━━━━━━━━╯"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━"


def screen(title: str, subtitle: str = "", body: list[str] | None = None) -> str:
    lines = [HEADER, f"│  {title}"]
    if subtitle:
        lines.append(f"│  {subtitle}")
    lines += [FOOTER, ""]
    if body:
        lines.extend(body)
    return "\n".join(lines)


def nav(*buttons: tuple[str, str], columns: int = 2) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for label, callback in buttons:
        row.append(InlineKeyboardButton(label, callback_data=callback))
        if len(row) == columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def dashboard() -> tuple[str, InlineKeyboardMarkup]:
    text = screen(
        "⚡ ARBITRAGE COMMAND CENTER",
        "Monitor markets, manage signals, and review simulations",
        [
            "🔎 <b>SCAN</b>       Live arbitrage opportunities",
            "🌐 <b>ROUTE</b>      Exchange selection",
            "🎛️ <b>CONTROL</b>    Filters, alerts & execution rules",
            "🎮 <b>SIMULATION</b> Paper trading & performance",
            "",
            "Choose a section below.",
        ],
    )
    keyboard = nav(
        ("🔎 Scan Now", "ui:scan"),
        ("🛠 Scan Info", "ui:scaninfo"),
        ("🌐 Exchanges", "ui:exchanges"),
        ("🎛️ Controls", "ui:filters"),
        ("🎮 Paper Trading", "ui:paper"),
        ("👤 Account", "ui:status"),
        ("🤖 AI Assistant", "ui:ai"),
        ("🏆 Leaderboard", "ui:leaderboard"),
        ("ℹ️ How It Works", "ui:help"),
        columns=2,
    )
    return text, keyboard


def welcome() -> tuple[str, InlineKeyboardMarkup]:
    text = screen(
        "🚀 CRYPTO ARBITRAGE SCANNER",
        "Live cross-exchange market intelligence",
        [
            "⚡ Compare live bid/ask prices",
            "💸 Account for exchange fees",
            "🛡️ Verify transfer routes",
            "🎮 Simulate execution without real funds",
            "",
            "<b>Start by selecting your exchanges.</b>",
        ],
    )
    return text, nav(("🚀 Start Setup", "ui:start"), ("ℹ️ How It Works", "ui:help"), columns=2)


def exchange_picker(selected: list[str], available: list[str]) -> tuple[str, InlineKeyboardMarkup]:
    chosen = set(selected)
    buttons = []
    for name in available:
        mark = "✅" if name in chosen else "▫️"
        buttons.append((f"{mark} {name}", f"ui:exchange:{name}"))
    exchange_rows = []
    for index in range(0, len(buttons), 2):
        pair = buttons[index:index + 2]
        exchange_rows.append([
            InlineKeyboardButton(label, callback_data=callback)
            for label, callback in pair
        ])
    exchange_rows.append([
        InlineKeyboardButton(f"✨ Done · {len(chosen)} selected", callback_data="ui:exchange:done")
    ])
    exchange_rows.append([
        InlineKeyboardButton("🏠 Dashboard", callback_data="ui:dashboard")
    ])
    return screen(
        "🌐 EXCHANGE ROUTE",
        "Select at least two exchanges for cross-market comparison",
        [
            f"Selected: <b>{len(chosen)}</b> / {len(available)}",
            "Tap an exchange to toggle it.",
        ],
    ), InlineKeyboardMarkup(exchange_rows)


def settings_menu() -> tuple[str, InlineKeyboardMarkup]:
    return screen(
        "🎛️ CONTROL CENTER",
        "Organized scanner and execution settings",
        [
            "📈 Profit & spread thresholds",
            "💧 Liquidity & trade size",
            "🛡️ Execution, fees & limits",
            "👁 Symbol lists",
            "🔔 Alerts",
            "⚠️ Transfer verification",
            "",
            "Choose a category to view its current values.",
        ],
    ), nav(
        ("📈 Profit & Spread", "ui:profit"),
        ("💧 Liquidity & Size", "ui:liquidity"),
        ("🛡️ Execution & Fees", "ui:execution"),
        ("👁 Symbols", "ui:symbols"),
        ("🔔 Alerts", "ui:alerts"),
        ("🎯 Signal Quality", "ui:quality"),
        ("⚠️ Verification", "ui:verification"),
        ("♻️ Reset All Filters", "ui:reset"),
        ("🏠 Dashboard", "ui:dashboard"),
        columns=2,
    )


def settings_category(filters: dict, category: str) -> tuple[str, InlineKeyboardMarkup]:
    back = ("↩️ Controls", "ui:filters")
    home = ("🏠 Dashboard", "ui:dashboard")
    if category == "profit":
        text = screen(
            "📈 PROFIT & SPREAD",
            "Signal thresholds used by the scanner",
            [
                f"Minimum profit  <b>{filters.get('min_profit', 0)}%</b>",
                f"Maximum profit  <b>{filters.get('max_profit', 100)}%</b>",
                f"Minimum spread  <b>{filters.get('min_spread', 0)}%</b>",
                f"Maximum spread  <b>{filters.get('max_spread', 100)}%</b>",
                f"Fee-adjusted     <b>{'ON' if filters.get('fee_adjusted', True) else 'OFF'}</b>",
                "",
                "Commands: /setminprofit /setmaxprofit",
                "/setminspread /setmaxspread",
                "/setfeeadjusted on|off",
            ],
        )
        return text, nav(
            (("💸 Fee Adjusted: ON" if filters.get("fee_adjusted", True) else "💸 Fee Adjusted: OFF"), "ui:toggle_fee"),
            back,
            home,
            columns=2,
        )
    if category == "liquidity":
        text = screen(
            "💧 LIQUIDITY & TRADE SIZE",
            "Market depth and position controls",
            [
                f"Minimum 24h volume  <b>${filters.get('min_volume', 10000):,.0f}</b>",
                f"Trade size           <b>${filters.get('trade_size', 1000):,.2f}</b>",
                f"Allowed range       <b>${filters.get('min_trade_size', 10):,.2f} → ${filters.get('max_trade_size', 100000):,.2f}</b>",
                f"Quote currency       <b>{filters.get('quote_currency', 'USDT')}</b>",
                "",
                "Commands: /setminvolume /setmintradesize",
                "/setmaxtradesize /settradesize",
                "/setquotecurrency USDT|USDC|BTC",
            ],
        )
        return text, nav(back, home, columns=2)
    if category == "execution":
        text = screen(
            "🛡️ EXECUTION & FEES",
            "Rules applied to executable paper trades and details",
            [
                f"Maximum slippage  <b>{filters.get('max_slippage', 2)}%</b>",
                f"Network fee        <b>${filters.get('network_fee', 0):,.2f}</b>",
                f"Daily paper cap   <b>${filters.get('daily_cap', 100000):,.2f}</b>",
                "",
                "Slippage is checked against live order-book depth.",
                "Network fee is deducted from simulated P/L.",
                "Daily cap limits cumulative paper-trade size.",
                "",
                "Commands: /setmaxslippage /setnetworkfee",
                "/setdailycap",
            ],
        )
        return text, nav(back, home, columns=2)
    if category == "symbols":
        watch = ", ".join(filters.get("watchlist", [])) if filters.get("watchlist") else "All pairs"
        blacklist = ", ".join(filters.get("blacklist", [])) if filters.get("blacklist") else "None"
        text = screen(
            "👁 SYMBOL LISTS",
            "Restrict or exclude specific markets",
            [
                f"Watchlist   <b>{watch}</b>",
                f"Blacklist   <b>{blacklist}</b>",
                "",
                "Commands:",
                "/watchlist add|remove SYMBOL",
                "/blacklist add|remove SYMBOL",
            ],
        )
        return text, nav(back, home, columns=2)
    if category == "alerts":
        paused = bool(filters.get("paused"))
        text = screen(
            "🔔 ALERTS",
            "Control automatic signal delivery",
            [
                f"Status       <b>{'PAUSED' if paused else 'LIVE'}</b>",
                f"Cooldown     <b>{filters.get('alert_cooldown', 300)}s</b>",
                f"Max results  <b>{filters.get('max_results', 10)}</b>",
                "",
                "Commands: /setalertfreq /setmaxresults",
            ],
        )
        return text, nav(
            (("▶️ Resume Alerts" if paused else "⏸️ Pause Alerts"), "ui:toggle_pause"),
            back,
            home,
            columns=2,
        )
    if category == "quality":
        text = screen(
            "🎯 SIGNAL QUALITY",
            "Control how persistent an opportunity must be before alerts",
            [
                f"Required observations  <b>{filters.get('min_stable_observations', 1)}</b>",
                "",
                "1 = alert on the first observation.",
                "Higher values require the same route to appear across more scan cycles.",
                "",
                "Command: /setstability 1–12",
            ],
        )
        return text, nav(back, home, columns=2)
    if category == "verification":
        loose = bool(filters.get("loose_mode"))
        text = screen(
            "⚠️ TRANSFER VERIFICATION",
            "Choose how strictly routes are checked",
            [
                f"Loose mode  <b>{'ON' if loose else 'OFF'}</b>",
                "",
                "OFF: transfer metadata must be verified before normal alerts.",
                "ON: alerts can be shown as unverified and require manual checking.",
                "",
                "Command: /loosemode on|off",
            ],
        )
        return text, nav(
            (("🛡️ Strict Mode" if loose else "⚠️ Enable Loose Mode"), "ui:toggle_loose"),
            back,
            home,
            columns=2,
        )
    return settings_menu()
