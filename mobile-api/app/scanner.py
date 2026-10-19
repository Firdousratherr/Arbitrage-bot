from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import time
from datetime import UTC, datetime
from typing import Any

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _positive(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _spot_market(market: dict[str, Any], quote_assets: set[str]) -> bool:
    if market.get("active") is False:
        return False
    if market.get("spot") is False or market.get("type") not in (None, "spot"):
        return False
    if any(market.get(flag) is True for flag in ("contract", "swap", "future", "option")):
        return False
    symbol = str(market.get("symbol") or "")
    if "/" not in symbol or ":" in symbol:
        return False
    quote = str(market.get("quote") or symbol.split("/", 1)[1]).upper()
    return quote in quote_assets


def _walk_buy(asks: list[list[float]], quote_budget: float) -> tuple[float, float]:
    remaining = quote_budget
    base_bought = 0.0
    quote_spent = 0.0
    for level in asks:
        if len(level) < 2:
            continue
        price, amount = _positive(level[0]), _positive(level[1])
        if price is None or amount is None:
            continue
        take_base = min(amount, remaining / price)
        if take_base <= 0:
            continue
        spent = take_base * price
        base_bought += take_base
        quote_spent += spent
        remaining -= spent
        if remaining <= max(1e-8, quote_budget * 1e-8):
            break
    return base_bought, quote_spent


def _walk_sell(bids: list[list[float]], base_amount: float) -> tuple[float, float]:
    remaining = base_amount
    base_sold = 0.0
    quote_received = 0.0
    for level in bids:
        if len(level) < 2:
            continue
        price, amount = _positive(level[0]), _positive(level[1])
        if price is None or amount is None:
            continue
        take_base = min(amount, remaining)
        if take_base <= 0:
            continue
        base_sold += take_base
        quote_received += take_base * price
        remaining -= take_base
        if remaining <= max(1e-12, base_amount * 1e-8):
            break
    return base_sold, quote_received


class MarketScanner:
    """Bounded, read-only public-market scanner. No authenticated trading methods are used."""

    DEFAULT_EXCHANGES = (
        "binance", "bybit", "okx", "kucoin", "gateio", "mexc", "bitget", "coinex"
    )

    def __init__(
        self,
        exchange_ids: list[str] | None = None,
        quote_assets: list[str] | None = None,
        scan_interval: int = 30,
        concurrency: int = 6,
        max_candidates: int = 40,
        max_notional_usd: float = 500.0,
        estimated_taker_fee: float = 0.001,
        min_24h_quote_volume: float = 5000.0,
        stale_after: int = 90,
        exchange_clients: dict[str, Any] | None = None,
    ):
        self.exchange_ids = list(dict.fromkeys(
            value.strip().lower() for value in (exchange_ids or self.DEFAULT_EXCHANGES) if value.strip()
        ))[:29]
        self.quote_assets = {value.strip().upper() for value in (quote_assets or ["USDT", "USDC"]) if value.strip()}
        self.scan_interval = max(10, int(scan_interval))
        self.concurrency = max(1, min(int(concurrency), 12))
        self.max_candidates = max(1, min(int(max_candidates), 100))
        self.max_notional_usd = max(20.0, float(max_notional_usd))
        self.estimated_taker_fee = max(0.0, min(float(estimated_taker_fee), 0.03))
        self.min_24h_quote_volume = max(0.0, float(min_24h_quote_volume))
        self.stale_after = max(30, int(stale_after))
        self.exchanges = exchange_clients if exchange_clients is not None else self._create_exchanges()
        self.markets_by_exchange: dict[str, dict[str, dict[str, Any]]] = {}
        self.status_by_exchange: dict[str, dict[str, Any]] = {
            name: {"exchange": name, "status": "starting", "market_count": 0, "ticker_count": 0, "error": None}
            for name in self.exchange_ids
        }
        self.tickers_by_exchange: dict[str, dict[str, dict[str, Any]]] = {}
        self.opportunities: list[dict[str, Any]] = []
        self.updated_at: str | None = None
        self.updated_epoch: float | None = None
        self.scan_duration_ms = 0
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._request_semaphore = asyncio.Semaphore(self.concurrency)
        self._scan_lock = asyncio.Lock()

    def _create_exchanges(self) -> dict[str, Any]:
        clients: dict[str, Any] = {}
        for name in self.exchange_ids:
            try:
                exchange_type = getattr(ccxt, name)
                clients[name] = exchange_type({"enableRateLimit": True, "timeout": 15000})
            except Exception as exc:
                self.status_by_exchange[name] = {
                    "exchange": name, "status": "unsupported", "market_count": 0,
                    "ticker_count": 0, "error": f"{type(exc).__name__}: {exc}",
                }
                logger.warning("Exchange %s is unavailable in this CCXT build: %s", name, exc)
        return clients

    async def start(self) -> None:
        await self.load_markets()
        self._task = asyncio.create_task(self._scan_loop(), name="mobile-market-scanner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.gather(
            *(exchange.close() for exchange in self.exchanges.values() if hasattr(exchange, "close")),
            return_exceptions=True,
        )

    async def load_markets(self) -> None:
        async def load_one(name: str, exchange: Any) -> None:
            async with self._request_semaphore:
                try:
                    await asyncio.wait_for(exchange.load_markets(), timeout=30)
                    all_markets = getattr(exchange, "markets", {}) or {}
                    spot = {
                        symbol: market for symbol, market in all_markets.items()
                        if _spot_market(market, self.quote_assets)
                    }
                    self.markets_by_exchange[name] = spot
                    state = self.status_by_exchange.setdefault(name, {"exchange": name})
                    state.update({
                        "status": "ready" if spot else "no-supported-markets",
                        "market_count": len(spot),
                        "error": None if spot else "No active spot markets for configured quote assets",
                    })
                except Exception as exc:
                    self.markets_by_exchange[name] = {}
                    state = self.status_by_exchange.setdefault(name, {"exchange": name})
                    state.update({
                        "status": "market-load-failed",
                        "market_count": 0,
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    logger.warning("Unable to load markets from %s: %s", name, exc)

        await asyncio.gather(*(load_one(name, exchange) for name, exchange in self.exchanges.items()))

    async def _fetch_tickers(self, name: str, exchange: Any) -> tuple[str, dict[str, dict[str, Any]], str | None]:
        async with self._request_semaphore:
            try:
                result = await asyncio.wait_for(exchange.fetch_tickers(), timeout=18)
                usable: dict[str, dict[str, Any]] = {}
                for symbol, ticker in (result or {}).items():
                    if symbol not in self.markets_by_exchange.get(name, {}):
                        continue
                    bid, ask = _positive(ticker.get("bid")), _positive(ticker.get("ask"))
                    if bid is None or ask is None:
                        continue
                    usable[symbol] = {
                        "bid": bid,
                        "ask": ask,
                        "quote_volume": float(ticker.get("quoteVolume") or 0.0),
                        "timestamp": ticker.get("timestamp"),
                    }
                return name, usable, None
            except Exception as exc:
                return name, {}, f"{type(exc).__name__}: {exc}"

    def _fee(self, exchange_name: str, symbol: str) -> tuple[float, str]:
        market = self.markets_by_exchange.get(exchange_name, {}).get(symbol, {})
        market_fee = market.get("taker")
        try:
            rate = float(market_fee)
            if math.isfinite(rate) and 0 <= rate <= 0.03:
                return rate, "exchange market metadata"
        except (TypeError, ValueError):
            pass
        exchange = self.exchanges.get(exchange_name)
        general = getattr(exchange, "fees", {}) if exchange else {}
        try:
            rate = float((general.get("trading") or {}).get("taker"))
            if math.isfinite(rate) and 0 <= rate <= 0.03:
                return rate, "exchange default fee metadata"
        except (TypeError, ValueError, AttributeError):
            pass
        return self.estimated_taker_fee, "configured fallback estimate"

    async def _order_book(self, exchange_name: str, symbol: str) -> dict[str, Any] | None:
        exchange = self.exchanges.get(exchange_name)
        if not exchange:
            return None
        async with self._request_semaphore:
            try:
                return await asyncio.wait_for(exchange.fetch_order_book(symbol, limit=20), timeout=12)
            except Exception as exc:
                logger.debug("Order book unavailable for %s %s: %s", exchange_name, symbol, exc)
                return None

    async def _price_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        symbol = candidate["symbol"]
        buy_exchange, sell_exchange = candidate["buy_exchange"], candidate["sell_exchange"]
        buy_book, sell_book = await asyncio.gather(
            self._order_book(buy_exchange, symbol),
            self._order_book(sell_exchange, symbol),
        )
        if not buy_book or not sell_book:
            return None
        asks = buy_book.get("asks") or []
        bids = sell_book.get("bids") or []
        if not asks or not bids:
            return None
        ask_depth = sum((_positive(level[0]) or 0) * (_positive(level[1]) or 0) for level in asks if len(level) >= 2)
        bid_depth = sum((_positive(level[0]) or 0) * (_positive(level[1]) or 0) for level in bids if len(level) >= 2)
        target = min(self.max_notional_usd, ask_depth, bid_depth)
        if target < 20:
            return None

        base_bought = quote_spent = base_sold = quote_received = 0.0
        # Reduce size if price movement across the visible book makes one leg unfillable.
        for _ in range(6):
            base_bought, quote_spent = _walk_buy(asks, target)
            base_sold, quote_received = _walk_sell(bids, base_bought)
            if base_bought > 0 and base_sold >= base_bought * 0.999999:
                break
            target *= 0.7
        if target < 20 or base_bought <= 0 or base_sold < base_bought * 0.999999:
            return None

        buy_fee, buy_fee_source = self._fee(buy_exchange, symbol)
        sell_fee, sell_fee_source = self._fee(sell_exchange, symbol)
        buy_fee_cost = quote_spent * buy_fee
        sell_fee_cost = quote_received * sell_fee
        gross_profit = quote_received - quote_spent
        estimate_before_transfer = quote_received - sell_fee_cost - quote_spent - buy_fee_cost
        size_usd = quote_spent
        pct = (estimate_before_transfer / size_usd * 100) if size_usd else 0.0
        ticker_buy = self.tickers_by_exchange.get(buy_exchange, {}).get(symbol, {})
        ticker_sell = self.tickers_by_exchange.get(sell_exchange, {}).get(symbol, {})
        raw_spread = (candidate["sell_bid"] - candidate["buy_ask"]) / candidate["buy_ask"] * 100
        buy_avg = quote_spent / base_bought
        sell_avg = quote_received / base_sold if base_sold else candidate["sell_bid"]
        depth_spread = (sell_avg - buy_avg) / buy_avg * 100 if buy_avg else raw_spread
        quote_volume = min(
            float(ticker_buy.get("quote_volume") or 0.0),
            float(ticker_sell.get("quote_volume") or 0.0),
        )
        if self.min_24h_quote_volume and quote_volume and quote_volume < self.min_24h_quote_volume:
            return None

        identifier_source = "|".join((symbol, buy_exchange, sell_exchange))
        identifier = hashlib.sha256(identifier_source.encode()).hexdigest()[:16]
        buy_url = (getattr(self.exchanges.get(buy_exchange), "urls", {}) or {}).get("www")
        sell_url = (getattr(self.exchanges.get(sell_exchange), "urls", {}) or {}).get("www")
        return {
            "id": identifier,
            "type": "cross-exchange",
            "symbol": symbol,
            "base_asset": symbol.split("/", 1)[0],
            "quote_asset": symbol.split("/", 1)[1],
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_exchange_url": buy_url,
            "sell_exchange_url": sell_url,
            "buy_price": buy_avg,
            "sell_price": sell_avg,
            "best_ask_at_scan": candidate["buy_ask"],
            "best_bid_at_scan": candidate["sell_bid"],
            "spread_pct": raw_spread,
            "depth_adjusted_spread_pct": depth_spread,
            "estimated_profit_pct_before_transfer": pct,
            "estimated_profit_usd_before_transfer": estimate_before_transfer,
            "gross_profit_usd": gross_profit,
            "estimated_size_usd": size_usd,
            "liquidity_usd": min(ask_depth, bid_depth, quote_volume or self.max_notional_usd),
            "buy_fee_pct": buy_fee * 100,
            "sell_fee_pct": sell_fee * 100,
            "buy_fee_usd_estimate": buy_fee_cost,
            "sell_fee_usd_estimate": sell_fee_cost,
            "fee_sources": {"buy": buy_fee_source, "sell": sell_fee_source},
            "withdrawal_fee_usd": None,
            "withdrawal_fee_status": "unknown",
            "transfer_network": None,
            "network_verification": "unknown",
            "contract_match": "unknown",
            "verification_status": "needs_manual_check",
            "estimate_scope": "after trading-fee estimates, before withdrawal/transfer costs",
            "warning": "Withdrawal fees, deposit/withdrawal status, token contract identity and transfer networks are not verified by this public-data scan. Check both exchanges manually.",
            "quote_volume_24h_usd": quote_volume,
            "timestamp": _utc_now(),
            "observed_at_epoch": time.time(),
        }

    async def refresh(self) -> None:
        async with self._scan_lock:
            started = time.monotonic()
            if not self.markets_by_exchange:
                await self.load_markets()
            tick_results = await asyncio.gather(
                *(self._fetch_tickers(name, exchange) for name, exchange in self.exchanges.items())
            )
            new_tickers: dict[str, dict[str, dict[str, Any]]] = {}
            for name, tickers, error in tick_results:
                new_tickers[name] = tickers
                state = self.status_by_exchange.setdefault(name, {"exchange": name})
                state.update({
                    "status": "ok" if not error and tickers else ("empty" if not error else "ticker-fetch-failed"),
                    "ticker_count": len(tickers),
                    "error": error,
                    "updated_at": _utc_now(),
                })
            self.tickers_by_exchange = new_tickers

            venues_by_symbol: dict[str, list[tuple[str, dict[str, Any]]]] = {}
            for name, tickers in new_tickers.items():
                for symbol, ticker in tickers.items():
                    venues_by_symbol.setdefault(symbol, []).append((name, ticker))

            candidates: list[dict[str, Any]] = []
            for symbol, venues in venues_by_symbol.items():
                if len(venues) < 2:
                    continue
                buy_name, buy_ticker = min(venues, key=lambda pair: pair[1]["ask"])
                sell_name, sell_ticker = max(venues, key=lambda pair: pair[1]["bid"])
                if buy_name == sell_name or buy_ticker["ask"] <= 0:
                    continue
                raw_spread = (sell_ticker["bid"] - buy_ticker["ask"]) / buy_ticker["ask"] * 100
                if raw_spread <= 0:
                    continue
                candidates.append({
                    "symbol": symbol,
                    "buy_exchange": buy_name,
                    "sell_exchange": sell_name,
                    "buy_ask": buy_ticker["ask"],
                    "sell_bid": sell_ticker["bid"],
                    "raw_spread": raw_spread,
                })
            candidates.sort(key=lambda item: item["raw_spread"], reverse=True)
            selected = candidates[:self.max_candidates]
            priced = await asyncio.gather(*(self._price_candidate(item) for item in selected))
            self.opportunities = sorted(
                [item for item in priced if item and item["estimated_profit_usd_before_transfer"] > 0],
                key=lambda item: item["estimated_profit_usd_before_transfer"],
                reverse=True,
            )
            self.updated_at = _utc_now()
            self.updated_epoch = time.time()
            self.scan_duration_ms = round((time.monotonic() - started) * 1000)
            self.last_error = None
            logger.info(
                "Mobile scanner cycle complete: %d ticker candidates, %d priced opportunities, %d ms",
                len(candidates), len(self.opportunities), self.scan_duration_ms,
            )

    async def _scan_loop(self) -> None:
        while True:
            start = time.monotonic()
            try:
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Mobile scanner cycle failed")
            elapsed = time.monotonic() - start
            await asyncio.sleep(max(1, self.scan_interval - elapsed))

    def is_stale(self) -> bool:
        return self.updated_epoch is None or time.time() - self.updated_epoch > self.stale_after

    def list_exchanges(self) -> list[dict[str, Any]]:
        return sorted(self.status_by_exchange.values(), key=lambda item: item.get("exchange", ""))

    def status(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at,
            "stale": self.is_stale(),
            "scan_interval_seconds": self.scan_interval,
            "scan_duration_ms": self.scan_duration_ms,
            "exchange_count": len(self.exchanges),
            "healthy_exchange_count": sum(1 for item in self.status_by_exchange.values() if item.get("status") == "ok"),
            "opportunity_count": len(self.opportunities),
            "last_error": self.last_error,
            "mode": "read-only public market data; no order placement",
        }

    def get_opportunity(self, identifier: str) -> dict[str, Any] | None:
        return next((item for item in self.opportunities if item["id"] == identifier), None)

    def list_opportunities(
        self,
        q: str = "",
        sort: str = "profit",
        min_spread: float = 0.0,
        min_liquidity: float = 0.0,
        quote: str = "",
        exchanges: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        items = list(self.opportunities)
        q, quote = q.strip().lower(), quote.strip().upper()
        selected_exchanges = {name.strip().lower() for name in (exchanges or []) if name.strip()}
        if q:
            items = [item for item in items if q in item["symbol"].lower() or q in item["buy_exchange"] or q in item["sell_exchange"]]
        if quote:
            items = [item for item in items if item["quote_asset"] == quote]
        if selected_exchanges:
            items = [
                item for item in items
                if item["buy_exchange"] in selected_exchanges and item["sell_exchange"] in selected_exchanges
            ]
        items = [
            item for item in items
            if item["spread_pct"] >= min_spread and item["liquidity_usd"] >= min_liquidity
        ]
        keys = {
            "spread": lambda item: item["spread_pct"],
            "profit": lambda item: item["estimated_profit_usd_before_transfer"],
            "liquidity": lambda item: item["liquidity_usd"],
            "latest": lambda item: item["observed_at_epoch"],
        }
        items.sort(key=keys.get(sort, keys["profit"]), reverse=True)
        return items[:max(1, min(int(limit), 200))]

    def list_triangular(self, exchange: str = "", q: str = "", min_profit_pct: float = 0.0, limit: int = 100) -> list[dict[str, Any]]:
        exchange_filter = exchange.strip().lower()
        query = q.strip().upper()
        loops: list[dict[str, Any]] = []
        now = _utc_now()
        for exchange_name, tickers in self.tickers_by_exchange.items():
            if exchange_filter and exchange_name != exchange_filter:
                continue
            markets = self.markets_by_exchange.get(exchange_name, {})
            graph: dict[str, list[dict[str, Any]]] = {}
            for symbol, ticker in tickers.items():
                market = markets.get(symbol, {})
                base = str(market.get("base") or symbol.split("/", 1)[0]).upper()
                quote = str(market.get("quote") or symbol.split("/", 1)[1]).upper()
                fee, fee_source = self._fee(exchange_name, symbol)
                graph.setdefault(quote, []).append({
                    "from": quote, "to": base, "rate": 1.0 / ticker["ask"] * (1.0 - fee),
                    "symbol": symbol, "side": "buy", "price": ticker["ask"], "fee": fee, "fee_source": fee_source,
                    "volume": ticker.get("quote_volume", 0),
                })
                graph.setdefault(base, []).append({
                    "from": base, "to": quote, "rate": ticker["bid"] * (1.0 - fee),
                    "symbol": symbol, "side": "sell", "price": ticker["bid"], "fee": fee, "fee_source": fee_source,
                    "volume": ticker.get("quote_volume", 0),
                })
            for start_asset in ("USDT", "USDC"):
                for first in graph.get(start_asset, []):
                    if first["to"] in (start_asset,) or first["volume"] < self.min_24h_quote_volume:
                        continue
                    for second in graph.get(first["to"], []):
                        if second["to"] in (start_asset, first["to"]) or second["volume"] < self.min_24h_quote_volume:
                            continue
                        for third in graph.get(second["to"], []):
                            if third["to"] != start_asset or third["volume"] < self.min_24h_quote_volume:
                                continue
                            market_ids = {first["symbol"], second["symbol"], third["symbol"]}
                            if len(market_ids) < 3:
                                continue
                            rate = first["rate"] * second["rate"] * third["rate"]
                            profit_pct = (rate - 1.0) * 100.0
                            if profit_pct <= max(0.0, min_profit_pct):
                                continue
                            route = f"{start_asset} → {first['to']} → {second['to']} → {start_asset}"
                            if query and query not in route.upper() and query not in exchange_name.upper():
                                continue
                            identifier = hashlib.sha256(f"{exchange_name}|{route}".encode()).hexdigest()[:16]
                            loops.append({
                                "id": identifier,
                                "type": "triangular",
                                "exchange": exchange_name,
                                "route": route,
                                "start_asset": start_asset,
                                "profit_pct_before_slippage": profit_pct,
                                "estimated_profit_usd_on_100": profit_pct,
                                "estimated_size_usd": 100.0,
                                "legs": [
                                    {key: leg[key] for key in ("from", "to", "symbol", "side", "price", "fee", "fee_source")}
                                    for leg in (first, second, third)
                                ],
                                "timestamp": now,
                                "estimate_scope": "ticker top-of-book estimate after fee assumptions; order-book depth, minimum order sizes and transfer costs are not included",
                                "verification_status": "needs_manual_check",
                            })
                            if len(loops) >= 500:
                                break
                        if len(loops) >= 500:
                            break
                    if len(loops) >= 500:
                        break
                if len(loops) >= 500:
                    break
        loops.sort(key=lambda item: item["profit_pct_before_slippage"], reverse=True)
        return loops[:max(1, min(int(limit), 200))]
