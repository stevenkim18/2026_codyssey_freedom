"""Synthetic model swap across real gateway HTTP / detector HTTPS and B DTOs.

Provider transport is isolated; no real model quality or C UI is claimed.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import socket

import httpx
import pytest
import uvicorn

from deployment.tests.test_gpu_tls import certificates
from detector.runtime import ModelRuntimeError
from detector.schemas import BackendResult
from detector.server import create_server as detector_server, settings_from_env
from detector.tests.test_runtime import package
from gateway.journal import Journal
from gateway.server import create_server as gateway_server
from gateway.settings import Settings
from integration.verify_dashboard_example import load_fixture, _typed


@asynccontextmanager
async def running(app, **tls):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(10)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        access_log=False, log_config=None, log_level="critical", ws="none", **tls))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                assert not task.done()
                await asyncio.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        listener.close()


@pytest.mark.parametrize("failure", ["load", "warmup"])
async def test_gateway_swap_policy_delivery_and_dashboard_dto(tmp_path, certificates, failure):
    roots = {version: package(tmp_path, version)[0]
             for version in ("synthetic-m0", "synthetic-m1", "synthetic-bad")}
    loading, loaded = asyncio.Event(), asyncio.Event()
    pause_loading = False

    class Backend:
        def __init__(self, version):
            self.version = version

        async def warmup(self):
            if self.version == "synthetic-bad":
                raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION")
            if pause_loading:
                loading.set()
                await loaded.wait()

        async def __call__(self, segments):
            findings = []
            if self.version == "synthetic-m1":
                for segment in segments:
                    start = segment.text.find("HIDDEN")
                    if start >= 0:
                        findings.append({"segment_id": segment.segment_id, "type": "PERSON",
                            "start": start, "end": start + 6, "score": 0.95, "detector": "model"})
            return BackendResult(findings=findings)

    def loader(path, manifest):
        version = manifest["model_version"]
        if version == "synthetic-bad" and failure == "load":
            raise RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION")
        return Backend(version)

    detector = detector_server(settings_from_env({"PII_DETECTOR_TOKEN": "synthetic-detector-token",
        "PII_MODEL_PACKAGE": str(roots["synthetic-m0"]), "PII_DETECTOR_HOST": "127.0.0.1"}), loader=loader)
    manager = detector.state.model_manager
    provider_calls = []
    swap_at_provider = False

    async def provider(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        provider_calls.append(json.loads(request.content))
        if swap_at_provider:
            await manager.replace(roots["synthetic-m1"])
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
            "role": "assistant", "content": [{"type": "output_text", "text": "PUBLIC_OUTPUT HIDDEN"}]}]})

    cert, key = certificates[0]
    async with running(detector, ssl_certfile=str(cert), ssl_keyfile=str(key)) as detector_port:
        config = Settings.from_env({"PII_GATEWAY_LOCAL_TOKEN": "synthetic-local-token",
            "PII_DETECTOR_TOKEN": "synthetic-detector-token", "OPENAI_API_KEY": "synthetic-provider-token",
            "PII_MODEL_ID": "synthetic-provider", "PII_STATE_DIR": str(tmp_path / "state"),
            "PII_DETECTOR_URL": f"https://127.0.0.1:{detector_port}/v1/inspect",
            "PII_DETECTOR_CA_FILE": str(cert)})
        clients = []

        def factory(**kwargs):
            clients.append(kwargs)
            if len(clients) == 1:
                return httpx.AsyncClient(**kwargs)
            assert kwargs == {"verify": True, "trust_env": False}
            return httpx.AsyncClient(transport=httpx.MockTransport(provider), **kwargs)

        gateway = gateway_server(config, client_factory=factory)
        awaiting_ack, acknowledge = asyncio.Event(), asyncio.Event()
        gate = False

        async def gated_app(scope, receive, send):
            async def gated_send(message):
                if gate and message["type"] == "http.response.body":
                    awaiting_ack.set()
                    await acknowledge.wait()
                await send(message)
            await gateway(scope, receive, gated_send)

        def rows():
            with Journal(tmp_path / "state" / "events.sqlite3") as journal:
                return journal.pending()

        async with running(gated_app) as gateway_port:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{gateway_port}", trust_env=False,
                    headers={"Authorization": "Bearer synthetic-local-token"}, timeout=5) as client:
                async def request():
                    return await client.post("/v1/responses", json={"model": "synthetic-provider",
                        "input": "PUBLIC_INPUT HIDDEN"})

                response = await request()
                assert response.status_code == 200 and "PUBLIC_OUTPUT HIDDEN" in response.text
                await manager.replace(roots["synthetic-m1"])
                gate = True
                pending = asyncio.create_task(request())
                try:
                    await asyncio.wait_for(awaiting_ack.wait(), 3)
                    current = rows()
                    latest_id = current[-1]["request_id"]
                    latest = [row for row in current if row["request_id"] == latest_id]
                    assert latest[-1]["delivery_status"] == "unknown"
                    assert not any(row["delivery_status"] == "delivered" for row in latest)
                    acknowledge.set()
                    response = await pending
                finally:
                    acknowledge.set()
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                    gate = False
                assert response.status_code == 200 and "HIDDEN" not in response.text
                assert "[REDACTED]" in response.text
                assert "HIDDEN" not in json.dumps(provider_calls[-1])
                async with asyncio.timeout(3):
                    while not any(row["request_id"] == latest_id and row["delivery_status"] == "delivered" for row in rows()):
                        await asyncio.sleep(0.01)
                latest = [row for row in rows() if row["request_id"] == latest_id]
                assert {row["model_version"] for row in latest} == {"synthetic-m1"}
                assert {(row["direction"], row["decision"]) for row in latest} == {("outbound", "mask"), ("inbound", "mask")}
                assert {row["delivery_status"] for row in latest} == {"not_sent", "unknown", "forwarded_sanitized", "delivered"}

                with pytest.raises(ModelRuntimeError) as error:
                    await manager.replace(roots["synthetic-bad"])
                assert "SYNTHETIC_PRIVATE_EXCEPTION" not in str(error.value)
                assert manager.ready and manager.model_version == "synthetic-m1"
                response = await request()
                assert response.status_code == 200 and "HIDDEN" not in response.text

                await manager.replace(roots["synthetic-m0"])
                swap_at_provider = True
                response = await request()
                swap_at_provider = False
                assert response.status_code == 503 and response.json()["error"]["code"] == "MODEL_CHANGED"
                assert "PUBLIC_OUTPUT" not in response.text and "HIDDEN" not in response.text
                changed = rows()[-1]
                assert changed["direction"] == "inbound" and changed["decision"] == "error"
                # Failed inspection metadata is deliberately recorded as unknown.
                assert changed["error_code"] == "MODEL_CHANGED" and changed["model_version"] == "unknown"

                pause_loading = True
                replacing = asyncio.create_task(manager.replace(roots["synthetic-m0"]))
                try:
                    await asyncio.wait_for(loading.wait(), 3)
                    count = len(provider_calls)
                    response = await request()
                    assert response.status_code == 503
                    assert response.json()["error"]["code"] == "INSPECTION_FAILED"
                    assert len(provider_calls) == count
                finally:
                    loaded.set()
                    await asyncio.wait_for(replacing, 3)

        events = rows()
        assert all(len(row) == 18 and type(row["revision"]) is int
                   and type(row["timing_ms"]) in (int, float) for row in events)
        assert all(type(item["count"]) is int for row in events for item in row["findings"])
        serialized = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in events)
        assert all(value not in serialized for value in ("PUBLIC_INPUT", "PUBLIC_OUTPUT", "HIDDEN",
            "synthetic-local-token", "synthetic-detector-token", "synthetic-provider-token", "SYNTHETIC_PRIVATE_EXCEPTION"))
        fixture = tmp_path / "actual-gateway-events.ndjson"
        fixture.write_text(serialized)
        assert _typed(load_fixture(fixture)) == _typed(events)
