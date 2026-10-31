"""Run installer adversarial fixtures inside an actual macOS sandbox."""
import hashlib
import io
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "plugin/scripts/install-macos.sh"


@pytest.fixture
def isolated(tmp_path):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        pytest.skip("macOS arm64 sandbox required")
    root = tmp_path.resolve()
    policy = root / "policy.sb"
    policy.write_text('(version 1)(deny default)(allow process*)(allow sysctl-read)'
                      '(allow file-read*)(allow file-write* (literal "/dev/null") (subpath "'
                      + str(root) + '"))')
    try:
        yield root, policy
    finally:
        # This fixture owns this one freshly allocated pytest directory only.
        shutil.rmtree(root)


def archive(root, name="pii-gateway/pii-gateway"):
    path = root / "fixture.tar.gz"
    content = b'#!/bin/sh\necho \'{"name":"pii-gateway","version":"0.1.0-engineering.1"}\'\n'
    with tarfile.open(path, "w:gz") as output:
        member = tarfile.TarInfo(name)
        member.mode = 0o755
        member.size = len(content)
        output.addfile(member, io.BytesIO(content))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(isolated, path, digest, destination):
    _, policy = isolated
    result = subprocess.run(["/usr/bin/sandbox-exec", "-f", str(policy), "/bin/bash", str(SCRIPT),
                           "--archive", str(path), "--sha256", digest,
                           "--destination", str(destination)], capture_output=True, text=True)
    assert "sandbox-exec:" not in result.stderr, result.stderr
    return result


def test_verified_install_and_no_overwrite(isolated):
    root, _ = isolated
    path, digest = archive(root)
    destination = root / "client"
    result = invoke(isolated, path, digest, destination)
    assert result.returncode == 0, result.stderr
    assert (destination / "pii-gateway").is_file()
    assert invoke(isolated, path, digest, destination).returncode != 0
    assert not list(root.glob(".pii-gateway-install.*"))


def test_bad_digest_never_extracts(isolated):
    root, _ = isolated
    path, _ = archive(root)
    destination = root / "client"
    assert invoke(isolated, path, "0" * 64, destination).returncode != 0
    assert not destination.exists()
    assert not list(root.glob(".pii-gateway-install.*"))


def test_path_traversal_refused(isolated):
    root, _ = isolated
    path, digest = archive(root, "pii-gateway/../../escaped")
    assert invoke(isolated, path, digest, root / "client").returncode != 0
    assert not (root / "escaped").exists()


def test_symlink_destination_parent_refused(isolated):
    root, _ = isolated
    path, digest = archive(root)
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert invoke(isolated, path, digest, alias / "client").returncode != 0
    assert not (root / "client").exists()
