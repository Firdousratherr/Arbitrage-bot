from __future__ import annotations
from .ccxt_adapter import CcxtAdapter


class LBankAdapter(CcxtAdapter):
    """LBank adapter. LBank-specific fallback/recovery belongs here."""
    pass


class XTAdapter(CcxtAdapter):
    """XT adapter. XT behavior is deliberately independent from LBank."""
    pass
