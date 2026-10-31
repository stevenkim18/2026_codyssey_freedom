"""T03/T04/T06/T08 — observe actual mock upstream payload and release timing."""
import asyncio
from copy import deepcopy
import json
import re

import pytest

from gateway.engine import Gateway, GatewayError
from gateway.journal import Journal, JournalError


def response(text):
    return {"status": "completed", "output": [{"type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": text}]}]}


class Inspector:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.residual = False

    async def __call__(self, segments, direction, request_id, inspection_id):
        self.calls.append(deepcopy((segments, direction)))
        if self.fail:
            raise RuntimeError("SYNTHETIC_RAW_ERROR")
        findings = []
        for s in segments:
            for kind, pattern in [("EMAIL_ADDRESS", r"[a-z]+@example\.invalid"), ("KR_RRN", r"900101-1234567")]:
                for match in re.finditer(pattern, s["text"]):
                    findings.append({"segment_id": s["segment_id"], "type": kind, "start": match.start(),
                        "end": match.end(), "score": 1.0, "detector": "model"})
            if self.residual and "[REDACTED]" in s["text"]:
                findings.append({"segment_id": s["segment_id"], "type": "PERSON", "start": 0, "end": 1,
                                 "score": 1.0, "detector": "model"})
        return {"schema_version": "1.0", "request_id": request_id, "inspection_id": inspection_id,
            "status": "complete", "findings": findings, "model_version": "synthetic-m0",
            "threshold_version": "synthetic-v1", "timing_ms": 1.0}


class Upstream:
    def __init__(self, text="안전한 답변"):
        self.received = []
        self.text = text
        self.entered = asyncio.Event()
        self.release = None

    async def __call__(self, body):
        self.received.append(deepcopy(body))
        self.entered.set()
        if self.release:
            await self.release.wait()
        return response(self.text)


@pytest.fixture
def components(tmp_path):
    inspector, upstream = Inspector(), Upstream()
    with Journal(tmp_path / "state" / "events.sqlite3") as journal:
        yield Gateway(inspector, upstream, journal, allowed_models={"mock-model"}), inspector, upstream, journal


async def test_request_block_makes_zero_upstream_calls(components):
    gateway, _, upstream, journal = components
    with pytest.raises(GatewayError) as exc:
        await gateway.handle({"model": "mock-model", "input": "주민 900101-1234567"})
    assert exc.value.code == "POLICY_BLOCK"
    assert upstream.received == []
    assert any(e["decision"] == "block" and e["delivery_status"] == "not_sent" for e in journal.pending())


async def test_both_directions_mask_and_reinspect_without_original_in_wire_or_journal(components):
    gateway, inspector, upstream, journal = components
    upstream.text = "😀 회신 answer@example.invalid 입니다"
    result = await gateway.handle({"model": "mock-model", "instructions": "admin@example.invalid",
                                  "input": "😀 user@example.invalid", "stream": True})
    sent = json.dumps(upstream.received, ensure_ascii=False)
    assert "user@example.invalid" not in sent
    assert "admin@example.invalid" not in sent
    assert "answer@example.invalid" not in json.dumps(result.body, ensure_ascii=False)
    assert "[REDACTED]" in json.dumps(result.body)
    assert [d for _, d in inspector.calls] == ["outbound", "outbound", "inbound", "inbound"]
    events = json.dumps(journal.pending())
    assert "@example.invalid" not in events and "😀" not in events
    assert not any(e["delivery_status"] == "delivered" for e in journal.pending())
    gateway.record_delivery(result, delivered=True)
    assert journal.pending()[-1]["delivery_status"] == "delivered"


@pytest.mark.parametrize("mode", ["inspector_failure", "residual", "journal_failure", "unsupported"])
async def test_failures_before_dispatch_never_call_upstream(components, mode, monkeypatch):
    gateway, inspector, upstream, journal = components
    body = {"model": "mock-model", "input": "user@example.invalid"}
    if mode == "inspector_failure":
        inspector.fail = True
    elif mode == "residual":
        inspector.residual = True
    elif mode == "journal_failure":
        def fail(_):
            raise JournalError("synthetic")
        monkeypatch.setattr(journal, "append", fail)
    else:
        body["previous_response_id"] = "resp_hidden"
    with pytest.raises(GatewayError) as exc:
        await gateway.handle(body)
    assert upstream.received == []
    assert "SYNTHETIC_RAW_ERROR" not in str(exc.value)


async def test_inbound_sensitive_block_never_returns_original(components):
    gateway, _, upstream, journal = components
    upstream.text = "900101-1234567"
    with pytest.raises(GatewayError) as exc:
        await gateway.handle({"model": "mock-model", "input": "hello"})
    assert exc.value.code == "POLICY_BLOCK"
    assert len(upstream.received) == 1
    assert journal.pending()[-1]["direction"] == "inbound"


async def test_cancellation_discards_late_result_and_records_unknown(components):
    gateway, _, upstream, journal = components
    upstream.release = asyncio.Event()
    task = asyncio.create_task(gateway.handle({"model": "mock-model", "input": "hello"}))
    await upstream.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(upstream.received) == 1
    assert journal.pending()[-1]["delivery_status"] == "unknown"


async def test_mismatched_inspection_identity_is_not_accepted(components):
    gateway, inspector, upstream, _ = components
    original = inspector.__call__
    async def wrong(*args):
        result = await original(*args)
        result["inspection_id"] = "other"
        return result
    gateway.inspector = wrong
    with pytest.raises(GatewayError):
        await gateway.handle({"model": "mock-model", "input": "hello"})
    assert not upstream.received
