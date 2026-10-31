"""Independent replay lifecycle generation ownership regression."""
import asyncio
from types import SimpleNamespace

from gateway.delivery import LocalEventShipper
from gateway.replay import ReplayWorker


async def test_old_run_cleanup_cannot_cancel_concurrently_started_generation():
    entered = asyncio.Event()
    fail_first = asyncio.Event()
    sleeps = 0
    async def sleep(delay):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            entered.set()
            await fail_first.wait()
            raise RuntimeError("synthetic first generation failure")
        await asyncio.Event().wait()
    async def unused_send(events):
        raise AssertionError("empty journal must not send")
    journal = SimpleNamespace(pending=lambda limit: [])
    worker = ReplayWorker(LocalEventShipper(journal, unused_send), sleep=sleep)
    lifetime = asyncio.create_task(worker.run())
    await asyncio.wait_for(entered.wait(), 1)
    first = worker._task
    await worker._lifecycle.acquire()
    replacement = asyncio.create_task(worker.start())
    try:
        await asyncio.sleep(0)  # replacement queues before old run's cleanup
        fail_first.set()
        await asyncio.wait_for(first, 1)
        await asyncio.sleep(0)
    finally:
        worker._lifecycle.release()
    second = await asyncio.wait_for(replacement, 1)
    await asyncio.wait_for(lifetime, 1)
    try:
        assert second is not first
        assert not second.done(), "old run cleanup cancelled the new generation"
        assert worker.running
    finally:
        await worker.stop()
