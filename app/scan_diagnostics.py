from __future__ import annotations

from copy import deepcopy
from threading import Lock

_lock = Lock()
_last_scan_diagnostics: dict = {"summary": {}, "gaps": []}
_last_filter_rejections: dict[str, str] = {}


def set_last_scan_diagnostics(diagnostics: dict | list[dict]) -> None:
    global _last_scan_diagnostics
    with _lock:
        if isinstance(diagnostics, list):
            _last_scan_diagnostics = {"summary": {}, "gaps": deepcopy(diagnostics)}
        else:
            _last_scan_diagnostics = deepcopy(diagnostics)


def set_filter_rejections(rejections: dict[str, str] | None) -> None:
    global _last_filter_rejections
    with _lock:
        _last_filter_rejections = deepcopy(rejections or {})


def get_last_scan_diagnostics() -> list[dict]:
    """Backward-compatible symbol-level diagnostics list.

    Also includes the exact user-filter rejection reasons captured during the
    latest /scan filter pass, so the normal scan result can explain why a
    detected opportunity was not returned.
    """
    with _lock:
        diagnostics = deepcopy(_last_scan_diagnostics.get("gaps", []))
        for symbol, reason in _last_filter_rejections.items():
            diagnostics.append({"symbol": symbol, "gaps": {"filter": reason}})
        return diagnostics


def get_last_scan_snapshot() -> dict:
    """Full latest-scan snapshot, including coverage and candidate counts."""
    with _lock:
        snapshot = deepcopy(_last_scan_diagnostics)
        snapshot["filter_rejections"] = deepcopy(_last_filter_rejections)
        return snapshot
