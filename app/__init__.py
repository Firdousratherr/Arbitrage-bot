"""Telegram arbitrage scanner application."""

# Install the scan-progress presentation layer before main imports build_handlers.
# This keeps the scanner logic unchanged while allowing the Telegram UI to show the
# user's actual selected exchanges and a richer animated status card.
from . import scan_ui as _scan_ui

_scan_ui.install()
