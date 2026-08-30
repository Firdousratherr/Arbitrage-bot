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
    async def _transfer_map(self,adapters,bases,diagnostics,warnings):
        result={}
        async def one(exchange,asset):
            try:return exchange,asset,await asyncio.wait_for(adapters[exchange].get_transfer_info(asset),self.timeout),None
            except Exception as e:return exchange,asset,None,e
        jobs=[one(n,a) for n,a in adapters.items() for a in sorted(bases)]
        for n,asset,info,e in await asyncio.gather(*jobs):
            if e:
                warnings.append(f'{n}: transfer information unavailable for {asset}; strict validation will reject unverified routes.')
                diagnostics.append(Diagnostic(n,'transfer_info','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(e,'error_type',type(e).__name__),http_status=getattr(e,'http_status',None),detail=f'{asset}: {str(e)[:350]}'))
            else: result[(n,asset)]=info or {}
        return result
    async def scan(self,user_id,selected,filters):
        scan_id=uuid.uuid4().hex;started=datetime.now(timezone.utc).isoformat();selected=list(dict.fromkeys(x.lower() for x in selected));missing=[n for n in selected if n not in self.exchanges];adapters={n:self.exchanges[n] for n in selected if n in self.exchanges}
        if len(selected)<2:return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,[],[],selected,0,0,0,0,ScanState.FAILED,errors=['At least two exchanges must be selected.'])
        results=await asyncio.gather(*(self._one(n,a) for n,a in adapters.items()));diagnostics=[r[3] for r in results]
        for n in missing:diagnostics.append(Diagnostic(n,'adapter_init','failed',0,datetime.now(timezone.utc).isoformat(),error_type='unavailable',detail='Selected exchange is not available in the exchange registry.'))
        healthy=[r[0] for r in results if r[4] is None];failed=[r[0] for r in results if r[4] is not None]+missing;market_sets={r[0]:r[1] for r in results};ticker_map={}
        for n in healthy:
            for t in next(r[2] for r in results if r[0]==n):ticker_map.setdefault(t.symbol,[]).append(t)
        union=set().union(*(market_sets.values())) if market_sets else set();warnings=['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else [];rejected=[];opportunities=[];comparisons=0;fee_maps={};degraded=[]
        async def fees(n,a):
            try:return n,(await asyncio.wait_for(a.get_trading_fees(set(ticker_map)),self.timeout)),None
            except Exception as e:return n,{},e
        for n,data,e in await asyncio.gather(*(fees(n,a) for n,a in adapters.items())):
            fee_maps[n]=data
            if e:
                degraded.append(n);diagnostics.append(Diagnostic(n,'trading_fees','degraded',0,datetime.now(timezone.utc).isoformat(),error_type=getattr(e,'error_type',type(e).__name__),http_status=getattr(e,'http_status',None),detail=str(e)[:500]));warnings.append(f'{n}: trading fee data unavailable; affected opportunities have unknown net profit.')
        bases={t.base for vals in ticker_map.values() for t in vals};transfer_map=await self._transfer_map(adapters,bases,diagnostics,warnings)
        for symbol,tickers in ticker_map.items():
            valid=[t for t in tickers if t.bid>0 and t.ask>0 and t.bid==t.bid and t.ask==t.ask]
            for buy in valid:
                for sell in valid:
                    if buy.exchange==sell.exchange:continue
                    comparisons+=1;buy_fee=fee_maps.get(buy.exchange,{}).get(symbol);sell_fee=fee_maps.get(sell.exchange,{}).get(symbol)
                    bn=transfer_map.get((buy.exchange,buy.base));sn=transfer_map.get((sell.exchange,sell.base));network,contract,networks=transfer_compatibility(bn,sn);transfer={'network_available':network,'contract_match':contract,'networks':networks}
                    o=pair_opportunity(buy,sell,buy_fee,sell_fee,None,filters.max_data_age,transfer)
                    if not o:continue
                    reason=filters.check(o)
                    if reason:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':reason});continue
                    opportunities.append(o)
        opportunities.sort(key=lambda o:(o.estimated_net_profit is not None,o.estimated_net_profit or float('-inf'),o.confidence,min(o.buy_volume,o.sell_volume)),reverse=True);state=ScanState.SUCCESS if not failed else ScanState.PARTIAL
        if not healthy:state=ScanState.FAILED
        errors=['No trustworthy market data was returned from selected exchanges.'] if state==ScanState.FAILED else []
        return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,healthy,degraded,failed,len(union),sum(len(r[2]) for r in results),comparisons,len(opportunities),state,opportunities,diagnostics,warnings,errors,rejected)
