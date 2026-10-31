"""R08/T08: bounded local replay with an injected in-process receipt adapter.

Synthetic central storage below is not the C-owned HTTP event API contract.
"""
import asyncio
from copy import deepcopy
import json

import pytest

from gateway.delivery import BatchReceipt, LocalEventShipper
from gateway.journal import Journal, JournalError
from gateway.tests.test_journal import event


def ids(journal):
    return [item["event_id"] for item in journal.pending()]


async def test_empty_journal_performs_no_central_call(tmp_path):
    async def send(events):
        pytest.fail("empty replay must not call the central adapter")
    with Journal(tmp_path / "events.sqlite3") as journal:
        report = await LocalEventShipper(journal, send).run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (0, 0, None)


async def test_bounded_batch_partial_ack_deletes_only_confirmed_ids(tmp_path):
    received = []
    async def send(events):
        received.append(deepcopy(events))
        return BatchReceipt([events[-1]["event_id"]])
    with Journal(tmp_path / "events.sqlite3") as journal:
        for index in range(3):
            journal.append(event(f"event-{index}"))
        shipper = LocalEventShipper(journal, send, batch_size=2)
        report = await shipper.run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (2, 1, None)
        assert ids(journal) == ["event-0", "event-2"]
        assert received == [[event("event-0"), event("event-1")]]
        assert "raw_text" not in json.dumps(received)


async def test_central_outage_is_content_free_and_preserves_all_events(tmp_path, caplog):
    async def send(events):
        raise RuntimeError("SYNTHETIC_RAW_CENTRAL_ERROR")
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        report = await LocalEventShipper(journal, send).run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (1, 0, "CENTRAL_SEND_FAILED")
        assert ids(journal) == ["event-1"]
        assert "SYNTHETIC_RAW_CENTRAL_ERROR" not in repr(report) + caplog.text


@pytest.mark.parametrize("receipt", [None, {}, {"acknowledged_ids": ["event-1"]},
    BatchReceipt("event-1"), BatchReceipt(None), BatchReceipt(["event-1", "event-1"]),
    BatchReceipt(["event-1", "unknown"]), BatchReceipt(["event-1", 1]),
    BatchReceipt([True]), BatchReceipt(["not-sent"]), BatchReceipt([[]])])
async def test_malformed_receipt_never_deletes_even_its_valid_subset(tmp_path, receipt):
    async def send(events):
        return receipt
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        journal.append(event("not-sent"))
        report = await LocalEventShipper(journal, send, batch_size=1).run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (1, 0, "INVALID_RECEIPT")
        assert ids(journal) == ["event-1", "not-sent"]


@pytest.mark.parametrize("acknowledged_ids", [[], (), ["event-1"], ("event-1",)])
async def test_list_and_tuple_receipts_allow_empty_or_confirmed_ack(tmp_path, acknowledged_ids):
    async def send(events):
        return BatchReceipt(acknowledged_ids)
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        report = await LocalEventShipper(journal, send).run_once()
        assert report.error_code is None
        assert report.acknowledged == len(acknowledged_ids)
        assert ids(journal) == ([] if acknowledged_ids else ["event-1"])


async def test_restart_after_lost_receipt_replays_identical_committed_ids_and_deduplicates(tmp_path):
    path = tmp_path / "state" / "events.sqlite3"
    central, received_ids = {}, []
    lost_receipt = True
    async def send(events):
        nonlocal lost_receipt
        received_ids.append([item["event_id"] for item in events])
        for item in events:
            central.setdefault(item["event_id"], deepcopy(item))
        if lost_receipt:
            lost_receipt = False
            raise ConnectionError("SYNTHETIC_LOST_ACK_AFTER_COMMIT")
        return BatchReceipt(tuple(item["event_id"] for item in events))
    with Journal(path) as journal:
        journal.append(event("stable-1"))
        journal.append(event("stable-2"))
        report = await LocalEventShipper(journal, send).run_once()
        assert report.acknowledged == 0 and len(journal.pending()) == 2
    with Journal(path) as journal:
        report = await LocalEventShipper(journal, send).run_once()
        assert report.acknowledged == 2 and journal.pending() == []
    assert received_ids == [["stable-1", "stable-2"], ["stable-1", "stable-2"]]
    assert central == {"stable-1": event("stable-1"), "stable-2": event("stable-2")}


@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_caller_cancellation_cannot_ack_a_late_receipt(tmp_path, suppress_cancel):
    entered = asyncio.Event()
    async def send(events):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if not suppress_cancel:
                raise
            return BatchReceipt(["event-1"])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        pending = asyncio.create_task(LocalEventShipper(journal, send).run_once())
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert ids(journal) == ["event-1"]


@pytest.mark.parametrize("suppress_cancel", [False, True])
async def test_send_deadline_never_accepts_late_receipt(tmp_path, suppress_cancel):
    async def send(events):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            if not suppress_cancel:
                raise
            asyncio.current_task().uncancel()
            return BatchReceipt(["event-1"])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        report = await LocalEventShipper(journal, send, send_timeout_s=0.01).run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (1, 0, "CENTRAL_SEND_FAILED")
        assert ids(journal) == ["event-1"]


async def test_adapter_mutation_cannot_authorize_deletion_of_unsent_id(tmp_path):
    async def send(events):
        events[0]["event_id"] = "not-sent"
        return BatchReceipt(["not-sent"])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        journal.append(event("not-sent"))
        report = await LocalEventShipper(journal, send, batch_size=1).run_once()
        assert report.error_code == "INVALID_RECEIPT"
        assert ids(journal) == ["event-1", "not-sent"]


async def test_payload_bytes_are_bounded_without_dropping_pending_metadata(tmp_path):
    received = []
    async def send(events):
        received.extend(deepcopy(events))
        assert len(json.dumps(events, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()) <= 1024 * 1024
        return BatchReceipt([item["event_id"] for item in events])
    with Journal(tmp_path / "events.sqlite3") as journal:
        for index in range(100):
            value = event(f"event-{index}")
            value["rule_ids"] = ["r" * 128] * 64
            value["findings"] = [{"type": "PERSON", "count": 1000000}] * 64
            journal.append(value)
        report = await LocalEventShipper(journal, send, batch_size=100).run_once()
        assert 0 < report.sent < 100
        assert report.acknowledged == report.sent == len(received)
        assert len(journal.pending()) + report.acknowledged == 100


async def test_journal_read_failure_is_safe_and_never_calls_adapter():
    class BrokenJournal:
        def pending(self, **kwargs):
            raise JournalError("SYNTHETIC_RAW_STORAGE_ERROR")
    async def send(events):
        pytest.fail("unreadable journal must not be transmitted")
    report = await LocalEventShipper(BrokenJournal(), send).run_once()
    assert (report.sent, report.acknowledged, report.error_code) == (0, 0, "JOURNAL_READ_FAILED")


async def test_ack_storage_failure_reports_no_durable_ack_and_preserves_records(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        class BrokenAck:
            pending = journal.pending
            def acknowledge(self, ids):
                raise JournalError("SYNTHETIC_RAW_STORAGE_ERROR")
        async def send(events):
            return BatchReceipt(["event-1"])
        report = await LocalEventShipper(BrokenAck(), send).run_once()
        assert (report.sent, report.acknowledged, report.error_code) == (1, 0, "JOURNAL_ACK_FAILED")
        assert ids(journal) == ["event-1"]


@pytest.mark.parametrize("batch_size", [0, 101, True, 1.0, "1"])
def test_batch_size_is_a_strict_bounded_count(tmp_path, batch_size):
    with pytest.raises(ValueError):
        LocalEventShipper(None, lambda events: None, batch_size=batch_size)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), "10", 121, 10**1000])
def test_send_timeout_is_a_finite_bounded_budget(timeout):
    with pytest.raises(ValueError):
        LocalEventShipper(None, lambda events: None, send_timeout_s=timeout)


def test_non_callable_adapter_is_rejected():
    with pytest.raises(ValueError):
        LocalEventShipper(None, None)


@pytest.mark.parametrize("pending", [None, {}, [event(), event()], [event() | {"raw_text": "SYNTHETIC_UNINSPECTED"}]])
async def test_invalid_pending_batch_is_rejected_without_transmission(pending):
    class InvalidJournal:
        def pending(self, **kwargs):
            return pending
    async def send(events):
        pytest.fail("invalid journal payload must not reach central storage")
    report = await LocalEventShipper(InvalidJournal(), send, batch_size=1).run_once()
    assert (report.sent, report.acknowledged, report.error_code) == (0, 0, "INVALID_BATCH")


async def test_duplicate_pending_ids_cannot_be_counted_as_two_confirmations():
    class InvalidJournal:
        def pending(self, **kwargs):
            return [event(), event()]
    async def send(events):
        pytest.fail("duplicate local IDs must not reach the adapter")
    report = await LocalEventShipper(InvalidJournal(), send).run_once()
    assert report.error_code == "INVALID_BATCH"


async def test_first_event_over_payload_budget_is_retained_without_transmission(tmp_path, monkeypatch):
    monkeypatch.setattr("gateway.delivery.MAX_BATCH_BYTES", 1)
    async def send(events):
        pytest.fail("oversized payload must not reach the adapter")
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        report = await LocalEventShipper(journal, send).run_once()
        assert report.error_code == "INVALID_BATCH"
        assert ids(journal) == ["event-1"]


async def test_adapter_cannot_clear_caller_cancellation_and_accept_a_late_receipt(tmp_path):
    entered = asyncio.Event()
    async def send(events):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()
            return BatchReceipt(["event-1"])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        pending = asyncio.create_task(LocalEventShipper(journal, send).run_once())
        await entered.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert ids(journal) == ["event-1"]


async def test_cancel_resistant_send_is_bounded_to_one_task_and_late_ack_is_discarded(tmp_path):
    cancelled, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def send(events):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
        return BatchReceipt(["event-1"])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        shipper = LocalEventShipper(journal, send, send_timeout_s=0.01)
        try:
            report = await shipper.run_once()
            assert report.error_code == "CENTRAL_SEND_FAILED"
            await cancelled.wait()
            draining = await shipper.run_once()
            assert (draining.sent, draining.acknowledged, draining.error_code) == (0, 0, "SEND_DRAINING")
            assert calls == 1 and ids(journal) == ["event-1"]
        finally:
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        assert ids(journal) == ["event-1"]
        report = await shipper.run_once()
        assert calls == 2 and report.acknowledged == 1 and journal.pending() == []


async def test_self_cancelled_adapter_is_a_central_failure_not_caller_cancellation(tmp_path):
    async def send(events):
        raise asyncio.CancelledError
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        report = await LocalEventShipper(journal, send).run_once()
        assert report.error_code == "CENTRAL_SEND_FAILED" and report.acknowledged == 0
        assert ids(journal) == ["event-1"]


async def test_concurrent_runs_do_not_duplicate_an_inflight_batch(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def send(events):
        calls.append(deepcopy(events))
        entered.set()
        await release.wait()
        return BatchReceipt([item["event_id"] for item in events])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        shipper = LocalEventShipper(journal, send)
        first = asyncio.create_task(shipper.run_once())
        await entered.wait()
        second = asyncio.create_task(shipper.run_once())
        await asyncio.sleep(0)
        assert len(calls) == 1
        release.set()
        first_report, second_report = await asyncio.gather(first, second)
        assert first_report.acknowledged == 1
        assert second_report.sent == second_report.acknowledged == 0
        assert len(calls) == 1 and journal.pending() == []


async def test_already_cancelled_caller_cannot_start_a_central_send(tmp_path):
    async def send(events):
        pytest.fail("a caller with pending cancellation must not start delivery")
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        shipper = LocalEventShipper(journal, send)
        async def cancelled_caller():
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                pass
            return await shipper.run_once()
        pending = asyncio.create_task(cancelled_caller())
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert ids(journal) == ["event-1"]
