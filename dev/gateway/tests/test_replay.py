"""R08/T08 bounded automatic local replay; no C protocol or external calls."""
import asyncio
import pytest

from gateway.delivery import BatchReceipt, LocalEventShipper
from gateway.journal import Journal
from gateway.replay import ReplayWorker
from gateway.tests.test_journal import event


class ManualTimer:
    def __init__(self):
        self.now=0.0
        self.requests=asyncio.Queue()
    def clock(self): return self.now
    async def sleep(self,delay):
        gate=asyncio.Event()
        await self.requests.put((delay,gate))
        await gate.wait()
    async def next(self): return await asyncio.wait_for(self.requests.get(),1)
    def wake(self,request,elapsed=None):
        delay,gate=request; self.now+=delay if elapsed is None else elapsed; gate.set()


async def test_outage_backoff_is_bounded_and_empty_queue_never_busyloops(tmp_path):
    timer=ManualTimer(); calls=[]
    async def send(events): calls.append(events); raise RuntimeError("PRIVATE")
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        worker=ReplayWorker(LocalEventShipper(journal,send),min_delay_s=1,max_delay_s=4,clock=timer.clock,sleep=timer.sleep)
        task=await worker.start()
        assert await worker.start() is task
        for expected in (1,2,4,4):
            request=await timer.next(); assert request[0] == expected
            assert len(journal.pending()) == 1
            timer.wake(request)
        await timer.next(); await worker.stop(); await worker.stop()
        count=len(calls); await asyncio.sleep(0)
        assert len(calls) == count and not worker.running


async def test_partial_ack_progress_resets_backoff_and_only_confirmed_ids_disappear(tmp_path):
    timer=ManualTimer(); fail=True
    async def send(events):
        nonlocal fail
        if fail: fail=False; raise ConnectionError("PRIVATE")
        return BatchReceipt([events[0]["event_id"]])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event("one")); journal.append(event("two"))
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); first=await timer.next(); timer.wake(first)
        second=await timer.next(); assert second[0] == 1 and [e["event_id"] for e in journal.pending()] == ["two"]
        timer.wake(second); third=await timer.next(); assert third[0] == 1 and not journal.pending()
        timer.wake(third); empty=await timer.next(); assert empty[0] == 1
        timer.wake(empty); later=await timer.next(); assert later[0] == 2
        await worker.stop()


async def test_early_timer_wakeup_cannot_send_before_deadline(tmp_path):
    timer=ManualTimer(); calls=[]
    async def send(events): calls.append(events); return BatchReceipt([])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); first=await timer.next(); timer.wake(first,elapsed=.25)
        remaining=await timer.next(); assert remaining[0] == .75 and len(calls) == 1
        await worker.stop()


async def test_stop_cancel_resistant_send_preserves_event_and_restart_does_not_duplicate_pending(tmp_path):
    timer=ManualTimer(); entered=asyncio.Event(); cancelled=asyncio.Event(); release=asyncio.Event(); calls=[]
    async def send(events):
        calls.append(events)
        if len(calls) == 1:
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set(); await release.wait()
        return BatchReceipt([events[0]["event_id"]])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); await entered.wait(); await asyncio.wait_for(worker.stop(),1); await cancelled.wait()
        assert len(journal.pending()) == 1
        await worker.start(); draining=await timer.next(); assert len(calls) == 1 and worker.last_report.error_code == "SEND_DRAINING"
        release.set(); await asyncio.sleep(0); await asyncio.sleep(0)
        assert len(journal.pending()) == 1
        timer.wake(draining); await timer.next()
        assert len(calls) == 2 and not journal.pending()
        await worker.stop()


async def test_persisted_outage_events_are_replayed_by_new_worker_after_restart(tmp_path):
    timer=ManualTimer(); path=tmp_path / "events.sqlite3"
    async def offline(events): raise ConnectionError("PRIVATE")
    with Journal(path) as journal:
        journal.append(event("stable"))
        worker=ReplayWorker(LocalEventShipper(journal,offline),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); await timer.next(); await worker.stop()
    seen=[]
    async def online(events): seen.extend(e["event_id"] for e in events); return BatchReceipt(seen[:])
    with Journal(path) as journal:
        worker=ReplayWorker(LocalEventShipper(journal,online),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); await timer.next(); await worker.stop()
        assert seen == ["stable"] and not journal.pending()


@pytest.mark.parametrize("settings",[{"min_delay_s":0},{"max_delay_s":0},{"min_delay_s":True},{"max_delay_s":float("inf")},
    {"min_delay_s":2,"max_delay_s":1},{"clock":None},{"sleep":None}])
def test_invalid_schedule_is_rejected(settings):
    with pytest.raises(ValueError): ReplayWorker(LocalEventShipper(None,lambda _:None),**settings)


def test_worker_requires_explicit_bounded_shipper():
    with pytest.raises(ValueError): ReplayWorker(None)


async def test_concurrent_start_and_stop_own_exactly_one_task(tmp_path):
    timer=ManualTimer()
    async def send(events): pytest.fail("empty journal must not send")
    with Journal(tmp_path / "events.sqlite3") as journal:
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=timer.clock,sleep=timer.sleep)
        await worker.stop()
        tasks=await asyncio.gather(*(worker.start() for _ in range(20)))
        assert all(task is tasks[0] for task in tasks)
        await timer.next()
        await asyncio.gather(*(worker.stop() for _ in range(20)))
        assert tasks[0].done() and not worker.running
        second=await worker.start(); assert second is not tasks[0]
        await timer.next(); await worker.stop()


async def test_run_cancellation_joins_worker_and_preserves_events(tmp_path):
    entered=asyncio.Event()
    async def send(events): entered.set(); await asyncio.Event().wait()
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        worker=ReplayWorker(LocalEventShipper(journal,send))
        lifetime=asyncio.create_task(worker.run()); await entered.wait(); lifetime.cancel()
        with pytest.raises(asyncio.CancelledError): await lifetime
        assert not worker.running and len(journal.pending()) == 1
        await worker.stop()


@pytest.mark.parametrize("clock",[lambda:float("nan"),lambda:float("inf"),lambda:True,lambda:"clock"])
async def test_invalid_clock_stops_without_unbounded_attempts(tmp_path,clock,caplog):
    calls=[]
    async def send(events): calls.append(events); raise RuntimeError("PRIVATE")
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=clock)
        task=await worker.start(); await asyncio.wait_for(task,1)
        assert worker.error_code == "REPLAY_SCHEDULER_FAILED" and len(calls) == 1
        assert len(journal.pending()) == 1 and "PRIVATE" not in caplog.text
        await worker.stop()


@pytest.mark.parametrize("mode",["stationary","backward","raises"])
async def test_broken_sleep_clock_cannot_busyloop(tmp_path,mode):
    now=[2]
    async def sleep(delay):
        if mode == "raises": raise RuntimeError("PRIVATE")
        if mode == "backward": now[0]-=1
    async def send(events): pytest.fail("empty journal")
    with Journal(tmp_path / "events.sqlite3") as journal:
        worker=ReplayWorker(LocalEventShipper(journal,send),clock=lambda:now[0],sleep=sleep)
        await asyncio.wait_for(worker.run(),1)
        assert worker.error_code == "REPLAY_SCHEDULER_FAILED" and not worker.running


async def test_unexpected_shipper_failure_is_backed_off_without_raw_error(tmp_path):
    timer=ManualTimer()
    class FailingShipper(LocalEventShipper):
        async def run_once(self): raise RuntimeError("PRIVATE")
    worker=ReplayWorker(FailingShipper(None,lambda _:None),clock=timer.clock,sleep=timer.sleep)
    await worker.start(); request=await timer.next()
    assert request[0] == 1 and worker.last_report.error_code == "REPLAY_ATTEMPT_FAILED"
    assert "PRIVATE" not in repr(worker.last_report)
    await worker.stop()


async def test_ack_failure_retries_same_event_without_new_journal_writes(tmp_path):
    timer=ManualTimer(); received=[]
    async def send(events): received.append(events[0]["event_id"]); return BatchReceipt([events[0]["event_id"]])
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event("stable"))
        class AckOutage:
            pending=journal.pending
            def acknowledge(self,ids): raise OSError("PRIVATE")
        worker=ReplayWorker(LocalEventShipper(AckOutage(),send),clock=timer.clock,sleep=timer.sleep)
        await worker.start(); first=await timer.next(); timer.wake(first); second=await timer.next()
        assert second[0] == 2 and received == ["stable","stable"] and len(journal.pending()) == 1
        assert worker.last_report.error_code == "JOURNAL_ACK_FAILED"
        await worker.stop()
