from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import logging
from datetime import UTC, datetime

from .db import Database
from .exchanges.base import Opportunity, Ticker
from .filters import matches, user_filters
from .scan_diagnostics import set_last_scan_diagnostics

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, db: Database, exchanges: dict, interval: int, concurrency: int):
        self.db = db
        self.exchanges = exchanges
        self.interval = interval
        self.semaphore = asyncio.Semaphore(concurrency)
        self.task: asyncio.Task | None = None
        self.running = False

    async def _fetch(self, exchange, symbols: list[str] | None = None) -> list[Ticker]:
        async with self.semaphore:
            return await exchange.fetch_tickers(symbols)

    async def run_cycle(
        self,
        *,
        require_matching_user: bool = True,
        exchange_names: set[str] | None = None,
    ) -> list[Opportunity]:
        if exchange_names is None and require_matching_user:
            exchange_names = set()
            for user in await self.db.list_users("vip"):
                exchange_names.update(json.loads(user["selected_exchanges"] or "[]"))
        active_exchanges = {
            name: exchange
            for name, exchange in self.exchanges.items()
            if exchange_names is None or name in exchange_names
        }
        if len(active_exchanges) < 2:
            logger.warning("scan skipped: select at least two active exchanges")
            set_last_scan_diagnostics([])
            return []

        fetched = await asyncio.gather(
            *(self._fetch(exchange) for exchange in active_exchanges.values()),
            return_exceptions=True,
        )
        by_symbol: dict[str, list[Ticker]] = {}
        successful_exchanges = 0
        exchange_status: dict[str, dict] = {}
        observed_symbols: set[str] = set()

        for name, exchange, result in zip(active_exchanges.keys(), active_exchanges.values(), fetched):
            stats = getattr(exchange, "last_fetch_stats", {}) or {}
            last_error = getattr(exchange, "last_fetch_error", None)
            missing_symbols = getattr(exchange, "last_fetch_symbols", {}) or {}
            observed_symbols.update(missing_symbols)
            if isinstance(result, Exception):
                exchange_status[name] = {
                    "status": "fetch failed",
                    "error": f"{type(result).__name__}: {result}",
                }
                logger.warning("%s exchange scan failed: %s", exchange.name, result)
                continue

            if result:
                successful_exchanges += 1
            for ticker in result:
                by_symbol.setdefault(ticker.symbol, []).append(ticker)
                observed_symbols.add(ticker.symbol)

            if last_error:
                exchange_status[name] = {"status": "fetch failed", "error": last_error}
            elif stats.get("raw", 0) == 0:
                exchange_status[name] = {"status": "no tickers returned"}
            elif missing_symbols:
                exchange_status[name] = {
                    "status": "partial",
                    "missing": missing_symbols,
                }
            else:
                exchange_status[name] = {"status": "ok"}

        diagnostics: list[dict] = []
        for symbol in sorted(observed_symbols):
            statuses: dict[str, str] = {}
            present_exchanges = {ticker.exchange for ticker in by_symbol.get(symbol, []) if ticker.ask > 0 and ticker.bid > 0}
            for name in active_exchanges:
                if name in present_exchanges:
                    statuses[name] = "OK"
                    continue
                status = exchange_status.get(name, {})
                if status.get("status") == "fetch failed":
                    statuses[name] = f"fetch failed: {status.get('error', 'unknown error')}"
                elif symbol in status.get("missing", {}):
                    statuses[name] = status["missing"][symbol]
                elif status.get("status") == "no tickers returned":
                    statuses[name] = "no tickers returned"
                else:
                    statuses[name] = "not returned"

            gaps = {name: reason for name, reason in statuses.items() if reason != "OK"}
            if gaps and present_exchanges:
                diagnostics.append({"symbol": symbol, "gaps": gaps})

        set_last_scan_diagnostics(diagnostics)

        opportunities = []
        for symbol, tickers in by_symbol.items():
            valid_tickers = [ticker for ticker in tickers if ticker.ask > 0 and ticker.bid > 0]
            if len(valid_tickers) < 2:
                continue
            pairs = [(buy, sell) for buy in valid_tickers for sell in valid_tickers if buy.exchange != sell.exchange]
            if not pairs:
                continue
            buy, sell = max(pairs, key=lambda pair: (pair[1].bid - pair[0].ask) / pair[0].ask)
            raw_spread = ((sell.bid - buy.ask) / buy.ask) * 100
            if raw_spread <= 0:
                continue

            # Calculate fee-adjusted net profit
            buy_fee_pct = 0.0
            sell_fee_pct = 0.0
            try:
                buy_exchange = self.exchanges.get(buy.exchange)
                sell_exchange = self.exchanges.get(sell.exchange)
                if buy_exchange and sell_exchange:
                    fees = await asyncio.gather(
                        buy_exchange.get_taker_fee(symbol),
                        sell_exchange.get_taker_fee(symbol),
                        return_exceptions=True,
                    )
                    buy_fee_pct = float(fees[0]) * 100 if not isinstance(fees[0], Exception) else 0.1
                    sell_fee_pct = float(fees[1]) * 100 if not isinstance(fees[1], Exception) else 0.1
            except Exception:
                logger.debug("fee calculation failed for %s, using defaults", symbol)
                buy_fee_pct = 0.1
                sell_fee_pct = 0.1

            net_profit = raw_spread - buy_fee_pct - sell_fee_pct
            opportunity = Opportunity(
                symbol,
                buy.exchange,
                sell.exchange,
                buy.ask,
                sell.bid,
                raw_spread,
                net_profit,
                buy.quote_volume,
                sell.quote_volume,
            )
            if require_matching_user and not await self._has_matching_users(opportunity):
                continue
            opportunities.append(opportunity)
        try:
            purged = await self.db.purge_expired_opportunities()
            if purged:
                logger.debug("purged %s expired opportunity rows", purged)
        except Exception:
            logger.exception("failed to purge expired opportunities")
        gc.collect()
        await self.db.increment_stat("scans_run")
        logger.info(
            "scan complete: %s/%s exchanges returned data, %s symbols, %s opportunities, %s symbol gaps",
            successful_exchanges,
            len(active_exchanges),
            len(by_symbol),
            len(opportunities),
            len(diagnostics),
        )
        return opportunities

    async def _has_matching_users(self, opportunity: Opportunity) -> bool:
        for user in await self.db.list_users("vip"):
            selected = json.loads(user["selected_exchanges"] or "[]")
            if opportunity.buy_exchange not in selected or opportunity.sell_exchange not in selected:
                continue
            filters = user_filters(user)
            if not filters["paused"] and matches(opportunity, filters):
                return True
        return False

    async def loop(self, alert_callback) -> None:
        self.running = True
        while self.running:
            try:
                opportunities = await self.run_cycle()
                await alert_callback(opportunities)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("scanner cycle failed")
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        self.running = False
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.gather(*(exchange.close() for exchange in self.exchanges.values()), return_exceptions=True)


def opportunity_id(opportunity: Opportunity) -> str:
    value = f"{opportunity.symbol}:{opportunity.buy_exchange}:{opportunity.sell_exchange}:{opportunity.buy_price}:{opportunity.sell_price}:{datetime.now(UTC).timestamp()}"
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()[:16]
