from __future__ import annotations
import asyncio,time,uuid
from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Diagnostic,ScanSnapshot,ScanState
from arbitrage_terminal.arbitrage.engine import pair_opportunity,transfer_compatibility
from arbitrage_terminal.exchanges.base import ExchangeError
from arbitrage_terminal.exchanges.circuit_breaker import CircuitBreaker
class ArbitrageScanner:
    def __init__(self,exchanges,concurrency=6,timeout=20):
        self.exchanges=exchanges;self.semaphore=asyncio.Semaphore(concurrency);self.timeout=timeout;self.breakers={name:CircuitBreaker() for name in exchanges}
    async def _call(self,fn):
        last=None
        for attempt in range(3):
            try:return await asyncio.wait_for(fn(),self.timeout),attempt
            except (ExchangeError,asyncio.TimeoutError) as exc:
                last=exc;transient=isinstance(exc,asyncio.TimeoutError) or getattr(exc,'error_type',None) in {'network','rate_limit'}
                if not transient or attempt==2:raise
                await asyncio.sleep(.25*(2**attempt))
        raise last
    async def _one(self,name,adapter):
        started=time.perf_counter();diag=Diagnostic(name,'market_data','',0,'');breaker=self.breakers.setdefault(name,CircuitBreaker())
        if not breaker.available:
            e=ExchangeError('Circuit breaker open; exchange temporarily suppressed.','circuit_open');diag.status='failed';diag.error_type=e.error_type;diag.detail=str(e);diag.timestamp=datetime.now(timezone.utc).isoformat();return name,set(),[],diag,e
        try:
            async with self.semaphore:
                (markets,r1)=await self._call(adapter.get_markets);symbols={m.symbol for m in markets};(tickers,r2)=await self._call(lambda:adapter.get_tickers(symbols))
            diag.status='ok';diag.retry_count=r1+r2;diag.latency_ms=(time.perf_counter()-started)*1000;diag.timestamp=datetime.now(timezone.utc).isoformat();breaker.success();return name,symbols,tickers,diag,None
        except Exception as e:
            breaker.failure();diag.status='failed';diag.latency_ms=(time.perf_counter()-started)*1000;diag.timestamp=datetime.now(timezone.utc).isoformat();diag.error_type=getattr(e,'error_type',type(e).__name__);diag.http_status=getattr(e,'http_status',None);diag.detail=str(e)[:500];return name,set(),[],diag,e
    async def scan(self,user_id,selected,filters,progress=None):
        async def emit(stage,**data):
            if progress:
                try: await progress(stage,data)
                except Exception: pass
        scan_id=uuid.uuid4().hex;started=datetime.now(timezone.utc).isoformat();selected=list(dict.fromkeys(x.lower() for x in selected));missing=[n for n in selected if n not in self.exchanges];adapters={n:self.exchanges[n] for n in selected if n in self.exchanges}
        if len(selected)<2:return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,[],[],selected,0,0,0,0,ScanState.FAILED,errors=['At least two exchanges must be selected.'])
        await emit('start',selected=len(selected))
        async def one(name,adapter):
            r=await self._one(name,adapter);await emit('exchange',exchange=name,status='healthy' if r[4] is None else 'failed',markets=len(r[1]),tickers=len(r[2]));return r
        results=await asyncio.gather(*(one(n,a) for n,a in adapters.items()))
        diagnostics=[r[3] for r in results]
        for n in missing:diagnostics.append(Diagnostic(n,'adapter_init','failed',0,datetime.now(timezone.utc).isoformat(),error_type='unavailable',detail='Selected exchange is not available in the exchange registry.'))
        healthy=[r[0] for r in results if r[4] is None];failed=[r[0] for r in results if r[4] is not None]+missing;market_sets={r[0]:r[1] for r in results};ticker_map={}
        for n in healthy:
            for t in next(r[2] for r in results if r[0]==n):ticker_map.setdefault(t.symbol,[]).append(t)
        union=set().union(*(market_sets.values())) if market_sets else set();warnings=['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else [];rejected=[];opportunities=[];comparisons=0;fee_maps={};degraded=[]
        await emit('markets',healthy=len(healthy),failed=len(failed),markets=len(union),symbols=len(ticker_map))
        async def fees(n,a):
            try:return n,(await asyncio.wait_for(a.get_trading_fees(set(ticker_map))),self.timeout),None
            except Exception as e:return n,{},e
        async def fee_one(n,a):
            try:return n,await asyncio.wait_for(a.get_trading_fees(set(ticker_map)),self.timeout),None
            except Exception as e:return n,{},e
        for n,data,e in await asyncio.gather(*(fee_one(n,a) for n,a in adapters.items())):
            fee_maps[n]=data
            if e:
                degraded.append(n);diagnostics.append(Diagnostic(n,'trading_fees','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(e,'error_type',type(e).__name__),http_status=getattr(e,'http_status',None),detail=str(e)[:500]));warnings.append(f'{n}: trading fee data unavailable; affected opportunities have unknown net profit.')
        await emit('fees',completed=len(adapters),degraded=len(degraded))
        transfer_cache={}
        transfer_tasks={}
        async def get_transfer(exchange,asset):
            key=(exchange,asset)
            if key in transfer_cache:return transfer_cache[key]
            if key not in transfer_tasks:
                async def load():
                    try:return await asyncio.wait_for(adapters[exchange].get_transfer_info(asset),self.timeout)
                    except Exception as e:return e
                transfer_tasks[key]=asyncio.create_task(load())
            result=await transfer_tasks[key]
            if isinstance(result,Exception):
                diagnostics.append(Diagnostic(exchange,'transfer_info','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(result,'error_type',type(result).__name__),http_status=getattr(result,'http_status',None),detail=f'{asset}: {str(result)[:350]}'))
                warnings.append(f'{exchange}: transfer information unavailable for {asset}; strict validation rejected affected routes.')
                transfer_cache[key]={}
            else:transfer_cache[key]=result or {}
            return transfer_cache[key]
        await emit('candidates',message='Evaluating price gaps before network validation')
        for symbol,tickers in ticker_map.items():
            valid=[t for t in tickers if t.bid>0 and t.ask>0 and t.bid==t.bid and t.ask==t.ask]
            for buy in valid:
                for sell in valid:
                    if buy.exchange==sell.exchange:continue
                    comparisons+=1;buy_fee=fee_maps.get(buy.exchange,{}).get(symbol);sell_fee=fee_maps.get(sell.exchange,{}).get(symbol)
                    o=pair_opportunity(buy,sell,buy_fee,sell_fee,None,filters.max_data_age,{'network_available':False,'contract_match':False,'networks':[]})
                    if not o:continue
                    reason=filters.check(o,include_validation=False)
                    if reason:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':reason});continue
                    if filters.validation_mode.lower()!='loose':
                        bn=await get_transfer(buy.exchange,buy.base);sn=await get_transfer(sell.exchange,sell.base);network,contract,networks=transfer_compatibility(bn,sn);transfer={'network_available':network,'contract_match':contract,'networks':networks}
                        o=pair_opportunity(buy,sell,buy_fee,sell_fee,None,filters.max_data_age,transfer)
                        reason=filters.check(o,include_validation=True)
                        if reason:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':reason});continue
                    opportunities.append(o)
                    if len(opportunities)%5==0:await emit('opportunity',count=len(opportunities),symbol=o.symbol,buy=o.buy_exchange,sell=o.sell_exchange,gap=o.raw_gap)
        opportunities.sort(key=lambda o:(o.estimated_net_profit is not None,o.estimated_net_profit or float('-inf'),o.confidence,min(o.buy_volume,o.sell_volume)),reverse=True)
        await emit('complete',comparisons=comparisons,opportunities=len(opportunities),healthy=len(healthy),failed=len(failed))
        state=ScanState.SUCCESS if not failed else ScanState.PARTIAL
        if not healthy:state=ScanState.FAILED
        errors=['No trustworthy market data was returned from selected exchanges.'] if state==ScanState.FAILED else []
        return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,healthy,degraded,failed,len(union),sum(len(r[2]) for r in results),comparisons,len(opportunities),state,opportunities,diagnostics,warnings,errors,rejected)
