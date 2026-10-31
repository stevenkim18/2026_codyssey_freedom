"""Run from dev: python -m deployment --help."""
import argparse
import base64
import hashlib
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import subprocess

from .core import DeploymentError, Spec, bundle, load_access, safe_path, ssh_argv
from .askpass import password_session


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def _packet_inputs(path, trusted):
    # Import only on this path; preparation validates metadata, never loads tensors.
    from . import prepare
    from .model_stage import _file, _same_file
    try:
        manifest = prepare.verify(path, expected_manifest_sha256=trusted)
        snapshots = {}
        for name, limit in (("deploy.json", 1024 * 1024), ("application.tar", 20 * 1024 * 1024)):
            with _file(path / name, limit) as (handle, before):
                data = handle.read(limit + 1)
                if len(data) > limit or not _same_file(before, os.fstat(handle.fileno())):
                    raise ValueError()
            if hashlib.sha256(data).hexdigest() != manifest["files"][name]:
                raise ValueError()
            snapshots[name] = data
        spec = Spec.parse(prepare._json(snapshots["deploy.json"]))
        if spec.role != manifest["role"] or len(snapshots["application.tar"]) != manifest["application"]["bytes"]:
            raise ValueError()
        return spec, snapshots["application.tar"], manifest["application"]["sha256"]
    except Exception:
        raise DeploymentError("invalid_deployment_packet") from None


def main(argv=None, *, run=subprocess.run):
    parser = SafeParser(description="Managed user-service deployment; no dependency installation or deletion")
    parser.add_argument("mode", choices=("plan", "preflight", "deploy", "verify", "rollback"))
    parser.add_argument("--role", required=True, choices=("gpu", "general"))
    parser.add_argument("--spec", type=Path, help="Nonsecret service JSON contract; required except read-only preflight")
    parser.add_argument("--python", help="Optional absolute app Python path to inspect during contract-free preflight")
    parser.add_argument("--known-hosts", type=Path, help="Independently verified host keys; exact file mode 0600")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--packet", type=Path, help="Verified prepared packet; incompatible with --source and --spec")
    parser.add_argument("--expected-manifest-sha256", help="Trusted packet manifest digest; required for remote packet modes")
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument("--interactive-auth", action="store_true", help="Permit native OpenSSH terminal authentication")
    authentication.add_argument("--password-auth", action="store_true", help="One-use native askpass from the private access file; verified host key required")
    args = parser.parse_args(argv)
    try:
        if args.packet:
            if args.spec is not None or args.source is not None or args.python is not None:
                raise DeploymentError("packet_input_conflict")
            if args.mode != "plan" and args.expected_manifest_sha256 is None:
                raise DeploymentError("trusted_packet_digest_required")
            spec, data, digest = _packet_inputs(args.packet, args.expected_manifest_sha256)
        else:
            if args.expected_manifest_sha256 is not None:
                raise DeploymentError("packet_required")
            spec = Spec.parse(json.loads(args.spec.read_text())) if args.spec else None
        if spec and spec.role != args.role:
            raise DeploymentError("role_mismatch")
        if not spec and args.mode != "preflight":
            raise DeploymentError("service_contract_required")
        python = spec.python if spec else args.python
        if (python is not None and not safe_path(python)) or (spec and args.python and args.python != spec.python):
            raise DeploymentError("invalid_remote_python")
        request = {"mode":args.mode, "role":args.role, "spec":spec.as_dict() if spec else None}
        if not spec and python:
            request["application_python"] = python
        if args.mode in {"plan", "deploy"}:
            if not args.packet:
                data, digest = bundle(args.source or Path(__file__).resolve().parents[1], args.role)
            request.update(bundle=base64.b64encode(data).decode("ascii"), sha256=digest)
        if args.mode == "plan":
            print(json.dumps({"status":"planned", "role":args.role, "sha256":digest,
                              "bundle_bytes":len(data), "remote_mutation":False}))
            return 0
        private = Path(__file__).resolve().parents[1] / ".private"
        access_file = private / "server-access.local.json"
        access = load_access(access_file, args.role)
        request["expected_hostname"] = access["hostname"]
        command = ssh_argv(access, args.known_hosts or private / "known_hosts", "/usr/bin/python3",
                           interactive=args.interactive_auth, password=args.password_auth)
        # The remote command is fixed safe-path + ' -'; secrets never enter argv,
        # shell interpolation, printed plans, remote source, or captured output.
        source = Path(__file__).with_name("remote.py").read_text()
        payload = base64.b64encode(json.dumps(request).encode()).decode("ascii")
        source += "\nraise SystemExit(dispatch(json.loads(base64.b64decode("+repr(payload)+"))))\n"
        context = password_session(access_file, args.role) if args.password_auth else nullcontext(None)
        with context as environment:
            options = {"env": environment} if environment is not None else {}
            completed = run(command, input=source, text=True, capture_output=True,
                            timeout=(spec.timeout if spec else 30)*3+120, check=False, **options)
        try:
            result = json.loads(completed.stdout)
            allowed = {"status", "role", "release", "details", "code"}
            if not isinstance(result, dict) or set(result) - allowed:
                raise ValueError
            if "role" in result and result["role"] != args.role:
                raise ValueError
            # Never echo arbitrary SSH diagnostics or a compromised remote response.
            status = result.get("status")
            expected = {"preflight":"preflight_ok", "verify":"verified", "deploy":"deployed", "rollback":"rolled_back"}[args.mode]
            if status not in {expected, "error"} or (completed.returncode and status != "error"):
                raise ValueError
            if status == "error":
                print(json.dumps({"status":"error", "code":"remote_operation_failed"}))
                return 2
            if args.packet and args.mode in {"deploy", "verify"} and result.get("release") != digest:
                raise ValueError
            public = {"status":status, "role":args.role}
            if args.mode == "preflight":
                details = result.get("details", {})
                safe = {}
                if isinstance(details, dict):
                    for key in ("cpu_count", "gpu_count"):
                        if type(details.get(key)) is int and 0 <= details[key] <= 65536:
                            safe[key] = details[key]
                    for key in ("contract", "cuda_available", "application_python_present"):
                        if type(details.get(key)) is bool:
                            safe[key] = details[key]
                    for key in ("python", "application_python"):
                        if isinstance(details.get(key), str) and re.fullmatch(r"[0-9.]{1,20}", details[key]):
                            safe[key] = details[key]
                    if details.get("os") == "Linux":
                        safe["os"] = "Linux"
                    if details.get("dependencies") == "verified":
                        safe["dependencies"] = "verified"
                public["details"] = safe
            if isinstance(result.get("release"), str) and re.fullmatch(r"[a-f0-9]{64}", result["release"]):
                public["release"] = result["release"]
            print(json.dumps(public))
            return 0
        except Exception:
            raise DeploymentError("ssh_or_remote_response_failed") from None
    except DeploymentError as exc:
        print(json.dumps({"status":"error", "code":str(exc)}))
        return 2
    except Exception:
        print(json.dumps({"status":"error", "code":"deployment_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
