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
    TARGETED_RECOVERY_EXCHANGES = {"xt", "lbank"}
    # Recovery is deliberately bounded. The goal is to restore useful common
    # markets without turning a scan into hundreds of extra API requests.
    TARGETED_RECOVERY_MAX_SYMBOLS = 40

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

    async def run_cycle(self, *, require_matching_user: bool = True, exchange_names: set[str] | None = None) -> list[Opportunity]:
        if exchange_names is None and require_matching_user:
            exchange_names = set()
            for user in await self.db.list_users("vip"):
                exchange_names.update(json.loads(user["selected_exchanges"] or "[]"))
        active_exchanges = {name: exchange for name, exchange in self.exchanges.items() if exchange_names is None or name in exchange_names}
        if len(active_exchanges) < 2:
            logger.warning("scan skipped: select at least two active exchanges")
            set_last_scan_diagnostics({"summary": {}, "gaps": []})
            return []

        fetched = await asyncio.gather(*(self._fetch(exchange) for exchange in active_exchanges.values()), return_exceptions=True)
        by_symbol: dict[str, list[Ticker]] = {}
        successful_exchanges = 0
        exchange_status: dict[str, dict] = {}
        observed_symbols: set[str] = set()

        for name, exchange, result in zip(active_exchanges.keys(), active_exchanges.values(), fetched):
            stats = getattr(exchange, "last_fetch_stats", {}) or {}
            last_error = getattr(exchange, "last_fetch_error", None)
            missing_symbols = getattr(exchange, "last_fetch_symbols", {}) or {}
            if isinstance(result, Exception):
                exchange_status[name] = {"status": "fetch failed", "error": f"{type(result).__name__}: {result}"}
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
                exchange_status[name] = {"status": "partial", "missing": missing_symbols}
            else:
                exchange_status[name] = {"status": "ok"}

        all_symbols = set(by_symbol)
        if all_symbols:
            recovery_tasks = []
            recovery_names = []
            for name, exchange in active_exchanges.items():
                exchange_id = getattr(exchange, "_exchange_id", name).lower()
                if exchange_id not in self.TARGETED_RECOVERY_EXCHANGES:
                    continue
                present = {
                    ticker.symbol
                    for ticker_list in by_symbol.values()
                    for ticker in ticker_list
                    if ticker.exchange == name and ticker.ask > 0 and ticker.bid > 0
                }
                missing_candidates = sorted(all_symbols - present)[: self.TARGETED_RECOVERY_MAX_SYMBOLS]
                if not missing_candidates:
                    continue
                missing_map = getattr(exchange, "last_fetch_symbols", {}) or {}
                for symbol in missing_candidates:
                    missing_map.setdefault(symbol, "not returned; targeted recovery failed")
                exchange.last_fetch_symbols = missing_map
                recovery_tasks.append(exchange.recover_symbols(missing_candidates, self.TARGETED_RECOVERY_MAX_SYMBOLS))
                recovery_names.append(name)

            if recovery_tasks:
                recovered_batches = await asyncio.gather(*recovery_tasks, return_exceptions=True)
                for name, recovered in zip(recovery_names, recovered_batches):
                    if isinstance(recovered, Exception):
                        logger.warning("%s targeted symbol recovery failed: %s", name, recovered)
                        continue
                    if recovered:
                        if exchange_status.get(name, {}).get("status") != "ok":
                            successful_exchanges += 1
                        for ticker in recovered:
                            by_symbol.setdefault(ticker.symbol, []).append(ticker)
                            observed_symbols.add(ticker.symbol)
                        exchange_status[name] = {
                            "status": "partial",
                            "missing": getattr(active_exchanges[name], "last_fetch_symbols", {}) or {},
                        }

        # Only symbols with usable quotes on at least two selected exchanges are
        # arbitrage candidates. A symbol existing on only one exchange is a
        # coverage difference, not an arbitrage data failure.
        valid_by_exchange: dict[str, set[str]] = {name: set() for name in active_exchanges}
        for symbol, tickers in by_symbol.items():
            for ticker in tickers:
                if ticker.ask > 0 and ticker.bid > 0 and ticker.exchange in valid_by_exchange:
                    valid_by_exchange[ticker.exchange].add(symbol)
        common_symbols = set.intersection(*valid_by_exchange.values()) if valid_by_exchange else set()

        coverage_gaps: list[dict] = []
        for symbol in sorted(observed_symbols):
            present = {name for name, symbols in valid_by_exchange.items() if symbol in symbols}
            if not present or len(present) >= len(active_exchanges):
                continue
            gaps = {}
            for name in active_exchanges:
                if name in present:
                    continue
                status = exchange_status.get(name, {})
                if status.get("status") == "fetch failed":
                    gaps[name] = f"fetch failed: {status.get('error', 'unknown error')}"
                elif symbol in status.get("missing", {}):
                    gaps[name] = status["missing"][symbol]
                elif status.get("status") == "no tickers returned":
                    gaps[name] = "no tickers returned"
                else:
                    gaps[name] = "not returned"
            if gaps:
                coverage_gaps.append({"symbol": symbol, "gaps": gaps})

        opportunities: list[Opportunity] = []
        positive_spread_symbols = 0
        detected_opportunities = 0
        filtered_opportunities = 0
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
            positive_spread_symbols += 1

            buy_fee_pct = 0.0
            sell_fee_pct = 0.0
            try:
                buy_exchange = self.exchanges.get(buy.exchange)
                sell_exchange = self.exchanges.get(sell.exchange)
                if buy_exchange and sell_exchange:
                    fees = await asyncio.gather(buy_exchange.get_taker_fee(symbol), sell_exchange.get_taker_fee(symbol), return_exceptions=True)
                    buy_fee_pct = float(fees[0]) * 100 if not isinstance(fees[0], Exception) else 0.1
                    sell_fee_pct = float(fees[1]) * 100 if not isinstance(fees[1], Exception) else 0.1
            except Exception:
                logger.debug("fee calculation failed for %s, using defaults", symbol)
                buy_fee_pct = 0.1
                sell_fee_pct = 0.1

            net_profit = raw_spread - buy_fee_pct - sell_fee_pct
            opportunity = Opportunity(symbol, buy.exchange, sell.exchange, buy.ask, sell.bid, raw_spread, net_profit, buy.quote_volume, sell.quote_volume)
            detected_opportunities += 1
            if require_matching_user and not await self._has_matching_users(opportunity):
                filtered_opportunities += 1
                continue
            opportunities.append(opportunity)

        summary = {
            "selected_exchanges": list(active_exchanges),
            "exchange_status": exchange_status,
            "returned_by_exchange": {name: len(symbols) for name, symbols in valid_by_exchange.items()},
            "common_markets": len(common_symbols),
            "positive_spreads": positive_spread_symbols,
            "opportunities_detected": detected_opportunities,
            "opportunities_filtered": filtered_opportunities,
            "opportunities_returned": len(opportunities),
            "opportunities_before_filters": detected_opportunities,
            "coverage_gap_symbols": len(coverage_gaps),
        }
        set_last_scan_diagnostics({"summary": summary, "gaps": coverage_gaps})

        try:
            purged = await self.db.purge_expired_opportunities()
            if purged:
                logger.debug("purged %s expired opportunity rows", purged)
        except Exception:
            logger.exception("failed to purge expired opportunities")
        gc.collect()
        await self.db.increment_stat("scans_run")
        logger.info("scan complete: %s/%s exchanges returned data, %s common markets, %s positive spreads, %s detected, %s filtered, %s returned, %s coverage gaps", successful_exchanges, len(active_exchanges), len(common_symbols), positive_spread_symbols, detected_opportunities, filtered_opportunities, len(opportunities), len(coverage_gaps))
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
