from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

ACTIONS = frozenset({"retry", "repair", "reload_markets", "invalidate_cache", "quarantine"})
CLASSIFICATIONS = frozenset({"transient", "rate_limit", "ccxt_client", "market_data", "authentication", "invalid_request", "unknown"})


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    classification: str
    confidence: float
    recommended_action: str
    safe_to_auto_repair: bool
    reason: str
    retry_delay_seconds: float = 0.0


class ExchangeRecoveryAdvisor:
    """AI advisor used only after deterministic exchange recovery fails."""

    def __init__(self, ai: Any, enabled: bool = False, timeout_seconds: float = 5.0, min_confidence: float = 0.80):
        self.ai = ai
        self.enabled = enabled
        self.timeout_seconds = max(1.0, min(15.0, timeout_seconds))
        self.min_confidence = max(0.0, min(1.0, min_confidence))

    async def advise(self, snapshot: dict[str, Any]) -> RecoveryDecision | None:
        if not self.enabled or self.ai is None or not getattr(self.ai, "configured", False):
            return None
        try:
            raw = await asyncio.wait_for(self.ai.advise_exchange_recovery(snapshot), timeout=self.timeout_seconds)
            decision = self._validate(raw)
            if decision is None or decision.confidence < self.min_confidence:
                return None
            return decision
        except Exception:
            return None

    def _validate(self, raw: Any) -> RecoveryDecision | None:
        if isinstance(raw, str):
            try: raw = json.loads(raw)
            except Exception: return None
        if not isinstance(raw, dict): return None
        classification = str(raw.get("classification", "unknown"))
        action = str(raw.get("recommended_action", "quarantine"))
        try:
            confidence = float(raw.get("confidence", 0.0)); delay = float(raw.get("retry_delay_seconds", 0.0))
        except (TypeError, ValueError): return None
        if classification not in CLASSIFICATIONS or action not in ACTIONS: return None
        if not 0.0 <= confidence <= 1.0 or not 0.0 <= delay <= 30.0: return None
        safe = bool(raw.get("safe_to_auto_repair", False))
        if classification in {"authentication", "invalid_request"}:
            safe = False
        return RecoveryDecision(classification, confidence, action, safe, str(raw.get("reason", "No reason supplied"))[:500], delay)
