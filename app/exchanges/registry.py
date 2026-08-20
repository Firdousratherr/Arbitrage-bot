from __future__ import annotations

from .ccxt_adapter import CcxtExchangeAdapter

CCXT_NAMES = {
    "binance": "binance", "kucoin": "kucoin", "gateio": "gate", "bybit": "bybit",
    "mexc": "mexc", "okx": "okx", "htx": "htx", "kraken": "kraken", "bitget": "bitget",
    "bitmart": "bitmart", "lbank": "lbank", "coinbase": "coinbase", "bitfinex": "bitfinex",
    "phemex": "phemex", "cryptocom": "cryptocom", "poloniex": "poloniex",
}


def build_exchanges(names: list[str], credentials_provider) -> dict[str, CcxtExchangeAdapter]:
    result = {}
    for name in names:
        if name in CCXT_NAMES:
            result[name] = CcxtExchangeAdapter(CCXT_NAMES[name], credentials_provider(name))
    return result
