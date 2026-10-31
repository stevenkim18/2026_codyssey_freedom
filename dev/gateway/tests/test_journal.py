"""R08/R10 T08/T10: durable metadata-only local event journal."""

import json
import sqlite3
import stat
import subprocess
import sys

import pytest

from gateway.journal import Journal, JournalError


def event(event_id="event-1"):
    return {"schema_version": "1.0", "event_id": event_id, "client_id": "client-1",
            "session_id": "session-1", "request_id": "request-1", "inspection_id": "inspection-1",
            "revision": 1, "occurred_at": "2026-10-31T00:00:00Z", "direction": "outbound",
            "stage": "decision", "decision": "mask", "delivery_status": "not_sent",
            "findings": [{"type": "PERSON", "count": 1}], "rule_ids": ["policy.mask.PERSON"],
            "model_version": "synthetic-m0", "policy_version": "v1", "timing_ms": 1.0,
            "error_code": None}


def test_committed_append_survives_restart_and_pending_returns_independent_values(tmp_path):
    path = tmp_path / "state" / "events.sqlite3"
    source = event()
    with Journal(path) as journal:
        journal.append(source)
        source["findings"][0]["count"] = 999
        pending = journal.pending()
        assert pending == [event()]
        pending[0]["rule_ids"].append("altered")
        assert journal.pending() == [event()]
    with Journal(path) as journal:
        assert journal.pending() == [event()]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_ack_only_removes_known_ids_and_duplicate_append_is_idempotent(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        journal.append(event())
        journal.append(event("event-2"))
        journal.acknowledge(["event-2", "unknown", "event-2"])
        assert journal.pending() == [event()]
        changed = event()
        changed["decision"] = "block"
        with pytest.raises(JournalError):
            journal.append(changed)
        assert journal.pending() == [event()]


@pytest.mark.parametrize("patch", [
    {"raw_text": "SYNTHETIC_FORBIDDEN_CONTENT"}, {"original_hash": "forbidden"},
    {"api_key": "synthetic"}, {"direction": "unknown"}, {"stage": "unknown"},
    {"decision": "unknown"}, {"delivery_status": "invalid"}, {"schema_version": "2.0"},
    {"revision": True}, {"revision": 0}, {"timing_ms": float("nan")},
    {"timing_ms": True}, {"timing_ms": 10**1000}, {"occurred_at": "2026-10-31T00:00:00"},
    {"occurred_at": None}, {"findings": None}, {"model_version": "raw arbitrary text"},
    {"event_id": "synthetic@example.invalid"}, {"error_code": "raw error contents"},
    {"findings": [{"type": "PERSON", "count": True}]},
    {"findings": [{"type": "PERSON", "count": 1, "text": "synthetic"}]},
    {"findings": [{"type": "UNKNOWN", "count": 1}]},
    {"rule_ids": ["raw text contents"]},
])
def test_invalid_or_raw_content_fields_fail_closed_without_echo(tmp_path, patch):
    value = event()
    value.update(patch)
    with Journal(tmp_path / "events.sqlite3") as journal:
        with pytest.raises(JournalError) as failure:
            journal.append(value)
        assert "SYNTHETIC_FORBIDDEN_CONTENT" not in str(failure.value)
        assert journal.pending() == []


def test_missing_required_field_is_rejected(tmp_path):
    value = event()
    del value["request_id"]
    with Journal(tmp_path / "events.sqlite3") as journal:
        with pytest.raises(JournalError):
            journal.append(value)


def test_physical_database_limit_rejects_new_events_without_losing_pending(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path, max_bytes=16384) as journal:
        accepted = []
        for index in range(100):
            value = event(f"event-{index}")
            try:
                journal.append(value)
            except JournalError:
                break
            accepted.append(value)
        else:
            pytest.fail("configured journal capacity was not enforced")
        assert accepted
        assert journal.pending() == accepted
        assert path.stat().st_size <= 16384
    with Journal(path, max_bytes=16384) as journal:
        assert journal.pending() == accepted


def test_uncommitted_tail_transaction_crash_preserves_all_committed_events(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    code = """
import json, os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('BEGIN IMMEDIATE')
connection.execute('INSERT INTO events(event_id, payload) VALUES (?, ?)', ('event-uncommitted', sys.argv[2]))
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(path), json.dumps(event("event-uncommitted"))],
                            check=False, capture_output=True)
    assert result.returncode == 9
    with Journal(path) as journal:
        assert journal.pending() == [event()]


def test_full_sync_is_enabled_and_failed_commit_preserves_existing_event(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        assert journal._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        journal.append(event())
        # A concurrent write lock simulates inability to commit another event.
        blocker = sqlite3.connect(journal.path)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(JournalError):
                journal.append(event("event-2"))
        finally:
            blocker.rollback()
            blocker.close()
        assert journal.pending() == [event()]


def test_corruption_is_never_silently_reset_and_symlinks_rejected(tmp_path):
    path = tmp_path / "events.sqlite3"
    path.write_bytes(b"synthetic corrupted database")
    with pytest.raises(JournalError):
        Journal(path)
    assert path.read_bytes() == b"synthetic corrupted database"
    target = tmp_path / "target"
    target.write_bytes(b"preserve")
    link = tmp_path / "linked.sqlite3"
    link.symlink_to(target)
    with pytest.raises(JournalError):
        Journal(link)
    assert target.read_bytes() == b"preserve"


def test_pending_detects_invalid_persisted_metadata(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload = ?", ('{"raw_text":"synthetic"}',))
    with Journal(path) as journal:
        with pytest.raises(JournalError):
            journal.pending()


@pytest.mark.parametrize("payload", ["not JSON", json.dumps(event("different-event-id"))])
def test_invalid_or_mismatched_stored_payload_is_not_delivered(tmp_path, payload):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        journal.append(event())
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE events SET payload = ?", (payload,))
    with Journal(path) as journal:
        with pytest.raises(JournalError):
            journal.pending()


def test_ack_failure_preserves_pending_and_invalid_ids_cannot_mutate_journal(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        journal.append(event())
        for invalid in (None, "event-1", ["event-1", "invalid raw text"]):
            with pytest.raises(JournalError):
                journal.acknowledge(invalid)
        blocker = sqlite3.connect(journal.path)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(JournalError):
                journal.acknowledge(["event-1"])
        finally:
            blocker.rollback()
            blocker.close()
        assert journal.pending() == [event()]


def test_acknowledged_pages_are_reused_and_unknown_delivery_state_is_valid(tmp_path):
    with Journal(tmp_path / "events.sqlite3", max_bytes=16384) as journal:
        accepted = []
        for index in range(100):
            value = event(f"event-{index}")
            value.update(stage="delivery", delivery_status="unknown", error_code="TRANSPORT_UNKNOWN")
            try:
                journal.append(value)
            except JournalError:
                break
            accepted.append(value)
        journal.acknowledge([value["event_id"] for value in accepted])
        assert journal.pending() == []
        for value in accepted:
            journal.append(value)
        assert journal.pending() == accepted


@pytest.mark.parametrize("limit", [0, 8192, True])
def test_capacity_below_schema_minimum_rejected(tmp_path, limit):
    with pytest.raises(JournalError):
        Journal(tmp_path / "events.sqlite3", max_bytes=limit)


def test_directory_symlink_and_empty_existing_file_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(JournalError):
        Journal(link / "events.sqlite3")
    empty = real / "empty.sqlite3"
    empty.touch()
    with pytest.raises(JournalError):
        Journal(empty)
    assert empty.read_bytes() == b""


def test_wrong_database_identity_and_undersized_restart_never_reset_events(tmp_path):
    path = tmp_path / "events.sqlite3"
    with Journal(path) as journal:
        for index in range(30):
            journal.append(event(f"event-{index}"))
    with pytest.raises(JournalError):
        Journal(path, max_bytes=12288)
    with Journal(path) as journal:
        assert len(journal.pending()) == 30
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id=0")
    original = path.read_bytes()
    with pytest.raises(JournalError):
        Journal(path)
    assert path.read_bytes() == original


def test_commit_remains_durable_after_process_exits_without_close(tmp_path):
    path = tmp_path / "events.sqlite3"
    code = """
import json, os, sys
from gateway.journal import Journal
journal = Journal(sys.argv[1])
journal.append(json.loads(sys.argv[2]))
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", code, str(path), json.dumps(event())],
                            check=False, capture_output=True)
    assert result.returncode == 9
    with Journal(path) as journal:
        assert journal.pending() == [event()]


def test_closed_journal_raises_content_free_error(tmp_path):
    journal = Journal(tmp_path / "events.sqlite3")
    journal.close()
    with pytest.raises(JournalError):
        journal.append(event())


def test_pending_limit_bounds_the_sql_read_and_preserves_default_api(tmp_path):
    with Journal(tmp_path / "events.sqlite3") as journal:
        for index in range(4):
            journal.append(event(f"event-{index}"))
        queries = []
        journal._connection.set_trace_callback(queries.append)
        assert journal.pending(limit=2) == [event("event-0"), event("event-1")]
        assert any("LIMIT 2" in query for query in queries)
        assert journal.pending(limit=1) == [event("event-0")]
        assert len(journal.pending()) == len(journal.pending(limit=None)) == 4


@pytest.mark.parametrize("limit", [0, -1, True, 1.0, "1", 1001])
def test_pending_invalid_limit_cannot_trigger_an_unbounded_read(tmp_path, limit):
    with Journal(tmp_path / "events.sqlite3") as journal:
        with pytest.raises(JournalError):
            journal.pending(limit=limit)
