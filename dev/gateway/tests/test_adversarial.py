"""R02/R05/R06/R08/R10: hostile protocol and asynchronous fault boundaries.

All content is synthetic. Assertions observe external request bodies and ASGI
bytes, rather than treating an internal policy decision as delivery evidence.
"""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from gateway.app import create_app
from gateway.engine import Gateway, GatewayError
from gateway.journal import JournalError


REQUEST = {"model": "mock-model", "input": "synthetic safe request"}
AUTH = {"Authorization": "Bearer synthetic-local-token"}


def output(text="synthetic safe response"):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}]}


class MetadataJournal:
    def __init__(self, fail_at=None):
        self.events, self.calls, self.fail_at = [], 0, fail_at

    def append(self, event):
        self.calls += 1
        if self.calls == self.fail_at:
            raise JournalError("SYNTHETIC_PRIVATE_JOURNAL_ERROR")
        self.events.append(deepcopy(event))


class ControlledInspector:
    def __init__(self, mutation=None):
        self.calls, self.mutation = [], mutation

    async def __call__(self, segments, direction, request_id, inspection_id):
        self.calls.append(deepcopy((segments, direction)))
        findings = []
        for segment in segments:
            if "SYNTHETIC_PERSON" in segment["text"]:
                start = segment["text"].index("SYNTHETIC_PERSON")
                findings.append({"segment_id": segment["segment_id"], "type": "PERSON", "start": start,
                                 "end": start + len("SYNTHETIC_PERSON"), "score": 1.0, "detector": "model"})
        result = {"schema_version": "1.0", "request_id": request_id, "inspection_id": inspection_id,
                  "status": "complete", "findings": findings, "model_version": "synthetic-m0",
                  "threshold_version": "synthetic-threshold-v1", "timing_ms": 0.1}
        if self.mutation:
            self.mutation(result, len(self.calls), direction)
        return result


class ControlledUpstream:
    def __init__(self, result=None, error=None):
        self.received, self.result, self.error = [], output() if result is None else result, error
        self.entered, self.release = asyncio.Event(), None

    async def __call__(self, body):
        self.received.append(deepcopy(body))
        self.entered.set()
        if self.release:
            await self.release.wait()
        if self.error:
            raise self.error
        return deepcopy(self.result)


def gateway_parts(*, mutation=None, fail_at=None, result=None, error=None, max_inflight=8):
    inspector = ControlledInspector(mutation)
    upstream = ControlledUpstream(result, error)
    journal = MetadataJournal(fail_at)
    gateway = Gateway(inspector, upstream, journal, allowed_models={"mock-model"}, max_inflight=max_inflight)
    return gateway, inspector, upstream, journal


@pytest.mark.parametrize("field", ["model_version", "threshold_version"])
async def test_inbound_cannot_switch_model_or_threshold_within_one_request(field):
    def mutate(result, call, direction):
        if direction == "inbound":
            result[field] = "synthetic-changed-v2"
    gateway, _, upstream, _ = gateway_parts(mutation=mutate)
    with pytest.raises(GatewayError):
        await gateway.handle(REQUEST)
    assert len(upstream.received) == 1


async def test_cancelled_inspection_cannot_dispatch_even_if_transport_suppresses_cancellation():
    gateway, original, upstream, _ = gateway_parts()
    entered = asyncio.Event()

    async def cancellation_suppressing_inspector(*args):
        if original.calls:
            return await original(*args)
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Some threaded or remote transports finish after cancellation.
            return await original(*args)

    gateway.inspector = cancellation_suppressing_inspector
    task = asyncio.create_task(gateway.handle(REQUEST))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert upstream.received == []


@pytest.mark.parametrize("field", ["model_version", "threshold_version"])
async def test_mask_reinspection_rejects_version_switch_before_any_dispatch(field):
    def mutate(result, call, direction):
        if call == 2:
            result[field] = "synthetic-changed-v2"
    gateway, inspector, upstream, _ = gateway_parts(mutation=mutate)
    with pytest.raises(GatewayError) as failure:
        await gateway.handle({**REQUEST, "input": "SYNTHETIC_PERSON"})
    assert failure.value.code == "REINSPECTION_FAILED"
    assert len(inspector.calls) == 2
    assert upstream.received == []


@pytest.mark.parametrize("patch", [
    {"reasoning": {"effort": []}}, {"reasoning": {"effort": {"raw": "synthetic"}}},
    {"text": {"verbosity": []}}, {"model": []}, {"store": []}, {"tools": {}},
    {"tool_choice": {}}, {"parallel_tool_calls": 1}, {"max_output_tokens": True},
    {"input": [None]}, {"input": [{"role": [], "content": "synthetic"}]},
    {"input": [{"role": "user", "content": [None]}]},
    {"input": [{"role": "user", "content": [{"type": {}, "text": "synthetic"}]}]},
    {"input": [{"role": "user", "content": [{"type": "input_text", "text": []}]}]},
    {"input": [{"role": "assistant", "content": [{"type": "input_text", "text": "synthetic"}]}]},
    {"input": [{"role": "user", "content": []}]},
    {"input": []}, {"instructions": "\ud800"},
    {"metadata": {"raw": "SYNTHETIC_UNINSPECTED_METADATA"}},
    {"previous_response_id": "resp_SYNTHETIC_UNINSPECTED_HISTORY"},
    {"conversation": "SYNTHETIC_UNINSPECTED_HISTORY"},
])
async def test_hostile_nested_requests_return_safe_400_without_dispatch(patch):
    gateway, inspector, upstream, _ = gateway_parts()
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        # ensure_ascii serializes lone-surrogate adversarial JSON for parser testing.
        wire = json.dumps({**REQUEST, **patch}, ensure_ascii=True).encode()
        result = await client.post("/v1/responses", headers={**AUTH, "Content-Type": "application/json"}, content=wire)
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "UNSUPPORTED_PROTOCOL"
    assert "SYNTHETIC_UNINSPECTED" not in result.text
    assert inspector.calls == []
    assert upstream.received == []


@pytest.mark.parametrize("fail_at,expected_dispatches", [(1, 0), (2, 0), (3, 1), (4, 1), (5, 1)])
async def test_each_journal_failure_before_cli_send_releases_no_response_content(fail_at, expected_dispatches):
    gateway, _, upstream, _ = gateway_parts(fail_at=fail_at, result=output("SYNTHETIC_SAFE_BUT_UNCOMMITTED_OUTPUT"))
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers=AUTH, json={**REQUEST, "stream": True})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "JOURNAL_UNAVAILABLE"
    assert "SYNTHETIC" not in result.text
    assert len(upstream.received) == expected_dispatches


async def test_failed_post_send_audit_freezes_new_work_and_preserves_unknown_delivery():
    gateway, _, upstream, journal = gateway_parts(fail_at=6)
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        first = await client.post("/v1/responses", headers=AUTH, json=REQUEST)
        readiness = await client.get("/readyz")
        second = await client.post("/v1/responses", headers=AUTH, json=REQUEST)
    assert first.status_code == 200
    assert readiness.status_code == second.status_code == 503
    assert len(upstream.received) == 1
    assert journal.events[-1]["direction"] == "inbound"
    assert journal.events[-1]["delivery_status"] == "unknown"
    assert not any(event["delivery_status"] == "delivered" for event in journal.events)


async def test_upstream_exception_keeps_unknown_dispatch_and_never_retries_or_echoes_error():
    gateway, _, upstream, journal = gateway_parts(error=RuntimeError("SYNTHETIC_RAW_PROVIDER_ERROR"))
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers=AUTH, json=REQUEST)
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "UPSTREAM_FAILED"
    assert "SYNTHETIC_RAW_PROVIDER_ERROR" not in result.text
    assert len(upstream.received) == 1
    assert journal.events[-1]["delivery_status"] == "unknown"


async def test_upstream_metadata_is_stripped_at_every_output_level_before_sse():
    raw = "SYNTHETIC_UNINSPECTED_METADATA"
    source = output("SYNTHETIC_PERSON")
    source.update(id=raw, model=raw, metadata={raw: raw}, error={"message": raw})
    source["output"][0].update(id=raw, unknown={raw: raw})
    source["output"][0]["content"][0].update(annotations=[{"text": raw}], logprobs=raw, unknown=raw)
    gateway, _, upstream, _ = gateway_parts(result=source)
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers=AUTH, json={**REQUEST, "stream": True})
    assert result.status_code == 200
    assert raw not in result.text
    assert "SYNTHETIC_PERSON" not in result.text
    assert "[REDACTED]" in result.text
    assert "response.completed" in result.text
    assert len(upstream.received) == 1


async def test_capacity_rejection_cannot_dispatch_and_cancel_releases_slot():
    gateway, _, upstream, _ = gateway_parts(max_inflight=1)
    upstream.release = asyncio.Event()
    first = asyncio.create_task(gateway.handle(REQUEST))
    await upstream.entered.wait()
    with pytest.raises(GatewayError) as failure:
        await gateway.handle(REQUEST)
    assert failure.value.code == "GATEWAY_BUSY"
    assert failure.value.status == 429
    assert len(upstream.received) == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    upstream.release.set()
    safe = await gateway.handle(REQUEST)
    assert safe.body["status"] == "completed"
    assert len(upstream.received) == 2


def asgi_scope():
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/v1/responses", "query_string": b"", "root_path": "",
            "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1234),
            "headers": [(b"host", b"127.0.0.1"), (b"authorization", b"Bearer synthetic-local-token"),
                        (b"content-type", b"application/json")]}


async def test_client_disconnect_during_upstream_discards_late_safe_bytes():
    gateway, _, upstream, journal = gateway_parts(result=output("SYNTHETIC_LATE_OUTPUT"))
    upstream.release = asyncio.Event()
    app = create_app(gateway, token="synthetic-local-token")
    incoming, sent = asyncio.Queue(), []
    await incoming.put({"type": "http.request", "body": json.dumps({**REQUEST, "stream": True}).encode(), "more_body": False})

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app(asgi_scope(), incoming.get, send))
    await upstream.entered.wait()
    assert sent == []
    await incoming.put({"type": "http.disconnect"})
    await asyncio.wait_for(task, 1)
    upstream.release.set()
    wire = b"".join(message.get("body", b"") for message in sent)
    assert b"SYNTHETIC_LATE_OUTPUT" not in wire
    assert b"response.output_text" not in wire
    assert len(upstream.received) == 1
    assert journal.events[-1]["delivery_status"] == "unknown"


@pytest.mark.parametrize("phase", ["initial", "recheck", "upstream"])
async def test_expired_deadline_cannot_be_swallowed_by_transport(phase, monkeypatch):
    # Exercise the real asyncio deadline at a short interval, including adapters
    # which consume cancellation and clear the task's cancellation counter.
    real_timeout = asyncio.timeout
    monkeypatch.setattr("gateway.engine.asyncio.timeout", lambda delay: real_timeout(0.01))
    gateway, original, upstream, _ = gateway_parts()

    async def finish_after_deadline():
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            asyncio.current_task().uncancel()

    async def inspector(*args):
        call = len(original.calls) + 1
        if (phase == "initial" and call == 1) or (phase == "recheck" and call == 2):
            await finish_after_deadline()
        return await original(*args)

    async def late_upstream(body):
        upstream.received.append(deepcopy(body))
        await finish_after_deadline()
        return output("SYNTHETIC_EXPIRED_OUTPUT")

    gateway.inspector = inspector
    if phase == "upstream":
        gateway.upstream = late_upstream
    with pytest.raises(GatewayError) as failure:
        await gateway.handle({**REQUEST, "input": "SYNTHETIC_PERSON" if phase == "recheck" else "safe"})
    assert failure.value.code in {"INSPECTION_FAILED", "REINSPECTION_FAILED", "UPSTREAM_FAILED"}
    assert len(upstream.received) == (1 if phase == "upstream" else 0)


async def test_cancelled_reinspection_never_dispatches_or_repeats_masking():
    gateway, original, upstream, _ = gateway_parts()
    entered = asyncio.Event()

    async def inspector(*args):
        if original.calls:
            entered.set()
            await asyncio.Event().wait()
        return await original(*args)

    gateway.inspector = inspector
    task = asyncio.create_task(gateway.handle({**REQUEST, "input": "SYNTHETIC_PERSON"}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(original.calls) == 1
    assert upstream.received == []


@pytest.mark.parametrize("patch", [
    {"status": "incomplete", "findings": []}, {"findings": [None]},
    {"threshold_version": ""}, {"timing_ms": 10**1000},
])
async def test_malformed_detector_response_is_never_converted_to_clean(patch):
    def mutate(result, call, direction):
        result.update(patch)
    gateway, _, upstream, journal = gateway_parts(mutation=mutate)
    with pytest.raises(GatewayError) as failure:
        await gateway.handle(REQUEST)
    assert failure.value.code == "INSPECTION_INVALID"
    assert upstream.received == []
    assert journal.events[-1]["decision"] == "error"


@pytest.mark.parametrize("malformed", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "function_call", "arguments": "SYNTHETIC_RAW_TOOL"}]},
    {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [None]}]},
])
async def test_upstream_unknown_output_returns_safe_error_without_cli_content(malformed):
    gateway, _, upstream, journal = gateway_parts(result=malformed)
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers=AUTH, json={**REQUEST, "stream": True})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "UNSUPPORTED_OUTPUT"
    assert "SYNTHETIC_RAW_TOOL" not in result.text
    assert len(upstream.received) == 1
    assert journal.events[-1]["direction"] == "inbound"
    assert journal.events[-1]["decision"] == "error"


@pytest.mark.parametrize("headers", [{"Content-Type": "text/plain"},
    {"Content-Type": "application/json", "Content-Encoding": "gzip"}])
async def test_unrecognized_request_encoding_is_rejected_before_read_or_dispatch(headers):
    gateway, inspector, upstream, _ = gateway_parts()
    app = create_app(gateway, token="synthetic-local-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers={**AUTH, **headers}, content=b"SYNTHETIC_UNREAD_PAYLOAD")
    assert result.status_code == 415
    assert "SYNTHETIC_UNREAD_PAYLOAD" not in result.text
    assert inspector.calls == upstream.received == []


async def test_asgi_server_cancellation_propagates_to_pending_upstream_without_late_output():
    gateway, _, upstream, journal = gateway_parts(result=output("SYNTHETIC_LATE_OUTPUT"))
    upstream.release = asyncio.Event()
    app = create_app(gateway, token="synthetic-local-token")
    incoming, sent = asyncio.Queue(), []
    await incoming.put({"type": "http.request", "body": json.dumps(REQUEST).encode(), "more_body": False})

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app(asgi_scope(), incoming.get, send))
    await upstream.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sent == []
    assert journal.events[-1]["delivery_status"] == "unknown"


@pytest.mark.parametrize("invalid_capacity", [0, True, 1.0])
def test_gateway_rejects_ambiguous_capacity_configuration(invalid_capacity):
    with pytest.raises(ValueError):
        gateway_parts(max_inflight=invalid_capacity)


@pytest.mark.parametrize("token", [None, "", 123])
def test_gateway_cannot_start_with_empty_or_non_string_local_authentication(token):
    gateway, _, _, _ = gateway_parts()
    with pytest.raises(ValueError):
        create_app(gateway, token=token)
