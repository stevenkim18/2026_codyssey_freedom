"""Plan-first, bounded HTTPS detector profiling. --run explicitly enables traffic.

Synthetic inputs, client wall latency, no retries or altered service deadlines.
First request is NOT cold-start latency; GPU placement/quality need other evidence.
"""
import asyncio
import json
import math
from pathlib import Path
import secrets
import sys

import httpx

from gateway.transport import InspectionClient, TransportError
from integration.verify_deployed_services import SafeParser, load_config, tls_context, _json_request, _service
from integration.verify_m0_runtime import validate_response


class ProfileError(Exception):
    pass


class _RunBudgetExceeded(Exception):
    pass


def require(condition, code="invalid_profile_plan"):
    if not condition:
        raise ProfileError(code)


def make_plan(*, samples=5, warmups=2, sizes=(2000, 16000), concurrency=(1, 2, 4), budget_s=120):
    require(type(samples) is int and 1 <= samples <= 100)
    require(type(warmups) is int and 0 <= warmups <= 10)
    require(type(budget_s) in (int, float) and math.isfinite(budget_s) and 0 < budget_s <= 120)
    require(type(sizes) in (tuple, list) and sizes and all(type(size) is int and size in (2000, 16000) for size in sizes))
    require(type(concurrency) in (tuple, list) and concurrency and all(type(count) is int and count in (1, 2, 4) for count in concurrency))
    require(len(set(sizes)) == len(sizes) and len(set(concurrency)) == len(concurrency))
    measurement_requests = samples * len(sizes) * len(concurrency)
    warmup_requests = warmups * len(sizes)
    return {"network_enabled": False, "samples_per_scenario": samples, "warmups_per_size": warmups,
            "sizes": list(sizes), "concurrency": list(concurrency), "budget_s": budget_s,
            "measurement_requests": measurement_requests, "warmup_requests": warmup_requests,
            "max_request_count": 2 + measurement_requests + warmup_requests,
            "max_pending_requests": max(concurrency), "inspection_deadline_s": 10,
            "request_deadline_sum_s": 20 + 10 * (warmup_requests + len(sizes) * sum(math.ceil(samples / count) for count in concurrency)),
            "wall_budget_note": "whole run capped by budget_s; cancellation cleanup is drained, no retry",
            "scope": "synthetic client-latency plan; not GPU, cold-start or quality evidence"}


def synthetic_text(length):
    require(type(length) is int and length in (2000, 16000))
    pattern = "공개 예시 public demonstration e\u0301 👩‍💻 12345. "
    repetitions, remainder = divmod(length, len(pattern))
    return pattern * repetitions + pattern[:remainder]


def percentile(values, quantile):
    require(type(quantile) in (int, float) and math.isfinite(quantile) and 0 < quantile <= 1, "invalid_statistics")
    require(isinstance(values, list) and values and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in values), "invalid_statistics")
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def summarize(records, *, size, concurrency, warmed, planned=None):
    complete = [record["elapsed_ms"] for record in records if record["status"] == "complete"]
    failures = len(records) - len(complete)
    not_started = max(0, (planned if planned is not None else len(records)) - len(records))
    p50, p95 = (percentile(complete, 0.5), percentile(complete, 0.95)) if complete else (None, None)
    if size != 2000 or concurrency != 1:
        assessment = "descriptive_only"
    elif failures or not_started:
        assessment = "failed_requests_present"
    elif not warmed:
        assessment = "not_warmed"
    elif len(complete) < 20:
        assessment = "insufficient_samples"
    else:
        assessment = "met_in_observed_sample" if p95 <= 500 else "not_met_in_observed_sample"
    return {"codepoints": size, "concurrency": concurrency, "complete_count": len(complete), "failure_count": failures,
            "not_started": not_started, "p50_ms": p50, "p95_ms": p95, "quantile_method": "nearest-rank: sorted[ceil(p*n)-1]",
            "target_assessment": assessment, "stable_slo_established": False}


async def profile_client(client, service, plan):
    """Injected-client seam for tests; this function alone makes NO TLS claim."""
    try:
        _service(service, "gpu")
        canonical = make_plan(samples=plan["samples_per_scenario"], warmups=plan["warmups_per_size"],
                              sizes=plan["sizes"], concurrency=plan["concurrency"], budget_s=plan["budget_s"])
        require(plan == canonical)
    except Exception:
        raise ProfileError("invalid_profile_configuration") from None
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + plan["budget_s"]
    result = {"status": "running", "readiness_verified": False, "budget_exhausted": False,
              "warmups": [], "measurements": [], "first_request": None,
              "model_version": service["model_version"], "threshold_version": service["threshold_version"]}
    statuses = {}
    async def record_status(response):
        if response.request.method == "POST" and response.request.url.path == "/v1/inspect":
            identity = json.loads(response.request.content)["request_id"]
            statuses.setdefault(identity, []).append(response.status_code)
    client.event_hooks["response"].append(record_status)
    inspect = InspectionClient(client, service["origin"] + "/v1/inspect", service["token"])

    def check_budget():
        if loop.time() >= deadline:
            raise _RunBudgetExceeded

    async def one(size, concurrency, phase, index):
        check_budget()
        before = loop.time()
        record = {"codepoints": size, "concurrency": concurrency, "phase": phase, "sample": index, "status": "running"}
        result["warmups" if phase == "warmup" else "measurements"].append(record)
        if result["first_request"] is None:
            result["first_request"] = record
        request = {"schema_version": "1.0", "request_id": secrets.token_hex(12), "inspection_id": secrets.token_hex(12),
                   "direction": "outbound", "segments": [{"segment_id": "synthetic", "text": synthetic_text(size)}]}
        try:
            raw = await inspect(request["segments"], request["direction"], request["request_id"], request["inspection_id"])
            check_budget()
            require(statuses.get(request["request_id"]) == [200], "unexpected_http_status")
            require(raw["model_version"] == service["model_version"] and raw["threshold_version"] == service["threshold_version"], "model_version_drift")
            if raw["status"] != "complete":
                record["status"] = "incomplete" if raw["status"] == "incomplete" else "error"
                record["error_code"] = "non_complete_response"
            else:
                checked = validate_response(200, raw, request, service["model_version"], service["threshold_version"])
                check_budget()
                record.update(status="complete", finding_count=checked["finding_count"], server_timing_ms=checked["timing_ms"])
        except _RunBudgetExceeded:
            record.update(status="cancelled", error_code="whole_run_budget_exhausted")
            raise
        except asyncio.CancelledError:
            record.update(status="cancelled", error_code="run_cancelled")
            raise
        except TransportError as error:
            record.update(status="timeout" if error.code == "inspection_timeout" else "error", error_code="inspection_transport_failed")
        except Exception:
            record.update(status="error", error_code="inspection_validation_failed")
        finally:
            record["elapsed_ms"] = (loop.time() - before) * 1000
            statuses.pop(request["request_id"], None)

    async def scenario(size, concurrency):
        indexes = iter(range(plan["samples_per_scenario"]))
        async def worker():
            for index in indexes:
                await one(size, concurrency, "measurement", index)
        tasks = [asyncio.create_task(worker()) for _ in range(min(concurrency, plan["samples_per_scenario"]))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    try:
        async with asyncio.timeout_at(deadline):
            for path, expected in (("/healthz", "alive"), ("/readyz", "ready")):
                body = await _json_request(client, "GET", service["origin"] + path, 200)
                require(body == {"status": expected}, "readiness_failed")
            result["readiness_verified"] = True
            for size in plan["sizes"]:
                for index in range(plan["warmups_per_size"]):
                    await one(size, 1, "warmup", index)
                for concurrency in plan["concurrency"]:
                    await scenario(size, concurrency)
    except (TimeoutError, _RunBudgetExceeded):
        result["budget_exhausted"] = True
    except asyncio.CancelledError:
        raise
    except Exception:
        result["error_code"] = "profile_readiness_or_execution_failed"
    finally:
        client.event_hooks["response"].remove(record_status)
    result["elapsed_s"] = loop.time() - started
    result["not_started_measurements"] = plan["measurement_requests"] - len(result["measurements"])
    result["not_started_warmups"] = plan["warmup_requests"] - len(result["warmups"])
    result["summaries"] = []
    for size in plan["sizes"]:
        warm = [record for record in result["warmups"] if record["codepoints"] == size]
        warmed = bool(warm) and len(warm) == plan["warmups_per_size"] and all(record["status"] == "complete" for record in warm)
        for concurrency in plan["concurrency"]:
            records = [record for record in result["measurements"] if record["codepoints"] == size and record["concurrency"] == concurrency]
            result["summaries"].append(summarize(records, size=size, concurrency=concurrency, warmed=warmed, planned=plan["samples_per_scenario"]))
    records = result["warmups"] + result["measurements"]
    result["status"] = "completed" if (result["readiness_verified"] and not result["budget_exhausted"]
        and not result["not_started_measurements"] and not result["not_started_warmups"] and all(record["status"] == "complete" for record in records)) else "partial_or_failed"
    result["scope"] = "synthetic client wall latency; first request is not cold start; no GPU-placement, quality or stable-SLO claim"
    return result


async def run(config, plan):
    require("gpu" in config, "gpu_configuration_required")
    async with httpx.AsyncClient(verify=tls_context(config["gpu"]), trust_env=False, follow_redirects=False, timeout=10) as client:
        report = await profile_client(client, config["gpu"], plan)
    return report | {"network_enabled": True, "test_only": config.get("test_only", False),
                     "tls_verified": report["readiness_verified"], "plan": plan}


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="explicitly send the planned synthetic requests")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--size", type=int, choices=(2000, 16000), action="append")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 4), action="append")
    parser.add_argument("--budget-seconds", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        require("gpu" in config, "gpu_configuration_required")
        plan = make_plan(samples=args.samples, warmups=args.warmups, sizes=args.size or (2000, 16000),
                         concurrency=args.concurrency or (1, 2, 4), budget_s=args.budget_seconds)
        with args.output.open("x") as output:
            try:
                report = asyncio.run(run(config, plan)) if args.run else plan
            except Exception:
                output.write(json.dumps({"status": "failed", "error_code": "profile_execution_failed"}) + "\n")
                raise ProfileError("profile_execution_failed") from None
            output.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        if not args.run:
            print(json.dumps(plan))
            return 0
        print("profiling evidence recorded")
        return 0 if report["status"] == "completed" else 1
    except Exception:
        print("profiling failed: configuration_or_execution_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
