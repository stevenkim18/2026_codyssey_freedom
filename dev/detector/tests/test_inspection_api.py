"""Synthetic T05/T06/T07/T10 checks; no model/GPU performance claims."""
import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest

from detector.app import create_app
from detector.schemas import BackendResult


TOKEN = "synthetic-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def payload(text="안전한 합성 문장", **changes):
    data = {"schema_version": "1.0", "request_id": "req-1", "inspection_id": "inspect-1",
            "direction": "outbound", "segments": [{"segment_id": "input:0", "text": text}]}
    return data | changes


async def empty_backend(segments):
    return []


def request(app, data, headers=HEADERS, path="/v1/inspect"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, json=data, headers=headers)
    return asyncio.run(run())


def app_for(backend=empty_backend, **options):
    return create_app(backend=backend, token=TOKEN, model_version="mock-m0", threshold_version="mock-v1", **options)


def test_t07_complete_no_findings_has_identity_versions_and_timings():
    response = request(app_for(), payload())
    assert response.status_code == 200
    data = response.json()
    assert {key: data[key] for key in ("schema_version", "request_id", "inspection_id", "status", "findings", "model_version", "threshold_version")} == {
        "schema_version": "1.0", "request_id": "req-1", "inspection_id": "inspect-1", "status": "complete", "findings": [], "model_version": "mock-m0", "threshold_version": "mock-v1"}
    assert data["timing_ms"] >= 0
    assert set(data["timing_breakdown_ms"]) == {"queue", "inference", "rules", "total"}
    assert all(value >= 0 for value in data["timing_breakdown_ms"].values())


def test_t05_unicode_codepoint_spans_are_not_utf16():
    async def backend(segments):
        assert segments[0].text == "😀홍길동은 안전"
        return [{"segment_id": "input:0", "type": "PERSON", "start": 1, "end": 4, "score": 0.99, "detector": "model"}]
    response = request(app_for(backend), payload("😀홍길동은 안전"))
    assert response.status_code == 200
    assert response.json()["findings"][0]["start"] == 1
    assert response.json()["findings"][0]["end"] == 4


@pytest.mark.parametrize("change", [
    {"schema_version": "2.0"}, {"unknown": "sensitive"}, {"direction": "sideways"},
    {"request_id": 1}, {"segments": []},
    {"segments": [{"segment_id": "x", "text": 1}]},
    {"segments": [{"segment_id": "x", "text": "a", "unknown": "private"}]},
    {"segments": [{"segment_id": "x", "text": "a"}, {"segment_id": "x", "text": "b"}]},
])
def test_t07_strict_bad_request_rejected_without_echo(change):
    response = request(app_for(), payload(**change))
    assert response.status_code == 400
    assert response.json() == {"status": "error", "error_code": "invalid_request"}
    assert "sensitive" not in response.text and "private" not in response.text


def test_t05_total_codepoint_limit_is_exact_and_across_segments():
    app = app_for()
    assert request(app, payload("😀" * 16000)).status_code == 200
    data = payload(segments=[{"segment_id": "x", "text": "a" * 8000}, {"segment_id": "y", "text": "b" * 8001}])
    assert request(app, data).status_code == 413


def test_t05_single_segment_over_limit_uses_413_not_format_error():
    assert request(app_for(), payload("😀" * 16001)).status_code == 413


def test_t06_backend_cannot_bypass_validation_with_constructed_model():
    from detector.schemas import Finding
    async def backend(segments):
        return [Finding.model_construct(segment_id="input:0", type="UNKNOWN", start=0, end=1, score=0.9, detector="model")]
    response = request(app_for(backend), payload())
    assert response.status_code == 503


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), True])
def test_t06_invalid_timeout_configuration_rejected(timeout):
    with pytest.raises(ValueError):
        app_for(inspection_timeout_s=timeout)


def test_t06_request_cancellation_cancels_backend_and_recovers_capacity():
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()
        calls = 0
        async def backend(segments):
            nonlocal calls
            calls += 1
            if calls > 1:
                return []
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        app = app_for(backend, max_queue=0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            pending = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await started.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await cancelled.wait()
            await asyncio.sleep(0)
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 200
    asyncio.run(run())


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}])
def test_t10_authentication_rejects_before_backend(headers):
    async def forbidden(segments):
        pytest.fail("unauthorized request reached backend")
    assert request(app_for(forbidden), payload(), headers=headers).status_code == 401


def test_t06_default_app_is_not_ready_and_never_uses_fake_backend():
    app = create_app(token=TOKEN)
    response = request(app, payload())
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
    asyncio.run(run())


def test_t06_backend_without_locked_versions_is_not_ready():
    app = create_app(backend=empty_backend, token=TOKEN)
    assert request(app, payload()).status_code == 503


@pytest.mark.parametrize("finding", [
    {"type": "UNKNOWN"}, {"start": -1}, {"end": 1000}, {"start": 2, "end": 1},
    {"segment_id": "missing"}, {"score": float("nan")}, {"start": True}, {"detector": "unknown"},
])
def test_t06_invalid_backend_output_fails_closed(finding):
    async def backend(segments):
        return [{"segment_id": "input:0", "type": "PERSON", "start": 0, "end": 1, "score": 0.9, "detector": "model"} | finding]
    response = request(app_for(backend), payload())
    assert response.status_code == 503
    assert response.json()["status"] == "error"
    assert response.json()["findings"] == []


def test_t06_incomplete_is_distinct_from_clean_complete_and_discards_partial_findings():
    async def backend(segments):
        return BackendResult(status="incomplete", findings=[])
    response = request(app_for(backend), payload())
    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"


def test_t10_backend_exception_does_not_echo_sensitive_message(caplog):
    async def backend(segments):
        raise RuntimeError("PRIVATE-SYNTHETIC-VALUE")
    response = request(app_for(backend), payload("PRIVATE-SYNTHETIC-VALUE"))
    assert response.status_code == 503
    assert "PRIVATE-SYNTHETIC-VALUE" not in response.text + caplog.text


def test_t06_timeout_returns_error_and_holds_slot_until_cancelled_backend_stops():
    async def run():
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def backend(segments):
            started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await stopped.wait()
                return []
        app = app_for(backend, inspection_timeout_s=0.01, max_queue=0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert started.is_set()
            assert response.status_code == 504
            assert response.json()["status"] == "error"
            overloaded = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert overloaded.status_code == 429
            stopped.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    asyncio.run(run())


def test_t06_queue_bound_and_wait_deadline():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        async def backend(segments):
            started.set()
            await release.wait()
            return []
        app = app_for(backend, max_queue=1, queue_timeout_s=0.03)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await started.wait()
            second = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await asyncio.sleep(0.005)
            third = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert third.status_code == 429
            assert (await second).status_code == 429
            release.set()
            assert (await first).status_code == 200
    asyncio.run(run())


def test_t06_total_inspection_budget_includes_queue_wait():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        async def backend(segments):
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await release.wait()
            else:
                await asyncio.sleep(0.04)
            return []
        app = app_for(backend, queue_timeout_s=0.2, inspection_timeout_s=0.065)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await started.wait()
            second = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await asyncio.sleep(0.04)
            release.set()
            assert (await first).status_code == 200
            assert (await second).status_code == 504
    asyncio.run(run())


def test_t03_rule_detectors_merge_key_and_private_key_findings():
    content = b"\x02\x01\x00\x30\x04\x06\x02\x2a\x03\x04\x40" + bytes(range(64))
    der = b"\x30" + bytes([len(content)]) + content
    text = "😀 key sk-proj-" + "a" * 64 + "\n-----BEGIN PRIVATE KEY-----\n" + base64.b64encode(der).decode() + "\n-----END PRIVATE KEY-----"
    response = request(app_for(), payload(text))
    assert response.status_code == 200
    findings = response.json()["findings"]
    assert {f["type"] for f in findings} == {"API_KEY", "PRIVATE_KEY"}
    assert all(f["detector"] == "rule" and text[f["start"]:f["end"]] for f in findings)
    assert text not in response.text


def test_t03_detector_and_gateway_use_same_secret_formats():
    from gateway.secret_rules import detect
    text = "sk-proj-" + "a" * 32 + "\n-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----"
    assert request(app_for(), payload(text)).json()["findings"] == detect(payload(text)["segments"])


def test_t07_published_contract_matches_runtime_schemas():
    from detector.schemas import inspection_contract
    published = json.loads((Path(__file__).parents[2] / "contracts" / "inspection.schema.json").read_text())
    assert published == inspection_contract()


def raw_http(app, chunks, headers=None, *, path="/v1/inspect", method="POST"):
    """Call ASGI directly so authentication/body consumption is observable."""
    async def run():
        consumed = 0
        messages = []
        async def receive():
            nonlocal consumed
            if consumed >= len(chunks):
                return {"type": "http.disconnect"}
            content = chunks[consumed]
            consumed += 1
            return {"type": "http.request", "body": content, "more_body": consumed < len(chunks)}
        async def send(message):
            messages.append(message)
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "headers": headers if headers is not None else [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 1000), "server": ("test", 80)}
        await app(scope, receive, send)
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return status, body, consumed
    return asyncio.run(run())


def test_t10_unauthenticated_body_is_not_consumed_or_parsed():
    status, body, consumed = raw_http(app_for(), [b"not-json"], headers=[])
    assert status == 401
    assert consumed == 0
    assert b"not-json" not in body


def test_t10_content_length_over_limit_is_rejected_without_consumption():
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json"), (b"content-length", b"1048577")]
    status, _, consumed = raw_http(app_for(), [b"{}"], headers=headers)
    assert status == 413 and consumed == 0


def test_t10_chunked_body_limit_stops_at_first_overflow_chunk():
    status, body, consumed = raw_http(app_for(), [b" " * 600000, b" " * 600000, b"PRIVATE-UNREAD"])
    assert status == 413 and consumed == 2
    assert b"PRIVATE" not in body


def test_t10_duplicate_json_keys_are_rejected_before_backend():
    raw = json.dumps(payload()).encode()
    raw = b'{"schema_version":"1.0",' + raw[1:]
    status, body, _ = raw_http(app_for(), [raw])
    assert status == 400
    assert json.loads(body) == {"status": "error", "error_code": "invalid_request"}


@pytest.mark.parametrize("encoding", [b"gzip", b"br", b"identity,gzip"])
def test_t10_compressed_requests_rejected_without_reading(encoding):
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json"), (b"content-encoding", encoding)]
    status, _, consumed = raw_http(app_for(), [b"compressed-PRIVATE"], headers=headers)
    assert status == 400 and consumed == 0


@pytest.mark.parametrize("token", ["", " ", "newline\n", "한글", 123])
def test_t10_invalid_server_token_configuration_fails_early(token):
    with pytest.raises(ValueError):
        create_app(token=token)


@pytest.mark.parametrize("options", [{"backend": 1}, {"model_version": ""}, {"threshold_version": None},
    {"max_queue": True}, {"max_queue": -1}, {"max_queue": 0.5}, {"queue_timeout_s": 0},
    {"queue_timeout_s": "2"}, {"inspection_timeout_s": -1}])
def test_t06_invalid_factory_configuration_rejected(options):
    with pytest.raises(ValueError):
        create_app(**options)


@pytest.mark.parametrize("extra", [
    [(b"authorization", f"Bearer {TOKEN}".encode())],
    [(b"content-length", b"-1")], [(b"content-length", b"1.5")],
    [(b"content-length", b"1"), (b"content-length", b"1")],
    [(b"content-length", b"9" * 5000)], [(b"content-type", b"application/json")],
    [(b"content-encoding", b"identity"), (b"content-encoding", b"identity")],
])
def test_t10_ambiguous_headers_rejected_without_consuming_body(extra):
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json")] + extra
    status, _, consumed = raw_http(app_for(), [b"PRIVATE"], headers=headers)
    assert status in {400, 401} and consumed == 0


@pytest.mark.parametrize("data", [b"", b"not-json", b"[]", b"\xff", b'{"x":NaN}', b'{"x":Infinity}',
    b'{"x":-Infinity}', b'{"x":1e999}', b'{"x":0.5}', b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}',
    b'{"segments":[{"segment_id":"s","segment_id":"s","text":"synthetic"}]}'])
def test_t10_invalid_json_is_sanitized(data, caplog):
    status, body, _ = raw_http(app_for(), [data])
    assert status == 400
    assert json.loads(body) == {"status": "error", "error_code": "invalid_request"}
    assert "synthetic" not in body.decode() + caplog.text


def test_t10_json_string_brackets_and_escaped_quotes_do_not_count_as_nesting():
    data = json.dumps(payload('"\\[]{}' * 100)).encode()
    status, _, _ = raw_http(app_for(), [data])
    assert status == 200


def test_t10_one_mebibyte_json_boundary_is_inclusive():
    data = json.dumps(payload()).encode()
    data += b" " * (1024 * 1024 - len(data))
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json; charset=utf-8"),
               (b"content-length", str(len(data)).encode()), (b"content-encoding", b"identity")]
    status, _, _ = raw_http(app_for(), [data[:500000], data[500000:]], headers=headers)
    assert status == 200


def test_t10_content_length_mismatch_is_invalid():
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"application/json"), (b"content-length", b"1")]
    assert raw_http(app_for(), [b"{}"], headers=headers)[0] == 400


def test_t10_wrong_content_type_rejected_before_reading():
    headers = [(b"authorization", f"Bearer {TOKEN}".encode()), (b"content-type", b"text/plain")]
    status, _, consumed = raw_http(app_for(), [b"PRIVATE"], headers=headers)
    assert status == 400 and consumed == 0


def test_t10_only_named_get_health_endpoints_are_public_and_do_not_read_body():
    for path in ("/healthz", "/readyz"):
        status, _, consumed = raw_http(app_for(), [b"PRIVATE"], headers=[], path=path, method="GET")
        assert status == 200 and consumed == 0
    assert raw_http(app_for(), [b"PRIVATE"], headers=[], path="/unknown", method="GET")[0] == 401
    status, _, consumed = raw_http(app_for(), [b"PRIVATE"], path="/unknown", method="GET")
    assert status == 404 and consumed == 0


def test_t10_missing_configuration_token_cannot_authorize_inspection():
    status, _, consumed = raw_http(create_app(), [b"PRIVATE"])
    assert status == 401 and consumed == 0


def test_t06_backend_self_cancellation_returns_safe_error():
    async def backend(segments):
        raise asyncio.CancelledError
    response = request(app_for(backend), payload())
    assert response.status_code == 503
    assert response.json()["error_code"] == "backend_cancelled"


def test_t06_queued_request_cancelled_does_not_leak_capacity():
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        async def backend(segments):
            started.set()
            await release.wait()
            return []
        app = app_for(backend, max_queue=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await started.wait()
            queued = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await asyncio.sleep(0)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            release.set()
            assert (await first).status_code == 200
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 200
    asyncio.run(run())


def test_t06_queued_request_total_budget_expires_with_busy_cancelled_backend():
    async def run():
        release = asyncio.Event()
        async def backend(segments):
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await release.wait()
            return []
        app = app_for(backend, max_queue=1, queue_timeout_s=1, inspection_timeout_s=0.01)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 504
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert response.status_code == 504
            assert response.json()["error_code"] == "inspection_timeout"
            release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
    asyncio.run(run())


def test_t07_manager_initial_state_is_unready_until_verified_warmup(tmp_path):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    root, _ = package(tmp_path)
    async def run():
        manager = ModelManager(lambda *args: FakeBackend())
        app = create_app(manager=manager, token=TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 503
            await manager.initialize(root)
            assert (await client.get("/readyz")).status_code == 200
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert response.status_code == 200
            assert response.json()["model_version"] == "mock-v1"
            assert response.json()["threshold_version"] == "mock-v1-thresholds"
    asyncio.run(run())


def test_t07_manager_swap_blocks_new_requests_and_preserves_inflight_version(tmp_path):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        warming, warmed = asyncio.Event(), asyncio.Event()
        class Backend(FakeBackend):
            def __init__(self, version):
                super().__init__()
                self.version = version
            async def warmup(self):
                if self.version == "new":
                    warming.set()
                    await warmed.wait()
                await super().warmup()
            async def __call__(self, segments):
                if self.version == "old":
                    entered.set()
                    await release.wait()
                return BackendResult()
        manager = ModelManager(lambda path, manifest: Backend(manifest["model_version"]))
        await manager.initialize(old)
        app = create_app(manager=manager, token=TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            existing = asyncio.create_task(client.post("/v1/inspect", json=payload(), headers=HEADERS))
            await entered.wait()
            swapping = asyncio.create_task(manager.replace(new))
            await asyncio.sleep(0)
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 503
            assert not warming.is_set()
            release.set()
            response = await existing
            assert response.status_code == 200 and response.json()["model_version"] == "old"
            await warming.wait()
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 503
            warmed.set()
            await swapping
            assert (await client.get("/readyz")).status_code == 200
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert response.status_code == 200 and response.json()["model_version"] == "new"
            assert response.json()["threshold_version"] == "new-thresholds"
    asyncio.run(run())


def test_t07_response_uses_inspected_version_after_concurrent_replacement_commits(tmp_path):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    class CompletionRaceManager(ModelManager):
        async def inspect_with_metadata(self, segments):
            inspected = await super().inspect_with_metadata(segments)
            await self.replace(new)
            return inspected
    async def run():
        manager = CompletionRaceManager(lambda *args: FakeBackend())
        await manager.initialize(old)
        app = create_app(manager=manager, token=TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert manager.model_version == "new"
            assert response.status_code == 200
            assert response.json()["model_version"] == "old"
            assert response.json()["threshold_version"] == "old-thresholds"
    asyncio.run(run())


def test_t07_failed_manager_replacement_restores_api_readiness_and_old_versions(tmp_path):
    from detector.runtime import ModelManager, ModelRuntimeError
    from detector.tests.test_runtime import FakeBackend, package
    old, _ = package(tmp_path, "old")
    bad, _ = package(tmp_path, "bad")
    async def run():
        manager = ModelManager(lambda path, manifest: FakeBackend(warmup_fails=manifest["model_version"] == "bad"))
        await manager.initialize(old)
        app = create_app(manager=manager, token=TOKEN)
        with pytest.raises(ModelRuntimeError):
            await manager.replace(bad)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/readyz")).status_code == 200
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert response.status_code == 200 and response.json()["model_version"] == "old"
    asyncio.run(run())


@pytest.mark.parametrize("managed", [False, True])
def test_t07_backend_cannot_mutate_original_text_and_claim_clean(tmp_path, managed):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    async def malicious(segments):
        segments[0].text = "replacement"
        return []
    if not managed:
        assert request(app_for(malicious), payload()).status_code == 503
        return
    class Backend(FakeBackend):
        __call__ = staticmethod(malicious)
    root, _ = package(tmp_path)
    async def run():
        manager = ModelManager(lambda *args: Backend())
        await manager.initialize(root)
        app = create_app(manager=manager, token=TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 503
    asyncio.run(run())


def test_t06_repeated_cancel_cannot_release_native_worker_drain(tmp_path):
    import threading
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import package
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    entered, release = threading.Event(), threading.Event()
    async def run():
        loaded = []
        class Backend:
            def warmup(self):
                pass
            def __call__(self, segments):
                entered.set()
                release.wait(2)
                return []
        def loader(path, manifest):
            loaded.append(manifest["model_version"])
            return Backend()
        manager = ModelManager(loader)
        await manager.initialize(old)
        pending = asyncio.create_task(manager.inspect([{"segment_id": "s", "text": "synthetic"}]))
        await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        swapping = asyncio.create_task(manager.replace(new))
        try:
            await asyncio.sleep(0.02)
            assert not pending.done()
            assert loaded == ["old"] and not swapping.done()
            assert not manager.ready
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await swapping
    asyncio.run(run())


def test_t07_repeated_cancel_of_replacement_keeps_api_unready_until_native_load_stops(tmp_path):
    import threading
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    entered, release = threading.Event(), threading.Event()
    def loader(path, manifest):
        if manifest["model_version"] == "new":
            entered.set()
            release.wait(2)
        return FakeBackend()
    async def run():
        manager = ModelManager(loader)
        await manager.initialize(old)
        app = create_app(manager=manager, token=TOKEN)
        pending = asyncio.create_task(manager.replace(new))
        await asyncio.to_thread(entered.wait, 2)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/readyz")).status_code == 503
                assert not pending.done()
                assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 503
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
        assert manager.ready and manager.model_version == "old"
    asyncio.run(run())


@pytest.mark.parametrize("conflict", ["invalid_manager", "backend", "model_version", "threshold_version"])
def test_t07_conflicting_manager_configuration_is_rejected(conflict):
    from detector.runtime import ModelManager
    manager = ModelManager(lambda *args: None)
    options = {"manager": object() if conflict == "invalid_manager" else manager}
    if conflict == "backend":
        options["backend"] = empty_backend
    elif conflict in {"model_version", "threshold_version"}:
        options[conflict] = "manual-override"
    with pytest.raises(ValueError):
        create_app(**options)


def test_t07_manager_readiness_still_requires_api_authentication_configuration(tmp_path):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    root, _ = package(tmp_path)
    async def run():
        manager = ModelManager(lambda *args: FakeBackend())
        await manager.initialize(root)
        app = create_app(manager=manager)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 401
    asyncio.run(run())


def test_t06_manager_timeout_preserves_app_slot_and_drains_before_replacement(tmp_path):
    from detector.runtime import ModelManager
    from detector.tests.test_runtime import FakeBackend, package
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    async def run():
        release = asyncio.Event()
        class Backend(FakeBackend):
            def __init__(self, version):
                super().__init__()
                self.version = version
            async def __call__(self, segments):
                if self.version == "old":
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        await release.wait()
                return BackendResult()
        manager = ModelManager(lambda path, manifest: Backend(manifest["model_version"]))
        await manager.initialize(old)
        app = create_app(manager=manager, token=TOKEN, max_queue=0, inspection_timeout_s=0.01)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 504
            assert (await client.post("/v1/inspect", json=payload(), headers=HEADERS)).status_code == 429
            swapping = asyncio.create_task(manager.replace(new))
            await asyncio.sleep(0)
            assert not manager.ready and not swapping.done()
            release.set()
            await swapping
            response = await client.post("/v1/inspect", json=payload(), headers=HEADERS)
            assert response.status_code == 200 and response.json()["model_version"] == "new"
    asyncio.run(run())
