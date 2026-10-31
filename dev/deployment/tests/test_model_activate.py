import hashlib
import os
from pathlib import Path

import pytest
from detector.tests.test_runtime import package
from deployment import model_activate as activation


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "control"
    root.mkdir(mode=0o700)
    old, _ = package(tmp_path, "old")
    new, _ = package(tmp_path, "new")
    env = root / "service.env"
    env.write_text(f"PII_MODEL_PACKAGE={old}\nPII_DETECTOR_TOKEN=SYNTHETIC_SECRET\nKEEP=value\n")
    env.chmod(0o600)
    return root, old, new, env


def call(setup, **kwargs):
    root, old, new, env = setup
    kwargs.setdefault("binding", lambda *a, **k: None)
    return activation.activate(new, root, env, hashlib.sha256((new / "manifest.json").read_bytes()).hexdigest(),
        origin="https://127.0.0.1:8766", ca_file=root / "ca.pem", allow_engineering_test=True, **kwargs)


def test_success_and_idempotence(setup):
    calls = []
    result = call(setup, command=lambda: calls.append("restart"), probe=lambda *a: calls.append("probe"))
    assert result["status"] == "activated" and calls == ["restart", "probe"]
    assert "KEEP=value" in setup[3].read_text()
    assert (setup[0] / "previous.env").stat().st_mode & 0o777 == 0o600
    assert call(setup, command=lambda: calls.append("restart"), probe=lambda *a: None)["status"] == "already_active"
    assert calls.count("restart") == 1


def test_integrity_failure_never_restarts(setup):
    (setup[2] / "weights.bin").write_bytes(b"partial")
    with pytest.raises(activation.ActivationError):
        call(setup, command=lambda: pytest.fail("restarted"), probe=lambda *a: None)
    assert str(setup[1]) in setup[3].read_text()


def test_restart_failure_restores_and_verifies_previous(setup):
    before = setup[3].read_bytes()
    calls = []
    def restart():
        calls.append(1)
        if len(calls) == 1: raise RuntimeError("PRIVATE")
    checked = []
    report = call(setup, command=restart, probe=lambda env, manifest, *a: checked.append(manifest["model_version"]))
    assert report == {"status": "failed", "code": "activation_failed", "rollback": "success"}
    assert checked == ["old"] and len(calls) == 2
    assert setup[3].read_bytes() == before


def test_failed_restore_reported(setup, monkeypatch):
    original = activation._atomic
    count = 0
    def atomic(*args):
        nonlocal count
        count += 1
        if count == 2: raise OSError("PRIVATE")
        return original(*args)
    monkeypatch.setattr(activation, "_atomic", atomic)
    report = call(setup, command=lambda: (_ for _ in ()).throw(RuntimeError("PRIVATE")), probe=lambda *a: None)
    assert report["rollback"] == "failure"
    assert "PRIVATE" not in str(report)


def test_lock_contention(setup):
    import fcntl
    path = setup[0] / "activate.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(activation.ActivationError, match="activation_in_progress"):
            call(setup, command=lambda: pytest.fail("restarted"), probe=lambda *a: None)
    finally:
        os.close(descriptor)


def test_partial_atomic_write_preserves_original(setup, monkeypatch):
    before = setup[3].read_bytes()
    def failed_replace(*a): raise OSError("PRIVATE")
    monkeypatch.setattr(activation.os, "replace", failed_replace)
    result = call(setup, command=lambda: pytest.fail("restarted"), probe=lambda *a: None)
    assert result["rollback"] == "failure"
    assert setup[3].read_bytes() == before


def test_expected_manifest_digest_required_before_restart(setup):
    root, old, new, env = setup
    with pytest.raises(activation.ActivationError, match="candidate_integrity_failed"):
        activation.activate(new, root, env, "0" * 64, origin="https://127.0.0.1:8766", ca_file=root / "ca",
            allow_engineering_test=True, command=lambda: pytest.fail("restarted"), probe=lambda *a: None)


def test_engineering_permission_never_implied(setup):
    root, old, new, env = setup
    with pytest.raises(activation.ActivationError, match="engineering_test_permission_required"):
        activation.activate(new, root, env, "0" * 64, origin="https://127.0.0.1:8766", ca_file=root / "ca")


def test_probe_failure_rolls_back(setup):
    versions = []
    def probe(env, manifest, *args):
        versions.append(manifest["model_version"])
        if manifest["model_version"] == "new": raise RuntimeError("PRIVATE")
    result = call(setup, command=lambda: None, probe=probe)
    assert result["rollback"] == "success"
    assert versions == ["new", "old"]


def test_real_probe_http_contract_uses_header_and_expected_identity(monkeypatch, setup):
    import json
    original_context = activation.ssl.create_default_context
    monkeypatch.setattr(activation.ssl, "create_default_context", lambda **kwargs: original_context())
    requests = []
    class Response:
        status = 200
        def __init__(self, value): self.value = value
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return json.dumps(self.value).encode()
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 10
            if request.full_url.endswith("healthz"): return Response({"status": "alive"})
            if request.full_url.endswith("readyz"): return Response({"status": "ready"})
            return Response({"schema_version": "1.0", "request_id": "activation-probe",
                "inspection_id": "activation-probe", "status": "complete", "findings": [],
                "model_version": "new", "threshold_version": "new-thresholds", "timing_ms": 1.0,
                "timing_breakdown_ms": {"queue": 0, "inference": 1, "rules": 0, "total": 1}})
    monkeypatch.setattr(activation.urllib.request, "build_opener", lambda *handlers: Opener())
    environment = {"PII_DETECTOR_TOKEN": "SYNTHETIC_SECRET"}
    manifest = {"model_version": "new", "threshold_version": "new-thresholds"}
    activation._probe(environment, manifest, "https://localhost:8766", setup[0] / "ca")
    assert len(requests) == 3
    assert requests[-1].get_header("Authorization") == "Bearer SYNTHETIC_SECRET"
    assert all("SYNTHETIC_SECRET" not in request.full_url for request in requests)
    with pytest.raises(activation.ActivationError, match="verification_failed"):
        activation._probe(environment, manifest | {"model_version": "wrong"}, "https://localhost:8766", setup[0] / "ca")


def test_stale_unit_environment_binding_refused_before_change(setup):
    before = setup[3].read_bytes()
    def reject(*args, **kwargs): raise activation.ActivationError("unit_binding_failed")
    with pytest.raises(activation.ActivationError, match="unit_binding_failed"):
        call(setup, binding=reject, command=lambda: pytest.fail("restarted"), probe=lambda *a: None)
    assert setup[3].read_bytes() == before


@pytest.mark.parametrize("kind", ["keyboard", "term"])
def test_transaction_interrupt_restores_and_defers_second_signal(setup, kind):
    import signal
    before = setup[3].read_bytes()
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    calls = []
    def restart():
        calls.append(1)
        if len(calls) == 1:
            if kind == "keyboard": raise KeyboardInterrupt
            os.kill(os.getpid(), signal.SIGTERM)
        else:
            os.kill(os.getpid(), signal.SIGINT)
    report = call(setup, command=restart, probe=lambda *a: None)
    assert report["rollback"] == "success" and report["code"] == "activation_interrupted"
    assert setup[3].read_bytes() == before and len(calls) == 2
    assert all(signal.getsignal(s) == h for s, h in handlers.items())


@pytest.mark.parametrize("suffix", [" (ignore_errors=yes)", " (ignore_errors=no) /other (ignore_errors=no)", ""])
def test_default_unit_binding_rejects_optional_extra_or_missing_environment(setup, monkeypatch, suffix):
    monkeypatch.setattr(activation, "_show", lambda name: str(setup[3]) + suffix)
    with pytest.raises(activation.ActivationError, match="unit_binding_failed"):
        activation._binding(setup[3], {"PII_MODEL_PACKAGE": str(setup[1])})


def test_default_binding_checks_process_package_without_exposing_environment(setup, monkeypatch):
    import io
    def show(name): return str(setup[3]) + " (ignore_errors=no)" if name == "EnvironmentFiles" else "123"
    monkeypatch.setattr(activation, "_show", show)
    monkeypatch.setattr(Path, "open", lambda *a, **k: io.BytesIO(b"TOKEN=PRIVATE\0PII_MODEL_PACKAGE=/stale\0"))
    with pytest.raises(activation.ActivationError, match="^unit_process_mismatch$"):
        activation._binding(setup[3], {"PII_MODEL_PACKAGE": str(setup[1])}, process=True)


def test_origin_is_bound_to_loopback_and_environment_port(setup):
    root, old, new, env = setup
    with pytest.raises(activation.ActivationError, match="origin_binding_failed"):
        activation.activate(new, root, env, hashlib.sha256((new / "manifest.json").read_bytes()).hexdigest(),
            origin="https://outside.invalid:8766", ca_file=root / "ca", allow_engineering_test=True,
            binding=lambda *a, **k: pytest.fail("binding reached"))


def test_interrupt_immediately_after_atomic_publish_is_recovered(setup, monkeypatch):
    original = activation._atomic
    published = []
    def interrupt(path, data):
        original(path, data)
        published.append(1)
        if len(published) == 1: raise KeyboardInterrupt
    monkeypatch.setattr(activation, "_atomic", interrupt)
    before = setup[3].read_bytes()
    result = call(setup, command=lambda: None, probe=lambda *a: None)
    assert result["rollback"] == "success" and setup[3].read_bytes() == before


@pytest.mark.parametrize("owned,state,address", [(False, "0A", "0100007F"), (True, "01", "0100007F"),
                                                (True, "0A", "00000000")])
def test_owned_listener_rejects_other_process_or_wrong_binding(tmp_path, owned, state, address):
    proc = tmp_path / "proc"
    descriptors = proc / "123" / "fd"
    descriptors.mkdir(parents=True)
    (descriptors / "3").symlink_to("socket:[111]" if owned else "socket:[222]")
    (proc / "net").mkdir()
    (proc / "net" / "tcp").write_text("header\n0: " + address + ":223E 00000000:0000 " + state
        + " 0:0 0:0 0 1000 0 111\n")
    with pytest.raises(activation.ActivationError, match="unit_listener_mismatch"):
        activation._owned_listener("123", {"PII_DETECTOR_PORT": "8766", "PII_DETECTOR_HOST": "127.0.0.1"}, proc=proc)


def test_owned_listener_accepts_same_process_socket(tmp_path):
    proc = tmp_path / "proc"
    descriptors = proc / "123" / "fd"
    descriptors.mkdir(parents=True)
    (descriptors / "3").symlink_to("socket:[111]")
    (proc / "net").mkdir()
    (proc / "net" / "tcp").write_text("header\n0: 00000000:223E 00000000:0000 0A 0:0 0:0 0 1000 0 111\n")
    activation._owned_listener("123", {"PII_DETECTOR_PORT": "8766", "PII_DETECTOR_HOST": "0.0.0.0"}, proc=proc)


def test_process_listener_binding_is_checked_after_successful_probe(setup):
    order = []
    def binding(*args, process=False): order.append("listener" if process else "unit")
    def probe(*args): order.append("probe")
    assert call(setup, binding=binding, probe=probe, command=lambda: order.append("restart"))["status"] == "activated"
    assert order == ["unit", "restart", "probe", "listener"]
    order.clear()
    assert call(setup, binding=binding, probe=probe, command=lambda: pytest.fail("restart"))["status"] == "already_active"
    assert order == ["unit", "probe", "listener"]


@pytest.mark.skipif(not __import__("sys").platform.startswith("linux"), reason="Linux /proc socket attribution")
def test_native_same_process_loopback_listener():
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        activation._owned_listener(str(os.getpid()), {"PII_DETECTOR_HOST": "127.0.0.1",
            "PII_DETECTOR_PORT": str(listener.getsockname()[1])})
