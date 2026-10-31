import pytest
import os
import socket

from integration.verify_m0_tokenizer import check_coverage
from integration import verify_m0_tokenizer as probe


def test_complete_cannot_skip_nonwhite_unicode():
    with pytest.raises(AssertionError):
        check_coverage("complete", ["가 👩‍💻 e\u0301"], [[(0, 1)]], 1, 1)


def test_complete_allows_whitespace_gaps_and_preserves_codepoints():
    check_coverage("complete", ["가 e\u0301"], [[(0, 1), (2, 4)]], 2, 2)


def test_incomplete_must_have_zero_prediction_calls():
    with pytest.raises(AssertionError):
        check_coverage("incomplete", ["가"], [[]], 1, 1)
    check_coverage("incomplete", ["가"], [[]], 0, 1)


def test_planning_budget_is_an_independent_failure():
    with pytest.raises(AssertionError):
        check_coverage("incomplete", ["가"], [[]], 0, 2049)


@pytest.mark.parametrize("name, expected", [
    ("korean_particles", "complete"), ("emoji_zwj", "complete"),
    ("combining", "complete"), ("decomposed_hangul", "complete"),
    ("16000_codepoints", "complete"), ("multi_segment_16000", "complete"),
    ("word_boundaries", "complete"), ("long_single_word", "incomplete"),
    ("over_limit_16001", "rejected_input_limit"),
])
def test_known_cases_enforce_expected_status(name, expected):
    probe.check_expected_status(name, expected)
    for wrong in {"complete", "incomplete", "rejected_input_limit"} - {expected}:
        with pytest.raises(AssertionError, match="unexpected_probe_status"):
            probe.check_expected_status(name, wrong)


@pytest.mark.parametrize("record", [
    ("secret-invalid-status", ["text"], [[]], 0, 0),
    ("complete", ["text"], [[("secret", 1)]], 1, 1),
    ("incomplete", ["text"], [[]], 0, -1),
    ("incomplete", ["text"], [[]], False, 0),
])
def test_bad_records_fail_sanitized(record):
    with pytest.raises(AssertionError) as error:
        check_coverage(*record)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("fail", [True, False])
def test_run_restores_environment_and_sockets(monkeypatch, tmp_path, fail):
    original_environment = os.environ.copy()
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    def altered(*args):
        os.environ["HF_HOME"] = "changed"
        socket.create_connection = lambda *args: None
        if fail:
            raise RuntimeError("probe failed")
        return {"scope": "test"}
    monkeypatch.setattr(probe, "_run", altered, raising=False)
    if fail:
        with pytest.raises(RuntimeError, match="probe failed"):
            probe.run(tmp_path)
    else:
        assert probe.run(tmp_path) == {"scope": "test"}
    assert os.environ == original_environment
    assert (socket.socket.connect, socket.socket.connect_ex, socket.create_connection) == originals
