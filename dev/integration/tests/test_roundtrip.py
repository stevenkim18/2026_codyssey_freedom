"""T03/T04/T06/T08 — complete HTTP envelopes across three isolated services."""
import json
import re

from fastapi import FastAPI, Request
import httpx
import pytest

from detector.app import create_app as create_detector
from gateway.app import create_app as create_gateway
from gateway.engine import Gateway
from gateway.journal import Journal
from gateway.transport import InspectionClient, UpstreamClient


@pytest.mark.parametrize("prompt,answer,code", [
    ("안녕하세요", "안전한 답변", 200),
    ("user@example.invalid", "answer@example.invalid", 200),
    ("900101-1234567", "safe", 403),
    ("safe", "900101-1234567", 403),
])
async def test_actual_inspection_contract_and_upstream_body(tmp_path, prompt, answer, code):
    received = []
    async def backend(segments):
        findings = []
        for segment in segments:
            for kind, pattern in [("EMAIL_ADDRESS", r"[a-z]+@example\.invalid"), ("KR_RRN", "900101-1234567")]:
                for match in re.finditer(pattern, segment.text):
                    findings.append({"segment_id": segment.segment_id, "type": kind,
                        "start": match.start(), "end": match.end(), "score": 1.0, "detector": "model"})
        return findings
    detector = create_detector(backend=backend, token="synthetic-detector", model_version="synthetic-m0", threshold_version="v1")
    supplier = FastAPI()
    @supplier.post("/v1/responses")
    async def generate(request: Request):
        received.append(await request.json())
        assert request.headers["Authorization"] == "Bearer synthetic-provider"
        return {"status": "completed", "output": [{"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": answer}]}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(detector)) as detector_http, \
            httpx.AsyncClient(transport=httpx.ASGITransport(supplier)) as supplier_http:
        inspector = InspectionClient(detector_http, "http://127.0.0.1/v1/inspect", "synthetic-detector", allow_loopback_http=True)
        upstream = UpstreamClient(supplier_http, "http://127.0.0.1/v1/responses", "synthetic-provider",
                                  {"mock-model"}, allow_loopback_http=True)
        with Journal(tmp_path / "state" / "events.sqlite3") as journal:
            gateway = create_gateway(Gateway(inspector, upstream, journal, allowed_models={"mock-model"}), token="synthetic-local")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(gateway), base_url="http://127.0.0.1") as cli:
                result = await cli.post("/v1/responses", headers={"Authorization": "Bearer synthetic-local"},
                    json={"model": "mock-model", "input": prompt, "stream": True})
            assert result.status_code == code
            assert "@example.invalid" not in result.text and "900101-1234567" not in result.text
            if "900101" in prompt:
                assert received == []
            else:
                assert len(received) == 1
                assert "@example.invalid" not in json.dumps(received)
                assert received[0]["stream"] is False
            assert "@example.invalid" not in json.dumps(journal.pending())
