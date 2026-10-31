"""R11/T07: deployed bytes remain immutable across verification and rollback."""
import json
import os
from pathlib import Path

import pytest

from deployment import remote
from deployment.tests.test_deployment import deployment_request


@pytest.fixture
def deployed(tmp_path,monkeypatch):
    monkeypatch.setattr(remote.Path,"home",lambda:tmp_path / "home")
    calls=[]
    def execute(request):
        return remote.execute(request,prerequisite=lambda _: {},control=lambda args:calls.append(args) or "active",check=lambda _:True)
    first=deployment_request(tmp_path)
    execute(first)
    root=Path(first["spec"]["root"])
    calls.clear()
    return tmp_path,root,first,execute,calls


def corrupt(release,variant,tmp_path):
    source=release / "detector/__main__.py"
    if variant == "modified": source.write_text("# changed after verification\n")
    elif variant == "added": (release / "detector/extra.py").write_text("# unexpected\n")
    elif variant == "missing": source.rename(tmp_path / "preserved-original.py")
    elif variant == "symlink":
        original=tmp_path / "preserved-original.py"; source.rename(original); source.symlink_to(original)
    elif variant == "manifest_symlink":
        manifest=release / "deployment-manifest.json"; original=tmp_path / "original-manifest.json"; manifest.rename(original); manifest.symlink_to(original)
    elif variant == "oversize": source.write_bytes(b"#"*(16*1024*1024+1))
    elif variant == "invalid_digest":
        manifest=release / "deployment-manifest.json"; value=json.loads(manifest.read_text()); value["files"]["detector/__main__.py"]="not-a-digest"; manifest.write_text(json.dumps(value))
    else:
        manifest=release / "deployment-manifest.json"; value=json.loads(manifest.read_text()); value["sha256"]="f"*64; manifest.write_text(json.dumps(value))


@pytest.mark.parametrize("variant",["modified","added","missing","symlink","manifest_symlink","folder_binding","oversize","invalid_digest"])
def test_verify_rejects_changed_release_before_service_commands(deployed,variant):
    tmp_path,root,first,execute,calls=deployed
    release=(root / "current").resolve(); corrupt(release,variant,tmp_path)
    with pytest.raises(remote.RemoteError): execute(first | {"mode":"verify"})
    assert not calls and (root / "current").resolve() == release


@pytest.mark.parametrize("variant",["modified","added","missing","symlink"])
def test_rollback_refuses_corrupted_archived_target_without_switch_or_restart(deployed,variant):
    tmp_path,root,first,execute,calls=deployed
    old=(root / "current").resolve(); second=deployment_request(tmp_path,"two"); execute(second)
    current=(root / "current").resolve(); state=(root / "state.json").read_bytes(); calls.clear()
    corrupt(old,variant,tmp_path)
    with pytest.raises(remote.RemoteError): execute(second | {"mode":"rollback"})
    assert not calls and (root / "current").resolve() == current
    assert (root / "state.json").read_bytes() == state
    assert old.exists() and current.exists()


def test_repeat_current_digest_is_verified_noop_without_file_or_service_mutation(deployed):
    _,root,first,execute,calls=deployed
    before={str(path.relative_to(root)):(path.lstat().st_mtime_ns,path.read_bytes() if path.is_file() else os.readlink(path) if path.is_symlink() else None)
            for path in root.rglob("*")}
    assert execute(first)["status"] == "deployed"
    after={str(path.relative_to(root)):(path.lstat().st_mtime_ns,path.read_bytes() if path.is_file() else os.readlink(path) if path.is_symlink() else None)
           for path in root.rglob("*")}
    assert not calls and before == after


def test_repeated_current_digest_does_not_hide_corruption(deployed):
    tmp_path,root,first,execute,calls=deployed
    corrupt((root / "current").resolve(),"modified",tmp_path)
    with pytest.raises(remote.RemoteError): execute(first)
    assert not calls


def test_archived_digest_is_never_overwritten_by_deploy(deployed):
    tmp_path,root,first,execute,calls=deployed
    second=deployment_request(tmp_path,"two"); execute(second); calls.clear()
    current=(root / "current").resolve()
    with pytest.raises(remote.RemoteError,match="release_already_exists"): execute(first)
    assert not calls and (root / "current").resolve() == current


def test_baseline_release_manifest_matches_exact_installed_bytes(deployed):
    import hashlib
    _,root,first,execute,_=deployed
    current=(root / "current").resolve()
    manifest=json.loads((current / "deployment-manifest.json").read_text())
    assert manifest["sha256"] == current.name
    assert manifest["files"] == {"detector/__main__.py":hashlib.sha256((current / "detector/__main__.py").read_bytes()).hexdigest()}
    assert execute(first | {"mode":"verify"})["status"] == "verified"
