import pytest
from arbitrage_terminal.infrastructure.retry import retry_async


@pytest.mark.asyncio
async def test_retry_retries_transient_failure():
    state = {'n': 0}
    async def fn():
        state['n'] += 1
        if state['n'] < 3:
            raise RuntimeError('temporary')
        return 'ok'
    assert await retry_async(fn, retries=2) == 'ok'
    assert state['n'] == 3


@pytest.mark.asyncio
async def test_retry_stops_after_limit():
    state = {'n': 0}
    async def fn():
        state['n'] += 1
        raise RuntimeError('permanent')
    with pytest.raises(RuntimeError):
        await retry_async(fn, retries=2)
    assert state['n'] == 3
