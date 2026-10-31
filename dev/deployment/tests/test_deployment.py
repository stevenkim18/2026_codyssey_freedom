"""R11/T07/T10 deployment safety, synthetic filesystem and SSH only."""
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from deployment.core import DeploymentError, Spec, bundle, ssh_argv, load_access
from deployment.remote import validate_archive, execute


def spec(tmp_path, role="gpu", **changes):
    return Spec.parse(dict(role=role, root=str(tmp_path / "managed"), python="/opt/conda/envs/py312/bin/python",
        env_file=str(tmp_path / "service.env"), device="cpu") | changes)


def test_general_contract_is_required(tmp_path):
    with pytest.raises(DeploymentError):
        spec(tmp_path, "general")

@pytest.mark.parametrize("change", [{"python":"/bin/python;touch /tmp/pwn"}, {"root":"/tmp/x\ny"},
    {"module":"detector;id"}, {"health_url":"http://evil.test/health"}, {"device":"auto"}, {"role":"other"}])
def test_unsafe_specs_rejected(tmp_path, change):
    with pytest.raises(DeploymentError):
        spec(tmp_path, **change)


def test_bundle_excludes_secrets_weights_and_cache(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "detector").mkdir()
    (source / "detector/__init__.py").write_text("")
    (source / "detector/.env").write_text("SECRET")
    (source / "detector/weights.bin").write_bytes(b"WEIGHTS")
    (source / "detector/.cache").mkdir()
    (source / "detector/.cache/secret.py").write_text("SECRET")
    data, digest = bundle(source, "gpu")
    names = [m.name for m in validate_archive(data, digest)]
    assert names == ["detector/__init__.py"]
    assert b"SECRET" not in data

@pytest.mark.parametrize("name,kind", [("../escape.py",tarfile.REGTYPE),("/escape.py",tarfile.REGTYPE),
    ("detector/link.py",tarfile.SYMTYPE),("detector/.env",tarfile.REGTYPE)])
def test_hostile_bundle_rejected(name, kind):
    import hashlib
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tf:
        item=tarfile.TarInfo(name); item.type=kind; item.linkname="/secret"
        tf.addfile(item)
    data=stream.getvalue()
    with pytest.raises(Exception):
        validate_archive(data, hashlib.sha256(data).hexdigest())


def test_ssh_pins_known_hosts_and_no_secrets_in_argv(tmp_path):
    known=tmp_path / "known_hosts"; known.write_text("pinned"); known.chmod(0o600)
    args=ssh_argv({"private_ip":"10.0.0.2","username":"worker","ssh_port":22,"password":"SECRET"}, known, "/opt/py312/bin/python")
    assert "StrictHostKeyChecking=yes" in args
    assert not any("SECRET" in item for item in args)
    assert args[-1] == "/opt/py312/bin/python -"
    with pytest.raises(DeploymentError):
        ssh_argv({"private_ip":"-oProxyCommand=evil","username":"worker","ssh_port":22}, known, "/opt/py312/bin/python")


def test_prerequisite_failure_has_no_mutation(tmp_path):
    config=spec(tmp_path).as_dict()
    def fail(*args): raise RuntimeError("PRIVATE")
    with pytest.raises(Exception):
        execute({"mode":"deploy","spec":config}, prerequisite=fail)
    assert not Path(config["root"]).exists()


def deployment_request(tmp_path, marker="one"):
    import base64
    source = tmp_path / ("source-"+marker)
    (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# "+marker+"\n")
    data, digest = bundle(source, "gpu")
    return {"mode":"deploy", "role":"gpu", "spec":spec(tmp_path, timeout=1).as_dict(),
            "bundle":base64.b64encode(data).decode(), "sha256":digest}


def test_failed_readiness_restores_previous_release_and_preserves_all_files(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(remote, "wait_ready", lambda config, check: check(config))
    commands=[]
    control=lambda args: commands.append(args) or "active"
    first=deployment_request(tmp_path)
    assert execute(first, prerequisite=lambda _: {}, control=control, check=lambda _: True)["status"] == "deployed"
    root=Path(first["spec"]["root"])
    previous=(root / "current").resolve()
    (root / "keep-data.txt").write_text("preserve")
    second=deployment_request(tmp_path, "two")
    health=iter([True, False, True])
    with pytest.raises(Exception, match="deploy_failed_previous_restored"):
        execute(second, prerequisite=lambda _: {}, control=control, check=lambda _: next(health))
    assert (root / "current").resolve() == previous
    assert (root / "keep-data.txt").read_text() == "preserve"
    assert (root / "releases" / second["sha256"] / "detector/__main__.py").is_file()
    assert not any("rm" in command for command in commands)


def test_explicit_rollback_switches_to_recorded_previous_release(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    first=deployment_request(tmp_path)
    second=deployment_request(tmp_path, "two")
    for request in (first, second):
        execute(request, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: True)
    result=execute(second | {"mode":"rollback"}, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: True)
    assert result["release"] == first["sha256"]
    root=Path(first["spec"]["root"])
    assert len(list((root / "releases").iterdir())) == 2


def test_unmanaged_unit_is_preserved(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    unit=tmp_path / "home/.config/systemd/user/codyssey-pii-gpu.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("other owner")
    request=deployment_request(tmp_path)
    with pytest.raises(Exception, match="unmanaged_or_changed_unit"):
        execute(request, prerequisite=lambda _: {})
    assert unit.read_text() == "other owner" and not Path(request["spec"]["root"]).exists()


def test_general_missing_contract_and_mismatched_role_never_calls_ssh(tmp_path, capsys):
    from deployment.__main__ import main
    def forbidden(*args, **kwargs):
        pytest.fail("SSH must not execute")
    assert main(["deploy", "--role", "general"], run=forbidden) == 2
    config=tmp_path / "spec.json"; config.write_text(json.dumps(spec(tmp_path).as_dict()))
    assert main(["deploy", "--role", "general", "--spec", str(config)], run=forbidden) == 2
    assert "Traceback" not in capsys.readouterr().out


def test_archive_digest_and_wrong_role_rejected(tmp_path):
    import base64
    request=deployment_request(tmp_path)
    data=base64.b64decode(request["bundle"])
    with pytest.raises(Exception):
        validate_archive(data, "0"*64, "gpu")
    with pytest.raises(Exception):
        validate_archive(data, request["sha256"], "general")


def test_missing_entrypoint_has_no_mutation(tmp_path):
    import base64
    source=tmp_path / "source"; (source / "detector").mkdir(parents=True)
    (source / "detector/__init__.py").write_text("")
    data,digest=bundle(source,"gpu")
    config=spec(tmp_path).as_dict()
    with pytest.raises(Exception, match="service_entrypoint_missing"):
        execute({"mode":"deploy","role":"gpu","spec":config,"bundle":base64.b64encode(data).decode(),"sha256":digest}, prerequisite=lambda _: {})
    assert not Path(config["root"]).exists()


def test_unmanaged_root_fails_without_changes(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    request=deployment_request(tmp_path)
    root=Path(request["spec"]["root"]); root.mkdir(); (root / "other-owner").write_text("keep")
    with pytest.raises(Exception, match="unmanaged_root"):
        execute(request, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: True)
    assert list(root.iterdir()) == [root / "other-owner"]


def test_first_failed_deploy_can_be_retried_without_removing_failed_release(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(remote, "wait_ready", lambda config, check: check(config))
    first=deployment_request(tmp_path)
    with pytest.raises(Exception, match="deploy_failed_no_previous_release"):
        execute(first, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: False)
    assert not (Path(first["spec"]["root"]) / "current").is_symlink()
    second=deployment_request(tmp_path,"two")
    assert execute(second, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: True)["status"] == "deployed"
    assert (Path(first["spec"]["root"]) / "releases" / first["sha256"]).is_dir()


def test_rollback_restart_failure_restores_current_and_state(tmp_path, monkeypatch):
    import deployment.remote as remote
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    first=deployment_request(tmp_path); second=deployment_request(tmp_path,"two")
    for request in (first,second):
        execute(request, prerequisite=lambda _: {}, control=lambda _: "active", check=lambda _: True)
    calls=[]
    def control(args):
        calls.append(args)
        if len(calls) == 1: raise RuntimeError("synthetic restart failure")
        return "active"
    with pytest.raises(Exception, match="rollback_failed_previous_restored"):
        execute(second | {"mode":"rollback"}, prerequisite=lambda _: {}, control=control, check=lambda _: True)
    root=Path(second["spec"]["root"])
    assert (root / "current").resolve().name == second["sha256"]
    assert Path(json.loads((root / "state.json").read_text())["current"]).name == second["sha256"]


def test_missing_model_package_stops_before_service_commands(tmp_path, monkeypatch):
    import sys
    import deployment.remote as remote
    config=spec(tmp_path, python=sys.executable).as_dict()
    env=Path(config["env_file"])
    env.write_text("PII_DETECTOR_TOKEN=synthetic\nPII_DETECTOR_DEVICE=cpu\nPII_MODEL_PACKAGE="+str(tmp_path / "absent")+"\n")
    env.chmod(0o600)
    monkeypatch.setattr(remote.platform, "system", lambda: "Linux")
    monkeypatch.setattr(remote.importlib.metadata, "version", lambda name: config["requirements"][name])
    monkeypatch.setattr(remote, "command", lambda args: pytest.fail("service command before model prerequisite"))
    with pytest.raises(Exception, match="model_package_missing"):
        execute({"mode":"deploy","role":"gpu","spec":config})
    assert not Path(config["root"]).exists()


def test_readiness_requires_the_managed_process_listener(tmp_path):
    from deployment.remote import owns_endpoints
    config=spec(tmp_path).as_dict()
    proc=tmp_path / "proc"; process=proc / "42"
    (process / "fd").mkdir(parents=True); (proc / "net").mkdir()
    (process / "cmdline").write_bytes(config["python"].encode()+b"\0-m\0detector\0")
    root=Path(config["root"]); root.mkdir(); (root / "current").mkdir()
    (process / "cwd").symlink_to(root / "current")
    (process / "fd/3").symlink_to("socket:[123]")
    (proc / "net/tcp").write_text("header\n0: 0100007F:223E 00000000:0000 0A 0:0 00:0 0 0 0 999\n")
    assert not owns_endpoints(config,"42",proc)
    (proc / "net/tcp").write_text("header\n0: 0100007F:223E 00000000:0000 0A 0:0 00:0 0 0 0 123\n")
    assert owns_endpoints(config,"42",proc)
    (process / "cmdline").write_bytes(b"/other/python\0-m\0detector\0")
    assert not owns_endpoints(config,"42",proc)


def test_general_static_assets_are_allowlisted(tmp_path):
    root=tmp_path / "observability/static"; root.mkdir(parents=True)
    (root / "app.js").write_text("console.log('safe');")
    (root / "secrets.json").write_text("private")
    data,digest=bundle(tmp_path,"general")
    assert [m.name for m in validate_archive(data,digest,"general")] == ["observability/static/app.js"]


def test_empty_known_hosts_is_a_trust_gate_not_network_failure(tmp_path):
    known=tmp_path / "known_hosts"; known.touch(); known.chmod(0o600)
    with pytest.raises(DeploymentError, match="verified_known_hosts_required"):
        ssh_argv({}, known, "/opt/python")


def test_host_preflight_uses_ubuntu_bootstrap_without_app_environment(tmp_path, monkeypatch, capsys):
    import deployment.__main__ as cli
    monkeypatch.setattr(cli, "load_access", lambda *args: {"hostname":"synthetic","password":"PRIVATE"})
    seen=[]
    monkeypatch.setattr(cli, "ssh_argv", lambda access, known, python, **kwargs: seen.append(python) or ["ssh","synthetic"])
    def run(args, **kwargs):
        assert "PRIVATE" not in kwargs["input"]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status":"preflight_ok", "details":{"cpu_count":2,"secret":"PRIVATE","python":"3.12.0"}}))
    assert cli.main(["preflight","--role","general"],run=run) == 0
    assert seen == ["/usr/bin/python3"]
    output=capsys.readouterr().out
    assert "PRIVATE" not in output and '"cpu_count": 2' in output


def test_application_environment_missing_is_not_bootstrap_substitution(tmp_path):
    from deployment.remote import application_probe
    with pytest.raises(Exception, match="application_python_missing"):
        application_probe(spec(tmp_path, python=str(tmp_path / "absent/bin/python")).as_dict())


def test_wrong_remote_success_action_is_rejected(tmp_path, monkeypatch, capsys):
    import deployment.__main__ as cli
    monkeypatch.setattr(cli,"load_access",lambda *args:{"hostname":"synthetic"})
    monkeypatch.setattr(cli,"ssh_argv",lambda *args,**kwargs:["ssh","synthetic"])
    assert cli.main(["preflight","--role","general"],run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout='{"status":"deployed"}')) == 2
    assert "ssh_or_remote_response_failed" in capsys.readouterr().out


def test_application_probe_discards_native_import_chatter(tmp_path, monkeypatch):
    import sys
    import deployment.remote as remote
    def run(args, **kwargs):
        payload=json.loads(kwargs["input"])
        report={"application_python":"3.12","dependencies":"verified","cuda_available":False}
        line="CODEX_DEPLOY_REPORT:"+payload.get("_report_nonce","missing")+":"+json.dumps(report)
        return SimpleNamespace(returncode=0, stdout="native library chatter\\n\n"+line+"\nmore native chatter\n")
    monkeypatch.setattr(remote.subprocess,"run",run)
    result=remote.application_probe(spec(tmp_path,python=sys.executable).as_dict())
    assert result["dependencies"] == "verified" and result["cuda_available"] is False
