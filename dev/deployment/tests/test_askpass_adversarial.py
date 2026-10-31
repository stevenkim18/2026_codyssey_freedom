"""Independent synthetic askpass boundary probes; no SSH connections."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from deployment.askpass import password_session
from deployment.tests.test_askpass import private_access, invoke

PROMPT = "synthetic@192.0.2.10's password:"


@pytest.mark.parametrize("change", ["context_mode", "context_extra", "context_role", "context_symlink", "access_host", "access_mode", "access_hardlink"])
def test_changed_private_state_fails_closed(private_access, tmp_path, change):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        context = Path(env["PII_DEPLOY_AUTH_CONTEXT"])
        if change == "context_mode":
            context.chmod(0o644)
        elif change in {"context_extra", "context_role"}:
            record = json.loads(context.read_text())
            record["extra" if change == "context_extra" else "role"] = "unknown"
            context.write_text(json.dumps(record))
        elif change == "context_symlink":
            saved = context.with_name("saved.json")
            context.rename(saved)
            context.symlink_to(saved)
        elif change == "access_host":
            record = json.loads(private_access.read_text())
            record["servers"]["gpu"]["private_ip"] = "192.0.2.99"
            private_access.write_text(json.dumps(record))
        elif change == "access_mode":
            private_access.chmod(0o644)
        else:
            os.link(private_access, private_access.with_name("hardlink.json"))
        result = invoke(env, PROMPT)
        assert result.returncode != 0 and result.stdout == result.stderr == ""


def test_concurrent_helpers_disclose_exactly_once(private_access, tmp_path):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: invoke(env, PROMPT), range(4)))
        assert sum(result.returncode == 0 for result in results) == 1
        assert sum(bool(result.stdout) for result in results) == 1
        assert all(result.stderr == "" for result in results)


def test_space_path_optimized_mode_and_exception_expiry(private_access, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    cache = tmp_path / "helper path with spaces"
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        with password_session(private_access, "gpu", cache_root=cache) as env:
            retained = dict(env)
            denied = invoke(env, "OTP:")
            assert denied.returncode != 0 and denied.stdout == denied.stderr == ""
            good = invoke(env, PROMPT)
            assert good.returncode == 0 and good.stdout == "synthetic-only-password\n"
            raise RuntimeError("synthetic interruption")
    expired = invoke(retained, PROMPT)
    assert expired.returncode != 0 and expired.stdout == expired.stderr == ""
    assert not Path(retained["PII_DEPLOY_AUTH_CONTEXT"]).exists()
    assert private_access.exists()


def test_cli_ssh_exception_expires_actual_context(private_access, tmp_path, monkeypatch, capsys):
    import deployment.__main__ as cli
    known = tmp_path / "known_hosts"
    known.write_text("synthetic host key")
    known.chmod(0o600)
    monkeypatch.setattr(cli, "load_access", lambda *a: {"hostname": "synthetic-host", "private_ip": "192.0.2.10", "username": "synthetic", "ssh_port": 22})
    monkeypatch.setattr(cli, "password_session", lambda path, role: password_session(private_access, role, cache_root=tmp_path))
    retained = {}
    def fail_ssh(command, **options):
        retained.update(options["env"])
        raise RuntimeError("synthetic transport interruption")
    assert cli.main(["preflight", "--role", "gpu", "--known-hosts", str(known), "--password-auth"], run=fail_ssh) == 2
    assert not Path(retained["PII_DEPLOY_AUTH_CONTEXT"]).exists()
    result = invoke(retained, PROMPT)
    assert result.returncode != 0 and result.stdout == result.stderr == ""
    output = capsys.readouterr()
    assert "synthetic-only-password" not in output.out + output.err


def test_managed_ssh_overrides_ambient_host_trust_configuration(tmp_path):
    import subprocess
    from deployment.core import ssh_argv
    configuration = tmp_path / "ambient-config"
    configuration.write_text("Host *\n  KnownHostsCommand /bin/echo synthetic-host-key\n  VerifyHostKeyDNS yes\n  ForwardAgent yes\n")
    known = tmp_path / "known_hosts"
    known.write_text("synthetic key")
    known.chmod(0o600)
    args = ssh_argv({"private_ip": "192.0.2.10", "username": "synthetic", "ssh_port": 22}, known, "/usr/bin/python3", password=True)
    # Evaluate only: explicit baseline config emulates ~/.ssh/config without
    # reading or modifying the user's real configuration; -G never connects.
    result = subprocess.run([args[0], "-G", "-F", str(configuration), *args[1:]], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    effective = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert effective.get("knownhostscommand", "none") == "none"
    assert effective.get("verifyhostkeydns") == "false"
    assert effective.get("forwardagent") == "no"


def test_pinned_host_file_rejects_replaceable_parent(tmp_path):
    from deployment.core import DeploymentError, ssh_argv
    directory = tmp_path / "shared-untrusted"
    directory.mkdir()
    directory.chmod(0o777)
    known = directory / "known_hosts"
    known.write_text("synthetic pinned key")
    known.chmod(0o600)
    with pytest.raises(DeploymentError):
        ssh_argv({"private_ip": "192.0.2.10", "username": "synthetic", "ssh_port": 22}, known, "/usr/bin/python3", password=True)
