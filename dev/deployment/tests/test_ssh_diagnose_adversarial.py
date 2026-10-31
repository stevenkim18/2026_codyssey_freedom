"""Independent no-network failure-evidence and owned-process cleanup checks."""
import json
import subprocess

import pytest

from deployment import ssh_diagnose as diagnostic

ACCESS = {"private_ip": "192.0.2.10", "username": "synthetic", "ssh_port": 22}


def test_spawn_failure_still_writes_sanitized_failure_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(diagnostic, "load_access", lambda *args: ACCESS)
    def fail(*args, **kwargs):
        raise OSError("SYNTHETIC_PRIVATE_DIAGNOSTIC")
    monkeypatch.setattr(diagnostic.subprocess, "Popen", fail)
    output = tmp_path / "evidence.json"
    assert diagnostic.main(["--role", "gpu", "--output", str(output)]) == 2
    evidence = json.loads(output.read_text())
    assert evidence["status"] == "failed"
    assert "SYNTHETIC_PRIVATE_DIAGNOSTIC" not in output.read_text() + repr(capsys.readouterr())


def test_early_observation_failure_kills_and_reaps_uncooperative_owned_process(tmp_path, monkeypatch):
    class Process:
        returncode = None
        killed = False
        reaped = False
        def poll(self): return self.returncode
        def terminate(self): pass
        def kill(self): self.killed = True
        def communicate(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("synthetic-owned-ssh", timeout)
            self.returncode = -9
            self.reaped = True
            return "", ""
    process = Process()
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(diagnostic.time, "sleep", lambda delay: None)
    def fail(arguments): raise RuntimeError("synthetic observation failure")
    monkeypatch.setattr(diagnostic, "_local_output", fail)
    with pytest.raises(Exception):
        diagnostic.diagnose_one(ACCESS, tmp_path / "known_hosts")
    assert process.killed and process.reaped


@pytest.mark.parametrize("message,expected_exit,phase", [
    ("Connection established. Permission denied (publickey,password).", 0, "authentication"),
    ("Operation timed out", 1, "tcp_connect"),
])
def test_full_driver_emits_only_sanitized_observations(tmp_path, monkeypatch, capsys, message, expected_exit, phase):
    class Process:
        returncode = 255
        def communicate(self, timeout): return "SYNTHETIC_STDOUT_PRIVATE", message + " SYNTHETIC_STDERR_PRIVATE"
        def poll(self): return self.returncode
    monkeypatch.setattr(diagnostic, "load_access", lambda *a: ACCESS)
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(diagnostic.time, "sleep", lambda _: None)
    monkeypatch.setattr(diagnostic.sys, "platform", "darwin")
    def local(arguments):
        if arguments[0] == "netstat":
            return "tcp4 0 0 192.0.2.1.1234 192.0.2.10.22 SYN_SENT\ntcp4 0 0 192.0.2.1.1235 192.0.2.99.22 ESTABLISHED\n"
        return "gateway: 192.0.2.1\ninterface: en0\n"
    monkeypatch.setattr(diagnostic, "_local_output", local)
    output = tmp_path / "evidence.json"
    assert diagnostic.main(["--role", "gpu", "--output", str(output)]) == expected_exit
    report = json.loads(output.read_text())
    server = report["servers"]["gpu"]
    assert server["phase"] == phase and server["socket_states"] == ["SYN_SENT"]
    assert server["password_authentication_attempted"] is False
    assert server["route"] == {"interface": "en0", "matches_default_route": True}
    assert "not PID-bound" in server["socket_observation_scope"]
    serialized = output.read_text() + repr(capsys.readouterr())
    assert "192.0.2." not in serialized and "SYNTHETIC_" not in serialized
    assert "root cause unconfirmed" in report["scope"]


def test_existing_evidence_is_preserved_and_prevents_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "load_access", lambda *a: ACCESS)
    monkeypatch.setattr(diagnostic, "diagnose_one", lambda *a: pytest.fail("probe must not start"))
    output = tmp_path / "evidence.json"
    output.write_text("preserve prior evidence")
    assert diagnostic.main(["--output", str(output)]) == 2
    assert output.read_text() == "preserve prior evidence"


@pytest.mark.parametrize("mode", ["nonzero", "exception"])
def test_local_metadata_failure_yields_no_route_claim(monkeypatch, mode):
    from types import SimpleNamespace
    def local(*args, **kwargs):
        if mode == "exception": raise subprocess.TimeoutExpired("local-metadata", 2, output="SYNTHETIC_PRIVATE")
        return SimpleNamespace(returncode=1, stdout="interface: deceptive\ngateway: deceptive", stderr="SYNTHETIC_PRIVATE")
    monkeypatch.setattr(diagnostic.subprocess, "run", local)
    value = diagnostic._local_output(["route", "-n", "get", "default"])
    assert value == ""
    assert diagnostic.route_observation(value, value) == {"available": False}


@pytest.mark.parametrize("ignore_terminate", [False, True])
def test_deadline_cleanup_terminates_or_kills_only_owned_process(tmp_path, monkeypatch, ignore_terminate):
    class Process:
        returncode = None
        terminated = False
        killed = False
        def communicate(self, timeout):
            assert 0 < timeout <= 8
            if self.killed or self.terminated and not ignore_terminate:
                self.returncode = -9 if self.killed else -15
                return "", ""
            raise subprocess.TimeoutExpired("synthetic-owned-ssh", timeout)
        def terminate(self): self.terminated = True
        def kill(self): self.killed = True
        def poll(self): return self.returncode
    process = Process()
    monkeypatch.setattr(diagnostic.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(diagnostic.time, "sleep", lambda _: None)
    monkeypatch.setattr(diagnostic, "_local_output", lambda _: "")
    report = diagnostic.diagnose_one(ACCESS, tmp_path / "known_hosts")
    assert process.terminated and process.killed == ignore_terminate and process.returncode is not None
    assert report["phase"] == "tcp_connect" and report["code"] == "timeout"
    assert report["socket_states"] == [] and "empty is inconclusive" in report["socket_observation_scope"]


def test_already_exited_owned_process_is_reaped(monkeypatch):
    class Process:
        def terminate(self): raise ProcessLookupError
        def communicate(self, timeout): return "", "captured diagnostic"
    assert diagnostic._terminate_owned(Process()) == ("", "captured diagnostic")
