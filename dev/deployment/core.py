"""Local validation, secret-free packaging and strict OpenSSH transport."""
from dataclasses import dataclass, asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from urllib.parse import urlsplit


class DeploymentError(Exception):
    def __init__(self, code="invalid_deployment_configuration"):
        super().__init__(code)


def safe_path(value):
    return (isinstance(value, str) and re.fullmatch(r"/[A-Za-z0-9_./-]+", value)
            and ".." not in Path(value).parts and "//" not in value and value != "/")


@dataclass(frozen=True)
class Spec:
    role: str
    root: str
    python: str
    env_file: str
    module: str
    health_url: str
    ready_url: str
    health_json: dict
    ready_json: dict
    device: str
    requirements: dict
    timeout: int = 120

    @classmethod
    def parse(cls, raw):
        try:
            value = dict(raw)
            if value.get("role") == "gpu":
                defaults = dict(module="detector", health_url="http://127.0.0.1:8766/healthz",
                                ready_url="http://127.0.0.1:8766/readyz", health_json={"status":"alive"},
                                ready_json={"status":"ready"}, device="cuda",
                                requirements={"fastapi":"0.116.1", "uvicorn":"0.35.0", "gliner":"0.2.29"})
                value = defaults | value
                if value["module"] != "detector":
                    raise ValueError
            elif value.get("role") == "general":
                value.setdefault("device", "cpu")
                if not value.get("module", "").startswith("observability."):
                    raise ValueError
            else:
                raise ValueError
            result = cls(**value)
            if not all(safe_path(p) for p in (result.root, result.python, result.env_file)):
                raise ValueError
            if result.root == result.env_file or Path(result.root) in Path(result.env_file).parents:
                raise ValueError  # operator-owned secrets must be outside releases
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", result.module):
                raise ValueError
            if result.device not in {"cpu", "cuda"} or (result.role == "general" and result.device != "cpu"):
                raise ValueError
            if type(result.timeout) is not int or not 1 <= result.timeout <= 600:
                raise ValueError
            for url in (result.health_url, result.ready_url):
                parsed = urlsplit(url)
                if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                        or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or not re.fullmatch(r"/[A-Za-z0-9_/-]+", parsed.path)):
                    raise ValueError
            for expected in (result.health_json, result.ready_json):
                if not isinstance(expected, dict) or not expected or any(not isinstance(k, str) or not isinstance(v, (str, bool, int)) for k,v in expected.items()):
                    raise ValueError
            if not isinstance(result.requirements, dict) or not result.requirements:
                raise ValueError
            if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", k) or not isinstance(v,str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+",v) for k,v in result.requirements.items()):
                raise ValueError
            return result
        except Exception:
            raise DeploymentError() from None

    def as_dict(self):
        return asdict(self)


def load_access(path, role):
    try:
        path = Path(path)
        if path.is_symlink() or path.parent.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600 or stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
            raise ValueError
        access = json.loads(path.read_text())["servers"]["gpu" if role == "gpu" else "base"]
        if not re.fullmatch(r"[A-Za-z0-9.-]+", access["hostname"]):
            raise ValueError
        return access
    except Exception:
        raise DeploymentError("invalid_private_access_file") from None


def ssh_argv(access, known_hosts, python, *, interactive=False, password=False):
    try:
        if interactive and password:
            raise ValueError
        known_hosts = Path(known_hosts).absolute()
        if any(ord(char) < 32 or ord(char) == 127 or char in {'"', '\\', '%', '$'} for char in str(known_hosts)):
            raise ValueError
        if not known_hosts.exists() or not known_hosts.stat().st_size:
            raise DeploymentError("verified_known_hosts_required")
        if known_hosts.is_symlink() or not known_hosts.is_file() or stat.S_IMODE(known_hosts.stat().st_mode) != 0o600 or not known_hosts.stat().st_size:
            raise ValueError
        if (known_hosts.parent.is_symlink() or known_hosts.stat().st_uid != os.getuid()
                or known_hosts.stat().st_nlink != 1 or known_hosts.parent.stat().st_uid != os.getuid()
                or stat.S_IMODE(known_hosts.parent.stat().st_mode) & 0o022):
            raise ValueError
        host, user, port = access["private_ip"], access["username"], access["ssh_port"]
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
                or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user)
                or type(port) is not int or not 1 <= port <= 65535 or not safe_path(python)):
            raise ValueError
        arguments = ["ssh", "-F", "/dev/null", "-T", "-o", "StrictHostKeyChecking=yes", "-o",
                'UserKnownHostsFile="'+str(known_hosts)+'"',
                "-o", "GlobalKnownHostsFile=/dev/null", "-o", "UpdateHostKeys=no",
                "-o", "KnownHostsCommand=none", "-o", "VerifyHostKeyDNS=no", "-o", "CheckHostIP=no",
                "-o", "ClearAllForwardings=yes",
                "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                "-o", "BatchMode="+("no" if interactive or password else "yes")]
        if password:
            arguments += ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                          "-o", "KbdInteractiveAuthentication=no", "-o", "NumberOfPasswordPrompts=1"]
        return arguments + ["-p", str(port), user+"@"+host, python+" -"]
    except DeploymentError:
        raise
    except Exception:
        raise DeploymentError("invalid_ssh_configuration") from None


def bundle(source, role):
    """Only Python source from explicit role-owned trees; no env/model/cache files."""
    source = Path(source)
    trees = ("detector", "gateway") if role == "gpu" else ("observability",)
    output = io.BytesIO()
    count = total = 0
    with tarfile.open(fileobj=output, mode="w") as archive:
        for tree in trees:
            root = source / tree
            if root.is_symlink():
                raise DeploymentError("unsafe_bundle_source")
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(source)
                if any(part.startswith(".") or part in {"tests", "__pycache__"} for part in relative.parts):
                    continue
                asset = (role == "general" and len(relative.parts) > 2 and relative.parts[1] in {"static", "templates"}
                         and path.suffix in {".html", ".css", ".js", ".svg", ".png", ".ico", ".woff2"})
                if not path.is_file() or (path.suffix != ".py" and not asset):
                    continue
                if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != source.parent):
                    raise DeploymentError("unsafe_bundle_source")
                data = path.read_bytes()
                total += len(data)
                count += 1
                if total > 16 * 1024 * 1024 or count > 1000:
                    raise DeploymentError("bundle_limit")
                member = tarfile.TarInfo(relative.as_posix())
                member.size, member.mode = len(data), 0o600
                archive.addfile(member, io.BytesIO(data))
    if not count:
        raise DeploymentError("service_source_missing")
    data = output.getvalue()
    return data, hashlib.sha256(data).hexdigest()
