import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from launcher.profiles import install, PINNED_CLI, PROFILE


@pytest.fixture
def fixture(tmp_path):
    binary = tmp_path / "codex"
    binary.write_text('#!/bin/sh\nprintf "codex-cli 0.166.0\\n"\n')
    binary.chmod(0o700)
    home = tmp_path / "home"
    env = dict(PATH=str(tmp_path), PII_GATEWAY_LOCAL_TOKEN="fixture-token")
    return home, binary, env


def invoke(fixture, *args):
    home, _, env = fixture
    command = [sys.executable, "-m", "launcher", "doctor", "--codex-home", str(home), *args]
    # Preserve the real fresh-process CLI path while measuring it in coverage
    # runs. pytest-cov combines these parallel data files at session completion.
    import coverage
    active = coverage.Coverage.current()
    if active is not None:
        command = [sys.executable, "-m", "coverage", "run", "--branch", "--parallel-mode",
                   "--data-file", active.config.data_file, "--source=launcher", *command[1:]]
    result = subprocess.run(command,
                            env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    assert str(home) not in output
    assert "fixture-token" not in output
    return result.returncode, json.loads(output)


def test_offline_and_changed_profile(fixture):
    home, _, _ = fixture
    install(home, port=8765, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    code, report = invoke(fixture)
    assert code == 0
    assert report["profile_verified"] and report["cli_verified"] and report["installed"]
    assert report["local_gateway_authorized"] == "unverified"
    assert report["remote_detector_readiness"] == "unverified"
    (home / PROFILE).write_text("changed-secret")
    code, report = invoke(fixture)
    assert code == 2 and not report["profile_verified"]


def test_missing_and_wrong_cli(fixture):
    home, binary, _ = fixture
    assert invoke(fixture)[0] == 2
    install(home, port=8765, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    binary.write_text('#!/bin/sh\nprintf "codex-cli wrong-secret\\n"\n')
    code, report = invoke(fixture)
    assert code == 2 and not report["cli_verified"]


@pytest.mark.parametrize("url", ["https://api.openai.com", "http://localhost:8765", "http://127.0.0.1:8766", "http://127.0.0.1:8765@evil.invalid"])
def test_refuses_unmatched_endpoint(fixture, url):
    install(fixture[0], port=8765, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    code, report = invoke(fixture, "--gateway-url", url)
    assert code == 2
    assert report["code"] == "gateway_url_refused"


@pytest.mark.parametrize("auth_enabled", [True, False])
def test_loopback_auth_and_bad_token(fixture, auth_enabled):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            assert self.path == "/healthz"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"alive"}')

        def do_POST(self):
            assert self.path == "/v1/responses"
            assert self.rfile.read(int(self.headers["Content-Length"])) == b"{"
            accepted = not auth_enabled or self.headers["Authorization"] == "Bearer fixture-token"
            self.send_response(400 if accepted else 401)
            self.end_headers()
            self.wfile.write(json.dumps({"error":{"code":"INVALID_JSON" if accepted else "UNAUTHORIZED"}}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    install(fixture[0], port=port, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    url = f"http://127.0.0.1:{port}"
    try:
        code, report = invoke(fixture, "--gateway-url", url)
        if not auth_enabled:
            assert code == 2 and report["local_gateway_authorized"] is False
            return
        assert code == 0 and report["local_gateway_reachable"] is True
        assert report["local_gateway_authorized"] is True
        fixture[2]["PII_GATEWAY_LOCAL_TOKEN"] = "wrong"
        code, report = invoke(fixture, "--gateway-url", url)
        assert code == 2 and report["local_gateway_authorized"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert invoke(fixture, "--gateway-url", url)[0] == 2


def test_unknown_argument_sanitized(fixture):
    code, report = invoke(fixture, "--secret-unknown=private-value")
    assert code == 2 and report == {"status":"error", "code":"invalid_cli_arguments"}


@pytest.mark.parametrize("token", ["", "bad\nheader", "한글"])
def test_missing_or_unsafe_token(fixture, token):
    install(fixture[0], port=8765, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    fixture[2]["PII_GATEWAY_LOCAL_TOKEN"] = token
    code, report = invoke(fixture, "--gateway-url", "http://127.0.0.1:8765")
    assert code == 2 and report["code"] == "gateway_local_token_required"
    assert report["local_gateway_reachable"] == "unverified"


@pytest.mark.parametrize("response_status,response_body", [(302, b'{}'), (401, b'not-json'), (401, b'[]')])
def test_invalid_auth_control_response(fixture, response_status, response_body):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"alive"}')

        def do_POST(self):
            assert self.headers.get("Authorization") is None
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(response_status)
            self.send_header("Location", "https://api.openai.com")
            self.end_headers()
            self.wfile.write(response_body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    install(fixture[0], port=port, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    try:
        code, report = invoke(fixture, "--gateway-url", f"http://127.0.0.1:{port}")
        assert code == 2 and report["local_gateway_authorized"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("payload", [b'PRIVATE_HEALTH_BODY' * 300, b'{"status":"ready"}'])
def test_health_reply_is_bounded_and_cannot_claim_connectivity(fixture, payload):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            pytest.fail("authorization probe followed invalid health")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    install(fixture[0], port=server.server_port, model="gpt-6.1-sol", cli_version=PINNED_CLI)
    try:
        code, report = invoke(fixture, "--gateway-url", f"http://127.0.0.1:{server.server_port}")
        assert code == 2 and report["local_gateway_reachable"] is False
        assert report["local_gateway_authorized"] is False
        assert "PRIVATE_HEALTH_BODY" not in json.dumps(report)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
