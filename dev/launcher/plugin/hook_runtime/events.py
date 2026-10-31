"""Metadata-only hook events in the same 18-field journal as the gateway."""
from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
import uuid

from gateway.journal import Journal, JournalError


POLICY_VERSION = "plugin-hook-v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_RETRIES = 5


def data_directory():
    configured = os.environ.get("PLUGIN_DATA")
    if configured:
        return Path(configured)
    # A manual --doctor has no PLUGIN_DATA; check the directory Codex gives the hooks.
    home = os.environ.get("CODEX_HOME")
    return (Path(home) if home else Path.home() / ".codex") / "plugins/data/pii-gateway-codyssey-security"


def _identifier(value):
    if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
        return value
    return str(uuid.uuid5(uuid.NAMESPACE_OID, str(value)))


def _client_id(directory):
    path = directory / "client_id"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return _identifier(path.read_text(encoding="ascii").strip())
    value = str(uuid.uuid4())
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(value)
    return value


def open_journal():
    return Journal(data_directory() / "events.sqlite3")


def record(data, *, direction, decision, delivery_status, findings, rule_ids, model_version, started, error_code=None):
    """Raise JournalError when durable recording fails; guards then stop the transfer."""
    counts = Counter(item["type"] for item in findings)
    for attempt in range(_RETRIES):
        try:
            with open_journal() as journal:
                journal.append({
                    "schema_version": "1.0", "event_id": str(uuid.uuid4()),
                    "client_id": _client_id(data_directory()),
                    "session_id": _identifier(data.get("session_id", "unknown-session")),
                    "request_id": _identifier(data.get("turn_id") or uuid.uuid4()),
                    "inspection_id": str(uuid.uuid4()), "revision": 1,
                    "occurred_at": datetime.now(timezone.utc).isoformat(), "direction": direction,
                    "stage": "decision", "decision": decision, "delivery_status": delivery_status,
                    "findings": [{"type": kind, "count": counts[kind]} for kind in sorted(counts)],
                    "rule_ids": rule_ids, "model_version": model_version, "policy_version": POLICY_VERSION,
                    "timing_ms": (time.monotonic() - started) * 1000, "error_code": error_code})
            return
        except (JournalError, OSError, ValueError):
            # Concurrent hooks can briefly hold the SQLite write lock.
            if attempt == _RETRIES - 1:
                raise JournalError("hook journal unavailable") from None
            time.sleep(0.05)
