"""Dashboard feed through the real hook wrapper, real journal and a loopback server.

All values are synthetic. No Codex home, provider or remote server is contacted.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import uuid

import pytest

from gateway.journal import Journal
from gateway.policy import ALLOW_TYPES, BLOCK_TYPES, MASK_TYPES
from observability.feed import RECENT_LIMIT, TYPE_LABELS, Feed, classify, default_journal
from observability.server import HOST, DashboardServer

DEV = Path(__file__).resolve().parents[2]
RUN = DEV / "launcher/plugin/hook_runtime/run.sh"
RRN = "900101-1234567"
EMAIL = "user@example.com"


def hook(data_directory, event, mode="guard", port="off"):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(data_directory / "home"), "PLUGIN_DATA": str(data_directory),
           "PII_HOOK_SETTINGS": str(data_directory / "no-settings.json"), "PII_DASHBOARD_PORT": str(port)}
    return subprocess.run(["/bin/sh", str(RUN), mode], input=json.dumps(event, ensure_ascii=False).encode(), env=env,
                          capture_output=True, timeout=30)


def prompt(text):
    return {"hook_event_name": "UserPromptSubmit", "session_id": "session-1", "turn_id": str(uuid.uuid4()), "prompt": text}


def tool(text):
    return {"hook_event_name": "PostToolUse", "session_id": "session-1", "turn_id": str(uuid.uuid4()),
            "tool_name": "Bash", "tool_response": text}


def stop(text):
    return {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": str(uuid.uuid4()),
            "stop_hook_active": False, "last_assistant_message": text}


def event(**overrides):
    base = {"schema_version": "1.0", "event_id": str(uuid.uuid4()), "client_id": "client-1", "session_id": "session-1",
            "request_id": "turn-1", "inspection_id": str(uuid.uuid4()), "revision": 1,
            "occurred_at": "2026-11-01T00:00:00+00:00", "direction": "outbound", "stage": "decision",
            "decision": "block", "delivery_status": "not_sent", "findings": [{"type": "KR_RRN", "count": 1}],
            "rule_ids": ["policy.block.KR_RRN", "hook.user_prompt"], "model_version": "local-rules-v1",
            "policy_version": "plugin-hook-v1", "timing_ms": 1.5, "error_code": None}
    return {**base, **overrides}


@pytest.mark.parametrize("fields, expected", [
    ({"decision": "block", "delivery_status": "not_sent"}, "block"),
    ({"decision": "mask", "delivery_status": "not_sent"}, "hold"),
    ({"decision": "mask", "delivery_status": "forwarded_sanitized"}, "sanitized"),
    ({"decision": "error", "delivery_status": "not_sent"}, "failure"),
    ({"decision": "allow", "delivery_status": "unknown"}, None),
    ({"decision": "allow", "delivery_status": "forwarded_original"}, None),
    ({"decision": "block", "delivery_status": "not_sent", "stage": "delivery"}, None),
    # The response was already displayed, so these are not pre-send stops.
    ({"decision": "error", "delivery_status": "not_sent", "direction": "inbound"}, None),
    ({"decision": "mask", "delivery_status": "delivered", "direction": "inbound"}, None),
    ({"decision": "block", "delivery_status": "delivered", "direction": "inbound"}, None),
    ({"decision": "mask", "delivery_status": "delivered", "direction": "inbound", "error_code": "POST_DISPLAY_FINDING"}, "late"),
    ({"decision": "block", "delivery_status": "delivered", "direction": "inbound", "error_code": "POST_DISPLAY_FINDING"}, "late"),
])
def test_only_outbound_stops_count_and_late_findings_stay_separate(fields, expected):
    assert classify(event(**fields)) == expected


def test_every_policy_type_has_a_label():
    assert ALLOW_TYPES | BLOCK_TYPES | MASK_TYPES == set(TYPE_LABELS)


def test_default_journal_follows_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert default_journal() == tmp_path / "plugins/data/pii-gateway-codyssey-security/events.sqlite3"


def test_real_hook_stops_reach_the_feed_in_order(tmp_path):
    feed = Feed(tmp_path / "events.sqlite3")
    assert feed.snapshot()["state"] == "missing"

    assert b'"block"' in hook(tmp_path, prompt(f"주민번호 {RRN}")).stdout
    assert b'"block"' in hook(tmp_path, prompt(f"메일은 {EMAIL} 입니다")).stdout
    assert b'"block"' in hook(tmp_path, tool(f"rrn {RRN}")).stdout
    assert b'"block"' in hook(tmp_path, tool(f"mail {EMAIL}")).stdout
    assert hook(tmp_path, prompt("안녕하세요")).stdout == b""
    hook(tmp_path, stop(f"메일은 {EMAIL} 입니다"), mode="observe")

    snapshot = feed.snapshot()
    assert snapshot["state"] == "ok"
    assert snapshot["total"] == 4
    assert snapshot["by_kind"] == {"block": 2, "hold": 1, "sanitized": 1, "failure": 0, "late": 1}
    assert [(row["kind"], row["source"]) for row in snapshot["events"]] == [
        ("late", "response"), ("sanitized", "tool"), ("block", "tool"), ("hold", "prompt"), ("block", "prompt")]
    assert {row["label"]: row["count"] for row in snapshot["findings"]} == {"주민등록번호": 2, "이메일": 2}
    text = json.dumps(snapshot, ensure_ascii=False)
    assert RRN not in text and EMAIL not in text


def test_real_hook_failure_names_its_path(tmp_path):
    settings = tmp_path / "bad-settings.json"
    settings.write_text("{", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "PLUGIN_DATA": str(tmp_path),
           "PII_HOOK_SETTINGS": str(settings), "PII_DASHBOARD_PORT": "off"}
    for event_input in (prompt("안녕하세요"), tool("안녕하세요")):
        payload = json.dumps(event_input, ensure_ascii=False).encode()
        completed = subprocess.run(["/bin/sh", str(RUN), "guard"], input=payload, env=env, capture_output=True, timeout=30)
        assert b'"block"' in completed.stdout

    rows = Feed(tmp_path / "events.sqlite3").snapshot()["events"]
    assert [(row["kind"], row["source"], row["findings"]) for row in rows] == [("failure", "tool", []), ("failure", "prompt", [])]
    assert rows[0]["error_code"] == "INVALID_SETTINGS"


def test_failed_response_check_counts_as_failure_but_not_as_a_stop(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event(direction="inbound", decision="error", findings=[], error_code="INVALID_SETTINGS",
                             rule_ids=["hook.inspection_failed", "hook.response"], model_version="unknown"))
    snapshot = Feed(tmp_path / "events.sqlite3").snapshot()
    assert (snapshot["total"], snapshot["events"]) == (0, [])
    assert snapshot["operations"]["inbound"]["error"] == 1


def test_records_without_a_path_stay_unknown(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event(decision="error", findings=[], rule_ids=["hook.inspection_failed"], model_version="unknown",
                             error_code="INVALID_SETTINGS"))
    assert Feed(tmp_path / "events.sqlite3").snapshot()["events"][0]["source"] == "unknown"


def test_operations_count_every_decision_by_direction(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        for timing in range(1, 21):
            journal.append(event(decision="allow", delivery_status="unknown", findings=[], timing_ms=float(timing),
                                 occurred_at="2026-11-01T00:00:01+00:00"))
        journal.append(event(timing_ms=21.0, occurred_at="2026-11-01T00:00:02+00:00"))
        journal.append(event(decision="error", findings=[], timing_ms=9999.0, model_version="unknown", error_code="TEXT_LIMIT",
                             occurred_at="2026-11-01T00:00:03+00:00"))
        journal.append(event(direction="inbound", decision="mask", delivery_status="delivered", timing_ms=7.0,
                             error_code="POST_DISPLAY_FINDING", occurred_at="2026-11-01T00:00:04+00:00"))
        journal.append(event(stage="delivery", timing_ms=8888.0, occurred_at="2026-11-01T00:00:05+00:00"))
    operations = Feed(tmp_path / "events.sqlite3").snapshot()["operations"]
    outbound, inbound = operations["outbound"], operations["inbound"]
    assert (outbound["inspected"], outbound["allow"], outbound["block"], outbound["error"]) == (22, 20, 1, 1)
    assert (outbound["samples"], outbound["p50_ms"], outbound["p95_ms"]) == (21, 11.0, 20.0)
    assert (inbound["inspected"], inbound["mask"], inbound["samples"], inbound["p95_ms"]) == (1, 1, 1, 7.0)
    assert operations["last_inspected_at"] == "2026-11-01T00:00:04+00:00"
    assert operations["mode"] == "rules"


def test_operations_report_model_mode_and_empty_state(tmp_path):
    assert Feed(tmp_path / "events.sqlite3").snapshot()["operations"]["mode"] is None
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event(model_version="m0-gliner"))
        journal.append(event(decision="error", findings=[], model_version="unknown", error_code="TEXT_LIMIT"))
    assert Feed(tmp_path / "events.sqlite3").snapshot()["operations"]["mode"] == "model"


def test_new_rows_appear_without_restart_and_journal_stays_untouched(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    feed = Feed(path)
    assert feed.snapshot()["total"] == 1

    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for _ in range(3):
        feed.snapshot()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == ["events.sqlite3"]

    with Journal(path) as journal:
        journal.append(event(occurred_at="2026-11-01T00:00:01+00:00"))
    snapshot = feed.snapshot()
    assert snapshot["total"] == 2
    assert snapshot["events"][0]["occurred_at"] == "2026-11-01T00:00:01+00:00"


def test_invalid_rows_are_counted_not_shown(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
        extra = event()
        journal._connection.execute("INSERT INTO events(event_id,payload) VALUES (?,?)",
                                    (extra["event_id"], json.dumps({**extra, "prompt": RRN})))
        journal._connection.execute("INSERT INTO events(event_id,payload) VALUES (?,?)", ("broken-row", "{"))
        journal._connection.commit()
    snapshot = Feed(path).snapshot()
    assert (snapshot["total"], snapshot["skipped_rows"]) == (1, 2)
    assert RRN not in json.dumps(snapshot)


def test_recent_list_is_bounded_but_totals_are_not(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        for _ in range(RECENT_LIMIT + 5):
            journal.append(event())
    snapshot = Feed(path).snapshot()
    assert snapshot["total"] == RECENT_LIMIT + 5
    assert len(snapshot["events"]) == RECENT_LIMIT
    assert snapshot["findings"][0]["count"] == RECENT_LIMIT + 5


def test_unreadable_journal_keeps_the_last_view(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    feed = Feed(path)
    assert feed.snapshot()["total"] == 1

    path.write_bytes(b"not a database" * 1000)
    snapshot = feed.snapshot()
    assert (snapshot["state"], snapshot["total"]) == ("unreadable", 1)


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    instance = DashboardServer(Feed(path), port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=5)


def fetch(instance, target, *, method="GET", host=None):
    port = instance.server_address[1]
    connection = http.client.HTTPConnection(HOST, port, timeout=5)
    connection.request(method, target, headers={"Host": host or f"127.0.0.1:{port}"})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def test_server_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


def test_feed_endpoint_returns_the_snapshot(server):
    response, body = fetch(server, "/api/feed")
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("application/json")
    assert response.getheader("Cache-Control") == "no-store"
    assert json.loads(body)["total"] == 1


def test_page_has_a_fresh_nonce_and_strict_policy(server):
    first, body = fetch(server, "/")
    second, _ = fetch(server, "/")
    policy = first.getheader("Content-Security-Policy")
    assert first.status == 200
    assert b"__NONCE__" not in body
    assert "default-src 'none'" in policy and "unsafe-inline" not in policy
    assert policy != second.getheader("Content-Security-Policy")


def test_page_survives_removal_of_its_source_folder(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<p>__NONCE__</p>", encoding="utf-8")
    monkeypatch.setattr("observability.server.PAGE", page)
    instance = DashboardServer(Feed(tmp_path / "events.sqlite3"), port=0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    page.unlink()
    try:
        assert fetch(instance, "/")[0].status == 200
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def test_page_never_builds_html_from_strings():
    page = (Path(__file__).resolve().parents[1] / "static/index.html").read_text(encoding="utf-8")
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page and "http://" not in page and "https://" not in page


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8787", "127.0.0.1"])
def test_rebound_host_is_rejected(server, host):
    response, body = fetch(server, "/api/feed", host=host)
    assert response.status == 403
    assert b"session" not in body


def test_unknown_path_and_writes_are_refused(server):
    assert fetch(server, "/events.sqlite3")[0].status == 404
    assert fetch(server, "/api/feed", method="POST")[0].status == 501


def test_page_sees_a_new_stop_within_one_poll(server, tmp_path):
    assert json.loads(fetch(server, "/api/feed")[1])["total"] == 1
    started = time.monotonic()
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
    assert json.loads(fetch(server, "/api/feed")[1])["total"] == 2
    assert time.monotonic() - started < 1


def test_module_runs_with_only_the_standard_library(tmp_path):
    source = Path(__file__).resolve().parents[2]
    code = ("import sys; sys.path.insert(0, sys.argv[1]); import observability.server, observability.feed; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('fastapi','starlette','pytest','httpx')))")
    result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-c", code, str(source)], capture_output=True, text=True,
                            timeout=30, env={**os.environ})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


SESSION = {"hook_event_name": "SessionStart", "session_id": "session-1"}


def message(result):
    return json.loads(result.stdout.decode("utf-8"))


def listeners(port):
    found = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    return {int(line) for line in found.stdout.split()}


def wait_for_feed(port, ready=lambda snapshot: True):
    deadline = time.monotonic() + 5
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/feed", timeout=1) as response:
                snapshot = json.load(response)
            if ready(snapshot):
                return snapshot
        except OSError:
            pass
        assert time.monotonic() < deadline, "dashboard did not answer"
        time.sleep(0.05)


def stop_dashboard(port):
    for pid in listeners(port):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 5
    while listeners(port):
        assert time.monotonic() < deadline
        time.sleep(0.05)


@pytest.fixture
def port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    yield free
    stop_dashboard(free)


@pytest.mark.parametrize("name", ["__init__.py", "__main__.py", "feed.py", "server.py", "static/index.html"])
def test_vendored_dashboard_is_the_observability_source(name):
    vendored = DEV / "launcher/plugin/hook_runtime/vendor/observability" / name
    assert vendored.read_bytes() == (DEV / "observability" / name).read_bytes()


def test_session_start_runs_the_plugin_copy_on_the_hook_journal(tmp_path, port):
    started = time.monotonic()
    result = hook(tmp_path, SESSION, mode="observe", port=port)
    assert time.monotonic() - started < 5  # An inherited pipe would hold this until the server exits.
    assert f"http://127.0.0.1:{port}" in message(result)["systemMessage"]
    assert wait_for_feed(port)["state"] == "missing"

    reason = message(hook(tmp_path, prompt(f"주민번호 {RRN}"), port=port))["reason"]
    assert reason.endswith(f"http://127.0.0.1:{port}")
    assert wait_for_feed(port, lambda snapshot: snapshot["total"] == 1)["by_kind"]["block"] == 1
    assert sorted(item.name for item in (DEV / "launcher/plugin/hook_runtime").glob("__pycache__")) == []


def test_mask_notice_keeps_the_rewritten_sentence_last(tmp_path, port):
    reason = message(hook(tmp_path, prompt(f"메일 {EMAIL} 보내줘"), port=port))["reason"]
    assert f"http://127.0.0.1:{port}" in reason
    assert reason.endswith("메일 [REDACTED] 보내줘")
    wait_for_feed(port)  # Teardown can only stop a server that has finished binding.


def test_a_running_dashboard_is_reused(tmp_path, port):
    hook(tmp_path, SESSION, mode="observe", port=port)
    wait_for_feed(port)
    running = listeners(port)
    hook(tmp_path, SESSION, mode="observe", port=port)
    hook(tmp_path, prompt(f"주민번호 {RRN}"), port=port)
    time.sleep(0.3)
    assert listeners(port) == running


def test_block_restarts_a_stopped_dashboard(tmp_path, port):
    hook(tmp_path, SESSION, mode="observe", port=port)
    wait_for_feed(port)
    stop_dashboard(port)
    hook(tmp_path, prompt(f"주민번호 {RRN}"), port=port)
    assert wait_for_feed(port, lambda snapshot: snapshot["total"] == 1)["total"] == 1


def test_removed_plugin_copy_stops_its_server(tmp_path, port):
    plugin = tmp_path / "plugin"
    subprocess.run(["cp", "-R", str(RUN.parent), str(plugin)], check=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "PLUGIN_DATA": str(tmp_path),
           "PII_HOOK_SETTINGS": str(tmp_path / "none.json"), "PII_DASHBOARD_PORT": str(port)}
    subprocess.run(["/bin/sh", str(plugin / "run.sh"), "observe"], input=json.dumps(SESSION).encode(), env=env,
                   capture_output=True, timeout=30)
    wait_for_feed(port)
    subprocess.run(["rm", "-rf", str(plugin)], check=True)
    deadline = time.monotonic() + 6
    while listeners(port):
        assert time.monotonic() < deadline, "server outlived its plugin copy"
        time.sleep(0.1)


def test_allowed_prompt_starts_nothing(tmp_path, port):
    assert hook(tmp_path, prompt("안녕하세요"), port=port).stdout == b""
    time.sleep(0.3)
    assert listeners(port) == set()


def test_tool_reason_reaches_the_llm_so_it_carries_no_address(tmp_path, port):
    assert "http" not in message(hook(tmp_path, tool(f"rrn {RRN}"), port=port))["reason"]
    assert "http" not in message(hook(tmp_path, tool(f"mail {EMAIL}"), port=port))["reason"]


@pytest.mark.parametrize("setting", ["off", "not-a-port", "99999", "-1"])
def test_unusable_port_setting_keeps_the_block(tmp_path, setting):
    decision = message(hook(tmp_path, prompt(f"주민번호 {RRN}"), port=setting))
    assert decision["decision"] == "block" and "http" not in decision["reason"]
    assert "http" not in message(hook(tmp_path, SESSION, mode="observe", port=setting))["systemMessage"]


def test_failed_start_keeps_the_block(tmp_path, port):
    broken = tmp_path / "plugin"
    subprocess.run(["cp", "-R", str(RUN.parent), str(broken)], check=True)
    (broken / "dashboard.py").unlink()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home"), "PLUGIN_DATA": str(tmp_path),
           "PII_HOOK_SETTINGS": str(tmp_path / "none.json"), "PII_DASHBOARD_PORT": str(port)}
    payload = json.dumps(prompt(f"주민번호 {RRN}"), ensure_ascii=False).encode()
    result = subprocess.run(["/bin/sh", str(broken / "run.sh"), "guard"], input=payload, env=env, capture_output=True, timeout=30)
    assert message(result)["decision"] == "block"
    time.sleep(0.3)
    assert listeners(port) == set()
