"""Telegram arbitrage scanner application."""

# Install the scan-progress presentation layer before main imports build_handlers.
from . import scan_ui as _scan_ui

_scan_ui.install()
