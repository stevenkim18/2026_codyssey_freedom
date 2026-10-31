"""Synthetic profiler control-plane tests; no model/GPU performance evidence."""
import asyncio
import json
from pathlib import Path
import sys
import subprocess
import time

import httpx
import pytest

from integration import profile_detector as profile


SERVICE = {"origin": "https://gpu.example", "token": "PRIVATE-TOKEN", "model_version": "m1", "threshold_version": "t1"}


def test_plan_is_bounded_and_counts_warmups_separately():
    plan = profile.make_plan(samples=5, warmups=2, sizes=(2000, 16000), concurrency=(1, 2, 4), budget_s=120)
    assert plan["measurement_requests"] == 30 and plan["warmup_requests"] == 4
    assert plan["max_request_count"] == 36 and plan["max_pending_requests"] == 4
    assert plan["network_enabled"] is False


@pytest.mark.parametrize("changes", [{"samples": True}, {"samples": 0}, {"samples": 101}, {"warmups": -1},
                                    {"warmups": 11}, {"budget_s": float("inf")}, {"budget_s": 121},
                                    {"sizes": (16001,)}, {"concurrency": (8,)}, {"concurrency": (1, 1)}])
def test_invalid_plan_fails_sanitized(changes):
    with pytest.raises(profile.ProfileError):
        profile.make_plan(**changes)


@pytest.mark.parametrize("length", [2000, 16000])
def test_workload_has_exact_original_codepoint_count(length):
    text = profile.synthetic_text(length)
    assert len(text) == length and "e\u0301" in text and "👩‍💻" in text
    assert text == profile.synthetic_text(length)


def test_nearest_rank_quantile_and_sample_gate():
    assert profile.percentile([1, 2, 3, 4], 0.5) == 2
    assert profile.percentile(list(range(1, 21)), 0.95) == 19
    records = [{"status": "complete", "elapsed_ms": 400.0,
                "server_timing_breakdown_ms": {"queue":0.0,"inference":1.0,"rules":0.0,"total":1.0}}] * 20
    assert profile.summarize(records, size=2000, concurrency=1, warmed=True)["target_assessment"] == "met_in_observed_sample"
    assert profile.summarize(records[:19], size=2000, concurrency=1, warmed=True)["target_assessment"] == "insufficient_samples"
    assert profile.summarize(records, size=16000, concurrency=1, warmed=True)["target_assessment"] == "descriptive_only"
    assert profile.summarize(records, size=2000, concurrency=2, warmed=True)["target_assessment"] == "descriptive_only"
    assert profile.summarize(records + [{"status": "timeout", "elapsed_ms": 1.0}], size=2000, concurrency=1, warmed=True)["target_assessment"] == "failed_requests_present"
    assert profile.summarize(records, size=2000, concurrency=1, warmed=False)["target_assessment"] == "not_warmed"


def response(request, **change):
    body = json.loads(request.content)
    return httpx.Response(200, json={"schema_version": "1.0", "request_id": body["request_id"],
        "inspection_id": body["inspection_id"], "status": "complete", "findings": [], "model_version": "m1",
        "threshold_version": "t1", "timing_ms": 1.0,
        "timing_breakdown_ms": {"queue": 0.0, "inference": 1.0, "rules": 0.0, "total": 1.0}} | change)


async def test_concurrency_cap_and_separate_warmup_samples():
    active, maximum, calls = 0, 0, []
    async def handler(request):
        nonlocal active, maximum
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"status": "alive" if request.url.path == "/healthz" else "ready"})
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.002)
        active -= 1
        return response(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await profile.profile_client(client, SERVICE, profile.make_plan(samples=9, warmups=2, sizes=(2000,), concurrency=(4,)))
    assert maximum == 4 and active == 0 and len(calls) == 13
    assert len(result["warmups"]) == 2 and len(result["measurements"]) == 9
    assert "PRIVATE-TOKEN" not in json.dumps(result) and "gpu.example" not in json.dumps(result)
    assert result["first_request"]["phase"] == "warmup"


@pytest.mark.parametrize("mode, expected", [("drift", "error"), ("incomplete", "incomplete"), ("timeout", "timeout")])
async def test_failed_requests_are_retained_without_retry(mode, expected):
    calls = []
    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"status": "alive" if request.url.path == "/healthz" else "ready"})
        calls.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("PRIVATE-TOKEN")
        return response(request, **({"model_version": "stale"} if mode == "drift" else {"status": "incomplete"}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await profile.profile_client(client, SERVICE, profile.make_plan(samples=2, warmups=0, sizes=(2000,), concurrency=(1,)))
    assert len(calls) == 2 and all(record["status"] == expected for record in result["measurements"])
    assert "PRIVATE-TOKEN" not in json.dumps(result)


async def test_whole_budget_cancels_drains_and_records_unstarted_samples():
    active = 0
    async def handler(request):
        nonlocal active
        if request.method == "GET":
            return httpx.Response(200, json={"status": "alive" if request.url.path == "/healthz" else "ready"})
        active += 1
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await profile.profile_client(client, SERVICE, profile.make_plan(samples=20, warmups=0, sizes=(2000,), concurrency=(4,), budget_s=0.02))
    assert active == 0 and result["budget_exhausted"] is True
    assert len(result["measurements"]) == 4 and result["not_started_measurements"] == 16
    assert all(item["status"] == "cancelled" for item in result["measurements"])
    assert all(stage["sample_count"] == 0 and stage["p95_ms"] is None
               for stage in result["summaries"][0]["server_stage_timing_ms"].values())


def private_config(tmp_path):
    private = tmp_path / ".private"
    private.mkdir(mode=0o700)
    path = private / "profile.json"
    path.write_text(json.dumps({"schema_version": "1.0", "gpu": SERVICE}))
    path.chmod(0o600)
    return path


def test_default_plan_never_opens_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("default plan opened network")
    monkeypatch.setattr(profile.httpx, "AsyncClient", forbidden)
    output = tmp_path / "plan.json"
    assert profile.main([str(private_config(tmp_path)), "--output", str(output)]) == 0
    data = json.loads(output.read_text())
    assert data["network_enabled"] is False and "PRIVATE-TOKEN" not in output.read_text()


def test_cli_parse_error_does_not_echo_private_argument():
    result = subprocess.run([sys.executable, "-m", "integration.profile_detector", "--samples", "PRIVATE-TOKEN"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0 and "PRIVATE-TOKEN" not in result.stdout + result.stderr


@pytest.mark.parametrize("values", [[float("nan")], [float("inf")], [-1.0], [True], []])
def test_percentile_rejects_nonfinite_or_untyped_samples(values):
    with pytest.raises(profile.ProfileError):
        profile.percentile(values, 0.95)


async def test_synchronous_completion_past_whole_budget_is_not_success():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"status": "alive" if request.url.path == "/healthz" else "ready"})
        time.sleep(0.03)
        return response(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await profile.profile_client(client, SERVICE, profile.make_plan(samples=1, warmups=0, sizes=(2000,), concurrency=(1,), budget_s=0.01))
    assert result["budget_exhausted"] is True and result["status"] != "completed"
    assert result["measurements"][0]["status"] != "complete"


async def test_external_cancellation_drains_tasks_and_restores_hooks():
    active = 0
    entered = asyncio.Event()
    async def handler(request):
        nonlocal active
        if request.method == "GET":
            return httpx.Response(200, json={"status": "alive" if request.url.path == "/healthz" else "ready"})
        active += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hooks = client.event_hooks["response"][:]
        task = asyncio.create_task(profile.profile_client(client, SERVICE, profile.make_plan(samples=20, warmups=0, sizes=(2000,), concurrency=(4,))))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0 and client.event_hooks["response"] == hooks


def test_existing_output_refuses_network_and_preserves_evidence(monkeypatch, tmp_path):
    async def forbidden(*args):
        pytest.fail("network invoked before output reservation")
    monkeypatch.setattr(profile, "run", forbidden)
    output = tmp_path / "evidence.json"
    output.write_text("preserve")
    assert profile.main([str(private_config(tmp_path)), "--run", "--output", str(output)]) == 1
    assert output.read_text() == "preserve"


def test_server_stage_nearest_rank_counts_and_missing_evidence_gate():
    records=[{"status":"complete","elapsed_ms":100,"server_timing_breakdown_ms":
              {"queue":value,"inference":value*2,"rules":0.0,"total":value*3}} for value in range(1,21)]
    summary=profile.summarize(records,size=2000,concurrency=1,warmed=True)
    assert summary["server_stage_timing_ms"]["queue"] == {"sample_count":20,"p50_ms":10,"p95_ms":19}
    assert summary["server_stage_timing_ms"]["inference"]["p95_ms"] == 38
    assert summary["server_timing_excluded_complete_count"] == 0
    assert summary["target_assessment"] == "met_in_observed_sample"
    missing=[{"status":"complete","elapsed_ms":100}]*20
    summary=profile.summarize(missing,size=2000,concurrency=1,warmed=True)
    assert summary["server_timing_excluded_complete_count"] == 20
    assert summary["server_stage_timing_ms"]["queue"] == {"sample_count":0,"p50_ms":None,"p95_ms":None}
    assert summary["target_assessment"] == "invalid_server_timing_evidence"


@pytest.mark.parametrize("timing",[None,{}, {"queue":-1,"inference":1,"rules":0,"total":1},
    {"queue":True,"inference":1,"rules":0,"total":1},
    {"queue":float("inf"),"inference":1,"rules":0,"total":1},
    {"queue":"1","inference":1,"rules":0,"total":1}])
def test_summary_never_uses_malformed_or_failed_timing(timing):
    record={"status":"complete","elapsed_ms":1,"server_timing_breakdown_ms":timing}
    failed={"status":"error","elapsed_ms":2,"server_timing_breakdown_ms":{"queue":999,"inference":999,"rules":999,"total":999}}
    summary=profile.summarize([record,failed],size=2000,concurrency=1,warmed=True,planned=4)
    assert summary["failure_count"] == 1 and summary["not_started"] == 2
    assert summary["server_timing_excluded_complete_count"] == 1
    assert all(value["sample_count"] == 0 and value["p95_ms"] is None for value in summary["server_stage_timing_ms"].values())


async def test_mock_https_stage_evidence_excludes_warmup_failed_and_missing_responses():
    calls=0
    async def handler(request):
        nonlocal calls
        if request.method == "GET":
            return httpx.Response(200,json={"status":"alive" if request.url.path == "/healthz" else "ready"})
        calls+=1
        stages={"queue":float(calls),"inference":2.0,"rules":0.0,"total":float(calls)+2}
        if calls == 3: return response(request,status="incomplete",timing_breakdown_ms=stages)
        if calls == 4:
            value=json.loads(response(request).content); value.pop("timing_breakdown_ms")
            return httpx.Response(200,json=value)
        return response(request,timing_breakdown_ms=stages)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await profile.profile_client(client,SERVICE,profile.make_plan(samples=3,warmups=1,sizes=(2000,),concurrency=(1,)))
    assert calls == 4 and result["status"] == "partial_or_failed"
    assert result["warmups"][0]["server_timing_breakdown_ms"]["queue"] == 1
    assert result["measurements"][0]["server_timing_breakdown_ms"]["queue"] == 2
    assert all("server_timing_breakdown_ms" not in value for value in result["measurements"][1:])
    summary=result["summaries"][0]
    assert summary["failure_count"] == 2 and summary["not_started"] == 0
    assert summary["server_stage_timing_ms"]["queue"] == {"sample_count":1,"p50_ms":2,"p95_ms":2}


@pytest.mark.parametrize("variant",["missing_stage","extra_stage","negative","nan","infinity","boolean","numeric_string","null","array"])
async def test_malformed_https_stage_timings_never_count_as_success_or_zero(variant):
    calls=0
    async def handler(request):
        nonlocal calls
        if request.method == "GET":
            return httpx.Response(200,json={"status":"alive" if request.url.path == "/healthz" else "ready"})
        calls+=1
        value=json.loads(response(request).content)
        stages=value["timing_breakdown_ms"]
        if variant == "missing_stage": stages.pop("rules")
        elif variant == "extra_stage": stages["PRIVATE-TOKEN"]=123
        elif variant == "array": value["timing_breakdown_ms"]=[]
        else: stages["queue"]={"negative":-1,"nan":float("nan"),"infinity":float("inf"),"boolean":True,"numeric_string":"1","null":None}[variant]
        return httpx.Response(200,content=json.dumps(value),headers={"Content-Type":"application/json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result=await profile.profile_client(client,SERVICE,profile.make_plan(samples=2,warmups=0,sizes=(2000,),concurrency=(1,)))
    summary=result["summaries"][0]
    assert calls == 2 and summary["failure_count"] == 2 and summary["complete_count"] == 0
    assert summary["target_assessment"] == "failed_requests_present"
    assert all(stage == {"sample_count":0,"p50_ms":None,"p95_ms":None} for stage in summary["server_stage_timing_ms"].values())
    assert "PRIVATE-TOKEN" not in json.dumps(result)
