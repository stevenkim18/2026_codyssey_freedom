"""Foreground ownership of a local TLS detector and loopback gateway only."""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time

import httpx

from detector.runtime import ModelPackage
from detector.server import ServerSettings
from gateway.settings import Settings
from gateway.tls import detector_tls_context


class StackError(Exception):
    """Only fixed operational codes may escape the runner."""


@dataclass(frozen=True)
class Options:
    package: Path
    state_dir: Path
    cert: Path
    key: Path
    ca: Path
    model_id: str
    gateway_port: int = 8765
    detector_port: int = 8766
    startup_timeout: float = 120


def _safe_path(path):
    if (not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts
            or any(ord(c) < 32 or ord(c) == 127 for c in str(path))
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise StackError("unsafe_path")


def _tls_preflight(options):
    client_context = detector_tls_context(options.ca)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(options.cert, options.key, password="")
    incoming, outgoing, peer_in, peer_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(incoming, outgoing, server_hostname="127.0.0.1")
    server = server_context.wrap_bio(peer_in, peer_out, server_side=True)
    completed = [False, False]
    for _ in range(20):
        for index, endpoint in enumerate((client, server)):
            try:
                endpoint.do_handshake()
                completed[index] = True
            except ssl.SSLWantReadError:
                pass
        if outgoing.pending:
            peer_in.write(outgoing.read())
        if peer_out.pending:
            incoming.write(peer_out.read())
        if all(completed):
            return client_context
    raise StackError("tls_preflight_failed")


def _environments(options, source, token, *, planning=False):
    # Deliberately exclude proxy/SSL/Python/provider overrides and unrelated secrets.
    base = {key: source[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if key in source}
    base.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                PYTHONNOUSERSITE="1", PYTHONUNBUFFERED="1")
    detector = base | {"PII_DETECTOR_TOKEN":token, "PII_MODEL_PACKAGE":str(options.package),
        "PII_DETECTOR_PORT":str(options.detector_port), "PII_DETECTOR_DEVICE":"cpu",
        "PII_DETECTOR_HOST":"127.0.0.1", "PII_DETECTOR_TLS_CERT_FILE":str(options.cert),
        "PII_DETECTOR_TLS_KEY_FILE":str(options.key)}
    gateway = base | {"PII_DETECTOR_TOKEN":token,
        "PII_DETECTOR_URL":f"https://127.0.0.1:{options.detector_port}/v1/inspect",
        "PII_DETECTOR_CA_FILE":str(options.ca), "PII_MODEL_ID":options.model_id,
        "PII_GATEWAY_PORT":str(options.gateway_port), "PII_STATE_DIR":str(options.state_dir),
        "OPENAI_API_KEY":"planning" if planning else source.get("OPENAI_API_KEY", ""),
        "PII_GATEWAY_LOCAL_TOKEN":"planning" if planning else source.get("PII_GATEWAY_LOCAL_TOKEN", "")}
    # Reject dangerous routing controls instead of silently legitimizing them.
    forbidden = {"OPENAI_BASE_URL", "OPENAI_API_BASE", "PII_UPSTREAM_URL", "PII_GATEWAY_HOST",
                 "PII_DETECTOR_VERIFY_TLS", "PII_VERIFY_TLS"}
    if forbidden.intersection(source) or source.get("PYTHONHTTPSVERIFY", "1") != "1":
        raise StackError("protected_setting_override")
    ServerSettings.from_env(detector)
    Settings.from_env(gateway)
    return detector, gateway


def plan(options):
    try:
        for path in (options.package, options.state_dir, options.cert, options.key, options.ca):
            _safe_path(path)
        if (options.gateway_port == options.detector_port or not 0 < options.startup_timeout <= 600
                or not options.state_dir.parent.is_dir()):
            raise ValueError
        _environments(options, {}, "planning", planning=True)
        path = options.package / "manifest.json"
        _safe_path(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1024 * 1024:
                raise ValueError
            ModelPackage.model_validate_json(source.read(1024 * 1024 + 1))
        _tls_preflight(options)
        return {"status":"planned", "device":"cpu", "manifest_structure":"verified",
                "tls_trust":"verified", "model_hashes":"unchecked", "model_readiness":"unchecked",
                "provider_connectivity":"unchecked", "gateway_port":options.gateway_port,
                "detector_port":options.detector_port}
    except Exception:
        raise StackError("preflight_failed") from None


@contextmanager
def state_lock(path):
    directory = descriptor = None
    try:
        _safe_path(path)
        # Refuse shared writable ancestors; root-owned sticky temporary roots are OK.
        for ancestor in path.parents:
            info = ancestor.stat()
            sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
                raise ValueError
        path.mkdir(mode=0o700, exist_ok=True)
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError
        descriptor = os.open(".local-stack.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=directory)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ValueError
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat(".local-stack.lock", dir_fd=directory, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)
        raise StackError("state_lock_refused") from None
    try:
        yield
    finally:
        os.close(descriptor)
        os.close(directory)


def check_ports(options):
    held = []
    try:
        for port in (options.detector_port, options.gateway_port):
            listener = socket.socket()
            held.append(listener)
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
        return held
    except OSError:
        for listener in held:
            listener.close()
        raise StackError("local_port_unavailable") from None


def probe_ready(url, verify, timeout):
    async def request():
        async with httpx.AsyncClient(verify=verify, trust_env=False, follow_redirects=False, timeout=timeout) as client:
            async with client.stream("GET", url) as response:
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4096:
                        return False
                return response.status_code == 200 and json.loads(content) == {"status":"ready"}
    async def bounded():
        return await asyncio.wait_for(request(), timeout)
    try:
        return asyncio.run(bounded())
    except Exception:
        return False


def _stop(children):
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, signal.SIG_IGN)
        _stop_owned(children)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _stop_owned(children):
    for child in reversed(children):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
    for child in reversed(children):
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def run_stack(options, env, *, popen=subprocess.Popen, probe=probe_ready,
              sleep=time.sleep, clock=time.monotonic):
    children = []
    listeners = []
    try:
        plan(options)
        detector_env, gateway_env = _environments(options, env, secrets.token_urlsafe(32))
        verify = detector_tls_context(options.ca)
        with state_lock(options.state_dir):
            try:
                listeners = check_ports(options)
                deadline = clock() + options.startup_timeout
                for listener, (module, environment, scheme, port, trust) in zip(listeners, (
                    ("detector", detector_env, "https", options.detector_port, verify),
                    ("gateway", gateway_env, "http", options.gateway_port, True))):
                    child_env = environment | {"PII_LOCAL_LISTENER_FD":str(listener.fileno())}
                    children.append(popen([sys.executable, "-m", "launcher.local_child", module], env=child_env,
                        cwd=str(Path(__file__).resolve().parents[1]), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                        pass_fds=(listener.fileno(),)))
                    while True:
                        remaining = deadline - clock()
                        if any(child.poll() is not None for child in children):
                            raise StackError("child_exited")
                        if remaining <= 0:
                            raise StackError("startup_timeout")
                        if probe(f"{scheme}://127.0.0.1:{port}/readyz", trust, min(1, remaining)):
                            if any(child.poll() is not None for child in children):
                                raise StackError("child_exited")
                            if clock() >= deadline:
                                raise StackError("startup_timeout")
                            break
                        sleep(min(.1, remaining))
                print(json.dumps({"status":"local_ready", "model_quality":"unverified",
                                  "provider_connectivity":"unchecked", "central_events":"not_configured"}), flush=True)
                while True:
                    if any(child.poll() is not None for child in children):
                        raise StackError("child_exited")
                    sleep(.2)
            finally:
                try:
                    _stop(children)
                finally:
                    for listener in listeners:
                        listener.close()
    except KeyboardInterrupt:
        return 130
    except StackError:
        raise
    except Exception:
        raise StackError("local_stack_failed") from None


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise StackError("invalid_arguments")


def main(argv=None):
    previous = {}
    try:
        parser = Parser(description="Foreground local CPU detector and gateway")
        parser.add_argument("action", choices=("plan", "run"))
        for name in ("package", "state-dir", "cert", "key", "ca"):
            parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--model-id", required=True)
        parser.add_argument("--gateway-port", type=int, default=8765)
        parser.add_argument("--detector-port", type=int, default=8766)
        parser.add_argument("--startup-timeout", type=float, default=120)
        values = vars(parser.parse_args(argv))
        action = values.pop("action")
        options = Options(**values)
        if action == "plan":
            print(json.dumps(plan(options)))
            return 0
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, interrupted)
        return run_stack(options, os.environ)
    except StackError as exc:
        print(json.dumps({"status":"error", "code":str(exc)}))
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        print(json.dumps({"status":"error", "code":"local_stack_failed"}))
        return 2
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
