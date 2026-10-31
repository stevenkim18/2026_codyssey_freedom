"""Loopback TCP smoke tests; synthetic detector/provider only, no external API.

Run explicitly: python integration/run_smoke.py from dev. The normal test suite
uses ASGI transports and has no port, subprocess, API-key, or GPU requirement.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
import httpx
import uvicorn

from detector.app import create_app as create_detector
from gateway.app import create_app as create_gateway
from gateway.engine import Gateway
from gateway.journal import Journal
from gateway.transport import InspectionClient, UpstreamClient
from launcher.profiles import install, uninstall, launch_arguments


def _listener():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(32)
    listener.setblocking(False)
    return listener


async def run():
    received, state = [], {"answer": "SAFE_SMOKE", "detector_failed": False}

    async def backend(segments):
        if state["detector_failed"]:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
        findings = []
        for segment in segments:
            for kind, pattern in [("EMAIL_ADDRESS", r"[a-z]+@example\.invalid"), ("KR_RRN", "900101-1234567")]:
                for match in re.finditer(pattern, segment.text):
                    findings.append({"segment_id": segment.segment_id, "type": kind, "start": match.start(),
                        "end": match.end(), "score": 1.0, "detector": "model"})
        return findings

    supplier = FastAPI()
    @supplier.post("/v1/responses")
    async def generate(request: Request):
        assert request.headers["Authorization"] == "Bearer synthetic-provider"
        received.append(await request.json())
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": state["answer"]}]}]}

    sockets = [_listener() for _ in range(3)]
    detector_port, supplier_port, gateway_port = [s.getsockname()[1] for s in sockets]
    tasks, servers = [], []
    checks = []
    try:
        with tempfile.TemporaryDirectory() as directory, Journal(Path(directory) / "state" / "events.sqlite3") as journal:
            async with httpx.AsyncClient(trust_env=False) as detector_http, httpx.AsyncClient(trust_env=False) as supplier_http:
                detector = create_detector(backend=backend, token="synthetic-detector", model_version="synthetic-m0", threshold_version="v1")
                inspector = InspectionClient(detector_http, f"http://127.0.0.1:{detector_port}/v1/inspect", "synthetic-detector", allow_loopback_http=True)
                upstream = UpstreamClient(supplier_http, f"http://127.0.0.1:{supplier_port}/v1/responses", "synthetic-provider", {"mock-model"}, allow_loopback_http=True)
                gateway = create_gateway(Gateway(inspector, upstream, journal, allowed_models={"mock-model"}), token="synthetic-local")
                for app, listener in zip((detector, supplier, gateway), sockets):
                    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off"))
                    servers.append(server)
                    tasks.append(asyncio.create_task(server.serve(sockets=[listener])))
                async with asyncio.timeout(5):
                    while not all(server.started for server in servers):
                        if any(task.done() for task in tasks):
                            raise RuntimeError("smoke server failed to start")
                        await asyncio.sleep(0.01)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{gateway_port}", trust_env=False,
                        headers={"Authorization": "Bearer synthetic-local"}) as cli:
                    cases = [("normal", "안녕", "SAFE_SMOKE", 200),
                        ("outbound_mask", "user@example.invalid", "SAFE_SMOKE", 200),
                        ("outbound_block", "900101-1234567", "SAFE_SMOKE", 403),
                        ("inbound_mask", "안녕", "answer@example.invalid", 200),
                        ("inbound_block", "안녕", "900101-1234567", 403)]
                    for name, prompt, answer, code in cases:
                        state["answer"] = answer
                        before = len(received)
                        result = await cli.post("/v1/responses", json={"model": "mock-model", "input": prompt, "stream": True})
                        assert result.status_code == code, name
                        assert "@example.invalid" not in result.text and "900101-1234567" not in result.text, name
                        assert len(received) - before == (0 if name == "outbound_block" else 1), name
                        assert "@example.invalid" not in json.dumps(received), name
                        checks.append(name)
                    state["detector_failed"] = True
                    before = len(received)
                    result = await cli.post("/v1/responses", json={"model": "mock-model", "input": "hello"})
                    assert result.status_code == 503 and len(received) == before
                    assert "SYNTHETIC_PRIVATE_ERROR" not in result.text
                    checks.append("detector_failure_blocks")
                    state["detector_failed"] = False

                codex = shutil.which("codex")
                cli_result = "unavailable"
                if codex:
                    version_run = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=5)
                    assert version_run.returncode == 0 and "0.166.0" in version_run.stdout
                    home = Path(directory) / "codex"
                    install(home, port=gateway_port, model="mock-model", cli_version="0.166.0")
                    env = {k: v for k, v in os.environ.items() if k in {"PATH", "TMPDIR", "LANG", "LC_ALL"}}
                    env.update(CODEX_HOME=str(home), PII_GATEWAY_LOCAL_TOKEN="synthetic-local",
                        HTTPS_PROXY=f"http://127.0.0.1:{gateway_port}", HTTP_PROXY=f"http://127.0.0.1:{gateway_port}",
                        ALL_PROXY=f"http://127.0.0.1:{gateway_port}", NO_PROXY="127.0.0.1,localhost")
                    command = launch_arguments("0.166.0", ["exec", "--ephemeral", "--skip-git-repo-check",
                        "--sandbox", "read-only", "--json", "Reply to this synthetic prompt without tools."], codex_home=home)
                    command[0] = codex
                    before = len(received)
                    result = await asyncio.to_thread(subprocess.run, command, cwd=directory, env=env,
                        input="", capture_output=True, text=True, timeout=20)
                    assert result.returncode != 0
                    assert "UNSUPPORTED_PROTOCOL" in result.stdout + result.stderr, (result.stdout[-1500:], result.stderr[-1500:])
                    assert len(received) == before
                    uninstall(home)
                    assert not (home / "pii-gateway.config.toml").exists()
                    checks.append("real_cli_profile_routes_to_gateway_and_rejects_unsupported_input")
                    cli_result = "0.166.0 safely rejected; G0 incomplete"
                pending = journal.pending()
                assert "@example.invalid" not in json.dumps(pending)
                return {"environment": sys.executable, "checks": checks, "passed": len(checks),
                    "upstream_requests": len(received), "events": len(pending), "cli": cli_result,
                    "external_api_calls": 0, "gpu_tested": False}
    finally:
        for server in servers:
            server.should_exit = True
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        for listener in sockets:
            listener.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))
