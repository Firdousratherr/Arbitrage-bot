from __future__ import annotations
import asyncio,time,uuid
from datetime import datetime,timezone
from arbitrage_terminal.domain.models import Diagnostic,ScanSnapshot,ScanState,Ticker
from arbitrage_terminal.domain.filters import ScanFilters
from arbitrage_terminal.arbitrage.engine import pair_opportunity
class ArbitrageScanner:
    def __init__(self,exchanges,concurrency=6,timeout=20):self.exchanges=exchanges;self.semaphore=asyncio.Semaphore(concurrency);self.timeout=timeout
    async def _one(self,name,adapter):
        started=time.perf_counter();diag=Diagnostic(name,'market_data','',0,'')
        try:
            async with self.semaphore:
                markets=await asyncio.wait_for(adapter.get_markets(),self.timeout);symbols={m.symbol for m in markets};tickers=await asyncio.wait_for(adapter.get_tickers(symbols),self.timeout)
            diag.status='ok';diag.latency_ms=(time.perf_counter()-started)*1000;diag.timestamp=datetime.now(timezone.utc).isoformat();return name,symbols,tickers,diag,None
        except Exception as e:
            diag.status='failed';diag.latency_ms=(time.perf_counter()-started)*1000;diag.timestamp=datetime.now(timezone.utc).isoformat();diag.error_type=getattr(e,'error_type',type(e).__name__);diag.http_status=getattr(e,'http_status',None);diag.detail=str(e)[:500];return name,set(),[],diag,e
    async def scan(self,user_id,selected,filters):
        scan_id=uuid.uuid4().hex;started=datetime.now(timezone.utc).isoformat();selected=list(dict.fromkeys(x.lower() for x in selected));adapters={n:self.exchanges[n] for n in selected if n in self.exchanges}
        if len(adapters)<2:return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,[],[],selected,0,0,0,0,ScanState.FAILED,errors=['At least two selected exchanges must be available.'])
        results=await asyncio.gather(*(self._one(n,a) for n,a in adapters.items()));healthy=[r[0] for r in results if r[4] is None];failed=[r[0] for r in results if r[4] is not None];market_sets={r[0]:r[1] for r in results};ticker_map={}
        for n in healthy:
            for t in next(r[2] for r in results if r[0]==n):ticker_map.setdefault(t.symbol,[]).append(t)
        union=set().union(*(market_sets.values()));diagnostics=[r[3] for r in results];warnings=['One or more selected exchanges failed; results use only healthy exchanges.'] if failed else [];rejected=[];opportunities=[];comparisons=0
        fee_maps={}
        async def fees(n,a):
            try:return n,await asyncio.wait_for(a.get_trading_fees(set(ticker_map)),self.timeout)
            except Exception:return n,{}
        fee_maps=dict(await asyncio.gather(*(fees(n,a) for n,a in adapters.items())))
        for symbol,tickers in ticker_map.items():
            valid=[t for t in tickers if t.bid>0 and t.ask>0 and t.bid==t.bid and t.ask==t.ask]
            for buy in valid:
                for sell in valid:
                    if buy.exchange==sell.exchange:continue
                    comparisons+=1;o=pair_opportunity(buy,sell,fee_maps.get(buy.exchange,{}).get(symbol,0),fee_maps.get(sell.exchange,{}).get(symbol,0),0,filters.max_data_age)
                    if not o:continue
                    reason=filters.check(o)
                    if reason:rejected.append({'symbol':symbol,'buy':buy.exchange,'sell':sell.exchange,'reason':reason});continue
                    opportunities.append(o)
        opportunities.sort(key=lambda o:(o.estimated_net_profit,o.confidence,min(o.buy_volume,o.sell_volume)),reverse=True);state=ScanState.SUCCESS if not failed else ScanState.PARTIAL
        if not healthy:state=ScanState.FAILED
        errors=['No trustworthy market data was returned from selected exchanges.'] if state==ScanState.FAILED else []
        return ScanSnapshot(scan_id,user_id,started,datetime.now(timezone.utc).isoformat(),selected,healthy,[],failed,len(union),sum(len(r[2]) for r in results),comparisons,len(opportunities),state,opportunities,diagnostics,warnings,errors,rejected)
