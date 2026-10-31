"""One-use OpenSSH askpass bridge. Passwords never enter argv or environment.

Only the native password prompt for the configured SSH target is accepted.
This helper is not used for host trust, key passphrases, OTP, or VPN login.
Context files contain paths and target identity, never the password itself.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


class PasswordAuthError(Exception):
    def __init__(self):
        super().__init__("invalid_password_authentication")


def _private_file(path):
    path = Path(path)
    info = path.stat()
    parent = path.parent.stat()
    if (path.is_symlink() or path.parent.is_symlink() or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid()
            or info.st_nlink != 1 or info.st_size > 65536
            or stat.S_IMODE(parent.st_mode) != 0o700 or parent.st_uid != os.getuid()):
        raise PasswordAuthError()
    return json.loads(path.read_text(encoding="utf-8"))


def _access(path, role):
    if role not in {"gpu", "general"}:
        raise PasswordAuthError()
    entry = _private_file(path)["servers"]["gpu" if role == "gpu" else "base"]
    user, host, password = entry["username"], entry["private_ip"], entry["password"]
    if (not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", user)
            or not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host)
            or not isinstance(password, str) or not password or len(password) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in password)):
        raise PasswordAuthError()
    return user, host, password


@contextmanager
def password_session(access_file, role, *, cache_root=None):
    """Yield a child-only environment; preserve all artifacts and expire context."""
    context = None
    try:
        access_file = Path(access_file).absolute()
        user, host, _ = _access(access_file, role)
        cache_root = Path(cache_root) if cache_root else Path(__file__).resolve().parents[1] / ".cache/deployment-auth"
        if cache_root.is_symlink():
            raise PasswordAuthError()
        cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(cache_root.stat().st_mode) != 0o700 or cache_root.stat().st_uid != os.getuid():
            raise PasswordAuthError()
        directory = Path(tempfile.mkdtemp(prefix="ssh-", dir=cache_root)).resolve()
        context = directory / "active.json"
        with context.open("x", encoding="utf-8") as handle:
            os.chmod(context, 0o600)
            json.dump({"access_file": str(access_file), "role": role, "user": user, "host": host}, handle)
        helper = directory / "askpass"
        with helper.open("x", encoding="utf-8") as handle:
            handle.write('#!/bin/sh\nexec "$PII_DEPLOY_AUTH_PYTHON" -I "$PII_DEPLOY_AUTH_HELPER" "$@"\n')
        helper.chmod(0o700)
        environment = dict(os.environ)
        environment.update(SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE="force",
                           DISPLAY=os.environ.get("DISPLAY") or ":0",
                           PII_DEPLOY_AUTH_PYTHON=sys.executable,
                           PII_DEPLOY_AUTH_HELPER=str(Path(__file__).resolve()),
                           PII_DEPLOY_AUTH_CONTEXT=str(context))
    except Exception:
        raise PasswordAuthError() from None
    try:
        yield environment
    finally:
        # Rename only our metadata; never delete credentials, keys, or files.
        if context is not None and context.exists():
            context.rename(context.with_name("closed.json"))


def main(argv=None):
    """Called solely by OpenSSH; failures produce no stdout or stderr."""
    try:
        arguments = sys.argv[1:] if argv is None else argv
        if len(arguments) != 1:
            raise PasswordAuthError()
        context = Path(os.environ["PII_DEPLOY_AUTH_CONTEXT"])
        if context.name != "active.json":
            raise PasswordAuthError()
        record = _private_file(context)
        if not isinstance(record, dict) or set(record) != {"access_file", "role", "user", "host"}:
            raise PasswordAuthError()
        user, host, password = _access(record["access_file"], record["role"])
        if (user != record["user"] or host != record["host"]
                or arguments[0].strip() != f"{user}@{host}'s password:"):
            raise PasswordAuthError()
        # Exclusive creation makes repeated or concurrent prompts fail closed.
        with (context.parent / "used").open("x") as handle:
            os.chmod(handle.fileno(), 0o600)
        sys.stdout.write(password + "\n")
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
