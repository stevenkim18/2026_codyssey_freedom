"""Verify public synthetic B DTOs against actual persistence/replay, offline.

This fixture/read-model check does not define a C HTTP codec or exercise C UI.
Only temporary journals are created. Input files are read-only; no network.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gateway.delivery import BatchReceipt, LocalEventShipper
from gateway.journal import Journal, JournalError, _FIELDS, _validate_event


DEFAULT_DATASET = Path(__file__).resolve().parents[2] / "docs" / "dashboard_example_dataset.log"


class FixtureError(Exception):
    """Content-free failure: input, exception text and paths are never echoed."""


def _require(condition):
    if not condition:
        raise FixtureError("FIXTURE_INVALID")


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _constant(_value):
    raise FixtureError("FIXTURE_INVALID")


def load_fixture(path=DEFAULT_DATASET):
    """Strict NDJSON reader; returns unchanged B DTOs, not C wire envelopes."""
    try:
        with Path(path).open("rb") as handle:
            content = handle.read(1024 * 1024 + 1)
        _require(0 < len(content) <= 1024 * 1024)
        lines = content.decode("utf-8").splitlines()
        _require(bool(lines) and all(line.strip() for line in lines))
        rows = [json.loads(line, object_pairs_hook=_object, parse_constant=_constant) for line in lines]
        for row in rows:
            _validate_event(row)
        return rows
    except Exception:
        raise FixtureError("FIXTURE_INVALID") from None


def _typed(value):
    """Equality including exact scalar/container types across JSON/SQLite."""
    if isinstance(value, dict):
        return (type(value), {key: _typed(item) for key, item in value.items()})
    if isinstance(value, list):
        return (type(value), [_typed(item) for item in value])
    return (type(value), value)


def summarize_events(events):
    """Fixture-only C display proposal; not a production aggregation contract.

    Counts use unique IDs, then one decision per request/direction. Ambiguous
    multiple decisions fail here rather than inventing an ordering contract.
    """
    unique = {}
    for row in events:
        identity = row["event_id"]
        _require(identity not in unique or _typed(unique[identity]) == _typed(row))
        unique[identity] = row
    decisions = [row for row in unique.values() if row["stage"] == "decision"]
    deliveries = [row for row in unique.values() if row["stage"] == "delivery"]
    cases = {(row["request_id"], row["direction"]) for row in decisions}
    _require(len(cases) == len(decisions))
    findings = Counter()
    for row in decisions:
        for item in row["findings"]:
            findings[item["type"]] += item["count"]
    unknown = {(row["request_id"], row["direction"]) for row in deliveries if row["delivery_status"] == "unknown"}
    confirmed = {(row["request_id"], row["direction"]) for row in deliveries if row["delivery_status"] != "unknown"}
    return {
        "rows": len(events), "unique_events": len(unique), "duplicate_rows": len(events)-len(unique),
        "requests": len({row["request_id"] for row in unique.values()}), "cases": len(cases),
        "decision_records": len(decisions), "delivery_records": len(deliveries),
        "decision_counts": dict(Counter(row["decision"] for row in decisions)),
        "outbound_counts": dict(Counter(row["decision"] for row in decisions if row["direction"] == "outbound")),
        "inbound_counts": dict(Counter(row["decision"] for row in decisions if row["direction"] == "inbound")),
        "findings_counts": dict(findings),
        "delivery_counts": dict(Counter(row["delivery_status"] for row in deliveries)),
        "unresolved_delivery_cases": len(unknown-confirmed),
    }


def _check_public_metadata(rows):
    """Lock the public sample to synthetic values; not a general PII detector."""
    for row in rows:
        _require(set(row) == _FIELDS and len(_FIELDS) == 18)
        for key, prefix in (("event_id", "1"), ("request_id", "2"), ("client_id", "3"),
                            ("session_id", "4"), ("inspection_id", "5")):
            UUID(row[key])
            _require(row[key].startswith(prefix + "0000000-0000-4000-8000-"))
        _require(row["client_id"] == "30000000-0000-4000-8000-000000000001")
        _require(row["session_id"] == "40000000-0000-4000-8000-000000000001")
        _require(row["revision"] == 1 and row["policy_version"] == "default-v1")
        _require(row["model_version"] in {"synthetic-m0-v1", "unknown"})
        _require(row["error_code"] in {None, "INSPECTION_FAILED", "INSPECTION_INVALID", "UNSUPPORTED_PROTOCOL"})
        _require(row["occurred_at"].startswith("2026-10-30T10:"))
        _require(datetime.fromisoformat(row["occurred_at"]).utcoffset().total_seconds() == 0)
        expected_rules = []
        for finding in row["findings"]:
            kind = finding["type"]
            action = "block" if kind in {"API_KEY", "CREDIT_CARD"} else "warn" if kind == "URL" else "mask"
            expected_rules.append(f"policy.{action}.{kind}")
        _require(row["rule_ids"] == expected_rules)


async def _check_replay(rows, directory):
    stored, modes, attempts = {}, iter(("lost", "partial", "all")), []

    async def synthetic_central(events):
        for event in events:
            identity = event["event_id"]
            _require(identity not in stored or _typed(stored[identity]) == _typed(event))
            stored[identity] = event
        mode = next(modes)
        if mode == "lost":
            raise RuntimeError  # Durable insertion followed by ambiguous receipt.
        selected = events[:2] if mode == "partial" else events
        return BatchReceipt([event["event_id"] for event in selected])

    with Journal(directory / "replay" / "events.sqlite3") as journal:
        for event in rows[:5]:
            journal.append(event)
        shipper = LocalEventShipper(journal, synthetic_central)
        for sent, acknowledged, pending in ((5, 0, 5), (5, 2, 3), (3, 3, 0)):
            result = await shipper.run_once()
            _require((result.sent, result.acknowledged, len(journal.pending())) == (sent, acknowledged, pending))
            _require(result.error_code == ("CENTRAL_SEND_FAILED" if not acknowledged else None))
            attempts.append({"sent": sent, "acknowledged": acknowledged, "pending": pending})
    _require(len(stored) == 5)
    return attempts, len(stored)


def verify_fixture(path=DEFAULT_DATASET):
    """Read public data, enforce documented counts, exercise actual B classes."""
    try:
        rows = load_fixture(path)
        _check_public_metadata(rows)
        report = summarize_events(rows)
        _require(report == {
            "rows": 38, "unique_events": 30, "duplicate_rows": 8, "requests": 10, "cases": 15,
            "decision_records": 15, "delivery_records": 15,
            "decision_counts": {"allow": 8, "mask": 2, "block": 2, "error": 3},
            "outbound_counts": {"allow": 5, "mask": 1, "block": 1, "error": 3},
            "inbound_counts": {"allow": 3, "mask": 1, "block": 1},
            "findings_counts": {"EMAIL_ADDRESS": 2, "PERSON": 1, "API_KEY": 1, "CREDIT_CARD": 1, "KR_PHONE_NUMBER": 1, "URL": 1},
            "delivery_counts": {"unknown": 7, "forwarded_original": 4, "forwarded_sanitized": 1, "delivered": 3},
            "unresolved_delivery_cases": 2,
        })
        _require(rows[30:35] == rows[:5] and rows[35:] == rows[2:5])
        _require(rows[27]["occurred_at"] > rows[28]["occurred_at"])
        with TemporaryDirectory(prefix="dashboard-fixture-") as temporary:
            directory = Path(temporary)
            with Journal(directory / "roundtrip" / "events.sqlite3") as journal:
                for row in rows:
                    journal.append(row)
                pending = journal.pending()
                unique = list({row["event_id"]: row for row in rows}.values())
                _require(_typed(pending) == _typed(unique))
                # Serialize the actual pending() output and reparse strictly.
                serialized = json.dumps(pending, ensure_ascii=False, allow_nan=False)
                _require(_typed(json.loads(serialized, object_pairs_hook=_object)) == _typed(unique))
                changed = dict(rows[0], timing_ms=9999)
                try:
                    journal.append(changed)
                except JournalError:
                    report["journal_conflict_rejected"] = True
                else:
                    raise FixtureError("FIXTURE_INVALID")
                _require(_typed(journal.pending()) == _typed(unique))
                report["journal_roundtrip"] = True
            report["replay_attempts"], report["synthetic_central_rows"] = asyncio.run(_check_replay(rows, directory))
        return {"status": "passed", **report}
    except Exception:
        raise FixtureError("FIXTURE_INVALID") from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def main(argv=None):
    parser = SafeParser(description="Offline synthetic B DTO/journal compatibility check; not a C API test.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    options = parser.parse_args(argv)
    try:
        print(json.dumps(verify_fixture(options.dataset), sort_keys=True))
        return 0
    except FixtureError:
        print(json.dumps({"status": "failed", "error_code": "FIXTURE_INVALID"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
