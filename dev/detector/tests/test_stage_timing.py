"""R11/T12 deterministic internal stage timings; no GPU/SLO claim."""
import asyncio
import json
import threading

import httpx
import pytest

from detector import timing
from detector.tests.test_inspection_api import app_for, request, payload, HEADERS
from detector.tests.test_gliner_backend import backend, manifest


class Clock:
    def __init__(self): self.now=0.0
    def __call__(self): return self.now
    def advance(self,seconds): self.now+=seconds


def test_completed_and_error_intervals_are_accumulated_without_text():
    clock=Clock(); reports=[]
    with timing.observe(reports.append,clock=clock):
        with timing.stage("preprocessing"): clock.advance(0.002)
        with pytest.raises(ValueError):
            with timing.stage("model_call"):
                clock.advance(0.003)
                raise ValueError("PRIVATE")
        clock.advance(0.001)
    report=reports[0]
    assert report["total_ms"] == pytest.approx(6)
    assert report["stages"]["preprocessing"] == {"elapsed_ms":2.0,"completed":1,"failed":0,"active_at_snapshot":0}
    assert report["stages"]["model_call"]["elapsed_ms"] == pytest.approx(3)
    assert report["stages"]["model_call"]["failed"] == 1
    assert "PRIVATE" not in json.dumps(report)


def test_timeout_freezes_active_thread_span_and_context_propagates():
    async def run():
        clock=Clock(); reports=[]; started=threading.Event(); finish=threading.Event()
        def worker():
            with timing.stage("model_call"):
                started.set(); finish.wait(2)
                clock.advance(100)
        with timing.observe(reports.append,clock=clock):
            task=asyncio.create_task(asyncio.to_thread(worker))
            await asyncio.to_thread(started.wait,2)
            clock.advance(0.004)
            timing.outcome("error")
        before=json.dumps(reports)
        finish.set(); await task
        assert json.dumps(reports) == before
        assert reports[0]["stages"]["model_call"]["active_at_snapshot"] == 1
        assert reports[0]["stages"]["model_call"]["elapsed_ms"] == 4
    asyncio.run(run())


def test_observer_failure_does_not_change_complete_response():
    def observer(value): raise RuntimeError("PRIVATE")
    response=request(app_for(timing_observer=observer),payload())
    assert response.status_code == 200 and response.json()["status"] == "complete"
    assert set(response.json()["timing_breakdown_ms"]) == {"queue","inference","rules","total"}


def test_gliner_stage_boundaries_are_composite_and_do_not_change_results(manifest):
    loaded,model=backend(manifest); clock=Clock(); reports=[]
    plan=loaded._plan; predict=model.predict_entities
    def measured_plan(*args): clock.advance(0.002); return plan(*args)
    def measured_predict(*args,**kwargs): clock.advance(0.003); return predict(*args,**kwargs)
    loaded._plan=measured_plan; model.predict_entities=measured_predict
    with timing.observe(reports.append,clock=clock):
        result=loaded.inspect([{"segment_id":"s","text":"홍길동"}])
    assert result.status == "complete" and len(result.findings) == 1
    assert reports[0]["stages"]["preprocessing"]["elapsed_ms"] == 2
    assert reports[0]["stages"]["model_call"]["elapsed_ms"] == 3
    assert reports[0]["stages"]["postprocessing"]["completed"] == 1
    assert "홍길동" not in json.dumps(reports,ensure_ascii=False)


def test_queue_timeout_records_elapsed_wait_and_internal_outcome():
    async def run():
        entered=asyncio.Event(); finish=asyncio.Event(); reports=[]
        async def backend(segments): entered.set(); await finish.wait(); return []
        app=app_for(backend,queue_timeout_s=0.01,timing_observer=reports.append)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            first=asyncio.create_task(client.post("/v1/inspect",json=payload(),headers=HEADERS))
            await entered.wait()
            second=await client.post("/v1/inspect",json=payload(),headers=HEADERS)
            assert second.json()["error_code"] == "queue_timeout"
            assert second.json()["timing_breakdown_ms"]["queue"] > 0
            assert reports[0]["outcome"] == "error"
            assert reports[0]["stages"]["queue"]["elapsed_ms"] > 0
            finish.set(); await first
    asyncio.run(run())


@pytest.mark.parametrize("mode",["error","incomplete","cancelled","timeout"])
def test_api_noncomplete_outcomes_remain_fail_closed_and_observed(mode):
    from detector.schemas import BackendResult
    reports=[]
    async def backend(segments):
        with timing.stage("model_call"):
            if mode == "error": raise RuntimeError("PRIVATE")
            if mode == "cancelled": raise asyncio.CancelledError()
            if mode == "timeout": await asyncio.sleep(1)
            return BackendResult(status="incomplete")
    response=request(app_for(backend,timing_observer=reports.append,inspection_timeout_s=0.01),payload())
    assert response.json()["status"] != "complete"
    assert reports[0]["outcome"] == ("incomplete" if mode == "incomplete" else "error")
    assert reports[0]["stages"]["model_call"]["elapsed_ms"] >= 0
    assert "PRIVATE" not in response.text+json.dumps(reports)


def test_request_cancellation_observed_once_and_capacity_recovers():
    async def run():
        entered=asyncio.Event(); reports=[]
        async def backend(segments): entered.set(); await asyncio.sleep(10)
        app=app_for(backend,timing_observer=reports.append)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            task=asyncio.create_task(client.post("/v1/inspect",json=payload(),headers=HEADERS))
            await entered.wait(); task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        assert len(reports) == 1 and reports[0]["outcome"] == "cancelled"
    asyncio.run(run())


def test_nested_intervals_are_not_forced_to_sum_to_total():
    reports=[]; clock=Clock()
    with timing.observe(reports.append,clock=clock):
        with timing.stage("preprocessing"):
            clock.advance(0.001)
            with timing.stage("model_call"): clock.advance(0.002)
        timing.outcome("complete")
    assert reports[0]["total_ms"] == 3
    assert sum(stage["elapsed_ms"] for stage in reports[0]["stages"].values()) == 5


def test_default_disabled_and_observer_mutation_do_not_leak_to_next_request():
    with timing.observe(None):
        with timing.stage("model_call"): pass
    reports=[]
    def observer(report):
        reports.append(report)
        report["stages"]["queue"]["completed"]=999
    request(app_for(timing_observer=observer),payload())
    clean=[]
    request(app_for(timing_observer=clean.append),payload())
    assert clean[0]["stages"]["queue"]["completed"] == 1


def test_model_exception_interval_is_recorded(manifest):
    from detector.gliner_backend import ModelBackendError
    loaded,model=backend(manifest); clock=Clock(); reports=[]
    def fail(*args,**kwargs): clock.advance(0.007); raise RuntimeError("PRIVATE")
    model.predict_entities=fail
    with timing.observe(reports.append,clock=clock):
        with pytest.raises(ModelBackendError): loaded.inspect([{"segment_id":"s","text":"홍길동"}])
    assert reports[0]["stages"]["model_call"]["elapsed_ms"] == 7
    assert reports[0]["stages"]["model_call"]["failed"] == 1
    assert reports[0]["stages"]["postprocessing"]["completed"] == 0


def test_uncaught_error_is_not_misreported_as_cancellation():
    reports=[]
    with pytest.raises(ValueError):
        with timing.observe(reports.append): raise ValueError("PRIVATE")
    assert reports[0]["outcome"] == "error"
