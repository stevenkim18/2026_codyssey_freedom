"""Local-only safety checks; no SSH process is launched."""
import json
import hashlib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment import gpu_release
from deployment.core import DeploymentError


def test_scp_retains_pinned_options_without_shell_or_password(tmp_path, monkeypatch):
    ssh = ["ssh", "-F", "/dev/null", "-T", "-o", "StrictHostKeyChecking=yes", "-o",
           "BatchMode=no", "-p", "2222", "user@10.0.0.1", "/usr/bin/python3 -"]
    monkeypatch.setattr(gpu_release, "ssh_argv", lambda *a, **kw: ssh)
    command = gpu_release.scp_argv({}, tmp_path, tmp_path / "model.tar", "/home/user/b/model-assets.tar")
    assert command == ["scp", "-F", "/dev/null", "-o", "StrictHostKeyChecking=yes", "-o",
        "BatchMode=no", "-P", "2222", str(tmp_path / "model.tar"), "user@10.0.0.1:/home/user/b/model-assets.tar"]
    assert "-O" not in command


@pytest.mark.parametrize("result", [SimpleNamespace(returncode=1, stdout="SECRET", stderr="SECRET"),
    SimpleNamespace(returncode=0, stdout='{"status":"ready","token":"SECRET"}', stderr=""),
    SimpleNamespace(returncode=0, stdout="x" * 20000, stderr="")])
def test_remote_response_rejects_unbounded_or_unexpected_data(result):
    with pytest.raises(DeploymentError) as caught:
        gpu_release.response(result, "ready", {"status"})
    assert "SECRET" not in str(caught.value)


def test_untrusted_packet_stops_before_credentials_or_network(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_release, "load_access", lambda *a: pytest.fail("credentials accessed"))
    monkeypatch.setattr(gpu_release.prepare, "verify", lambda *a, **k: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(DeploymentError):
        gpu_release.plan(tmp_path, "f" * 64)


def test_private_output_never_overwrites(tmp_path):
    target = tmp_path / "token"
    gpu_release.private_write(target, b"synthetic")
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        gpu_release.private_write(target, b"replacement")
    assert target.read_bytes() == b"synthetic"


def test_remote_source_compiles_and_catches_raw_failures():
    source = gpu_release.remote_source({"mode": "reserve"})
    compile(source, "remote", "exec")
    assert "gpu_release_failed" in source
    assert "capture_output=True" in source
    assert "--query-compute-apps=pid" in source
    assert "rm " not in source and "sudo " not in source


@pytest.mark.parametrize("transfer_fails", [False, True])
def test_prepare_orchestration_never_activates_and_preserves_partial_evidence(tmp_path, monkeypatch, transfer_fails):
    packet = tmp_path / "packet"
    packet.mkdir()
    root = Path("/home/user/b-gpu-trial-" + "a" * 32)
    spec = SimpleNamespace(health_url="https://127.0.0.1:8766/healthz")
    monkeypatch.setattr(gpu_release, "plan", lambda *a: (packet, {"model":{"sha256":"b" * 64}}, spec, root))
    monkeypatch.setattr(gpu_release, "load_access", lambda *a: {"private_ip":"10.0.0.1", "hostname":"gpu-host"})
    monkeypatch.setattr(gpu_release, "password_session", lambda *a: nullcontext({"SYNTHETIC_AUTH_CONTEXT":"private"}))
    monkeypatch.setattr(gpu_release, "ssh_argv", lambda *a, **kw: ["ssh", "-p", "22", "user@10.0.0.1", "/usr/bin/python3 -"])
    monkeypatch.setattr(gpu_release.ssl, "create_default_context", lambda **kw: object())
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "scp":
            return SimpleNamespace(returncode=int(transfer_fails), stdout="", stderr="PRIVATE FAILURE")
        reply = {"status":"reserved"} if len(calls) == 1 else {"status":"prepared", "ca_pem":"PUBLIC_CA"}
        return SimpleNamespace(returncode=0, stdout=json.dumps(reply), stderr="")

    output = tmp_path / "private-output"
    if transfer_fails:
        with pytest.raises(DeploymentError, match="gpu_release_transfer_failed"):
            gpu_release.apply(packet, "f" * 64, output, run=run)
        assert len(calls) == 2 and not (output / "prepared.json").exists()
    else:
        assert gpu_release.apply(packet, "f" * 64, output, run=run)["model_loaded"] is False
        assert len(calls) == 3 and (output / "detector-ca.pem").read_text() == "PUBLIC_CA"
        assert json.loads((output / "prepared.json").read_text())["application_deployed"] is False
    assert len((output / "detector-token").read_text()) == 43
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert all("systemctl" not in str(command) for command, _ in calls)


@pytest.mark.parametrize("busy", [False, True])
def test_remote_reserve_enforces_gpu_idle_and_exclusive_root(tmp_path, monkeypatch, busy):
    import subprocess
    import socket
    import platform
    source = gpu_release.remote_source({})
    namespace = {}
    exec(source.split("try:\n    os.umask")[0], namespace)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(socket, "gethostname", lambda: "gpu-host")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"42\n" if busy else b""))
    root = tmp_path / ("b-gpu-trial-" + "c" * 32)
    request = {"mode":"reserve", "hostname":"gpu-host", "root":str(root), "archive_sha256":"d" * 64}
    if busy:
        with pytest.raises(ValueError): namespace["execute"](request)
        assert not root.exists()
    else:
        assert namespace["execute"](request) == {"status":"reserved"}
        with pytest.raises(FileExistsError): namespace["execute"](request)
        assert root.stat().st_mode & 0o777 == 0o700


@pytest.fixture
def release_packet(tmp_path):
    from detector.tests.test_runtime import package
    source = tmp_path / "source"
    (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# synthetic\n")
    model, _ = package(tmp_path)
    root = tmp_path / ("b-gpu-trial-" + "e" * 32)
    config = {"role":"gpu", "root":str(tmp_path / "app"), "python":"/usr/bin/python3",
        "env_file":str(root / "private.env"), "tls_ca_file":str(root / "server.pem"),
        "bind_host":"0.0.0.0", "health_url":"https://127.0.0.1:8766/healthz",
        "ready_url":"https://127.0.0.1:8766/readyz"}
    packet = tmp_path / "packet"
    manifest = gpu_release.prepare.build(source, config, packet, model=model)
    trusted = hashlib.sha256((packet / "release-manifest.json").read_bytes()).hexdigest()
    return packet, manifest, trusted, root


def test_plan_rejects_spec_changed_after_packet_verification(release_packet, monkeypatch):
    packet, manifest, trusted, root = release_packet
    original = gpu_release.prepare.verify

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        path = packet / "deploy.json"
        config = json.loads(path.read_text())
        config["python"] = "/different/python"
        path.write_text(json.dumps(config))
        return result

    monkeypatch.setattr(gpu_release.prepare, "verify", changed)
    with pytest.raises(DeploymentError, match="invalid_gpu_release_packet"):
        gpu_release.plan(packet, trusted)


def test_remote_prepare_real_openssl_env_and_verified_stager_contract(release_packet, monkeypatch):
    import platform
    import socket
    import ssl
    import subprocess
    from deployment import model_stage
    from detector.server import settings_from_env

    packet, manifest, trusted, root = release_packet
    gpu_release.plan(packet, trusted)
    namespace = {}
    exec(gpu_release.remote_source({}).split("try:\n    os.umask")[0], namespace)
    real_run = subprocess.run
    monkeypatch.setattr(Path, "home", lambda: root.parent)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(socket, "gethostname", lambda: "gpu-host")

    def run(args, **kwargs):
        if args[0] == "nvidia-smi":
            return SimpleNamespace(returncode=0, stdout=b"")
        assert args[0] == "/usr/bin/openssl"
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    request = {"mode":"reserve", "hostname":"gpu-host", "root":str(root),
        "archive_sha256":manifest["model"]["sha256"]}
    namespace["execute"](request)
    (root / "model-assets.tar").write_bytes((packet / "model-assets.tar").read_bytes())
    # Run the actual standalone metadata/archive validators. Stub only publication,
    # whose Linux renameat2 behavior is tested by model_stage's own suite.
    stage_source = Path(model_stage.__file__).read_text() + '''
def apply(archive, metadata, root, expected_host):
    release = _release(metadata)
    inspect_archive(archive, release['model'])
    assert metadata.name == 'release-manifest.json'
    assert root.name == 'models' and expected_host == 'gpu-host'
    return {'model_path':str(root/release['model']['manifest_sha256'])}
'''
    request.update(mode="prepare", manifest=manifest, private_ip="10.0.0.1", token="t" * 43,
                   port=8766, stage_source=stage_source)
    result = namespace["execute"](request)
    assert result["status"] == "prepared"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "server.pem", root / "server.key")
    ssl.create_default_context(cadata=result["ca_pem"])
    text = (root / "private.env").read_text()
    assert len(text.splitlines()) == 7 and "\\n" not in text
    env = dict(line.split("=", 1) for line in text.splitlines())
    assert env["PII_DETECTOR_TOKEN"] == "t" * 43
    assert env["PII_MODEL_PACKAGE"] == str(root / "models" / manifest["model"]["manifest_sha256"])
    assert settings_from_env(env).device == "cuda"
    assert (root / "private.env").stat().st_mode & 0o777 == 0o600
    assert "\\n" not in (root / "tls.cnf").read_text()
