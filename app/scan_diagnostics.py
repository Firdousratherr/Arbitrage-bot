from __future__ import annotations

from copy import deepcopy
from threading import Lock

_lock = Lock()
_last_scan_diagnostics: dict = {"summary": {}, "gaps": []}


def set_last_scan_diagnostics(diagnostics: dict | list[dict]) -> None:
    global _last_scan_diagnostics
    with _lock:
        if isinstance(diagnostics, list):
            _last_scan_diagnostics = {"summary": {}, "gaps": deepcopy(diagnostics)}
        else:
            _last_scan_diagnostics = deepcopy(diagnostics)


def get_last_scan_diagnostics() -> list[dict]:
    """Backward-compatible symbol-level diagnostics list."""
    with _lock:
        return deepcopy(_last_scan_diagnostics.get("gaps", []))


def get_last_scan_snapshot() -> dict:
    """Full latest-scan snapshot, including coverage and candidate counts."""
    with _lock:
        return deepcopy(_last_scan_diagnostics)
