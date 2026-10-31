"""R03/R04: synthetic T03/T05 policy and Unicode span invariants."""

from copy import deepcopy

import pytest
from hypothesis import given, strategies as st

from gateway.policy import evaluate


def inspection(findings=()):
    return {
        "status": "complete", "findings": list(findings),
        "model_version": "synthetic-m0", "threshold_version": "test-v1",
        "timing_ms": 0.1,
    }


def finding(kind="PERSON", start=0, end=2, segment="s", detector="model"):
    return {"segment_id": segment, "type": kind, "start": start, "end": end,
            "score": 0.9, "detector": detector}


def test_block_beats_mask_and_never_returns_payload():
    decision = evaluate([{"segment_id": "s", "text": "가나1234567"}],
                        inspection([finding(), finding("KR_RRN", 2, 9)]))
    assert decision.action == "block"
    assert decision.segments == []
    assert "policy.block.KR_RRN" in decision.rule_ids


def test_unicode_overlap_merges_and_preserves_other_segments_without_mutation():
    segments = [{"segment_id": "s", "text": "😀김철수에게 연락"},
                {"segment_id": "other", "text": "정상"}]
    result = inspection([finding(start=1, end=4), finding("EMAIL_ADDRESS", 3, 6)])
    original = deepcopy((segments, result))
    decision = evaluate(segments, result)
    assert decision.action == "mask"
    assert decision.segments == [{"segment_id": "s", "text": "😀[REDACTED] 연락"},
                                 {"segment_id": "other", "text": "정상"}]
    assert (segments, result) == original


@pytest.mark.parametrize("kind", ["ORGANIZATION", "URL", "IP_ADDRESS", "NRP"])
def test_warning_only_types_allow_original_copy(kind):
    segments = [{"segment_id": "s", "text": "safe"}]
    decision = evaluate(segments, inspection([finding(kind)]))
    assert decision.action == "allow"
    assert decision.segments == segments
    assert decision.segments is not segments


@pytest.mark.parametrize("patch", [
    {"start": True}, {"end": False}, {"start": -1}, {"start": 2, "end": 2},
    {"end": 5}, {"start": 0.0}, {"score": True}, {"score": float("nan")},
    {"score": float("inf")}, {"score": -0.1}, {"score": 1.1},
    {"type": "UNKNOWN"}, {"segment_id": "unknown"}, {"detector": "unknown"},
    {"type": "API_KEY", "detector": "model"},
])
def test_invalid_finding_fails_closed(patch):
    item = finding()
    item.update(patch)
    decision = evaluate([{"segment_id": "s", "text": "safe"}], inspection([item]))
    assert decision.action == "error"
    assert decision.segments == []


@pytest.mark.parametrize("patch", [
    {"status": "incomplete"}, {"status": "error"}, {"findings": None},
    {"model_version": ""}, {"threshold_version": ""}, {"timing_ms": float("inf")},
    {"timing_ms": True}, {"timing_ms": -1},
])
def test_invalid_inspection_fails_closed(patch):
    result = inspection()
    result.update(patch)
    decision = evaluate([{"segment_id": "s", "text": "safe"}], result)
    assert decision.action == "error"
    assert decision.segments == []


@pytest.mark.parametrize("segments", [None, {}, [{"segment_id": "s", "text": 1}],
    [{"segment_id": "s", "text": "a"}, {"segment_id": "s", "text": "b"}]])
def test_invalid_segments_fail_closed(segments):
    decision = evaluate(segments, inspection())
    assert decision.action == "error"
    assert decision.segments == []


def test_validated_rule_key_blocks_and_malformed_blocks_take_error_precedence():
    assert evaluate([{"segment_id": "s", "text": "synthetic"}],
                    inspection([finding("API_KEY", detector="rule")])).action == "block"
    decision = evaluate([{"segment_id": "s", "text": "synthetic"}],
                        inspection([finding("KR_RRN"), finding(start=-1)]))
    assert decision.action == "error"


def test_masking_disjoint_spans_from_right_and_placeholder_stability():
    decision = evaluate([{"segment_id": "s", "text": "AB-😀-CD"}],
                        inspection([finding(start=0, end=2), finding(start=5, end=7)]))
    assert decision.segments[0]["text"] == "[REDACTED]-😀-[REDACTED]"
    assert evaluate(decision.segments, inspection()).action == "allow"


@pytest.mark.parametrize("segments,findings", [([None], []), (["synthetic"], []),
    ([{"segment_id": "s", "text": "synthetic"}], [None]),
    ([{"segment_id": "s", "text": "synthetic"}], ["synthetic malformed finding"])])
def test_malformed_nested_containers_are_rejected_without_payload(segments, findings):
    decision = evaluate(segments, inspection(findings))
    assert decision.action == "error"
    assert decision.segments == []
    assert decision.findings == []


def test_finding_content_extras_are_not_carried_into_metadata():
    item = finding()
    item["raw_text"] = "synthetic forbidden content"
    decision = evaluate([{"segment_id": "s", "text": "synthetic"}], inspection([item]))
    assert decision.action == "mask"
    assert "raw_text" not in decision.findings[0]
    assert "synthetic forbidden content" not in repr(decision)


@given(st.text(min_size=1, max_size=60), st.data())
def test_property_union_of_spans_is_replaced_exactly(text, data):
    spans = data.draw(st.lists(st.tuples(st.integers(0, len(text) - 1),
                                      st.integers(1, len(text))), min_size=1, max_size=12))
    spans = [(min(a, b - 1), max(a + 1, b)) for a, b in spans]
    covered = {index for start, end in spans for index in range(start, end)}
    expected = []
    for index, character in enumerate(text):
        if index not in covered:
            expected.append(character)
        elif index == 0 or index - 1 not in covered:
            expected.append("[REDACTED]")
    decision = evaluate([{"segment_id": "s", "text": text}],
                        inspection([finding(start=a, end=b) for a, b in spans]))
    assert decision.action == "mask"
    assert decision.segments[0]["text"] == "".join(expected)
