"""R03/R05/R06/R10: synthetic HTTP boundary checks, never live credentials."""
import asyncio
import gzip
import json

import httpx
import pytest

from gateway.transport import InspectionClient, TransportError, UpstreamClient


SEGMENTS = [{"segment_id": "input:0", "text": "😀 synthetic only"}]


def inspection(**changes):
    return {"schema_version": "1.0", "request_id": "r1", "inspection_id": "i1", "status": "complete",
            "findings": [], "model_version": "mock-m0", "threshold_version": "mock-t0", "timing_ms": 1.0,
            "timing_breakdown_ms": {"queue": 0.0, "inference": 1.0, "rules": 0.0, "total": 1.0}} | changes


async def call_inspector(handler, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = InspectionClient(client, "https://gpu.example.invalid/v1/inspect", "synthetic-inspector", **options)
        return await adapter(SEGMENTS, "outbound", "r1", "i1")


async def call_upstream(handler, body=None, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = UpstreamClient(client, "https://api.openai.com/v1/responses", "synthetic-upstream", {"model-test"}, **options)
        return await adapter(body or {"model": "model-test", "input": "synthetic", "stream": True})


def test_t03_inspector_exact_body_and_separate_bearer_token():
    calls = []
    async def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-inspector"
        assert json.loads(request.content) == {"schema_version": "1.0", "request_id": "r1", "inspection_id": "i1", "direction": "outbound", "segments": SEGMENTS}
        return httpx.Response(200, json=inspection())
    assert asyncio.run(call_inspector(handler)) == inspection()
    assert len(calls) == 1


def test_t05_upstream_forces_buffered_json_without_mutating_body():
    body = {"model": "model-test", "input": "synthetic", "stream": True}
    async def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-upstream"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == body | {"stream": False}
        return httpx.Response(200, json={"id": "safe", "output": []})
    assert asyncio.run(call_upstream(handler, body)) == {"id": "safe", "output": []}
    assert body["stream"] is True


@pytest.mark.parametrize("url", ["http://gpu.example/v1/inspect", "https://user:secret@gpu.example/v1/inspect",
    "https://gpu.example/v1/inspect?key=secret", "https://gpu.example/v1/inspect#fragment", "https://gpu.example/other",
    "https://gpu.example/v1/inspect\n", "https://gpu.example\\@127.0.0.1/v1/inspect"])
def test_t10_inspector_rejects_unsafe_destinations(url):
    with httpx.Client() as unused:
        with pytest.raises(TransportError):
            InspectionClient(unused, url, "synthetic")


@pytest.mark.parametrize("url", ["https://evil.invalid/v1/responses", "https://api.openai.com:443/v1/responses",
    "https://api.openai.com/v1/responses?x=1", "https://api.openai.com/v1/other", "http://api.openai.com/v1/responses"])
def test_t10_upstream_production_destination_is_exact(url):
    with pytest.raises(TransportError):
        UpstreamClient(None, url, "synthetic", {"model-test"})


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "[::1]:8000", "localhost:8000"])
def test_t10_explicit_loopback_test_escape_hatch(host):
    InspectionClient(None, f"http://{host}/v1/inspect", "synthetic", allow_loopback_http=True)
    UpstreamClient(None, f"http://{host}/v1/responses", "synthetic", {"model-test"}, allow_loopback_http=True)
    with pytest.raises(TransportError):
        InspectionClient(None, f"http://{host}/v1/inspect", "synthetic")


def test_t10_loopback_flag_does_not_allow_remote_http():
    with pytest.raises(TransportError):
        UpstreamClient(None, "http://remote.invalid/v1/responses", "synthetic", {"model-test"}, allow_loopback_http=True)


@pytest.mark.parametrize("token", ["", " ", " leading", "line\nbreak", "line\rbreak", "unicode-한글", None])
def test_t10_credentials_reject_header_injection(token):
    with pytest.raises(TransportError):
        InspectionClient(None, "https://gpu.example/v1/inspect", token)
    with pytest.raises(TransportError):
        UpstreamClient(None, "https://api.openai.com/v1/responses", token, {"model-test"})


@pytest.mark.parametrize("change", [{"schema_version": "2.0"}, {"request_id": "other"}, {"inspection_id": "other"},
    {"status": "mystery"}, {"findings": "unsafe"}, {"timing_ms": float("nan")}, {"model_version": ""}])
def test_t03_corrupt_inspection_responses_fail_closed(change):
    async def handler(request):
        # Python JSON encoder permits NaN, deliberately testing defensive parsing.
        return httpx.Response(200, content=json.dumps(inspection(**change)), headers={"Content-Type": "application/json"})
    with pytest.raises(TransportError):
        asyncio.run(call_inspector(handler))


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 429, 500, 503])
def test_t06_http_error_is_safe_has_one_attempt_and_never_follows_redirect(status, caplog):
    calls = []
    async def handler(request):
        calls.append(request)
        return httpx.Response(status, text="SYNTHETIC-SECRET-RAW", headers={"Location": "https://evil.invalid"})
    with pytest.raises(TransportError) as caught:
        asyncio.run(call_upstream(handler))
    assert "SYNTHETIC-SECRET-RAW" not in str(caught.value) + caplog.text
    assert caught.value.code == "upstream_http_error"
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"), httpx.Response(200, json=[]),
    httpx.Response(200, content=b"\xff", headers={"Content-Type": "application/json"}),
    httpx.Response(200, content=gzip.compress(b"{}"), headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}),
    httpx.Response(200, content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "1048577"}),
])
def test_t05_malformed_or_encoded_response_is_rejected(response):
    with pytest.raises(TransportError):
        asyncio.run(call_upstream(lambda request: response))


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def test_t05_chunked_response_limit_is_enforced_without_content_length():
    stream = Chunks([b" " * 600000, b" " * 600000])
    async def handler(request):
        return httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})
    with pytest.raises(TransportError) as caught:
        asyncio.run(call_upstream(handler))
    assert caught.value.code == "upstream_response_limit"
    assert stream.closed


def test_t06_http_exception_is_sanitized_and_not_retried():
    calls = []
    async def handler(request):
        calls.append(request)
        raise httpx.ConnectError("SYNTHETIC-SECRET-RAW", request=request)
    with pytest.raises(TransportError) as caught:
        asyncio.run(call_inspector(handler))
    assert "SYNTHETIC-SECRET-RAW" not in str(caught.value)
    assert len(calls) == 1


def test_t06_total_timeout_is_enforced_independently_of_httpx_mock(monkeypatch):
    import gateway.transport as module
    monkeypatch.setattr(module, "INSPECTION_TIMEOUT_S", 0.01)
    async def handler(request):
        await asyncio.sleep(10)
    with pytest.raises(TransportError) as caught:
        asyncio.run(call_inspector(handler))
    assert caught.value.code == "inspection_timeout"


def test_t06_cancellation_propagates_without_retry():
    async def run():
        entered = asyncio.Event()
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            entered.set()
            await asyncio.sleep(10)
        task = asyncio.create_task(call_upstream(handler))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == 1
    asyncio.run(run())


def test_t10_client_default_query_cannot_change_fixed_destination():
    async def run():
        async def handler(request):
            pytest.fail("query-bearing request must not leave the PC")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), params={"secret": "unsafe"}) as client:
            adapter = UpstreamClient(client, "https://api.openai.com/v1/responses", "synthetic", {"model-test"})
            with pytest.raises(TransportError):
                await adapter({"model": "model-test", "input": "safe"})
    asyncio.run(run())


def test_t10_model_allowlist_is_checked_before_http():
    async def handler(request):
        pytest.fail("unapproved model must not leave the PC")
    with pytest.raises(TransportError):
        asyncio.run(call_upstream(handler, {"model": "unapproved", "input": "safe"}))


@pytest.mark.parametrize("allowlist", [set(), "model-test", {""}, {1}])
def test_t10_invalid_model_allowlist_is_rejected(allowlist):
    with pytest.raises(TransportError):
        UpstreamClient(None, "https://api.openai.com/v1/responses", "synthetic", allowlist)


@pytest.mark.parametrize("data", [b'{"output": [], "output": []}', b'{"timing_ms": NaN}', b'{}'])
def test_t03_duplicate_keys_constants_and_absent_contract_are_rejected(data):
    async def handler(request):
        return httpx.Response(200, content=data, headers={"Content-Type": "application/json"})
    with pytest.raises(TransportError):
        asyncio.run(call_inspector(handler))


def test_t03_inspector_invalid_request_never_reaches_network():
    async def run():
        async def handler(request):
            pytest.fail("invalid inspection must not leave PC")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = InspectionClient(client, "https://gpu.example.invalid/v1/inspect", "synthetic")
            for segments in ([{"segment_id": "s", "text": "a" * 16001}], [{"segment_id": "s", "text": 1}]):
                with pytest.raises(TransportError):
                    await adapter(segments, "outbound", "r1", "i1")
    asyncio.run(run())


def test_t03_inspector_span_is_validated_against_original_segment():
    async def handler(request):
        finding = {"segment_id": "input:0", "type": "PERSON", "start": 0, "end": 1000, "score": 0.9, "detector": "model"}
        return httpx.Response(200, json=inspection(findings=[finding]))
    with pytest.raises(TransportError):
        asyncio.run(call_inspector(handler))


def test_t10_nondecimal_content_length_is_rejected():
    async def handler(request):
        return httpx.Response(200, json={}, headers={"Content-Length": "invalid"})
    with pytest.raises(TransportError):
        asyncio.run(call_upstream(handler))


@pytest.mark.parametrize("status", [200, 503])
def test_t06_close_failure_does_not_echo_raw_remote_exception(status):
    class BrokenClose(Chunks):
        async def aclose(self):
            raise RuntimeError("SYNTHETIC-SECRET-CLOSE")
    async def handler(request):
        return httpx.Response(status, stream=BrokenClose([b"{}"]), headers={"Content-Type": "application/json"})
    with pytest.raises(TransportError) as caught:
        asyncio.run(call_upstream(handler))
    assert "SYNTHETIC-SECRET-CLOSE" not in str(caught.value)
