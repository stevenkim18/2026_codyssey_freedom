"""Local orchestration only: synthetic package, injected processes, no model/provider calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import socket
import threading
import time

import pytest

from deployment.tests.test_gpu_tls import certificates
from detector.tests.test_runtime import package
from launcher import local_stack as stack


@pytest.fixture
def options(tmp_path, certificates):
    root, _ = package(tmp_path)
    cert, key = certificates[0]
    return stack.Options(root, tmp_path / "state", cert, key, cert, "gpt-6.1-sol")


def test_plan_is_read_only_and_honest(options, monkeypatch):
    torch_before = "torch" in sys.modules
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("child in plan"))
    report = stack.plan(options)
    assert report["model_hashes"] == "unchecked"
    assert report["model_readiness"] == "unchecked"
    assert not options.state_dir.exists()
    assert ("torch" in sys.modules) == torch_before
    assert str(options.package) not in json.dumps(report)


def test_preflight_rejects_bad_ports_and_ca(options, certificates):
    from dataclasses import replace
    for changed in [replace(options, detector_port=options.gateway_port), replace(options, gateway_port=0),
                    replace(options, ca=certificates[1][0])]:
        with pytest.raises(stack.StackError):
            stack.plan(changed)


def test_private_lock_rejects_overlap_symlink_hardlink_permissions(options, tmp_path):
    with stack.state_lock(options.state_dir):
        assert options.state_dir.stat().st_mode & 0o777 == 0o700
        with pytest.raises(stack.StackError):
            with stack.state_lock(options.state_dir):
                pytest.fail("duplicate owner")
    lock = options.state_dir / ".local-stack.lock"
    assert lock.stat().st_mode & 0o777 == 0o600
    os.link(lock, tmp_path / "other-lock")
    with pytest.raises(stack.StackError):
        with stack.state_lock(options.state_dir):
            pass
    linked = tmp_path / "linked"
    linked.symlink_to(options.state_dir, target_is_directory=True)
    with pytest.raises(stack.StackError):
        with stack.state_lock(linked):
            pass


class Listener:
    def fileno(self):
        return 99
    def close(self):
        pass


class Child:
    def __init__(self):
        self.stopped = False
        self.killed = False
    def poll(self):
        return 0 if self.stopped else None
    def terminate(self):
        self.stopped = True
    def wait(self, timeout):
        return 0
    def kill(self):
        self.killed = True


def test_owned_children_environment_and_cleanup(options, monkeypatch):
    calls = []
    children = []
    env = {"OPENAI_API_KEY": "synthetic-openai", "PII_GATEWAY_LOCAL_TOKEN": "synthetic-local",
           "AWS_SECRET_ACCESS_KEY": "must-not-propagate", "PYTHONPATH": "/bad/path"}
    before = dict(env)
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        child = Child(); children.append(child)
        return child
    def sleep(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(stack, "check_ports", lambda _: [Listener(), Listener()])
    code = stack.run_stack(options, env, popen=popen, probe=lambda *a: True, sleep=sleep)
    assert code == 130 and len(children) == 2
    assert all(child.stopped for child in children)
    detector, gateway = [item[1]["env"] for item in calls]
    assert "OPENAI_API_KEY" not in detector and "PII_GATEWAY_LOCAL_TOKEN" not in detector
    assert gateway["OPENAI_API_KEY"] == "synthetic-openai"
    assert detector["PII_DETECTOR_TOKEN"] == gateway["PII_DETECTOR_TOKEN"]
    assert detector["PII_DETECTOR_TOKEN"] != "synthetic-local"
    assert all("AWS_SECRET_ACCESS_KEY" not in e and "PYTHONPATH" not in e for e in (detector, gateway))
    assert all(item[0][0] == sys.executable and item[0][1] == "-m" for item in calls)
    assert env == before


def test_start_failure_cleans_first_child(options, monkeypatch):
    child = Child()
    def popen(command, **kwargs):
        if command[-1] == "gateway":
            raise OSError("sensitive raw exception")
        return child
    monkeypatch.setattr(stack, "check_ports", lambda _: [Listener(), Listener()])
    with pytest.raises(stack.StackError):
        stack.run_stack(options, {"OPENAI_API_KEY":"key", "PII_GATEWAY_LOCAL_TOKEN":"token"},
                        popen=popen, probe=lambda *a: True)
    assert child.stopped


def test_missing_credentials_no_mutation(options):
    with pytest.raises(stack.StackError):
        stack.run_stack(options, {}, popen=lambda *a, **k: pytest.fail("spawn"))
    assert not options.state_dir.exists()


def test_cli_sanitizes_unknown_values(capsys):
    assert stack.main(["plan", "--secret=do-not-print"]) == 2
    assert "do-not-print" not in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["deadline", "exit"])
def test_startup_failure_is_bounded_and_owned(options, monkeypatch, mode):
    child = Child()
    tick = [0]
    def clock():
        tick[0] += 50
        return tick[0]
    if mode == "exit":
        child.stopped = True
    monkeypatch.setattr(stack, "check_ports", lambda _: [Listener(), Listener()])
    with pytest.raises(stack.StackError, match="startup_timeout" if mode == "deadline" else "child_exited"):
        stack.run_stack(options, {"OPENAI_API_KEY":"key", "PII_GATEWAY_LOCAL_TOKEN":"token"},
                        popen=lambda *a, **k: child, probe=lambda *a: False, sleep=lambda _:None, clock=clock)
    assert child.stopped


def test_cleanup_kills_only_uncooperative_owned_child():
    class Stubborn(Child):
        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("owned", timeout)
            return 0
    child = Stubborn()
    stack._stop([child])
    assert child.stopped and child.killed


@pytest.mark.parametrize("status,body,expected", [(200,b'{"status":"ready"}', True),
    (302,b'{"status":"ready"}',False), (200,b'{"status":"alive"}',False),
    (200,b'{"status":"ready","extra":true}',False), (200,b'not-json',False)])
def test_readiness_exact_contract_and_proxy_redirect_disabled(monkeypatch, status, body, expected):
    import httpx
    original = httpx.AsyncClient
    def factory(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["verify"] is True
        return original(**kwargs, transport=httpx.MockTransport(lambda _:httpx.Response(status, content=body)))
    monkeypatch.setattr(stack.httpx, "AsyncClient", factory)
    assert stack.probe_ready("http://127.0.0.1:8765/readyz", True, 1) is expected


def test_cleanup_ignores_second_signal_and_restores_handlers():
    previous = signal.signal(signal.SIGTERM, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    class InterruptedChild(Child):
        def terminate(self):
            os.kill(os.getpid(), signal.SIGTERM)
            super().terminate()
    child = InterruptedChild()
    try:
        try:
            stack._stop([child])
        except KeyboardInterrupt:
            pytest.fail("second signal interrupted cleanup")
        assert child.stopped
        assert signal.getsignal(signal.SIGTERM) != signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_ports_remain_owned_until_explicit_close():
    from types import SimpleNamespace
    held = stack.check_ports(SimpleNamespace(detector_port=0, gateway_port=0))
    assert held is not None
    try:
        for listener in held:
            with socket.socket() as competing:
                with pytest.raises(OSError):
                    competing.bind(listener.getsockname())
    finally:
        for listener in held:
            listener.close()


def test_slow_response_headers_obey_total_deadline():
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen()
    stopped = threading.Event()
    def serve():
        connection, _ = listener.accept()
        with connection:
            connection.recv(4096)
            for char in b"HTTP/1.1 200 OK\r\nContent-Length: 18\r\n\r\n":
                if stopped.wait(.03):
                    break
                try:
                    connection.send(bytes([char]))
                except OSError:
                    break
    thread = threading.Thread(target=serve); thread.start()
    start = time.monotonic()
    try:
        assert not stack.probe_ready(f"http://127.0.0.1:{listener.getsockname()[1]}/readyz", True, .15)
        assert time.monotonic() - start < .6
    finally:
        stopped.set(); listener.close(); thread.join(2)


def test_actual_gateway_child_serves_only_inherited_socket(options):
    from dataclasses import replace
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(128)
    options = replace(options, gateway_port=listener.getsockname()[1])
    options.state_dir.mkdir(mode=0o700)
    _, env = stack._environments(options, {"OPENAI_API_KEY":"synthetic-never-used",
        "PII_GATEWAY_LOCAL_TOKEN":"synthetic-local"}, "synthetic-detector")
    env["PII_LOCAL_LISTENER_FD"] = str(listener.fileno())
    child = subprocess.Popen([sys.executable, "-m", "launcher.local_child", "gateway"],
        cwd=str(Path(__file__).resolve().parents[2]), env=env, pass_fds=(listener.fileno(),),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            assert child.poll() is None
            if stack.probe_ready(f"http://127.0.0.1:{options.gateway_port}/readyz", True, .3):
                break
        else:
            pytest.fail("gateway child did not serve inherited socket")
        with socket.socket() as competing:
            with pytest.raises(OSError):
                competing.bind(listener.getsockname())
    finally:
        stack._stop([child]); listener.close()
    assert child.poll() is not None


def test_detector_bootstrap_preserves_tls_on_inherited_socket(options, monkeypatch):
    from launcher import local_child
    from dataclasses import replace
    import detector.server
    listener = socket.socket(); listener.bind(("127.0.0.1", 0)); listener.listen(128)
    options = replace(options, detector_port=listener.getsockname()[1])
    env, _ = stack._environments(options, {}, "synthetic", planning=True)
    env["PII_LOCAL_LISTENER_FD"] = str(os.dup(listener.fileno()))
    captured = {}
    class Server:
        def __init__(self, config):
            captured["config"] = config
        def run(self, sockets):
            assert sockets[0].getsockname() == listener.getsockname()
    monkeypatch.setattr(local_child.os, "environ", env)
    monkeypatch.setattr(detector.server, "create_server", lambda _: object())
    monkeypatch.setattr(local_child.uvicorn, "Server", Server)
    try:
        assert local_child.main(["detector"]) == 0
        assert captured["config"].ssl_certfile == str(options.cert)
        assert captured["config"].ssl_keyfile == str(options.key)
    finally:
        listener.close()


def test_readiness_cannot_win_against_owned_child_exit(options, monkeypatch):
    child = Child()
    def probe(*args):
        child.stopped = True
        return True
    monkeypatch.setattr(stack, "check_ports", lambda _: [Listener(), Listener()])
    with pytest.raises(stack.StackError, match="child_exited"):
        stack.run_stack(options, {"OPENAI_API_KEY":"key", "PII_GATEWAY_LOCAL_TOKEN":"token"},
                        popen=lambda *a, **k: child, probe=probe)
