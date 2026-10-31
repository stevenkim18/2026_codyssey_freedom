"""Explicit GPU trial preparation; never activates a service or changes a packet."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
from urllib.parse import urlsplit

from . import prepare
from .askpass import password_session
from .core import DeploymentError, Spec, load_access, ssh_argv
from .model_stage import _file, _same_file


def private_write(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def plan(packet, trusted):
    try:
        if not isinstance(trusted, str) or not re.fullmatch(r"[a-f0-9]{64}", trusted):
            raise ValueError()
        packet = Path(packet).absolute()
        manifest = prepare.verify(packet, expected_manifest_sha256=trusted)
        with _file(packet / "deploy.json", 1024 * 1024) as (handle, before):
            spec_bytes = handle.read(1024 * 1024 + 1)
            if (len(spec_bytes) > 1024 * 1024 or not _same_file(before, os.fstat(handle.fileno()))
                    or hashlib.sha256(spec_bytes).hexdigest() != manifest["files"]["deploy.json"]):
                raise ValueError()
        spec = Spec.parse(prepare._json(spec_bytes))
        root = Path(spec.env_file).parent
        if (spec.role != "gpu" or not manifest["model"] or spec.device != "cuda"
                or not re.fullmatch(r"b-gpu-trial-[a-f0-9]{32}", root.name)
                or spec.env_file != str(root / "private.env")
                or spec.tls_ca_file != str(root / "server.pem")
                or spec.bind_host != "0.0.0.0"
                or urlsplit(spec.health_url).scheme != "https"
                or urlsplit(spec.ready_url).port != urlsplit(spec.health_url).port):
            raise ValueError()
        return packet, manifest, spec, root
    except Exception:
        raise DeploymentError("invalid_gpu_release_packet") from None


def scp_argv(access, known_hosts, source, target):
    ssh = ssh_argv(access, known_hosts, "/usr/bin/python3", password=True)
    # Native SFTP-based scp streams the file, retaining every pinned SSH option.
    args = ["scp"]
    for value in ssh[1:-4]:
        if value != "-T":
            args.append(value)
    return args + ["-P", ssh[-3], str(source), ssh[-2] + ":" + target]


def response(completed, expected, fields):
    try:
        if completed.returncode or len(completed.stdout) > 16384:
            raise ValueError()
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or set(value) != fields or value["status"] != expected:
            raise ValueError()
        return value
    except Exception:
        raise DeploymentError("gpu_release_remote_failed") from None


def remote_source(request):
    # Standalone source is executed only by the explicit prepare command.
    body = r'''
import hashlib, ipaddress, json, os, platform, re, socket, stat, subprocess
from pathlib import Path
def require(value):
    if not value: raise ValueError()
def write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle: handle.write(data)
def command(args):
    result = subprocess.run(args, capture_output=True, timeout=60, check=False)
    require(result.returncode == 0)
    return result.stdout
def execute(q):
    require(platform.system() == 'Linux' and socket.gethostname() == q['hostname'])
    root = Path(q['root'])
    require(root.parent == Path.home() and re.fullmatch(r'b-gpu-trial-[a-f0-9]{32}', root.name))
    require(not Path.home().is_symlink())
    require(not command(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader']).strip())
    if q['mode'] == 'reserve':
        root.mkdir(mode=0o700, exist_ok=False)
        write(root / 'owner.json', json.dumps({'uid':os.getuid(),'archive':q['archive_sha256']}).encode())
        return {'status':'reserved'}
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    require(json.loads((root/'owner.json').read_text()) == {'uid':os.getuid(),'archive':q['archive_sha256']})
    archive = root/'model-assets.tar'
    info = archive.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid())
    digest = hashlib.sha256()
    with archive.open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''): digest.update(chunk)
    require(digest.hexdigest() == q['archive_sha256'])
    archive.chmod(0o600)
    write(root/'release-manifest.json', json.dumps(q['manifest']).encode())
    namespace = {'__name__':'gpu_release_stager'}
    exec(compile(q['stage_source'],'model_stage.py','exec'), namespace)
    staged = namespace['apply'](archive, root/'release-manifest.json', root/'models', q['hostname'])
    address = str(ipaddress.IPv4Address(q['private_ip']))
    config = ('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\nCN=GPU trial\n'
        '[ext]\nsubjectAltName=IP:127.0.0.1,IP:'+address+'\nbasicConstraints=critical,CA:TRUE\n'
        'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n')
    write(root/'tls.cnf',config.encode())
    require(not (root/'server.key').exists() and not (root/'server.pem').exists())
    command(['/usr/bin/openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','7',
        '-config',str(root/'tls.cnf'),'-keyout',str(root/'server.key'),'-out',str(root/'server.pem')])
    (root/'server.key').chmod(0o600)
    (root/'server.pem').chmod(0o600)
    require(re.fullmatch(r'[A-Za-z0-9_-]{43}',q['token']))
    env = {'PII_DETECTOR_TOKEN':q['token'],'PII_MODEL_PACKAGE':staged['model_path'],
        'PII_DETECTOR_DEVICE':'cuda','PII_DETECTOR_HOST':'0.0.0.0','PII_DETECTOR_PORT':str(q['port']),
        'PII_DETECTOR_TLS_CERT_FILE':str(root/'server.pem'),'PII_DETECTOR_TLS_KEY_FILE':str(root/'server.key')}
    write(root/'private.env', ''.join(k+'='+v+'\n' for k,v in env.items()).encode())
    return {'status':'prepared','ca_pem':(root/'server.pem').read_text()}
try:
    os.umask(0o077)
    print(json.dumps(execute(REQUEST)))
except Exception:
    print(json.dumps({'status':'error','code':'gpu_release_failed'}))
    raise SystemExit(2)
'''
    return "REQUEST=" + repr(request) + "\n" + body


def apply(packet, trusted, output, *, run=subprocess.run):
    packet, manifest, spec, root = plan(packet, trusted)
    private = Path(__file__).resolve().parents[1] / ".private"
    access_file = private / "server-access.local.json"
    access = load_access(access_file, "gpu")
    address = str(ipaddress.IPv4Address(access["private_ip"]))
    output = Path(output).absolute()
    if any(parent.is_symlink() for parent in (output, *output.parents)):
        raise DeploymentError("unsafe_local_output")
    output.mkdir(mode=0o700, exist_ok=False)
    token = secrets.token_urlsafe(32)
    # Retain recovery credential locally before first remote mutation; never print.
    private_write(output / "detector-token", token.encode())
    request = {"mode":"reserve", "root":str(root), "hostname":access["hostname"],
        "archive_sha256":manifest["model"]["sha256"]}
    command = ssh_argv(access, private / "known_hosts", "/usr/bin/python3", password=True)

    def remote(payload, expected, fields):
        with password_session(access_file, "gpu") as environment:
            result = run(command, input=remote_source(payload), text=True, capture_output=True,
                         env=environment, timeout=900, check=False)
        return response(result, expected, fields)

    remote(request, "reserved", {"status"})
    with password_session(access_file, "gpu") as environment:
        result = run(scp_argv(access, private / "known_hosts", packet / "model-assets.tar",
                             str(root / "model-assets.tar")), env=environment,
                     capture_output=True, timeout=1800, check=False)
    if result.returncode:
        raise DeploymentError("gpu_release_transfer_failed")
    request.update(mode="prepare", manifest=manifest, private_ip=address, token=token,
        port=urlsplit(spec.health_url).port, stage_source=Path(__file__).with_name("model_stage.py").read_text())
    result = remote(request, "prepared", {"status", "ca_pem"})
    if not isinstance(result["ca_pem"], str) or len(result["ca_pem"]) > 8192:
        raise DeploymentError("invalid_remote_ca")
    try:
        ssl.create_default_context(cadata=result["ca_pem"])
    except Exception:
        raise DeploymentError("invalid_remote_ca") from None
    private_write(output / "detector-ca.pem", result["ca_pem"].encode())
    private_write(output / "prepared.json", json.dumps({"packet":str(packet), "trusted_sha256":trusted,
        "remote_root":str(root), "detector_url":f"https://{address}:{urlsplit(spec.health_url).port}/v1/inspect",
        "application_deployed":False, "model_loaded":False}).encode())
    return {"status":"prepared", "application_deployed":False, "model_loaded":False}


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message): self.exit(2, "invalid_gpu_release_arguments\n")
    parser = Parser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare"))
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            plan(args.packet, args.expected_manifest_sha256)
            result = {"status":"planned", "remote_mutation":False}
        else:
            if args.output is None: raise DeploymentError("private_output_required")
            result = apply(args.packet, args.expected_manifest_sha256, args.output)
        print(json.dumps(result)); return 0
    except Exception:
        print(json.dumps({"status":"error", "code":"gpu_release_failed"})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
