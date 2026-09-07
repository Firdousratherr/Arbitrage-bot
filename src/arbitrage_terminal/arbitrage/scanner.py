from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

from arbitrage_terminal.domain.models import Diagnostic, ScanSnapshot, ScanState
from arbitrage_terminal.arbitrage.engine import pair_opportunity, transfer_compatibility
from arbitrage_terminal.exchanges.base import ExchangeError
from arbitrage_terminal.exchanges.circuit_breaker import CircuitBreaker


class ArbitrageScanner:
    def __init__(self, exchanges, concurrency=6, timeout=20):
        self.exchanges = exchanges
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.scan_deadline = min(max(timeout * 4, 60), 120)
        self.breakers = {name: CircuitBreaker() for name in exchanges}

    async def _call(self, fn):
        last = None
        for attempt in range(3):
            try:
                return await asyncio.wait_for(fn(), self.timeout), attempt
            except (ExchangeError, asyncio.TimeoutError) as exc:
                last = exc
                transient = (
                    isinstance(exc, asyncio.TimeoutError)
                    or getattr(exc, 'error_type', None) in {'network', 'rate_limit'}
                )
                if not transient or attempt == 2:
                    raise
                await asyncio.sleep(.25 * (2 ** attempt))
        raise last

    async def _one(self, name, adapter):
        started = time.perf_counter()
        diag = Diagnostic(name, 'market_data', '', 0, '')
        breaker = self.breakers.setdefault(name, CircuitBreaker())
        if not breaker.available:
            e = ExchangeError('Circuit breaker open; exchange temporarily suppressed.', 'circuit_open')
            diag.status = 'failed'
            diag.error_type = e.error_type
            diag.detail = str(e)
            diag.timestamp = datetime.now(timezone.utc).isoformat()
            return name, set(), [], diag, e
        try:
            async with self.semaphore:
                markets, r1 = await self._call(adapter.get_markets)
                symbols = {m.symbol for m in markets}
                tickers, r2 = await self._call(lambda: adapter.get_tickers(symbols))
            diag.status = 'ok'
            diag.retry_count = r1 + r2
            diag.latency_ms = (time.perf_counter() - started) * 1000
            diag.timestamp = datetime.now(timezone.utc).isoformat()
            breaker.success()
            return name, symbols, tickers, diag, None
        except Exception as e:
            breaker.failure()
            diag.status = 'failed'
            diag.latency_ms = (time.perf_counter() - started) * 1000
            diag.timestamp = datetime.now(timezone.utc).isoformat()
            diag.error_type = getattr(e, 'error_type', type(e).__name__)
            diag.http_status = getattr(e, 'http_status', None)
            diag.detail = str(e)[:500]
            return name, set(), [], diag, e

    async def scan(self, user_id, selected, filters, progress=None):
        async def emit(stage, **data):
            if progress:
                try:
                    await progress(stage, data)
                except Exception:
                    pass

        scan_id = uuid.uuid4().hex
        started = datetime.now(timezone.utc).isoformat()
        started_mono = time.monotonic()
        selected = list(dict.fromkeys(x.lower() for x in selected))
        missing = [n for n in selected if n not in self.exchanges]
        adapters = {n: self.exchanges[n] for n in selected if n in self.exchanges}
        if len(selected) < 2:
            return ScanSnapshot(
                scan_id, user_id, started, datetime.now(timezone.utc).isoformat(),
                selected, [], [], selected, 0, 0, 0, 0, ScanState.FAILED,
                errors=['At least two exchanges must be selected.']
            )

        await emit('start', selected=len(selected))

        async def one(name, adapter):
            r = await self._one(name, adapter)
            await emit(
                'exchange', exchange=name,
                status='healthy' if r[4] is None else 'failed',
                markets=len(r[1]), tickers=len(r[2]),
            )
            return r

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(one(n, a) for n, a in adapters.items())),
                timeout=self.scan_deadline,
            )
        except asyncio.TimeoutError:
            failed = selected
            diagnostics = [
                Diagnostic(
                    n, 'market_data', 'failed', 0,
                    datetime.now(timezone.utc).isoformat(),
                    error_type='scan_timeout',
                    detail=f'Exchange market scan exceeded {self.scan_deadline:.0f}s deadline.',
                )
                for n in selected
            ]
            await emit('complete', comparisons=0, opportunities=0, healthy=0, failed=len(failed))
            return ScanSnapshot(
                scan_id, user_id, started, datetime.now(timezone.utc).isoformat(),
                selected, [], [], failed, 0, 0, 0, 0, ScanState.FAILED,
                diagnostics=diagnostics,
                warnings=[f'Scan stopped after the {self.scan_deadline:.0f}s safety deadline.'],
                errors=['Scan timed out before market data collection completed.'],
            )

        diagnostics = [r[3] for r in results]
        for n in missing:
            diagnostics.append(
                Diagnostic(
                    n, 'adapter_init', 'failed', 0,
                    datetime.now(timezone.utc).isoformat(),
                    error_type='unavailable',
                    detail='Selected exchange is not available in the exchange registry.',
                )
            )
        healthy = [r[0] for r in results if r[4] is None]
        failed = [r[0] for r in results if r[4] is not None] + missing
        market_sets = {r[0]: r[1] for r in results}
        ticker_map = {}
        for n in healthy:
            for t in next(r[2] for r in results if r[0] == n):
                ticker_map.setdefault(t.symbol, []).append(t)

        union = set().union(*(market_sets.values())) if market_sets else set()
        warnings = ['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else []
        rejected = []
        opportunities = []
        comparisons = 0
        fee_maps = {}
        degraded = []
        remaining = max(1, self.scan_deadline - (time.monotonic() - started_mono))

        await emit('markets', healthy=len(healthy), failed=len(failed), markets=len(union), symbols=len(ticker_map))

        async def fee_one(n, a):
            try:
                return n, await asyncio.wait_for(a.get_trading_fees(set(ticker_map)), self.timeout), None
            except Exception as e:
                return n, {}, e

        if healthy and time.monotonic() - started_mono < self.scan_deadline:
            try:
                fee_results = await asyncio.wait_for(
                    asyncio.gather(*(fee_one(n, adapters[n]) for n in healthy)),
                    timeout=min(remaining, self.scan_deadline - (time.monotonic() - started_mono)),
                )
            except asyncio.TimeoutError:
                fee_results = [(n, {}, TimeoutError('Fee collection exceeded scan deadline.')) for n in healthy]
        else:
            fee_results = [(n, {}, TimeoutError('Scan deadline reached before fee collection.')) for n in healthy]

        for n, data, e in fee_results:
            fee_maps[n] = data
            if e:
                degraded.append(n)
                diagnostics.append(
                    Diagnostic(
                        n, 'trading_fees', 'degraded', 0,
                        datetime.now(timezone.utc).isoformat(),
                        error_type=getattr(e, 'error_type', type(e).__name__),
                        http_status=getattr(e, 'http_status', None),
                        detail=str(e)[:500],
                    )
                )
                warnings.append(
                    f'{n}: trading fee data unavailable; affected opportunities have unknown net profit.'
                )
        await emit('fees', completed=len(healthy), degraded=len(degraded))

        transfer_cache = {}
        transfer_tasks = {}

        async def get_transfer(exchange, asset):
            key = (exchange, asset)
            if key in transfer_cache:
                return transfer_cache[key]
            if key not in transfer_tasks:
                async def load():
                    try:
                        return await asyncio.wait_for(
                            adapters[exchange].get_transfer_info(asset), self.timeout
                        )
                    except Exception as e:
                        return e
                transfer_tasks[key] = asyncio.create_task(load())
            result = await transfer_tasks[key]
            if isinstance(result, Exception):
                diagnostics.append(
                    Diagnostic(
                        exchange, 'transfer_info', 'degraded', 0,
                        datetime.now(timezone.utc).isoformat(),
                        error_type=getattr(result, 'error_type', type(result).__name__),
                        http_status=getattr(result, 'http_status', None),
                        detail=f'{asset}: {str(result)[:350]}',
                    )
                )
                warnings.append(
                    f'{exchange}: transfer information unavailable for {asset}; strict validation rejected affected routes.'
                )
                transfer_cache[key] = {}
            else:
                transfer_cache[key] = result or {}
            return transfer_cache[key]

        await emit('candidates', message='Evaluating price gaps before network validation')
        timed_out = False
        for symbol, tickers in ticker_map.items():
            if time.monotonic() - started_mono >= self.scan_deadline:
                timed_out = True
                break
            valid = [
                t for t in tickers
                if t.bid > 0 and t.ask > 0 and t.bid == t.bid and t.ask == t.ask
            ]
            for buy in valid:
                for sell in valid:
                    if buy.exchange == sell.exchange:
                        continue
                    if time.monotonic() - started_mono >= self.scan_deadline:
                        timed_out = True
                        break
                    comparisons += 1
                    buy_fee = fee_maps.get(buy.exchange, {}).get(symbol)
                    sell_fee = fee_maps.get(sell.exchange, {}).get(symbol)
                    o = pair_opportunity(
                        buy, sell, buy_fee, sell_fee, None, filters.max_data_age,
                        {'network_available': False, 'contract_match': False, 'networks': []}
                    )
                    if not o:
                        continue
                    reason = filters.check(o, include_validation=False)
                    if reason:
                        rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': reason})
                        if comparisons % 500 == 0:
                            await emit('candidates', comparisons=comparisons, opportunities=len(opportunities), message='Still evaluating candidates…')
                        continue
                    if filters.validation_mode.lower() != 'loose':
                        bn = await get_transfer(buy.exchange, buy.base)
                        sn = await get_transfer(sell.exchange, sell.base)
                        network, contract, networks = transfer_compatibility(bn, sn)
                        transfer = {'network_available': network, 'contract_match': contract, 'networks': networks}
                        o = pair_opportunity(buy, sell, buy_fee, sell_fee, None, filters.max_data_age, transfer)
                        reason = filters.check(o, include_validation=True)
                        if reason:
                            rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': reason})
                            if comparisons % 500 == 0:
                                await emit('candidates', comparisons=comparisons, opportunities=len(opportunities), message='Still validating candidates…')
                            continue
                    opportunities.append(o)
                    if len(opportunities) % 5 == 0:
                        await emit(
                            'opportunity', count=len(opportunities), symbol=o.symbol,
                            buy=o.buy_exchange, sell=o.sell_exchange, gap=o.raw_gap,
                            comparisons=comparisons,
                        )
                    elif comparisons % 500 == 0:
                        await emit('candidates', comparisons=comparisons, opportunities=len(opportunities), message='Still evaluating candidates…')
                if timed_out:
                    break
            if timed_out:
                break

        if timed_out:
            warnings.append(f'Scan stopped at the {self.scan_deadline:.0f}s safety deadline; results are partial.')
        await emit('complete', comparisons=comparisons, opportunities=len(opportunities), healthy=len(healthy), failed=len(failed))

        opportunities.sort(
            key=lambda o: (
                o.estimated_net_profit is not None,
                o.estimated_net_profit or float('-inf'),
                o.confidence,
                min(o.buy_volume, o.sell_volume),
            ),
            reverse=True,
        )
        state = ScanState.SUCCESS if not failed and not timed_out else ScanState.PARTIAL
        if not healthy:
            state = ScanState.FAILED
        errors = ['No trustworthy market data was returned from selected exchanges.'] if state == ScanState.FAILED else []
        return ScanSnapshot(
            scan_id, user_id, started, datetime.now(timezone.utc).isoformat(),
            selected, healthy, degraded, failed, len(union),
            sum(len(r[2]) for r in results), comparisons, len(opportunities), state,
            opportunities, diagnostics, warnings, errors, rejected,
        )
