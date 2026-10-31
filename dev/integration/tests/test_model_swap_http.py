"""R07/R11/T07: synthetic package replacement observed through real local HTTP.

Fixtures are deterministic fake backends, not measured M0/M1 model quality.
Replacement is the local operator method; no administrative HTTP route exists.
"""
import asyncio
import socket

import httpx
import pytest
import uvicorn

from detector.runtime import ModelRuntimeError
from detector.schemas import BackendResult
from detector.server import create_server, settings_from_env
from detector.tests.test_runtime import package


@pytest.mark.parametrize("failure", ["load", "warmup"])
async def test_synthetic_swap_drain_and_failed_candidate_rollback_over_http(tmp_path, failure):
    initial, _ = package(tmp_path, "synthetic-m0")
    candidate, _ = package(tmp_path, "synthetic-m1")
    invalid, _ = package(tmp_path, "synthetic-bad")
    entered, release = asyncio.Event(), asyncio.Event()
    warming, warmed = asyncio.Event(), asyncio.Event()
    secret = "SYNTHETIC_BACKEND_FAILURE_SECRET"

    class Backend:
        def __init__(self, version):
            self.version = version

        async def warmup(self):
            if self.version == "synthetic-bad":
                raise RuntimeError(secret)
            if self.version == "synthetic-m1":
                warming.set()
                await warmed.wait()

        async def __call__(self, segments):
            if self.version == "synthetic-m0":
                entered.set()
                await release.wait()
            start, end = (1, 3) if self.version == "synthetic-m0" else (4, 6)
            return BackendResult(findings=[{"segment_id": "s1", "type": "PERSON",
                "start": start, "end": end, "score": 0.95, "detector": "model"}])

    def loader(path, manifest):
        if manifest["model_version"] == "synthetic-bad" and failure == "load":
            raise RuntimeError(secret)
        return Backend(manifest["model_version"])

    settings = settings_from_env({"PII_DETECTOR_TOKEN": "synthetic-token",
        "PII_MODEL_PACKAGE": str(initial), "PII_DETECTOR_HOST": "127.0.0.1"})
    app = create_server(settings, loader=loader)
    manager = app.state.model_manager
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(10)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        access_log=False, log_config=None, log_level="critical", ws="none"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    pending = swapping = None
    body = {"schema_version": "1.0", "request_id": "r1", "inspection_id": "i1",
        "direction": "outbound", "segments": [{"segment_id": "s1", "text": "😀가나 다라"}]}

    def check(response, version, start, end):
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "complete"
        assert result["model_version"] == version
        assert result["threshold_version"] == version + "-thresholds"
        assert [(row["start"], row["end"]) for row in result["findings"]] == [(start, end)]
        assert body["segments"][0]["text"] not in response.text
        assert secret not in response.text

    try:
        async with asyncio.timeout(10):
            while not server.started:
                assert not serving.done()
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5) as client:
            headers = {"Authorization": "Bearer synthetic-token"}
            assert (await client.post("/v1/inspect", json=body)).status_code == 401
            assert (await client.get("/readyz")).status_code == 200
            pending = asyncio.create_task(client.post("/v1/inspect", headers=headers, json=body))
            await asyncio.wait_for(entered.wait(), 3)
            swapping = asyncio.create_task(manager.replace(candidate))
            async with asyncio.timeout(3):
                while manager.ready:
                    await asyncio.sleep(0)
            assert (await client.get("/readyz")).status_code == 503
            denied = await client.post("/v1/inspect", headers=headers, json=body)
            assert denied.status_code == 503 and denied.json()["error_code"] == "not_ready"
            assert not warming.is_set()  # Candidate cannot load until M0 drains.
            release.set()
            check(await pending, "synthetic-m0", 1, 3)
            await asyncio.wait_for(warming.wait(), 3)
            assert (await client.get("/readyz")).status_code == 503
            denied = await client.post("/v1/inspect", headers=headers, json=body)
            assert denied.status_code == 503 and denied.json()["error_code"] == "not_ready"
            warmed.set()
            await asyncio.wait_for(swapping, 3)
            assert (await client.get("/readyz")).status_code == 200
            check(await client.post("/v1/inspect", headers=headers, json=body), "synthetic-m1", 4, 6)
            with pytest.raises(ModelRuntimeError) as caught:
                await manager.replace(invalid)
            assert secret not in str(caught.value)
            assert (await client.get("/readyz")).status_code == 200
            check(await client.post("/v1/inspect", headers=headers, json=body), "synthetic-m1", 4, 6)
    finally:
        release.set()
        warmed.set()
        for task in (pending, swapping):
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()
