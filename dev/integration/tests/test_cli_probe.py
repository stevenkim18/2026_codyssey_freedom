"""Probe helpers never retain source text or inherit real credentials."""

import json
import subprocess

import pytest

from integration.probe_cli import build_command, experiment_overrides, isolated_env, summarize_request
from integration.probe_cli import run_probe


def test_request_summary_preserves_only_lengths_types_keys_and_tool_names():
    raw = "SYNTHETIC_PRIVATE_CONTENT_NOT_FOR_REPORTS"
    body = {"instructions": "😀" + raw, "model": raw, "metadata": {"nested": raw},
            "input": [{"role": "user", "content": [{"type": "input_text", "text": raw}]},
                      {"type": "additional_tools", "tools": [{"type": "namespace", "name": "functions",
                        "description": raw, "tools": [{"type": "function", "name": "update_plan", "description": raw,
                          "parameters": {"type": "object", "properties": {"text": {"description": raw}}}}]}]}],
            "tools": []}
    summary = summarize_request(body)
    assert raw not in json.dumps(summary)
    assert summary["instructions_length"] == len(raw) + 1
    assert summary["supported_text_codepoints"] == 2 * len(raw) + 1
    assert summary["tool_names"] == ["functions.update_plan"]
    assert summary["tool_definitions_count"] == 1
    assert summary["additional_tools_count"] == 1
    assert summary["tool_groups"][0]["name"] == "functions"
    assert summary["tool_groups"][0]["field_types"]["tools"] == "list"
    assert summary["input_types"] == ["message", "additional_tools"]
    assert summary["field_types"]["model"] == "str"


def test_leaf_schema_structure_and_string_locations_are_reported_without_values():
    raw = "SYNTHETIC_PRIVATE_SCHEMA_DESCRIPTION"
    # A synthetic ordinary JSON Schema fixture; never copied from CLI source text.
    body = {"input": [{"type": "additional_tools", "tools": [{"type": "namespace", "name": "functions",
        "tools": [{"type": "function", "name": "exec", "description": raw, "strict": True,
            "parameters": {"type": "object", "properties": {"code": {"type": "string", "description": raw}},
                           "required": ["code"], "additionalProperties": False}}]}]}]}
    summary = summarize_request(body)
    assert raw not in json.dumps(summary)
    leaf = summary["tool_schemas"][0]
    assert leaf["name"] == "functions.exec"
    assert leaf["field_types"]["parameters"] == "dict"
    root = leaf["schema_nodes"][0]
    assert root["path"] == "/parameters"
    assert root["schema_type"] == "object"
    assert root["required_names"] == ["code"]
    assert any(item["path"] == "/parameters/properties/code/description" and item["codepoints"] == len(raw)
               for item in leaf["string_fields"])


def test_custom_tool_grammar_is_counted_without_recording_its_content():
    raw = "SYNTHETIC_PRIVATE_GRAMMAR_CONTENT"
    body = {"input": "😀", "tools": [{"type": "custom", "name": "exec", "description": raw,
            "format": {"type": "grammar", "syntax": "lark", "definition": raw}}]}
    summary = summarize_request(body)
    assert raw not in json.dumps(summary)
    leaf = summary["tool_schemas"][0]
    assert leaf["tool_type"] == "custom"
    assert leaf["format_type"] == "grammar"
    assert any(item["path"] == "/format/definition" and item["codepoints"] == len(raw)
               for item in leaf["string_fields"])
    expected = len("custom") + len("exec") + 2 * len(raw) + len("grammar") + len("lark")
    assert summary["tool_string_codepoints"] == expected
    assert summary["inspection_text_codepoints"] == 1 + expected


def test_plain_string_input_and_message_content_are_counted_by_codepoints():
    assert summarize_request({"instructions": "😀", "input": "한국"})["supported_text_codepoints"] == 3
    summary = summarize_request({"input": [{"role": "user", "content": "😀한"}]})
    assert summary["supported_text_codepoints"] == 2
    assert summary["tool_definitions_count"] == 0


@pytest.mark.parametrize("body", [None, [], {"input": [None, {"type": "additional_tools", "tools": None}], "tools": None},
    {"instructions": None, "input": None}])
def test_malformed_probe_requests_produce_content_free_metadata(body):
    result = summarize_request(body)
    assert result["malformed"] is True
    assert "text" not in result


def test_child_environment_has_only_synthetic_credentials_and_proxy_guard():
    parent = {"PATH": "/synthetic/bin", "LANG": "C", "OPENAI_API_KEY": "SYNTHETIC_REAL_KEY",
              "OPENAI_BASE_URL": "https://evil.invalid", "CODEX_HOME": "/real/home",
              "HTTPS_PROXY": "http://evil.invalid", "OTHER_SECRET": "SYNTHETIC_PRIVATE"}
    env = isolated_env(parent, "/isolated/home", 9876)
    assert env["CODEX_HOME"] == "/isolated/home"
    assert env["PII_PROBE_TOKEN"] == "synthetic-loopback-only"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert env[name] == "http://127.0.0.1:9876"
    assert env["NO_PROXY"] == env["no_proxy"] == "127.0.0.1,localhost,::1"
    assert "OPENAI_API_KEY" not in env and "OPENAI_BASE_URL" not in env and "OTHER_SECRET" not in env
    assert env["PATH"] == "/synthetic/bin"


@pytest.mark.parametrize("variant", ["baseline", "minimal", "agents_off", "images_off", "sleep_goals_off", "all_off",
    "user_input_off", "code_mode_off", "direct_tools_off"])
def test_every_experiment_pins_loopback_provider_before_synthetic_prompt(variant):
    command = build_command("/synthetic/codex", 8765, "/isolated/instructions.md", variant)
    assignments = [command[index + 1] for index, token in enumerate(command[:-1]) if token == "-c"]
    assert command[:2] == ["/synthetic/codex", "exec"]
    assert "--ignore-user-config" in command
    assert 'model_provider="mock"' in assignments
    assert 'model_providers.mock.base_url="http://127.0.0.1:8765/v1"' in assignments
    assert 'model_providers.mock.env_key="PII_PROBE_TOKEN"' in assignments
    assert "model_providers.mock.request_max_retries=0" in assignments
    assert "model_providers.mock.stream_max_retries=0" in assignments
    assert "--profile" not in command
    assert all("openai.com" not in part for part in command)


def test_minimal_variant_only_replaces_client_instructions_not_wire_content():
    overrides = experiment_overrides("minimal", "/isolated/instructions.md")
    assert overrides == ['model_instructions_file="/isolated/instructions.md"']
    assert experiment_overrides("baseline", "/isolated/instructions.md") == []
    assert "features.multi_agent=false" in experiment_overrides("all_off", "/isolated/instructions.md")


def test_core_tool_experiments_use_official_config_schema_settings():
    questions = experiment_overrides("user_input_off", "/isolated/instructions.md")
    assert "tools.experimental_request_user_input.enabled=false" in questions
    core = experiment_overrides("code_mode_off", "/isolated/instructions.md")
    assert "features.code_mode.enabled=false" in core
    assert "tools.experimental_request_user_input.enabled=false" in core
    assert "features.incremental_tools=false" in experiment_overrides("direct_tools_off", "/isolated/instructions.md")


def test_host_table_and_network_discovery_variants_keep_security_defaults():
    host = experiment_overrides("host_table_off", "/isolated/instructions.md")
    assert "features.code_mode_host.enabled=false" in host
    assert "features.code_mode_host=false" not in host
    offline = experiment_overrides("no_discovery", "/isolated/instructions.md")
    assert "features.api_key_model_discovery=false" in offline
    assert "analytics.enabled=false" in offline


@pytest.mark.parametrize("variant", [None, [], "model_provider=evil", "unrecognized"])
def test_arbitrary_experiment_configuration_is_rejected(variant):
    with pytest.raises(ValueError, match="invalid probe variant"):
        experiment_overrides(variant, "/isolated/instructions.md")


@pytest.mark.parametrize("timeout", [None, True, 0, -1, float("nan"), float("inf"), 121])
def test_invalid_timeout_is_rejected_before_opening_any_server(timeout, monkeypatch):
    def forbidden_server(*args, **kwargs):
        pytest.fail("invalid timeout reached server creation")
    monkeypatch.setattr("integration.probe_cli.ThreadingHTTPServer", forbidden_server)
    with pytest.raises(ValueError, match="invalid probe timeout"):
        run_probe(timeout_s=timeout)


@pytest.fixture
def isolated_driver(monkeypatch):
    servers, invocations = [], []

    class Server:
        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            self.server_port, self.started = 9876 + len(servers), False
            servers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def serve_forever(self):
            self.started = True

        def shutdown(self):
            self.started = False

    class Thread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

        def join(self, timeout):
            pass

    monkeypatch.setattr("integration.probe_cli.ThreadingHTTPServer", Server)
    monkeypatch.setattr("integration.probe_cli.threading.Thread", Thread)
    monkeypatch.setattr("integration.probe_cli.shutil.which", lambda _: "/synthetic/codex")
    monkeypatch.setenv("OPENAI_API_KEY", "SYNTHETIC_REAL_KEY_MUST_NOT_INHERIT")
    return servers, invocations


def test_driver_never_returns_child_stdout_stderr_or_inherits_real_key(isolated_driver, monkeypatch):
    servers, invocations = isolated_driver

    def child(command, **kwargs):
        assert len(servers) == 2 and all(server.started for server in servers)
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:9876"
        invocations.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, "codex-cli 0.166.0\n", "")
        return subprocess.CompletedProcess(command, 0, "SAFE_PROBE SYNTHETIC_RAW_CHILD_OUTPUT", "SYNTHETIC_RAW_CHILD_ERROR")

    monkeypatch.setattr("integration.probe_cli.subprocess.run", child)
    report = run_probe(variant="all_off")
    assert len(invocations) == 2
    assert report["safe_output_seen"] is True
    assert report["returncode"] == 0
    assert report["error_code"] is None
    assert "SYNTHETIC_RAW" not in json.dumps(report)
    assert all(not server.started for server in servers)


@pytest.mark.parametrize("failure,code", [("version", "unsupported_cli_version"),
    ("timeout", "probe_timeout"), ("unavailable", "probe_unavailable")])
def test_driver_failures_are_content_free_and_cleanup_servers(isolated_driver, monkeypatch, failure, code):
    servers, invocations = isolated_driver

    def child(command, **kwargs):
        invocations.append(command)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("SYNTHETIC_PRIVATE_COMMAND", 5, output="SYNTHETIC_PRIVATE_STDOUT")
        if failure == "unavailable":
            raise OSError("SYNTHETIC_PRIVATE_ERROR")
        return subprocess.CompletedProcess(command, 0, "codex-cli unverified", "SYNTHETIC_PRIVATE_ERROR")

    monkeypatch.setattr("integration.probe_cli.subprocess.run", child)
    report = run_probe()
    assert report["error_code"] == code
    assert report["safe_output_seen"] is False
    assert "SYNTHETIC_PRIVATE" not in json.dumps(report)
    assert len(invocations) == 1
    assert all(not server.started for server in servers)
