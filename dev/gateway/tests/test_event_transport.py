"""R08 HTTP transport; this synthetic codec is NOT an adopted C protocol."""
import asyncio
import json
import ssl
import time

import httpx
import pytest

from gateway.delivery import BatchReceipt, LocalEventShipper
from gateway.event_transport import EventHTTPTransport, EventTransportError
from gateway.journal import Journal
from gateway.tests.test_journal import event


class SyntheticCodec:
    def encode(self, events):
        return {"synthetic_events": events}
    def decode(self, body):
        return BatchReceipt(body["synthetic_ack"])


def sender(handler, **options):
    return EventHTTPTransport("https://central.example/v1/events/batch", "private-token", codec=SyntheticCodec(),
                              _test_transport=httpx.MockTransport(handler), **options)


async def test_partial_ack_authenticated_copy_and_shipper_ownership(tmp_path):
    received = []
    def handler(request):
        received.append(request)
        return httpx.Response(200, json={"synthetic_ack": ["event-1"]})
    async with sender(handler) as transport:
        with Journal(tmp_path / "events.sqlite3") as journal:
            journal.append(event("event-1"))
            journal.append(event("event-2"))
            report = await LocalEventShipper(journal, transport.send_batch).run_once()
            assert report.acknowledged == 1 and journal.pending()[0]["event_id"] == "event-2"
    assert received[0].headers["Authorization"] == "Bearer private-token"
    assert json.loads(received[0].content)["synthetic_events"] == [event("event-1"), event("event-2")]


@pytest.mark.parametrize("ack", [["unknown"], ["event-1", "event-1"], [True], "event-1", None, [[]]])
async def test_invalid_ack_is_rejected(ack):
    async with sender(lambda request: httpx.Response(200, json={"synthetic_ack": ack})) as transport:
        with pytest.raises(EventTransportError):
            await transport.send_batch([event()])


@pytest.mark.parametrize("batch", [[event()] * 101, [event(), event()], [event() | {"raw_text": "PRIVATE"}], []])
async def test_invalid_metadata_never_calls_network(batch):
    def handler(request):
        pytest.fail("invalid metadata reached network")
    async with sender(handler) as transport:
        with pytest.raises(EventTransportError) as error:
            await transport.send_batch(batch)
        assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("url", ["http://central.example/v1/events/batch", "https://user:secret@central.example/v1/events/batch",
                                 "https://central.example/v1/events/batch?x=1", "https://central.example/other"])
def test_destination_is_exact_and_secure(url):
    with pytest.raises(EventTransportError):
        EventHTTPTransport(url, "token", codec=SyntheticCodec())


def test_codec_is_required_and_token_not_in_repr():
    with pytest.raises(TypeError):
        EventHTTPTransport("https://central.example/v1/events/batch", "token")
    transport = sender(lambda request: httpx.Response(200, json={}))
    assert "private-token" not in repr(transport)


@pytest.mark.parametrize("status", [302, 307, 401, 429, 500])
async def test_http_failure_has_no_redirect_retry_or_raw_error(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.example"}, content="PRIVATE")
    async with sender(handler) as transport:
        with pytest.raises(EventTransportError) as error:
            await transport.send_batch([event()])
    assert len(calls) == 1 and calls[0].url.host == "central.example"
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("body, headers", [
    (b'{"synthetic_ack":[],"synthetic_ack":[]}', {}), (b'{"synthetic_ack":[],"x":NaN}', {}),
    (b'{"synthetic_ack":[],"x":1e999}', {}), (b'\xff', {}),
    (b' ' * (1024 * 1024 + 1), {}), (b'{}', {"Content-Length": "10"}),
    (b'{}', {"Content-Encoding": "gzip"}),
])
async def test_bad_response_json_or_framing_is_rejected(body, headers):
    async with sender(lambda request: httpx.Response(200, content=body, headers={"Content-Type": "application/json"} | headers)) as transport:
        with pytest.raises(EventTransportError):
            await transport.send_batch([event()])


async def test_oversized_codec_request_never_reaches_network():
    codec = SyntheticCodec()
    codec.encode = lambda events: {"envelope": "x" * (1024 * 1024)}
    async with EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=codec,
        _test_transport=httpx.MockTransport(lambda request: pytest.fail("oversized request sent"))) as transport:
        with pytest.raises(EventTransportError):
            await transport.send_batch([event()])


async def test_timeout_does_not_retry():
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(10)
    async with sender(handler, timeout_s=0.01) as transport:
        with pytest.raises(EventTransportError) as error:
            await transport.send_batch([event()])
    assert str(error.value) == "event_timeout" and len(calls) == 1


async def test_cancellation_propagates_without_receipt_or_retry():
    entered = asyncio.Event()
    calls = []
    async def handler(request):
        calls.append(request)
        entered.set()
        await asyncio.Event().wait()
    async with sender(handler) as transport:
        task = asyncio.create_task(transport.send_batch([event()]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(calls) == 1


async def test_synchronous_codec_cannot_accept_a_late_receipt():
    codec = SyntheticCodec()
    def decode(body):
        time.sleep(0.02)
        return BatchReceipt(["event-1"])
    codec.decode = decode
    async with EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=codec, timeout_s=0.01,
        _test_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))) as transport:
        with pytest.raises(EventTransportError) as error:
            await transport.send_batch([event()])
    assert str(error.value) == "event_timeout"


async def test_serialization_cannot_smuggle_raw_fields_to_codec():
    class ChangedDuringCopy(dict):
        def items(self):
            return list(super().items()) + [("raw_text", "PRIVATE")]
    codec = SyntheticCodec()
    codec.encode = lambda events: pytest.fail("unvalidated copied metadata reached codec")
    async with EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=codec,
        _test_transport=httpx.MockTransport(lambda request: pytest.fail("network reached"))) as transport:
        with pytest.raises(EventTransportError):
            await transport.send_batch([ChangedDuringCopy(event())])


def test_owned_client_verifies_tls_ignores_proxy_auth_and_redirect_defaults(monkeypatch):
    import gateway.event_transport as module
    calls = []
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: calls.append(kwargs))
    EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=SyntheticCodec())
    assert calls[0]["trust_env"] is False and calls[0]["follow_redirects"] is False
    assert calls[0]["verify"].verify_mode == ssl.CERT_REQUIRED and calls[0]["verify"].check_hostname
    assert calls[0]["timeout"] == 10.0


async def test_codec_mutation_cannot_change_callers_metadata_or_receipt_membership():
    original = event()
    codec = SyntheticCodec()
    def encode(events):
        events[0]["event_id"] = "changed"
        return {"ids": ["changed"]}
    codec.encode = encode
    async with EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=codec,
        _test_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"synthetic_ack": ["changed"]}))) as transport:
        with pytest.raises(EventTransportError):
            await transport.send_batch([original])
    assert original == event()


async def test_already_cancelling_task_never_encodes_or_sends():
    async with sender(lambda request: pytest.fail("cancelled task reached network")) as transport:
        async def cancelled_attempt():
            asyncio.current_task().cancel()
            await transport.send_batch([event()])
        task = asyncio.create_task(cancelled_attempt())
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize("kind", ["relative", "missing", "symlink", "malformed_certificate"])
def test_explicit_ca_failure_never_falls_back_to_insecure_tls(tmp_path, kind):
    certificate = tmp_path / "SYNTHETIC_PRIVATE_CA.pem"
    certificate.write_text("not a valid certificate")
    if kind == "relative":
        path = "relative-ca.pem"
    elif kind == "missing":
        path = tmp_path / "absent.pem"
    elif kind == "symlink":
        path = tmp_path / "linked.pem"
        path.symlink_to(certificate)
    else:
        path = certificate
    with pytest.raises(EventTransportError) as error:
        EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=SyntheticCodec(), ca_file=path)
    assert str(error.value) == ("event_configuration_failed" if kind == "malformed_certificate" else "event_invalid_ca")
    assert "SYNTHETIC_PRIVATE" not in str(error.value)


async def test_explicit_ca_is_passed_to_verified_context_without_fallback(tmp_path, monkeypatch):
    """Mock the SSL loader only: checks routing, not certificate validity or TLS."""
    import gateway.event_transport as module
    certificate = tmp_path / "fixture-ca.pem"
    certificate.write_text("synthetic CA loader input")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loaded = []
    def create_context(**kwargs):
        loaded.append(kwargs)
        return context
    monkeypatch.setattr(module.ssl, "create_default_context", create_context)
    async with EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=SyntheticCodec(),
        ca_file=certificate, _test_transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"synthetic_ack": []}))):
        assert loaded == [{"cafile": str(certificate)}]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


async def test_owned_http_close_failure_is_content_free():
    class FailingClose(httpx.MockTransport):
        async def aclose(self):
            raise RuntimeError("SYNTHETIC_PRIVATE_CLOSE")
    transport = EventHTTPTransport("https://central.example/v1/events/batch", "token", codec=SyntheticCodec(),
                                   _test_transport=FailingClose(lambda request: httpx.Response(200)))
    with pytest.raises(EventTransportError) as error:
        await transport.aclose()
    assert str(error.value) == "event_close_failed" and "SYNTHETIC_PRIVATE" not in str(error.value)
    assert transport._client.is_closed


@pytest.mark.parametrize("oversized", [False, True])
async def test_stream_without_content_length_stays_bounded_and_closes(oversized):
    class Stream(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            if oversized:
                yield b" " * (1024 * 1024)
                yield b" "
            else:
                yield b'{"synthetic_ack":'
                yield b'["event-1"]}'
        async def aclose(self):
            self.closed = True
    stream = Stream()
    async with sender(lambda request: httpx.Response(200, stream=stream, headers={"Content-Type": "application/json"})) as transport:
        if oversized:
            with pytest.raises(EventTransportError) as error:
                await transport.send_batch([event()])
            assert str(error.value) == "event_size_limit"
        else:
            assert await transport.send_batch([event()]) == BatchReceipt(("event-1",))
    assert stream.closed


@pytest.mark.parametrize("status", [200, 503])
async def test_response_cleanup_failure_cannot_replace_valid_receipt_or_safe_error(status):
    closes = []
    response = httpx.Response(status, json={"synthetic_ack": ["event-1"]})
    async def broken_close():
        closes.append(True)
        raise RuntimeError("SYNTHETIC_PRIVATE_RESPONSE_CLOSE")
    response.aclose = broken_close
    async with sender(lambda request: response) as transport:
        if status == 200:
            assert await transport.send_batch([event()]) == BatchReceipt(("event-1",))
        else:
            with pytest.raises(EventTransportError) as error:
                await transport.send_batch([event()])
            assert str(error.value) == "event_http_error" and "SYNTHETIC_PRIVATE" not in str(error.value)
    assert closes
