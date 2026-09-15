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
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.concurrency = max(1, concurrency)
        self.timeout = timeout
        self.scan_deadline = min(max(timeout * 6, 90), 120)
        self.network_validation_concurrency = min(8, max(2, self.concurrency))
        self.network_validation_budget = min(12.0, max(5.0, timeout * .6))
        self.orderbook_concurrency = min(8, max(2, self.concurrency))
        self.orderbook_budget = min(20.0, max(5.0, timeout * .8))
        self.breakers = {name: CircuitBreaker() for name in exchanges}

    async def _call(self, fn, deadline=None):
        last = None
        for attempt in range(3):
            try:
                remaining = self.timeout if deadline is None else min(self.timeout, deadline-time.monotonic())
                if remaining <= 0: raise asyncio.TimeoutError
                return await asyncio.wait_for(fn(), remaining), attempt
            except (ExchangeError, asyncio.TimeoutError) as exc:
                last = exc
                transient = isinstance(exc, asyncio.TimeoutError) or getattr(exc, 'error_type', None) in {'network','rate_limit','exchange_api'}
                if not transient or attempt == 2: raise
                delay = .25 * (2 ** attempt)
                if deadline is not None and deadline-time.monotonic() <= delay: raise asyncio.TimeoutError
                await asyncio.sleep(delay)
        raise last

    async def _load_markets_one(self, name, adapter, budget):
        started = time.perf_counter(); diag = Diagnostic(name,'market_data','',0,'')
        breaker = self.breakers.setdefault(name, CircuitBreaker())
        if not breaker.available:
            e=ExchangeError('Circuit breaker open; exchange temporarily suppressed.','circuit_open')
            diag.status='failed'; diag.error_type=e.error_type; diag.detail=str(e); diag.timestamp=datetime.now(timezone.utc).isoformat()
            return name,set(),diag,e
        try:
            async with self.semaphore: markets,retries=await self._call(adapter.get_markets,time.monotonic()+budget)
            symbols={m.symbol for m in markets if getattr(m,'active',True)}
            if not symbols: raise ExchangeError(f'{name} returned no usable markets.','empty_data')
            diag.status='ok'; diag.retry_count=retries; diag.latency_ms=(time.perf_counter()-started)*1000; diag.timestamp=datetime.now(timezone.utc).isoformat(); breaker.success()
            return name,symbols,diag,None
        except Exception as exc:
            breaker.failure(); diag.status='failed'; diag.latency_ms=(time.perf_counter()-started)*1000; diag.timestamp=datetime.now(timezone.utc).isoformat(); diag.error_type=getattr(exc,'error_type',type(exc).__name__); diag.http_status=getattr(exc,'http_status',None); diag.detail=str(exc)[:500]
            return name,set(),diag,exc

    async def _load_tickers_one(self,name,adapter,symbols,budget):
        started=time.perf_counter(); requested=set(symbols or ())
        async def fetch(wanted):
            async with self.semaphore: return await self._call(lambda: adapter.get_tickers(wanted),time.monotonic()+budget)
        try:
            tickers,retries=await fetch(requested)
            # A market intersection can be incomplete on individual exchanges.
            # Retry the exchange without the intersection so actual ticker symbols
            # determine cross-exchange coverage.
            if not tickers:
                tickers,retry2=await fetch(set())
                retries += retry2
            if not tickers: raise ExchangeError(f'{name} returned no usable tickers.','empty_data')
            return name,tickers,retries,(time.perf_counter()-started)*1000,None
        except Exception as exc:
            return name,[],0,(time.perf_counter()-started)*1000,exc

    async def scan(self,user_id,selected,filters,progress=None):
        async def emit(stage,**data):
            if progress:
                try: await progress(stage,data)
                except Exception: pass
        scan_id=uuid.uuid4().hex; started=datetime.now(timezone.utc).isoformat(); started_mono=time.monotonic()
        selected=list(dict.fromkeys(str(x).lower() for x in selected)); missing=[n for n in selected if n not in self.exchanges]; adapters={n:self.exchanges[n] for n in selected if n in self.exchanges}
        if len(selected)<2: return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,[],[],selected,0,0,0,0,ScanState.FAILED,errors=['At least two exchanges must be selected.'])
        await emit('start',selected=len(selected))
        waves=max(1,math.ceil(len(adapters)/self.concurrency)); exchange_budget=max(5.,min(self.timeout*2.,(self.scan_deadline-2.)/waves))
        async def load(n,a):
            await emit('exchange',exchange=n,status='loading',markets=0,tickers=0)
            r=await self._load_markets_one(n,a,exchange_budget)
            await emit('exchange',exchange=n,status='healthy' if r[3] is None else 'failed',markets=len(r[1]),tickers=0)
            return r
        try: market_results=await asyncio.wait_for(asyncio.gather(*(load(n,a) for n,a in adapters.items())),self.scan_deadline)
        except asyncio.TimeoutError:
            ds=[Diagnostic(n,'market_data','failed',0,datetime.now(timezone.utc).isoformat(),error_type='scan_timeout',detail=f'Market scan exceeded {self.scan_deadline:.0f}s deadline.') for n in selected]
            return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,[],[],selected,0,0,0,0,ScanState.FAILED,diagnostics=ds,warnings=[f'Scan stopped after the {self.scan_deadline:.0f}s safety deadline.'],errors=['Scan timed out before market data collection completed.'])
        diagnostics=[r[2] for r in market_results]
        for n in missing: diagnostics.append(Diagnostic(n,'adapter_init','failed',0,datetime.now(timezone.utc).isoformat(),error_type='unavailable',detail='Selected exchange is not available in the exchange registry.'))
        healthy=[r[0] for r in market_results if r[3] is None]; failed=[r[0] for r in market_results if r[3] is not None]+missing; market_sets={r[0]:r[1] for r in market_results}; union=set().union(*(market_sets.values())) if market_sets else set()
        await emit('markets',healthy=len(healthy),failed=len(failed),markets=len(union),symbols=len(set().union(*(s for s in market_sets.values())) if market_sets else set()))
        remaining=max(1.,self.scan_deadline-(time.monotonic()-started_mono)); ticker_results=[]
        if healthy:
            try: ticker_results=await asyncio.wait_for(asyncio.gather(*(self._load_tickers_one(n,adapters[n],market_sets.get(n,set()),remaining) for n in healthy)),remaining)
            except asyncio.TimeoutError: ticker_results=[(n,[],0,0.,asyncio.TimeoutError('Ticker collection exceeded scan deadline.')) for n in healthy]
        ticker_map={}; ticker_counts={}
        for n,tickers,retries,latency,error in ticker_results:
            ticker_counts[n]=len(tickers)
            if error:
                if n in healthy: healthy.remove(n)
                if n not in failed: failed.append(n)
                diagnostics.append(Diagnostic(n,'ticker_data','failed',latency,datetime.now(timezone.utc).isoformat(),error_type=getattr(error,'error_type',type(error).__name__),http_status=getattr(error,'http_status',None),detail=str(error)[:500])); continue
            for t in tickers:
                if getattr(t,'bid',0)>0 and getattr(t,'ask',0)>0: ticker_map.setdefault(t.symbol,[]).append(t)
            for d in diagnostics:
                if d.exchange==n and d.operation=='market_data': d.retry_count+=retries; d.latency_ms+=latency; break
            await emit('exchange',exchange=n,status='healthy',markets=len(market_sets.get(n,set())),tickers=len(tickers))
        failed=list(dict.fromkeys(failed)); warnings=['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else []
        rejected=[]; comparisons=0; fee_maps={}; degraded=[]
        async def fee_one(n,a):
            try:return n,await asyncio.wait_for(a.get_trading_fees(set(ticker_map)),self.timeout),None
            except Exception as exc:return n,{},exc
        remaining=max(1.,self.scan_deadline-(time.monotonic()-started_mono))
        try: fee_results=await asyncio.wait_for(asyncio.gather(*(fee_one(n,adapters[n]) for n in healthy)),remaining) if healthy and ticker_map else []
        except asyncio.TimeoutError: fee_results=[(n,{},TimeoutError('Fee collection exceeded scan deadline.')) for n in healthy]
        for n,data,error in fee_results:
            fee_maps[n]=data
            if error: degraded.append(n); diagnostics.append(Diagnostic(n,'trading_fees','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(error,'error_type',type(error).__name__),detail=str(error)[:500])); warnings.append(f'{n}: trading fee data unavailable; affected opportunities have unknown net profit.')
        await emit('fees',completed=len(healthy),degraded=len(degraded))
        orderbook_cache={}; orderbook_tasks={}; ob_sem=asyncio.Semaphore(self.orderbook_concurrency); depth_validated=[]; candidate_batch=[]; depth_completed=0; depth_started=time.monotonic(); timed_out=False; batch_size=max(2,self.orderbook_concurrency*2)
        async def get_orderbook(exchange,symbol):
            key=(exchange,symbol)
            if key in orderbook_cache:return orderbook_cache[key]
            async def load():
                async with ob_sem:
                    rem=self.scan_deadline-(time.monotonic()-started_mono)
                    if rem<=0:return TimeoutError('Order-book validation deadline reached.')
                    try:b,_=await self._call(lambda:adapters[exchange].get_orderbook(symbol,limit=20),time.monotonic()+min(self.timeout,rem));return b
                    except Exception as exc:return exc
            task=orderbook_tasks.setdefault(key,asyncio.create_task(load())); rem=self.scan_deadline-(time.monotonic()-started_mono)
            if rem<=0: task.cancel(); return TimeoutError('Order-book validation deadline reached.')
            try:r=await asyncio.wait_for(task,min(self.timeout,rem))
            except asyncio.TimeoutError: task.cancel(); r=TimeoutError(f'Order-book deadline reached for {exchange} {symbol}.')
            orderbook_cache[key]=r if isinstance(r,Exception) else (r or {}); return orderbook_cache[key]
        async def validate_depth(o):
            b,s=await asyncio.gather(get_orderbook(o.buy_exchange,o.symbol),get_orderbook(o.sell_exchange,o.symbol))
            if isinstance(b,Exception) or isinstance(s,Exception): return None,f'order-book unavailable: {str(b if isinstance(b,Exception) else s)[:250]}'
            r=round_trip(b,s,filters.trade_size)
            if not r.get('complete'):return None,'insufficient executable order-book depth for configured trade size'
            gap=float(r.get('effective_gap_pct',0.));
            if gap<=0:return None,f'executable spread {gap:.3f}% is non-positive'
            net=None if o.buy_fee is None or o.sell_fee is None else gap-float(o.buy_fee)-float(o.sell_fee)-float(o.withdrawal_cost or 0.)
            meta=dict(o.metadata);meta.update({'orderbook_validated':True,'orderbook_depth_limit':20,'executable_gap_pct':gap,'ticker_gap_pct':o.raw_gap,'buy_average_price':float(r['buy_average_price']),'sell_average_price':float(r['sell_average_price']),'executable_quote_size':float(r['quote_spent']),'executable_base_amount':float(r['base_acquired']),'executable_quote_received':float(r['quote_received']),'estimated_slippage_pct':o.raw_gap-gap})
            return replace(o,buy_price=float(r['buy_average_price']),sell_price=float(r['sell_average_price']),raw_gap=gap,estimated_net_profit=net,metadata=meta),None
        async def flush():
            nonlocal depth_completed
            if not candidate_batch:return
            batch=list(candidate_batch);candidate_batch.clear();await emit('orderbook',comparisons=comparisons,opportunities=len(depth_validated),validated=depth_completed,total=depth_completed+len(batch),message='Validating executable order books…')
            async def one(o):
                rem=min(self.orderbook_budget-(time.monotonic()-depth_started),self.scan_deadline-(time.monotonic()-started_mono))
                if rem<=0:return o,None,'order-book validation budget exhausted'
                try:return o,*(await asyncio.wait_for(validate_depth(o),min(self.timeout,rem)))
                except asyncio.TimeoutError:return o,None,'order-book validation timed out'
                except Exception as exc:return o,None,f'order-book validation failed: {str(exc)[:250]}'
            tasks=[asyncio.create_task(one(o)) for o in batch]
            try:
                for task in asyncio.as_completed(tasks):
                    original,checked,reason=await task;depth_completed+=1
                    if reason:rejected.append({'symbol':original.symbol,'buy':original.buy_exchange,'sell':original.sell_exchange,'reason':reason})
                    elif checked is not None:
                        reason=filters.check(checked,include_validation=False)
                        if reason:rejected.append({'symbol':checked.symbol,'buy':checked.buy_exchange,'sell':checked.sell_exchange,'reason':reason})
                        else:depth_validated.append(checked)
                    await emit('orderbook',comparisons=comparisons,opportunities=len(depth_validated),validated=depth_completed,total=len(tasks),message=f'Validating executable order books · {depth_completed}/{len(tasks)} complete')
            finally:
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        await emit('candidates',message='Evaluating price gaps using live ticker coverage…')
        processed=0; symbol_count=len(ticker_map)
        for symbol,tickers in ticker_map.items():
            if time.monotonic()-started_mono>=self.scan_deadline:timed_out=True;break
            processed+=1;valid=[t for t in tickers if t.bid>0 and t.ask>0 and math.isfinite(t.bid) and math.isfinite(t.ask)]
            for buy in valid:
                for sell in valid:
                    if buy.exchange==sell.exchange:continue
                    if time.monotonic()-started_mono>=self.scan_deadline:timed_out=True;break
                    comparisons+=1; opp=pair_opportunity(buy,sell,fee_maps.get(buy.exchange,{}).get(symbol),fee_maps.get(sell.exchange,{}).get(symbol),None,filters.max_data_age,{'network_available':False,'contract_match':False,'networks':[]})
                    if opp:
                        reason=filters.check(opp,include_validation=False)
                        if reason:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':reason})
                        elif filters.require_orderbook and filters.validation_mode!='loose':
                            if time.monotonic()-depth_started>self.orderbook_budget:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':'order-book validation budget exhausted'})
                            else:
                                candidate_batch.append(opp)
                                if len(candidate_batch)>=batch_size:await flush()
                        else:depth_validated.append(opp)
                    if comparisons<=2 or comparisons%25==0:await emit('candidates',comparisons=comparisons,opportunities=len(depth_validated),symbols_processed=processed,message='Comparing exchange prices…')
                    if comparisons%25==0:await asyncio.sleep(0)
                if timed_out:break
            if processed%25==0:await emit('candidates',comparisons=comparisons,opportunities=len(depth_validated),symbols_processed=processed,message=f'Comparing exchange prices · {processed}/{symbol_count} symbols…');await asyncio.sleep(0)
            if timed_out:break
        if candidate_batch and not timed_out:await flush()
        opportunities=depth_validated;await emit('orderbook',comparisons=comparisons,opportunities=len(opportunities),validated=len(opportunities) if filters.require_orderbook and filters.validation_mode!='loose' else 0,message='Executable order-book validation complete')
        transfer_cache={};transfer_tasks={};transfer_sem=asyncio.Semaphore(self.network_validation_concurrency)
        async def get_transfer(exchange,asset):
            key=(exchange,asset)
            if key in transfer_cache:return transfer_cache[key]
            async def load():
                async with transfer_sem:
                    try:return await adapters[exchange].get_transfer_info(asset)
                    except Exception as exc:return exc
            task=transfer_tasks.setdefault(key,asyncio.create_task(load()));rem=self.scan_deadline-(time.monotonic()-started_mono)
            if rem<=0:task.cancel();return {}
            try:r=await asyncio.wait_for(task,min(self.timeout,rem))
            except asyncio.TimeoutError:r=TimeoutError(f'Transfer information deadline reached for {asset}.')
            if isinstance(r,Exception):diagnostics.append(Diagnostic(exchange,'transfer_info','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(r,'error_type',type(r).__name__),detail=f'{asset}: {str(r)[:350]}'));degraded.append(exchange);warnings.append(f'{exchange}: transfer information unavailable for {asset}; strict validation rejected affected routes.');r={}
            transfer_cache[key]=r or {};return transfer_cache[key]
        network_incomplete=False
        if timed_out: warnings.append(f'Scan stopped at the {self.scan_deadline:.0f}s safety deadline; results are partial.')
        elif opportunities and filters.validation_mode!='loose':
            await emit('network',comparisons=comparisons,opportunities=len(opportunities),message='Validating transfer networks for qualifying routes…')
            keys={(o.buy_exchange,o.symbol.split('/')[0]) for o in opportunities}|{(o.sell_exchange,o.symbol.split('/')[0]) for o in opportunities};budget=min(self.scan_deadline-(time.monotonic()-started_mono),self.network_validation_budget)
            if budget>0 and keys:
                try:await asyncio.wait_for(asyncio.gather(*(get_transfer(*k) for k in keys)),budget)
                except asyncio.TimeoutError:
                    network_incomplete=True;warnings.append(f'Network validation was capped at {budget:.0f}s; unfinished routes were rejected as unverified.')
                    for task in transfer_tasks.values():
                        if not task.done():task.cancel()
            else:network_incomplete=True
            validated=[];total=len(opportunities)
            for i,o in enumerate(opportunities,1):
                if time.monotonic()-started_mono>=self.scan_deadline:timed_out=True;break
                asset=o.symbol.split('/')[0];bn=transfer_cache.get((o.buy_exchange,asset),{});sn=transfer_cache.get((o.sell_exchange,asset),{});network,contract,networks=transfer_compatibility(bn,sn);base_amount=o.metadata.get('executable_base_amount')
                if base_amount is None:
                    try:base_amount=filters.trade_size/float(o.buy_price)
                    except (TypeError,ValueError,ZeroDivisionError):base_amount=None
                withdrawal=withdrawal_cost_pct(bn,networks,base_amount) if network and contract else None;transfer={'network_available':network,'contract_match':contract,'networks':networks}
                if network and contract and withdrawal is not None:
                    net=None if o.buy_fee is None or o.sell_fee is None else o.raw_gap-float(o.buy_fee)-float(o.sell_fee)-withdrawal;verified=replace(o,withdrawal_cost=withdrawal,estimated_net_profit=net,metadata={**o.metadata,**transfer,'withdrawal_fee_available':True,'withdrawal_fee_pct':withdrawal,'withdrawal_fee_networks':networks,'withdrawal_fee_base_asset':asset,'withdrawal_fee_base_amount':base_amount},confidence=min(100.,o.confidence+5.))
                else:verified=replace(o,metadata={**o.metadata,**transfer,'withdrawal_fee_available':False})
                reason=filters.check(verified,include_validation=True)
                if reason:rejected.append({'symbol':o.symbol,'buy':o.buy_exchange,'sell':o.sell_exchange,'reason':reason})
                else:validated.append(verified)
                if i%25==0 or i==total:await emit('network',comparisons=comparisons,opportunities=len(validated),validated=i,total=total,message='Validating transfer networks…');await asyncio.sleep(0)
            opportunities=validated
        degraded=list(dict.fromkeys(degraded));failed=list(dict.fromkeys(failed));await emit('complete',comparisons=comparisons,opportunities=len(opportunities),healthy=len(healthy),failed=len(failed))
        opportunities.sort(key=lambda o:(o.estimated_net_profit is not None,o.estimated_net_profit or float('-inf'),o.confidence,min(o.buy_volume,o.sell_volume)),reverse=True)
        state=ScanState.SUCCESS if not failed and not timed_out and not degraded and not network_incomplete else ScanState.PARTIAL
        if not healthy:state=ScanState.FAILED
        errors=['No trustworthy market data was returned from selected exchanges.'] if state==ScanState.FAILED else []
        return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,healthy,degraded,failed,len(union),sum(ticker_counts.values()),comparisons,len(opportunities),state,opportunities,diagnostics,warnings,errors,rejected)
