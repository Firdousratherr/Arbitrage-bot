from __future__ import annotations
from dataclasses import dataclass, field
from .models import Opportunity

@dataclass(slots=True)
class ScanFilters:
    min_gap:float=.50
    min_net_profit:float=.20
    min_volume:float=10000.
    min_liquidity:float=1000.
    max_data_age:float=10.
    require_network:bool=True
    require_fees:bool=False
    selected_coins:set[str]=field(default_factory=set)
    quote_currency:str='USDT'
    validation_mode:str='strict'

    def check(self,o:Opportunity):
        if o.raw_gap<self.min_gap:return f"gap {o.raw_gap:.3f}% below {self.min_gap:.3f}%"
        if o.estimated_net_profit<self.min_net_profit:return f"net profit {o.estimated_net_profit:.3f}% below {self.min_net_profit:.3f}%"
        if min(o.buy_volume,o.sell_volume)<self.min_volume:return 'volume below minimum'
        if min(o.buy_volume,o.sell_volume)<self.min_liquidity:return 'liquidity below minimum'
        if o.data_age_seconds>self.max_data_age:return f"data age {o.data_age_seconds:.1f}s exceeds limit"
        if self.selected_coins and o.symbol.split('/',1)[0] not in self.selected_coins:return 'coin not selected'
        if self.quote_currency and o.symbol.split('/',1)[1]!=self.quote_currency.upper():return 'quote currency mismatch'
        if self.require_fees and not o.metadata.get('fee_data_available',False):return 'fee data unavailable'
        if self.validation_mode.lower()!='loose':
            if not o.metadata.get('network_available',False):return 'deposit/withdrawal or network validation unavailable'
            if not o.metadata.get('contract_match',False):return 'contract/address matching unavailable or mismatched'
        return None
