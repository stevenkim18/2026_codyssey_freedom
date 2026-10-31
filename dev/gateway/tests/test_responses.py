"""R02/R05/R06 — strict supported text surface and safe SSE reconstruction."""
import json

import pytest

from gateway.responses import ProtocolError, parse_request, parse_response, safe_events


def test_extract_all_text_and_replace_without_mutating_original():
    body = {"model": "mock-model", "instructions": "지시", "input": [
        {"role": "user", "content": [{"type": "input_text", "text": "😀 이메일"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "이력"}]}], "stream": True}
    parsed = parse_request(body, {"mock-model"})
    assert [s["text"] for s in parsed.segments] == ["지시", "😀 이메일", "이력"]
    replaced = [{**s, "text": "safe"} for s in parsed.segments]
    safe = parsed.replace(replaced)
    assert safe["input"][0]["content"][0]["text"] == "safe"
    assert body["instructions"] == "지시"


@pytest.mark.parametrize("extra", [
    {"previous_response_id": "resp_hidden"}, {"unknown_text": "secret"},
    {"metadata": {"secret": "raw"}}, {"tools": [{"type": "web_search"}]},
    {"store": True}, {"background": True}, {"model": "other"},
    {"input": [{"type": "function_call_output", "output": "raw"}]},
    {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "raw"}]}]},
    {"input": "x" * 16001}, {"stream": "true"},
])
def test_unsupported_state_content_and_limits_fail_closed(extra):
    with pytest.raises(ProtocolError):
        parse_request({"model": "mock-model", "input": "안녕", **extra}, {"mock-model"})


def test_response_output_only_text_and_strips_uninspected_fields():
    parsed = parse_response({"id": "resp_1", "object": "response", "status": "completed", "model": "mock-model",
        "output": [{"type": "message", "id": "msg_1", "role": "assistant", "content": [
            {"type": "output_text", "text": "sensitive", "annotations": []}]}], "metadata": {"x": "secret"}})
    result = parsed.replace([{**s, "text": "[REDACTED]"} for s in parsed.segments])
    assert "sensitive" not in json.dumps(result)
    assert "secret" not in json.dumps(result)
    assert "[REDACTED]" in json.dumps(result)
    events = b"".join(safe_events(result))
    assert b"sensitive" not in events
    assert b"response.output_text.delta" in events
    assert b"response.completed" in events


@pytest.mark.parametrize("output", [
    [{"type": "function_call", "arguments": "secret"}],
    [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "secret"}]}],
    [{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "secret"}]}],
])
def test_unknown_output_is_rejected(output):
    with pytest.raises(ProtocolError):
        parse_response({"id": "resp_1", "status": "completed", "output": output})


@pytest.mark.parametrize("extra", [
    {"input": None}, {"input": []}, {"input": [{}]}, {"input": [{"role": "tool", "content": "raw"}]},
    {"input": [{"role": "user", "content": []}]}, {"input": [{"role": "user", "content": None}]},
    {"input": [{"role": "assistant", "content": [{"type": "input_text", "text": "raw"}]}]},
    {"instructions": 123}, {"instructions": "\ud800"}, {"max_output_tokens": True},
    {"max_output_tokens": 0}, {"max_output_tokens": 32769}, {"parallel_tool_calls": True},
    {"tool_choice": "auto"}, {"reasoning": {"effort": "invalid"}}, {"reasoning": {"summary": "secret"}},
    {"text": {"verbosity": "secret"}}, {"text": {"format": {"type": "json_schema"}}},
])
def test_malformed_or_uninspected_request_surface_rejected(extra):
    with pytest.raises(ProtocolError):
        parse_request({"model": "mock-model", "input": "safe", **extra}, {"mock-model"})


def test_supported_scalar_controls_and_plain_history_are_retained():
    parsed = parse_request({"model": "mock-model", "input": [{"role": "user", "content": "history"}],
        "instructions": "full instructions", "reasoning": {"effort": "low"},
        "text": {"verbosity": "low"}, "max_output_tokens": 100, "tools": [],
        "tool_choice": "none", "stream": False}, {"mock-model"})
    assert [s["text"] for s in parsed.segments] == ["full instructions", "history"]
    assert parsed.replace(parsed.segments)["store"] is False


@pytest.mark.parametrize("replacement", [[], [{"segment_id": "wrong", "text": "safe"}], [{"segment_id": "s0", "text": None}]])
def test_replacement_cannot_lose_or_reorder_segments(replacement):
    with pytest.raises(ProtocolError):
        parse_request({"model": "mock-model", "input": "safe"}, {"mock-model"}).replace(replacement)


@pytest.mark.parametrize("value", [None, {}, {"status": "incomplete"}, {"status": "completed", "output": []},
    {"status": "completed", "output": [{}]},
    {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": []}]},
    {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": None}]}]},
])
def test_incomplete_or_malformed_output_never_becomes_empty_allow(value):
    with pytest.raises(ProtocolError):
        parse_response(value)
