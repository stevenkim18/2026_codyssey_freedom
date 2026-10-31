import asyncio
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from integration import verify_deployed_services as probe


def config():
    return {"schema_version": "1.0", "gpu": {"origin": "https://gpu.example", "token": "private-token",
            "model_version": "m1", "threshold_version": "t1"}}


def private_config(tmp_path, data=None):
    directory = tmp_path / ".private"
    directory.mkdir(mode=0o700)
    path = directory / "deployed.json"
    path.write_text(json.dumps(data or config()))
    path.chmod(0o600)
    return path


def test_private_configuration_is_loaded(tmp_path):
    assert probe.load_config(private_config(tmp_path))["gpu"]["model_version"] == "m1"


@pytest.mark.parametrize("location, mode", [("file", 0o644), ("directory", 0o755)])
def test_private_permissions_are_required(tmp_path, location, mode):
    path = private_config(tmp_path)
    (path if location == "file" else path.parent).chmod(mode)
    with pytest.raises(probe.VerificationError):
        probe.load_config(path)


@pytest.mark.parametrize("origin", ["http://gpu.example", "https://user:private@gpu.example", "https://gpu.example/path",
                                    "https://gpu.example?x=private", "https://gpu.example#private", "https://gpu.example\\evil", "https://gpu.example:0"])
def test_unsafe_destinations_are_rejected_before_requests(tmp_path, origin):
    data = config()
    data["gpu"]["origin"] = origin
    with pytest.raises(probe.VerificationError) as error:
        probe.load_config(private_config(tmp_path, data))
    assert "private" not in str(error.value)


def test_unknown_config_keys_are_rejected(tmp_path):
    with pytest.raises(probe.VerificationError):
        probe.load_config(private_config(tmp_path, config() | {"verify_tls": False}))


def test_absolute_ca_path_can_contain_spaces(tmp_path):
    data = config()
    data["gpu"]["ca_file"] = str(tmp_path / "test CA.pem")
    assert probe.load_config(private_config(tmp_path, data))["gpu"]["ca_file"] == data["gpu"]["ca_file"]


def handler(request):
    if request.url.path == "/healthz":
        return httpx.Response(200, json={"status": "alive"})
    if request.url.path == "/readyz":
        return httpx.Response(200, json={"status": "ready"})
    if request.headers.get("authorization") != "Bearer private-token":
        return httpx.Response(401, json={"status": "error", "error_code": "unauthorized"})
    body = json.loads(request.content)
    return httpx.Response(200, json={"schema_version": "1.0", "request_id": body["request_id"],
        "inspection_id": body["inspection_id"], "status": "complete", "findings": [],
        "model_version": "m1", "threshold_version": "t1", "timing_ms": 1.0,
        "timing_breakdown_ms": {"queue": 0.0, "inference": 1.0, "rules": 0.0, "total": 1.0}})


def exercise(data, callback=handler):
    async def work():
        async with httpx.AsyncClient(transport=httpx.MockTransport(callback), trust_env=False) as client:
            return await probe.verify_gpu(client, data["gpu"])
    return asyncio.run(work())


def test_gpu_probe_checks_auth_and_version_without_reporting_credentials():
    report = exercise(config())
    assert report["unauthorized_status"] == 401 and report["inspection"]["status"] == "complete"
    assert "private-token" not in json.dumps(report)


@pytest.mark.parametrize("change", [{"request_id": "stale"}, {"model_version": "stale"},
                                    {"threshold_version": "stale"}, {"status": "incomplete"},
                                    {"timing_ms": float("inf")},
                                    {"findings": [{"segment_id": "wrong", "type": "PERSON", "start": 0,
                                                   "end": 999, "score": 0.5, "detector": "model"}]}])
def test_bad_inspection_is_rejected(change):
    def changed(request):
        response = handler(request)
        if request.url.path == "/v1/inspect" and response.status_code == 200:
            return httpx.Response(200, content=json.dumps(response.json() | change), headers={"Content-Type": "application/json"})
        return response
    with pytest.raises(probe.VerificationError):
        exercise(config(), changed)


def test_inspection_201_is_not_misreported_as_200():
    def changed(request):
        response = handler(request)
        if request.url.path == "/v1/inspect" and response.status_code == 200:
            return httpx.Response(201, json=response.json())
        return response
    with pytest.raises(probe.VerificationError):
        exercise(config(), changed)


def test_redirect_never_receives_a_second_request_or_token():
    seen = []
    def redirect(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example"})
    with pytest.raises(probe.VerificationError):
        exercise(config(), redirect)
    assert len(seen) == 1 and "authorization" not in seen[0].headers


@pytest.mark.parametrize("body", [b'{"status":"alive","status":"alive"}', b'{"status":NaN}',
                                  b'{"status":1e999}', b' ' * (1024 * 1024 + 1)])
def test_non_strict_or_oversized_health_json_is_rejected(body):
    with pytest.raises(probe.VerificationError):
        exercise(config(), lambda request: httpx.Response(200, content=body, headers={"Content-Type": "application/json"}))


def test_wrong_auth_cannot_succeed_or_echo_secret():
    def incorrect(request):
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json={"private": "private-token"})
        return handler(request)
    with pytest.raises(probe.VerificationError) as error:
        exercise(config(), incorrect)
    assert "private-token" not in str(error.value)


def test_general_role_only_uses_explicit_get_contract():
    service = {"origin": "https://general.example", "token": "private-token", "health": {
        "path": "/health", "status": 200, "json": {"ok": True}, "authentication": "none"}, "ready": {
        "path": "/ready", "status": 200, "json": {"ready": True}, "authentication": "bearer"}}
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True} if request.url.path == "/health" else {"ready": True})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await probe.verify_general(client, service)
    report = asyncio.run(run())
    assert report["contract_only"] is True and all(request.method == "GET" for request in seen)
    assert "authorization" not in seen[0].headers and seen[1].headers["authorization"] == "Bearer private-token"


def test_general_expected_json_does_not_equate_boolean_with_number():
    service = {"origin": "https://general.example", "health": {
        "path": "/health", "status": 200, "json": {"ok": True}, "authentication": "none"}, "ready": {
        "path": "/ready", "status": 200, "json": {"ok": True}, "authentication": "none"}}
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": 1}))) as client:
            return await probe.verify_general(client, service)
    with pytest.raises(probe.VerificationError):
        asyncio.run(run())


def test_ca_error_is_sanitized_and_never_disables_verification(tmp_path):
    data = config()
    data["gpu"]["ca_file"] = str(tmp_path / "private-missing-ca.pem")
    with pytest.raises(probe.VerificationError) as error:
        probe.tls_context(data["gpu"])
    assert "private-missing" not in str(error.value)


def test_authenticated_redirect_does_not_forward_real_token():
    seen = []
    def redirect(request):
        seen.append(request)
        if request.url.path == "/v1/inspect" and request.headers.get("authorization") == "Bearer private-token":
            return httpx.Response(307, headers={"Location": "https://attacker.example/v1/inspect"})
        return handler(request)
    with pytest.raises(probe.VerificationError):
        exercise(config(), redirect)
    assert len(seen) == 4 and all(request.url.host == "gpu.example" for request in seen)


def test_private_config_symlink_is_refused(tmp_path):
    path = private_config(tmp_path)
    link = path.parent / "linked.json"
    link.symlink_to(path)
    with pytest.raises(probe.VerificationError):
        probe.load_config(link)


def test_loopback_https_cli_verifies_pinned_ca_and_rejects_untrusted_ca(tmp_path):
    """Actual TLS+CLI path, synthetic server only; no trust-store edits or models."""
    # Keep cert/key/evidence independently of pytest's automatic retention cleanup.
    tmp_path = Path(tempfile.mkdtemp(prefix="deployed-tls-fixture-"))
    certificate, key = tmp_path / "fixture-cert.pem", tmp_path / "fixture-key.pem"
    openssl_config = tmp_path / "openssl.cnf"
    openssl_config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                              "[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n"
                              "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n")
    subprocess.run(["/usr/bin/openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(key), "-out", str(certificate), "-config", str(openssl_config)],
                   capture_output=True, check=True, timeout=20)
    key.chmod(0o600)
    requests = []
    class FixtureHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def handle_request(self):
            requests.append((self.command, self.path))
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = httpx.Request(self.command, "https://gpu.example" + self.path,
                                    headers=dict(self.headers), content=body)
            response = handler(request)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        do_GET = handle_request
        do_POST = handle_request
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = config() | {"test_only": True}
        data["gpu"].update(origin=f"https://127.0.0.1:{server.server_port}", ca_file=str(certificate))
        path = private_config(tmp_path, data)
        output = tmp_path / "tls-evidence.json"
        environment = os.environ | {"HTTPS_PROXY": "http://127.0.0.1:1", "ALL_PROXY": "http://127.0.0.1:1"}
        command = [sys.executable, "-m", "integration.verify_deployed_services", str(path), "--output", str(output)]
        completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], env=environment,
                                   capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(output.read_text())
        assert report["test_only"] is True and report["tls_verified"] is True and len(requests) == 4
        assert "private-token" not in output.read_text() + completed.stdout + completed.stderr
        del data["gpu"]["ca_file"]
        path.write_text(json.dumps(data))
        bad_output = tmp_path / "tls-untrusted-evidence.json"
        failed = subprocess.run(command[:-1] + [str(bad_output)], cwd=Path(__file__).resolve().parents[2],
                                env=environment, capture_output=True, text=True, timeout=30)
        assert failed.returncode == 1 and len(requests) == 4
        assert json.loads(bad_output.read_text())["tls_verified"] is False
        assert "private-token" not in failed.stdout + failed.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
