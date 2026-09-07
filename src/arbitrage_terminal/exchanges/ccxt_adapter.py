from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt
from ccxt.base.errors import BadRequest

from arbitrage_terminal.domain.models import Market, MarketType, Ticker
from arbitrage_terminal.domain.normalization import normalize_symbol
from .base import ExchangeAdapter, ExchangeError


class CcxtAdapter(ExchangeAdapter):
    def __init__(self, exchange_id, public_name=None, credentials=None):
        self.exchange_id = exchange_id
        self.name = public_name or exchange_id
        klass = getattr(ccxt, exchange_id, None)
        if klass is None:
            raise ValueError(f"CCXT exchange not available: {exchange_id}")
        self.client = klass({'enableRateLimit': True, 'timeout': 15000, **(credentials or {})})
        self._markets = {}
        self._currencies = None
        self._currencies_task = None
        self.last_diagnostics = {}
        self.last_market_symbols = set()
        self.last_market_asset_identities = {}
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        self.last_ticker_source = ''

    async def _call(self, op, fn, *args, **kwargs):
        started = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        except ccxt.RateLimitExceeded as e:
            raise ExchangeError(str(e), 'rate_limit', 429) from e
        except ccxt.AuthenticationError as e:
            raise ExchangeError(str(e), 'authentication', 401) from e
        except BadRequest as e:
            raise ExchangeError(str(e), 'invalid_request', 400) from e
        except ccxt.NetworkError as e:
            raise ExchangeError(str(e), 'network') from e
        except ccxt.ExchangeError as e:
            raise ExchangeError(str(e), 'exchange_api') from e
        finally:
            self.last_diagnostics[op] = {'latency_ms': (time.perf_counter() - started) * 1000}

    async def health_check(self):
        await self._call('health_check', self.client.load_markets)

    @staticmethod
    def _spot(m):
        return (
            m.get('active') is not False
            and (m.get('spot') is True or m.get('type') == 'spot')
            and not any(m.get(k) is True for k in ('contract', 'swap', 'future', 'option'))
        )

    @staticmethod
    def _asset_identity(base, market, currency):
        """Return a chain/contract identity when the exchange exposes one.

        Base/quote symbols are not sufficient to prove that two exchange tickers
        represent the same token. Prefer explicit contract addresses from the
        currency/network metadata; return None when the exchange exposes no
        globally comparable identity so legacy symbol matching can still work.
        """
        addresses = set()

        def add(value):
            if value is not None and str(value).strip():
                addresses.add(str(value).strip().lower())

        for source in (currency or {}, market or {}, (currency or {}).get('info') or {}, (market or {}).get('info') or {}):
            if isinstance(source, dict):
                for key in ('contractAddress', 'contract_address', 'tokenAddress', 'token_address', 'address'):
                    add(source.get(key))
