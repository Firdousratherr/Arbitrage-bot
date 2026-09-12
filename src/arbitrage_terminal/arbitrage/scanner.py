from __future__ import annotations

import asyncio
import math
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from arbitrage_terminal.domain.models import Diagnostic, ScanSnapshot, ScanState
from arbitrage_terminal.arbitrage.engine import pair_opportunity, transfer_compatibility, withdrawal_cost_pct
from arbitrage_terminal.arbitrage.market_depth import round_trip
from arbitrage_terminal.exchanges.base import ExchangeError
from arbitrage_terminal.exchanges.circuit_breaker import CircuitBreaker


class ArbitrageScanner:
    def __init__(self, exchanges, concurrency=6, timeout=20):
        self.exchanges = exchanges
        self.semaphore = asyncio.Semaphore(concurrency)
        self.concurrency = max(1, concurrency)
        self.timeout = timeout
        self.scan_deadline = min(max(timeout * 6, 90), 120)
        self.network_validation_concurrency = min(8, max(2, self.concurrency))
        self.network_validation_budget = min(12.0, max(5.0, timeout * 0.6))
        self.orderbook_concurrency = min(8, max(2, self.concurrency))
        self.orderbook_budget = min(20.0, max(5.0, timeout * 0.8))
        self.breakers = {name: CircuitBreaker() for name in exchanges}

    async def _call(self, fn, deadline=None):
        last = None
        for attempt in range(3):
            try:
                timeout = self.timeout
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise asyncio.TimeoutError
                    timeout = min(timeout, remaining)
                return await asyncio.wait_for(fn(), timeout), attempt
            except (ExchangeError, asyncio.TimeoutError) as exc:
                last = exc
                transient = isinstance(exc, asyncio.TimeoutError) or getattr(exc, 'error_type', None) in {'network', 'rate_limit'}
                if not transient or attempt == 2: raise
                remaining = None if deadline is None else deadline - time.monotonic()
                sleep_for = .25 * (2 ** attempt)
                if remaining is not None and remaining <= sleep_for: raise asyncio.TimeoutError
                await asyncio.sleep(sleep_for)
        raise last

    async def _load_markets_one(self, name, adapter, exchange_budget):
        started = time.perf_counter(); diag = Diagnostic(name, 'market_data', '', 0, '')
        breaker = self.breakers.setdefault(name, CircuitBreaker())
        if not breaker.available:
            e = ExchangeError('Circuit breaker open; exchange temporarily suppressed.', 'circuit_open')
            diag.status = 'failed'; diag.error_type = e.error_type; diag.detail = str(e); diag.timestamp = datetime.now(timezone.utc).isoformat()
            return name, set(), diag, e
        try:
            async with self.semaphore:
                deadline = time.monotonic() + exchange_budget
                markets, retries = await self._call(adapter.get_markets, deadline)
            symbols = {m.symbol for m in markets}; diag.status = 'ok'; diag.retry_count = retries
            diag.latency_ms = (time.perf_counter() - started) * 1000; diag.timestamp = datetime.now(timezone.utc).isoformat(); breaker.success()
            return name, symbols, diag, None
        except Exception as e:
            breaker.failure(); diag.status = 'failed'; diag.latency_ms = (time.perf_counter() - started) * 1000; diag.timestamp = datetime.now(timezone.utc).isoformat()
            diag.error_type = getattr(e, 'error_type', type(e).__name__); diag.http_status = getattr(e, 'http_status', None); diag.detail = str(e)[:500]
            return name, set(), diag, e

    async def _load_tickers_one(self, name, adapter, symbols, exchange_budget):
        started = time.perf_counter()
        try:
            async with self.semaphore:
                deadline = time.monotonic() + exchange_budget
                tickers, retries = await self._call(lambda: adapter.get_tickers(symbols), deadline)
            return name, tickers, retries, (time.perf_counter() - started) * 1000, None
        except Exception as e:
            return name, [], 0, (time.perf_counter() - started) * 1000, e

    async def scan(self, user_id, selected, filters, progress=None):
        async def emit(stage, **data):
            if progress:
                try: await progress(stage, data)
                except Exception: pass

        scan_id = uuid.uuid4().hex; started = datetime.now(timezone.utc).isoformat(); started_mono = time.monotonic()
        selected = list(dict.fromkeys(x.lower() for x in selected)); missing = [n for n in selected if n not in self.exchanges]
        adapters = {n: self.exchanges[n] for n in selected if n in self.exchanges}
        if len(selected) < 2:
            return ScanSnapshot(scan_id, user_id, started, datetime.now(timezone.utc).isoformat(), selected, [], [], selected, 0, 0, 0, 0, ScanState.FAILED, errors=['At least two exchanges must be selected.'])

        await emit('start', selected=len(selected)); waves = max(1, math.ceil(len(adapters) / self.concurrency)); exchange_budget = max(5.0, min(self.timeout * 2.0, (self.scan_deadline - 2.0) / waves))

        async def load_market(name, adapter):
            await emit('exchange', exchange=name, status='loading', markets=0, tickers=0)
            r = await self._load_markets_one(name, adapter, exchange_budget)
            await emit('exchange', exchange=name, status='healthy' if r[3] is None else 'failed', markets=len(r[1]), tickers=0); return r

        try:
            market_results = await asyncio.wait_for(asyncio.gather(*(load_market(n, a) for n, a in adapters.items())), timeout=self.scan_deadline)
        except asyncio.TimeoutError:
            failed = selected; diagnostics = [Diagnostic(n, 'market_data', 'failed', 0, datetime.now(timezone.utc).isoformat(), error_type='scan_timeout', detail=f'Exchange market scan exceeded {self.scan_deadline:.0f}s deadline.') for n in selected]
            await emit('complete', comparisons=0, opportunities=0, healthy=0, failed=len(failed))
            return ScanSnapshot(scan_id, user_id, started, datetime.now(timezone.utc).isoformat(), selected, [], [], failed, 0, 0, 0, 0, ScanState.FAILED, diagnostics=diagnostics, warnings=[f'Scan stopped after the {self.scan_deadline:.0f}s safety deadline.'], errors=['Scan timed out before market data collection completed.'])

        diagnostics = [r[2] for r in market_results]
        for n in missing: diagnostics.append(Diagnostic(n, 'adapter_init', 'failed', 0, datetime.now(timezone.utc).isoformat(), error_type='unavailable', detail='Selected exchange is not available in the exchange registry.'))
        healthy = [r[0] for r in market_results if r[3] is None]; failed = [r[0] for r in market_results if r[3] is not None] + missing
        market_sets = {r[0]: r[1] for r in market_results}; union = set().union(*(market_sets.values())) if market_sets else set(); symbol_counts = {}
        for symbols in market_sets.values():
            for symbol in symbols: symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        shared_symbols = {symbol for symbol, count in symbol_counts.items() if count >= 2}; symbols_by_exchange = {name: (market_sets.get(name, set()) & shared_symbols) for name in healthy}
        await emit('markets', healthy=len(healthy), failed=len(failed), markets=len(union), symbols=len(shared_symbols))

        ticker_budgets = {}; remaining_scan = max(1.0, self.scan_deadline - (time.monotonic() - started_mono))
        for name in healthy:
            count = len(symbols_by_exchange.get(name, set()))
            if name == 'lbank' and count:
                estimated = count / 7.0 + 8.0; ticker_budgets[name] = min(remaining_scan, max(exchange_budget, min(90.0, estimated)))
            else: ticker_budgets[name] = min(remaining_scan, exchange_budget)

        if healthy and time.monotonic() - started_mono < self.scan_deadline:
            try:
                ticker_results = await asyncio.wait_for(asyncio.gather(*(
                    self._load_tickers_one(name, adapters[name], symbols_by_exchange[name], ticker_budgets[name]) for name in healthy)),
                    timeout=max(1.0, self.scan_deadline - (time.monotonic() - started_mono)))
            except asyncio.TimeoutError:
                ticker_results = [(name, [], 0, 0.0, asyncio.TimeoutError('Ticker collection exceeded scan deadline.')) for name in healthy]
        else: ticker_results = [(name, [], 0, 0.0, asyncio.TimeoutError('Scan deadline reached before ticker collection.')) for name in healthy]

        ticker_map = {}; ticker_counts = {}
        for name, tickers, retries, latency_ms, error in ticker_results:
            ticker_counts[name] = len(tickers)
            if error:
                if name in healthy: healthy.remove(name)
                if name not in failed: failed.append(name)
                diagnostics.append(Diagnostic(name, 'ticker_data', 'failed', latency_ms, datetime.now(timezone.utc).isoformat(), error_type=getattr(error, 'error_type', type(error).__name__), http_status=getattr(error, 'http_status', None), detail=str(error)[:500])); continue
            for t in tickers: ticker_map.setdefault(t.symbol, []).append(t)
            for diag in diagnostics:
                if diag.exchange == name and diag.operation == 'market_data': diag.retry_count += retries; diag.latency_ms += latency_ms; break
            await emit('exchange', exchange=name, status='healthy', markets=len(market_sets.get(name, set())), tickers=len(tickers))

        failed = list(dict.fromkeys(failed)); warnings = ['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else []
        rejected = []; opportunities = []; comparisons = 0; fee_maps = {}; degraded = []; remaining = max(1, self.scan_deadline - (time.monotonic() - started_mono))

        async def fee_one(n, a):
            try: return n, await asyncio.wait_for(a.get_trading_fees(set(ticker_map)), self.timeout), None
            except Exception as e: return n, {}, e

        if healthy and ticker_map and time.monotonic() - started_mono < self.scan_deadline:
            try: fee_results = await asyncio.wait_for(asyncio.gather(*(fee_one(n, adapters[n]) for n in healthy)), timeout=min(remaining, self.scan_deadline - (time.monotonic() - started_mono)))
            except asyncio.TimeoutError: fee_results = [(n, {}, TimeoutError('Fee collection exceeded scan deadline.')) for n in healthy]
        else: fee_results = [(n, {}, TimeoutError('No usable ticker data before fee collection.')) for n in healthy]
        for n, data, e in fee_results:
            fee_maps[n] = data
            if e:
                degraded.append(n); diagnostics.append(Diagnostic(n, 'trading_fees', 'degraded', 0, datetime.now(timezone.utc).isoformat(), error_type=getattr(e, 'error_type', type(e).__name__), http_status=getattr(e, 'http_status', None), detail=str(e)[:500])); warnings.append(f'{n}: trading fee data unavailable; affected opportunities have unknown net profit.')
        await emit('fees', completed=len(healthy), degraded=len(degraded))

        orderbook_cache = {}; orderbook_tasks = {}; orderbook_semaphore = asyncio.Semaphore(self.orderbook_concurrency)
        async def get_orderbook(exchange, symbol):
            key = (exchange, symbol)
            if key in orderbook_cache: return orderbook_cache[key]
            if key not in orderbook_tasks:
                async def load():
                    async with orderbook_semaphore:
                        remaining_scan = self.scan_deadline - (time.monotonic() - started_mono)
                        if remaining_scan <= 0: return TimeoutError('Order-book validation deadline reached.')
                        try:
                            book, _ = await self._call(lambda: adapters[exchange].get_orderbook(symbol, limit=20), time.monotonic() + min(self.timeout, remaining_scan)); return book
                        except Exception as e: return e
                orderbook_tasks[key] = asyncio.create_task(load())
            task = orderbook_tasks[key]; remaining_scan = self.scan_deadline - (time.monotonic() - started_mono)
            if remaining_scan <= 0: task.cancel(); orderbook_tasks.pop(key, None); return TimeoutError('Order-book validation deadline reached.')
            try: result = await asyncio.wait_for(task, timeout=min(self.timeout, remaining_scan))
            except asyncio.TimeoutError: task.cancel(); orderbook_tasks.pop(key, None); result = TimeoutError(f'Order-book deadline reached for {exchange} {symbol}.')
            if isinstance(result, Exception): orderbook_cache[key] = result
            else: orderbook_cache[key] = result or {}
            return orderbook_cache[key]

        async def validate_depth(o):
            buy_book, sell_book = await asyncio.gather(get_orderbook(o.buy_exchange, o.symbol), get_orderbook(o.sell_exchange, o.symbol))
            if isinstance(buy_book, Exception) or isinstance(sell_book, Exception):
                err = buy_book if isinstance(buy_book, Exception) else sell_book; return None, f'order-book unavailable: {str(err)[:250]}'
            result = round_trip(buy_book, sell_book, filters.trade_size)
            if not result.get('complete'): return None, 'insufficient executable order-book depth for configured trade size'
            gap = float(result.get('effective_gap_pct', 0.0))
            if gap <= 0: return None, f'executable spread {gap:.3f}% is non-positive'
            net = None
            if o.buy_fee is not None and o.sell_fee is not None: net = gap - float(o.buy_fee) - float(o.sell_fee) - float(o.withdrawal_cost or 0.0)
            meta = dict(o.metadata); buy_avg = float(result['buy_average_price']); sell_avg = float(result['sell_average_price'])
            meta.update({'orderbook_validated': True, 'orderbook_depth_limit': 20, 'executable_gap_pct': gap, 'ticker_gap_pct': o.raw_gap, 'buy_average_price': buy_avg, 'sell_average_price': sell_avg, 'executable_quote_size': float(result['quote_spent']), 'executable_base_amount': float(result['base_acquired']), 'executable_quote_received': float(result['quote_received']), 'estimated_slippage_pct': o.raw_gap - gap})
            return replace(o, buy_price=buy_avg, sell_price=sell_avg, raw_gap=gap, estimated_net_profit=net, metadata=meta), None

        await emit('candidates', message='Evaluating price gaps before order-book/network validation'); timed_out = False; network_validation_incomplete = False; depth_validated = []; depth_started = time.monotonic()
        for symbol, tickers in ticker_map.items():
            if time.monotonic() - started_mono >= self.scan_deadline: timed_out = True; break
            valid = [t for t in tickers if t.bid > 0 and t.ask > 0 and t.bid == t.bid and t.ask == t.ask]
            for buy in valid:
                for sell in valid:
                    if buy.exchange == sell.exchange: continue
                    if time.monotonic() - started_mono >= self.scan_deadline: timed_out = True; break
                    comparisons += 1; buy_fee = fee_maps.get(buy.exchange, {}).get(symbol); sell_fee = fee_maps.get(sell.exchange, {}).get(symbol)
                    o = pair_opportunity(buy, sell, buy_fee, sell_fee, None, filters.max_data_age, {'network_available': False, 'contract_match': False, 'networks': []})
                    if not o: continue
                    reason = filters.check(o, include_validation=False)
                    if reason: rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': reason}); continue
                    if filters.require_orderbook and filters.validation_mode != 'loose':
                        if time.monotonic() - depth_started > self.orderbook_budget: rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': 'order-book validation budget exhausted'}); continue
                        try: checked, depth_reason = await asyncio.wait_for(validate_depth(o), timeout=min(self.timeout, max(.1, self.orderbook_budget - (time.monotonic() - depth_started))))
                        except asyncio.TimeoutError: checked, depth_reason = None, 'order-book validation timed out'
                        if depth_reason: rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': depth_reason}); continue
                        o = checked; reason = filters.check(o, include_validation=False)
                        if reason: rejected.append({'symbol': symbol, 'buy': buy.exchange, 'sell': sell.exchange, 'reason': reason}); continue
                        depth_validated.append(o)
                    else: depth_validated.append(o)
                    if comparisons % 100 == 0: await emit('candidates', comparisons=comparisons, opportunities=len(depth_validated), message='Still evaluating candidates…')
                if timed_out: break
            if timed_out: break

        opportunities = depth_validated
        await emit('orderbook', comparisons=comparisons, opportunities=len(opportunities), validated=len(opportunities) if filters.require_orderbook and filters.validation_mode != 'loose' else 0, message='Executable order-book validation complete' if filters.require_orderbook and filters.validation_mode != 'loose' else 'Candidate evaluation complete; order-book validation disabled')

        transfer_cache = {}; transfer_tasks = {}; transfer_semaphore = asyncio.Semaphore(self.network_validation_concurrency)
        async def get_transfer(exchange, asset):
            key = (exchange, asset)
            if key in transfer_cache: return transfer_cache[key]
            if key not in transfer_tasks:
                async def load():
                    async with transfer_semaphore:
                        try: return await adapters[exchange].get_transfer_info(asset)
                        except Exception as e: return e
                transfer_tasks[key] = asyncio.create_task(load())
            task = transfer_tasks[key]; remaining_scan = self.scan_deadline - (time.monotonic() - started_mono)
            if remaining_scan <= 0: task.cancel(); transfer_tasks.pop(key, None); return {}
            try: result = await asyncio.wait_for(task, timeout=min(self.timeout, remaining_scan))
            except asyncio.TimeoutError: transfer_tasks.pop(key, None); result = TimeoutError(f'Transfer information deadline reached for {asset}.')
            except asyncio.CancelledError: transfer_tasks.pop(key, None); result = TimeoutError(f'Transfer information cancelled for {asset}.')
            if isinstance(result, Exception):
                diagnostics.append(Diagnostic(exchange, 'transfer_info', 'degraded', 0, datetime.now(timezone.utc).isoformat(), error_type=getattr(result, 'error_type', type(result).__name__), http_status=getattr(result, 'http_status', None), detail=f'{asset}: {str(result)[:350]}')); warnings.append(f'{exchange}: transfer information unavailable for {asset}; strict validation rejected affected routes.'); degraded.append(exchange); transfer_cache[key] = {}
            else: transfer_cache[key] = result or {}
            return transfer_cache[key]

        if timed_out: warnings.append(f'Scan stopped at the {self.scan_deadline:.0f}s safety deadline; results are partial.')
        elif opportunities and filters.validation_mode != 'loose':
            await emit('network', comparisons=comparisons, opportunities=len(opportunities), message='Validating transfer networks for qualifying routes…')
            keys = {(o.buy_exchange, o.symbol.split('/')[0]) for o in opportunities} | {(o.sell_exchange, o.symbol.split('/')[0]) for o in opportunities}
            remaining_scan = self.scan_deadline - (time.monotonic() - started_mono); validation_budget = min(remaining_scan, self.network_validation_budget)
            if validation_budget > 0 and keys:
                async def preload(key): return key, await get_transfer(*key)
                try: await asyncio.wait_for(asyncio.gather(*(preload(k) for k in keys)), timeout=validation_budget)
                except asyncio.TimeoutError:
                    network_validation_incomplete = True; warnings.append(f'Network validation was capped at {validation_budget:.0f}s; unfinished routes were rejected as unverified.')
                    for task in transfer_tasks.values():
                        if not task.done(): task.cancel()
            else: network_validation_incomplete = True
            validated = []; total_to_validate = len(opportunities)
            for index, o in enumerate(opportunities, 1):
                if time.monotonic() - started_mono >= self.scan_deadline: timed_out = True; break
                asset = o.symbol.split('/')[0]; bn = transfer_cache.get((o.buy_exchange, asset), {}); sn = transfer_cache.get((o.sell_exchange, asset), {})
                network, contract, networks = transfer_compatibility(bn, sn)
                if network and contract:
                    base_amount = o.metadata.get('executable_base_amount')
                    if base_amount is None:
                        try: base_amount = filters.trade_size / float(o.buy_price)
                        except (TypeError, ValueError, ZeroDivisionError): base_amount = None
                    withdrawal_pct = withdrawal_cost_pct(bn, networks, base_amount)
                else: withdrawal_pct = None
                transfer = {'network_available': network, 'contract_match': contract, 'networks': networks}
                if network and contract and withdrawal_pct is not None:
                    net = None if o.buy_fee is None or o.sell_fee is None else o.raw_gap - float(o.buy_fee) - float(o.sell_fee) - withdrawal_pct
                    verified = replace(o, withdrawal_cost=withdrawal_pct, estimated_net_profit=net, metadata={**o.metadata, **transfer, 'withdrawal_fee_available': True, 'withdrawal_fee_pct': withdrawal_pct, 'withdrawal_fee_networks': networks, 'withdrawal_fee_base_asset': asset, 'withdrawal_fee_base_amount': base_amount})
                    verified = replace(verified, confidence=min(100.0, verified.confidence + 5.0))
                else:
                    verified = replace(o, metadata={**o.metadata, **transfer, 'withdrawal_fee_available': False})
                reason = filters.check(verified, include_validation=True)
                if reason: rejected.append({'symbol': o.symbol, 'buy': o.buy_exchange, 'sell': o.sell_exchange, 'reason': reason})
                else: validated.append(verified)
                if index % 50 == 0: await emit('network', comparisons=comparisons, opportunities=len(validated), validated=index, total=total_to_validate, message='Validating transfer networks…')
            opportunities = validated

        degraded = list(dict.fromkeys(degraded))
        if timed_out: warnings.append(f'Scan stopped at the {self.scan_deadline:.0f}s safety deadline; results are partial.')
        await emit('complete', comparisons=comparisons, opportunities=len(opportunities), healthy=len(healthy), failed=len(failed))
        opportunities.sort(key=lambda o: (o.estimated_net_profit is not None, o.estimated_net_profit or float('-inf'), o.confidence, min(o.buy_volume, o.sell_volume)), reverse=True)
        state = ScanState.SUCCESS if not failed and not timed_out and not degraded and not network_validation_incomplete else ScanState.PARTIAL
        if not healthy: state = ScanState.FAILED
        errors = ['No trustworthy market data was returned from selected exchanges.'] if state == ScanState.FAILED else []
        return ScanSnapshot(scan_id, user_id, started, datetime.now(timezone.utc).isoformat(), selected, healthy, degraded, failed, len(union), sum(ticker_counts.values()), comparisons, len(opportunities), state, opportunities, diagnostics, warnings, errors, rejected)
