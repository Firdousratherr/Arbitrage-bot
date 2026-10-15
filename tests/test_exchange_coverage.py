from types import SimpleNamespace

from arbitrage_terminal.application.service import TerminalService
from arbitrage_terminal.domain.models import Diagnostic, Opportunity, ScanSnapshot, ScanState


class Adapter:
    def __init__(self, markets, tickers):
        self.last_market_symbols = set(markets)
        self.last_ticker_symbols = set(tickers)
        self.last_ticker_count = len(tickers)


def make_service(exchanges):
    return TerminalService(
        repo=None,
        scanner=None,
        ai=None,
        settings=SimpleNamespace(scan_timeout_seconds=20),
        exchanges=exchanges,
    )


def test_exchange_coverage_exposes_zero_comparison_root_cause():
    service = make_service({
        'alpha': Adapter({'BTC/USDT', 'ETH/USDT'}, {'BTC/USDT', 'ETH/USDT'}),
        'beta': Adapter({'BTC/USDT', 'SOL/USDT'}, {'BTC/USDT'}),
        'gamma': Adapter({'XRP/USDT'}, set()),
    })
    snap = ScanSnapshot(
        'scan', 1, 'start', 'done',
        ['alpha', 'beta', 'gamma'], ['alpha', 'beta'], [], ['gamma'],
        5, 5, 2, 0, ScanState.SUCCESS,
        diagnostics=[
            Diagnostic('alpha', 'market_data', 'ok', 10, 'now'),
            Diagnostic('beta', 'market_data', 'ok', 20, 'now'),
            Diagnostic('gamma', 'market_data', 'failed', 30, 'now', detail='ticker endpoint failed'),
        ],
    )

    service._add_exchange_coverage(snap)
    coverage = snap.exchange_coverage

    assert coverage['alpha']['ticker_count'] == 2
    assert coverage['beta']['ticker_count'] == 1
    assert coverage['gamma']['status'] == 'failed'
    assert coverage['alpha']['shared_symbols'] == 1
    assert coverage['beta']['shared_symbols'] == 1
    assert coverage['alpha']['candidate_comparisons'] == 1
    assert coverage['beta']['candidate_comparisons'] == 1
    assert coverage['gamma']['candidate_comparisons'] == 0
    assert coverage['gamma']['error'] == 'ticker endpoint failed'


def test_exchange_coverage_counts_final_routes_and_rejections():
    service = make_service({
        'alpha': Adapter({'BTC/USDT'}, {'BTC/USDT'}),
        'beta': Adapter({'BTC/USDT'}, {'BTC/USDT'}),
    })
    opportunity = Opportunity(
        'BTC/USDT', 'alpha', 'beta', 100.0, 101.0,
        1.0, None, None, None, 1.0,
        10000.0, 10000.0, 1.0, 90.0,
    )
    snap = ScanSnapshot(
        'scan', 1, 'start', 'done', ['alpha', 'beta'], ['alpha', 'beta'], [], [],
        2, 2, 2, 1, ScanState.SUCCESS, [opportunity],
        filter_rejections=[
            {'symbol': 'BTC/USDT', 'buy': 'alpha', 'sell': 'beta', 'reason': 'gap below threshold'},
            {'symbol': 'BTC/USDT', 'buy': 'alpha', 'sell': 'beta', 'reason': 'deposit/withdrawal unavailable'},
        ],
    )

    service._add_exchange_coverage(snap)
    assert snap.exchange_coverage['alpha']['final_opportunities'] == 1
    assert snap.exchange_coverage['beta']['final_opportunities'] == 1
    assert snap.exchange_coverage['alpha']['candidate_rejections'] == 2
    assert snap.exchange_coverage['alpha']['network_rejections'] == 1
    assert snap.exchange_coverage['alpha']['filter_rejections'] == 1
