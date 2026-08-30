from __future__ import annotations
from datetime import datetime,timezone
from math import isfinite
from arbitrage_terminal.domain.models import Opportunity,Ticker,MarketType
def confidence(*,freshness,liquidity,price_consistency,fees_known,network_known,exchange_health):
    age=max(0,min(1,1-freshness/30));liq=max(0,min(1,liquidity/100000));return round(100*(.25*age+.20*liq+.20*price_consistency+.15*(1 if fees_known else .5)+.10*(1 if network_known else .5)+.10*exchange_health),1)
def pair_opportunity(buy,sell,buy_fee,sell_fee,withdrawal_cost_pct=0,max_age=10):
    if buy.exchange==sell.exchange or buy.symbol!=sell.symbol or buy.base!=sell.base or buy.quote!=sell.quote:return None
    if buy.ask<=0 or sell.bid<=0 or not isfinite(buy.ask) or not isfinite(sell.bid):return None
    gap=(sell.bid-buy.ask)/buy.ask*100
    if gap<=0:return None
    age=max(0,(datetime.now(timezone.utc)-min(buy.timestamp,sell.timestamp)).total_seconds())
    if age>max_age*3:return None
    net=gap-buy_fee-sell_fee-withdrawal_cost_pct;liq=min(buy.quote_volume,sell.quote_volume);pc=max(0,min(1,1-abs(gap)/10))
    score=confidence(freshness=age,liquidity=liq,price_consistency=pc,fees_known=True,network_known=True,exchange_health=1)
    return Opportunity(buy.symbol,buy.exchange,sell.exchange,buy.ask,sell.bid,gap,buy_fee,sell_fee,withdrawal_cost_pct,net,buy.quote_volume,sell.quote_volume,age,score,MarketType.SPOT)
