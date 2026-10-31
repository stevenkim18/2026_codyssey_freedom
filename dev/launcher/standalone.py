"""Relocatable packaged client; Python and gateway dependencies ship in the bundle.

This module calls the existing gateway and protected launcher in process. It
never selects another Python interpreter or assumes a source checkout exists.
"""
import argparse
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from urllib.parse import urlsplit

from gateway.settings import Settings
from gateway.tls import detector_tls_context


APP_VERSION = "0.1.0-engineering.1"
SETTINGS_LIMIT = 65536
_FIELDS = {
    "local_token": "PII_GATEWAY_LOCAL_TOKEN",
    "detector_url": "PII_DETECTOR_URL",
    "detector_token": "PII_DETECTOR_TOKEN",
    "openai_api_key": "OPENAI_API_KEY",
    "model_id": "PII_MODEL_ID",
    "port": "PII_GATEWAY_PORT",
    "state_dir": "PII_STATE_DIR",
}
_OPTIONAL = {"detector_ca_file": "PII_DETECTOR_CA_FILE"}
_PROTECTED = {"OPENAI_BASE_URL", "OPENAI_API_BASE", "PII_UPSTREAM_URL",
              "PII_GATEWAY_HOST", "PII_DETECTOR_VERIFY_TLS", "PII_VERIFY_TLS",
              "PYTHONHTTPSVERIFY"}
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR")


class ClientError(Exception):
    """Fixed codes only; user-provided values never enter diagnostics."""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ClientError("invalid_arguments")


def _path(value):
    path = Path(value)
    if (not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in str(path))):
        raise ClientError("unsafe_settings_path")
    return path


@contextmanager
def _settings_parent(path, *, create=False):
    """Descriptor-relative walk rejects symlinks and unsafe parent ownership."""
    path = _path(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:-1]:
            info = os.fstat(descriptor)
            sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
                raise ClientError("unsafe_settings_path")
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ClientError("unsafe_settings_directory")
        yield descriptor
    finally:
        os.close(descriptor)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ClientError("invalid_settings")
        result[key] = value
    return result


def _validate(data):
    if (not isinstance(data, dict) or not set(_FIELDS).union({"codex_home"}) <= data.keys()
            or data.keys() - set(_FIELDS) - set(_OPTIONAL) - {"codex_home"}
            or any(not isinstance(value, str) for value in data.values())):
        raise ClientError("invalid_settings")
    from .__main__ import selected_home
    selected_home(data["codex_home"])
    _path(data["state_dir"])
    environment = {variable: data[key] for key, variable in (_FIELDS | _OPTIONAL).items() if key in data}
    # Preserve detection of inherited protected overrides before sanitizing the
    # environment. Silently dropping these would bypass Settings' refusal.
    environment.update({key: os.environ[key] for key in _PROTECTED if key in os.environ})
    settings = Settings.from_env(environment)
    detector_tls_context(settings.detector_ca_file)
    return environment


def load_settings(path):
    path = _path(path)
    with _settings_parent(path) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= SETTINGS_LIMIT):
                raise ClientError("unsafe_settings_file")
            raw = source.read(SETTINGS_LIMIT + 1)
            if len(raw) > SETTINGS_LIMIT:
                raise ClientError("invalid_settings")
    data = json.loads(raw, object_pairs_hook=_unique_pairs)
    _validate(data)
    return data


def _configure(path):
    # Refuse overwrite before requesting any credentials.
    if path.exists() or path.is_symlink():
        raise ClientError("settings_already_exist")
    origin = input("Detector HTTPS origin (for example https://detector.example): ").strip()
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or any(c in origin for c in "?#")):
        raise ClientError("invalid_detector_origin")
    model = input("OpenAI model [gpt-6.1-sol]: ").strip() or "gpt-6.1-sol"
    ca = input("Detector CA PEM absolute path (blank for public trust): ").strip()
    data = {
        "detector_url": origin.rstrip("/") + "/v1/inspect",
        "model_id": model,
        "detector_token": getpass.getpass("Detector bearer token: "),
        "openai_api_key": getpass.getpass("OpenAI API key: "),
        "local_token": secrets.token_urlsafe(32),
        "port": "8765",
        "state_dir": str(Path.home() / ".local/state/ai-pii-gateway"),
        "codex_home": str(Path.home() / ".local/share/ai-pii-gateway/codex-home"),
    }
    if ca:
        data["detector_ca_file"] = ca
    _validate(data)
    encoded = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    if len(encoded) > SETTINGS_LIMIT:
        raise ClientError("invalid_settings")
    with _settings_parent(path, create=True) as parent:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        os.fsync(parent)
    print(json.dumps({"status": "configured", "remote_detector": "unverified"}))
    return 0


@contextmanager
def _environment(environment):
    previous = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(environment)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def main(argv=None):
    parser = Parser(prog="pii-gateway", description="Packaged protected text-only Codex gateway client")
    parser.add_argument("--settings", type=Path,
                        default=Path.home() / ".config/ai-pii-gateway/client.json",
                        help="Absolute private JSON path; place before the command")
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("configure", "serve", "install", "uninstall", "version"):
        actions.add_parser(name)
    actions.add_parser("doctor").add_argument("--probe", action="store_true",
                                              help="Probe the configured loopback gateway")
    actions.add_parser("run").add_argument("arguments", nargs=argparse.REMAINDER,
                                          help="After --, provide exec and supported Codex arguments")
    try:
        args = parser.parse_args(argv)
        if args.action == "version":
            print(json.dumps({"name": "pii-gateway", "version": APP_VERSION}))
            return 0
        path = _path(args.settings)
        if args.action == "configure":
            return _configure(path)
        data = load_settings(path)
        validated = _validate(data)
        base = {key: os.environ[key] for key in _BASE_ENV if key in os.environ}
        if args.action == "serve":
            from gateway.server import main as serve
            with _environment(base | validated):
                return serve()
        from .__main__ import main as launch
        command = [args.action, "--codex-home", data["codex_home"]]
        if args.action == "install":
            command += ["--port", data["port"], "--model", data["model_id"]]
        elif args.action == "doctor" and args.probe:
            command += ["--gateway-url", "http://127.0.0.1:" + data["port"]]
        elif args.action == "run":
            command += args.arguments
        # Codex needs only the local credential. Provider/detector credentials
        # never enter either its version probe or its actual child environment.
        with _environment(base | {"PII_GATEWAY_LOCAL_TOKEN": data["local_token"]}):
            return launch(command)
    except KeyboardInterrupt:
        return 130
    except ClientError as exc:
        print(json.dumps({"status": "error", "code": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"status": "error", "code": "client_operation_refused"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
