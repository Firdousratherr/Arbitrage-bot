from __future__ import annotations

import asyncio
import json
from collections import Counter

from arbitrage_terminal.domain.models import AIMode


class TerminalService:
    def __init__(self, repo, scanner, ai, settings, exchanges=None):
        self.repo = repo
        self.scanner = scanner
        self.ai = ai
        self.settings = settings
        self.exchanges = exchanges if exchanges is not None else getattr(scanner, 'exchanges', {})
        self._scan_locks: dict[int, asyncio.Lock] = {}
        self._scan_locks_guard = asyncio.Lock()

    async def ensure_user(self, user, email=None):
        await self.repo.ensure_user(user.id, user.username, email)

    async def get_user(self, user_id):
        return await self.repo.user(user_id)

    async def set_exchanges(self, user_id, exchanges):
        await self.repo.set_exchanges(user_id, exchanges)

    async def set_ai_mode(self, user_id, mode):
        await self.repo.set_ai_mode(user_id, mode)

    async def set_validation_mode(self, user_id, mode):
        mode = str(mode).lower()
        if mode not in {'strict', 'loose'}:
            raise ValueError('Validation mode must be strict or loose.')
        row = await self.repo.user(user_id)
        data = json.loads(row['filters'] or '{}')
        data['validation_mode'] = mode
        await self.repo.set_filters(user_id, data)

    async def _scan_lock(self, user_id):
        async with self._scan_locks_guard:
            return self._scan_locks.setdefault(user_id, asyncio.Lock())

    async def run_scan(self, user_id, progress=None):
        lock = await self._scan_lock(user_id)
        if lock.locked():
            raise RuntimeError('A scan is already running for this user. Please wait for it to finish.')
        async with lock:
            row = await self.repo.user(user_id)
            selected = json.loads(row['exchanges'] or '[]')
            snap = await self.scanner.scan(
                user_id, selected, self.repo.filters_from_row(row), progress=progress
            )
            await self.repo.save_scan(snap)
            return snap

    async def history(self, user_id):
        return await self.repo.history(user_id)

    async def scan(self, user_id, scan_id):
        return await self.repo.get_scan(user_id, scan_id)

    async def ai_scan_analysis(self, user_id, snap):
        row = await self.repo.user(user_id)
        mode = AIMode(row['result_mode'] or 'off')
        rejections = snap.get('filter_rejections') or []
        rejection_counts = Counter(
            str(item.get('reason', 'unknown')).split(' ')[0] for item in rejections
        )
        filters = self.repo.filters_from_row(row)
        payload = {
            'scan_id': snap['scan_id'],
            'state': snap.get('state'),
            'selected_exchanges': snap.get('selected_exchanges', []),
            'healthy_exchanges': snap.get('healthy_exchanges', []),
            'degraded_exchanges': snap.get('degraded_exchanges', []),
            'failed_exchanges': snap.get('failed_exchanges', []),
            'markets_discovered': snap.get('markets_discovered', 0),
            'markets_validated': snap.get('markets_validated', 0),
            'tickers_received': snap.get('markets_validated', 0),
            'candidates_evaluated': snap.get('candidates_evaluated', 0),
            'opportunity_count': snap.get('opportunities_found', 0),
            'opportunities': snap.get('opportunities', []),
            'filter_rejection_count': len(rejections),
            'rejection_summary': dict(rejection_counts.most_common(12)),
            'validation_mode': filters.validation_mode,
            'filters': {
                'min_gap': filters.min_gap,
                'min_net_profit': filters.min_net_profit,
                'min_volume': filters.min_volume,
                'min_liquidity': filters.min_liquidity,
                'max_data_age': filters.max_data_age,
                'require_fees': filters.require_fees,
                'quote_currency': filters.quote_currency,
                'selected_coins': sorted(filters.selected_coins),
            },
            'diagnostics': snap.get('diagnostics', []),
            'warnings': snap.get('warnings', []),
            'errors': snap.get('errors', []),
        }
        return await self.ai.analyze(
            mode,
            'Analyze only the supplied deterministic scan snapshot. Never invent market facts.',
            payload,
        )

    async def order_route(self, user_id, scan_id, index):
        snap = await self.repo.get_scan(user_id, scan_id)
        if not snap:
            raise ValueError('Scan snapshot not found')
        opportunities = snap.get('opportunities', [])
        if index < 0 or index >= len(opportunities):
            raise ValueError('Invalid opportunity')
        o = opportunities[index]
        buy = self.exchanges.get(str(o['buy_exchange']).lower())
        sell = self.exchanges.get(str(o['sell_exchange']).lower())
        if not buy or not sell:
            raise RuntimeError('Required exchange adapter is unavailable')
        timeout = max(3.0, min(float(self.settings.scan_timeout_seconds), 30.0))
        buy_book, sell_book = await asyncio.wait_for(
            asyncio.gather(
                buy.get_orderbook(o['symbol'], limit=10),
                sell.get_orderbook(o['symbol'], limit=10),
                return_exceptions=True,
            ),
            timeout=timeout,
        )
        return {
            'symbol': o['symbol'],
            'buy_exchange': o['buy_exchange'],
            'sell_exchange': o['sell_exchange'],
            'buy': buy_book,
            'sell': sell_book,
        }
