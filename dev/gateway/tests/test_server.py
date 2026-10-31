"""Executable composition: real settings/journal and isolated HTTP transports."""
from pathlib import Path
import os
import subprocess
import sys

import httpx
import pytest

from gateway.server import create_server
from gateway.settings import Settings
from gateway import server


async def test_server_composes_verified_tls_clients_and_durable_journal(tmp_path):
    calls = []
    def transport(request):
        import json
        body = json.loads(request.content)
        calls.append((str(request.url), request.headers["Authorization"], body))
        if request.url.host == "detector.example.invalid":
            return httpx.Response(200, json={"schema_version": "1.0", "request_id": body["request_id"],
                "inspection_id": body["inspection_id"], "status": "complete", "findings": [],
                "model_version": "synthetic-m0", "threshold_version": "v1", "timing_ms": 1.0,
                "timing_breakdown_ms": {"queue": 0.0, "inference": 1.0, "rules": 0.0, "total": 1.0}})
        assert str(request.url) == "https://api.openai.com/v1/responses"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "SAFE"}]}]})
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["verify"] is True
        return httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs)
    config = Settings.from_env({"PII_GATEWAY_LOCAL_TOKEN": "synthetic-local", "PII_DETECTOR_TOKEN": "synthetic-detector",
        "OPENAI_API_KEY": "synthetic-upstream", "PII_DETECTOR_URL": "https://detector.example.invalid/v1/inspect",
        "PII_MODEL_ID": "mock-model", "PII_STATE_DIR": str(tmp_path / "state")})
    app = create_server(config, client_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
            result = await client.post("/v1/responses", headers={"Authorization": "Bearer synthetic-local"},
                json={"model": "mock-model", "input": "hello"})
    assert result.status_code == 200 and "SAFE" in result.text
    assert [auth for _, auth, _ in calls] == ["Bearer synthetic-detector", "Bearer synthetic-upstream", "Bearer synthetic-detector"]
    assert (tmp_path / "state" / "events.sqlite3").exists()


def test_real_module_invocation_without_configuration_is_safe():
    result = subprocess.run([sys.executable, "-m", "gateway"],
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])}, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "Gateway startup rejected: invalid or unavailable secure configuration."


def test_main_cannot_bind_publicly_or_enable_access_logs(tmp_path, monkeypatch):
    config = Settings.from_env({"PII_GATEWAY_LOCAL_TOKEN": "synthetic-local", "PII_DETECTOR_TOKEN": "synthetic-detector",
        "OPENAI_API_KEY": "synthetic-upstream", "PII_DETECTOR_URL": "https://detector.example.invalid/v1/inspect",
        "PII_MODEL_ID": "mock-model", "PII_STATE_DIR": str(tmp_path / "state")})
    monkeypatch.setattr(server.Settings, "from_env", lambda env: config)
    run = []
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kwargs: run.append(kwargs))
    assert server.main() == 0
    assert run == [{"host": "127.0.0.1", "port": 8765, "access_log": False, "log_level": "critical"}]


def test_main_reports_missing_configuration_without_logging_values(monkeypatch, capsys):
    monkeypatch.setattr(server.os, "environ", {})
    assert server.main() == 2
    assert capsys.readouterr().err.strip() == "Gateway startup rejected: invalid or unavailable secure configuration."


@pytest.mark.parametrize("failure,expected", [(SystemExit(0), 0), (SystemExit(None), 0),
    (SystemExit(1), 2), (OSError("SYNTHETIC_PRIVATE_BIND_DETAIL"), 2)])
def test_runner_exit_and_bind_errors_have_safe_process_contract(tmp_path, monkeypatch, capsys, failure, expected):
    from gateway.tests.test_settings import environment
    config = Settings.from_env(environment() | {"PII_STATE_DIR": str(tmp_path / "state")})
    monkeypatch.setattr(server.Settings, "from_env", lambda env: config)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(server.uvicorn, "run", fail)
    assert server.main() == expected
    output = capsys.readouterr()
    assert output.out == ""
    assert "SYNTHETIC_PRIVATE_BIND_DETAIL" not in output.err
    assert output.err.strip() == ("Gateway startup rejected: invalid or unavailable secure configuration." if expected else "")
