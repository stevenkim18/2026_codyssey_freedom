"""Dedicated Codex profile with private, verified backup and cautious rollback.

The pinned CLI is a protocol investigation target, NOT a compatibility claim.
Profile creation does not bypass the gateway's unsupported-content rejection.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib


PINNED_CLI = "0.166.0"
PROFILE = "pii-gateway.config.toml"


class ProfileError(Exception):
    pass


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _regular(path):
    if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
        raise ProfileError("unsafe profile path")


def _write(path, content):
    _regular(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".pii-profile-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _profile(port, model, cli_version):
    if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", model)
            or cli_version != PINNED_CLI):
        raise ProfileError("unsupported profile configuration")
    return f'''# Protected text gateway; CLI compatibility gate remains required.
model = {json.dumps(model)}
model_provider = "pii-gateway"
web_search = "disabled"

[model_providers.pii-gateway]
name = "Protected PII Gateway"
base_url = "http://127.0.0.1:{port}/v1"
env_key = "PII_GATEWAY_LOCAL_TOKEN"
wire_api = "responses"
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0

[features]
shell_tool = false
unified_exec = false
apply_patch_freeform = false
apps = false
'''.encode()


def _paths(home):
    home = Path(home)
    if home.is_symlink():
        raise ProfileError("unsafe configuration directory")
    state = home / ".pii-gateway-install"
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise ProfileError("unsafe installation state")
    return home, home / PROFILE, state


def _manifest(state):
    path = state / "manifest.json"
    _regular(path)
    try:
        data = json.loads(path.read_bytes())
        if (set(data) != {"schema_version", "original_sha256", "installed_sha256"}
                or data["schema_version"] != "1.0"
                or not isinstance(data["installed_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["installed_sha256"])
                or data["original_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", data["original_sha256"])):
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        raise ProfileError("invalid installation state") from None


def install(codex_home, *, port, model, cli_version):
    content = _profile(port, model, cli_version)
    home, profile, state = _paths(codex_home)
    _regular(profile)
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state.exists():
            manifest = _manifest(state)
            if manifest["installed_sha256"] != _hash(content):
                raise ProfileError("remove the previous profile before reconfiguration")
            if manifest["original_sha256"] is not None:
                backup = state / "original.toml"
                _regular(backup)
                if _hash(backup.read_bytes()) != manifest["original_sha256"]:
                    raise ProfileError("backup verification failed")
            actual = _hash(profile.read_bytes()) if profile.exists() else None
            if actual == manifest["installed_sha256"]:
                return
            if actual != manifest["original_sha256"]:
                raise ProfileError("profile changed after installation")
            # Resume only the exact pre-write crash window after checking backup.
            if actual is not None:
                backup = state / "original.toml"
                _regular(backup)
                if _hash(backup.read_bytes()) != actual:
                    raise ProfileError("backup verification failed")
        else:
            state.mkdir(mode=0o700)
            original = profile.read_bytes() if profile.exists() else None
            if original is not None:
                _write(state / "original.toml", original)
            manifest = {"schema_version": "1.0", "original_sha256": _hash(original) if original is not None else None,
                        "installed_sha256": _hash(content)}
            _write(state / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
        _write(profile, content)
    except OSError:
        raise ProfileError("profile installation failed") from None


def uninstall(codex_home):
    _, profile, state = _paths(codex_home)
    _regular(profile)
    manifest = _manifest(state)
    try:
        actual = _hash(profile.read_bytes()) if profile.exists() else None
        if actual not in (manifest["installed_sha256"], manifest["original_sha256"]):
            raise ProfileError("profile changed after installation")
        if manifest["original_sha256"] is not None and actual != manifest["original_sha256"]:
            backup = state / "original.toml"
            _regular(backup)
            original = backup.read_bytes()
            if _hash(original) != manifest["original_sha256"]:
                raise ProfileError("backup verification failed")
            _write(profile, original)
        else:
            if manifest["original_sha256"] is None:
                profile.unlink(missing_ok=True)
        backup = state / "original.toml"
        _regular(backup)
        backup.unlink(missing_ok=True)
        (state / "manifest.json").unlink()
        state.rmdir()
    except OSError:
        raise ProfileError("profile recovery failed") from None


def launch_arguments(cli_version, args, *, codex_home=None):
    if cli_version != PINNED_CLI:
        raise ProfileError("CLI version differs from the pinned protocol target")
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ProfileError("invalid CLI arguments")
    if not args or args[0] != "exec":
        raise ProfileError("only the pinned exec protocol is under verification")
    # Provider/profile/config overrides could route around the protection profile.
    forbidden = ("--config", "-c", "--profile", "-p", "--oss", "--local-provider", "--enable")
    if (any(arg == option or arg.startswith(option + "=") for arg in args for option in forbidden)
            or any(arg.startswith(("-c", "-p")) and not arg.startswith("--") for arg in args)):
        raise ProfileError("provider or feature override denied")
    if codex_home is None:
        raise ProfileError("verified installed profile required")
    _, profile, state = _paths(codex_home)
    _regular(profile)
    manifest = _manifest(state)
    try:
        content = profile.read_bytes()
        if _hash(content) != manifest["installed_sha256"]:
            raise ProfileError("profile changed after installation")
        config = tomllib.loads(content.decode())
        model = config["model"]
        base_url = config["model_providers"]["pii-gateway"]["base_url"]
        match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})/v1", base_url)
        if not match or content != _profile(int(match[1]), model, cli_version):
            raise ProfileError("invalid protected profile")
    except (OSError, ValueError, TypeError, KeyError):
        raise ProfileError("invalid protected profile") from None

    # In 0.166.0 ignore-user-config also suppresses profile-file loading. Explicit
    # overrides are essential: a profile name alone silently selects OpenAI.
    def flattened(value, prefix=""):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, dict):
                yield from flattened(item, path)
            else:
                yield "-c"
                yield f"{path}={json.dumps(item)}"

    return ["codex", "exec", "--ignore-user-config", *flattened(config), *args[1:]]
