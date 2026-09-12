import json

from arbitrage_terminal.ai.assistant import AIAssistant


def test_ai_analysis_payload_is_compact_and_bounded():
    payload = {
        'scan_id': 'scan-1',
        'state': 'success',
        'selected_exchanges': ['a', 'b'],
        'healthy_exchanges': ['a', 'b'],
        'failed_exchanges': [],
        'markets_discovered': 1000,
        'markets_validated': 900,
        'candidates_evaluated': 50000,
        'opportunity_count': 100,
        'opportunities': [
            {
                'symbol': 'BTC/USDT',
                'buy_exchange': 'a',
                'sell_exchange': 'b',
                'buy_price': 100.0,
                'sell_price': 101.0,
                'gap_percent': 1.0,
                'estimated_net_profit': 0.8,
                'volume': 100000,
                'liquidity': 10000,
                'data_age_seconds': 1,
                'confidence': 90,
                'metadata': {'verbose': 'x' * 5000},
            }
            for _ in range(100)
        ],
        'filter_rejection_count': 1000,
        'rejection_summary': {'gap': 500, 'net': 500},
        'filters': {'selected_coins': [f'COIN{i}' for i in range(100)]},
        'diagnostics': [{'exchange': 'a', 'error': 'x' * 5000} for _ in range(100)],
        'warnings': ['warning ' + 'x' * 5000 for _ in range(100)],
        'errors': ['error ' + 'x' * 5000 for _ in range(100)],
    }

    compact = AIAssistant._compact_analysis_payload(payload)
    encoded = json.dumps(compact, default=str, separators=(',', ':'))

    assert len(encoded) <= AIAssistant.ANALYSIS_PAYLOAD_LIMIT
    assert len(compact['opportunities']) <= AIAssistant.MAX_OPPORTUNITIES
    assert 'metadata' not in compact['opportunities'][0]
    assert compact['opportunities'][0]['symbol'] == 'BTC/USDT'
    assert len(compact['diagnostics']) <= AIAssistant.MAX_DIAGNOSTICS
    assert len(compact['warnings']) <= AIAssistant.MAX_MESSAGES
    assert len(compact['errors']) <= AIAssistant.MAX_MESSAGES
