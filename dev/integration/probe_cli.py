"""Probe the installed CLI with synthetic text and a loopback-only fake provider.

Never contacts OpenAI, reads a real API key, or records request text. This is a
protocol probe, not the gateway acceptance test (see tests/test_cli.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def run_probe() -> dict:
    summaries = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            summaries.append({"path": self.path, "keys": sorted(body),
                "input_types": [x.get("type", "message") for x in body.get("input", [])],
                "tool_types": [x.get("type") for x in body.get("tools", [])],
                "fields": {k: v for k, v in body.items() if k not in {"instructions", "input", "tools"}},
                "instructions_length": len(body.get("instructions", "")),
                "input_keys": [sorted(x) for x in body.get("input", [])],
                "input_summary": [{"type": x.get("type"), "role": x.get("role"),
                    "tools_count": len(x.get("tools", [])),
                    "content_types": [c.get("type") for c in x.get("content", [])] if isinstance(x.get("content"), list) else type(x.get("content")).__name__,
                    "text_length": sum(len(c.get("text", "")) for c in x.get("content", []) if isinstance(c, dict)) if isinstance(x.get("content"), list) else 0}
                    for x in body.get("input", [])]})
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

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        command = [shutil.which("codex") or "codex", "exec", "--ignore-user-config", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--json",
            "-c", 'model_provider="mock"', "-c", 'model="gpt-6.1-sol"',
            "-c", 'model_providers.mock.name="Loopback probe"',
            "-c", f'model_providers.mock.base_url="http://127.0.0.1:{server.server_port}/v1"',
            "-c", 'model_providers.mock.env_key="PII_PROBE_TOKEN"',
            "-c", 'model_providers.mock.wire_api="responses"',
            "-c", 'model_providers.mock.supports_websockets=false',
            "-c", 'model_providers.mock.request_max_retries=0',
            "-c", 'model_providers.mock.stream_max_retries=0',
            "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
            "-c", "features.shell_tool=false", "-c", "features.unified_exec=false",
            "-c", "features.apply_patch_freeform=false", "-c", "features.apps=false",
            "Reply SAFE_PROBE without tools."]
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "TMPDIR", "LANG", "LC_ALL"}}
        # CODEX_HOME is used for its documented purpose: a dedicated isolated
        # Codex configuration/state root, never the user's production profile.
        env["CODEX_HOME"] = directory
        env["PII_PROBE_TOKEN"] = "synthetic-loopback-only"
        try:
            result = subprocess.run(command, cwd=directory, env=env, input="", capture_output=True, text=True, timeout=30)
            return {"returncode": result.returncode, "requests": summaries,
                    "safe_output_seen": "SAFE_PROBE" in result.stdout,
                    "stdout": result.stdout[-2000:],
                    "stderr_tail": result.stderr[-1500:]}
        finally:
            server.shutdown()
            thread.join(timeout=2)


if __name__ == "__main__":
    print(json.dumps(run_probe(), ensure_ascii=False, indent=2))
