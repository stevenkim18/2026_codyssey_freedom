"""Offline real-weight probe through detector.server lifespan and ASGI HTTP.

From dev: python -m integration.verify_m0_runtime PACKAGE --device cpu --output NEW.json
Loads the real model once and runs its normal warmup plus two tiny inspections.
CPU output is not GPU evidence; neither mode establishes quality or latency SLOs.
No listener is opened. TLS, deployed hosts, and external CLI integration are untested.
Use a fresh process; process-wide offline guards are not safe for concurrent work.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import resource
import secrets
import socket
import sys
import tempfile
import time


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


class ProbeError(Exception):
    """Only fixed, content-free verification error codes escape this probe."""


def _require(condition, code):
    if not condition:
        raise ProbeError(code)


def validate_response(http_status, payload, request, model_version, threshold_version):
    """Validate actual response contract and original Unicode codepoint bounds."""
    from detector.schemas import InspectionRequest, InspectionResponse
    try:
        _require(http_status == 200, "inspection_http_failed")
        incoming = InspectionRequest.model_validate(request)
        checked = InspectionResponse.model_validate(payload)
        _require(checked.status == "complete" and checked.error_code is None, "inspection_not_complete")
        _require(checked.request_id == incoming.request_id and checked.inspection_id == incoming.inspection_id,
                 "inspection_identity_mismatch")
        _require(checked.model_version == model_version and checked.threshold_version == threshold_version
                 and model_version != "unloaded" and threshold_version != "unloaded", "inspection_version_mismatch")
        _require(set(checked.timing_breakdown_ms) == {"queue", "inference", "rules", "total"}, "invalid_timing_breakdown")
        lengths = {segment.segment_id: len(segment.text) for segment in incoming.segments}
        for finding in checked.findings:
            _require(finding.segment_id in lengths and finding.end <= lengths[finding.segment_id], "invalid_codepoint_span")
        return {"http_status": http_status, "status": checked.status, "finding_count": len(checked.findings),
                "model_finding_count": sum(finding.detector == "model" for finding in checked.findings),
                "rule_finding_count": sum(finding.detector == "rule" for finding in checked.findings),
                "model_version": checked.model_version, "threshold_version": checked.threshold_version,
                "timing_ms": checked.timing_ms, "timing_breakdown_ms": checked.timing_breakdown_ms}
    except ProbeError:
        raise
    except Exception:
        raise ProbeError("invalid_inspection_response") from None


async def exercise_app(app, token, model_version, threshold_version):
    """Normal lifecycle and HTTP path; tests may supply an explicitly synthetic app."""
    import httpx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline-probe") as client:
        health = await client.get("/healthz")
        _require(health.status_code == 200 and health.json() == {"status": "alive"}, "health_failed")
        initial = await client.get("/readyz")
        _require(initial.status_code == 503 and initial.json() == {"status": "not_ready"}, "unexpected_initial_readiness")
        unauthorized = await client.post("/v1/inspect", headers={"Authorization": "Bearer deliberately-wrong-token"},
                                         json={"private_sentinel": "must-not-be-returned"})
        _require(unauthorized.status_code == 401 and unauthorized.json() == {"status": "error", "error_code": "unauthorized"},
                 "authorization_boundary_failed")
        started = time.monotonic()
        async with app.router.lifespan_context(app):
            startup_seconds = time.monotonic() - started
            ready = await client.get("/readyz")
            _require(ready.status_code == 200 and ready.json() == {"status": "ready"}, "runtime_not_ready")
            results = []
            for index, text in enumerate(("public demonstration", "공개 예시 e\u0301 👩‍💻")):
                request = {"schema_version": "1.0", "request_id": f"runtime-probe-{index}",
                           "inspection_id": f"runtime-inspection-{index}", "direction": "outbound",
                           "segments": [{"segment_id": f"segment-{index}", "text": text}]}
                response = await client.post("/v1/inspect", headers={"Authorization": f"Bearer {token}"}, json=request)
                # The server retains its normal 10-second inspection deadline.
                results.append(validate_response(response.status_code, response.json(), request, model_version, threshold_version))
        return {"initial_readiness": initial.status_code, "readiness": ready.status_code,
                "unauthorized_status": unauthorized.status_code, "startup_seconds": round(startup_seconds, 4),
                "inspections": results}


def run(package, device="cpu"):
    """Restore environment/socket state even when initialization fails."""
    environment = os.environ.copy()
    sockets = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    attempts = []
    def deny(*args, **kwargs):
        attempts.append(True)
        raise ProbeError("network_denied")
    try:
        _require(type(device) is str and device in {"cpu", "cuda"}, "invalid_device")
        cache = tempfile.mkdtemp(prefix="m0-runtime-offline-")
        os.environ.update(HF_HOME=cache, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                          MPLCONFIGDIR=str(Path(cache) / "mpl"), XDG_CACHE_HOME=str(Path(cache) / "xdg"))
        socket.socket.connect = deny
        socket.socket.connect_ex = deny
        socket.create_connection = deny
        report = _execute(Path(package).resolve(), device)
        _require(not attempts, "network_attempt_detected")
        return report | {"network_attempts": len(attempts)}
    except ProbeError:
        raise
    except Exception:
        raise ProbeError("runtime_probe_failed") from None
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = sockets
        os.environ.clear()
        os.environ.update(environment)


def _execute(package, device):
    started = time.monotonic()
    import torch
    from detector.server import ServerSettings, create_server
    previous_threads = torch.get_num_threads()
    try:
        if device == "cpu":
            torch.set_num_threads(2)
        else:
            _require(torch.cuda.is_available(), "cuda_unavailable")
        manifest = json.loads((package / "manifest.json").read_text())
        token = secrets.token_urlsafe(32)
        settings = ServerSettings.from_env({"PII_DETECTOR_TOKEN": token, "PII_MODEL_PACKAGE": str(package),
                                            "PII_DETECTOR_DEVICE": device})
        app = create_server(settings)  # Actual default load_gliner and ModelManager.
        result = asyncio.run(exercise_app(app, token, manifest["model_version"], manifest["threshold_version"]))
        # Read-only confirmation of actual placement, beyond requested settings.
        backend = getattr(app.state.model_manager._backend, "__self__", None)
        parameters = iter(backend.model.parameters())
        first = next(parameters)
        _require(first.device.type == device and all(parameter.device.type == device for parameter in parameters),
                 "model_device_mismatch")
        return result | {"scope": "real model warmup and two tiny ASGI inspections; no quality/SLO/TLS/remote-CLI claim",
                         "requested_device": device, "verified_model_device": first.device.type,
                         "gpu_verified": device == "cuda", "python": sys.executable, "torch_version": torch.__version__,
                         "seconds": round(time.monotonic() - started, 4),
                         "max_rss_native": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    finally:
        torch.set_num_threads(previous_threads)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="new JSON evidence file; existing files are preserved")
    arguments = parser.parse_args(argv)
    try:
        # Reserve the evidence path before spending resources on model loading.
        with arguments.output.open("x") as evidence:
            try:
                with redirect_stdout(sys.stderr):
                    report = run(arguments.package, arguments.device)
            except ProbeError as error:
                evidence.write(json.dumps({"status": "failed", "error_code": str(error)}) + "\n")
                raise
            evidence.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print("runtime probe passed")
        return 0
    except ProbeError as error:
        print(f"runtime probe failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("runtime probe failed: evidence_output_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
