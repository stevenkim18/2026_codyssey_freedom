"""R07/R11/T07: isolated model-artifact staging, never model execution."""
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from deployment.model_stage import ModelStageError, inspect_archive, plan, apply


def digest(data):
    return hashlib.sha256(data).hexdigest()


def packet(tmp_path, *, fmt=tarfile.USTAR_FORMAT, extra=None):
    files = {'weights.bin': b'synthetic-not-a-model', 'nested/config.json': b'{}'}
    manifest = {'schema_version': '1.0', 'files': {name: digest(data) for name, data in files.items()}}
    files['manifest.json'] = json.dumps(manifest).encode()
    archive = tmp_path / 'model-assets.tar'
    with tarfile.open(archive, 'w', format=fmt) as output:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
            output.addfile(info, io.BytesIO(data))
        if extra:
            output.addfile(extra)
    model = {'archive': archive.name, 'sha256': digest(archive.read_bytes()),
             'manifest_sha256': digest(files['manifest.json']), 'files': {name: digest(data) for name, data in files.items()}}
    metadata = {'schema_version': '1.0', 'role': 'gpu', 'application': {'archive': 'application.tar', 'sha256': 'a' * 64, 'bytes': 10240},
                'spec': {'file': 'deploy.json', 'sha256': 'b' * 64}, 'model': model,
                'files': {'model-assets.tar': model['sha256'], 'application.tar': 'a' * 64,
                          'deploy.json': 'b' * 64, 'APPLY.md': 'c' * 64}}
    path = tmp_path / 'release-manifest.json'; path.write_text(json.dumps(metadata))
    return archive, path, metadata, files


def run_apply(archive, metadata, root, home, **kwargs):
    return apply(archive, metadata, root, 'test-host', system=lambda: 'Linux',
                 hostname=lambda: 'test-host', home=lambda: home, **kwargs)


def test_plan_and_shared_validator_are_readonly(tmp_path):
    archive, metadata, value, files = packet(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert inspect_archive(archive, value['model'])['files']['weights.bin'] == digest(files['weights.bin'])
    report = plan(archive, metadata)
    assert report['status'] == 'planned' and report['file_count'] == 3
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_apply_exact_bytes_permissions_and_idempotence(tmp_path):
    archive, metadata, value, files = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    result = run_apply(archive, metadata, root, home)
    target = root / value['model']['manifest_sha256']
    assert result['status'] == 'applied' and Path(result['model_path']) == target
    for name, data in files.items():
        assert (target / name).read_bytes() == data
        assert (target / name).stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o700
    before = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    assert run_apply(archive, metadata, root, home)['status'] == 'already_present'
    assert before == sorted(str(p.relative_to(root)) for p in root.rglob('*'))


@pytest.mark.parametrize('kind', ['metadata_duplicate', 'wrong_role', 'wrong_tar_hash', 'wrong_manifest_hash',
                                  'unexpected_file', 'manifest_self_claim', 'bad_hash_type'])
def test_invalid_packets_fail_before_root_creation(tmp_path, kind):
    archive, metadata, value, _ = packet(tmp_path)
    if kind == 'metadata_duplicate':
        metadata.write_text(metadata.read_text().replace('"role": "gpu"', '"role": "gpu", "role": "gpu"', 1))
    else:
        if kind == 'wrong_role': value['role'] = 'general'
        if kind == 'wrong_tar_hash': value['model']['sha256'] = '0' * 64
        if kind == 'wrong_manifest_hash': value['model']['manifest_sha256'] = '0' * 64
        if kind == 'unexpected_file': value['model']['files']['extra'] = '0' * 64
        if kind == 'manifest_self_claim': value['model']['files']['weights.bin'] = '1' * 64
        if kind == 'bad_hash_type': value['model']['files']['weights.bin'] = False
        metadata.write_text(json.dumps(value))
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    assert not root.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'traversal', 'duplicate', 'directory', 'pax'])
def test_hostile_tar_headers_fail_without_extraction(tmp_path, kind):
    info = tarfile.TarInfo('../escape' if kind == 'traversal' else 'weights.bin' if kind == 'duplicate' else 'extra')
    if kind == 'symlink': info.type = tarfile.SYMTYPE; info.linkname = '/outside'
    if kind == 'hardlink': info.type = tarfile.LNKTYPE; info.linkname = 'weights.bin'
    if kind == 'directory': info.type = tarfile.DIRTYPE
    if kind == 'pax': info.type = tarfile.XHDTYPE
    archive, metadata, value, _ = packet(tmp_path, extra=info)
    with pytest.raises(ModelStageError): inspect_archive(archive, value['model'])
    assert not (tmp_path / 'escape').exists()


@pytest.mark.parametrize('kind', ['wrong_host', 'wrong_os', 'outside_home', 'existing_unknown', 'ancestor_symlink'])
def test_apply_host_and_owned_prefix_guards_preserve_existing(tmp_path, kind):
    archive, metadata, _, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    if kind == 'outside_home': root = tmp_path / 'outside'
    if kind == 'existing_unknown': root.mkdir(mode=0o700); (root / 'keep').write_text('keep')
    if kind == 'ancestor_symlink':
        actual = home / 'actual'; actual.mkdir(mode=0o700); (home / 'linked').symlink_to(actual); root = home / 'linked/models'
    with pytest.raises(ModelStageError):
        apply(archive, metadata, root, 'test-host', system=lambda: 'Other' if kind == 'wrong_os' else 'Linux',
              hostname=lambda: 'other-host' if kind == 'wrong_host' else 'test-host', home=lambda: home)
    if kind == 'existing_unknown': assert (root / 'keep').read_text() == 'keep'
    else: assert not root.exists()


def test_changed_existing_target_rejected_without_writes(tmp_path):
    archive, metadata, value, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    run_apply(archive, metadata, root, home)
    target = root / value['model']['manifest_sha256']; (target / 'weights.bin').write_bytes(b'changed')
    before = {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    assert before == {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def test_atomic_publish_cannot_replace_late_empty_target(tmp_path, monkeypatch):
    import deployment.model_stage as module
    archive, metadata, value, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    original = module._publish
    collided = []
    def collide(fd, source, target):
        os.mkdir(target, mode=0o700, dir_fd=fd)
        collided.append(os.stat(target, dir_fd=fd).st_ino)
        return original(fd, source, target)
    monkeypatch.setattr(module, '_publish', collide)
    with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    target = root / value['model']['manifest_sha256']
    assert target.stat().st_ino == collided[0] and list(target.iterdir()) == []
    assert len(list(root.glob('stage-*'))) == 1


def test_partial_failure_preserves_stage_and_lock_excludes_writer(tmp_path, monkeypatch):
    import fcntl
    import deployment.model_stage as module
    archive, metadata, value, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    def fail(source, stage, *args):
        (stage / 'partial').write_bytes(b'preserve')
        raise OSError('simulated disk failure')
    monkeypatch.setattr(module, '_populate', fail)
    with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    stages = list(root.glob('stage-*'))
    assert len(stages) == 1 and (stages[0] / 'partial').read_bytes() == b'preserve'
    assert not (root / value['model']['manifest_sha256']).exists()
    with (root / 'stage.lock').open('r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ModelStageError, match='model_stage_in_progress'):
            run_apply(archive, metadata, root, home)
    assert list(root.glob('stage-*')) == stages


@pytest.mark.parametrize('kind', ['archive_hardlink', 'metadata_hardlink', 'ancestor_symlink', 'archive_symlink'])
def test_sources_never_follow_links(tmp_path, kind):
    archive, metadata, _, _ = packet(tmp_path)
    if kind == 'archive_hardlink': os.link(archive, tmp_path / 'second')
    if kind == 'metadata_hardlink': os.link(metadata, tmp_path / 'second')
    if kind == 'archive_symlink':
        link = tmp_path / 'linked'; link.symlink_to(archive); archive = link
    if kind == 'ancestor_symlink':
        link = tmp_path / 'linked'; link.symlink_to(tmp_path, target_is_directory=True); archive = link / archive.name
    with pytest.raises(ModelStageError): plan(archive, metadata)


@pytest.mark.parametrize('kind', ['checksum', 'truncated', 'trailing', 'zero_bomb', 'hidden_pax'])
def test_raw_archive_rejects_malformed_bytes_even_with_updated_digest(tmp_path, kind):
    archive, _, value, _ = packet(tmp_path)
    data = bytearray(archive.read_bytes())
    if kind == 'checksum': data[0] ^= 1
    if kind == 'truncated': data = data[:-1]
    if kind == 'trailing': data[-1] = 1
    if kind == 'zero_bomb': data.extend(bytes(10240))
    if kind == 'hidden_pax':
        data[156] = ord('x'); data[148:156] = b'        '
        data[148:156] = ('%06o\0 ' % sum(data[:512])).encode()
    archive.write_bytes(data); value['model']['sha256'] = digest(data)
    with pytest.raises(ModelStageError): inspect_archive(archive, value['model'])


def test_existing_target_rejects_extra_empty_directory(tmp_path):
    archive, metadata, value, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700); root = home / 'models'
    run_apply(archive, metadata, root, home)
    target = root / value['model']['manifest_sha256']; (target / 'extra').mkdir(mode=0o700)
    with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    assert (target / 'extra').is_dir()


def test_fresh_standalone_cli_plan_is_readonly(tmp_path):
    import subprocess
    import sys
    import deployment.model_stage as module
    archive, metadata, _, _ = packet(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = subprocess.run([sys.executable, '-B', module.__file__, '--archive', str(archive),
                             '--metadata', str(metadata)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['mutation'] is False
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_cli_argument_errors_never_echo_unknown_values():
    import subprocess
    import sys
    import deployment.model_stage as module
    marker = '--PRIVATE_ARGUMENT_MARKER'
    result = subprocess.run([sys.executable, '-B', module.__file__, '--archive', '/unused',
                             '--metadata', '/unused', marker], capture_output=True, text=True)
    assert result.returncode == 2
    assert marker not in result.stdout + result.stderr
    assert 'invalid_model_stage_arguments' in result.stdout + result.stderr


def test_file_map_cannot_declare_file_as_another_files_parent(tmp_path):
    archive, metadata, value, _ = packet(tmp_path)
    value['model']['files']['nested'] = digest(b'file cannot also be directory')
    metadata.write_text(json.dumps(value))
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    with pytest.raises(ModelStageError):
        run_apply(archive, metadata, home / 'models', home)
    assert list(home.iterdir()) == []


def test_nonascii_ustar_name_rejected_even_with_valid_checksum_and_archive_hash(tmp_path):
    archive, metadata, value, _ = packet(tmp_path)
    data = bytearray(archive.read_bytes())
    data[0] = 0xff
    data[148:156] = b'        '
    checksum = sum(data[:512])
    data[148:156] = ('%06o\0 ' % checksum).encode('ascii')
    assert int(data[148:154], 8) == sum(data[:148]) + 8 * 32 + sum(data[156:512])
    archive.write_bytes(data)
    value['model']['sha256'] = digest(data)
    value['files']['model-assets.tar'] = digest(data)
    metadata.write_text(json.dumps(value))
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    with pytest.raises(ModelStageError):
        run_apply(archive, metadata, home / 'models', home)
    assert list(home.iterdir()) == []


@pytest.mark.parametrize('valid', [True, False])
def test_target_published_while_acquiring_lock_is_reverified_and_preserved(tmp_path, monkeypatch, valid):
    import fcntl
    archive, metadata, value, _ = packet(tmp_path)
    home = tmp_path / 'home'; home.mkdir(mode=0o700)
    parent = home / 'owned'; parent.mkdir(mode=0o700)
    root = parent / 'models'
    run_apply(archive, metadata, root, home)
    target = root / value['model']['manifest_sha256']
    pending = home / 'pending-publication'; target.rename(pending)
    if not valid:
        (pending / 'weights.bin').write_bytes(b'changed by another publisher')
    expected = {p.relative_to(pending).as_posix(): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
                for p in pending.rglob('*') if p.is_file()}
    original = fcntl.flock
    def publish_then_lock(fd, operation):
        pending.rename(target)
        return original(fd, operation)
    monkeypatch.setattr(fcntl, 'flock', publish_then_lock)
    if valid:
        result = run_apply(archive, metadata, root, home)
        assert result['status'] == 'already_present' and result['mutation'] is False
    else:
        with pytest.raises(ModelStageError): run_apply(archive, metadata, root, home)
    assert {p.relative_to(target).as_posix(): (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes())
            for p in target.rglob('*') if p.is_file()} == expected
    assert not list(root.glob('stage-*'))
