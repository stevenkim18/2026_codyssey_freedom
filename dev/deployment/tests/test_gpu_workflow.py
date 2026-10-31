"""Offline tests for dynamic GPU-home discovery and frozen packet orchestration."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from deployment import gpu_workflow
from deployment.core import DeploymentError, bundle


def test_discovery_uses_remote_home_without_username_guess(monkeypatch):
    monkeypatch.setattr(gpu_workflow, "load_access", lambda *a: {"hostname":"gpu-host"})
    monkeypatch.setattr(gpu_workflow, "ssh_argv", lambda *a, **kw: ["ssh", "pinned"])
    monkeypatch.setattr(gpu_workflow, "password_session", lambda *a: nullcontext({}))
    expected = {"status":"discovered", "home":"/srv/users/actual-home",
        "python":"/srv/users/actual-home/miniforge3/envs/py312/bin/python",
        "python_version":"3.12.8", "gpu_compute_count":0}
    calls = []
    def run(command, **kwargs):
        calls.append(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(expected), stderr="")
    assert gpu_workflow.discover(run=run) == expected
    assert "import torch" not in calls[0] and "Path.home()" in calls[0]


@pytest.mark.parametrize("patch", [{"home":"/unsafe/../home"}, {"gpu_compute_count":1},
    {"gpu_compute_count":False}, {"python":"/other/env/bin/python"}, {"python_version":"3.11.8"},
    {"unexpected":"PRIVATE"}])
def test_discovery_rejects_unsafe_records(patch):
    record = {"status":"discovered", "home":"/srv/home", "python":"/srv/home/miniforge3/envs/py312/bin/python",
        "python_version":"3.12.8", "gpu_compute_count":0} | patch
    with pytest.raises(DeploymentError): gpu_workflow.validate_discovery(record)


def test_frozen_source_rebundles_identically(tmp_path):
    source = tmp_path / "source"
    (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# synthetic\n")
    data, digest = bundle(source, "gpu")
    frozen = tmp_path / "frozen"
    gpu_workflow.freeze_source(data, digest, frozen)
    assert bundle(frozen, "gpu")[1] == digest


@pytest.mark.parametrize("name,kind", [("../escape.py", tarfile.REGTYPE), ("detector/link.py", tarfile.SYMTYPE)])
def test_frozen_source_rejects_unsafe_members(tmp_path, name, kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        member.linkname = "/outside"
        archive.addfile(member)
    data = stream.getvalue()
    with pytest.raises(DeploymentError):
        gpu_workflow.freeze_source(data, hashlib.sha256(data).hexdigest(), tmp_path / "frozen")


@pytest.mark.parametrize("mode", ["prepare", "deploy"])
def test_workflow_freezes_source_inside_original_tree_and_reuses_trusted_packet(tmp_path, monkeypatch, mode):
    from detector.tests.test_runtime import package
    source = tmp_path / "source"
    (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# synthetic before\n")
    model, _ = package(source)
    private = source / ".private"
    private.mkdir(mode=0o700)
    output = private / "new-case"
    record = {"status":"discovered", "home":"/srv/users/actual-home",
        "python":"/srv/users/actual-home/miniforge3/envs/py312/bin/python",
        "python_version":"3.12.8", "gpu_compute_count":0}
    discoveries, operations = [], []
    def discover(**kwargs):
        discoveries.append(True)
        # Mutating live sources after the initial bundle cannot alter the packet.
        (source / "detector/__main__.py").write_text("# synthetic after\n")
        return dict(record)
    monkeypatch.setattr(gpu_workflow, "discover", discover)
    def apply(packet, trusted, connection, **kwargs):
        gpu_workflow.prepare.verify(packet, expected_manifest_sha256=trusted)
        operations.append(("prepare", str(packet), trusted))
        return {"status":"prepared"}
    monkeypatch.setattr(gpu_workflow.gpu_release, "apply", apply)
    def cli(args, **kwargs):
        operations.append((args[0], args[args.index("--packet") + 1], args[args.index("--expected-manifest-sha256") + 1]))
        assert "--password-auth" in args
        return 0
    monkeypatch.setattr(gpu_workflow.deployment_cli, "main", cli)
    persistence_calls = []
    monkeypatch.setattr(gpu_workflow, "ensure_persistence", lambda record, **kwargs: persistence_calls.append((kwargs["enable_unit"], [op[0] for op in operations])))
    result = gpu_workflow.workflow(mode, source, model, output)
    assert result["application_deployed"] == (mode == "deploy")
    assert [op[0] for op in operations] == (["prepare", "preflight", "deploy", "verify"] if mode == "deploy" else ["prepare"])
    assert len(discoveries) == (3 if mode == "deploy" else 1)
    assert persistence_calls == ([(False, ["prepare"]), (True, ["prepare", "preflight", "deploy", "verify"])] if mode == "deploy" else [])
    assert len({(op[1], op[2]) for op in operations}) == 1
    context = json.loads((output / "context.json").read_text())
    assert context["home"] == record["home"]
    config = json.loads((output / "packet/deploy.json").read_text())
    assert config["root"] == record["home"] + "/codyssey-pii-gpu"
    assert config["python"] == record["python"]
    assert config["env_file"].startswith(record["home"] + "/b-gpu-trial-")
    assert (output / "frozen-source/detector/__main__.py").read_text() == "# synthetic before\n"
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize("missing", [None, "google.protobuf", "sentencepiece"])
def test_discovery_source_executes_locally_without_importing_model(tmp_path, monkeypatch, capsys, missing):
    import platform
    import socket
    import subprocess
    home = tmp_path / "nonusername-home"
    python = home / "miniforge3/envs/py312/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("synthetic executable")
    home.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(socket, "gethostname", lambda: "gpu-host")
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        if args[0] != "/usr/bin/nvidia-smi" and missing:
            assert missing in args[-1]
            return SimpleNamespace(returncode=1, stdout="")
        output = "" if args[0] == "/usr/bin/nvidia-smi" else json.dumps({"version":[3,12,8], "prefix":str(python.parent.parent)})
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(subprocess, "run", run)
    if missing:
        with pytest.raises(SystemExit):
            exec("EXPECTED='gpu-host'\n" + gpu_workflow.DISCOVERY_SOURCE, {})
        assert json.loads(capsys.readouterr().out) == {"status":"error", "code":"gpu_runtime_probe_failed"}
        return
    exec("EXPECTED='gpu-host'\n" + gpu_workflow.DISCOVERY_SOURCE, {})
    result = json.loads(capsys.readouterr().out)
    assert gpu_workflow.validate_discovery(result)["home"] == str(home)
    assert len(commands) == 2 and "torch" not in str(commands)
    assert "google.protobuf" in commands[1][-1] and "sentencepiece" in commands[1][-1]


@pytest.mark.parametrize("initial,enable,deny", [("yes",False,False), ("no",False,False), ("yes",True,False), ("no",False,True)])
def test_persistence_changes_only_own_linger_and_fixed_b_unit(tmp_path, monkeypatch, capsys, initial, enable, deny):
    import os
    import platform
    import socket
    import subprocess
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(socket, "gethostname", lambda: "gpu-host")
    state = {"linger":initial, "enabled":False}
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        assert kwargs["timeout"] == 20
        if args[:2] == ["loginctl", "show-user"]:
            assert args == ["loginctl", "show-user", str(os.getuid()), "--property=Linger", "--value"]
            return SimpleNamespace(returncode=0, stdout=state["linger"] + "\n")
        if args[:2] == ["loginctl", "enable-linger"]:
            assert args == ["loginctl", "enable-linger", str(os.getuid())]
            if not deny: state["linger"] = "yes"
            return SimpleNamespace(returncode=int(deny), stdout="")
        assert args in (["systemctl", "--user", "enable", "codyssey-pii-gpu.service"],
                        ["systemctl", "--user", "is-enabled", "codyssey-pii-gpu.service"])
        if args[2] == "enable": state["enabled"] = True
        return SimpleNamespace(returncode=0, stdout="enabled\n" if state["enabled"] else "disabled\n")
    monkeypatch.setattr(subprocess, "run", run)
    source = gpu_workflow.persistence_source({"home":str(tmp_path)}, "gpu-host", enable)
    if deny:
        with pytest.raises(SystemExit): exec(source, {})
        assert not any(args[0] == "systemctl" for args in commands)
    else:
        exec(source, {})
        assert json.loads(capsys.readouterr().out)["status"] == "persistent"
        assert state["linger"] == "yes" and state["enabled"] == enable
        if initial == "yes": assert not any(args[1] == "enable-linger" for args in commands)


def test_persistence_failure_stops_before_application_start(tmp_path, monkeypatch):
    from detector.tests.test_runtime import package
    source = tmp_path / "source"
    (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# synthetic\n")
    model, _ = package(tmp_path)
    record = {"status":"discovered", "home":"/srv/home", "python":"/srv/home/miniforge3/envs/py312/bin/python",
        "python_version":"3.12.8", "gpu_compute_count":0}
    monkeypatch.setattr(gpu_workflow, "discover", lambda **kw: record)
    monkeypatch.setattr(gpu_workflow.gpu_release, "apply", lambda *a, **kw: None)
    monkeypatch.setattr(gpu_workflow.deployment_cli, "main", lambda *a, **kw: pytest.fail("service started"))
    def failed(*a, **kw): raise DeploymentError("gpu_persistence_failed")
    monkeypatch.setattr(gpu_workflow, "ensure_persistence", failed)
    output = tmp_path / "output"
    with pytest.raises(DeploymentError, match="gpu_persistence_failed"):
        gpu_workflow.workflow("deploy", source, model, output)
    assert not (output / "result.json").exists()
