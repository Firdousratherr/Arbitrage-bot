from __future__ import annotations

from dataclasses import dataclass, field

from .models import Opportunity


@dataclass(slots=True)
class ScanFilters:
    min_gap: float = .50
    min_net_profit: float = 2.00
    min_volume: float = 10000.
    min_liquidity: float = 1000.
    max_data_age: float = 10.
    trade_size: float = 1000.
    require_network: bool = True
    require_fees: bool = False
    require_orderbook: bool = False
    selected_coins: set[str] = field(default_factory=set)
    quote_currency: str = 'USDT'
    validation_mode: str = 'strict'

    def __post_init__(self):
        self.selected_coins = {str(x).strip().upper() for x in self.selected_coins if str(x).strip()}
        self.quote_currency = str(self.quote_currency or 'USDT').strip().upper()
        self.validation_mode = str(self.validation_mode or 'strict').strip().lower()
        if self.validation_mode not in {'strict', 'loose'}: self.validation_mode = 'strict'
        self.trade_size = float(self.trade_size or 0)
        if self.trade_size <= 0: self.trade_size = 1000.0

    def net_profit_amount(self, o: Opportunity) -> float | None:
        if o.estimated_net_profit is None: return None
        return float(o.estimated_net_profit) * self.trade_size / 100.0

    def _annotate_profit(self, o: Opportunity):
        amount = self.net_profit_amount(o)
        if amount is not None:
            o.metadata['trade_size'] = self.trade_size
            o.metadata['net_profit_quote'] = self.quote_currency
            o.metadata['net_profit_amount'] = amount
            o.metadata['net_profit_roi'] = float(o.estimated_net_profit)
        return amount

    def check(self, o: Opportunity, include_validation: bool = True, include_orderbook: bool | None = None):
        if include_orderbook is None: include_orderbook = include_validation
        if o.raw_gap < self.min_gap: return f'gap {o.raw_gap:.3f}% below {self.min_gap:.3f}%'
        amount = self._annotate_profit(o)
        if amount is None: return 'net profit unavailable because required fee data is missing'
        if amount < self.min_net_profit: return f'net profit {amount:.3f} {self.quote_currency} below {self.min_net_profit:.3f} {self.quote_currency}'
        if min(o.buy_volume, o.sell_volume) < self.min_volume: return 'volume below minimum'
        if min(o.buy_volume, o.sell_volume) < self.min_liquidity: return 'liquidity below minimum'
        if o.data_age_seconds > self.max_data_age: return f'data age {o.data_age_seconds:.1f}s exceeds limit'
        if self.selected_coins and o.symbol.split('/', 1)[0].upper() not in self.selected_coins: return 'coin not selected'
        if self.quote_currency and o.symbol.split('/', 1)[1].upper() != self.quote_currency: return 'quote currency mismatch'
        if self.require_fees and not o.metadata.get('fee_data_available', False): return 'fee data unavailable'
        if include_orderbook and self.require_orderbook and self.validation_mode != 'loose' and not o.metadata.get('orderbook_validated', False): return 'order-book depth validation unavailable or insufficient'
        if self.require_network and self.validation_mode != 'loose':
            network_verified = bool(o.metadata.get('transfer_verified', False))
            if include_validation:
                network_verified = network_verified or bool(o.metadata.get('network_available', False) and o.metadata.get('contract_match', False))
            if network_verified and not o.metadata.get('withdrawal_fee_available', False):
                return 'withdrawal fee unavailable; executable net profit cannot be verified'
        if include_validation and self.validation_mode != 'loose':
            if self.require_network and not o.metadata.get('network_available', False): return 'deposit/withdrawal or network validation unavailable'
            if self.require_network and not o.metadata.get('contract_match', False): return 'contract/address matching unavailable or mismatched'
        return None
