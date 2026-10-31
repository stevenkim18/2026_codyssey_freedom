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
    records = [{"status": "complete", "elapsed_ms": 400.0}] * 20
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
