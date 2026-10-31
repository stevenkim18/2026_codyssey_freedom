"""R11/T07/T10: operational boundaries with isolated files and local HTTP only."""
import contextlib
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest

from deployment import remote
from deployment.tests.test_deployment import deployment_request, spec


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(remote.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(remote, "wait_ready", lambda config, check: check(config))
    return tmp_path


def run(request, check=lambda _: True, control=lambda _: "active"):
    return remote.execute(request, prerequisite=lambda _: {}, control=control, check=check)


@pytest.mark.parametrize("value", [b" active\n", b"\xff", b"private failure"])
def test_system_command_output_and_errors_do_not_leak(monkeypatch, value):
    monkeypatch.setattr(remote.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1 if value == b"private failure" else 0, stdout=value))
    if value == b" active\n":
        assert remote.command(["synthetic"]) == "active"
    else:
        with pytest.raises(Exception) as error:
            remote.command(["synthetic"])
        assert "private failure" not in str(error.value)


@pytest.mark.parametrize("content", ["TOKEN", "=secret", "A=", "A=x\nA=y", "A=white space", 'A="quoted"', "A=back\\slash", "A\t=x"])
def test_environment_parser_refuses_ambiguous_or_shell_style_values(tmp_path, content):
    path=tmp_path / "env"; path.write_text(content); path.chmod(0o600)
    with pytest.raises(remote.RemoteError, match="invalid_remote_environment"):
        remote.environment(path)
    assert path.read_text() == content


@pytest.mark.parametrize("variant", ["mode", "owner", "symlink", "oversize", "missing"])
def test_environment_metadata_gate_preserves_secret_file(tmp_path, monkeypatch, variant):
    path=tmp_path / "env"; path.write_text("TOKEN=private"); path.chmod(0o600)
    target=path
    if variant == "mode": path.chmod(0o644)
    if variant == "owner": monkeypatch.setattr(remote.os, "getuid", lambda: path.stat().st_uid+1)
    if variant == "symlink":
        target=tmp_path / "link"; target.symlink_to(path)
    if variant == "oversize": path.write_text("A="+"x"*65536)
    if variant == "missing": target=tmp_path / "absent"
    with pytest.raises(remote.RemoteError): remote.environment(target)
    assert path.exists()


def test_environment_reads_comments_without_shell_evaluation(tmp_path):
    path=tmp_path / "env"; path.write_text("# comment\n\nTOKEN=$SHELL;touch\n"); path.chmod(0o600)
    assert remote.environment(path) == {"TOKEN":"$SHELL;touch"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("bad", ["hostname", "platform", "version"])
def test_preflight_host_identity_failure_precedes_all_application_work(monkeypatch, bad):
    monkeypatch.setattr(remote.platform,"system",lambda: "Other" if bad == "platform" else "Linux")
    monkeypatch.setattr(remote.sys,"version_info",(3,11) if bad == "version" else (3,12))
    monkeypatch.setattr(remote.socket,"gethostname",lambda:"actual")
    with pytest.raises(remote.RemoteError):
        remote.preflight({"role":"gpu", "expected_hostname":"wrong" if bad == "hostname" else "actual"})


def test_contract_free_preflight_checks_candidate_python_without_running_it(tmp_path, monkeypatch):
    monkeypatch.setattr(remote.platform,"system",lambda:"Linux")
    candidate=tmp_path / "python"; candidate.write_text("must not run")
    result=remote.preflight({"role":"general","application_python":str(candidate)})
    assert result["application_python_present"] is True and result["contract"] is False
    assert remote.preflight({"role":"general"})["contract"] is False


@pytest.mark.parametrize("variant", ["valid", "unknown", "missing_token", "wrong_device", "wrong_port", "health_port", "general"])
def test_preflight_model_environment_and_port_contract(tmp_path, monkeypatch, variant):
    monkeypatch.setattr(remote.platform,"system",lambda:"Linux")
    config=spec(tmp_path).as_dict()
    package=tmp_path / "package"; package.mkdir(); (package / "manifest.json").write_text("{}")
    values={"PII_DETECTOR_TOKEN":"private","PII_MODEL_PACKAGE":str(package),"PII_DETECTOR_DEVICE":"cpu"}
    if variant == "unknown": values["EXTRA"]="private"
    if variant == "missing_token": values.pop("PII_DETECTOR_TOKEN")
    if variant == "wrong_device": values["PII_DETECTOR_DEVICE"]="cuda"
    if variant == "wrong_port": values["PII_DETECTOR_PORT"]="1234"
    if variant == "health_port": config["health_url"]="http://127.0.0.1:1234/healthz"
    if variant == "general": config["role"]="general"
    env=Path(config["env_file"]); env.write_text("\n".join(k+"="+v for k,v in values.items())); env.chmod(0o600)
    calls=[]
    monkeypatch.setattr(remote,"application_probe",lambda value:calls.append("probe") or {"application_python":"3.12"})
    monkeypatch.setattr(remote,"command",lambda value:calls.append("systemd") or "")
    if variant in {"valid","general"}:
        result=remote.preflight({"role":config["role"],"spec":config})
        assert result["application_python"] == "3.12" and calls == ["probe","systemd"]
    else:
        with pytest.raises(remote.RemoteError): remote.preflight({"role":"gpu","spec":config})
        assert not calls


@pytest.mark.parametrize("variant", ["empty", "duplicate", "bad_json", "wrong_schema", "failed"])
def test_child_probe_machine_report_integrity(tmp_path, monkeypatch, variant):
    def child(args, **kwargs):
        value=json.loads(kwargs["input"])
        prefix="CODEX_DEPLOY_REPORT:"+value["_report_nonce"]+":"
        payload=json.dumps({"application_python":"3.12","dependencies":"verified"})
        if variant == "bad_json": payload="{"
        if variant == "wrong_schema": payload='{"application_python":"3.11","dependencies":"verified"}'
        stdout="" if variant == "empty" else prefix+payload+"\n"
        if variant == "duplicate": stdout*=2
        return SimpleNamespace(returncode=1 if variant == "failed" else 0, stdout=stdout)
    monkeypatch.setattr(remote.subprocess,"run",child)
    with pytest.raises(remote.RemoteError,match="application_environment_probe_failed"):
        remote.application_probe(spec(tmp_path,python=sys.executable).as_dict())


@pytest.fixture
def http_service():
    state={"body":b'{"status":"ready"}',"status":200,"redirects":0}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect-target": state["redirects"]+=1
            self.send_response(state["status"])
            self.send_header("Location","/redirect-target")
            self.end_headers()
            self.wfile.write(state["body"])
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    yield state,server.server_port
    server.shutdown(); server.server_close(); thread.join(timeout=2)


@pytest.mark.parametrize("variant", ["ready", "wrong_status", "invalid_json", "array", "too_large", "unready", "redirect", "inactive", "wrong_owner"])
def test_http_readiness_boundary_and_redirect_refusal(tmp_path, monkeypatch, http_service, variant):
    state,port=http_service
    config=spec(tmp_path).as_dict()
    config.update(health_url=f"http://127.0.0.1:{port}/health",ready_url=f"http://127.0.0.1:{port}/ready",health_json={"status":"ready"})
    if variant == "wrong_status": state["status"]=204
    if variant == "invalid_json": state["body"]=b"PRIVATE-not-json"
    if variant == "array": state["body"]=b"[]"
    if variant == "too_large": state["body"]=b" "*8193
    if variant == "unready": state["body"]=b'{"status":"not_ready"}'
    if variant == "redirect": state["status"]=302
    monkeypatch.setattr(remote,"owns_endpoints",lambda *args:variant != "wrong_owner")
    def control(args): return ("inactive" if variant == "inactive" else "active") if "is-active" in args else "42"
    assert remote.healthy(config,control) is (variant == "ready")
    assert state["redirects"] == 0


@pytest.mark.parametrize("pid", ["0", "-1", "bad", "４２", "99999999"])
def test_missing_or_noncanonical_main_pid_never_passes(tmp_path,pid):
    assert not remote.owns_endpoints(spec(tmp_path).as_dict(),pid,tmp_path / "proc")


def test_pid_working_directory_must_match_active_release(tmp_path):
    config=spec(tmp_path).as_dict(); proc=tmp_path / "proc"; process=proc / "42"; process.mkdir(parents=True)
    (process / "cmdline").write_bytes(config["python"].encode()+b"\0-m\0detector")
    (process / "cwd").symlink_to(tmp_path / "unrelated")
    assert not remote.owns_endpoints(config,"42",proc)


def test_readiness_polling_is_bounded_and_eventually_successful(monkeypatch):
    ticks=iter([0,0,0,1,1,2]); monkeypatch.setattr(remote.time,"monotonic",lambda:next(ticks))
    sleeps=[]; monkeypatch.setattr(remote.time,"sleep",lambda seconds:sleeps.append(seconds))
    assert not remote.wait_ready({"timeout":1},lambda _:False)
    assert sleeps and all(0 <= value <= 1 for value in sleeps)
    checks=iter([False,True]); monkeypatch.setattr(remote.time,"monotonic",lambda:0)
    assert remote.wait_ready({"timeout":1},lambda _:next(checks))


@pytest.mark.parametrize("variant", ["root_symlink","marker_role","marker_symlink","unit_parent_symlink","unit_symlink","current_directory","release_symlink","release_role","releases_symlink"])
def test_rogue_managed_paths_cannot_modify_outside_files(home,variant):
    request=deployment_request(home); root=Path(request["spec"]["root"])
    outside=home / "outside"; outside.mkdir(); sentinel=outside / "sentinel"; sentinel.write_text("keep")
    root.mkdir(); (root / "managed.json").write_text(json.dumps({"manager":"codyssey-pii-v1","role":"gpu"}))
    unit=home / "home/.config/systemd/user/codyssey-pii-gpu.service"
    if variant == "root_symlink":
        request["spec"]["root"]=str(home / "link"); Path(request["spec"]["root"]).symlink_to(outside)
    elif variant == "marker_role": (root / "managed.json").write_text('{"manager":"codyssey-pii-v1","role":"general"}')
    elif variant == "marker_symlink":
        (root / "managed.json").rename(root / "saved-marker"); (root / "managed.json").symlink_to(sentinel)
    elif variant == "unit_parent_symlink":
        unit.parent.parent.mkdir(parents=True); unit.parent.symlink_to(outside)
    elif variant == "unit_symlink":
        unit.parent.mkdir(parents=True); unit.symlink_to(sentinel)
    elif variant == "current_directory": (root / "current").mkdir()
    elif variant in {"release_symlink","release_role"}:
        releases=root / "releases"; releases.mkdir(); release=releases / "old"
        if variant == "release_symlink": release.symlink_to(outside)
        else:
            release.mkdir(); (release / "deployment-manifest.json").write_text('{"manager":"codyssey-pii-v1","role":"general"}')
        (root / "current").symlink_to(release)
    else: (root / "releases").symlink_to(outside)
    with pytest.raises(remote.RemoteError): run(request)
    assert sentinel.read_text() == "keep" and list(outside.iterdir()) == [sentinel]


def test_repeated_digest_and_unhealthy_previous_preserve_releases(home):
    first=deployment_request(home); run(first); root=Path(first["spec"]["root"])
    before=(root / "current").resolve()
    assert run(first)["status"] == "deployed"
    second=deployment_request(home,"two")
    with pytest.raises(remote.RemoteError,match="previous_service_not_healthy"): run(second,check=lambda _:False)
    assert (root / "current").resolve() == before and len(list((root / "releases").iterdir())) == 1


@pytest.mark.parametrize("variant", ["missing","unhealthy","success"])
def test_verify_reports_only_existing_healthy_managed_release(home,variant):
    request=deployment_request(home)
    if variant != "missing": run(request)
    if variant == "success":
        assert run(request | {"mode":"verify"})["status"] == "verified"
    else:
        with pytest.raises(remote.RemoteError,match="verification_failed"):
            run(request | {"mode":"verify"},check=lambda _:False)


@pytest.mark.parametrize("variant", ["state_mismatch","readiness_failure","restart_and_restore_failure"])
def test_rollback_failure_preserves_both_releases_and_tracks_actual_pointer(home,variant):
    first=deployment_request(home); second=deployment_request(home,"two"); run(first); run(second)
    root=Path(second["spec"]["root"]); before=(root / "current").resolve()
    if variant == "state_mismatch":
        state=json.loads((root / "state.json").read_text()); state["current"]="/unrelated"; (root / "state.json").write_text(json.dumps(state))
        control=lambda _:"active"
    else:
        def control(args):
            if variant == "restart_and_restore_failure": raise RuntimeError("PRIVATE")
            return "active"
    with pytest.raises(remote.RemoteError) as caught:
        run(second | {"mode":"rollback"},check=lambda _:False,control=control)
    assert "PRIVATE" not in str(caught.value)
    assert (root / "current").resolve() == before and len(list((root / "releases").iterdir())) == 2


def test_failed_deploy_and_failed_restore_leave_old_code_pointer(home):
    first=deployment_request(home); run(first); second=deployment_request(home,"two")
    root=Path(first["spec"]["root"]); previous=(root / "current").resolve()
    def fail(args): raise RuntimeError("PRIVATE")
    with pytest.raises(remote.RemoteError,match="deploy_failed_rollback_unhealthy"):
        run(second,control=fail)
    assert (root / "current").resolve() == previous
    assert json.loads((root / "state.json").read_text())["current"] == str(previous)


def test_invalid_modes_and_contracts_never_create_root(tmp_path):
    config=spec(tmp_path).as_dict()
    for request in ({"mode":"deploy","spec":None},{"mode":"bad","spec":config},{"mode":"deploy","spec":config,"role":"general"}):
        with pytest.raises(remote.RemoteError): run(request)
    assert not Path(config["root"]).exists()


def test_archive_resource_limits_reject_empty_and_many_files():
    for count in (0,1001):
        stream=io.BytesIO()
        with tarfile.open(fileobj=stream,mode="w") as archive:
            for i in range(count): archive.addfile(tarfile.TarInfo(f"detector/file{i}.py"))
        data=stream.getvalue()
        with pytest.raises(remote.RemoteError): remote.validate_archive(data,hashlib.sha256(data).hexdigest(),"gpu")


@pytest.fixture
def runtime(home,monkeypatch):
    base=home / "run-user"; directory=base / str(os.getuid()); directory.mkdir(parents=True,mode=0o700)
    monkeypatch.setattr(remote,"USER_RUNTIME_BASE",base)
    return directory


def test_dispatch_preflight_failure_creates_no_runtime_or_service_files(tmp_path,monkeypatch,capsys):
    base=tmp_path / "runtime"; monkeypatch.setattr(remote,"USER_RUNTIME_BASE",base)
    def fail(request): raise RuntimeError("PRIVATE")
    monkeypatch.setattr(remote,"preflight",fail)
    assert remote.dispatch({"mode":"deploy"}) == 2
    assert not base.exists() and "PRIVATE" not in capsys.readouterr().out


def test_dispatch_readonly_preflight_never_needs_runtime_lock(tmp_path,monkeypatch,capsys):
    base=tmp_path / "absent-runtime"; monkeypatch.setattr(remote,"USER_RUNTIME_BASE",base)
    monkeypatch.setattr(remote,"preflight",lambda _: {"contract":False})
    assert remote.dispatch({"mode":"preflight","spec":None}) == 0
    assert not base.exists() and json.loads(capsys.readouterr().out)["status"] == "preflight_ok"


def test_actual_flock_contention_refuses_deploy_and_releases_after_success(runtime,home,monkeypatch,capsys):
    monkeypatch.setattr(remote,"preflight",lambda _: {})
    original=remote.execute
    monkeypatch.setattr(remote,"execute",lambda request,**kwargs: original(request,control=lambda _:"active",check=lambda _:True,**kwargs))
    request=deployment_request(home)
    lock=runtime / "codyssey-pii-deployment.lock"
    with lock.open("a") as holder:
        fcntl.flock(holder,fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert remote.dispatch(request) == 2
        assert not Path(request["spec"]["root"]).exists()
        assert json.loads(capsys.readouterr().out)["code"] == "deployment_in_progress"
    assert remote.dispatch(request) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "deployed"
    with lock.open("a") as holder:
        fcntl.flock(holder,fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("variant", ["directory_mode","owner","directory_symlink","lock_symlink","operation_failure"])
def test_dispatch_lock_guards_and_exception_release(runtime,home,monkeypatch,capsys,variant):
    monkeypatch.setattr(remote,"preflight",lambda _: {})
    outside=home / "outside"; outside.write_text("keep")
    if variant == "directory_mode": runtime.chmod(0o755)
    if variant == "owner":
        actual=Path.stat
        def bad_owner(path,*args,**kwargs):
            result=actual(path,*args,**kwargs)
            return SimpleNamespace(st_uid=result.st_uid+1,st_mode=result.st_mode) if path == runtime else result
        monkeypatch.setattr(Path,"stat",bad_owner)
    if variant == "directory_symlink":
        saved=runtime.with_name("saved"); runtime.rename(saved); runtime.symlink_to(saved)
    if variant == "lock_symlink": (runtime / "codyssey-pii-deployment.lock").symlink_to(outside)
    if variant == "operation_failure":
        def fail(*args,**kwargs): raise RuntimeError("PRIVATE")
        monkeypatch.setattr(remote,"execute",fail)
    assert remote.dispatch({"mode":"deploy"}) == 2
    assert outside.read_text() == "keep" and "PRIVATE" not in capsys.readouterr().out
    if variant == "operation_failure":
        with (runtime / "codyssey-pii-deployment.lock").open("a") as holder:
            fcntl.flock(holder,fcntl.LOCK_EX | fcntl.LOCK_NB)
