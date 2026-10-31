"""Source-only build boundary tests; no native binary claim from mocked builds."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from launcher import distribution as dist


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "project"
    files = ["gateway/__init__.py", "gateway/app.py", *dist.LAUNCHER_FILES,
             "detector/__init__.py", "detector/schemas.py"]
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    for name in ["gateway/tests/test_secret.py", "gateway/.env", "launcher/local_stack.py",
                 "detector/model.py", "training/private.py"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE FIXTURE")
    monkeypatch.setattr(dist.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(dist.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(dist.metadata, "version", lambda name: "1.2.3")
    return root


def test_plan_does_not_write_or_execute(source, tmp_path, monkeypatch):
    monkeypatch.setattr(dist.subprocess, "run", lambda *a, **k: pytest.fail("process"))
    output = tmp_path / "build"
    receipt = dist.build(output, source_root=source, plan=True)
    assert not output.exists()
    assert receipt["status"] == "planned"
    assert receipt["notarized"] is False


def test_build_stages_allowlist_and_sanitizes_environment(source, tmp_path, monkeypatch):
    output = tmp_path / "build"
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-SECRET")
    monkeypatch.setenv("PII_GATEWAY_LOCAL_TOKEN", "PRIVATE-TOKEN")
    monkeypatch.setenv("PYTHONPATH", "/private/project")
    def run(command, **kwargs):
        assert command[:3] == [sys.executable, "-m", "PyInstaller"]
        assert "--onedir" in command and "--target-arch" in command
        assert "uvicorn.protocols.http.h11_impl" in command
        assert "torch" in command and "numpy" in command
        assert kwargs["cwd"] == output / "source"
        assert not {"OPENAI_API_KEY", "PII_GATEWAY_LOCAL_TOKEN", "PYTHONPATH"} & kwargs["env"].keys()
        target = output / "dist/pii-gateway/pii-gateway"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"fake-native")
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(dist.subprocess, "run", run)
    receipt = dist.build(output, source_root=source)
    names = {str(p.relative_to(output / "source")) for p in (output / "source").rglob("*") if p.is_file()}
    assert names == {"gateway/__init__.py", "gateway/app.py", *dist.LAUNCHER_FILES,
                     "detector/__init__.py", "detector/schemas.py"}
    assert output.stat().st_mode & 0o777 == 0o700
    assert receipt["status"] == "built"
    evidence = json.loads((output / "source-evidence.json").read_text())
    assert evidence["files"]["gateway/app.py"]["sha256"] == hashlib.sha256(b"# fixture\n").hexdigest()
    assert "PRIVATE" not in json.dumps(receipt)
    artifact = output / receipt["artifact"]
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == receipt["artifact_sha256"]
    assert (output / (dist.ARTIFACT + ".sha256")).read_text().startswith(receipt["artifact_sha256"])
    with tarfile.open(artifact) as archive:
        assert archive.getnames() == ["pii-gateway", "pii-gateway/pii-gateway"]


def test_existing_output_preserved(source, tmp_path):
    output = tmp_path / "build"
    output.mkdir()
    sentinel = output / "keep"
    sentinel.write_text("keep")
    with pytest.raises(dist.BuildError, match="output_exists"):
        dist.build(output, source_root=source)
    assert sentinel.read_text() == "keep"


def test_unsupported_platform_refused_before_writes(source, tmp_path, monkeypatch):
    monkeypatch.setattr(dist.platform, "machine", lambda: "x86_64")
    output = tmp_path / "build"
    with pytest.raises(dist.BuildError, match="requires_macos_arm64"):
        dist.build(output, source_root=source)
    assert not output.exists()


def test_symlink_source_refused(source, tmp_path):
    path = source / "gateway/app.py"
    path.unlink()
    path.symlink_to(source / "training/private.py")
    with pytest.raises(dist.BuildError, match="unsafe_source"):
        dist.build(tmp_path / "build", source_root=source)


def test_failed_builder_never_claims_success(source, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(dist.subprocess, "run", fail)
    output = tmp_path / "build"
    with pytest.raises(dist.BuildError, match="pyinstaller_failed"):
        dist.build(output, source_root=source)
    assert json.loads((output / "build-receipt.json").read_text())["status"] == "failed"


def test_archive_preserves_internal_native_symlinks(tmp_path):
    bundle = tmp_path / "dist/pii-gateway"
    (bundle / "_internal/framework/Versions/A").mkdir(parents=True)
    (bundle / "_internal/framework/Versions/A/library").write_bytes(b"native")
    (bundle / "_internal/framework/Versions/Current").symlink_to("A")
    (bundle / "_internal/framework/library").symlink_to("Versions/Current/library")
    manifest = dist._archive_bundle(tmp_path)
    with tarfile.open(tmp_path / manifest["artifact"]) as archive:
        link = archive.getmember("pii-gateway/_internal/framework/library")
        assert link.issym() and link.linkname == "Versions/Current/library"
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in archive.getnames())


def test_archive_rejects_external_symlink_before_writing(tmp_path):
    bundle = tmp_path / "dist/pii-gateway"
    bundle.mkdir(parents=True)
    (tmp_path / "secret").write_text("private")
    (bundle / "escape").symlink_to("../../secret")
    with pytest.raises(dist.BuildError, match="unsafe_bundle_link"):
        dist._archive_bundle(tmp_path)
    assert not (tmp_path / dist.ARTIFACT).exists()
