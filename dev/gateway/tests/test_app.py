"""R02/R05/R06/R10 at the ASGI wire boundary, including no early bytes."""
import asyncio
import json

import httpx
import pytest

from gateway.app import create_app
from gateway.engine import Gateway
from gateway.journal import Journal
from test_engine import Inspector, Upstream


@pytest.fixture
def setup_app(tmp_path):
    upstream, inspector = Upstream(), Inspector()
    with Journal(tmp_path / "state" / "events.sqlite3") as journal:
        gateway = Gateway(inspector, upstream, journal, allowed_models={"mock-model"})
        yield create_app(gateway, token="synthetic-local-token"), upstream, journal


async def test_wire_masks_both_directions_and_never_echoes_raw_headers(setup_app):
    app, upstream, journal = setup_app
    upstream.text = "answer@example.invalid"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers={"Authorization": "Bearer synthetic-local-token"},
            json={"model": "mock-model", "input": "user@example.invalid", "stream": True})
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    assert "@example.invalid" not in result.text
    assert "[REDACTED]" in result.text and "response.completed" in result.text
    assert "@example.invalid" not in json.dumps(upstream.received)
    assert journal.pending()[-1]["delivery_status"] == "delivered"


async def test_safe_error_summary_contains_reason_for_cli_clients(setup_app):
    app, upstream, _ = setup_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers={"Authorization": "Bearer synthetic-local-token"},
            json={"model": "mock-model", "input": "900101-1234567"})
    assert result.status_code == 403 and upstream.received == []
    assert "POLICY_BLOCK" in result.json()["error"]["message"]
    assert "900101-1234567" not in result.text


@pytest.mark.parametrize("headers,code", [
    ({}, 401), ({"Authorization": "Bearer wrong"}, 401),
    ({"Authorization": "Bearer synthetic-local-token", "Origin": "https://evil.invalid"}, 403),
    ({"Authorization": "Bearer synthetic-local-token", "Host": "evil.invalid"}, 403),
])
async def test_unauthorized_origins_and_hosts_never_dispatch(setup_app, headers, code):
    app, upstream, _ = setup_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        result = await client.post("/v1/responses", headers=headers, json={"model": "mock-model", "input": "hello"})
    assert result.status_code == code and not upstream.received


async def test_json_size_duplicates_and_nan_fail_closed(setup_app):
    app, upstream, _ = setup_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer synthetic-local-token", "Content-Type": "application/json"}) as client:
        for content in [b'{"model":"mock-model","input":"secret","input":"safe"}',
                        b'{"model":"mock-model","input":"safe","max_output_tokens":NaN}', b'x' * (1024 * 1024 + 1)]:
            result = await client.post("/v1/responses", content=content)
            assert result.status_code in (400, 413)
            assert "secret" not in result.text
    assert not upstream.received


async def test_no_cli_response_start_until_full_upstream_and_inspection(setup_app):
    app, upstream, _ = setup_app
    upstream.release = asyncio.Event()
    sent, incoming = [], asyncio.Queue()
    body = json.dumps({"model": "mock-model", "input": "hello", "stream": True}).encode()
    await incoming.put({"type": "http.request", "body": body, "more_body": False})
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/v1/responses", "query_string": b"", "root_path": "",
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 1234),
        "headers": [(b"host", b"127.0.0.1"), (b"authorization", b"Bearer synthetic-local-token"), (b"content-type", b"application/json")]}
    async def send(message):
        sent.append(message)
    task = asyncio.create_task(app(scope, incoming.get, send))
    await upstream.entered.wait()
    await asyncio.sleep(0)
    assert sent == []
    upstream.release.set()
    await asyncio.wait_for(task, 2)
    assert sent[0]["type"] == "http.response.start"
    assert b"response.completed" in b"".join(m.get("body", b"") for m in sent)
