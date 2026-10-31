"""Probe the installed CLI with synthetic text and a loopback-only fake provider.

Uses an explicit loopback provider and deny proxy, with synthetic credentials. This is a
protocol probe, not the gateway acceptance test (see tests/test_roundtrip.py).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


VARIANTS = ("baseline", "minimal", "agents_off", "images_off", "sleep_goals_off", "all_off",
            "user_input_off", "code_mode_off", "direct_tools_off", "host_table_off", "no_discovery")
MINIMAL_INSTRUCTIONS = "You are a text-only assistant. Answer the user with plain text. Do not call tools.\n"
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}\Z")


def _identifier(value):
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else "invalid"


def _schema_summary(item, name):
    result = {"name": name, "tool_type": _identifier(item.get("type")), "keys": sorted(item), "field_types": {key: type(value).__name__ for key, value in item.items()},
              "schema_nodes": [], "string_fields": []}
    if isinstance(item.get("format"), dict):
        result["format_type"] = _identifier(item["format"].get("type"))

    def walk(value, path):
        if isinstance(value, str):
            result["string_fields"].append({"path": path, "codepoints": len(value)})
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}/{index}")
        elif isinstance(value, dict):
            node = {"path": path, "keys": sorted(value), "field_types": {key: type(child).__name__ for key, child in value.items()}}
            if value.get("type") in ("object", "array", "string", "integer", "number", "boolean", "null"):
                node["schema_type"] = value["type"]
            if isinstance(value.get("required"), list):
                node["required_names"] = [_identifier(child) for child in value["required"]]
            if type(value.get("additionalProperties")) is bool:
                node["additional_properties"] = value["additionalProperties"]
            result["schema_nodes"].append(node)
            for key, child in value.items():
                walk(child, f"{path}/{key}")

    if "parameters" in item:
        walk(item["parameters"], "/parameters")
    if "format" in item:
        walk(item["format"], "/format")
    for key, value in item.items():
        if isinstance(value, str):
            walk(value, f"/{key}")
    return result


def summarize_request(body):
    """Copy only structural metadata and codepoint lengths, never text values."""
    result = {"keys": [], "field_types": {}, "input_types": [], "input_summary": [],
              "instructions_length": 0, "supported_text_codepoints": 0,
              "additional_tools_count": 0, "top_level_tool_count": 0,
              "tool_definitions_count": 0, "tool_names": [], "tool_schemas": [], "tool_groups": [], "tool_string_codepoints": 0,
              "inspection_text_codepoints": 0, "malformed": False}
    if not isinstance(body, dict):
        result["malformed"] = True
        return result
    result["keys"] = sorted(str(key) for key in body)
    result["field_types"] = {str(key): type(value).__name__ for key, value in body.items()}
    instructions = body.get("instructions", "")
    if isinstance(instructions, str):
        result["instructions_length"] = len(instructions)
        result["supported_text_codepoints"] += len(instructions)
    else:
        result["malformed"] = True

    def tools(value, prefix=""):
        if not isinstance(value, list):
            result["malformed"] = True
            return
        for item in value:
            if not isinstance(item, dict):
                result["malformed"] = True
                continue
            name = _identifier(item.get("name", item.get("type")))
            qualified = f"{prefix}.{name}" if prefix else name
            if item.get("type") == "namespace" and "tools" in item:
                group = _schema_summary(item, qualified)
                result["tool_groups"].append(group)
                result["tool_string_codepoints"] += sum(value["codepoints"] for value in group["string_fields"])
                tools(item["tools"], qualified)
            else:
                result["tool_names"].append(qualified)
                leaf = _schema_summary(item, qualified)
                result["tool_schemas"].append(leaf)
                result["tool_string_codepoints"] += sum(value["codepoints"] for value in leaf["string_fields"])
                result["tool_definitions_count"] += 1

    top_tools = body.get("tools", [])
    result["top_level_tool_count"] = len(top_tools) if isinstance(top_tools, list) else 0
    tools(top_tools)
    inputs = body.get("input")
    if isinstance(inputs, str):
        result["supported_text_codepoints"] += len(inputs)
        result["input_types"] = ["str"]
    elif isinstance(inputs, list):
        for item in inputs:
            if not isinstance(item, dict):
                result["malformed"] = True
                continue
            kind = _identifier(item.get("type", "message"))
            result["input_types"].append(kind)
            info = {"type": kind, "keys": sorted(item), "content_types": [], "text_length": 0}
            if kind == "additional_tools":
                definitions = item.get("tools", [])
                result["additional_tools_count"] += len(definitions) if isinstance(definitions, list) else 0
                tools(definitions)
            content = item.get("content")
            if isinstance(content, str):
                info["text_length"] = len(content)
                info["content_types"] = ["str"]
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        result["malformed"] = True
                        continue
                    info["content_types"].append(_identifier(part.get("type")))
                    if isinstance(part.get("text"), str):
                        info["text_length"] += len(part["text"])
            result["supported_text_codepoints"] += info["text_length"]
            result["input_summary"].append(info)
    else:
        result["malformed"] = True
    result["inspection_text_codepoints"] = result["supported_text_codepoints"] + result["tool_string_codepoints"]
    return result


def experiment_overrides(variant, instructions_file):
    if not isinstance(variant, str) or variant not in VARIANTS:
        raise ValueError("invalid probe variant")
    if variant == "baseline":
        return []
    extended = variant in ("user_input_off", "code_mode_off", "direct_tools_off", "host_table_off", "no_discovery")
    base_variant = "all_off" if extended else variant
    values = [f"model_instructions_file={json.dumps(str(instructions_file))}"]
    if base_variant in ("agents_off", "all_off"):
        values += ["agents.enabled=false", "features.multi_agent=false", "features.request_permissions_tool=false"]
    if base_variant in ("images_off", "all_off"):
        values += ["tools.view_image=false", "features.view_image=false", "features.image_generation=false"]
    if base_variant in ("sleep_goals_off", "all_off"):
        values += ["features.sleep_tool=false", "features.goals=false"]
    if base_variant == "all_off":
        values += ["features.plugins=false", "features.remote_plugin=false", "features.skill_search=false",
                   "features.tool_suggest=false", "features.skill_mcp_dependency_install=false",
                   "features.memories=false", "features.hooks=false", "features.browser_use=false",
                   "features.computer_use=false", "features.in_app_browser=false", "features.code_mode_host=false",
                   "skills.bundled.enabled=false", "skills.include_instructions=false", 'personality="none"']
    if extended:
        values += ["tools.experimental_request_user_input.enabled=false", "tools.update_plan.enabled=false",
                   "features.default_mode_request_user_input=false"]
    if variant in ("code_mode_off", "direct_tools_off", "host_table_off", "no_discovery"):
        values += ["features.code_mode.enabled=false", "features.code_mode_only=false"]
    if variant in ("direct_tools_off", "host_table_off", "no_discovery"):
        values += ["features.incremental_tools=false"]
    if variant == "host_table_off":
        values.remove("features.code_mode_host=false")
        values += ["features.code_mode_host.enabled=false", "features.code_mode_host.disable_in_process_fallback=true"]
    if variant == "no_discovery":
        values += ["features.api_key_model_discovery=false", "features.api_key_cyber_access_programs=false",
                   "analytics.enabled=false", "feedback.enabled=false"]
    return values


def build_command(binary, port, instructions_file, variant):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid probe port")
    assignments = ['model_provider="mock"', 'model="gpt-6.1-sol"',
        'model_providers.mock.name="Loopback probe"',
        f'model_providers.mock.base_url="http://127.0.0.1:{port}/v1"',
        'model_providers.mock.env_key="PII_PROBE_TOKEN"', 'model_providers.mock.wire_api="responses"',
        "model_providers.mock.supports_websockets=false", "model_providers.mock.request_max_retries=0",
        "model_providers.mock.stream_max_retries=0", 'web_search="disabled"', "project_doc_max_bytes=0",
        "features.shell_tool=false", "features.unified_exec=false", "features.apply_patch_freeform=false",
        "features.apps=false", "check_for_update_on_startup=false", *experiment_overrides(variant, instructions_file)]
    command = [binary, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
               "--sandbox", "read-only", "--color", "never", "--json"]
    for assignment in assignments:
        command += ["-c", assignment]
    return command + ["Reply SAFE_PROBE without tools."]


def isolated_env(parent, home, deny_proxy_port):
    result = {key: value for key, value in parent.items() if key in {"PATH", "TMPDIR", "LANG", "LC_ALL"}}
    result.update(CODEX_HOME=str(home), PII_PROBE_TOKEN="synthetic-loopback-only")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        result[name] = f"http://127.0.0.1:{deny_proxy_port}"
    result["NO_PROXY"] = result["no_proxy"] = "127.0.0.1,localhost,::1"
    return result


def run_probe(*, variant="baseline", timeout_s=30) -> dict:
    experiment_overrides(variant, "/unused")
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 120:
        raise ValueError("invalid probe timeout")
    summaries, denied = [], []

    class DenyHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reject(self):
            denied.append(1)
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_CONNECT = do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = reject

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/v1/responses" or not 0 < length <= 8 * 1024 * 1024:
                    raise ValueError
                summaries.append(summarize_request(json.loads(self.rfile.read(length))))
            except (ValueError, TypeError):
                self.send_response(400)
                self.end_headers()
                return
            response = {"id": "resp_probe", "object": "response", "created_at": 1,
                "status": "completed", "model": "gpt-6.1-sol", "output": [
                    {"type": "message", "id": "msg_probe", "status": "completed", "role": "assistant",
                     "content": [{"type": "output_text", "text": "SAFE_PROBE", "annotations": []}]}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            events = [
                {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                {"type": "response.output_item.added", "output_index": 0, "item": response["output"][0]},
                {"type": "response.output_text.delta", "item_id": "msg_probe", "output_index": 0,
                 "content_index": 0, "delta": "SAFE_PROBE"},
                {"type": "response.output_item.done", "output_index": 0, "item": response["output"][0]},
                {"type": "response.completed", "response": response},
            ]
            data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), DenyHandler) as guard, \
            ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
        guard_thread = threading.Thread(target=guard.serve_forever, daemon=True)
        guard_thread.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        instructions = Path(directory) / "text-only-instructions.md"
        instructions.write_text(MINIMAL_INSTRUCTIONS)
        binary = shutil.which("codex") or "codex"
        command = build_command(binary, server.server_port, instructions, variant)
        # CODEX_HOME is used for its documented purpose: a dedicated isolated
        # Codex configuration/state root, never the user's production profile.
        env = isolated_env(os.environ, directory, guard.server_port)
        report = {"variant": variant, "returncode": None, "requests": summaries,
                  "safe_output_seen": False, "blocked_external_attempts": 0, "error_code": None}
        try:
            version = subprocess.run([binary, "--version"], cwd=directory, env=env, input="", capture_output=True, text=True, timeout=5)
            if version.returncode or version.stdout.strip() != "codex-cli 0.166.0":
                report["error_code"] = "unsupported_cli_version"
            else:
                result = subprocess.run(command, cwd=directory, env=env, input="", capture_output=True, text=True, timeout=timeout_s)
                report.update(returncode=result.returncode, safe_output_seen="SAFE_PROBE" in result.stdout,
                              stdout_codepoints=len(result.stdout), stderr_codepoints=len(result.stderr))
        except subprocess.TimeoutExpired:
            report["error_code"] = "probe_timeout"
        except (OSError, ValueError):
            report["error_code"] = "probe_unavailable"
        finally:
            server.shutdown()
            guard.shutdown()
            thread.join(timeout=2)
            guard_thread.join(timeout=2)
            report["blocked_external_attempts"] = len(denied)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=VARIANTS, default="baseline")
    options = parser.parse_args()
    print(json.dumps(run_probe(variant=options.variant), ensure_ascii=False, indent=2))
