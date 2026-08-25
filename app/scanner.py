from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import logging
from datetime import UTC, datetime

from .arbitrage_features import OpportunityHistory, confidence_score, rank_score
from .db import Database
from .exchanges.base import Opportunity, Ticker
from .filters import matches, user_filters
from .scan_diagnostics import set_last_scan_diagnostics

logger = logging.getLogger(__name__)


class Scanner:
    TARGETED_RECOVERY_EXCHANGES = {"xt", "lbank"}

    def __init__(self, db: Database, exchanges: dict, interval: int, concurrency: int):
        self.db = db
        self.exchanges = exchanges
        self.interval = interval
        self.semaphore = asyncio.Semaphore(concurrency)
        self.task: asyncio.Task | None = None
        self.running = False
        self.history = OpportunityHistory(max_points=12)

    async def _fetch(self, exchange, symbols: list[str] | None = None) -> list[Ticker]:
        async with self.semaphore:
            return await exchange.fetch_tickers(symbols)

    async def _load_market_symbols(self, active_exchanges: dict) -> tuple[dict[str, set[str]], dict[str, str]]:
        async def _one(name: str, exchange):
            try:
                symbols = await exchange.get_active_spot_symbols()
                return name, set(symbols), None
            except Exception as exc:
                return name, set(), f"{type(exc).__name__}: {exc}"

        results = await asyncio.gather(*(_one(name, exchange) for name, exchange in active_exchanges.items()))
        market_symbols: dict[str, set[str]] = {}
        errors: dict[str, str] = {}
        for name, symbols, error in results:
            market_symbols[name] = symbols
            if error:
                errors[name] = error
        return market_symbols, errors

    @staticmethod
    def _merge_tickers(by_symbol: dict[str, list[Ticker]], tickers: list[Ticker]) -> int:
        added = 0
        for ticker in tickers:
            existing = by_symbol.setdefault(ticker.symbol, [])
            if any(item.exchange == ticker.exchange for item in existing):
                continue
            existing.append(ticker)
            added += 1
        return added

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

        market_symbols, market_errors = await self._load_market_symbols(active_exchanges)
        all_market_sets = [market_symbols[name] for name in active_exchanges]
        common_market_symbols = set.intersection(*all_market_sets) if all_market_sets and all(all_market_sets) else set()
        union_market_symbols = set().union(*all_market_sets) if all_market_sets else set()
        listing_difference_symbols = len(union_market_symbols - common_market_symbols)

        if not common_market_symbols:
            summary = {
                "selected_exchanges": list(active_exchanges),
                "exchange_status": {
                    name: ({"status": "market discovery failed", "error": market_errors[name]} if name in market_errors else {"status": "no active spot markets", "market_count": len(market_symbols[name])})
                    for name in active_exchanges
                },
                "listed_markets_by_exchange": {name: len(market_symbols[name]) for name in active_exchanges},
                "common_listed_markets": 0,
                "common_markets": 0,
                "positive_spreads": 0,
                "fee_positive_spreads": 0,
                "opportunities_detected": 0,
                "opportunities_filtered": 0,
                "opportunities_returned": 0,
                "coverage_gap_symbols": 0,
                "listing_difference_symbols": listing_difference_symbols,
            }
            set_last_scan_diagnostics({"summary": summary, "gaps": []})
            logger.warning("scan stopped: no common active spot markets; listed=%s errors=%s", {name: len(symbols) for name, symbols in market_symbols.items()}, market_errors)
            return []

        requested_symbols = sorted(common_market_symbols)
        fetched = await asyncio.gather(
            *(self._fetch(exchange, requested_symbols) for exchange in active_exchanges.values()),
            return_exceptions=True,
        )
        by_symbol: dict[str, list[Ticker]] = {}
        successful_exchanges = 0
        exchange_status: dict[str, dict] = {}

        for name, exchange, result in zip(active_exchanges.keys(), active_exchanges.values(), fetched):
            missing_symbols = getattr(exchange, "last_fetch_symbols", {}) or {}
            if isinstance(result, Exception):
                exchange_status[name] = {"status": "fetch failed", "error": f"{type(result).__name__}: {result}"}
                logger.warning("%s exchange scan failed: %s", exchange.name, result)
                continue
            successful_exchanges += 1
            self._merge_tickers(by_symbol, result)
            exchange_status[name] = {
                "status": "ok" if not missing_symbols else "partial",
                "market_count": len(market_symbols.get(name, set())),
                "requested_symbols": len(requested_symbols),
                "usable_tickers": len(result),
                "missing": dict(missing_symbols),
                "recovered": 0,
            }
            if name in market_errors:
                exchange_status[name].update({"status": "market discovery failed", "error": market_errors[name]})

        async def _recover(name: str, exchange):
            status = exchange_status.get(name, {})
            missing = list(status.get("missing", {}).keys())
            if not missing or name not in self.TARGETED_RECOVERY_EXCHANGES:
                return name, []
            recover = getattr(exchange, "recover_symbols", None)
            if not recover:
                return name, []
            try:
                recovered = await recover(missing)
                return name, recovered
            except Exception as exc:
                logger.warning("%s targeted recovery failed: %s: %s", name, type(exc).__name__, exc)
                return name, []

        recovery_results = await asyncio.gather(*(_recover(name, exchange) for name, exchange in active_exchanges.items()))
        for name, recovered in recovery_results:
            added = self._merge_tickers(by_symbol, recovered)
            if name in exchange_status:
                exchange_status[name]["recovered"] = added
                for ticker in recovered:
                    exchange_status[name].get("missing", {}).pop(ticker.symbol, None)
                if not exchange_status[name].get("missing") and exchange_status[name].get("status") == "partial":
                    exchange_status[name]["status"] = "ok"

        valid_by_exchange: dict[str, set[str]] = {name: set() for name in active_exchanges}
        for symbol, tickers in by_symbol.items():
            for ticker in tickers:
                if ticker.ask > 0 and ticker.bid > 0 and ticker.exchange in valid_by_exchange:
                    valid_by_exchange[ticker.exchange].add(symbol)
        common_symbols = set.intersection(*valid_by_exchange.values()) if valid_by_exchange else set()

        # Only report actionable data gaps for markets that are actually listed
        # on every selected exchange. A symbol listed on one exchange but absent
        # on another is normal market coverage, not a scan failure, and should
        # not flood the user with thousands of irrelevant "not listed" rows.
        coverage_gaps: list[dict] = []
        for symbol in sorted(common_market_symbols):
            gaps = {
                name: str((exchange_status.get(name, {}).get("missing", {}) or {}).get(symbol, "ticker data unavailable"))
                for name in active_exchanges
                if symbol not in valid_by_exchange.get(name, set())
            }
            if gaps:
                coverage_gaps.append({"symbol": symbol, "gaps": gaps})

        opportunities: list[Opportunity] = []
        positive_spread_symbols = 0
        fee_positive_spreads = 0
        detected_opportunities = 0
        filtered_opportunities = 0
        observed_at = datetime.now(UTC).isoformat()

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
            if net_profit > 0:
                fee_positive_spreads += 1

            history_key = f"{symbol}:{buy.exchange}:{sell.exchange}"
            history = self.history.add(history_key, raw_spread, net_profit)
            confidence = confidence_score(
                net_profit_pct=net_profit,
                buy_volume=buy.quote_volume,
                sell_volume=sell.quote_volume,
                trade_size=1000.0,
                freshness_seconds=0.0,
                transfer_verified=False,
                coverage_complete=symbol in common_symbols,
                executable_complete=False,
            )
            metadata = {
                "observed_at": observed_at,
                "history": history,
                "coverage_complete": symbol in common_symbols,
                "selected_exchange_count": len(active_exchanges),
                "confidence": confidence,
                "rank_score": rank_score(net_profit, confidence, None),
                "headline_only": True,
            }
            opportunity = Opportunity(
                symbol, buy.exchange, sell.exchange, buy.ask, sell.bid,
                raw_spread, net_profit, buy.quote_volume, sell.quote_volume,
                metadata=metadata,
            )
            detected_opportunities += 1
            if require_matching_user and not await self._has_matching_users(opportunity):
                filtered_opportunities += 1
                continue
            opportunities.append(opportunity)

        opportunities.sort(key=lambda item: item.metadata.get("rank_score", item.net_profit), reverse=True)
        summary = {
            "selected_exchanges": list(active_exchanges),
            "exchange_status": exchange_status,
            "returned_by_exchange": {name: len(symbols) for name, symbols in valid_by_exchange.items()},
            "listed_markets_by_exchange": {name: len(symbols) for name, symbols in market_symbols.items()},
            "common_listed_markets": len(common_market_symbols),
            "common_markets": len(common_symbols),
            "positive_spreads": positive_spread_symbols,
            "fee_positive_spreads": fee_positive_spreads,
            "opportunities_detected": detected_opportunities,
            "opportunities_filtered": filtered_opportunities,
            "opportunities_returned": len(opportunities),
            "opportunities_before_filters": detected_opportunities,
            "coverage_gap_symbols": len(coverage_gaps),
            "listing_difference_symbols": listing_difference_symbols,
        }
        set_last_scan_diagnostics({"summary": summary, "gaps": coverage_gaps})

        try:
            purged = await self.db.purge_expired_opportunities()
            if purged:
                logger.debug("purged %s expired opportunity rows", purged)
        except Exception:
            logger.exception("failed to purge expired opportunity rows")
        gc.collect()
        await self.db.increment_stat("scans_run")
        logger.info(
            "scan complete: %s/%s exchanges returned data, %s/%s listed markets overlap, %s ticker-common markets, %s raw price gaps, %s fee-positive gaps, %s detected, %s filtered, %s returned, %s actionable data gaps, %s listing differences",
            successful_exchanges, len(active_exchanges), len(common_market_symbols), len(union_market_symbols),
            len(common_symbols), positive_spread_symbols, fee_positive_spreads, detected_opportunities,
            filtered_opportunities, len(opportunities), len(coverage_gaps), listing_difference_symbols,
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
