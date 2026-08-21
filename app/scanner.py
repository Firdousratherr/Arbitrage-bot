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
            return []
        fetched = await asyncio.gather(*(self._fetch(exchange) for exchange in active_exchanges.values()), return_exceptions=True)
        by_symbol: dict[str, list[Ticker]] = {}
        successful_exchanges = 0
        for exchange, result in zip(active_exchanges.values(), fetched):
            if isinstance(result, Exception):
                logger.warning("%s exchange scan failed: %s", exchange.name, result)
                continue
            if result:
                successful_exchanges += 1
            for ticker in result:
                by_symbol.setdefault(ticker.symbol, []).append(ticker)

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
            net_profit = raw_spread
            opportunity = Opportunity(symbol, buy.exchange, sell.exchange, buy.ask, sell.bid, raw_spread, net_profit, buy.quote_volume, sell.quote_volume)
            if raw_spread <= 0:
                continue
            if require_matching_user and not await self._has_matching_users(opportunity):
                continue
            opportunities.append(opportunity)
        gc.collect()
        await self.db.increment_stat("scans_run")
        logger.info(
            "scan complete: %s/%s exchanges returned data, %s symbols, %s opportunities",
            successful_exchanges,
            len(active_exchanges),
            len(by_symbol),
            len(opportunities),
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
