"""Independent native bundle check: actual macOS sandbox, synthetic loopback TLS.

Controller uses py312; all installed-app invocations have no Python on PATH.
The temporary home, installed bundle, credentials, processes and TLS files are
removed even on failure. No real detector, provider or user account is used.
"""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def verify(archive, digest, codex):
    repository = Path(__file__).resolve().parents[2]
    checks = []
    server = thread = child = None
    detector_calls = []
    with tempfile.TemporaryDirectory(prefix="pii-native-verification-") as temporary:
        root = Path(temporary).resolve()
        for name in ("home", "tmp", "bin", "config"):
            (root / name).mkdir(mode=0o700)
        shutil.copy2(codex, root / "bin/codex")
        policy = root / "policy.sb"
        policy.write_text('(version 1)(allow default)(deny network*)'
            '(allow network* (remote ip "localhost:*") (local ip "localhost:*"))'
            '(deny file-write*)(allow file-write* (literal "/dev/null") (subpath "' + str(root) + '"))')
        env = {"HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
               "PATH": str(root / "bin") + ":/usr/bin:/bin", "LANG": "en_US.UTF-8"}
        prefix = ["/usr/bin/sandbox-exec", "-f", str(policy)]
        def run(command, expected=0, timeout=40, input=None):
            result = subprocess.run(prefix + list(map(str, command)), cwd=root,
                                    env=env, capture_output=True, text=True, timeout=timeout, input=input)
            if expected is not None and result.returncode != expected:
                raise AssertionError({"command": str(command[0]), "code": result.returncode,
                                      "stderr": result.stderr[-1600:], "stdout": result.stdout[-800:]})
            return result
        try:
            destination = root / "installed"
            run(["/bin/bash", repository / "dev/launcher/plugin/scripts/install-macos.sh",
                 "--archive", archive, "--sha256", digest, "--destination", destination], timeout=90)
            executable = destination / "pii-gateway"
            version = json.loads(run([executable, "version"]).stdout)
            assert version["version"] == "0.1.0-engineering.1"
            assert "configure" in run([executable, "--help"]).stdout
            checks += ["checksum_installer", "cold_version", "cold_help", "no_python_path"]
            interactive = root / "config/interactive.json"
            configured = run([executable, "--settings", interactive, "configure"],
                input="https://127.0.0.1:9876\n\n\nsynthetic-detector\nsynthetic-provider\n")
            assert interactive.stat().st_mode & 0o777 == 0o600
            assert json.loads(interactive.read_text())["model_id"] == "gpt-6.1-sol"
            assert "synthetic-detector" not in configured.stdout + configured.stderr
            assert "synthetic-provider" not in configured.stdout + configured.stderr
            checks.append("native_configure_private_no_secret_output")
            print(json.dumps({"progress": checks}), flush=True)

            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-loopback")])
            now = datetime.now(timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                    .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                    .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                    .sign(key, hashes.SHA256()))
            ca = root / "ca.pem"
            ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            keyfile = root / "key.pem"
            keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            keyfile.chmod(0o600)

            class Detector(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def do_POST(self):
                    assert self.headers["Authorization"] == "Bearer synthetic-detector"
                    data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    findings = []
                    for segment in data["segments"]:
                        index = segment["text"].find("900101-1234567")
                        if index >= 0:
                            findings.append({"segment_id": segment["segment_id"], "type": "KR_RRN",
                                "start": index, "end": index + 14, "score": 1.0, "detector": "rule"})
                    detector_calls.append(data["direction"])
                    body = json.dumps({"schema_version": "1.0", "request_id": data["request_id"],
                        "inspection_id": data["inspection_id"], "status": "complete", "findings": findings,
                        "model_version": "synthetic-m0", "threshold_version": "synthetic-v1",
                        "timing_ms": 1.0, "timing_breakdown_ms": {}, "error_code": None}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Detector)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(ca, keyfile)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            settings = root / "config/client.json"
            settings.write_text(json.dumps({"local_token": "synthetic-local", "detector_token": "synthetic-detector",
                "openai_api_key": "synthetic-unused-provider", "model_id": "gpt-6.1-sol", "port": str(port),
                "detector_url": f"https://127.0.0.1:{server.server_port}/v1/inspect", "detector_ca_file": str(ca),
                "state_dir": str(root / "state"), "codex_home": str(root / "codex-home")}))
            settings.chmod(0o600)
            command = [executable, "--settings", settings]
            run(command + ["install"])
            checks.append("real_codex_profile_install")
            run(command + ["doctor", "--probe"], expected=2)
            checks.append("offline_doctor_refusal")
            settings.chmod(0o644)
            run(command + ["doctor"], expected=2)
            settings.chmod(0o600)
            checks.append("unsafe_settings_permissions_refusal")
            child = subprocess.Popen(prefix + list(map(str, command + ["serve"])), cwd=root,
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 15
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while True:
                if child.poll() is not None:
                    raise AssertionError("native serve exited: " + child.stderr.read()[-1200:])
                try:
                    with opener.open(f"http://127.0.0.1:{port}/healthz", timeout=.3) as response:
                        assert json.load(response) == {"status": "alive"}
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise AssertionError("native serve health timeout")
                    time.sleep(.1)
            report = json.loads(run(command + ["doctor", "--probe"]).stdout)
            assert report["local_gateway_authorized"] is True
            checks += ["native_serve_health", "authorized_doctor_probe"]
            print(json.dumps({"progress": checks}), flush=True)
            result = run(command + ["run", "--", "exec", "--ephemeral", "--skip-git-repo-check",
                "--sandbox", "read-only", "--json", "Read 900101-1234567 without tools."], expected=None)
            output = result.stdout + result.stderr
            assert result.returncode != 0 and "POLICY_BLOCK" in output, output[-1600:]
            assert "900101-1234567" not in output
            assert detector_calls and all(value == "outbound" for value in detector_calls)
            checks.append("real_codex_outbound_policy_block")
            run(command + ["uninstall"])
            checks.append("profile_uninstall")
        finally:
            if child is not None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                if child.stderr:
                    child.stderr.close()
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=5)
    assert not root.exists()
    return {"checks": checks, "passed": len(checks), "sandbox": "macOS sandbox-exec",
            "application_path_python": False, "external_network": "denied",
            "external_api_calls": 0, "temporary_files_removed": True,
            "remote_model_quality": "unverified", "artifact_sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--codex", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive.resolve(), args.sha256, args.codex.resolve()), indent=2))
