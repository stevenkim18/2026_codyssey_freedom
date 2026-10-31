"""B03/R08: actual gateway events with an explicitly injected synthetic codec."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json

import httpx
import pytest

from gateway import server
from gateway.delivery import BatchReceipt
from gateway.journal import Journal, JournalError
from gateway.settings import Settings
from gateway.tests.test_replay import ManualTimer


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"PII_GATEWAY_LOCAL_TOKEN":"synthetic-local","PII_DETECTOR_TOKEN":"synthetic-detector",
        "OPENAI_API_KEY":"synthetic-upstream","PII_DETECTOR_URL":"https://detector.example.invalid/v1/inspect",
        "PII_MODEL_ID":"mock-model","PII_STATE_DIR":str(tmp_path / "state")})


@pytest.fixture
def clients():
    made=[]
    def transport(request):
        body=json.loads(request.content)
        if request.url.host == "detector.example.invalid":
            return httpx.Response(200,json={"schema_version":"1.0","request_id":body["request_id"],"inspection_id":body["inspection_id"],
                "status":"complete","findings":[],"model_version":"synthetic-m0","threshold_version":"v1","timing_ms":1,
                "timing_breakdown_ms":{"queue":0,"inference":1,"rules":0,"total":1}})
        return httpx.Response(200,json={"status":"completed","output":[{"type":"message","role":"assistant",
            "content":[{"type":"output_text","text":"SAFE_RESPONSE"}]}]})
    def factory(**kwargs):
        assert kwargs == {"trust_env":False,"verify":True}
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport),**kwargs); made.append(client); return client
    return factory,made


async def request(app,authorized=True):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://127.0.0.1") as client:
        return await client.post("/v1/responses",headers={"Authorization":"Bearer synthetic-local" if authorized else "wrong"},
                                 json={"model":"mock-model","input":"SYNTHETIC_SOURCE_TEXT"})


async def ready(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url="http://127.0.0.1") as client:
        return await client.get("/readyz")


async def test_default_disabled_preserves_auth_and_repeated_lifespan_uses_fresh_journal(settings,clients):
    factory,made=clients; app=server.create_server(settings,client_factory=factory)
    for _ in range(2):
        async with app.router.lifespan_context(app):
            assert app.state.replay_worker is None
            assert (await request(app,False)).status_code == 401
            assert (await request(app)).status_code == 200
        assert all(client.is_closed for client in made)
    with Journal(settings.state_dir / "events.sqlite3") as journal:
        events=journal.pending()
        assert len({item["request_id"] for item in events}) == 2 and len(events) >= 10
    assert not app.router.routes


async def test_reentered_lifespan_never_routes_to_closed_previous_journal(settings,clients):
    app=server.create_server(settings,client_factory=clients[0])
    async with app.router.lifespan_context(app): assert (await request(app)).status_code == 200
    async with app.router.lifespan_context(app): assert (await request(app)).status_code == 200


async def test_actual_gateway_events_replay_partial_receipt_only_then_remaining(settings,clients):
    timer=ManualTimer(); received=[]
    async def sender(events): received.append(deepcopy(events)); return BatchReceipt([events[0]["event_id"]])
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async with app.router.lifespan_context(app):
        initial=await timer.next()
        response=await request(app); assert response.status_code == 200 and "SAFE_RESPONSE" in response.text
        journal=app.state.replay_worker.shipper.journal
        original=journal.pending(); assert len(original) >= 5
        timer.wake(initial); following=await timer.next()
        assert len(received) == 1 and len(journal.pending()) == len(original)-1
        assert received[0] == original and "SYNTHETIC_SOURCE_TEXT" not in json.dumps(received)
        assert {e["event_id"] for e in journal.pending()} == {e["event_id"] for e in original[1:]}
        assert (await ready(app)).status_code == 200
    assert not app.state.replay_worker.running
    with pytest.raises(JournalError): journal.pending()
    assert all(client.is_closed for client in clients[1])


async def test_outage_does_not_fail_readiness_and_restart_recovers_persisted_ids(settings,clients):
    timer=ManualTimer(); online=False; attempts=[]
    async def sender(events):
        attempts.append([e["event_id"] for e in events])
        if not online: raise ConnectionError("PRIVATE_C_ERROR")
        return BatchReceipt(attempts[-1])
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async with app.router.lifespan_context(app):
        initial=await timer.next(); assert (await request(app)).status_code == 200
        timer.wake(initial); await timer.next()
        assert (await ready(app)).status_code == 200
        assert app.state.replay_worker.last_report.error_code == "CENTRAL_SEND_FAILED"
    online=True
    async with app.router.lifespan_context(app):
        await timer.next()
        assert app.state.replay_worker.shipper.journal.pending() == []
        assert attempts[0] == attempts[1]
        assert (await request(app)).status_code == 200


async def test_cancel_resistant_sender_stays_bounded_across_server_lifespans(settings,clients):
    timer=ManualTimer(); entered=asyncio.Event(); cancelled=asyncio.Event(); release=asyncio.Event(); calls=[]
    async def sender(events):
        calls.append([e["event_id"] for e in events])
        if len(calls) == 1:
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: cancelled.set(); await release.wait()
        return BatchReceipt(calls[-1])
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async with app.router.lifespan_context(app):
        initial=await timer.next(); await request(app); timer.wake(initial); await entered.wait()
    await cancelled.wait()
    try:
        async with app.router.lifespan_context(app):
            draining=await timer.next()
            assert len(calls) == 1 and app.state.replay_worker.last_report.error_code == "SEND_DRAINING"
            assert {e["event_id"] for e in app.state.replay_worker.shipper.journal.pending()} == set(calls[0])
            release.set(); await asyncio.sleep(0); await asyncio.sleep(0)
            assert {e["event_id"] for e in app.state.replay_worker.shipper.journal.pending()} == set(calls[0])
            timer.wake(draining); await timer.next()
            assert len(calls) == 2 and not app.state.replay_worker.shipper.journal.pending()
    finally: release.set()


async def test_start_failure_after_task_creation_closes_resources_and_removes_mount(settings,clients,monkeypatch):
    timer=ManualTimer()
    async def sender(events): return BatchReceipt([])
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    worker=app.state.replay_worker; original=worker.start
    async def fail(): await original(); raise RuntimeError("synthetic startup failure")
    monkeypatch.setattr(worker,"start",fail)
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app): pytest.fail("startup must fail")
    assert not worker.running and not app.router.routes and all(client.is_closed for client in clients[1])
    with pytest.raises(JournalError): worker.shipper.journal.pending()


async def test_overlapping_lifespans_cannot_duplicate_worker_or_resources(settings,clients):
    app=server.create_server(settings,client_factory=clients[0])
    async with app.router.lifespan_context(app):
        with pytest.raises(RuntimeError):
            async with app.router.lifespan_context(app): pytest.fail("overlapping server owner")
        assert len(clients[1]) == 2 and (await request(app)).status_code == 200


@pytest.mark.parametrize("options",[{"send_events":"not-callable"},{"replay_options":{"min_delay_s":1}},
    {"send_events":lambda events:None,"replay_options":{"min_delay_s":0}},
    {"send_events":lambda events:None,"replay_options":{"send_events":"override"}}])
def test_bad_replay_configuration_fails_before_io(settings,clients,options):
    with pytest.raises((ValueError,TypeError)): server.create_server(settings,client_factory=clients[0],**options)
    assert not clients[1] and not settings.state_dir.exists()


async def test_worker_stops_before_owned_clients_and_journal_close(settings,clients,monkeypatch):
    timer=ManualTimer(); closed=[]; app=None
    class TrackingJournal(Journal):
        def close(self):
            assert app is not None and not app.state.replay_worker.running
            closed.append("journal")
            super().close()
    monkeypatch.setattr(server,"Journal",TrackingJournal)
    @asynccontextmanager
    async def factory(**kwargs):
        async with clients[0](**kwargs) as client:
            try: yield client
            finally:
                assert not app.state.replay_worker.running
                closed.append("client")
    async def sender(events): return BatchReceipt([])
    app=server.create_server(settings,client_factory=factory,send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async with app.router.lifespan_context(app):
        await timer.next(); assert app.state.replay_worker.running
    assert closed == ["client","client","journal"]


async def test_second_http_client_startup_failure_never_starts_worker_and_can_retry(settings,clients):
    timer=ManualTimer(); opens=0
    @asynccontextmanager
    async def factory(**kwargs):
        nonlocal opens
        opens+=1
        if opens == 2: raise RuntimeError("synthetic client unavailable")
        async with clients[0](**kwargs) as client: yield client
    async def sender(events): return BatchReceipt([])
    app=server.create_server(settings,client_factory=factory,send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    with pytest.raises(RuntimeError):
        async with app.router.lifespan_context(app): pytest.fail("startup failure")
    assert not app.state.replay_worker.running and not app.router.routes and all(client.is_closed for client in clients[1])
    assert timer.requests.empty()
    async with app.router.lifespan_context(app):
        await timer.next(); assert (await request(app)).status_code == 200


async def test_cancelled_lifespan_joins_worker_before_resources_close_and_remains_restartable(settings,clients):
    timer=ManualTimer(); active=asyncio.Event()
    async def sender(events): return BatchReceipt([])
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async def serve():
        async with app.router.lifespan_context(app):
            await timer.next(); active.set(); await asyncio.Event().wait()
    task=asyncio.create_task(serve()); await active.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not app.state.replay_worker.running and not app.router.routes and all(client.is_closed for client in clients[1])
    async with app.router.lifespan_context(app):
        await timer.next(); assert (await request(app)).status_code == 200


async def test_unadopted_dictionary_receipt_cannot_ack_actual_gateway_events(settings,clients):
    timer=ManualTimer(); sent=[]
    async def sender(events):
        sent.extend(e["event_id"] for e in events)
        return {"acknowledged_ids":sent[:]}
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options={"clock":timer.clock,"sleep":timer.sleep})
    async with app.router.lifespan_context(app):
        first=await timer.next(); await request(app); timer.wake(first); await timer.next()
        assert app.state.replay_worker.last_report.error_code == "INVALID_RECEIPT"
        assert {e["event_id"] for e in app.state.replay_worker.shipper.journal.pending()} == set(sent)
        assert (await ready(app)).status_code == 200


def test_replay_options_are_copied_and_invalid_container_rejected(settings,clients):
    async def sender(events): return BatchReceipt([])
    options={"min_delay_s":2}
    app=server.create_server(settings,client_factory=clients[0],send_events=sender,replay_options=options)
    options["min_delay_s"]=0
    assert app.state.replay_worker.min_delay_s == 2
    with pytest.raises(ValueError): server.create_server(settings,send_events=sender,replay_options=[])
