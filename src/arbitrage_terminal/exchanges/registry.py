from __future__ import annotations

import logging

from .ccxt_adapter import CcxtAdapter
from .lbank import LBankAdapter
from .rate_limited import RateLimitedExchangeAdapter
from .self_healing import SelfHealingAdapter
from .xt import XTAdapter

logger = logging.getLogger(__name__)
SPECIAL = {'lbank': LBankAdapter, 'xt': XTAdapter}
CCXT_IDS = {'gateio': 'gate'}


def build_exchanges(names, credentials_provider, diagnostics=None, self_healing=True,
                    concurrency=3, recovery_advisor=None):
    result = {}
    diagnostics = diagnostics if diagnostics is not None else []
    for name in dict.fromkeys(str(n).strip().lower() for n in names if str(n).strip()):
        try:
            adapter_cls = SPECIAL.get(name, CcxtAdapter)
            exchange_id = CCXT_IDS.get(name, name)
            adapter = adapter_cls(exchange_id, public_name=name, credentials=credentials_provider(name))
            # Keep rate limiting/cache outside the raw CCXT adapter but inside
            # self-healing. This is important: automatic recovery must invalidate
            # the same cache layer that all scanner reads use.
            adapter = RateLimitedExchangeAdapter(adapter, concurrency=concurrency)
            if self_healing:
                adapter = SelfHealingAdapter(adapter, recovery_advisor=recovery_advisor)
            result[name] = adapter
        except Exception as exc:
            detail = str(exc)[:500]
            logger.exception('exchange adapter initialization failed', extra={'exchange': name})
            diagnostics.append({'exchange': name, 'operation': 'adapter_init', 'status': 'failed', 'error_type': type(exc).__name__, 'detail': detail})
    return result
