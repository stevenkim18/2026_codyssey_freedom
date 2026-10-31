"""R02/R03/R04/T03/T05/T11: text-only execution with inspected CLI definitions."""

from copy import deepcopy
from uuid import uuid4

import pytest
from hypothesis import given, strategies as st

from gateway.cli_request import parse_cli_request
from gateway.responses import ParsedText, ProtocolError


def cli_request():
    return {"model": "mock-model", "stream": True, "store": False, "parallel_tool_calls": False,
        "tool_choice": "auto", "include": ["reasoning.encrypted_content"], "prompt_cache_key": str(uuid4()),
        "client_metadata": {"thread_id": str(uuid4()), "x-codex-turn-metadata": "SYNTHETIC_LOCAL_TELEMETRY"},
        "reasoning": {"effort": "low", "context": "all_turns"}, "text": {"verbosity": "low"},
        "input": [{"type": "additional_tools", "role": "developer", "id": "tools-synthetic", "tools": [
            {"type": "namespace", "name": "functions", "description": "", "tools": [
                {"type": "custom", "name": "exec", "description": "Synthetic exec description",
                 "format": {"type": "grammar", "syntax": "lark", "definition": 'start: "synthetic"'}},
                {"type": "function", "name": "wait", "description": "Synthetic wait description", "strict": False,
                 "parameters": {"type": "object", "properties": {
                     "cell_id": {"type": "string", "description": "Synthetic cell identifier"},
                     "max_tokens": {"type": "number"}, "terminate": {"type": "boolean"},
                     "yield_time_ms": {"type": "number"}}, "required": ["cell_id"], "additionalProperties": False}}
            ]}]},
            {"type": "message", "id": "message-synthetic", "role": "developer", "content": [
                {"type": "input_text", "text": "Synthetic reviewed instructions"}]},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": "Synthetic history", "annotations": []}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "😀 synthetic user"}]}]}


def test_cli_canonicalization_preserves_messages_and_moves_only_inspected_definitions():
    value = cli_request()
    original = deepcopy(value)
    parsed = parse_cli_request(value, {"mock-model"})
    assert isinstance(parsed, ParsedText)
    canonical = parsed.replace(parsed.segments)
    assert value == original
    assert canonical["tool_choice"] == "none"
    assert canonical["parallel_tool_calls"] is False and canonical["store"] is False
    assert canonical["reasoning"] == {"effort": "low"}
    assert canonical["tools"] == value["input"][0]["tools"]
    assert [item["role"] for item in canonical["input"]] == ["developer", "assistant", "user"]
    assert all("id" not in item for item in canonical["input"])
    assert not {"client_metadata", "prompt_cache_key", "include"} & canonical.keys()
    texts = [segment["text"] for segment in parsed.segments]
    assert "Synthetic reviewed instructions" in texts and "Synthetic history" in texts
    assert "😀 synthetic user" in texts
    assert "SYNTHETIC_LOCAL_TELEMETRY" not in texts
    assert 'start: "synthetic"' in texts


def test_description_schema_text_and_grammar_are_extracted_without_normalization_and_can_mask():
    value = cli_request()
    definition = value["input"][0]["tools"][0]["tools"][0]
    definition["description"] = "😀 person@example.invalid\nsecond line"
    parsed = parse_cli_request(value, {"mock-model"})
    replacements = deepcopy(parsed.segments)
    for segment in replacements:
        if segment["text"] == definition["description"]:
            segment["text"] = "😀 [REDACTED]\nsecond line"
    canonical = parsed.replace(replacements)
    assert canonical["tools"][0]["tools"][0]["description"] == "😀 [REDACTED]\nsecond line"


def test_dynamic_schema_keys_are_inspected_as_protected_original_unicode_text():
    value = cli_request()
    schema = value["input"][0]["tools"][0]["tools"][1]["parameters"]
    schema["properties"]["😀person@example.invalid"] = {"type": "string"}
    parsed = parse_cli_request(value, {"mock-model"})
    replacements = deepcopy(parsed.segments)
    changed = False
    for segment in replacements:
        if segment["text"] == "😀person@example.invalid":
            segment["text"] = "[REDACTED]"
            changed = True
    assert changed
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacements)


@pytest.mark.parametrize("protected", ["functions", "exec", "wait", "custom", "grammar", "lark", "object", "cell_id"])
def test_structural_identifiers_cannot_be_masked_into_a_different_tool_or_schema(protected):
    parsed = parse_cli_request(cli_request(), {"mock-model"})
    replacement = deepcopy(parsed.segments)
    changed = False
    for segment in replacement:
        if segment["text"] == protected:
            segment["text"] = "[REDACTED]"
            changed = True
    assert changed
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacement)


@pytest.mark.parametrize("patch", [
    {"previous_response_id": "synthetic"}, {"conversation": "synthetic"}, {"unknown": "synthetic"},
    {"store": True}, {"parallel_tool_calls": True}, {"tool_choice": "required"}, {"model": []},
    {"prompt_cache_key": "not-a-uuid"}, {"include": ["arbitrary.text"]},
    {"reasoning": {"effort": "low", "context": "stored_server_state"}},
    {"client_metadata": {"unknown_text": "synthetic"}}, {"client_metadata": {"thread_id": {"nested": "synthetic"}}},
    {"client_metadata": {"thread_id": "x" * 4097}}, {"max_output_tokens": True},
])
def test_unknown_or_stateful_envelope_fields_fail_closed(patch):
    with pytest.raises(ProtocolError):
        parse_cli_request(cli_request() | patch, {"mock-model"})


@pytest.mark.parametrize("item", [
    {"type": "function_call_output", "call_id": "synthetic", "output": "synthetic"},
    {"type": "custom_tool_call_output", "call_id": "synthetic", "output": "synthetic"},
    {"type": "reasoning", "encrypted_content": "synthetic"},
    {"type": "function_call", "name": "exec", "arguments": "synthetic"},
    {"type": "message", "role": "tool", "content": "synthetic"},
    {"type": "message", "id": "id\nSYNTHETIC", "role": "user", "content": "synthetic"},
    {"type": "message", "role": "user", "content": [{"type": "input_image", "image_url": "synthetic"}]},
])
def test_tool_results_encrypted_history_and_unsupported_contents_are_never_forwarded(item):
    value = cli_request()
    value["input"].append(item)
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("mutation", ["unknown_name", "unknown_field", "duplicate", "remote_ref", "invalid_syntax", "schema_unknown"])
def test_tool_definitions_use_a_small_explicit_structure_allowlist(mutation):
    value = cli_request()
    tools = value["input"][0]["tools"][0]["tools"]
    if mutation == "unknown_name":
        tools[0]["name"] = "dangerous"
    elif mutation == "unknown_field":
        tools[0]["arbitrary_text"] = "synthetic"
    elif mutation == "duplicate":
        tools.append(deepcopy(tools[0]))
    elif mutation == "remote_ref":
        tools[1]["parameters"]["$ref"] = "https://synthetic.invalid/schema"
    elif mutation == "invalid_syntax":
        tools[0]["format"]["syntax"] = "arbitrary-runtime"
    else:
        tools[1]["parameters"]["unrecognized"] = "synthetic"
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


def test_aggregate_limit_includes_tool_descriptions_and_virtual_key_text():
    value = cli_request()
    value["input"][0]["tools"][0]["tools"][0]["description"] = "x" * 16000
    with pytest.raises(ProtocolError, match="text_limit"):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("replacement", [None, [], [None], [{"segment_id": "wrong", "text": "synthetic"}]])
def test_replacement_shape_cannot_bypass_protected_span_validation(replacement):
    parsed = parse_cli_request(cli_request(), {"mock-model"})
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacement)


def test_minimal_cli_text_and_root_definitions_round_trip_without_optional_telemetry():
    value = cli_request()
    minimal = {"model": "mock-model", "input": [{"role": "user", "content": "😀 original"}],
               "tools": value["input"][0]["tools"], "max_output_tokens": 512}
    del minimal["tools"][0]["description"]
    parsed = parse_cli_request(minimal, {"mock-model"})
    result = parsed.replace(parsed.segments)
    assert result["input"][0]["content"] == "😀 original"
    assert result["max_output_tokens"] == 512
    assert result["stream"] is False
    without_tools = parse_cli_request({"model": "mock-model", "input": [{"role": "user", "content": "safe"}]}, {"mock-model"})
    assert "tools" not in without_tools.replace(without_tools.segments)


def test_supported_schema_descriptors_composition_and_defaults_are_all_inspected():
    value = cli_request()
    schema = value["input"][0]["tools"][0]["tools"][1]["parameters"]
    # Exercise supported schema vocabulary with synthetic reference names and values.
    schema["title"] = "Synthetic schema title"
    schema["description"] = "Synthetic schema description"
    schema["$defs"] = {"Local": {"type": "string", "pattern": "synthetic.*", "format": "synthetic"}}
    schema["properties"]["cell_id"] = {"$ref": "#/$defs/Local"}
    schema["properties"]["array"] = {"type": "array", "items": {"type": ["string", "null"]},
        "minItems": 0, "maxItems": 3, "default": ["synthetic default"], "examples": [["synthetic example"]]}
    schema["additionalProperties"] = {"type": "number", "minimum": -1, "maximum": 3.0}
    schema["anyOf"] = [{"type": "object"}]
    schema["oneOf"] = [{"type": "object"}]
    schema["allOf"] = [{"type": "object"}]
    schema["properties"]["label"] = {"type": "string", "minLength": 0, "maxLength": 20,
        "enum": ["synthetic enum", None, True, 1]}
    parsed = parse_cli_request(value, {"mock-model"})
    texts = [item["text"] for item in parsed.segments]
    assert {"synthetic default", "synthetic example", "synthetic enum", "synthetic.*", "#/$defs/Local"} <= set(texts)
    assert parsed.replace(parsed.segments)["tools"][0]["tools"][1]["parameters"] == schema


@pytest.mark.parametrize("patch", [
    {"type": []}, {"type": ["invalid"]}, {"type": {}}, {"properties": []},
    {"properties": {"": {"type": "string"}}}, {"properties": {"x" * 129: {"type": "string"}}},
    {"properties": {f"field{i}": {"type": "string"} for i in range(33)}},
    {"required": "cell_id"}, {"required": ["cell_id", "cell_id"]}, {"required": ["missing"]},
    {"additionalProperties": None}, {"items": []}, {"anyOf": []}, {"oneOf": {}},
    {"allOf": [{}] * 17}, {"enum": []}, {"enum": [{}]}, {"minimum": True},
    {"maximum": "synthetic"}, {"minLength": True}, {"maxLength": -1},
    {"maxItems": 1000001}, {"examples": "synthetic"}, {"examples": [None] * 17},
])
def test_invalid_schema_cannot_hide_payload_in_unvalidated_shapes(patch):
    value = cli_request()
    value["input"][0]["tools"][0]["tools"][1]["parameters"].update(patch)
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("properties", [None, 1, False])
def test_schema_key_order_cannot_turn_invalid_properties_into_raw_typeerror(properties):
    value = cli_request()
    # JSON object order is attacker-controlled; validate required after checking
    # its referenced container even if the container occurs later on the wire.
    value["input"][0]["tools"][0]["tools"][1]["parameters"] = {
        "required": ["synthetic"], "properties": properties}
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("value", [None, [], {1: "synthetic"},
    {"model": "mock-model", "input": None}, {"model": "mock-model", "input": []},
    {"model": "mock-model", "input": [None]},
    {"model": "mock-model", "input": [{"role": "user", "content": None}]},
    {"model": "mock-model", "input": [{"role": "assistant", "status": "in_progress", "content": "safe"}]},
    {"model": "mock-model", "input": [{"role": "user", "content": [{"type": "input_text", "text": "safe", "annotations": []}]}]},
])
def test_malformed_envelopes_are_protocol_errors_not_raw_python_exceptions(value):
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("mutation", ["wrong_namespace", "namespace_type", "namespace_empty", "child_not_dict",
    "duplicate_child", "exec_wrong_type", "wait_wrong_type", "strict_bool", "wrong_additional_role", "bad_tools",
    "root_tools_wrong_type", "only_definitions", "nonstring_boolean", "nested_nonstring_key"])
def test_noncanonical_tool_shapes_never_reach_upstream(mutation):
    value = cli_request()
    group = value["input"][0]["tools"][0]
    if mutation == "wrong_namespace": group["name"] = "untrusted"
    elif mutation == "namespace_type": group["type"] = "function"
    elif mutation == "namespace_empty": group["tools"] = []
    elif mutation == "child_not_dict": group["tools"][0] = None
    elif mutation == "duplicate_child": group["tools"][1] = deepcopy(group["tools"][0])
    elif mutation == "exec_wrong_type": group["tools"][0]["type"] = "function"
    elif mutation == "wait_wrong_type": group["tools"][1]["type"] = "custom"
    elif mutation == "strict_bool": group["tools"][1]["strict"] = 1
    elif mutation == "wrong_additional_role": value["input"][0]["role"] = "user"
    elif mutation == "bad_tools": value["input"][0]["tools"] = None
    elif mutation == "root_tools_wrong_type": value["tools"] = {}
    elif mutation == "only_definitions": value["input"] = value["input"][:1]
    elif mutation == "nonstring_boolean": value["stream"] = "true"
    else: group["tools"][1]["parameters"]["default"] = {1: "synthetic"}
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 10**1000, {"synthetic"}])
def test_non_json_or_nonfinite_schema_defaults_fail_closed(value):
    body = cli_request()
    body["input"][0]["tools"][0]["tools"][1]["parameters"]["default"] = value
    with pytest.raises(ProtocolError):
        parse_cli_request(body, {"mock-model"})


def test_schema_and_payload_complexity_limits_prevent_deep_or_fragmented_bypass():
    body = cli_request()
    schema = body["input"][0]["tools"][0]["tools"][1]["parameters"]
    child = schema
    for _ in range(10):
        child["items"] = {}
        child = child["items"]
    with pytest.raises(ProtocolError, match="cli_schema_limit"):
        parse_cli_request(body, {"mock-model"})
    deep = current = {}
    for _ in range(40):
        current["nested"] = {}
        current = current["nested"]
    body = cli_request()
    body["client_metadata"] = deep
    with pytest.raises(ProtocolError, match="cli_shape_limit"):
        parse_cli_request(body, {"mock-model"})
    for oversized in ([0] * 4100, "x" * (1024 * 1024 + 1)):
        with pytest.raises(ProtocolError, match="cli_shape_limit"):
            parse_cli_request(cli_request() | {"client_metadata": oversized}, {"mock-model"})
    fragments = [{"type": "input_text", "text": ""} for _ in range(256)]
    with pytest.raises(ProtocolError, match="segment_limit"):
        parse_cli_request({"model": "mock-model", "input": [{"role": "user", "content": fragments},
                           {"role": "user", "content": "one more"}]}, {"mock-model"})


@pytest.mark.parametrize("mutation", ["none_entry", "wrong_id", "not_text", "extra_field", "surrogate", "expanded"])
def test_full_length_replacement_cannot_inject_new_structure(mutation):
    parsed = parse_cli_request(cli_request(), {"mock-model"})
    replacement = deepcopy(parsed.segments)
    if mutation == "none_entry": replacement[0] = None
    elif mutation == "wrong_id": replacement[0]["segment_id"] = "wrong"
    elif mutation == "not_text": replacement[0]["text"] = False
    elif mutation == "extra_field": replacement[0]["raw"] = "synthetic"
    elif mutation == "surrogate": replacement[0]["text"] = "\ud800"
    else: replacement[0]["text"] = "x" * 16000
    with pytest.raises(ProtocolError):
        parsed.replace(replacement)


@given(st.text(max_size=40))
def test_descriptor_codepoints_round_trip_and_mask_without_changing_other_input(text):
    body = cli_request()
    body["input"][0]["tools"][0]["tools"][0]["description"] = "😀Synthetic prefix\n" + text
    original = deepcopy(body)
    parsed = parse_cli_request(body, {"mock-model"})
    replacements = deepcopy(parsed.segments)
    expected = body["input"][0]["tools"][0]["tools"][0]["description"]
    for segment in replacements:
        if segment["text"] == expected:
            segment["text"] = "[REDACTED]"
    canonical = parsed.replace(replacements)
    assert canonical["tools"][0]["tools"][0]["description"] == "[REDACTED]"
    assert body == original


def test_multiline_secret_descriptor_is_detected_at_original_codepoints_and_blocks():
    from gateway.policy import evaluate
    from gateway.secret_rules import detect

    synthetic_key = "sk-proj-" + "SyntheticFixture0123456789" * 3
    value = cli_request()
    value["input"][0]["tools"][0]["tools"][0]["description"] = "😀 header\n" + synthetic_key + "\nfooter"
    parsed = parse_cli_request(value, {"mock-model"})
    findings = detect(parsed.segments)
    assert len(findings) == 1
    found = findings[0]
    segment = next(item for item in parsed.segments if item["segment_id"] == found["segment_id"])
    assert segment["text"][found["start"]:found["end"]] == synthetic_key
    decision = evaluate(parsed.segments, {"status": "complete", "findings": findings,
        "model_version": "synthetic", "threshold_version": "synthetic", "timing_ms": 0.0})
    assert decision.action == "block"
    assert decision.segments == []


@pytest.mark.parametrize("patch", [{"prompt_cache_key": "x" * 36}, {"text": {"verbosity": "unsupported"}}])
def test_malformed_fixed_metadata_tokens_do_not_escape_typed_validation(patch):
    with pytest.raises(ProtocolError):
        parse_cli_request(cli_request() | patch, {"mock-model"})


def test_duplicate_namespaces_and_mismatched_history_part_types_are_rejected():
    value = cli_request()
    value["tools"] = deepcopy(value["input"][0]["tools"])
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})
    value = cli_request()
    value["input"][2]["content"][0]["type"] = "input_text"
    with pytest.raises(ProtocolError):
        parse_cli_request(value, {"mock-model"})
    value = cli_request()
    del value["input"][0]["id"]
    assert parse_cli_request(value, {"mock-model"}).segments


_JSON_VALUES = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-1000, 1000), st.text(max_size=12)),
    lambda values: st.one_of(st.lists(values, max_size=4), st.dictionaries(st.text(max_size=10), values, max_size=4)),
    max_leaves=20,
)


@given(st.dictionaries(st.sampled_from(["type", "properties", "required", "additionalProperties", "items", "enum",
    "anyOf", "$defs", "$ref", "minimum", "maxLength", "description", "examples", "default"]), _JSON_VALUES, max_size=8))
def test_schema_shape_and_key_order_fuzz_never_escape_protocol_error(schema):
    body = cli_request()
    body["input"][0]["tools"][0]["tools"][1]["parameters"] = schema
    try:
        parsed = parse_cli_request(body, {"mock-model"})
    except ProtocolError:
        return
    assert parsed.replace(parsed.segments)["tool_choice"] == "none"


def test_numeric_schema_payloads_are_inspected_as_immutable_canonical_segments():
    body = cli_request()
    schema = body["input"][0]["tools"][0]["tools"][1]["parameters"]
    schema.update(default=5123456789012345, examples=[1.25, False, None], minimum=-3, maximum=9.0)
    parsed = parse_cli_request(body, {"mock-model"})
    texts = [segment["text"] for segment in parsed.segments]
    for canonical in ("5123456789012345", "1.25", "-3", "9.0"):
        assert canonical in texts
    assert "false" not in texts and "None" not in texts
    replacement = deepcopy(parsed.segments)
    for segment in replacement:
        if segment["text"] == "5123456789012345":
            segment["text"] = "[REDACTED]"
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacement)
    canonical = parsed.replace(parsed.segments)
    assert canonical["tools"][0]["tools"][1]["parameters"]["default"] == 5123456789012345


@pytest.mark.parametrize("pii_type,action", [("CREDIT_CARD", "block"), ("PERSON", "mask")])
def test_policy_cannot_forward_numeric_pii_from_schema_literal(pii_type, action):
    from gateway.policy import evaluate

    body = cli_request()
    number = 5123456789012345
    body["input"][0]["tools"][0]["tools"][1]["parameters"]["enum"] = [number]
    parsed = parse_cli_request(body, {"mock-model"})
    segment = next(item for item in parsed.segments if item["text"] == str(number))
    decision = evaluate(parsed.segments, {"status": "complete", "findings": [{"segment_id": segment["segment_id"],
        "type": pii_type, "start": 0, "end": len(segment["text"]), "score": 1.0, "detector": "model"}],
        "model_version": "synthetic", "threshold_version": "synthetic", "timing_ms": 0.0})
    assert decision.action == action
    if action == "block":
        assert decision.segments == []
    else:
        with pytest.raises(ProtocolError, match="invalid_replacement"):
            parsed.replace(decision.segments)


@pytest.mark.parametrize("location", ["grammar", "default", "examples"])
def test_semantic_grammar_and_default_data_are_not_mutable_descriptors(location):
    body = cli_request()
    target = "SYNTHETIC_SEMANTIC_VALUE"
    children = body["input"][0]["tools"][0]["tools"]
    if location == "grammar":
        children[0]["format"]["definition"] = f'start: "{target}"'
        target = children[0]["format"]["definition"]
    elif location == "default":
        children[1]["parameters"]["default"] = {"description": target}
    else:
        children[1]["parameters"]["examples"] = [{"nested": {"title": target}}]
    parsed = parse_cli_request(body, {"mock-model"})
    replacement = deepcopy(parsed.segments)
    for segment in replacement:
        if segment["text"] == target:
            segment["text"] = "[REDACTED]"
    with pytest.raises(ProtocolError, match="invalid_replacement"):
        parsed.replace(replacement)


def test_schema_property_named_default_still_has_mutable_annotation_context():
    body = cli_request()
    schema = body["input"][0]["tools"][0]["tools"][1]["parameters"]
    schema["properties"]["default"] = {"type": "string", "description": "Synthetic actual schema annotation"}
    parsed = parse_cli_request(body, {"mock-model"})
    replacement = deepcopy(parsed.segments)
    for segment in replacement:
        if segment["text"] == "Synthetic actual schema annotation":
            segment["text"] = "[REDACTED]"
    canonical = parsed.replace(replacement)
    assert canonical["tools"][0]["tools"][1]["parameters"]["properties"]["default"]["description"] == "[REDACTED]"
