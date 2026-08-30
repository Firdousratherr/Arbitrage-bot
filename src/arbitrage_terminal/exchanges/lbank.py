from .ccxt_adapter import CcxtAdapter


class LBankAdapter(CcxtAdapter):
    """LBank-specific adapter; exchange quirks stay inside this class."""
    async def get_tickers(self, symbols=None):
        return await super().get_tickers(symbols)
