from __future__ import annotations

import logging

from .ccxt_adapter import CcxtAdapter
from .lbank import LBankAdapter
from .self_healing import SelfHealingAdapter
from .xt import XTAdapter

logger = logging.getLogger(__name__)
SPECIAL = {'lbank': LBankAdapter, 'xt': XTAdapter}
CCXT_IDS = {'gateio': 'gate'}


def build_exchanges(names, credentials_provider, diagnostics=None, self_healing=True):
    result = {}
    diagnostics = diagnostics if diagnostics is not None else []
    for name in dict.fromkeys(str(n).strip().lower() for n in names if str(n).strip()):
        try:
            adapter_cls = SPECIAL.get(name, CcxtAdapter)
            exchange_id = CCXT_IDS.get(name, name)
            adapter = adapter_cls(exchange_id, public_name=name, credentials=credentials_provider(name))
            result[name] = SelfHealingAdapter(adapter) if self_healing else adapter
        except Exception as exc:
            detail = str(exc)[:500]
            logger.exception("exchange adapter initialization failed", extra={"exchange": name})
            diagnostics.append({"exchange": name, "operation": "adapter_init", "status": "failed", "error_type": type(exc).__name__, "detail": detail})
    return result
