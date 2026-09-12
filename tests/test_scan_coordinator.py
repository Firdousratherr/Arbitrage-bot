import asyncio

import pytest

from arbitrage_terminal.application.scan_coordinator import ConcurrentScanCoordinator


@pytest.mark.asyncio
async def test_identical_concurrent_scans_run_once_for_many_users():
    coordinator = ConcurrentScanCoordinator()
    producer_calls = 0
    progress_calls = 0

    async def producer(progress):
        nonlocal producer_calls
        producer_calls += 1
        await progress('markets', {'markets': 10})
        await asyncio.sleep(0.03)
        return {'opportunities': 3}

    def make_progress():
        async def progress(stage, data):
            nonlocal progress_calls
            progress_calls += 1
        return progress

    progress_callbacks = [make_progress() for _ in range(20)]

    try:
        results = await asyncio.gather(*(
            coordinator.run('same-scan', producer, progress=progress)
            for progress in progress_callbacks
        ))
        await asyncio.sleep(0)
    finally:
        await coordinator.close()

    assert producer_calls == 1
    assert len(results) == 20
    assert all(result == {'opportunities': 3} for result in results)
    assert progress_calls == 20


@pytest.mark.asyncio
async def test_different_scan_keys_do_not_share_work():
    coordinator = ConcurrentScanCoordinator()
    producer_calls = 0

    async def producer(progress):
        nonlocal producer_calls
        producer_calls += 1
        call_number = producer_calls
        await asyncio.sleep(0.01)
        return call_number

    try:
        results = await asyncio.gather(
            coordinator.run('filters-a', producer),
            coordinator.run('filters-b', producer),
        )
    finally:
        await coordinator.close()

    assert producer_calls == 2
    assert sorted(results) == [1, 2]
