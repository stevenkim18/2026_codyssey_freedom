"""Controlled completed-package activation; no checkpoint watcher or model loader."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from contextlib import contextmanager
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

UNIT = "codyssey-pii-gpu.service"
LIMIT = 1024 * 1024


class ActivationError(Exception):
    """Fixed content-free failure codes."""


def _require(value, code="invalid_activation_configuration"):
    if not value:
        raise ActivationError(code)


def _directory(path):
    _require(path.is_absolute() and ".." not in path.parts and path.resolve() == path)
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700)


def _read(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= LIMIT)
        data = source.read(LIMIT + 1)
        _require(len(data) <= LIMIT)
        return data


def _environment(data):
    values = {}
    for line in data.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        _require(separator and re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and key not in values
                 and value and all(33 <= ord(c) <= 126 and c not in "\"'\\" for c in value))
        values[key] = value
    _require("PII_MODEL_PACKAGE" in values and "PII_DETECTOR_TOKEN" in values)
    return values


def _create(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def _atomic(path, data):
    # Failed staged files are retained, private, for operator investigation.
    temporary = path.parent / (".activation-" + uuid.uuid4().hex + ".env")
    _create(temporary, data)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify(candidate, expected=None):
    from detector.runtime import _verify_package
    _require(candidate.is_absolute() and ".." not in candidate.parts and candidate.resolve() == candidate)
    if expected is not None:
        _require(isinstance(expected, str) and re.fullmatch(r"[a-f0-9]{64}", expected))
        with (candidate / "manifest.json").open("rb") as source:
            manifest_data = source.read(LIMIT + 1)
        _require(len(manifest_data) <= LIMIT and hashlib.sha256(manifest_data).hexdigest() == expected,
                 "candidate_integrity_failed")
    _, manifest = _verify_package(candidate)
    return manifest


def _show(property_name):
    result = subprocess.run(["systemctl", "--user", "show", UNIT,
        "--property=" + property_name, "--value"], capture_output=True, timeout=10)
    _require(result.returncode == 0 and len(result.stdout) <= 8192, "unit_binding_failed")
    return result.stdout.decode("utf-8").strip()


def _owned_listener(pid, environment, *, proc=Path("/proc")):
    """Match the configured IPv4 LISTEN socket to the unit MainPID's descriptors."""
    try:
        _require(pid.isascii() and pid.isdecimal() and int(pid) > 0, "unit_listener_mismatch")
        address = {"127.0.0.1": "0100007F", "0.0.0.0": "00000000"}.get(
            environment.get("PII_DETECTOR_HOST", "127.0.0.1"))
        port = int(environment.get("PII_DETECTOR_PORT", "8766"))
        _require(address is not None and 1 <= port <= 65535, "unit_listener_mismatch")
        owned = set()
        for descriptor in (proc / pid / "fd").iterdir():
            try:
                owned.add(os.readlink(descriptor))
            except FileNotFoundError:
                continue  # Unrelated short-lived request descriptors can close.
        with (proc / "net" / "tcp").open("rb") as source:
            data = source.read(LIMIT + 1)
        _require(len(data) <= LIMIT, "unit_listener_mismatch")
        for line in data.decode("ascii").splitlines()[1:]:
            fields = line.split()
            local_address, local_port = fields[1].split(":")
            if (fields[3] == "0A" and local_address == address and int(local_port, 16) == port
                    and "socket:[" + fields[9] + "]" in owned):
                return
        raise ActivationError("unit_listener_mismatch")
    except Exception:
        raise ActivationError("unit_listener_mismatch") from None


def _binding(env_file, environment, *, process=False):
    _require(_show("EnvironmentFiles") == str(env_file) + " (ignore_errors=no)", "unit_binding_failed")
    if process:
        raw_pid = _show("MainPID")
        _require(raw_pid.isascii() and raw_pid.isdecimal() and int(raw_pid) > 0, "unit_process_mismatch")
        with Path("/proc", raw_pid, "environ").open("rb") as source:
            data = source.read(LIMIT + 1)
        _require(len(data) <= LIMIT, "unit_process_mismatch")
        entries = data.split(b"\0")
        expected = b"PII_MODEL_PACKAGE=" + environment["PII_MODEL_PACKAGE"].encode()
        _require([entry for entry in entries if entry.startswith(b"PII_MODEL_PACKAGE=")] == [expected],
                 "unit_process_mismatch")
        _owned_listener(raw_pid, environment)
        _require(_show("MainPID") == raw_pid, "unit_process_mismatch")


class ActivationInterrupted(BaseException):
    pass


@contextmanager
def _signals():
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    state = {"rollback": False}
    def terminate(number, frame):
        if not state["rollback"]:
            raise ActivationInterrupted()
        # Additional termination requests are deferred until recovery finishes.
    try:
        for number in previous:
            signal.signal(number, terminate)
        yield state
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _restart():
    result = subprocess.run(["systemctl", "--user", "restart", UNIT], capture_output=True, timeout=120)
    _require(result.returncode == 0, "restart_failed")


def _probe(environment, manifest, origin, ca_file):
    context = ssl.create_default_context(cafile=str(ca_file))
    _require(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ActivationError("verification_failed")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context), NoRedirect())
    def request(path, payload=None):
        headers = {"Authorization": "Bearer " + environment["PII_DETECTOR_TOKEN"]}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        with opener.open(urllib.request.Request(origin + path, data=data, headers=headers), timeout=10) as response:
            _require(response.status == 200, "verification_failed")
            content = response.read(LIMIT + 1)
            _require(len(content) <= LIMIT, "verification_failed")
            return json.loads(content)
    deadline = time.monotonic() + 180
    while True:
        try:
            _require(request("/healthz") == {"status": "alive"}, "verification_failed")
            _require(request("/readyz") == {"status": "ready"}, "verification_failed")
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise ActivationError("readiness_failed") from None
            time.sleep(1)
    response = request("/v1/inspect", {"schema_version": "1.0", "request_id": "activation-probe",
        "inspection_id": "activation-probe", "direction": "outbound",
        "segments": [{"segment_id": "public", "text": "public demonstration"}]})
    from detector.schemas import InspectionResponse
    checked = InspectionResponse.model_validate(response)
    _require(checked.status == "complete" and checked.error_code is None
             and checked.request_id == "activation-probe" and checked.inspection_id == "activation-probe"
             and checked.model_version == manifest["model_version"]
             and checked.threshold_version == manifest["threshold_version"]
             and set(checked.timing_breakdown_ms) == {"queue", "inference", "rules", "total"}
             and all(finding.segment_id == "public" and finding.end <= len("public demonstration")
                     for finding in checked.findings), "verification_failed")


def activate(candidate, root, env_file, expected_manifest_sha256, *, origin, ca_file,
             allow_engineering_test=False, command=_restart, probe=_probe, binding=_binding):
    """Verify completed export, swap only package setting, restart, verify or roll back."""
    try:
        _require(allow_engineering_test is True, "engineering_test_permission_required")
        candidate, root, env_file, ca_file = map(Path, (candidate, root, env_file, ca_file))
        _directory(root)
        _require(env_file.parent == root and env_file.name not in {"previous.env", "activate.lock"})
        from detector import runtime
        application_root = Path(runtime.__file__).resolve().parent.parent
        _require(root != application_root and application_root not in root.parents)
        parsed = urlsplit(origin)
        _require(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment and parsed.port != 0)
        lock = os.open(root / "activate.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(lock, "r+b") as handle:
            info = os.fstat(handle.fileno())
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                     and stat.S_IMODE(info.st_mode) == 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ActivationError("activation_in_progress") from None
            manifest = _verify(candidate, expected_manifest_sha256)
            previous = _read(env_file)
            environment = _environment(previous)
            port = environment.get("PII_DETECTOR_PORT", "8766")
            _require(port.isascii() and port.isdecimal() and 1 <= int(port) <= 65535
                     and origin == "https://127.0.0.1:" + str(int(port)), "origin_binding_failed")
            binding(env_file, environment)
            old_manifest = _verify(Path(environment["PII_MODEL_PACKAGE"]))
            if Path(environment["PII_MODEL_PACKAGE"]) == candidate:
                probe(environment, manifest, origin, ca_file)
                binding(env_file, environment, process=True)
                return {"status": "already_active"}
            # Preserve all previous secret lines byte-for-byte except package path.
            value = str(candidate)
            _require(all(33 <= ord(c) <= 126 and c not in "\"'\\" for c in value))
            updated = b"".join((b"PII_MODEL_PACKAGE=" + value.encode() + b"\n")
                if line.startswith(b"PII_MODEL_PACKAGE=") else line
                for line in previous.splitlines(keepends=True))
            backup = root / "previous.env"
            if backup.exists() or backup.is_symlink():
                # Never overwrite prior recovery evidence; retain a versioned copy.
                _read(backup)
                _create(root / ("previous-" + uuid.uuid4().hex + ".env"), previous)
            else:
                _create(backup, previous)
            with _signals() as signal_state:
                try:
                    _atomic(env_file, updated)
                    command()
                    active_environment = _environment(updated)
                    probe(active_environment, manifest, origin, ca_file)
                    binding(env_file, active_environment, process=True)
                    return {"status": "activated", "quality_approved": False}
                except BaseException as failure:
                    signal_state["rollback"] = True
                    try:
                        _atomic(env_file, previous)
                        command()
                        probe(environment, old_manifest, origin, ca_file)
                        binding(env_file, environment, process=True)
                        rollback = "success"
                    except BaseException:
                        rollback = "failure"
                    interrupted = isinstance(failure, (ActivationInterrupted, KeyboardInterrupt, SystemExit))
                    return {"status": "failed", "code": "activation_interrupted" if interrupted else "activation_failed",
                            "rollback": rollback}
    except ActivationError:
        raise
    except Exception:
        raise ActivationError("activation_rejected") from None


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_activation_arguments\n")


def main(argv=None):
    parser = SafeParser()
    for name in ("candidate", "root", "env-file", "manifest-sha256", "origin", "ca-file"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--allow-engineering-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = activate(args.candidate, args.root, args.env_file, args.manifest_sha256,
            origin=args.origin, ca_file=args.ca_file, allow_engineering_test=args.allow_engineering_test)
        print(json.dumps(report))
        return 0 if report["status"] in {"activated", "already_active"} else 1
    except ActivationError as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
