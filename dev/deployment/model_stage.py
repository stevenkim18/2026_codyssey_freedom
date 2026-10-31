"""Standalone stdlib model-asset validator/stager. No network, loading or deletion."""
import argparse
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import socket
import stat
import sys
import uuid


MAX_ARCHIVE_BYTES = 2 * 1024 ** 3
MAX_FILES = 1000
MAX_JSON_BYTES = 1024 * 1024
MANAGER = 'codyssey-model-stage-v1'


class ModelStageError(Exception):
    """Content-free operational failure."""


def _require(value, code='invalid_model_archive'):
    if not value:
        raise ModelStageError(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'invalid_metadata')
        result[key] = value
    return result


def _json(data):
    try:
        _require(len(data) <= MAX_JSON_BYTES, 'invalid_metadata')
        return json.loads(data, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except Exception:
        raise ModelStageError('invalid_metadata') from None


def _name(name):
    if not isinstance(name, str) or not 1 <= len(name) <= 256:
        return False
    parts = PurePosixPath(name).parts
    return (name == '/'.join(parts) and len(parts) <= 32 and not name.startswith('/')
            and all(part not in {'.', '..'} and not part.startswith('.')
                    and re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in parts))


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _file_map(value):
    _require(isinstance(value, dict) and 1 <= len(value) <= MAX_FILES, 'invalid_metadata')
    _require(all(_name(name) and _hash(digest) for name, digest in value.items()), 'invalid_metadata')
    for name in value:
        parts = name.split('/')
        _require(all('/'.join(parts[:i]) not in value for i in range(1, len(parts))), 'invalid_metadata')
    return value


def _open_path(path, flags, mode=0o600):
    """Walk every ancestor with directory descriptors; never follow a link."""
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts, 'unsafe_source_file')
    directory = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        return os.open(path.name, flags | os.O_NOFOLLOW, mode, dir_fd=directory)
    finally:
        os.close(directory)


@contextmanager
def _file(path, limit, *, allow_empty=False):
    path = Path(path).absolute()
    try:
        _require(path.resolve() == path, 'unsafe_source_file')
        fd = _open_path(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and (0 if allow_empty else 1) <= info.st_size <= limit, 'unsafe_source_file')
            yield handle, info
    except ModelStageError:
        raise
    except Exception:
        raise ModelStageError('unsafe_source_file') from None


def _same_file(before, after):
    return (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink) == (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)


def _number(field):
    value = field.strip(b'\0 ')
    _require(bool(value) and all(48 <= char <= 55 for char in value))
    return int(value, 8)


def _text(field):
    value, separator, rest = field.partition(b'\0')
    _require(not separator or not any(rest))
    try:
        return value.decode('ascii')
    except UnicodeError:
        raise ModelStageError('invalid_model_archive') from None


def _model_metadata(metadata):
    _require(isinstance(metadata, dict) and set(metadata) == {'archive', 'sha256', 'manifest_sha256', 'files'}, 'invalid_metadata')
    _require(metadata['archive'] == 'model-assets.tar' and _hash(metadata['sha256'])
             and _hash(metadata['manifest_sha256']), 'invalid_metadata')
    files = _file_map(metadata['files'])
    _require(files.get('manifest.json') == metadata['manifest_sha256'], 'invalid_metadata')
    return files


def _inspect(path, metadata):
    expected = _model_metadata(metadata)
    entries, actual, manifest_data = [], {}, None
    archive_digest = hashlib.sha256()
    with _file(path, MAX_ARCHIVE_BYTES) as (handle, before):
        _require(before.st_size % 10240 == 0)
        while True:
            header = handle.read(512); archive_digest.update(header)
            _require(len(header) == 512)
            if header == bytes(512):
                tail = handle.read(10240 + 1); archive_digest.update(tail)
                _require(512 <= len(tail) < 10240 and not any(tail) and not handle.read(1))
                break
            _require(header[257:263] == b'ustar\0' and header[263:265] == b'00'
                     and header[156:157] in {b'0', b'\0'} and not any(header[157:257])
                     and not any(header[500:512]))
            _require(_number(header[148:156]) == sum(header[:148]) + 8 * 32 + sum(header[156:]))
            for start, end in ((100, 108), (108, 116), (116, 124), (136, 148)):
                _number(header[start:end])
            name, prefix = _text(header[:100]), _text(header[345:500])
            name = prefix + '/' + name if prefix else name
            _require(_name(name) and name in expected and name not in actual and len(entries) < MAX_FILES)
            size = _number(header[124:136]); offset = handle.tell()
            _require(size <= MAX_ARCHIVE_BYTES and offset + size <= before.st_size)
            if name == 'manifest.json': _require(size <= MAX_JSON_BYTES)
            digest = hashlib.sha256(); remaining = size; pieces = []
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                _require(bool(chunk)); remaining -= len(chunk)
                digest.update(chunk); archive_digest.update(chunk)
                if name == 'manifest.json': pieces.append(chunk)
            padding = handle.read((-size) % 512); archive_digest.update(padding)
            _require(len(padding) == (-size) % 512 and not any(padding))
            actual[name] = digest.hexdigest()
            _require(actual[name] == expected[name])
            entries.append((name, offset, size))
            if name == 'manifest.json': manifest_data = b''.join(pieces)
        _require(_same_file(before, os.fstat(handle.fileno())), 'source_changed')
    _require(actual == expected and archive_digest.hexdigest() == metadata['sha256'])
    _require(manifest_data is not None and hashlib.sha256(manifest_data).hexdigest() == metadata['manifest_sha256'])
    manifest = _json(manifest_data)
    _require(isinstance(manifest, dict) and manifest.get('schema_version') == '1.0', 'invalid_metadata')
    declared = _file_map(manifest.get('files'))
    _require('manifest.json' not in declared and expected == {'manifest.json': metadata['manifest_sha256'], **declared}, 'invalid_metadata')
    return manifest, entries, before


def inspect_archive(path, metadata):
    """Shared pure validator: return the decoded model manifest; never write."""
    return _inspect(path, metadata)[0]


def _release(path):
    with _file(path, MAX_JSON_BYTES) as (handle, before):
        value = _json(handle.read(MAX_JSON_BYTES + 1))
        _require(_same_file(before, os.fstat(handle.fileno())), 'source_changed')
    _require(isinstance(value, dict) and set(value) == {'schema_version', 'role', 'application', 'spec', 'model', 'files'}, 'invalid_metadata')
    _require(value['schema_version'] == '1.0' and value['role'] == 'gpu', 'invalid_metadata')
    spec = value['spec']
    _require(isinstance(spec, dict) and set(spec) == {'file', 'sha256'}
             and spec['file'] == 'deploy.json' and _hash(spec['sha256']), 'invalid_metadata')
    app = value['application']
    _require(isinstance(app, dict) and set(app) == {'archive', 'sha256', 'bytes'}
             and app['archive'] == 'application.tar' and _hash(app['sha256'])
             and type(app['bytes']) is int and 0 < app['bytes'] <= 20 * 1024 * 1024, 'invalid_metadata')
    files = _file_map(value['files'])
    _require(set(files) == {'application.tar', 'deploy.json', 'APPLY.md', 'model-assets.tar'}, 'invalid_metadata')
    _model_metadata(value['model'])
    _require(files['application.tar'] == app['sha256'] and files['model-assets.tar'] == value['model']['sha256']
             and files['deploy.json'] == spec['sha256'], 'invalid_metadata')
    return value


def plan(archive, metadata):
    release = _release(metadata)
    _, entries, info = _inspect(archive, release['model'])
    return {'status': 'planned', 'role': 'gpu', 'file_count': len(entries), 'archive_bytes': info.st_size,
            'manifest_sha256': release['model']['manifest_sha256'], 'model_loaded': False, 'mutation': False}


def _directory(path, *, private=False):
    _require(path.is_absolute() and path.resolve() == path, 'unsafe_model_root')
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and not stat.S_IMODE(info.st_mode) & 0o022, 'unsafe_model_root')
    if private: _require(stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_model_root')
    return info


def _marker(root):
    info = _directory(root, private=True)
    marker = root / 'managed.json'
    with _file(marker, MAX_JSON_BYTES) as (handle, record):
        _require(record.st_uid == os.getuid() and stat.S_IMODE(record.st_mode) == 0o600, 'unsafe_model_root')
        value = _json(handle.read(MAX_JSON_BYTES + 1))
    _require(value == {'manager': MANAGER, 'role': 'gpu', 'uid': os.getuid(),
                       'device': info.st_dev, 'inode': info.st_ino}, 'unmanaged_model_root')


def _create_file(path, data):
    fd = _open_path(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())


def _mkdir(path):
    parent = _open_path(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
    finally:
        os.close(parent)


def _verify_target(target, expected):
    _directory(target, private=True)
    seen, seen_directories = {}, set()
    expected_directories = {'/'.join(name.split('/')[:i]) for name in expected for i in range(1, len(name.split('/')))}
    for parent, directories, names in os.walk(target, followlinks=False):
        parent = Path(parent); _directory(parent, private=True)
        for name in directories:
            _directory(parent / name, private=True)
            seen_directories.add((parent / name).relative_to(target).as_posix())
        for name in names:
            path = parent / name; relative = path.relative_to(target).as_posix()
            _require(relative in expected, 'changed_existing_target')
            with _file(path, MAX_ARCHIVE_BYTES, allow_empty=True) as (handle, info):
                _require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600, 'changed_existing_target')
                seen[relative] = hashlib.file_digest(handle, 'sha256').hexdigest()
                _require(_same_file(info, os.fstat(handle.fileno())), 'changed_existing_target')
    _require(seen == expected and seen_directories == expected_directories, 'changed_existing_target')


def _populate(archive, stage, entries, expected, source_info):
    with _file(archive, MAX_ARCHIVE_BYTES) as (source, current):
        _require(_same_file(source_info, current), 'source_changed')
        for name, offset, size in entries:
            path = stage / name
            for parent in reversed(path.parents):
                if parent == stage or stage in parent.parents:
                    if not parent.exists(): _mkdir(parent)
                    _directory(parent, private=True)
            source.seek(offset); digest = hashlib.sha256(); remaining = size
            fd = _open_path(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(fd, 'wb') as output:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining)); _require(bool(chunk), 'source_changed')
                    remaining -= len(chunk); digest.update(chunk); output.write(chunk)
                output.flush(); os.fsync(output.fileno())
            _require(digest.hexdigest() == expected[name], 'source_changed')
        _require(_same_file(current, os.fstat(source.fileno())), 'source_changed')


def _publish(root_fd, stage_name, target_name):
    """Atomic no-replace: Linux production; Darwin supports local fixture tests."""
    library = ctypes.CDLL(None, use_errno=True)
    name, flag = ('renameat2', 1) if sys.platform.startswith('linux') else ('renameatx_np', 4)
    operation = getattr(library, name, None)
    _require(operation is not None, 'atomic_publish_unavailable')
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    result = operation(root_fd, stage_name.encode(), root_fd, target_name.encode(), flag)
    _require(result == 0, 'atomic_publish_failed')


def apply(archive, metadata, root, expected_host, *, system=platform.system, hostname=socket.gethostname, home=Path.home):
    """Explicit mutation; injected identity providers are for isolated tests only."""
    try:
        _require(system() == 'Linux' and isinstance(expected_host, str)
                 and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', expected_host)
                 and hostname() == expected_host, 'host_confirmation_failed')
        release = _release(metadata); model = release['model']
        _, entries, source_info = _inspect(archive, model)
        root, home = Path(root), Path(home())
        _require(root.is_absolute() and home.is_absolute() and root != home and home in root.parents, 'unsafe_model_root')
        _directory(home)
        for parent in root.parents:
            _directory(parent)
            if parent == home: break
        target = root / model['manifest_sha256']
        if root.exists() or root.is_symlink():
            _marker(root)
            if target.exists() or target.is_symlink():
                _verify_target(target, model['files'])
                return {'status': 'already_present', 'model_path': str(target), 'model_loaded': False, 'mutation': False}
        else:
            _mkdir(root)
            info = _directory(root, private=True)
            _create_file(root / 'managed.json', json.dumps({'manager': MANAGER, 'role': 'gpu', 'uid': os.getuid(),
                         'device': info.st_dev, 'inode': info.st_ino}, sort_keys=True).encode())
        _marker(root)
        lock_path = root / 'stage.lock'
        lock = _open_path(lock_path, os.O_RDWR | os.O_CREAT | os.O_NONBLOCK)
        with os.fdopen(lock, 'r+b') as handle:
            info = os.fstat(handle.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                     and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_model_lock')
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ModelStageError('model_stage_in_progress') from None
            _marker(root)
            if target.exists() or target.is_symlink():
                _verify_target(target, model['files'])
                return {'status': 'already_present', 'model_path': str(target), 'model_loaded': False, 'mutation': False}
            stage = root / ('stage-' + uuid.uuid4().hex)
            _mkdir(stage)
            _populate(archive, stage, entries, model['files'], source_info)
            _verify_target(stage, model['files'])
            # Native no-replace publication also rejects a racing empty directory.
            _require(not target.exists() and not target.is_symlink(), 'target_collision')
            root_fd = _open_path(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                _publish(root_fd, stage.name, target.name)
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
            _verify_target(target, model['files'])
        return {'status': 'applied', 'model_path': str(target), 'model_loaded': False, 'mutation': True}
    except ModelStageError:
        raise
    except Exception:
        raise ModelStageError('model_stage_failed') from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'invalid_model_stage_arguments\n')


def main(argv=None):
    parser = SafeParser(description='Read-only model plan by default; explicit apply never loads a model')
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--expected-host')
    args = parser.parse_args(argv)
    try:
        if args.apply:
            _require(args.root is not None and args.expected_host is not None, 'apply_confirmation_required')
            result = apply(args.archive, args.metadata, args.root, args.expected_host)
        else:
            _require(args.root is None and args.expected_host is None, 'unexpected_apply_options')
            result = plan(args.archive, args.metadata)
        print(json.dumps(result)); return 0
    except ModelStageError as error:
        print(json.dumps({'status': 'error', 'code': str(error)})); return 2
    except Exception:
        print(json.dumps({'status': 'error', 'code': 'model_stage_failed'})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
