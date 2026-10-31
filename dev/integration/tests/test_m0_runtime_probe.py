"""Runtime probe oracles and synthetic ASGI composition; no neural weights."""
import asyncio
import copy
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from integration import verify_m0_runtime as probe
from detector.server import ServerSettings, create_server
from detector.tests.test_runtime import FakeBackend, package


def request():
    return {"schema_version": "1.0", "request_id": "r", "inspection_id": "i", "direction": "outbound",
            "segments": [{"segment_id": "unicode", "text": "공개 e\u0301 👩‍💻"}]}


def response():
    return {"schema_version": "1.0", "request_id": "r", "inspection_id": "i", "status": "complete",
            "findings": [{"segment_id": "unicode", "type": "PERSON", "start": 3, "end": 5,
                          "score": 0.75, "detector": "model"}],
            "model_version": "m1", "threshold_version": "t1", "timing_ms": 5.0,
            "timing_breakdown_ms": {"queue": 1.0, "inference": 3.0, "rules": 0.0, "total": 5.0}}


def test_valid_codepoint_response_yields_counts_without_input():
    result = probe.validate_response(200, response(), request(), "m1", "t1")
    assert result["finding_count"] == 1
    assert "공개" not in str(result) and "findings" not in result


@pytest.mark.parametrize("change", [
    {"status": "incomplete"}, {"request_id": "stale"}, {"inspection_id": "stale"},
    {"model_version": "stale"}, {"threshold_version": "stale"}, {"timing_ms": float("nan")},
    {"timing_ms": -1.0}, {"timing_breakdown_ms": {"inference": float("inf")}},
    {"error_code": "RAW-PRIVATE-ERROR"}, {"raw_text": "RAW-PRIVATE-ERROR"},
])
def test_bad_response_is_rejected_without_leaking_payload(change):
    with pytest.raises(probe.ProbeError) as error:
        probe.validate_response(200, response() | change, request(), "m1", "t1")
    assert "RAW-PRIVATE-ERROR" not in str(error.value)


@pytest.mark.parametrize("change", [{"segment_id": "stale"}, {"end": 999}, {"start": -1},
                                    {"start": 4, "end": 4}, {"score": float("nan")}])
def test_invalid_unicode_finding_is_rejected(change):
    payload = response()
    payload["findings"][0].update(change)
    with pytest.raises(probe.ProbeError):
        probe.validate_response(200, payload, request(), "m1", "t1")


def test_default_api_timeout_response_cannot_pass():
    with pytest.raises(probe.ProbeError):
        probe.validate_response(504, response(), request(), "m1", "t1")


def test_optimized_process_keeps_runtime_validation():
    code = "from integration.verify_m0_runtime import validate_response; validate_response(200, {}, {}, 'm', 't')"
    result = subprocess.run([sys.executable, "-O", "-c", code], cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and "ProbeError" in result.stderr


@pytest.mark.parametrize("failure", [True, False])
def test_run_restores_environment_socket_state(monkeypatch, tmp_path, failure):
    environment = os.environ.copy()
    sockets = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    def execute(*args):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        if failure:
            raise RuntimeError("PRIVATE ERROR")
        return {"status": "test"}
    monkeypatch.setattr(probe, "_execute", execute)
    if failure:
        with pytest.raises(probe.ProbeError) as error:
            probe.run(tmp_path, "cpu")
        assert "PRIVATE" not in str(error.value)
    else:
        assert probe.run(tmp_path, "cpu")["status"] == "test"
    assert os.environ == environment
    assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection) == sockets


def test_real_asgi_composition_with_synthetic_loader(tmp_path):
    root, manifest = package(tmp_path)
    backend = FakeBackend()
    app = create_server(ServerSettings(token="probe-token", package_dir=root.resolve()), loader=lambda *args: backend)
    report = asyncio.run(probe.exercise_app(app, "probe-token", manifest["model_version"], manifest["threshold_version"]))
    assert report["initial_readiness"] == 503 and report["readiness"] == 200
    assert report["unauthorized_status"] == 401
    assert len(report["inspections"]) == 2 and backend.warmed


def test_failed_startup_is_not_reported_as_readiness(tmp_path):
    root, manifest = package(tmp_path)
    def failed(*args):
        raise RuntimeError("PRIVATE ERROR")
    app = create_server(ServerSettings(token="probe-token", package_dir=root.resolve()), loader=failed)
    with pytest.raises(probe.ProbeError) as error:
        asyncio.run(probe.exercise_app(app, "probe-token", manifest["model_version"], manifest["threshold_version"]))
    assert "PRIVATE" not in str(error.value)
