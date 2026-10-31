"""S04: read-only diagnostics after genuine subprocess exit, not power-loss proof."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deployment import remote
from deployment.recovery import inspect_recovery, main
from deployment.tests.test_deployment import deployment_request


def snapshot(root):
    return {str(path.relative_to(root)):(path.lstat().st_mtime_ns,os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
            for path in [root,*root.rglob("*")]}


@pytest.fixture
def baseline(tmp_path,monkeypatch):
    monkeypatch.setattr(remote.Path,"home",lambda:tmp_path / "home")
    request=deployment_request(tmp_path)
    remote.execute(request,prerequisite=lambda _: {},control=lambda _:"active",check=lambda _:True)
    outside=tmp_path / "outside-secret"; outside.write_text("SYNTHETIC_SECRET")
    return tmp_path,Path(request["spec"]["root"]),request


def assert_read_only(root,expected):
    before=snapshot(root)
    report=inspect_recovery(root,role="gpu")
    assert report["classification"] == expected
    assert report["service_verified"] is False and report["mutations_performed"] is False
    assert snapshot(root) == before
    assert str(root) not in json.dumps(report) and "SYNTHETIC_SECRET" not in json.dumps(report)
    return report


def test_consistent_install_is_not_a_service_health_claim(baseline):
    _,root,_=baseline
    report=assert_read_only(root,"consistent")
    assert report["actions"] == ["VERIFY_SERVICE_BEFORE_USE"]


CRASH_SCRIPT = r'''
import json, os, sys
from pathlib import Path
from deployment import remote
request=json.loads(sys.stdin.read())
checkpoint=sys.argv[1]
remote.Path.home=lambda:Path(sys.argv[2])
original_atomic=remote.atomic_json
original_switch=remote.switch
original_chmod=remote.os.chmod
def atomic(path,value):
    original_atomic(path,value)
    if checkpoint == "manifest" and path.name == "deployment-manifest.json": os._exit(91)
    if checkpoint == "state" and path.name == "state.json": os._exit(91)
def switch(root,target):
    original_switch(root,target)
    if checkpoint == "switch": os._exit(91)
def chmod(path,mode):
    original_chmod(path,mode)
    if checkpoint == "partial_file" and str(path).endswith("/__main__.py"): os._exit(91)
def control(args):
    result="active"
    if checkpoint == "restart" and "restart" in args: os._exit(91)
    return result
remote.atomic_json=atomic
remote.switch=switch
remote.os.chmod=chmod
remote.execute(request,prerequisite=lambda _: {},control=control,check=lambda _:True)
raise SystemExit(92)
'''


@pytest.mark.parametrize("checkpoint,classification",[("partial_file","incomplete_staged"),("manifest","staged_unreferenced"),
    ("state","state_ahead_of_current"),("switch","consistent"),("restart","consistent")])
def test_abrupt_process_exit_reports_real_interrupted_files_without_repair(baseline,checkpoint,classification):
    tmp_path,root,first=baseline
    old=root / "releases" / first["sha256"]; original=snapshot(old)
    second=deployment_request(tmp_path,"two")
    result=subprocess.run([sys.executable,"-c",CRASH_SCRIPT,checkpoint,str(tmp_path / "home")],
                          input=json.dumps(second),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    assert_read_only(root,classification)
    assert snapshot(old) == original and (tmp_path / "outside-secret").read_text() == "SYNTHETIC_SECRET"


@pytest.mark.parametrize("variant,classification",[("root_link","untrusted_root"),("marker_link","untrusted_marker"),
    ("state_link","untrusted_state"),("state_path_escape","untrusted_state"),("current_escape","current_unknown"),
    ("modified_code","untrusted_manifest"),("missing_previous","previous_missing"),("release_parent_link","untrusted_release_tree")])
def test_hostile_metadata_paths_are_not_followed_and_report_is_sanitized(baseline,variant,classification):
    tmp_path,root,_=baseline; outside=tmp_path / "outside-secret"
    if variant == "root_link":
        link=tmp_path / "link"; link.symlink_to(root); report=inspect_recovery(link,role="gpu")
        assert report["classification"] == classification; return
    if variant == "marker_link":
        marker=root / "managed.json"; marker.rename(root / "saved-marker"); marker.symlink_to(outside)
    elif variant == "state_link":
        state=root / "state.json"; state.rename(root / "saved-state"); state.symlink_to(outside)
    elif variant == "state_path_escape":
        value=json.loads((root / "state.json").read_text()); value["previous"]=str(outside); (root / "state.json").write_text(json.dumps(value))
    elif variant == "current_escape":
        (root / "current").rename(root / "saved-current"); (root / "current").symlink_to(outside)
    elif variant == "modified_code": ((root / "current").resolve() / "detector/__main__.py").write_text("SYNTHETIC_SECRET")
    elif variant == "missing_previous":
        value=json.loads((root / "state.json").read_text()); value["previous"]=str(root / "releases" / ("a"*64)); (root / "state.json").write_text(json.dumps(value))
    else:
        (root / "releases").rename(root / "saved-releases"); (root / "releases").symlink_to(root / "saved-releases")
    assert_read_only(root,classification)
    assert outside.read_text() == "SYNTHETIC_SECRET"


def test_cli_reports_only_fixed_codes_and_never_writes_output_file(baseline,capsys):
    _,root,_=baseline; before=snapshot(root)
    assert main(["--root",str(root),"--role","gpu"]) == 0
    output=capsys.readouterr().out
    assert json.loads(output)["classification"] == "consistent" and str(root) not in output
    assert snapshot(root) == before


@pytest.mark.parametrize("checkpoint", ["partial_file", "manifest", "state"])
@pytest.mark.parametrize("first_install", [False, True])
def test_same_digest_retry_recovers_interrupted_stage(tmp_path, monkeypatch, checkpoint, first_install):
    monkeypatch.setattr(remote.Path,"home",lambda:tmp_path / "home")
    first=deployment_request(tmp_path)
    if not first_install:
        remote.execute(first,prerequisite=lambda _: {},control=lambda _:"active",check=lambda _:True)
    request=first if first_install else deployment_request(tmp_path,"two")
    root=Path(request["spec"]["root"])
    old=snapshot(root / "releases" / first["sha256"]) if not first_install else None
    result=subprocess.run([sys.executable,"-c",CRASH_SCRIPT,checkpoint,str(tmp_path / "home")],
                          input=json.dumps(request),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    partial=snapshot(root / "releases" / request["sha256"])
    assert remote.execute(request,prerequisite=lambda _: {},control=lambda _:"active",check=lambda _:True)["status"] == "deployed"
    assert (root / "current").resolve().name == request["sha256"]
    assert_read_only(root,"consistent")
    if old is not None:
        assert snapshot(root / "releases" / first["sha256"]) == old
        assert remote.execute(request | {"mode":"rollback"},prerequisite=lambda _: {},control=lambda _:"active",check=lambda _:True)["release"] == first["sha256"]
    if checkpoint == "partial_file":
        preserved=list(root.glob("preserved-stage-*"))
        assert len(preserved) == 1 and snapshot(preserved[0] / request["sha256"]) == partial


@pytest.mark.parametrize("variant,classification",[("missing_state","state_missing"),("missing_current","state_ahead_of_current"),
    ("dangling_current","current_missing"),("state_target_missing","state_target_missing"),("bad_marker","untrusted_marker"),
    ("malformed_state","untrusted_state"),("duplicate_state","untrusted_state"),("bad_inventory","untrusted_release_tree"),
    ("bad_archived_manifest","untrusted_manifest"),("failed_only","no_active_release")])
def test_recovery_classifies_inconsistent_metadata_conservatively(baseline,variant,classification):
    _,root,_=baseline
    state=root / "state.json"; current=root / "current"
    if variant == "missing_state": state.rename(root / "preserved-state")
    elif variant == "missing_current": current.rename(root / "preserved-current")
    elif variant == "dangling_current":
        current.rename(root / "preserved-current"); current.symlink_to(root / "releases" / ("f"*64))
    elif variant == "state_target_missing":
        value=json.loads(state.read_text()); value["current"]=str(root / "releases" / ("f"*64)); state.write_text(json.dumps(value))
    elif variant == "bad_marker": (root / "managed.json").write_text('{"manager":"SYNTHETIC_SECRET","role":"gpu"}')
    elif variant == "malformed_state": state.write_text("SYNTHETIC_SECRET")
    elif variant == "duplicate_state": state.write_text('{"current":null,"current":null,"previous":null}')
    elif variant == "bad_inventory": (root / "releases/unexpected-name").mkdir()
    elif variant == "bad_archived_manifest":
        release=root / "releases" / ("f"*64); release.mkdir(); (release / "deployment-manifest.json").write_text("SYNTHETIC_SECRET")
    else:
        value=json.loads(state.read_text()); failed=value["current"]; current.rename(root / "preserved-current")
        state.write_text(json.dumps({"current":None,"previous":None,"failed":failed}))
    assert_read_only(root,classification)


def test_absent_unmanaged_and_invalid_roots_do_not_get_created(tmp_path):
    missing=tmp_path / "absent"
    assert inspect_recovery(missing,role="gpu")["classification"] == "no_installation"
    assert not missing.exists()
    assert inspect_recovery(tmp_path,role="gpu")["classification"] == "unmanaged_root"
    assert inspect_recovery("relative",role="gpu")["classification"] == "untrusted_root"
    assert inspect_recovery(tmp_path,role="wrong")["classification"] == "untrusted_root"


def test_real_recovery_cli_process_and_parser_errors_never_reveal_values(baseline):
    _,root,_=baseline
    result=subprocess.run([sys.executable,"-m","deployment.recovery","--root",str(root),"--role","gpu"],capture_output=True,text=True,timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["classification"] == "consistent"
    failed=subprocess.run([sys.executable,"-m","deployment.recovery","--token","SYNTHETIC_SECRET"],capture_output=True,text=True,timeout=10)
    assert failed.returncode == 2 and "SYNTHETIC_SECRET" not in failed.stdout+failed.stderr


@pytest.mark.parametrize("variant,classification",[("current_number","untrusted_state"),("state_array","untrusted_state"),
    ("extra_state_key","untrusted_state"),("current_regular_file","current_unknown"),
    ("current_none_state","state_current_mismatch"),("previous_corrupted","untrusted_manifest"),
    ("declared_partial","untrusted_manifest")])
def test_typed_state_and_reference_validation(baseline,variant,classification):
    _,root,_=baseline; state=root / "state.json"; value=json.loads(state.read_text())
    if variant == "current_number": value["current"]=42
    elif variant == "state_array": state.write_text("[]"); assert_read_only(root,classification); return
    elif variant == "extra_state_key": value["SYNTHETIC_SECRET"]="hidden"
    elif variant == "current_regular_file":
        (root / "current").rename(root / "preserved-current"); (root / "current").write_text("SYNTHETIC_SECRET")
    elif variant == "current_none_state": value["current"]=None
    else:
        archived=root / "releases" / ("b"*64); archived.mkdir()
        value["previous" if variant == "previous_corrupted" else "current"]=str(archived)
        if variant == "previous_corrupted": (archived / "deployment-manifest.json").write_text("[]")
    state.write_text(json.dumps(value)); assert_read_only(root,classification)


def test_empty_managed_root_is_incomplete_not_healthy(tmp_path):
    (tmp_path / "managed.json").write_text('{"manager":"codyssey-pii-v1","role":"gpu"}')
    assert_read_only(tmp_path,"incomplete_staged")


def test_inventory_limits_and_io_failure_remain_readonly(tmp_path,monkeypatch):
    (tmp_path / "managed.json").write_text('{"manager":"codyssey-pii-v1","role":"gpu"}')
    releases=tmp_path / "releases"; releases.mkdir()
    for i in range(1001): (releases / f"{i:064x}").mkdir()
    assert_read_only(tmp_path,"diagnostic_limit")
    def denied(path): raise PermissionError("SYNTHETIC_SECRET")
    monkeypatch.setattr(Path,"iterdir",denied)
    report=inspect_recovery(tmp_path,role="gpu")
    assert report["classification"] == "diagnostic_unavailable" and "SYNTHETIC_SECRET" not in json.dumps(report)


def test_missing_recorded_failed_release_is_not_silently_consistent(baseline):
    _,root,_=baseline; state=root / "state.json"; value=json.loads(state.read_text())
    value["failed"]=str(root / "releases" / ("f"*64)); state.write_text(json.dumps(value))
    assert_read_only(root,"failed_release_missing")


@pytest.mark.parametrize("variant", ["partial_bytes", "extra_file", "extra_dir", "file_symlink", "dir_symlink", "hardlink", "forged_manifest", "state_symlink", "state_duplicate", "state_missing", "state_mismatch"])
def test_retry_rejects_untrusted_stage_or_state_without_mutation(baseline, variant):
    import hashlib
    tmp_path, root, first = baseline
    request = deployment_request(tmp_path, "two")
    checkpoint = "manifest" if variant == "forged_manifest" else "partial_file"
    result = subprocess.run([sys.executable,"-c",CRASH_SCRIPT,checkpoint,str(tmp_path / "home")],
                            input=json.dumps(request),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    stage = root / "releases" / request["sha256"]
    source = stage / "detector/__main__.py"
    state = root / "state.json"
    if variant == "partial_bytes": source.write_text("UNTRUSTED")
    elif variant == "extra_file": (stage / "detector/unknown.py").write_text("UNTRUSTED")
    elif variant == "extra_dir": (stage / "unknown").mkdir()
    elif variant == "file_symlink":
        source.rename(tmp_path / "saved-source"); source.symlink_to(tmp_path / "outside-secret")
    elif variant == "dir_symlink":
        (stage / "detector").rename(tmp_path / "saved-dir"); (stage / "detector").symlink_to(tmp_path / "saved-dir")
    elif variant == "hardlink":
        source.rename(tmp_path / "saved-source"); os.link(tmp_path / "saved-source", source)
    elif variant == "forged_manifest":
        source.write_text("UNTRUSTED")
        manifest = stage / "deployment-manifest.json"; value = json.loads(manifest.read_text())
        value["files"]["detector/__main__.py"] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(value))
    elif variant == "state_symlink":
        state.rename(tmp_path / "saved-state"); state.symlink_to(tmp_path / "saved-state")
    elif variant == "state_duplicate": state.write_text('{"current":null,"current":null,"previous":null}')
    elif variant == "state_missing": state.rename(tmp_path / "saved-state")
    else: state.write_text(json.dumps({"current": None, "previous": None}))
    before = snapshot(root)
    with pytest.raises(remote.RemoteError):
        remote.execute(request,prerequisite=lambda _: {},control=lambda _:pytest.fail("must not control service"),check=lambda _:True)
    assert snapshot(root) == before
    assert (tmp_path / "outside-secret").read_text() == "SYNTHETIC_SECRET"


def test_preservation_destination_collision_cannot_replace_evidence(baseline, monkeypatch):
    from types import SimpleNamespace
    tmp_path, root, first = baseline
    request = deployment_request(tmp_path,"two")
    result = subprocess.run([sys.executable,"-c",CRASH_SCRIPT,"partial_file",str(tmp_path / "home")],
                            input=json.dumps(request),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    (root / "preserved-stage-fixed").mkdir()
    (root / "preserved-stage-fixed/evidence").write_text("keep")
    monkeypatch.setattr(remote.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    before = snapshot(root)
    with pytest.raises(FileExistsError):
        remote.execute(request,prerequisite=lambda _: {},control=lambda _:pytest.fail("must not control service"),check=lambda _:True)
    assert snapshot(root) == before


@pytest.mark.parametrize("failure", ["unhealthy_previous", "restart_failure"])
def test_retry_service_failure_preserves_existing_and_partial_releases(baseline, monkeypatch, failure):
    tmp_path, root, first = baseline
    request = deployment_request(tmp_path,"two")
    result = subprocess.run([sys.executable,"-c",CRASH_SCRIPT,"partial_file",str(tmp_path / "home")],
                            input=json.dumps(request),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    before = snapshot(root)
    old = snapshot(root / "releases" / first["sha256"])
    partial = snapshot(root / "releases" / request["sha256"])
    monkeypatch.setattr(remote,"wait_ready",lambda spec,check:check(spec))
    calls = []
    def control(args):
        calls.append(args)
        if len(calls) == 2: raise RuntimeError("restart failed")
        return "active"
    with pytest.raises(remote.RemoteError, match="previous_service_not_healthy" if failure == "unhealthy_previous" else "deploy_failed_previous_restored"):
        remote.execute(request,prerequisite=lambda _: {},control=control,check=lambda _:failure != "unhealthy_previous")
    assert (root / "current").resolve().name == first["sha256"]
    assert snapshot(root / "releases" / first["sha256"]) == old
    if failure == "unhealthy_previous":
        assert snapshot(root) == before and not calls
    else:
        preserved = list(root.glob("preserved-stage-*"))
        assert snapshot(preserved[0] / request["sha256"]) == partial
        remote.checked_release(root, str(root / "releases" / request["sha256"]), "gpu")


@pytest.mark.parametrize("variant", ["absent_current_with_previous", "self_history"])
def test_retry_refuses_impossible_history_without_discarding_rollback(baseline, variant):
    tmp_path, root, first = baseline
    request = deployment_request(tmp_path,"two")
    result = subprocess.run([sys.executable,"-c",CRASH_SCRIPT,"partial_file",str(tmp_path / "home")],
                            input=json.dumps(request),text=True,capture_output=True,timeout=15)
    assert result.returncode == 91
    old = str(root / "releases" / first["sha256"])
    if variant == "absent_current_with_previous":
        (root / "current").rename(root / "saved-current")
    (root / "state.json").write_text(json.dumps({"current":None if variant == "absent_current_with_previous" else old,"previous":old}))
    before = snapshot(root)
    with pytest.raises(remote.RemoteError,match="retry_state_mismatch"):
        remote.execute(request,prerequisite=lambda _: {},control=lambda _:"active",check=lambda _:True)
    assert snapshot(root) == before
