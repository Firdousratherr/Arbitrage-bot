from __future__ import annotations

from copy import deepcopy
from threading import Lock

_lock = Lock()
_last_scan_diagnostics: dict = {"summary": {}, "gaps": []}
_manual_scan_diagnostics: dict = {"summary": {}, "gaps": [], "filter_rejections": {}}
_last_filter_rejections: dict[str, str] = {}


def set_last_scan_diagnostics(diagnostics: dict | list[dict]) -> None:
    global _last_scan_diagnostics
    with _lock:
        if isinstance(diagnostics, list):
            _last_scan_diagnostics = {"summary": {}, "gaps": deepcopy(diagnostics)}
        else:
            _last_scan_diagnostics = deepcopy(diagnostics)


def set_manual_scan_diagnostics(diagnostics: dict) -> None:
    """Store the most recent user-triggered /scan separately from the background loop."""
    global _manual_scan_diagnostics
    with _lock:
        _manual_scan_diagnostics = deepcopy(diagnostics)


def set_filter_rejections(rejections: dict[str, str] | None) -> None:
    global _last_filter_rejections
    with _lock:
        _last_filter_rejections = deepcopy(rejections or {})


def get_last_scan_diagnostics() -> list[dict]:
    with _lock:
        filter_diagnostics = [
            {"symbol": symbol, "gaps": {"filter": reason}}
            for symbol, reason in _last_filter_rejections.items()
        ]
        return filter_diagnostics + deepcopy(_last_scan_diagnostics.get("gaps", []))


def get_last_scan_snapshot() -> dict:
    with _lock:
        snapshot = deepcopy(_last_scan_diagnostics)
        snapshot["filter_rejections"] = deepcopy(_last_filter_rejections)
        return snapshot


def get_manual_scan_snapshot() -> dict:
    """Return the latest completed manual /scan snapshot, if one exists."""
    with _lock:
        return deepcopy(_manual_scan_diagnostics)
