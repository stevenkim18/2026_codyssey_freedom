"""R08/R10: fresh production module startup, local auth and durable restart.

Only loopback health and requests rejected before inspection are sent. These
checks do not establish detector/model readiness or remote deployment health.
"""
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time

import pytest

from gateway.journal import Journal


DEV = Path(__file__).resolve().parents[2]
REJECTION = "Gateway startup rejected: invalid or unavailable secure configuration."
TOKENS = ("SYNTHETIC_LOCAL_STARTUP_TOKEN", "SYNTHETIC_DETECTOR_STARTUP_TOKEN",
          "SYNTHETIC_UPSTREAM_STARTUP_TOKEN")


def _env(state, port):
    # Deliberately do not inherit developer credentials, proxy or Python hooks.
    return {"PYTHONPATH": str(DEV), "PII_STATE_DIR": str(state),
            "PII_GATEWAY_PORT": str(port), "PII_MODEL_ID": "synthetic-model",
            "PII_GATEWAY_LOCAL_TOKEN": TOKENS[0], "PII_DETECTOR_TOKEN": TOKENS[1],
            "OPENAI_API_KEY": TOKENS[2],
            "PII_DETECTOR_URL": "https://127.0.0.1:1/v1/inspect"}


@contextmanager
def _reserved_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock.getsockname()[1]


def _free_port():
    with _reserved_port() as port:
        return port


def _request(port, path, *, token=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request("GET" if body is None else "POST", path, body, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _safe_output(stdout, stderr, state):
    output = stdout + stderr
    assert stdout == ""
    assert "Traceback" not in output
    assert str(state) not in output
    assert all(token not in output for token in TOKENS)


@contextmanager
def _running(state):
    port = _free_port()
    process = subprocess.Popen([sys.executable, "-m", "gateway"], cwd=DEV,
        env=_env(state, port), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert process.poll() is None, "gateway exited before readiness"
            try:
                if _request(port, "/readyz") == (200, {"status": "ready"}):
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.025)
        else:
            pytest.fail("gateway readiness timeout")
        yield port
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        _safe_output(stdout, stderr, state)
        assert stderr == ""
        # Uvicorn restores/re-raises SIGTERM after graceful lifespan teardown.
        assert process.returncode in (0, -signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def _event():
    return {"schema_version": "1.0", "event_id": "startup-event", "client_id": "client-1",
        "session_id": "session-1", "request_id": "request-1", "inspection_id": "inspection-1",
        "revision": 1, "occurred_at": "2026-10-31T00:00:00Z", "direction": "outbound",
        "stage": "decision", "decision": "allow", "delivery_status": "not_sent",
        "findings": [], "rule_ids": [], "model_version": "synthetic-m0",
        "policy_version": "v1", "timing_ms": 0.0, "error_code": None}


def test_module_health_auth_sigterm_and_existing_journal_restart(tmp_path):
    state = tmp_path / "private-state"
    with _running(state) as port:
        assert _request(port, "/healthz") == (200, {"status": "alive"})
        for token in (None, "wrong-token"):
            status, body = _request(port, "/v1/responses", token=token, body="{")
            assert status == 401 and body["error"]["code"] == "UNAUTHORIZED"
        status, body = _request(port, "/v1/responses", token=TOKENS[0], body="{")
        assert status == 400 and body["error"]["code"] == "INVALID_JSON"
    path = state / "events.sqlite3"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with Journal(path) as journal:
        assert journal.pending() == []
        journal.append(_event())
    with _running(state):
        pass
    with Journal(path) as journal:
        assert journal.pending() == [_event()]


def _rejected(state, port):
    result = subprocess.run([sys.executable, "-m", "gateway"], cwd=DEV,
        env=_env(state, port), capture_output=True, text=True, timeout=10)
    _safe_output(result.stdout, result.stderr, state)
    assert result.returncode != 0
    assert result.stderr.strip() == REJECTION


def test_module_port_collision_has_safe_actionable_failure(tmp_path):
    with _reserved_port() as port:
        _rejected(tmp_path / "private-state", port)


@pytest.mark.parametrize("unsafe", ["corrupt", "symlink", "state-file"])
def test_module_rejects_unsafe_journal_without_leaking_paths(tmp_path, unsafe):
    state = tmp_path / "SYNTHETIC_PRIVATE_PATH"
    if unsafe == "state-file":
        state.write_text("synthetic")
    elif unsafe == "symlink":
        target = tmp_path / "target"
        target.mkdir(mode=0o700)
        state.symlink_to(target, target_is_directory=True)
    else:
        state.mkdir(mode=0o700)
        (state / "events.sqlite3").write_bytes(b"synthetic corrupt journal")
        (state / "events.sqlite3").chmod(0o600)
    _rejected(state, _free_port())
