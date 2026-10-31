"""Stdlib remote runner. Sent over SSH stdin; never sources shell configuration."""
import base64
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid


USER_RUNTIME_BASE = Path("/run/user")


class RemoteError(Exception):
    pass


def validate_archive(data, digest, role=None):
    if len(data) > 20 * 1024 * 1024 or hashlib.sha256(data).hexdigest() != digest:
        raise RemoteError("invalid_bundle")
    members, names, total = [], set(), 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            allowed = {"detector", "gateway"} if role == "gpu" else {"observability"} if role == "general" else {"detector", "gateway", "observability"}
            asset = (len(path.parts) > 2 and path.parts[0] == "observability" and path.parts[1] in {"static", "templates"}
                     and path.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"})
            if (not member.isfile() or member.name != path.as_posix() or path.is_absolute()
                    or ".." in path.parts or len(path.parts) < 2 or path.parts[0] not in allowed
                    or any(part.startswith(".") or part in {"tests", "__pycache__"} for part in path.parts)
                    or (path.suffix != ".py" and not asset) or member.name in names or "\\" in member.name
                    or any(ord(char) < 33 or ord(char) > 126 for char in member.name)):
                raise RemoteError("invalid_bundle")
            total += member.size
            names.add(member.name)
            members.append(member)
            if total > 16 * 1024 * 1024 or len(members) > 1000:
                raise RemoteError("bundle_limit")
    if not members:
        raise RemoteError("invalid_bundle")
    return members


def command(args):
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
    if completed.returncode:
        raise RemoteError("service_command_failed")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def environment(path):
    path = Path(path)
    if (path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600
            or path.stat().st_uid != os.getuid() or path.stat().st_size > 65536):
        raise RemoteError("invalid_remote_environment")
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (not separator or not key.replace("_", "").isalnum() or key in values or not value
                or any(ord(c) < 33 or ord(c) > 126 or c in "\\\"'" for c in value)):
            raise RemoteError("invalid_remote_environment")
        values[key] = value
    return values


def preflight(request):
    spec = request.get("spec")
    expected = request.get("expected_hostname")
    if expected and socket.gethostname().split(".")[0] != expected.split(".")[0]:
        raise RemoteError("host_role_mismatch")
    if platform.system() != "Linux" or sys.version_info[:2] != (3, 12):
        raise RemoteError("unsupported_remote_runtime")
    result = {"os": platform.system(), "python": platform.python_version(), "cpu_count": os.cpu_count(),
              "role": request["role"], "contract": bool(spec)}
    if not spec:
        if request.get("application_python"):
            result["application_python_present"] = Path(request["application_python"]).is_file()
        return result
    values = environment(spec["env_file"])
    if spec["role"] == "gpu":
        allowed = {"PII_DETECTOR_TOKEN", "PII_MODEL_PACKAGE", "PII_DETECTOR_PORT", "PII_DETECTOR_DEVICE"}
        if (set(values) - allowed or not values.get("PII_DETECTOR_TOKEN")
                or values.get("PII_DETECTOR_DEVICE", "cpu") != spec["device"]):
            raise RemoteError("invalid_detector_environment")
        package = Path(values.get("PII_MODEL_PACKAGE", ""))
        if not package.is_absolute() or package.is_symlink() or not (package / "manifest.json").is_file():
            raise RemoteError("model_package_missing")
        from urllib.parse import urlsplit
        port = str(urlsplit(spec["ready_url"]).port)
        if values.get("PII_DETECTOR_PORT", "8766") != port or str(urlsplit(spec["health_url"]).port) != port:
            raise RemoteError("detector_port_mismatch")
    result.update(application_probe(spec))
    command(["systemctl", "--user", "show-environment"])
    return result


def application_probe(spec):
    """The Ubuntu stdlib bootstrap never substitutes for the selected app env."""
    python = Path(spec["python"])
    if not python.is_file():
        raise RemoteError("application_python_missing")
    probe = '''import json, sys, importlib.metadata
from pathlib import Path
spec = json.loads(sys.stdin.read())
if (sys.version_info[:2] != (3, 12)
        or Path(sys.executable).resolve() != Path(spec["python"]).resolve()
        or Path(sys.prefix).resolve() != Path(spec["python"]).parent.parent.resolve()):
    raise RuntimeError("application_environment_probe_failed")
for name, version in spec["requirements"].items():
    if importlib.metadata.version(name) != version:
        raise RuntimeError("application_environment_probe_failed")
result = {"application_python": "3.12", "dependencies": "verified"}
if spec["role"] == "gpu":
    import fastapi, uvicorn, gliner, torch
    result["cuda_available"] = torch.cuda.is_available()
    if spec["device"] == "cuda":
        if not result["cuda_available"]:
            raise RuntimeError("application_environment_probe_failed")
        torch.zeros(1, device="cuda")
        torch.cuda.synchronize()
        result["gpu_count"] = torch.cuda.device_count()
print("\\nCODEX_DEPLOY_REPORT:"+spec["_report_nonce"]+":"+json.dumps(result), flush=True)
'''
    nonce = uuid.uuid4().hex
    completed = subprocess.run([str(python), "-c", probe], input=json.dumps(spec | {"_report_nonce":nonce}),
                               text=True, capture_output=True, timeout=60, check=False)
    if completed.returncode:
        raise RemoteError("application_environment_probe_failed")
    try:
        prefix = "CODEX_DEPLOY_REPORT:"+nonce+":"
        reports = [line[len(prefix):] for line in completed.stdout.splitlines() if line.startswith(prefix)]
        if len(reports) != 1:
            raise ValueError
        result = json.loads(reports[0])
        if not isinstance(result, dict) or result.get("application_python") != "3.12" or result.get("dependencies") != "verified":
            raise ValueError
        return result
    except Exception:
        raise RemoteError("application_environment_probe_failed") from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def healthy(spec, control=command):
    try:
        if control(["systemctl", "--user", "is-active", unit_name(spec)]) != "active":
            return False
        pid = control(["systemctl", "--user", "show", unit_name(spec), "--property=MainPID", "--value"])
        if not owns_endpoints(spec, pid):
            return False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        for key in ("health", "ready"):
            with opener.open(spec[key+"_url"], timeout=2) as response:
                if response.status != 200:
                    return False
                data = response.read(8193)
                if len(data) > 8192:
                    return False
                actual = json.loads(data)
                if not isinstance(actual, dict) or any(actual.get(k) != v for k,v in spec[key+"_json"].items()):
                    return False
        return True
    except Exception:
        return False


def owns_endpoints(spec, pid, proc=Path("/proc")):
    """Require the managed Python main process to own both loopback listeners."""
    from urllib.parse import urlsplit
    try:
        if not pid.isascii() or not pid.isdecimal() or int(pid) < 1:
            return False
        process = proc / pid
        args = (process / "cmdline").read_bytes().split(b"\0")
        if args[-1:] == [b""]:
            args.pop()
        if args != [spec["python"].encode(), b"-m", spec["module"].encode()]:
            return False
        if (process / "cwd").resolve() != (Path(spec["root"]) / "current").resolve():
            return False
        ports = {urlsplit(spec[key+"_url"]).port for key in ("health", "ready")}
        owned = {os.readlink(fd) for fd in (process / "fd").iterdir()}
        found = set()
        for line in (proc / "net/tcp").read_text().splitlines()[1:]:
            fields = line.split()
            address, port = fields[1].split(":")
            if fields[3] == "0A" and address == "0100007F" and "socket:["+fields[9]+"]" in owned:
                found.add(int(port, 16))
        return ports <= found
    except Exception:
        return False


def wait_ready(spec, check):
    deadline = time.monotonic() + spec["timeout"]
    while True:
        if check(spec):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(1, max(0, deadline-time.monotonic())))


def unit_name(spec):
    return "codyssey-pii-"+spec["role"]+".service"


def unit_text(spec):
    return ("# managed-by: codyssey-pii-deployment-v1\n[Unit]\nDescription=Codyssey PII "+spec["role"]+"\n"
            "[Service]\nType=simple\nWorkingDirectory="+spec["root"]+"/current\n"
            "ExecStart="+spec["python"]+" -m "+spec["module"]+"\nEnvironmentFile="+spec["env_file"]+"\n"
            "Environment=PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1\nUMask=0077\n"
            "Restart=on-failure\nRestartSec=3\nTimeoutStopSec=60\nKillMode=control-group\n"
            "StandardOutput=null\nStandardError=null\n[Install]\nWantedBy=default.target\n")


def atomic_json(path, value):
    temporary = path.with_name(path.name+".pending-"+uuid.uuid4().hex)
    with temporary.open("x") as handle:
        os.chmod(temporary, 0o600)
        json.dump(value, handle, sort_keys=True)
    os.replace(temporary, path)


def switch(root, target):
    temporary = root / ("link-"+uuid.uuid4().hex)
    temporary.symlink_to(target)
    os.replace(temporary, root / "current")


def checked_release(root, target, role):
    path = Path(target)
    if (not path.is_absolute() or path.parent != root / "releases" or path.parent.is_symlink()
            or path.is_symlink() or not path.is_dir()):
        raise RemoteError("unmanaged_release")
    try:
        metadata = path / "deployment-manifest.json"
        if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > 1024 * 1024:
            raise ValueError
        manifest = json.loads(metadata.read_text())
        if manifest.get("role") != role or manifest.get("manager") != "codyssey-pii-v1":
            raise RemoteError("role_mismatch")
        if (set(manifest) != {"manager", "role", "sha256", "files"}
                or not re.fullmatch(r"[a-f0-9]{64}", path.name) or manifest["sha256"] != path.name
                or not isinstance(manifest["files"], dict) or not 1 <= len(manifest["files"]) <= 1000):
            raise ValueError
        actual = set()
        for item in path.rglob("*"):
            if item.is_symlink() or not (item.is_file() or item.is_dir()):
                raise ValueError
            if item.is_file():
                actual.add(item.relative_to(path).as_posix())
        if actual != set(manifest["files"]) | {"deployment-manifest.json"}:
            raise ValueError
        total = 0
        for name, expected in manifest["files"].items():
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError
            item = path / name  # exact membership above excludes paths outside this tree
            total += item.stat().st_size
            if total > 16 * 1024 * 1024:
                raise ValueError
            digest = hashlib.sha256()
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError
    except RemoteError:
        raise
    except Exception:
        raise RemoteError("release_integrity_failed") from None
    return path


def retry_state(root, release, previous, role):
    """Only known consistent state or the precise pre-switch interruption may resume."""
    state = root / "state.json"
    if not state.exists() and not state.is_symlink():
        if previous is not None:
            raise RemoteError("retry_state_mismatch")
        return
    try:
        if state.is_symlink() or not state.is_file() or state.stat().st_nlink != 1 or state.stat().st_size > 65536:
            raise ValueError
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        value = json.loads(state.read_text(), object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != {"current", "previous"}:
            raise ValueError
        if value["previous"] is not None and value["current"] in {None, value["previous"]}:
            raise ValueError
        for target in value.values():
            if target is not None:
                if not isinstance(target, str) or str(Path(target)) != target:
                    raise ValueError
                checked_release(root, target, role)
        if value["current"] != previous and value != {"current":str(release), "previous":previous}:
            raise ValueError
        if str(release) == value["previous"]:
            raise RemoteError("release_already_exists")
    except RemoteError:
        raise
    except Exception:
        raise RemoteError("retry_state_mismatch") from None


def check_retry_release(root, release, role, data, members):
    """Compare staging against the supplied digest-checked archive, never its own claims."""
    if release.is_symlink() or not release.is_dir() or release.parent.is_symlink():
        raise RemoteError("release_integrity_failed")
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in members:
            contents[member.name] = archive.extractfile(member).read()
    metadata = release / "deployment-manifest.json"
    complete = metadata.exists() or metadata.is_symlink()
    if complete:
        checked_release(root, str(release), role)
        expected = {name:hashlib.sha256(content).hexdigest() for name, content in contents.items()}
        if json.loads(metadata.read_text())["files"] != expected:
            raise RemoteError("release_integrity_failed")
    directories = {parent.as_posix() for name in contents for parent in PurePosixPath(name).parents if parent != PurePosixPath(".")}
    for item in release.rglob("*"):
        name = item.relative_to(release).as_posix()
        if item.is_symlink():
            raise RemoteError("release_integrity_failed")
        if item.is_dir() and name in directories:
            continue
        if complete and name == "deployment-manifest.json":
            continue
        if (not item.is_file() or item.stat().st_nlink != 1 or name not in contents
                or item.stat().st_size > len(contents[name])):
            raise RemoteError("release_integrity_failed")
        content = item.read_bytes()
        if (complete and content != contents[name]) or not contents[name].startswith(content):
            raise RemoteError("release_integrity_failed")
    return complete


def execute(request, *, prerequisite=preflight, control=command, check=None):
    mode, spec = request["mode"], request.get("spec")
    result = prerequisite(request)  # Every prerequisite gate precedes any write.
    if mode == "preflight":
        return {"status":"preflight_ok", "details":result}
    if not spec or spec["role"] != request.get("role", spec["role"]):
        raise RemoteError("service_contract_required")
    if mode not in {"deploy", "verify", "rollback"}:
        raise RemoteError("unsupported_mode")
    check = check or (lambda value: healthy(value, control))
    root = Path(spec["root"])
    if any(path.is_symlink() for path in [root, *root.parents]):
        raise RemoteError("unsafe_deployment_root")
    marker = root / "managed.json"
    if root.exists():
        if marker.is_symlink() or not marker.is_file():
            raise RemoteError("unmanaged_root")
        if json.loads(marker.read_text()) != {"manager":"codyssey-pii-v1", "role":spec["role"]}:
            raise RemoteError("role_mismatch")
    current = root / "current"
    unit = Path.home() / ".config/systemd/user" / unit_name(spec)
    desired = unit_text(spec)
    if any(path.is_symlink() for path in unit.parents):
        raise RemoteError("unsafe_unit_directory")
    if unit.is_symlink() or (unit.exists() and unit.read_text() != desired):
        raise RemoteError("unmanaged_or_changed_unit")
    previous = None
    if current.is_symlink():
        previous = str(checked_release(root, os.readlink(current), spec["role"]))
    elif current.exists():
        raise RemoteError("unmanaged_current")
    if mode == "verify":
        if not previous or not unit.exists() or not check(spec):
            raise RemoteError("verification_failed")
        return {"status":"verified", "role":spec["role"], "release":Path(previous).name}
    if mode == "rollback":
        history = json.loads((root / "state.json").read_text())
        target = str(checked_release(root, history["previous"], spec["role"]))
        if history.get("current") != previous or not unit.exists():
            raise RemoteError("rollback_state_mismatch")
        switch(root, target)
        try:
            control(["systemctl", "--user", "restart", unit_name(spec)])
            if not wait_ready(spec, check):
                raise RemoteError("rollback_verification_failed")
        except Exception:
            switch(root, previous)
            atomic_json(root / "state.json", {"current":previous,"previous":target})
            restored = False
            try:
                control(["systemctl", "--user", "restart", unit_name(spec)])
                restored = wait_ready(spec, check)
            except Exception:
                pass
            raise RemoteError("rollback_failed_previous_restored" if restored else "rollback_failed_restore_unhealthy") from None
        atomic_json(root / "state.json", {"current":target,"previous":previous})
        return {"status":"rolled_back", "role":spec["role"], "release":Path(target).name}
    data = base64.b64decode(request["bundle"], validate=True)
    members = validate_archive(data, request["sha256"], spec["role"])
    entry = spec["module"].replace(".", "/")
    names = {member.name for member in members}
    if entry+"/__main__.py" not in names and entry+".py" not in names:
        raise RemoteError("service_entrypoint_missing")
    if previous and (not unit.exists() or not check(spec)):
        raise RemoteError("previous_service_not_healthy")
    release = root / "releases" / request["sha256"]
    complete = False
    if release.exists() or release.is_symlink():
        retry_state(root, release, previous, spec["role"])
        complete = check_retry_release(root, release, spec["role"], data, members)
        if str(release) == previous:
            return {"status":"deployed", "role":spec["role"], "release":request["sha256"]}
        if not complete:
            # Exclusive container creation prevents replacement of earlier evidence.
            preserved = root / ("preserved-stage-" + uuid.uuid4().hex)
            preserved.mkdir(mode=0o700)
            release.rename(preserved / release.name)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not marker.exists():
        atomic_json(marker, {"manager":"codyssey-pii-v1", "role":spec["role"]})
    releases = root / "releases"
    if releases.is_symlink():
        raise RemoteError("unsafe_releases_directory")
    releases.mkdir(mode=0o700, exist_ok=True)
    if not complete:
        release.mkdir(mode=0o700)
        file_hashes = {}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in members:
                target = release / member.name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open("xb") as handle:
                    os.chmod(target, 0o600)
                    content = archive.extractfile(member).read()
                    handle.write(content)
                    file_hashes[member.name] = hashlib.sha256(content).hexdigest()
        atomic_json(release / "deployment-manifest.json", {"manager":"codyssey-pii-v1","role":spec["role"],"sha256":request["sha256"],"files":file_hashes})
    checked_release(root, str(release), spec["role"])
    if not unit.exists():
        unit.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with unit.open("x") as handle:
            os.chmod(unit, 0o600)
            handle.write(desired)
    atomic_json(root / "state.json", {"current":str(release),"previous":previous})
    switch(root, str(release))
    try:
        control(["systemctl", "--user", "daemon-reload"])
        control(["systemctl", "--user", "restart", unit_name(spec)])
        if not wait_ready(spec, check):
            raise RemoteError("readiness_failed")
    except Exception:
        if previous:
            switch(root, previous)
            atomic_json(root / "state.json", {"current":previous,"previous":None,"failed":str(release)})
            restored = False
            try:
                control(["systemctl", "--user", "restart", unit_name(spec)])
                restored = wait_ready(spec, check)
            except Exception:
                pass
            raise RemoteError("deploy_failed_previous_restored" if restored else "deploy_failed_rollback_unhealthy") from None
        control(["systemctl", "--user", "stop", unit_name(spec)])
        current.rename(root / ("failed-current-"+uuid.uuid4().hex))
        atomic_json(root / "state.json", {"current":None,"previous":None,"failed":str(release)})
        raise RemoteError("deploy_failed_no_previous_release") from None
    return {"status":"deployed", "role":spec["role"], "release":request["sha256"]}


def dispatch(request):
    try:
        # systemd's per-user runtime directory supplies a private lock shared by
        # all deployment roots, preventing concurrent mutation of fixed units.
        inspected = preflight(request)
        if request["mode"] in {"deploy", "rollback"}:
            import fcntl
            runtime = USER_RUNTIME_BASE / str(os.getuid())
            if runtime.is_symlink() or runtime.stat().st_uid != os.getuid() or stat.S_IMODE(runtime.stat().st_mode) != 0o700:
                raise RemoteError("invalid_user_runtime_directory")
            lock = runtime / "codyssey-pii-deployment.lock"
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "a") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RemoteError("deployment_in_progress") from None
                result = execute(request, prerequisite=lambda _: inspected)
        else:
            result = execute(request, prerequisite=lambda _: inspected)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, RemoteError) else "remote_operation_failed"
        print(json.dumps({"status":"error", "code":code}))
        return 2
