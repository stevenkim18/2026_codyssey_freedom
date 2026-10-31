"""Read-only view of the hook journal: what Codex stopped and what it saw too late.

A stopped transfer is an outbound decision whose original content never reached
the LLM. ``delivery_status`` alone cannot tell: a failed response check is also
recorded as ``not_sent`` although the response is already on screen. Responses
found after display are shown as ``late`` and never count as stopped. The journal
holds metadata only, so no original value can reach this view.
"""
import json
import math
import os
from pathlib import Path
import sqlite3
import threading

from gateway.journal import JournalError, _validate_event


RECENT_LIMIT = 200  # Newest rows sent to the page; totals still cover every row.
LATE = "late"
STOPPED_KINDS = ("block", "hold", "sanitized", "failure")
TYPE_LABELS = {
    "KR_RRN": "주민등록번호", "CREDIT_CARD": "카드번호", "API_KEY": "API 키·비밀번호", "PRIVATE_KEY": "개인 키",
    "PERSON": "이름", "EMAIL_ADDRESS": "이메일", "KR_PHONE_NUMBER": "전화번호", "KR_BANK_ACCOUNT": "계좌번호",
    "KR_LOCATION": "주소", "DATE_OF_BIRTH": "생년월일", "KR_DRIVER_LICENSE": "운전면허번호", "KR_PASSPORT": "여권번호",
    "KR_NHIS": "건강보험번호", "KR_VEHICLE_PLATE": "차량번호", "USERNAME": "사용자 이름",
    "PERSONAL_ATTRIBUTE": "개인 속성", "ORGANIZATION": "조직", "URL": "URL", "IP_ADDRESS": "IP 주소", "NRP": "국적·종교·정치 성향",
}
_STOPPED = {("block", "not_sent"): "block", ("mask", "not_sent"): "hold",
            ("mask", "forwarded_sanitized"): "sanitized", ("error", "not_sent"): "failure"}
_SOURCES = {"hook.user_prompt": "prompt", "hook.tool_output": "tool", "hook.response": "response"}
_RULE_MODEL = "local-rules-v1"  # What the hook records when no detector is configured.


def default_journal():
    home = os.environ.get("CODEX_HOME")
    base = Path(home) if home else Path.home() / ".codex"
    return base / "plugins/data/pii-gateway-codyssey-security/events.sqlite3"


def classify(event):
    """Return the event's kind, or None when the dashboard should not list it."""
    if event["stage"] != "decision":
        return None
    if event["direction"] == "inbound":
        return LATE if event["error_code"] == "POST_DISPLAY_FINDING" else None
    return _STOPPED.get((event["decision"], event["delivery_status"]))


def _source(rule_ids):
    # Records written before plugin 0.2.0-hook.3 name no path on inspection failures.
    return next((_SOURCES[rule] for rule in rule_ids if rule in _SOURCES), "unknown")


def _view(event, kind):
    findings = [{"type": item["type"], "label": TYPE_LABELS.get(item["type"], item["type"]), "count": item["count"]}
                for item in event["findings"]]
    return {"event_id": event["event_id"], "occurred_at": event["occurred_at"], "kind": kind,
            "source": _source(event["rule_ids"]), "findings": findings, "rule_ids": event["rule_ids"],
            "model_version": event["model_version"], "policy_version": event["policy_version"],
            "timing_ms": event["timing_ms"], "error_code": event["error_code"],
            "session_id": event["session_id"], "request_id": event["request_id"]}


def _direction():
    return {"inspected": 0, "allow": 0, "mask": 0, "block": 0, "error": 0, "samples": 0, "p50_ms": None, "p95_ms": None}


def _empty(state):
    return {"state": state, "total": 0, "by_kind": dict.fromkeys(STOPPED_KINDS + (LATE,), 0), "findings": [], "events": [],
            "skipped_rows": 0, "operations": {"last_inspected_at": None, "mode": None,
                                              "outbound": _direction(), "inbound": _direction()}}


def _read_rows(path):
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    try:
        return connection.execute("SELECT event_id,payload FROM events ORDER BY rowid").fetchall()
    finally:
        connection.close()


def _percentile(ordered, share):
    return ordered[math.ceil(share * len(ordered)) - 1]


def _record_operation(operations, timings, event):
    side = operations[event["direction"]]
    side["inspected"] += 1
    side[event["decision"]] += 1
    operations["last_inspected_at"] = event["occurred_at"]
    if event["decision"] != "error":  # A failed check never measured an inspection.
        timings[event["direction"]].append(event["timing_ms"])
    if event["model_version"] != "unknown":
        operations["mode"] = "rules" if event["model_version"] == _RULE_MODEL else "model"


def _build(rows):
    result = _empty("ok")
    operations = result["operations"]
    timings = {"outbound": [], "inbound": []}
    listed, totals = [], {}
    for event_id, payload in rows:
        try:
            event = json.loads(payload)
            _validate_event(event)
            if event["event_id"] != event_id:
                raise ValueError
        except (ValueError, TypeError, JournalError):
            result["skipped_rows"] += 1
            continue
        if event["stage"] == "decision":
            _record_operation(operations, timings, event)
        kind = classify(event)
        if kind is None:
            continue
        result["by_kind"][kind] += 1
        listed.append((event, kind))
        if kind != LATE:
            for item in event["findings"]:
                totals[item["type"]] = totals.get(item["type"], 0) + item["count"]
    for direction, values in timings.items():
        if values:
            ordered = sorted(values)
            operations[direction].update(samples=len(ordered), p50_ms=_percentile(ordered, 0.5), p95_ms=_percentile(ordered, 0.95))
    result["total"] = sum(result["by_kind"][kind] for kind in STOPPED_KINDS)
    result["events"] = [_view(event, kind) for event, kind in reversed(listed[-RECENT_LIMIT:])]
    ranked = sorted(totals.items(), key=lambda pair: (-pair[1], pair[0]))
    result["findings"] = [{"type": kind, "label": TYPE_LABELS.get(kind, kind), "count": count} for kind, count in ranked]
    return result


class Feed:
    """Rebuilds the snapshot only when the journal file changes."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stamp = None
        self._snapshot = None

    def snapshot(self):
        with self._lock:
            try:
                info = os.stat(self.path)
            except FileNotFoundError:
                return _empty("missing")
            except OSError:
                return _empty("unreadable")
            stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
            if stamp == self._stamp:
                return self._snapshot
            try:
                self._snapshot = _build(_read_rows(self.path))
            except sqlite3.Error:
                # A hook may hold the write lock; keep the last view and retry on the next poll.
                return {**(self._snapshot or _empty("ok")), "state": "unreadable"}
            self._stamp = stamp
            return self._snapshot
