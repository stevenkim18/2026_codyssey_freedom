"""Explicit detector-only trust anchors; never changes provider/system trust."""

import os
from pathlib import Path
import ssl
import stat
import sys

from .settings import SettingsError


CA_LIMIT = 1024 * 1024


def _platform_path(path):
    """Only Darwin's fixed root-owned system aliases may precede the fd walk."""
    if ".." in path.parts:
        raise ValueError
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in {"var", "tmp", "etc"}:
        alias = Path("/") / path.parts[1]
        info = alias.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = "private/" + path.parts[1]
            if info.st_uid != 0 or os.readlink(alias) != target:
                raise ValueError
            return Path("/") / target / Path(*path.parts[2:])
    return path


def _open_ca(path):
    """Pin every ancestor before opening the leaf relative to its descriptor."""
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            info = os.fstat(parent)
            trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                    or (info.st_mode & 0o022 and not trusted_sticky)):
                raise ValueError
            child = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        info = os.fstat(parent)
        trusted_sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.getuid()}
                or (info.st_mode & 0o022 and not trusted_sticky)):
            raise ValueError
        return os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)


def detector_tls_context(ca_file):
    """Return HTTPX's default verification or an isolated verified CA context.

    CA certificates are public, so 0644 is valid. Accept root/current-user owned
    regular files only, disallow writable-by-others and multiply linked files.
    Read bounded bytes from a no-follow descriptor instead of reopening for SSL.
    """
    if ca_file is None:
        return True
    try:
        path = Path(ca_file)
        if not path.is_absolute() or path.is_symlink():
            raise ValueError
        # Never resolve arbitrary ancestors: it would erase unsafe directories.
        path = _platform_path(path)
        descriptor = _open_ca(path)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022
                    or not 0 < info.st_size <= CA_LIMIT):
                raise ValueError
            data = source.read(CA_LIMIT + 1)
            if not 0 < len(data) <= CA_LIMIT:
                raise ValueError
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=data.decode("ascii"))
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise ValueError
        return context
    except (OSError, ValueError, TypeError, UnicodeError):
        raise SettingsError("INVALID_DETECTOR_CA_FILE") from None
