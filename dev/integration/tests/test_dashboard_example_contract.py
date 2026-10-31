"""Public fixture compatibility with actual B persistence; no C wire implied."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from integration.verify_dashboard_example import (
    DEFAULT_DATASET, FixtureError, load_fixture, verify_fixture,
)


def test_public_fixture_uses_actual_journal_and_shipper_without_format_changes():
    report = verify_fixture()
    assert report["rows"] == 38
    assert report["unique_events"] == 30
    assert report["duplicate_rows"] == 8
    assert report["requests"] == 10
    assert report["cases"] == 15
    assert report["decision_counts"] == {"allow": 8, "mask": 2, "block": 2, "error": 3}
    assert report["findings_counts"] == {
        "EMAIL_ADDRESS": 2, "PERSON": 1, "API_KEY": 1,
        "CREDIT_CARD": 1, "KR_PHONE_NUMBER": 1, "URL": 1,
    }
    assert report["unresolved_delivery_cases"] == 2
    assert report["journal_roundtrip"] is True
    assert report["journal_conflict_rejected"] is True
    assert report["replay_attempts"] == [
        {"sent": 5, "acknowledged": 0, "pending": 5},
        {"sent": 5, "acknowledged": 2, "pending": 3},
        {"sent": 3, "acknowledged": 3, "pending": 0},
    ]
    assert report["synthetic_central_rows"] == 5


def test_raw_finding_text_cannot_enter_the_dashboard_fixture(tmp_path):
    rows = deepcopy(load_fixture(DEFAULT_DATASET))
    row = next(row for row in rows if row["findings"])
    row["findings"][0]["text"] = "PRIVATE_TEST_MARKER"
    path = tmp_path / "unsafe.ndjson"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(FixtureError, match="FIXTURE_INVALID") as error:
        verify_fixture(path)
    assert "PRIVATE_TEST_MARKER" not in str(error.value)


def test_same_event_id_with_changed_payload_is_a_conflict(tmp_path):
    rows = load_fixture(DEFAULT_DATASET)
    rows[-1] = dict(rows[-1], timing_ms=9999)
    path = tmp_path / "conflict.ndjson"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(FixtureError, match="FIXTURE_INVALID"):
        verify_fixture(path)


@pytest.mark.parametrize("content", ['{"event_id":1,"event_id":2}', '{"timing_ms":NaN}', '\n'])
def test_malformed_json_cannot_silently_change_the_read_model(tmp_path, content):
    path = tmp_path / "invalid.ndjson"
    path.write_text(content)
    with pytest.raises(FixtureError, match="FIXTURE_INVALID"):
        verify_fixture(path)


def test_fixture_drift_cannot_keep_the_documented_counts_green(tmp_path):
    rows = load_fixture(DEFAULT_DATASET)
    path = tmp_path / "truncated.ndjson"
    path.write_text("\n".join(json.dumps(row) for row in rows[:-1]))
    with pytest.raises(FixtureError, match="FIXTURE_INVALID"):
        verify_fixture(path)


def test_fresh_process_cli_uses_default_public_file_and_sanitizes_errors(tmp_path):
    script = Path(__file__).resolve().parents[1] / "verify_dashboard_example.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["journal_roundtrip"] is True and report["unique_events"] == 30
    missing = tmp_path / "PRIVATE_PATH_MARKER"
    failure = subprocess.run([sys.executable, str(script), "--dataset", str(missing)], capture_output=True, text=True)
    assert failure.returncode == 1
    assert json.loads(failure.stdout) == {"status": "failed", "error_code": "FIXTURE_INVALID"}
    assert "PRIVATE_PATH_MARKER" not in failure.stdout + failure.stderr


def test_invalid_cli_arguments_do_not_echo_sensitive_values():
    script = Path(__file__).resolve().parents[1] / "verify_dashboard_example.py"
    result = subprocess.run([sys.executable, str(script), "--PRIVATE_ARGUMENT_MARKER"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "PRIVATE_ARGUMENT_MARKER" not in result.stdout + result.stderr
