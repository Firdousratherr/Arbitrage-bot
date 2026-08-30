from __future__ import annotations
import logging
from .ccxt_adapter import CcxtAdapter
from .lbank import LBankAdapter
from .xt import XTAdapter

logger = logging.getLogger(__name__)
SPECIAL = {'lbank': LBankAdapter, 'xt': XTAdapter}


def build_exchanges(names, credentials_provider, diagnostics=None):
    result = {}
    diagnostics = diagnostics if diagnostics is not None else []
    for name in dict.fromkeys(str(n).strip().lower() for n in names if str(n).strip()):
        try:
            adapter = SPECIAL.get(name, CcxtAdapter)
            result[name] = adapter(name, public_name=name, credentials=credentials_provider(name))
        except Exception as exc:
            detail = str(exc)[:500]
            logger.exception("exchange adapter initialization failed", extra={"exchange": name})
            diagnostics.append({"exchange": name, "operation": "adapter_init", "status": "failed", "error_type": type(exc).__name__, "detail": detail})
    return result
