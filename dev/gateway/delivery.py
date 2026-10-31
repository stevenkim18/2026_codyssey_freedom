"""R08 local metadata replay; no C-owned HTTP protocol is defined here.

The injected async ``send_batch(events)`` adapter returns a BatchReceipt only
after the central store durably confirms those event IDs. Replaying the same IDs
requires central idempotency. ``sent`` counts attempted records, not proven
central receipt; ``acknowledged`` counts records durably removed locally.

No forwarding decision depends on this module. Failures retain unacknowledged
records, and cancellation propagates without accepting a late receipt. There is
no automatic worker, network adapter, or claim of deployed C integration.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math

from .journal import _validate_event


MAX_BATCH_BYTES = 1024 * 1024


@dataclass(frozen=True)
class BatchReceipt:
    acknowledged_ids: tuple[str, ...] | list[str]


@dataclass(frozen=True)
class DeliveryReport:
    sent: int
    acknowledged: int
    error_code: str | None = None


def _require_active():
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class LocalEventShipper:
    """Send one bounded committed prefix and accept only a validated receipt.

    ``run_once`` is serialized per shipper. Scheduling/backoff and the actual
    authenticated central adapter remain caller responsibilities. A failed or
    ambiguous attempt may be retried explicitly with unchanged event IDs.
    """

    def __init__(self, journal, send_batch, *, batch_size=100, send_timeout_s=10):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("invalid delivery batch size")
        if (type(send_timeout_s) not in (int, float) or not 0 < send_timeout_s <= 120
                or not math.isfinite(send_timeout_s)):
            raise ValueError("invalid delivery timeout")
        if not callable(send_batch):
            raise ValueError("invalid delivery adapter")
        self.journal, self.send_batch = journal, send_batch
        self.batch_size, self.send_timeout_s = batch_size, send_timeout_s
        self._lock = asyncio.Lock()
        self._pending_send = None

    def _bounded_batch(self, pending):
        if not isinstance(pending, list) or len(pending) > self.batch_size:
            raise ValueError
        selected, seen, size = [], set(), 2  # JSON array brackets.
        for event in pending:
            _validate_event(event)
            event_id = event["event_id"]
            if event_id in seen:
                raise ValueError
            seen.add(event_id)
            payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            required = len(payload) + bool(selected)
            if size + required > MAX_BATCH_BYTES:
                if not selected:
                    raise ValueError
                break
            selected.append(event)
            size += required
        return selected

    async def run_once(self) -> DeliveryReport:
        async with self._lock:
            if self._pending_send is not None and not self._pending_send.done():
                # A timed-out/cancelled adapter may still finish remotely. Keep
                # at most one such task; a late receipt is never acknowledged.
                return DeliveryReport(0, 0, "SEND_DRAINING")
            self._pending_send = None
            try:
                pending = self.journal.pending(limit=self.batch_size)
            except Exception:
                return DeliveryReport(0, 0, "JOURNAL_READ_FAILED")
            try:
                events = self._bounded_batch(pending)
            except Exception:
                return DeliveryReport(0, 0, "INVALID_BATCH")
            if not events:
                return DeliveryReport(0, 0)
            # Snapshot IDs before an adapter can mutate its independent payload.
            requested = frozenset(event["event_id"] for event in events)
            sent = len(events)
            _require_active()

            async def send():
                return await self.send_batch(events)

            def consume(task):
                if not task.cancelled():
                    task.exception()

            task = asyncio.create_task(send())
            self._pending_send = task
            task.add_done_callback(consume)
            try:
                done, _ = await asyncio.wait({task}, timeout=self.send_timeout_s)
                _require_active()
                if not done or task.cancelled():
                    return DeliveryReport(sent, 0, "CENTRAL_SEND_FAILED")
                receipt = task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                return DeliveryReport(sent, 0, "CENTRAL_SEND_FAILED")
            finally:
                if not task.done():
                    task.cancel()
                else:
                    self._pending_send = None
            if type(receipt) is not BatchReceipt:
                return DeliveryReport(sent, 0, "INVALID_RECEIPT")
            acknowledged = receipt.acknowledged_ids
            if (type(acknowledged) not in (list, tuple) or len(acknowledged) > sent
                    or any(type(event_id) is not str or event_id not in requested for event_id in acknowledged)
                    or len(set(acknowledged)) != len(acknowledged)):
                return DeliveryReport(sent, 0, "INVALID_RECEIPT")
            if not acknowledged:
                return DeliveryReport(sent, 0)
            _require_active()
            try:
                self.journal.acknowledge(list(acknowledged))
            except Exception:
                return DeliveryReport(sent, 0, "JOURNAL_ACK_FAILED")
            return DeliveryReport(sent, len(acknowledged))
