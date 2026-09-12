from arbitrage_terminal.domain.filters import ScanFilters
from arbitrage_terminal.domain.models import MarketType, Opportunity


def make_opportunity(net_roi=1.2):
    return Opportunity(
        symbol='BTC/USDT',
        buy_exchange='a',
        sell_exchange='b',
        buy_price=100.0,
        sell_price=101.0,
        raw_gap=1.0,
        buy_fee=0.1,
        sell_fee=0.1,
        withdrawal_cost=0.0,
        estimated_net_profit=net_roi,
        buy_volume=100000.0,
        sell_volume=100000.0,
        data_age_seconds=1.0,
        confidence=90.0,
        market_type=MarketType.SPOT,
        metadata={'fee_data_available': True, 'network_available': True, 'contract_match': True, 'withdrawal_fee_verified': True, 'withdrawal_fee_pct': 0.0},
    )


def test_trade_size_calculates_absolute_quote_profit():
    filters = ScanFilters(trade_size=1000.0, min_net_profit=10.0, quote_currency='USDT')
    opportunity = make_opportunity(1.2)

    assert filters.net_profit_amount(opportunity) == 12.0
    assert filters.check(opportunity) is None
    assert opportunity.metadata['trade_size'] == 1000.0
    assert opportunity.metadata['net_profit_amount'] == 12.0
    assert opportunity.metadata['net_profit_quote'] == 'USDT'
    assert opportunity.metadata['net_profit_roi'] == 1.2


def test_min_net_profit_is_absolute_quote_amount():
    filters = ScanFilters(trade_size=1000.0, min_net_profit=15.0, quote_currency='USDT')
    opportunity = make_opportunity(1.2)

    assert 'below 15.000 USDT' in filters.check(opportunity)
