"""Explicit engineering activation of a completed remote model export."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from urllib.parse import urlsplit

from .__main__ import _packet_inputs
from .askpass import password_session
from .core import DeploymentError, load_access, safe_path, ssh_argv
from .model_stage import _file, _same_file


def load_context(directory):
    try:
        directory = Path(directory).absolute()
        info = directory.lstat()
        if (directory.resolve() != directory or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError()
        with _file(directory / "context.json", 1024 * 1024) as (handle, before):
            if before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o600:
                raise ValueError()
            data = handle.read(1024 * 1024 + 1)
            if not _same_file(before, os.fstat(handle.fileno())):
                raise ValueError()
        context = json.loads(data)
        if (set(context) != {"schema_version", "packet", "trusted_sha256", "home", "python", "trial", "application_sha256"}
                or context["schema_version"] != "1.0"
                or context["packet"] != str(directory / "packet")
                or any(not isinstance(context[key], str) or not re.fullmatch(r"[a-f0-9]{64}", context[key])
                       for key in ("trusted_sha256", "application_sha256"))
                or any(not safe_path(context[key]) for key in ("home", "python", "trial"))):
            raise ValueError()
        home, trial = Path(context["home"]), Path(context["trial"])
        if home == Path("/") or trial.parent != home or not re.fullmatch(r"b-gpu-trial-[a-f0-9]{32}", trial.name):
            raise ValueError()
        if context["python"] not in {str(home / name / "envs/py312/bin/python")
                                     for name in ("miniforge3", "anaconda3", "miniconda3")}:
            raise ValueError()
        spec, _, digest = _packet_inputs(directory / "packet", context["trusted_sha256"])
        if (spec.role != "gpu" or spec.device != "cuda" or spec.root != str(home / "codyssey-pii-gpu")
                or spec.python != context["python"] or spec.env_file != str(trial / "private.env")
                or spec.tls_ca_file != str(trial / "server.pem") or digest != context["application_sha256"]
                or urlsplit(spec.health_url).scheme != "https"):
            raise ValueError()
        return context, spec
    except Exception:
        raise DeploymentError("invalid_gpu_publish_context") from None


def validate_report(value):
    accepted = ({"status":"already_active"}, {"status":"activated", "quality_approved":False})
    if isinstance(value, dict):
        if any(value == item and all(type(value[key]) is type(item[key]) for key in item) for item in accepted):
            return value
        if (set(value) == {"status", "code", "rollback"} and value["status"] == "failed"
                and value["code"] in {"activation_failed", "activation_interrupted"}
                and value["rollback"] in {"success", "failure"}):
            return value
    raise DeploymentError("gpu_publish_failed")


def remote_source(request):
    return "REQUEST=" + repr(request) + "\n" + r'''
import json, os, platform, socket, stat, sys
from pathlib import Path
# Managed releases are immutable, including their file membership.
sys.dont_write_bytecode = True
try:
    q = REQUEST
    assert platform.system() == 'Linux' and socket.gethostname() == q['hostname']
    assert str(Path.home()) == q['home']
    root = Path(q['application_root'])
    assert all(not path.is_symlink() for path in (root,*root.parents))
    info = root.stat()
    assert info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700
    marker = root/'managed.json'
    assert not marker.is_symlink() and marker.stat().st_size < 4096
    assert json.loads(marker.read_text()) == {'manager':'codyssey-pii-v1','role':'gpu'}
    current = root/'current'
    assert current.is_symlink()
    verifier = {'__name__':'gpu_publish_release_verifier'}
    exec(compile(q['remote_source'],'deployment_remote.py','exec'),verifier)
    release = verifier['checked_release'](root,os.readlink(current),'gpu')
    assert release.name == q['application_sha256']
    # Resolve the managed immutable release before importing detector validators.
    sys.path.insert(0,str(release))
    activation = {'__name__':'gpu_publish_activation'}
    exec(compile(q['activation_source'],'model_activate.py','exec'),activation)
    report = activation['activate'](q['candidate'],q['trial'],str(Path(q['trial'])/'private.env'),
        q['manifest_sha256'],origin=q['origin'],ca_file=str(Path(q['trial'])/'server.pem'),
        allow_engineering_test=True)
    print(json.dumps(report))
except Exception:
    print(json.dumps({'status':'error','code':'gpu_publish_failed'}))
    raise SystemExit(2)
'''


def publish(directory, candidate, manifest_sha256, *, allow_engineering_test=False, run=subprocess.run):
    if (allow_engineering_test is not True or not safe_path(candidate)
            or not isinstance(manifest_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", manifest_sha256)):
        raise DeploymentError("invalid_gpu_publish_arguments")
    context, spec = load_context(directory)
    private = Path(__file__).resolve().parents[1] / ".private"
    access_file = private / "server-access.local.json"
    access = load_access(access_file, "gpu")
    request = {"hostname":access["hostname"], "home":context["home"], "trial":context["trial"],
        "application_root":spec.root, "application_sha256":context["application_sha256"],
        "candidate":candidate, "manifest_sha256":manifest_sha256,
        "origin":"https://127.0.0.1:" + str(urlsplit(spec.health_url).port),
        "remote_source":Path(__file__).with_name("remote.py").read_text(),
        "activation_source":Path(__file__).with_name("model_activate.py").read_text()}
    command = ssh_argv(access, private / "known_hosts", context["python"], password=True)
    try:
        with password_session(access_file, "gpu") as environment:
            result = run(command, input=remote_source(request), text=True, capture_output=True,
                         env=environment, timeout=900, check=False)
        if result.returncode or len(result.stdout) > 4096:
            raise ValueError()
        return validate_report(json.loads(result.stdout))
    except Exception:
        raise DeploymentError("gpu_publish_failed") from None


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message): self.exit(2, "invalid_gpu_publish_arguments\n")
    parser = Parser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--allow-engineering-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = publish(args.deployment, args.candidate, args.manifest_sha256,
                         allow_engineering_test=args.allow_engineering_test)
        print(json.dumps(report))
        return 0 if report["status"] in {"activated", "already_active"} else 1
    except Exception:
        print(json.dumps({"status":"error", "code":"gpu_publish_failed"})); return 2


if __name__ == "__main__":
    raise SystemExit(main())
