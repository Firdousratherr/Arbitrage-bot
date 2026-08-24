from __future__ import annotations

from copy import deepcopy
from threading import Lock


_lock = Lock()
_last_scan_diagnostics: list[dict] = []


def set_last_scan_diagnostics(diagnostics: list[dict]) -> None:
    global _last_scan_diagnostics
    with _lock:
        _last_scan_diagnostics = deepcopy(diagnostics)


def get_last_scan_diagnostics() -> list[dict]:
    with _lock:
        return deepcopy(_last_scan_diagnostics)
