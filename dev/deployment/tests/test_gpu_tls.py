"""R10/R11/T07/T10: local certificate, auth and managed endpoint contracts."""
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from deployment.core import DeploymentError, Spec
from deployment import remote
from detector.server import DetectorConfigError, ServerSettings, settings_from_env
from detector.tests.test_runtime import FakeBackend, package


@pytest.fixture
def certificates(tmp_path):
    result = []
    for name in ('trusted', 'other'):
        cert, key = tmp_path / (name + '.pem'), tmp_path / (name + '.key')
        config = tmp_path / (name + '.cnf')
        config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n'
                          '[dn]\nCN=127.0.0.1\n[ext]\nsubjectAltName=IP:127.0.0.1\n'
                          'basicConstraints=critical,CA:TRUE\n'
                          'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
        subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '1', '-keyout', str(key), '-out', str(cert), '-config', str(config)],
                       capture_output=True, check=True, timeout=20)
        key.chmod(0o600)
        result.append((cert, key))
    return result


def tls_env(certificates, **changes):
    cert, key = certificates[0]
    return {'PII_DETECTOR_TOKEN': 'synthetic-private-token', 'PII_MODEL_PACKAGE': '/synthetic/model',
            'PII_DETECTOR_HOST': '0.0.0.0', 'PII_DETECTOR_TLS_CERT_FILE': str(cert),
            'PII_DETECTOR_TLS_KEY_FILE': str(key)} | changes


def gpu_spec(tmp_path, **changes):
    return Spec.parse({'role': 'gpu', 'root': str(tmp_path / 'release'),
                       'python': '/opt/conda/envs/py312/bin/python', 'env_file': str(tmp_path / 'env'),
                       'device': 'cpu'} | changes)


def test_tls_settings_and_uvicorn_kwargs(certificates, monkeypatch):
    import detector.server as server
    settings = settings_from_env(tls_env(certificates))
    assert settings.host == '0.0.0.0' and settings.tls_cert_file == certificates[0][0]
    assert str(certificates[0][1]) not in repr(settings)
    calls = []
    monkeypatch.setattr(server, 'settings_from_env', lambda: settings)
    monkeypatch.setattr(server.importlib, 'import_module', lambda _: SimpleNamespace(run=lambda *a, **kw: calls.append(kw)))
    assert server.main([]) == 0
    assert calls[0]['host'] == '0.0.0.0'
    assert calls[0]['ssl_certfile'] == str(certificates[0][0])
    assert calls[0]['ssl_keyfile'] == str(certificates[0][1])
    assert calls[0]['ssl_keyfile_password'] == ''  # Never invoke an interactive key prompt.
    assert calls[0]['access_log'] is False


@pytest.mark.parametrize('variant', ['missing_key', 'missing_cert', 'bad_host', 'bad_mode', 'mismatch', 'symlink', 'owner'])
def test_tls_config_rejected_before_model_or_server_start(certificates, tmp_path, monkeypatch, variant):
    values = tls_env(certificates)
    if variant == 'missing_key': values.pop('PII_DETECTOR_TLS_KEY_FILE')
    if variant == 'missing_cert': values.pop('PII_DETECTOR_TLS_CERT_FILE')
    if variant == 'bad_host': values['PII_DETECTOR_HOST'] = 'example.test'
    if variant == 'bad_mode': certificates[0][1].chmod(0o644)
    if variant == 'mismatch': values['PII_DETECTOR_TLS_KEY_FILE'] = str(certificates[1][1])
    if variant == 'symlink':
        link = tmp_path / 'linked.key'; link.symlink_to(certificates[0][1]); values['PII_DETECTOR_TLS_KEY_FILE'] = str(link)
    if variant == 'owner': monkeypatch.setattr(os, 'getuid', lambda: certificates[0][1].stat().st_uid + 1)
    with pytest.raises(DetectorConfigError) as error:
        settings_from_env(values)
    assert str(certificates[0][1]) not in str(error.value)


def test_spec_tls_defaults_and_exposed_bind_gate(tmp_path, certificates):
    assert gpu_spec(tmp_path).bind_host == '127.0.0.1'
    secure = dict(bind_host='0.0.0.0', health_url='https://127.0.0.1:8766/healthz',
                  ready_url='https://127.0.0.1:8766/readyz', tls_ca_file=str(certificates[0][0]))
    assert gpu_spec(tmp_path, **secure).tls_ca_file == str(certificates[0][0])
    for changes in [dict(bind_host='0.0.0.0'), dict(bind_host='localhost'),
                    secure | {'ready_url': 'http://127.0.0.1:8766/readyz'},
                    secure | {'tls_ca_file': 'relative.pem'}]:
        with pytest.raises(DeploymentError): gpu_spec(tmp_path, **changes)


@pytest.mark.parametrize('configured,address,expected', [
    ('127.0.0.1', '0100007F', True), ('127.0.0.1', '00000000', False),
    ('0.0.0.0', '00000000', True), ('0.0.0.0', '0100007F', False)])
def test_process_must_own_exact_configured_bind_address(tmp_path, configured, address, expected):
    config = gpu_spec(tmp_path).as_dict() | {'bind_host': configured}
    proc = tmp_path / 'proc'; process = proc / '42'
    (process / 'fd').mkdir(parents=True); (proc / 'net').mkdir()
    (process / 'cmdline').write_bytes(config['python'].encode() + b'\0-m\0detector\0')
    current = Path(config['root']) / 'current'; current.mkdir(parents=True)
    (process / 'cwd').symlink_to(current); (process / 'fd/3').symlink_to('socket:[123]')
    (proc / 'net/tcp').write_text(f'header\n0: {address}:223E 00000000:0000 0A 0:0 00:0 0 0 0 123\n')
    assert remote.owns_endpoints(config, '42', proc) is expected


@pytest.mark.parametrize('variant', ['valid', 'bind_mismatch', 'http_mismatch', 'bad_ca', 'bad_key'])
def test_tls_preflight_precedes_application_and_mutation(tmp_path, certificates, monkeypatch, variant):
    monkeypatch.setattr(remote.platform, 'system', lambda: 'Linux')
    model = tmp_path / 'model'; model.mkdir(); (model / 'manifest.json').write_text('{}')
    values = tls_env(certificates, PII_MODEL_PACKAGE=str(model), PII_DETECTOR_DEVICE='cpu')
    config = gpu_spec(tmp_path, bind_host='0.0.0.0', health_url='https://127.0.0.1:8766/healthz',
                      ready_url='https://127.0.0.1:8766/readyz', tls_ca_file=str(certificates[0][0])).as_dict()
    if variant == 'bind_mismatch': values['PII_DETECTOR_HOST'] = '127.0.0.1'
    if variant == 'http_mismatch': config['ready_url'] = 'http://127.0.0.1:8766/readyz'
    if variant == 'bad_ca': config['tls_ca_file'] = str(tmp_path / 'missing.pem')
    if variant == 'bad_key': certificates[0][1].chmod(0o644)
    env = Path(config['env_file']); env.write_text('\n'.join(f'{k}={v}' for k, v in values.items())); env.chmod(0o600)
    calls = []
    monkeypatch.setattr(remote, 'application_probe', lambda _: calls.append('app') or {})
    monkeypatch.setattr(remote, 'command', lambda _: calls.append('systemd'))
    if variant == 'valid':
        remote.preflight({'role': 'gpu', 'spec': config}); assert calls == ['app', 'systemd']
    else:
        with pytest.raises(remote.RemoteError): remote.preflight({'role': 'gpu', 'spec': config})
        assert calls == []
    assert not Path(config['root']).exists()


def test_actual_uvicorn_tls_auth_strict_ca_and_remote_readiness(tmp_path, certificates, monkeypatch):
    import uvicorn
    from detector.server import create_server
    root, _ = package(tmp_path)
    settings = settings_from_env(tls_env(certificates, PII_MODEL_PACKAGE=str(root), PII_DETECTOR_HOST='127.0.0.1'))
    app = create_server(settings, loader=lambda *args: FakeBackend())
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(10)
    port = listener.getsockname()[1]
    cert, key = certificates[0]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, ssl_certfile=str(cert),
                            ssl_keyfile=str(key), access_log=False, log_config=None, log_level='critical'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline: time.sleep(.02)
        assert server.started
        origin = f'https://127.0.0.1:{port}'
        with httpx.Client(verify=ssl.create_default_context(cafile=str(cert)), trust_env=False) as client:
            assert client.get(origin + '/readyz').status_code == 200
            assert client.post(origin + '/v1/inspect', json={}).status_code == 401
            response = client.post(origin + '/v1/inspect', headers={'Authorization': 'Bearer synthetic-private-token'},
                json={'schema_version': '1.0', 'request_id': 'r1', 'inspection_id': 'i1', 'direction': 'outbound',
                      'segments': [{'segment_id': 's1', 'text': 'synthetic'}]})
            assert response.status_code == 200 and response.json()['status'] == 'complete'
        with httpx.Client(verify=ssl.create_default_context(cafile=str(certificates[1][0])), trust_env=False) as client:
            with pytest.raises(httpx.ConnectError): client.get(origin + '/healthz')
        with httpx.Client(verify=ssl.create_default_context(cafile=str(cert)), trust_env=False) as client:
            with pytest.raises(httpx.ConnectError): client.get(origin.replace('127.0.0.1', 'localhost') + '/healthz')
        config = gpu_spec(tmp_path, health_url=origin + '/healthz', ready_url=origin + '/readyz', tls_ca_file=str(cert)).as_dict()
        monkeypatch.setattr(remote, 'owns_endpoints', lambda *args: True)  # Linux /proc is tested separately.
        control = lambda args: 'active' if 'is-active' in args else '42'
        assert remote.healthy(config, control)
        assert not remote.healthy(config | {'tls_ca_file': str(certificates[1][0])}, control)
        assert not remote.healthy(config | {'tls_ca_file': None}, control)
    finally:
        server.should_exit = True; thread.join(timeout=5); listener.close()
        assert not thread.is_alive()
