import pytest

from arbitrage_terminal.infrastructure.repository import DEFAULT_FILTERS, Repository


@pytest.mark.asyncio
async def test_ensure_user_initializes_scanner_config(tmp_path):
    repo = Repository(str(tmp_path / 'test.db'))
    await repo.connect()
    try:
        await repo.ensure_user(12345, 'tester')
        row = await repo.user(12345)
        assert row is not None
        filters = repo.filters_from_row(row)
        assert filters.min_gap == DEFAULT_FILTERS['min_gap']
        assert filters.min_net_profit == DEFAULT_FILTERS['min_net_profit']
        assert filters.trade_size == DEFAULT_FILTERS['trade_size']
        assert filters.validation_mode == DEFAULT_FILTERS['validation_mode']
    finally:
        await repo.close()
