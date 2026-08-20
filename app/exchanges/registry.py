from __future__ import annotations

import logging

from .ccxt_adapter import CcxtExchangeAdapter

logger = logging.getLogger(__name__)

CCXT_NAMES = {
    "xt": "xt", "kucoin": "kucoin", "gateio": "gate",
    "mexc": "mexc", "okx": "okx", "htx": "htx", "kraken": "kraken", "bitget": "bitget", "bitrue": "bitrue",
    "lbank": "lbank", "coinbase": "coinbase", "bitfinex": "bitfinex",
    "phemex": "phemex", "cryptocom": "cryptocom", "poloniex": "poloniex",
}


def build_exchanges(names: list[str], credentials_provider) -> dict[str, CcxtExchangeAdapter]:
    result = {}
    for name in names:
        if name in CCXT_NAMES:
            try:
                result[name] = CcxtExchangeAdapter(CCXT_NAMES[name], credentials_provider(name), public_name=name)
            except Exception as exc:
                logger.error("exchange %s disabled: CCXT identifier %s failed to load: %s", name, CCXT_NAMES[name], exc)
        else:
            logger.warning("exchange %s is not supported by this CCXT version", name)
    return result
