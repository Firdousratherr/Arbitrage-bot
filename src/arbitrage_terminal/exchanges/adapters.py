from .ccxt_adapter import CcxtAdapter


class LBankAdapter(CcxtAdapter):
    """LBank-specific adapter; exchange-specific behavior stays here."""
    async def get_tickers(self, symbols=None):
        return await super().get_tickers(symbols)


class XTAdapter(CcxtAdapter):
    """XT-specific adapter; deliberately independent from LBank."""
    async def get_tickers(self, symbols=None):
        return await super().get_tickers(symbols)
