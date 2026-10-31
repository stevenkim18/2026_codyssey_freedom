"""Durable metadata-only local journal (R08/R10).

SQLite FULL synchronous commits replace JSONL partial-tail handling with atomic
transaction recovery. Acknowledged rows are deleted and their pages reused;
unacknowledged rows never expire or rotate away. ``max_bytes`` bounds the main
database, rounded down to whole 4096-byte pages. SQLite's temporary rollback file
requires additional disk space. Use a dedicated private state directory.
"""

from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading

from gateway.policy import ALLOW_TYPES, BLOCK_TYPES, MASK_TYPES


class JournalError(Exception):
    """Content-free failure: callers must stop forwarding the affected request."""


_FIELDS = frozenset({
    "schema_version", "event_id", "client_id", "session_id", "request_id", "inspection_id",
    "revision", "occurred_at", "direction", "stage", "decision", "delivery_status", "findings",
    "rule_ids", "model_version", "policy_version", "timing_ms", "error_code",
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_APPLICATION_ID = 0x5049494A
_PAGE_SIZE = 4096


def _token(value, pattern=_ID):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _validate_event(event):
    if not isinstance(event, dict) or set(event) != _FIELDS or event["schema_version"] != "1.0":
        raise JournalError("invalid event metadata")
    if any(not _token(event[key]) for key in ("event_id", "client_id", "session_id", "request_id", "inspection_id")):
        raise JournalError("invalid event metadata")
    if type(event["revision"]) is not int or not 1 <= event["revision"] <= 2147483647:
        raise JournalError("invalid event metadata")
    if (event["direction"] not in ("outbound", "inbound") or event["stage"] not in ("decision", "delivery")
            or event["decision"] not in ("allow", "mask", "block", "error")
            or event["delivery_status"] not in ("not_sent", "forwarded_sanitized", "forwarded_original", "delivered", "unknown")):
        raise JournalError("invalid event metadata")
    try:
        occurred_at = event["occurred_at"]
        if not isinstance(occurred_at, str) or len(occurred_at) > 40 or "T" not in occurred_at:
            raise ValueError
        timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if timestamp.utcoffset() != timedelta(0):
            raise ValueError
    except (ValueError, TypeError):
        raise JournalError("invalid event metadata") from None
    findings = event["findings"]
    if not isinstance(findings, list) or len(findings) > 64:
        raise JournalError("invalid event metadata")
    for finding in findings:
        if (not isinstance(finding, dict) or set(finding) != {"type", "count"}
                or not isinstance(finding["type"], str)
                or finding["type"] not in ALLOW_TYPES | BLOCK_TYPES | MASK_TYPES
                or type(finding["count"]) is not int or not 1 <= finding["count"] <= 1000000):
            raise JournalError("invalid event metadata")
    if not isinstance(event["rule_ids"], list) or len(event["rule_ids"]) > 64 or any(not _token(rule) for rule in event["rule_ids"]):
        raise JournalError("invalid event metadata")
    if any(not _token(event[key], _VERSION) for key in ("model_version", "policy_version")):
        raise JournalError("invalid event metadata")
    timing = event["timing_ms"]
    try:
        valid_timing = type(timing) in (int, float) and math.isfinite(timing) and timing >= 0
    except OverflowError:
        valid_timing = False
    if not valid_timing or (event["error_code"] is not None and not _token(event["error_code"], _CODE)):
        raise JournalError("invalid event metadata")


class Journal:
    """Synchronous append/ack operations; successful append is durably committed."""

    def __init__(self, path, max_bytes=100 * 1024 * 1024):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._connection = None
        if type(max_bytes) is not int or max_bytes < 3 * _PAGE_SIZE:
            raise JournalError("journal capacity too small")
        self.max_bytes = max_bytes
        try:
            parent = self.path.parent
            if parent.is_symlink():
                raise JournalError("unsafe journal path")
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            parent.chmod(0o700)
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                info = self.path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_nlink != 1:
                    raise JournalError("unsafe journal file")
                created = False
            else:
                os.close(descriptor)
                created = True
            self.path.chmod(0o600)
            connection = sqlite3.connect(self.path, timeout=0.1, check_same_thread=False)
            self._connection = connection
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA page_size=4096")
            connection.execute("PRAGMA secure_delete=ON")
            if created:
                with connection:
                    connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                    connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    connection.execute("PRAGMA user_version=1")
                directory_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"):
                raise JournalError("invalid journal database")
            if connection.execute("PRAGMA page_size").fetchone()[0] != _PAGE_SIZE:
                raise JournalError("invalid journal database")
            page_limit = max_bytes // _PAGE_SIZE
            if connection.execute("PRAGMA page_count").fetchone()[0] > page_limit:
                raise JournalError("journal exceeds capacity")
            connection.execute(f"PRAGMA max_page_count={page_limit}")
        except (OSError, sqlite3.Error, JournalError):
            self.close()
            raise JournalError("journal unavailable") from None

    def _require_open(self):
        if self._connection is None:
            raise JournalError("journal closed")
        return self._connection

    def append(self, event):
        _validate_event(event)
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock:
            connection = self._require_open()
            try:
                with connection:
                    previous = connection.execute("SELECT payload FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
                    if previous:
                        if previous[0] != payload:
                            raise JournalError("event identifier conflict")
                        return
                    connection.execute("INSERT INTO events(event_id,payload) VALUES (?,?)", (event["event_id"], payload))
            except sqlite3.Error:
                raise JournalError("journal write failed") from None

    def pending(self, *, limit=None):
        """Read committed events; optional SQL LIMIT bounds replay allocation."""
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
            raise JournalError("invalid pending limit")
        with self._lock:
            try:
                query = "SELECT event_id,payload FROM events ORDER BY rowid"
                if limit is not None:
                    query += " LIMIT ?"
                rows = self._require_open().execute(query, () if limit is None else (limit,)).fetchall()
                events = []
                for event_id, payload in rows:
                    event = json.loads(payload)
                    _validate_event(event)
                    if event["event_id"] != event_id:
                        raise JournalError("invalid stored event")
                    events.append(event)
                return events
            except (sqlite3.Error, ValueError, TypeError):
                raise JournalError("journal read failed") from None

    def acknowledge(self, event_ids):
        if not isinstance(event_ids, list) or any(not _token(event_id) for event_id in event_ids):
            raise JournalError("invalid acknowledgment")
        with self._lock:
            connection = self._require_open()
            try:
                with connection:
                    connection.executemany("DELETE FROM events WHERE event_id=?", [(event_id,) for event_id in event_ids])
            except sqlite3.Error:
                raise JournalError("journal acknowledgment failed") from None

    def close(self):
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, *_):
        self.close()
