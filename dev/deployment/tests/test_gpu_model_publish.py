"""Local-only publish boundary tests; no SSH or GPU work."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from deployment import gpu_model_publish
from deployment.core import DeploymentError


def test_explicit_engineering_permission_required_before_private_reads(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_model_publish, "load_context", lambda *a: pytest.fail("private read"))
    with pytest.raises(DeploymentError):
        gpu_model_publish.publish(tmp_path, "/model", "a" * 64)


@pytest.mark.parametrize("value", [{"status":"activated", "token":"SECRET"},
    {"status":"failed", "code":"RAW_SECRET", "rollback":"success"},
    {"status":"activated", "quality_approved":True}, {"status":"failed", "code":"activation_failed", "rollback":"unknown"}])
def test_report_is_fixed_allowlist(value):
    with pytest.raises(DeploymentError) as caught:
        gpu_model_publish.validate_report(value)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("status", ["already_active", "activated", "failed"])
def test_publish_uses_trusted_sources_and_fixed_result(monkeypatch, tmp_path, status):
    context = {"home":"/srv/home", "python":"/srv/home/miniforge3/envs/py312/bin/python",
        "trial":"/srv/home/b-gpu-trial-" + "a" * 32, "application_sha256":"b" * 64}
    spec = SimpleNamespace(root="/srv/home/codyssey-pii-gpu", health_url="https://127.0.0.1:8766/healthz")
    monkeypatch.setattr(gpu_model_publish, "load_context", lambda *a: (context, spec))
    monkeypatch.setattr(gpu_model_publish, "load_access", lambda *a: {"hostname":"gpu-host"})
    monkeypatch.setattr(gpu_model_publish, "ssh_argv", lambda *a, **kw: ["ssh", a[2]])
    monkeypatch.setattr(gpu_model_publish, "password_session", lambda *a: nullcontext({}))
    report = {"status":status}
    if status == "activated": report["quality_approved"] = False
    if status == "failed": report.update(code="activation_failed", rollback="success")
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps(report), stderr="PRIVATE")
    assert gpu_model_publish.publish(tmp_path, "/srv/export/model", "c" * 64,
        allow_engineering_test=True, run=run) == report
    assert calls[0][0] == ["ssh", context["python"]]
    compile(calls[0][1], "remote", "exec")
    assert "checked_release" in calls[0][1] and "managed.json" in calls[0][1]


def test_context_directory_must_be_private(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(DeploymentError): gpu_model_publish.load_context(tmp_path)


@pytest.mark.parametrize("tamper", [None, "trial", "digest", "mode", "symlink"])
def test_context_binds_verified_packet_and_private_paths(tmp_path, monkeypatch, tamper):
    home = "/srv/home"
    trial = home + "/b-gpu-trial-" + "a" * 32
    context = {"schema_version":"1.0", "packet":str(tmp_path / "packet"), "trusted_sha256":"b" * 64,
        "home":home, "python":home + "/miniforge3/envs/py312/bin/python", "trial":trial,
        "application_sha256":"c" * 64}
    spec = SimpleNamespace(role="gpu", device="cuda", root=home + "/codyssey-pii-gpu", python=context["python"],
        env_file=trial + "/private.env", tls_ca_file=trial + "/server.pem", health_url="https://127.0.0.1:8766/healthz")
    monkeypatch.setattr(gpu_model_publish, "_packet_inputs", lambda *a: (spec, b"", "c" * 64))
    if tamper == "trial": context["trial"] = "/different/b-gpu-trial-" + "a" * 32
    if tamper == "digest": context["application_sha256"] = "d" * 64
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context)); path.chmod(0o600)
    if tamper == "mode": path.chmod(0o644)
    if tamper == "symlink":
        path.rename(tmp_path / "other.json")
        path.symlink_to(tmp_path / "other.json")
    if tamper is None:
        assert gpu_model_publish.load_context(tmp_path) == (context, spec)
    else:
        with pytest.raises(DeploymentError): gpu_model_publish.load_context(tmp_path)


def test_fresh_publish_process_never_changes_immutable_release_membership(tmp_path):
    from deployment import remote
    root = tmp_path / "codyssey-pii-gpu"
    release = root / "releases" / ("c" * 64)
    (release / "detector").mkdir(parents=True)
    root.chmod(0o700)
    files = {"detector/__init__.py": b"", "detector/runtime.py": b"IDENTITY = 'synthetic'\n"}
    for name, data in files.items():
        (release / name).write_bytes(data)
    (release / "deployment-manifest.json").write_text(json.dumps({"manager":"codyssey-pii-v1",
        "role":"gpu", "sha256":release.name,
        "files":{name:hashlib.sha256(data).hexdigest() for name, data in files.items()}}))
    (root / "managed.json").write_text(json.dumps({"manager":"codyssey-pii-v1", "role":"gpu"}))
    (root / "current").symlink_to(release)
    request = {"hostname":"synthetic-host", "home":str(tmp_path), "application_root":str(root),
        "application_sha256":release.name, "trial":str(tmp_path / "trial"), "candidate":str(tmp_path / "model"),
        "manifest_sha256":"d" * 64, "origin":"https://127.0.0.1:8766",
        "remote_source":Path(remote.__file__).read_text(),
        "activation_source":"def activate(*a, **kw):\n    from detector import runtime\n    assert runtime.IDENTITY == 'synthetic'\n    return {'status':'already_active'}\n"}
    source = gpu_model_publish.remote_source(request)
    # A genuinely new isolated interpreter imports the release on every call;
    # the pytest parent's cached detector modules cannot mask bytecode writes.
    bootstrap = ("from pathlib import Path\nfrom unittest.mock import patch\n"
        "with patch('platform.system',return_value='Linux'), patch('socket.gethostname',return_value='synthetic-host'), "
        "patch.object(Path,'home',return_value=Path(" + repr(str(tmp_path)) + ")):\n    exec(" + repr(source) + ")\n")
    original = {str(path.relative_to(release)) for path in release.rglob("*")}
    for _ in range(2):
        result = subprocess.run([sys.executable, "-I", "-c", bootstrap], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stdout
        assert json.loads(result.stdout) == {"status":"already_active"}
        assert {str(path.relative_to(release)) for path in release.rglob("*")} == original
        assert remote.checked_release(root, str(release), "gpu") == release
