"""R10: composed gateway uses detector-only private trust over real loopback TLS."""
import socket
import ssl
import threading
import time

from fastapi import FastAPI, Request
import httpx
import pytest
import uvicorn

from deployment.tests.test_gpu_tls import certificates  # Shared local OpenSSL fixture.
from gateway.server import create_server
from gateway.settings import Settings


@pytest.fixture
def detector_endpoint(certificates):
    app = FastAPI()
    calls = []

    @app.post("/v1/inspect")
    async def inspect(request: Request):
        body = await request.json()
        calls.append((request.headers.get("authorization"), body))
        return {"schema_version": "1.0", "request_id": body["request_id"],
            "inspection_id": body["inspection_id"], "status": "complete", "findings": [],
            "model_version": "synthetic-m0", "threshold_version": "v1", "timing_ms": 1.0,
            "timing_breakdown_ms": {"queue": 0.0, "inference": 1.0, "rules": 0.0, "total": 1.0}}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(10)
    port = listener.getsockname()[1]
    cert, key = certificates[0]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False,
        log_config=None, log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        yield f"https://127.0.0.1:{port}/v1/inspect", calls
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("variant", ["trusted", "wrong-ca", "wrong-hostname"])
async def test_composed_detector_tls_is_verified_and_scoped(tmp_path, certificates, detector_endpoint, variant):
    url, detector_calls = detector_endpoint
    if variant == "wrong-hostname":
        url = url.replace("127.0.0.1", "localhost")
    config = Settings.from_env({"PII_GATEWAY_LOCAL_TOKEN": "synthetic-local",
        "PII_DETECTOR_TOKEN": "synthetic-detector", "OPENAI_API_KEY": "synthetic-upstream",
        "PII_DETECTOR_URL": url, "PII_MODEL_ID": "mock-model",
        "PII_STATE_DIR": str(tmp_path / "state"),
        "PII_DETECTOR_CA_FILE": str(certificates[variant == "wrong-ca"][0])})
    factory_calls, upstream_calls = [], []

    def upstream(request):
        upstream_calls.append(request)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer synthetic-upstream"
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
            "role": "assistant", "content": [{"type": "output_text", "text": "SAFE"}]}]})

    def factory(**kwargs):
        factory_calls.append(kwargs)
        if len(factory_calls) == 1:
            # Real httpx TLS transport for detector; provider always isolated.
            return httpx.AsyncClient(**kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream), **kwargs)

    app = create_server(config, client_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
            response = await client.post("/v1/responses", headers={"Authorization": "Bearer synthetic-local"},
                                         json={"model": "mock-model", "input": "synthetic safe text"})
    if variant == "trusted":
        assert response.status_code == 200, response.text
        assert "SAFE" in response.text
        assert len(upstream_calls) == 1
        assert len(detector_calls) == 2  # outbound and inbound inspections
        assert all(auth == "Bearer synthetic-detector" for auth, _ in detector_calls)
    else:
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "INSPECTION_FAILED"
        assert upstream_calls == [] and detector_calls == []
    assert len(factory_calls) == 2
    assert factory_calls[0]["trust_env"] is False
    context = factory_calls[0]["verify"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert factory_calls[1] == {"trust_env": False, "verify": True}
