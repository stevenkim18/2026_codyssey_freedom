"""R08 composed pipeline; fixture codec/storage are NOT the adopted C contract.

Actual local factory, ASGI authorization, journal, replay scheduler and event
transport are composed with synthetic detector/provider/central HTTP transports.
No network, model weights, actual LLM calls or C deployment are exercised.
"""
from collections import Counter
from dataclasses import asdict
import json

import httpx
import pytest

from gateway.delivery import BatchReceipt
from gateway.event_transport import EventHTTPTransport
from gateway.journal import Journal, JournalError, _FIELDS, _validate_event
from gateway.server import create_server
from gateway.tests.test_replay import ManualTimer
from gateway.tests.test_replay_lifecycle import clients, ready, request, settings


class FixtureOnlyCodec:
    """Explicit synthetic encoder/decoder; never a production C protocol default."""
    def encode(self, events):
        return {"fixture_events": events}

    def decode(self, body):
        return BatchReceipt(body["fixture_ack"])


PRIVATE_MARKERS = ("SYNTHETIC_SOURCE_TEXT", "SAFE_RESPONSE", "synthetic-local", "synthetic-detector",
                   "synthetic-upstream", "central-fixture-token", "PRIVATE_C_ERROR")


def check_metadata(events, reports=()):
    for event in events:
        assert set(event) == _FIELDS
        _validate_event(event)
        assert all(set(finding) == {"type", "count"} for finding in event["findings"])
    serialized = json.dumps({"events": events, "reports": [asdict(report) for report in reports]})
    assert all(marker not in serialized for marker in PRIVATE_MARKERS)


def traced_factory(clients, calls):
    factory, made = clients
    async def trace(outgoing):
        # Deliberately retain destination category only, never text/auth headers.
        calls.append(outgoing.url.host)
    def create(**options):
        client = factory(**options)
        client.event_hooks["request"].append(trace)
        return client
    return create, made


async def test_actual_event_http_pipeline_partial_ack_retry_and_restart(settings, clients):
    timer = ManualTimer()
    dependency_calls, wire_batches, reports = [], [], []
    durable_fixture_store = {}
    received_counts = Counter()
    applications, lifecycle = [], []
    factory, made = traced_factory(clients, dependency_calls)
    responses = iter(("ambiguous_503", "malformed", "partial", "all"))

    def central(outgoing):
        assert str(outgoing.url) == "https://central.example.invalid/v1/events/batch"
        assert outgoing.headers["Authorization"] == "Bearer central-fixture-token"
        assert outgoing.method == "POST"
        body = json.loads(outgoing.content)
        assert set(body) == {"fixture_events"}
        batch = body["fixture_events"]
        check_metadata(batch)
        wire_batches.append(batch)
        for item in batch:
            identity = item["event_id"]
            if identity in durable_fixture_store:
                assert durable_fixture_store[identity] == item
            durable_fixture_store[identity] = item
            received_counts[identity] += 1
        # Simulate an ambiguous reply after durable fixture insertion. Retrying
        # these IDs must not repeat an LLM request or create a new fixture row.
        mode = next(responses)
        if mode == "ambiguous_503":
            return httpx.Response(503, json={"private_error": "PRIVATE_C_ERROR"})
        if mode == "malformed":
            return httpx.Response(200, json={"fixture_ack": [batch[0]["event_id"], "not-a-sent-id"]})
        acknowledged = [batch[0]["event_id"]] if mode == "partial" else [item["event_id"] for item in batch]
        return httpx.Response(200, json={"fixture_ack": acknowledged})

    class CallerOwnedHTTP(httpx.MockTransport):
        async def aclose(self):
            assert all(not app.state.replay_worker.running for app in applications)
            lifecycle.append("central_closed_after_workers")
            await super().aclose()

    async with EventHTTPTransport("https://central.example.invalid/v1/events/batch", "central-fixture-token",
            codec=FixtureOnlyCodec(), _test_transport=CallerOwnedHTTP(central)) as sender:
        app = create_server(settings, client_factory=factory, send_events=sender.send_batch,
                            replay_options={"clock": timer.clock, "sleep": timer.sleep})
        applications.append(app)
        async with app.router.lifespan_context(app):
            wake = await timer.next()
            journal = app.state.replay_worker.shipper.journal
            assert (await request(app, authorized=False)).status_code == 401
            assert not dependency_calls and not wire_batches and journal.pending() == []

            assert (await request(app)).status_code == 200
            original = journal.pending()
            assert len(original) >= 5
            check_metadata(original)
            assert dependency_calls.count("api.openai.com") == 1

            timer.wake(wake)
            wake = await timer.next()
            reports.append(app.state.replay_worker.last_report)
            assert reports[-1].error_code == "CENTRAL_SEND_FAILED"
            assert journal.pending() == original and len(wire_batches) == 1
            assert (await ready(app)).status_code == 200

            # A second explicitly requested protected operation still succeeds
            # while central receipt is unavailable and local journaling works.
            assert (await request(app)).status_code == 200
            combined = journal.pending()
            assert len(combined) > len(original) and combined[:len(original)] == original
            assert dependency_calls.count("api.openai.com") == 2

            timer.wake(wake)
            wake = await timer.next()
            reports.append(app.state.replay_worker.last_report)
            assert reports[-1].error_code == "CENTRAL_SEND_FAILED"
            assert journal.pending() == combined and len(wire_batches) == 2

            timer.wake(wake)
            await timer.next()
            reports.append(app.state.replay_worker.last_report)
            assert reports[-1].acknowledged == 1
            remaining = journal.pending()
            assert remaining == combined[1:]
            assert wire_batches[1] == wire_batches[2] == combined
            assert (await ready(app)).status_code == 200
        assert not app.state.replay_worker.running and not sender._client.is_closed
        assert lifecycle == [] and all(client.is_closed for client in made)
        with pytest.raises(JournalError):
            journal.pending()
        with Journal(settings.state_dir / "events.sqlite3") as reopened:
            assert reopened.pending() == remaining

        # A newly composed server reopens the persisted journal. The external
        # transport survives because only its caller owns its lifespan.
        restarted = create_server(settings, client_factory=factory, send_events=sender.send_batch,
                                  replay_options={"clock": timer.clock, "sleep": timer.sleep})
        applications.append(restarted)
        async with restarted.router.lifespan_context(restarted):
            await timer.next()
            reports.append(restarted.state.replay_worker.last_report)
            assert restarted.state.replay_worker.shipper.journal.pending() == []
            assert reports[-1].acknowledged == len(remaining)
            assert wire_batches[3] == remaining
            assert (await ready(restarted)).status_code == 200
            assert dependency_calls.count("api.openai.com") == 2
        assert all(not instance.state.replay_worker.running for instance in applications)
        assert not sender._client.is_closed and lifecycle == []
    assert sender._client.is_closed and lifecycle == ["central_closed_after_workers"]
    assert all(client.is_closed for client in made)
    assert len(wire_batches) == 4 and len(durable_fixture_store) == len(combined)
    assert any(count > 1 for count in received_counts.values())
    assert set(durable_fixture_store) == {item["event_id"] for item in combined}
    check_metadata(list(durable_fixture_store.values()), reports)
    with Journal(settings.state_dir / "events.sqlite3") as journal:
        assert journal.pending() == []


async def test_central_auth_failure_retains_metadata_without_retrying_provider(settings, clients):
    timer, dependency_calls, batches = ManualTimer(), [], []
    factory, _ = traced_factory(clients, dependency_calls)
    def rejected(outgoing):
        batch = json.loads(outgoing.content)["fixture_events"]
        batches.append(batch)
        return httpx.Response(401, json={"private_error": "PRIVATE_C_ERROR"})
    async with EventHTTPTransport("https://central.example.invalid/v1/events/batch", "central-fixture-token",
            codec=FixtureOnlyCodec(), _test_transport=httpx.MockTransport(rejected)) as sender:
        app = create_server(settings, client_factory=factory, send_events=sender.send_batch,
                            replay_options={"clock": timer.clock, "sleep": timer.sleep})
        async with app.router.lifespan_context(app):
            wake = await timer.next()
            assert (await request(app)).status_code == 200
            original = app.state.replay_worker.shipper.journal.pending()
            for _ in range(2):
                timer.wake(wake)
                wake = await timer.next()
                assert app.state.replay_worker.shipper.journal.pending() == original
                assert app.state.replay_worker.last_report.acknowledged == 0
                assert app.state.replay_worker.last_report.error_code == "CENTRAL_SEND_FAILED"
            assert batches == [original, original]
            assert dependency_calls.count("api.openai.com") == 1
            assert dependency_calls.count("detector.example.invalid") == 2
            check_metadata(original, [app.state.replay_worker.last_report])
