from .ccxt_adapter import CcxtAdapter


class XTAdapter(CcxtAdapter):
    """XT-specific adapter; never inherits LBank behavior."""
    async def get_tickers(self, symbols=None):
        return await super().get_tickers(symbols)
