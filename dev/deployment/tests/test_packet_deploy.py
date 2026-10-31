"""Prepared packet transport bridge: local fixtures and fake SSH only."""
import ast
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest
from deployment import __main__ as cli, prepare
from deployment.tests.test_deployment import spec


@pytest.fixture
def packet(tmp_path):
    source = tmp_path / 'source'; (source / 'detector').mkdir(parents=True)
    (source / 'detector/__main__.py').write_text('# original')
    output = tmp_path / 'packet'
    prepare.build(source, spec(tmp_path).as_dict(), output)
    trusted = hashlib.sha256((output / 'release-manifest.json').read_bytes()).hexdigest()
    return source, output, trusted


def forbidden(*args, **kwargs):
    pytest.fail('private access or transport must not be reached')


def test_packet_plan_is_local_and_does_not_rebuild(packet, monkeypatch, capsys):
    source, output, trusted = packet
    (source / 'detector/__main__.py').write_text('# changed')
    monkeypatch.setattr(cli, 'bundle', forbidden)
    monkeypatch.setattr(cli, 'load_access', forbidden)
    monkeypatch.setattr(cli, 'password_session', forbidden)
    assert cli.main(['plan', '--role', 'gpu', '--packet', str(output)], run=forbidden) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['sha256'] == hashlib.sha256((output / 'application.tar').read_bytes()).hexdigest()


@pytest.mark.parametrize('kind', ['wrong_hash', 'missing_hash', 'wrong_role', 'spec_override', 'source_override', 'python_override', 'corrupt_app', 'corrupt_spec'])
def test_invalid_packet_fails_before_credentials(packet, monkeypatch, kind, capsys):
    source, output, trusted = packet
    args = ['deploy', '--role', 'general' if kind == 'wrong_role' else 'gpu', '--packet', str(output), '--password-auth']
    if kind != 'missing_hash': args += ['--expected-manifest-sha256', '0' * 64 if kind == 'wrong_hash' else trusted]
    if kind == 'spec_override': args += ['--spec', str(output / 'deploy.json')]
    if kind == 'source_override': args += ['--source', str(source)]
    if kind == 'python_override': args += ['--python', spec(output.parent).python]
    if kind == 'corrupt_app': (output / 'application.tar').write_bytes(b'PRIVATE_BAD')
    if kind == 'corrupt_spec': (output / 'deploy.json').write_bytes(b'PRIVATE_BAD')
    monkeypatch.setattr(cli, 'load_access', forbidden); monkeypatch.setattr(cli, 'password_session', forbidden)
    assert cli.main(args, run=forbidden) == 2
    assert 'PRIVATE_BAD' not in capsys.readouterr().out


@pytest.mark.parametrize('mode,status', [('preflight','preflight_ok'), ('deploy','deployed'), ('verify','verified'), ('rollback','rolled_back')])
def test_remote_modes_use_verified_spec_and_exact_packet_bytes(packet, monkeypatch, mode, status):
    source, output, trusted = packet
    original = (output / 'application.tar').read_bytes()
    (source / 'detector/__main__.py').write_text('# mutable changed source')
    monkeypatch.setattr(cli, 'bundle', forbidden)
    monkeypatch.setattr(cli, 'load_access', lambda *a: {'hostname': 'synthetic-host'})
    monkeypatch.setattr(cli, 'ssh_argv', lambda *a, **k: ['fake-ssh'])
    def transport(command, **kwargs):
        encoded = ast.literal_eval(kwargs['input'].rsplit('base64.b64decode(', 1)[1].split('))))', 1)[0])
        request = json.loads(base64.b64decode(encoded))
        assert request['spec'] == json.loads((output / 'deploy.json').read_text())
        if mode == 'deploy':
            assert base64.b64decode(request['bundle']) == original
            assert request['sha256'] == hashlib.sha256(original).hexdigest()
        release = hashlib.sha256(original).hexdigest() if mode != 'rollback' else '0' * 64
        return SimpleNamespace(stdout=json.dumps({'status': status, 'role': 'gpu', 'release': release}), returncode=0)
    assert cli.main([mode, '--role', 'gpu', '--packet', str(output), '--expected-manifest-sha256', trusted], run=transport) == 0


@pytest.mark.parametrize('name', ['application.tar', 'deploy.json'])
def test_change_after_verify_is_rejected_before_private_access(packet, monkeypatch, name):
    _, output, trusted = packet
    original = prepare.verify
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (output / name).write_bytes(b'changed after verification')
        return result
    monkeypatch.setattr(prepare, 'verify', changed)
    monkeypatch.setattr(cli, 'load_access', forbidden); monkeypatch.setattr(cli, 'password_session', forbidden)
    assert cli.main(['deploy', '--role', 'gpu', '--packet', str(output), '--expected-manifest-sha256', trusted], run=forbidden) == 2


@pytest.mark.parametrize('mode', ['preflight', 'verify', 'rollback'])
def test_every_remote_mode_requires_trusted_digest(packet, monkeypatch, mode):
    _, output, _ = packet
    monkeypatch.setattr(cli, 'load_access', forbidden); monkeypatch.setattr(cli, 'password_session', forbidden)
    assert cli.main([mode, '--role', 'gpu', '--packet', str(output)], run=forbidden) == 2


def test_fresh_cli_packet_plan(packet):
    import subprocess
    import sys
    _, output, trusted = packet
    result = subprocess.run([sys.executable, '-B', '-m', 'deployment', 'plan', '--role', 'gpu',
                             '--packet', str(output), '--expected-manifest-sha256', trusted],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert json.loads(result.stdout)['remote_mutation'] is False


def test_digest_without_packet_rejected_before_access(monkeypatch):
    monkeypatch.setattr(cli, 'load_access', forbidden)
    assert cli.main(['preflight', '--role', 'gpu', '--expected-manifest-sha256', 'a' * 64], run=forbidden) == 2


@pytest.mark.parametrize('mode,status', [('verify', 'verified'), ('deploy', 'deployed')])
@pytest.mark.parametrize('release', [None, '0' * 64, 'PRIVATE_INVALID_DIGEST', 123])
def test_packet_success_requires_exact_reported_release(packet, monkeypatch, capsys, mode, status, release):
    _, output, trusted = packet
    monkeypatch.setattr(cli, 'load_access', lambda *a: {'hostname': 'synthetic-host'})
    monkeypatch.setattr(cli, 'ssh_argv', lambda *a, **k: ['fake-ssh'])
    response = {'status': status, 'role': 'gpu'}
    if release is not None: response['release'] = release
    result = cli.main([mode, '--role', 'gpu', '--packet', str(output), '--expected-manifest-sha256', trusted],
                      run=lambda *a, **k: SimpleNamespace(stdout=json.dumps(response), returncode=0))
    assert result == 2
    public = capsys.readouterr().out
    assert json.loads(public)['status'] == 'error'
    assert 'PRIVATE_INVALID_DIGEST' not in public
