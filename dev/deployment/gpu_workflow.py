"""GPU trial workflow with read-only discovery and an immutable local packet."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import uuid

from . import gpu_release, prepare
from . import __main__ as deployment_cli
from .askpass import password_session
from .core import DeploymentError, Spec, bundle, load_access, safe_path, ssh_argv


DISCOVERY_SOURCE = r'''
import json, os, platform, socket, stat, subprocess, sys
from pathlib import Path
failure_code = 'gpu_discovery_failed'
try:
    assert platform.system() == 'Linux' and socket.gethostname() == EXPECTED
    home = Path.home()
    assert home.is_absolute() and home.resolve() == home
    assert all(not item.is_symlink() for item in (home,*home.parents))
    info = home.stat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o022
    gpu = subprocess.run(['/usr/bin/nvidia-smi','--query-compute-apps=pid','--format=csv,noheader'],
        capture_output=True,text=True,timeout=10,check=False)
    assert gpu.returncode == 0 and not gpu.stdout.strip()
    found = []
    for distribution in ('miniforge3','anaconda3','miniconda3'):
        executable = home/distribution/'envs/py312/bin/python'
        if not executable.is_file(): continue
        assert all(not parent.is_symlink() for parent in executable.parents)
        environment = executable.parent.parent
        assert executable.resolve().is_relative_to(environment)
        assert environment.stat().st_uid == os.getuid()
        result = subprocess.run([str(executable),'-I','-c',
            'import json,sys,google.protobuf,sentencepiece;print(json.dumps({"version":list(sys.version_info[:3]),"prefix":sys.prefix}))'],
            capture_output=True,text=True,timeout=10,check=False)
        failure_code = 'gpu_runtime_probe_failed'
        assert result.returncode == 0 and len(result.stdout) < 1024
        metadata = json.loads(result.stdout)
        assert set(metadata) == {'version','prefix'} and metadata['prefix'] == str(environment)
        version = metadata['version']
        assert len(version) == 3 and all(type(v) is int and 0 <= v < 100 for v in version) and version[:2] == [3,12]
        found.append((str(executable),'.'.join(map(str,version))))
        failure_code = 'gpu_discovery_failed'
    assert len(found) == 1
    print(json.dumps({'status':'discovered','home':str(home),'python':found[0][0],
        'python_version':found[0][1],'gpu_compute_count':0}))
except Exception:
    print(json.dumps({'status':'error','code':failure_code}))
    raise SystemExit(2)
'''


def validate_discovery(value):
    try:
        if (not isinstance(value, dict) or set(value) != {"status", "home", "python", "python_version", "gpu_compute_count"}
                or value["status"] != "discovered" or type(value["gpu_compute_count"]) is not int
                or value["gpu_compute_count"] != 0 or not safe_path(value["home"]) or value["home"] == "/"
                or not safe_path(value["python"]) or not re.fullmatch(r"3\.12\.[0-9]{1,2}", value["python_version"])):
            raise ValueError()
        home = Path(value["home"])
        if value["python"] not in {str(home / distro / "envs/py312/bin/python")
                                    for distro in ("miniforge3", "anaconda3", "miniconda3")}:
            raise ValueError()
        return value
    except Exception:
        raise DeploymentError("gpu_discovery_failed") from None


def discover(*, run=subprocess.run):
    """Return a private home/runtime record; print neither identity nor paths."""
    try:
        private = Path(__file__).resolve().parents[1] / ".private"
        access_file = private / "server-access.local.json"
        access = load_access(access_file, "gpu")
        command = ssh_argv(access, private / "known_hosts", "/usr/bin/python3", password=True)
        with password_session(access_file, "gpu") as environment:
            result = run(command, input="EXPECTED=" + repr(access["hostname"]) + "\n" + DISCOVERY_SOURCE,
                text=True, capture_output=True, env=environment, timeout=60, check=False)
        if len(result.stdout) > 4096:
            raise ValueError()
        report = json.loads(result.stdout)
        if result.returncode and report == {"status":"error", "code":"gpu_runtime_probe_failed"}:
            raise DeploymentError("gpu_runtime_probe_failed")
        if result.returncode: raise ValueError()
        return validate_discovery(report)
    except DeploymentError:
        raise
    except Exception:
        raise DeploymentError("gpu_discovery_failed") from None


def persistence_source(record, hostname, enable_unit):
    return "HOME=" + repr(record["home"]) + "\nEXPECTED=" + repr(hostname) + "\nENABLE=" + repr(enable_unit) + "\n" + r'''
import json, os, platform, socket, subprocess
from pathlib import Path
try:
    assert platform.system() == 'Linux' and socket.gethostname() == EXPECTED
    assert str(Path.home()) == HOME
    uid = str(os.getuid())
    def command(args):
        result = subprocess.run(args,capture_output=True,text=True,timeout=20,check=False)
        assert result.returncode == 0 and len(result.stdout) <= 4096
        return result.stdout.strip()
    def linger():
        return command(['loginctl','show-user',uid,'--property=Linger','--value'])
    state = linger()
    assert state in ('yes','no')
    if state == 'no':
        command(['loginctl','enable-linger',uid])
    assert linger() == 'yes'
    if ENABLE:
        command(['systemctl','--user','enable','codyssey-pii-gpu.service'])
        assert command(['systemctl','--user','is-enabled','codyssey-pii-gpu.service']) == 'enabled'
    print(json.dumps({'status':'persistent','unit_enabled':ENABLE}))
except Exception:
    print(json.dumps({'status':'error','code':'gpu_persistence_failed'}))
    raise SystemExit(2)
'''


def ensure_persistence(record, *, enable_unit=False, run=subprocess.run):
    """Own-user linger only; B unit is enabled only after deployment verification."""
    try:
        private = Path(__file__).resolve().parents[1] / ".private"
        access_file = private / "server-access.local.json"
        access = load_access(access_file, "gpu")
        command = ssh_argv(access, private / "known_hosts", "/usr/bin/python3", password=True)
        with password_session(access_file, "gpu") as environment:
            result = run(command, input=persistence_source(record, access["hostname"], enable_unit),
                         text=True, capture_output=True, env=environment, timeout=90, check=False)
        if (result.returncode or len(result.stdout) > 4096
                or json.loads(result.stdout) != {"status":"persistent", "unit_enabled":enable_unit}):
            raise ValueError()
    except Exception:
        raise DeploymentError("gpu_persistence_failed") from None


def freeze_source(data, digest, output):
    """Extract only bounded regular role-owned Python files, then re-bundle."""
    try:
        if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError()
        members, seen, total = [], set(), 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                path = PurePosixPath(member.name)
                if (not member.isreg() or path.is_absolute() or str(path) != member.name
                        or len(path.parts) < 2 or path.parts[0] not in {"detector", "gateway"}
                        or any(part in {".", "..", "tests", "__pycache__"} or part.startswith(".") for part in path.parts)
                        or path.suffix != ".py" or member.name in seen or member.size < 0):
                    raise ValueError()
                total += member.size
                if total > 16 * 1024 * 1024 or len(seen) >= 1000:
                    raise ValueError()
                seen.add(member.name)
                content = archive.extractfile(member).read(member.size + 1)
                if len(content) != member.size: raise ValueError()
                members.append((path, content))
        if not members: raise ValueError()
        output = Path(output)
        output.mkdir(mode=0o700, exist_ok=False)
        for path, content in members:
            target = output / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            gpu_release.private_write(target, content)
        if bundle(output, "gpu")[1] != digest:
            raise ValueError()
    except Exception:
        raise DeploymentError("gpu_source_snapshot_failed") from None


def workflow(mode, source, model, output, *, port=8766, run=subprocess.run):
    if mode not in {"prepare", "deploy"} or type(port) is not int or not 1024 <= port <= 65535:
        raise DeploymentError("invalid_gpu_workflow_arguments")
    output = Path(output).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise DeploymentError("unsafe_gpu_workflow_output")
    # Bundle before creating output: --source dev and output dev/.private are safe.
    data, digest = bundle(source, "gpu")
    record = discover(run=run)
    output.mkdir(mode=0o700, exist_ok=False)
    gpu_release.private_write(output / "discovery.json", json.dumps(record).encode())
    home = Path(record["home"])
    trial = home / ("b-gpu-trial-" + uuid.uuid4().hex)
    spec = Spec.parse({"role":"gpu", "root":str(home / "codyssey-pii-gpu"),
        "python":record["python"], "env_file":str(trial / "private.env"), "device":"cuda",
        "bind_host":"0.0.0.0", "tls_ca_file":str(trial / "server.pem"),
        "health_url":f"https://127.0.0.1:{port}/healthz", "ready_url":f"https://127.0.0.1:{port}/readyz"})
    frozen = output / "frozen-source"
    freeze_source(data, digest, frozen)
    packet = output / "packet"
    manifest = prepare.build(frozen, spec.as_dict(), packet, model=model)
    if manifest["application"]["sha256"] != digest:
        raise DeploymentError("gpu_source_snapshot_failed")
    trusted = hashlib.sha256((packet / "release-manifest.json").read_bytes()).hexdigest()
    gpu_release.private_write(output / "trusted-manifest.sha256", (trusted + "\n").encode())
    context = {"schema_version":"1.0", "packet":str(packet), "trusted_sha256":trusted,
        "home":record["home"], "python":record["python"], "trial":str(trial), "application_sha256":digest}
    gpu_release.private_write(output / "context.json", json.dumps(context).encode())
    gpu_release.apply(packet, trusted, output / "connection", run=run)
    if mode == "deploy":
        ensure_persistence(record, enable_unit=False, run=run)
        arguments = ["--role", "gpu", "--packet", str(packet), "--expected-manifest-sha256", trusted, "--password-auth"]
        for step in ("preflight", "deploy", "verify"):
            if step != "verify":
                if discover(run=run) != record:
                    raise DeploymentError("gpu_resource_snapshot_changed")
            # Existing CLI emits bounded status only; keep aggregate output fixed.
            with redirect_stdout(io.StringIO()):
                result = deployment_cli.main([step, *arguments], run=run)
            if result != 0:
                raise DeploymentError("gpu_service_operation_failed")
        ensure_persistence(record, enable_unit=True, run=run)
    status = {"status":"verified" if mode == "deploy" else "prepared", "role":"gpu",
        "application_deployed":mode == "deploy", "model_quality_approved":False}
    gpu_release.private_write(output / "result.json", json.dumps(status).encode())
    return status


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message): self.exit(2, "invalid_gpu_workflow_arguments\n")
    parser = Parser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "deploy"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(workflow(args.mode, args.source, args.model, args.output, port=args.port)))
        return 0
    except DeploymentError as error:
        print(json.dumps({"status":"error", "code":str(error)}))
        return 2
    except Exception:
        print(json.dumps({"status":"error", "code":"gpu_workflow_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
