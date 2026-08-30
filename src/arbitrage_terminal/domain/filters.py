from __future__ import annotations
from dataclasses import dataclass,field
from .models import Opportunity
@dataclass(slots=True)
class ScanFilters:
    min_gap:float=.50; min_net_profit:float=.20; min_volume:float=10000.; min_liquidity:float=1000.; max_data_age:float=10.; require_network:bool=False; require_fees:bool=False; selected_coins:set[str]=field(default_factory=set); quote_currency:str='USDT'
    def check(self,o:Opportunity):
        if o.raw_gap<self.min_gap:return f"gap {o.raw_gap:.3f}% below {self.min_gap:.3f}%"
        if o.estimated_net_profit<self.min_net_profit:return f"net profit {o.estimated_net_profit:.3f}% below {self.min_net_profit:.3f}%"
        if min(o.buy_volume,o.sell_volume)<self.min_volume:return 'volume below minimum'
        if min(o.buy_volume,o.sell_volume)<self.min_liquidity:return 'liquidity below minimum'
        if o.data_age_seconds>self.max_data_age:return f"data age {o.data_age_seconds:.1f}s exceeds limit"
        if self.selected_coins and o.symbol.split('/',1)[0] not in self.selected_coins:return 'coin not selected'
        if self.quote_currency and o.symbol.split('/',1)[1]!=self.quote_currency.upper():return 'quote currency mismatch'
        if self.require_fees and (o.buy_fee is None or o.sell_fee is None):return 'fee data unavailable'
        return None
