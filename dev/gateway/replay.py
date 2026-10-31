"""R08 bounded scheduling around the receipt-validating LocalEventShipper.

Explicit opt-in: construct with an authenticated adapter-backed shipper, then
await start()/stop(), or await run() for a cancellation-owned lifetime. No worker
is created at import, and no C-owned network protocol is defined here. The same
shipper is retained across restarts so cancellation-resistant adapter work stays
bounded by its SEND_DRAINING gate. Only the shipper can acknowledge journal IDs.

stop() cancels and joins the scheduling task; a cancellation-resistant adapter
may still drain under the shipper's ownership. It cannot authorize a late ack.
Do not close a journal until stop() completes. A later process can reopen the
persisted journal and replay unacknowledged IDs through an idempotent adapter.
"""
import asyncio
import math
import time

from .delivery import DeliveryReport, LocalEventShipper


class ReplayWorker:
    """One sequential attempt followed by positive, bounded retry delay.

Any durable acknowledgement counts as progress and resets delay to the minimum.
Empty queues, failed sends/acks, no receipts and SEND_DRAINING double the delay
up to max_delay_s. This never retries an external LLM operation. Injected clock
must be finite and monotonic; sleep must advance it when returning normally.
Broken scheduling dependencies stop the worker with a content-free error code.
"""

    def __init__(self, shipper, *, min_delay_s=1, max_delay_s=60,
                 clock=time.monotonic, sleep=asyncio.sleep):
        if not isinstance(shipper, LocalEventShipper):
            raise ValueError("explicit bounded event shipper required")
        for delay in (min_delay_s, max_delay_s):
            if type(delay) not in (int, float) or not 0 < delay <= 3600 or not math.isfinite(delay):
                raise ValueError("invalid replay delay")
        if min_delay_s > max_delay_s or not callable(clock) or not callable(sleep):
            raise ValueError("invalid replay schedule")
        self.shipper = shipper
        self.min_delay_s, self.max_delay_s = min_delay_s, max_delay_s
        self.clock, self.sleep = clock, sleep
        self.last_report = None
        self.error_code = None
        self._task = None
        self._lifecycle = asyncio.Lock()
        self._last_clock = None

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    def _now(self):
        value = self.clock()
        if (type(value) not in (int, float) or not math.isfinite(value)
                or self._last_clock is not None and value < self._last_clock):
            raise ValueError("invalid scheduler clock")
        self._last_clock = value
        return value

    async def _wait(self, delay):
        now = self._now()
        deadline = now + delay
        while now < deadline:
            await self.sleep(deadline-now)
            current = self._now()
            if current <= now:
                raise ValueError("scheduler sleep made no progress")
            now = current

    async def _loop(self):
        next_delay = self.min_delay_s
        self.error_code = None
        try:
            while True:
                try:
                    report = await self.shipper.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    report = DeliveryReport(0, 0, "REPLAY_ATTEMPT_FAILED")
                self.last_report = report
                if report.acknowledged > 0:
                    delay = next_delay = self.min_delay_s
                else:
                    delay = next_delay
                    next_delay = min(self.max_delay_s, next_delay*2)
                await self._wait(delay)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.error_code = "REPLAY_SCHEDULER_FAILED"

    async def start(self):
        """Start immediately, or return the existing task; no parallel workers."""
        async with self._lifecycle:
            if not self.running:
                self._task = asyncio.create_task(self._loop())
            return self._task

    async def stop(self):
        """Cancel and join the worker, serializing overlapping start/stop calls."""
        await self._stop_task()

    async def _stop_task(self, expected=None):
        async with self._lifecycle:
            task = self._task
            # run() owns its captured generation. A queued start may already
            # have replaced a completed task before this cleanup gets the lock.
            # Public stop() instead targets whichever generation owns the lock.
            if expected is not None and task is not expected:
                return
            if task is None:
                return
            if not task.done():
                task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            if not task.cancelled():
                task.exception()
            caller = asyncio.current_task()
            if caller is not None and caller.cancelling():
                raise asyncio.CancelledError

    async def run(self):
        """Keep running until cancelled; cancelling this waiter stops its worker."""
        task = await self.start()
        try:
            await asyncio.shield(task)
        finally:
            await self._stop_task(expected=task)
