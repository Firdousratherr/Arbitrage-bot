from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

def utcnow(): return datetime.now(timezone.utc)
class MarketType(str, Enum):
    SPOT='spot'; PERPETUAL='perpetual'; FUTURES='futures'; MARGIN='margin'; OPTIONS='options'
class ScanState(str, Enum): SUCCESS='success'; PARTIAL='partial'; FAILED='failed'
class AIMode(str, Enum): OFF='off'; ASSIST='assist'; ENHANCED='enhanced'
@dataclass(frozen=True, slots=True)
class Market:
    exchange:str; symbol:str; base:str; quote:str; market_type:MarketType; active:bool=True
@dataclass(frozen=True, slots=True)
class Ticker:
    exchange:str; symbol:str; base:str; quote:str; bid:float; ask:float; quote_volume:float; timestamp:datetime
@dataclass(frozen=True, slots=True)
class Opportunity:
    symbol:str; buy_exchange:str; sell_exchange:str; buy_price:float; sell_price:float
    raw_gap:float; buy_fee:float|None; sell_fee:float|None; withdrawal_cost:float|None; estimated_net_profit:float|None
    buy_volume:float; sell_volume:float; data_age_seconds:float; confidence:float
    market_type:MarketType=MarketType.SPOT; metadata:dict[str,Any]=field(default_factory=dict)
    def to_dict(self):
        d=asdict(self);d['market_type']=self.market_type.value;return d
@dataclass(slots=True)
class Diagnostic:
    exchange:str; operation:str; status:str; latency_ms:float; timestamp:str
    error_type:str|None=None; http_status:int|None=None; retry_count:int=0; detail:str|None=None
@dataclass(slots=True)
class ScanSnapshot:
    scan_id:str; user_id:int; started_at:str; completed_at:str|None
    selected_exchanges:list[str]; healthy_exchanges:list[str]; degraded_exchanges:list[str]; failed_exchanges:list[str]
    markets_discovered:int; markets_validated:int; candidates_evaluated:int; opportunities_found:int
    state:ScanState; opportunities:list[Opportunity]=field(default_factory=list)
    diagnostics:list[Diagnostic]=field(default_factory=list); warnings:list[str]=field(default_factory=list); errors:list[str]=field(default_factory=list)
    filter_rejections:list[dict[str,Any]]=field(default_factory=list)
    def to_dict(self): return {**asdict(self),'state':self.state.value,'opportunities':[x.to_dict() for x in self.opportunities]}
