from __future__ import annotations
from datetime import datetime,timezone
from math import isfinite
from arbitrage_terminal.domain.models import Opportunity,MarketType

MAX_UNVERIFIED_GAP_PERCENT = 1000.0

def confidence(*,freshness,liquidity,price_consistency,fees_known,network_known,exchange_health):
    age=max(0,min(1,1-freshness/30));liq=max(0,min(1,liquidity/100000))
    return round(100*(.25*age+.20*liq+.20*price_consistency+.15*(1 if fees_known else .5)+.10*(1 if network_known else .5)+.10*exchange_health),1)

def _networks(info):
    return {str(n.get('network') or k).upper():n for k,n in (info or {}).get('networks',{}).items()} if isinstance((info or {}).get('networks'),dict) else {str(n.get('network')).upper():n for n in (info or {}).get('networks',[]) if n.get('network')}

def transfer_compatibility(buy_info,sell_info):
    """Return route-level deposit/withdrawal and contract compatibility."""
    bnet,snet=_networks(buy_info),_networks(sell_info); common=set(bnet)&set(snet); usable=[]
    for network in common:
        b,s=bnet[network],snet[network]
        if b.get('withdraw') is False or s.get('deposit') is False: continue
        bc=b.get('contract_address') or b.get('address'); sc=s.get('contract_address') or s.get('address')
        if bc and sc and str(bc).lower()!=str(sc).lower(): continue
        usable.append(network)
    contract_match=bool(usable) and all(not ((bnet[n].get('contract_address') or bnet[n].get('address')) and (snet[n].get('contract_address') or snet[n].get('address')) and str(bnet[n].get('contract_address') or bnet[n].get('address')).lower()!=str(snet[n].get('contract_address') or snet[n].get('address')).lower()) for n in usable)
    return bool(usable),contract_match,usable

def withdrawal_cost_pct(buy_info, networks, base_amount):
    """Return the cheapest known withdrawal fee as a percentage of the routed base amount.

    CCXT network fees are normally denominated in the withdrawn asset, so the fee
    must be normalized against the executable base amount rather than treated as
    a percentage directly. If any usable route lacks a fee, it is ignored only if
    another compatible route has a known fee; if none are known, return None.
    """
    if base_amount is None or not isfinite(float(base_amount)) or float(base_amount) <= 0:
        return None
    by_network = _networks(buy_info)
    known = []
    for network in networks:
        value = by_network.get(str(network).upper(), {}).get('fee')
        try:
            fee = float(value)
        except (TypeError, ValueError):
            continue
        if isfinite(fee) and fee >= 0:
            known.append(fee)
    if not known:
        return None
    return min(known) / float(base_amount) * 100.0

def pair_opportunity(buy,sell,buy_fee,sell_fee,withdrawal_cost_pct=None,max_age=10,transfer=None):
    if buy.exchange==sell.exchange or buy.symbol!=sell.symbol or buy.base!=sell.base or buy.quote!=sell.quote:return None
    buy_identity = getattr(buy, 'asset_identity', None)
    sell_identity = getattr(sell, 'asset_identity', None)
    if buy_identity or sell_identity:
        if not buy_identity or not sell_identity or buy_identity != sell_identity:return None
    if buy.ask<=0 or sell.bid<=0 or not isfinite(buy.ask) or not isfinite(sell.bid):return None
    gap=(sell.bid-buy.ask)/buy.ask*100
    if gap<=0:return None
    if not buy_identity and not sell_identity and gap > MAX_UNVERIFIED_GAP_PERCENT:return None
    age=max(0,(datetime.now(timezone.utc)-min(buy.timestamp,sell.timestamp)).total_seconds())
    if age>max_age*3:return None
    fees_known=buy_fee is not None and sell_fee is not None
    if not fees_known: net=None
    else: net=gap-float(buy_fee)-float(sell_fee)-(float(withdrawal_cost_pct or 0))
    liq=min(buy.quote_volume,sell.quote_volume);pc=max(0,min(1,1-abs(gap)/10))
    network_known=bool(transfer and transfer.get('network_available'));contract_match=bool(transfer and transfer.get('contract_match'))
    score=confidence(freshness=age,liquidity=liq,price_consistency=pc,fees_known=fees_known,network_known=network_known,exchange_health=1)
    meta={'network_available':network_known,'contract_match':contract_match,'compatible_networks':(transfer or {}).get('networks',[]),'fee_data_available':fees_known,'withdrawal_fee_available':withdrawal_cost_pct is not None,'deterministic':True,'transfer_verified':network_known and contract_match,'asset_identity_verified':bool(buy_identity and sell_identity)}
    return Opportunity(buy.symbol,buy.exchange,sell.exchange,buy.ask,sell.bid,gap,buy_fee,sell_fee,withdrawal_cost_pct,net,buy.quote_volume,sell.quote_volume,age,score,MarketType.SPOT,meta)
