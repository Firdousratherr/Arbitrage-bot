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

        networks = (currency or {}).get('networks') or {}
        entries = networks.items() if isinstance(networks, dict) else enumerate(networks)
        for key, network in entries:
            network = network or {}
            if not isinstance(network, dict):
                continue
            for field in ('contractAddress', 'contract_address', 'tokenAddress', 'token_address', 'address'):
                add(network.get(field))

        if not addresses:
            return None
        return f"{base.upper()}|contract:{'|'.join(sorted(addresses))}"

    async def get_markets(self):
        self.last_market_symbols = set()
        self.last_market_asset_identities = {}
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        self.last_ticker_source = ''
        data = await self._call('markets', self.client.load_markets)
        self._markets = data or {}
        out = []
        currencies = getattr(self.client, 'currencies', None)
        if isinstance(currencies, dict):
            self._currencies = currencies
        for raw, m in self._markets.items():
            if not self._spot(m):
                continue
            try:
                sym, base, quote, _ = normalize_symbol(raw)
            except ValueError:
                continue
            currency = (self._currencies or {}).get(base) or (self._currencies or {}).get(str(m.get('baseId') or '').upper())
            identity = self._asset_identity(base, m, currency)
            out.append(Market(self.name, sym, base, quote, MarketType.SPOT, True, identity))
            if identity:
                self.last_market_asset_identities[sym] = identity
        self.last_market_symbols = {m.symbol for m in out}
        return out

    def _parse_tickers(self, name, raw, wanted):
        out = []
        identities = self.last_market_asset_identities
        for raw_symbol, t in (raw or {}).items():
            try:
                sym, base, quote, _ = normalize_symbol(raw_symbol)
                bid = float(t.get('bid'))
                ask = float(t.get('ask'))
                vol = float(t.get('quoteVolume') or 0)
            except (ValueError, TypeError, AttributeError):
                continue
            if wanted and sym.upper() not in wanted:
                continue
            if bid <= 0 or ask <= 0:
                continue
            ts = t.get('timestamp')
            stamp = datetime.fromtimestamp(float(ts) / 1000, timezone.utc) if ts else datetime.now(timezone.utc)
            out.append(Ticker(name, sym, base, quote, bid, ask, max(0.0, vol), stamp, identities.get(sym)))
        return out

    async def _fetch_best_quotes(self, wanted):
        has = getattr(self.client, 'has', {}) or {}
        if has.get('fetchBidsAsks') and hasattr(self.client, 'fetch_bids_asks'):
            self.last_ticker_source = 'fetch_bids_asks'
            return await self._call('bids_asks', self.client.fetch_bids_asks, sorted(wanted))

        self.last_ticker_source = 'fetch_tickers'
        try:
            return await self._call('tickers', self.client.fetch_tickers, sorted(wanted))
        except ExchangeError as exc:
            if getattr(exc, 'error_type', None) not in {'invalid_request', 'exchange_api'}:
                raise
            self.last_ticker_source = 'fetch_tickers_all'
            return await self._call('tickers_all', self.client.fetch_tickers)

    async def get_tickers(self, symbols=None):
        self.last_ticker_symbols = set()
        self.last_ticker_count = 0
        self.last_ticker_source = ''
        if not self._markets:
            await self.get_markets()

        wanted = {s.upper() for s in (symbols or self.last_market_symbols)}
        if not wanted:
            return []

        raw = await self._fetch_best_quotes(wanted)
        out = self._parse_tickers(self.name, raw, wanted)
        self.last_ticker_symbols = {t.symbol for t in out}
        self.last_ticker_count = len(out)
        return out

    async def get_orderbook(self, symbol, limit=10):
        return await self._call('orderbook', self.client.fetch_order_book, symbol, limit)

    async def get_trading_fees(self, symbols=None):
        if not self._markets:
            await self.get_markets()
        wanted = set(symbols or [])
        default = self.client.fees.get('trading', {}).get('taker')
        result = {}
        for raw, m in self._markets.items():
            if not self._spot(m):
                continue
            try:
                sym, *_ = normalize_symbol(raw)
            except ValueError:
                continue
            if wanted and sym not in wanted:
                continue
            fee = m.get('taker', default)
            if fee is not None:
                try:
                    result[sym] = float(fee) * 100
                except (TypeError, ValueError):
                    pass
        return result

    async def _get_currencies(self):
        if self._currencies is not None:
            return self._currencies
        cached = getattr(self.client, 'currencies', None)
        if isinstance(cached, dict) and cached:
            self._currencies = cached
            return cached
        return {}

    async def get_transfer_info(self, asset):
        currencies = await self._get_currencies()
        info = (currencies or {}).get(asset.upper(), {})
        networks = []
        for key, n in (info.get('networks') or {}).items():
            n = n or {}
            address = n.get('contractAddress') or n.get('contract_address') or n.get('address')
            networks.append({
                'network': key,
                'deposit': n.get('deposit') is not False,
                'withdraw': n.get('withdraw') is not False,
                'fee': n.get('fee'),
                'contract_address': address,
            })
        return {'available': bool(networks), 'asset': asset.upper(), 'networks': networks}

    async def close(self):
        await self.client.close()
