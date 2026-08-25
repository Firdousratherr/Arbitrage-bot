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
        "Live cross-exchange intelligence",
        [
            "🔎 <b>Scanner</b>     Find live price gaps",
            "🌐 <b>Exchanges</b>   Manage your route",
            "🎛️ <b>Filters</b>     Control signal quality",
            "📊 <b>Portfolio</b>   Track paper P/L",
            "🏆 <b>Leaderboard</b> Compare results",
            "",
            "🟢 <b>Scanner ready</b>",
            FOOTER,
        ],
    )
    keyboard = nav(
        ("🔎 Scan Now", "ui:scan"),
        ("🌐 Exchanges", "ui:exchanges"),
        ("🎛️ Filters", "ui:filters"),
        ("📊 Portfolio", "ui:portfolio"),
        ("🏆 Leaderboard", "ui:leaderboard"),
        ("👤 Account", "ui:status"),
    )
    return text, keyboard


def welcome() -> tuple[str, InlineKeyboardMarkup]:
    text = screen(
        "🚀 CRYPTO ARBITRAGE SCANNER",
        "Turn fragmented markets into actionable signals",
        [
            "⚡ Live exchange comparison",
            "🛡️ Transfer-route verification",
            "📈 Fee-aware opportunity filtering",
            "🎮 Risk-free paper trading",
            "",
            "<b>Ready to build your route?</b>",
        ],
    )
    return text, nav(("🚀 Start Setup", "ui:start"), ("ℹ️ How It Works", "ui:help"), columns=2)


def exchange_picker(selected: list[str], available: list[str]) -> tuple[str, InlineKeyboardMarkup]:
    chosen = set(selected)
    buttons = []
    for name in available:
        mark = "✅" if name in chosen else "▫️"
        buttons.append((f"{mark} {name}", f"ui:exchange:{name}"))
    buttons.append((f"✨ Done · {len(chosen)} selected", "ui:exchange:done"))
    buttons.append(("↩️ Back", "ui:dashboard"))
    return screen(
        "🌐 EXCHANGE ROUTE",
        "Select two or more exchanges",
        [f"Selected: <b>{len(chosen)} / {len(available)}</b>"],
    ), nav(*buttons, columns=2)


def settings_menu() -> tuple[str, InlineKeyboardMarkup]:
    return screen("🎛️ CONTROL CENTER", "Tune your scanner without memorising commands", ["Choose a category below."]), nav(
        ("📈 Profit & Spread", "ui:profit"),
        ("💧 Volume", "ui:volume"),
        ("👁 Watchlist", "ui:watchlist"),
        ("🚫 Blacklist", "ui:blacklist"),
        ("⏱️ Alerts", "ui:alerts"),
        ("⚠️ Loose Mode", "ui:loose"),
        ("♻️ Reset", "ui:reset"),
        ("↩️ Dashboard", "ui:dashboard"),
    )
