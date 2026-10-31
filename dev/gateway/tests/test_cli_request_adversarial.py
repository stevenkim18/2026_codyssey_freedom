"""Independent R02/R04/R05/R06/T03/T11 CLI-envelope boundary attacks.

All markers are synthetic. Tests observe the actual Gateway/ASGI upstream
payload and client bytes; no CLI executable or external service is invoked.
"""
from copy import deepcopy
import json

import httpx
import pytest

from gateway.app import create_app
from gateway.cli_request import parse_cli_request
from gateway.engine import Gateway
from gateway.responses import ProtocolError
from gateway.tests.test_cli_request import cli_request


PRIVATE = "SYNTHETIC_PRIVATE_MARKER"
TELEMETRY = "SYNTHETIC_UNINSPECTED_TELEMETRY"
AUTH = {"Authorization": "Bearer synthetic-local"}


def schema(body):
    return body["input"][0]["tools"][0]["tools"][1]["parameters"]


def definitions(body):
    return body["input"][0]["tools"][0]["tools"]


class Inspector:
    def __init__(self, marker=PRIVATE):
        self.calls, self.marker = [], marker

    async def __call__(self, segments, direction, request_id, inspection_id):
        self.calls.append(deepcopy((direction, segments)))
        findings = []
        for segment in segments:
            start = 0
            while (start := segment["text"].find(self.marker, start)) >= 0:
                findings.append({"segment_id": segment["segment_id"], "type": "PERSON", "start": start,
                                 "end": start + len(self.marker), "score": 1.0, "detector": "model"})
                start += len(self.marker)
        return {"schema_version": "1.0", "request_id": request_id, "inspection_id": inspection_id,
                "status": "complete", "findings": findings, "model_version": "synthetic-m0",
                "threshold_version": "synthetic-threshold", "timing_ms": 0.1}


class Journal:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(deepcopy(event))


async def request(body, *, marker=PRIVATE, upstream_output=None):
    received, journal, inspector = [], Journal(), Inspector(marker)

    async def upstream(value):
        received.append(deepcopy(value))
        return deepcopy(upstream_output) if upstream_output is not None else {
            "status": "completed", "output": [{"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "synthetic safe response"}]}]}

    app = create_app(Gateway(inspector, upstream, journal, allowed_models={"mock-model"}), token="synthetic-local")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        response = await client.post("/v1/responses", headers=AUTH, json=body)
    return response, received, inspector, journal


@pytest.mark.parametrize("container", ["default", "examples"])
@pytest.mark.parametrize("colliding_key", ["description", "title", "definition"])
async def test_opaque_default_example_keys_cannot_enable_semantic_value_masking(container, colliding_key):
    body = cli_request()
    value = {"nested": [{colliding_key: PRIVATE}]}
    schema(body)[container] = value if container == "default" else [value]
    response, received, inspector, journal = await request(body)
    assert any(PRIVATE in item["text"] for _, segments in inspector.calls for item in segments)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UNSAFE_REPLACEMENT"
    assert received == []
    assert journal.events[-1]["error_code"] == "UNSAFE_REPLACEMENT"
    assert PRIVATE not in response.text + json.dumps(journal.events)


@pytest.mark.parametrize("property_name", ["default", "examples", "enum", "description", "$defs"])
async def test_property_names_that_match_schema_keywords_do_not_freeze_real_annotations(property_name):
    body = cli_request()
    schema(body)["properties"][property_name] = {"type": "string", "description": PRIVATE}
    response, received, inspector, _ = await request(body)
    assert response.status_code == 200
    assert received[0]["tools"][0]["tools"][1]["parameters"]["properties"][property_name]["description"] == "[REDACTED]"
    assert PRIVATE not in json.dumps(received)
    assert any(item["text"] == PRIVATE for _, segments in inspector.calls for item in segments)


@pytest.mark.parametrize("location", ["dynamic_key", "local_ref", "pattern", "format", "enum", "numeric_default", "grammar"])
async def test_sensitive_schema_identity_and_grammar_fail_before_external_dispatch(location):
    body = cli_request()
    marker = PRIVATE
    if location == "dynamic_key":
        schema(body)["properties"][PRIVATE] = {"type": "string"}
    elif location == "local_ref":
        schema(body)["$defs"] = {PRIVATE: {"type": "string"}}
        schema(body)["properties"]["cell_id"] = {"$ref": "#/$defs/" + PRIVATE}
    elif location in ("pattern", "format"):
        schema(body)["properties"]["cell_id"][location] = PRIVATE
    elif location == "enum":
        schema(body)["properties"]["cell_id"]["enum"] = [PRIVATE]
    elif location == "numeric_default":
        marker = "9801011234567"
        schema(body)["default"] = {"nested": [int(marker)]}
    else:
        definitions(body)[0]["format"]["definition"] = 'start: "' + PRIVATE + '"'
    response, received, inspector, journal = await request(body, marker=marker)
    assert any(marker in item["text"] for _, segments in inspector.calls for item in segments)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UNSAFE_REPLACEMENT"
    assert received == []
    assert marker not in response.text + json.dumps(journal.events)


def test_masked_grammar_cannot_be_replaced_with_invalid_lark_text():
    parsed = parse_cli_request(cli_request(), {"mock-model"})
    replacements = deepcopy(parsed.segments)
    grammar = definitions(cli_request())[0]["format"]["definition"]
    for segment in replacements:
        if segment["text"] == grammar:
            segment["text"] = '[REDACTED] "unterminated'
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacements)


async def test_descriptor_json_escape_masking_remains_text_and_preserves_other_message_bytes():
    body = cli_request()
    malicious_text = '😀\n"}], "tool_choice":"required", "input":[' + PRIVATE + "\\\n"
    definitions(body)[0]["description"] = malicious_text
    original_messages = deepcopy(body["input"][1:])
    response, received, inspector, _ = await request(body)
    assert response.status_code == 200
    sent = json.loads(json.dumps(received[0], ensure_ascii=False))
    assert sent["tool_choice"] == "none"
    assert sent["tools"][0]["tools"][0]["description"] == malicious_text.replace(PRIVATE, "[REDACTED]")
    assert [item["content"] for item in sent["input"]] == [item["content"] for item in original_messages]
    assert any(item["text"] == malicious_text for _, segments in inspector.calls for item in segments)
    assert PRIVATE not in json.dumps(sent)


async def test_telemetry_and_message_ids_are_discarded_even_when_they_contain_uninspected_markers():
    body = cli_request()
    body["client_metadata"] = {key: TELEMETRY for key in (
        "thread_id", "root_turn_id", "session_id", "turn_id", "x-codex-installation-id",
        "x-codex-turn-metadata", "x-codex-window-id")}
    for message in body["input"]:
        message["id"] = TELEMETRY
    response, received, inspector, journal = await request(body)
    assert response.status_code == 200
    assert TELEMETRY not in json.dumps(received) + response.text + json.dumps(journal.events)
    assert all(TELEMETRY not in item["text"] for _, segments in inspector.calls for item in segments)
    assert "client_metadata" not in received[0]
    assert all("id" not in message for message in received[0]["input"])


@pytest.mark.parametrize("field,value", [
    ("previous_response_id", "resp_SYNTHETIC_STORED_STATE"),
    ("conversation", {"id": "SYNTHETIC_STORED_STATE"}),
    ("context_management", [{"type": "compaction"}]),
    ("cached_state", "SYNTHETIC_STORED_STATE"),
    ("metadata", {"raw": PRIVATE}),
    ("instructions", PRIVATE),
])
async def test_cli_discriminator_cannot_enable_unknown_state_or_uninspected_root_strings(field, value):
    body = cli_request()
    body[field] = value
    response, received, inspector, _ = await request(body)
    assert response.status_code == 400
    assert received == inspector.calls == []
    assert PRIVATE not in response.text


@pytest.mark.parametrize("item", [
    {"type": "function_call", "name": "functions.wait", "arguments": PRIVATE},
    {"type": "custom_tool_call", "name": "functions.exec", "input": PRIVATE},
    {"type": "reasoning", "encrypted_content": PRIVATE},
])
async def test_allowed_definitions_do_not_enable_tool_or_encrypted_response_content(item):
    response, received, _, journal = await request(cli_request(), upstream_output={"status": "completed", "output": [item]})
    assert len(received) == 1 and received[0]["tool_choice"] == "none"
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UNSUPPORTED_OUTPUT"
    assert PRIVATE not in response.text + json.dumps(journal.events)


@pytest.mark.parametrize("bad_ref", ["https://synthetic.invalid/" + PRIVATE, "#/$defs/x/" + PRIVATE,
    "#/$dynamicRef/" + PRIVATE, "file:///" + PRIVATE])
def test_reference_targets_cannot_hide_remote_or_unbounded_schema_resolution(bad_ref):
    body = cli_request()
    schema(body)["$ref"] = bad_ref
    with pytest.raises(ProtocolError):
        parse_cli_request(body, {"mock-model"})


def test_nested_opaque_payload_depth_is_bounded_before_any_inspection():
    body = cli_request()
    current = nested = {}
    for _ in range(40):
        current["description"] = {}
        current = current["description"]
    current["text"] = PRIVATE
    schema(body)["default"] = nested
    with pytest.raises(ProtocolError, match="cli_shape_limit"):
        parse_cli_request(body, {"mock-model"})


def test_every_forwarded_tool_string_key_and_numeric_literal_has_an_original_inspection_segment():
    body = cli_request()
    schema(body)["default"] = {"😀opaque-key": [{"title": "opaque-title", "number": 9801011234567}]}
    schema(body)["examples"] = [{"format": "opaque-format", "definition": "opaque-definition"}]
    schema(body)["$defs"] = {"local/~key": {"type": "string", "description": "😀local annotation"}}
    schema(body)["properties"]["cell_id"] = {"$ref": "#/$defs/local~1~0key"}
    parsed = parse_cli_request(body, {"mock-model"})
    sent = parsed.replace(parsed.segments)
    original_segments = {item["text"] for item in parsed.segments}

    def assert_covered(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key in original_segments
                assert_covered(child)
        elif isinstance(value, list):
            for child in value:
                assert_covered(child)
        elif isinstance(value, str):
            assert value in original_segments
        elif type(value) in (int, float):
            assert json.dumps(value, allow_nan=False) in original_segments

    assert_covered(sent["tools"])


async def test_surrogate_telemetry_is_rejected_at_asgi_without_echoing_private_context():
    body = cli_request()
    body["client_metadata"]["thread_id"] = "\ud800" + PRIVATE
    received, inspector, journal = [], Inspector(), Journal()

    async def upstream(value):
        received.append(value)
        pytest.fail("invalid Unicode must not leave the gateway")

    app = create_app(Gateway(inspector, upstream, journal, allowed_models={"mock-model"}), token="synthetic-local")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as client:
        response = await client.post("/v1/responses", headers={**AUTH, "Content-Type": "application/json"},
                                     content=json.dumps(body, ensure_ascii=True).encode())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNSUPPORTED_PROTOCOL"
    assert received == inspector.calls == []
    assert PRIVATE not in response.text + json.dumps(journal.events)
