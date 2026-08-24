from __future__ import annotations

from html import escape


TOP = "╭────────────────────────╮"
BOTTOM = "╰────────────────────────╯"


def _num(value: int | float | None) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def format_scanner_dashboard(
    exchanges: list[str] | tuple[str, ...],
    *,
    stage: str,
    exchange_stats: dict[str, dict] | None = None,
    common_markets: int | None = None,
    candidates: int | None = None,
    opportunities: int | None = None,
    extra_line: str | None = None,
) -> str:
    """Render a Telegram-safe, data-driven scan progress dashboard.

    This helper intentionally contains no sleeps, API calls, or fake percentage
    progress. The caller can edit the same Telegram message as real scan data
    becomes available.
    """
    route = " ↔ ".join(escape(str(name).upper()) for name in exchanges) or "NO EXCHANGES"
    lines = [
        TOP,
        "│  🔍 MARKET SCANNER",
        BOTTOM,
        "",
        f"🌐 {route}",
        "",
        f"⚡ <b>{escape(stage)}</b>",
        "",
    ]

    stats = exchange_stats or {}
    for name in exchanges:
        row = stats.get(name, {})
        usable = row.get("usable")
        lines.append(f"📊 {escape(str(name).upper()):<10} {_num(usable)} usable")

    if common_markets is not None:
        lines.append(f"🔗 COMMON     {_num(common_markets)}")
    if candidates is not None:
        lines.append(f"🎯 CANDIDATES {_num(candidates)}")
    if opportunities is not None:
        lines.append(f"💰 OPPORTUNITIES {_num(opportunities)}")
    if extra_line:
        lines.extend(["", escape(extra_line)])

    return "\n".join(lines)


def scanner_stage_messages() -> tuple[str, ...]:
    """Canonical scan stages used by the scanner animation."""
    return (
        "📡 Fetching market data…",
        "🔄 Comparing markets…",
        "🧮 Calculating spreads…",
        "🛡️ Verifying opportunities…",
        "✅ Scan complete",
    )
