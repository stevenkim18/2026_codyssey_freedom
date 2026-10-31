import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from deployment.askpass import PasswordAuthError, password_session


@pytest.fixture
def private_access(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    access = private / "access.json"
    access.write_text(json.dumps({"servers": {"gpu": {
        "private_ip": "192.0.2.10", "username": "synthetic", "password": "synthetic-only-password"}}}))
    access.chmod(0o600)
    return access


def invoke(env, prompt):
    return subprocess.run([env["SSH_ASKPASS"], prompt], env=env, capture_output=True, text=True, timeout=10)


def test_actual_fresh_helper_returns_secret_once_only(private_access, tmp_path):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        assert "synthetic-only-password" not in json.dumps(env)
        first = invoke(env, "synthetic@192.0.2.10's password: ")
        assert first.returncode == 0
        assert first.stdout == "synthetic-only-password\n" and first.stderr == ""
        second = invoke(env, "synthetic@192.0.2.10's password: ")
        assert second.returncode != 0 and second.stdout == "" and second.stderr == ""


def test_wrong_prompt_never_discloses_secret(private_access, tmp_path):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        for prompt in ("other@192.0.2.10's password:", "Password:", "OTP:", "synthetic@foreign.example's password:"):
            result = invoke(env, prompt)
            assert result.returncode != 0 and result.stdout == "" and result.stderr == ""


def test_session_expiry_disables_saved_helper(private_access, tmp_path):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        retained = dict(env)
    result = invoke(retained, "synthetic@192.0.2.10's password:")
    assert result.returncode != 0 and result.stdout == "" and result.stderr == ""
    assert private_access.exists()


@pytest.mark.parametrize("change", ["mode", "symlink", "newline", "role"])
def test_invalid_private_source_fails_before_helper(private_access, tmp_path, change):
    role = "gpu"
    if change == "mode":
        private_access.chmod(0o644)
    elif change == "symlink":
        link = private_access.parent / "link.json"
        link.symlink_to(private_access)
        private_access = link
    elif change == "newline":
        data = json.loads(private_access.read_text())
        data["servers"]["gpu"]["password"] = "line1\nline2"
        private_access.write_text(json.dumps(data))
    else:
        role = "unknown"
    with pytest.raises(PasswordAuthError):
        with password_session(private_access, role, cache_root=tmp_path):
            pytest.fail("invalid credentials were accepted")


def test_password_transport_keeps_host_trust_and_one_attempt(tmp_path):
    from deployment.core import ssh_argv
    known = tmp_path / "known_hosts"
    known.write_text("synthetic host key\n")
    known.chmod(0o600)
    access = {"private_ip": "192.0.2.10", "username": "synthetic", "ssh_port": 22}
    arguments = ssh_argv(access, known, "/usr/bin/python3", password=True)
    for option in ("StrictHostKeyChecking=yes", "BatchMode=no", "NumberOfPasswordPrompts=1",
                   "PreferredAuthentications=password", "PubkeyAuthentication=no", "KbdInteractiveAuthentication=no"):
        assert option in arguments
    with pytest.raises(Exception):
        ssh_argv(access, known, "/usr/bin/python3", interactive=True, password=True)


def test_cli_password_session_only_supplies_child_environment(monkeypatch, tmp_path, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import deployment.__main__ as cli
    known = tmp_path / "known_hosts"
    known.write_text("synthetic key\n")
    known.chmod(0o600)
    monkeypatch.setattr(cli, "load_access", lambda *a: {"hostname":"synthetic-host", "private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":22})
    events = []
    @contextmanager
    def session(path, role):
        events.append(("opened", role))
        yield {"SSH_ASKPASS": "synthetic-helper"}
        events.append(("closed", role))
    monkeypatch.setattr(cli, "password_session", session)
    def run(command, **kwargs):
        assert kwargs["env"] == {"SSH_ASKPASS": "synthetic-helper"}
        assert "synthetic-only-password" not in " ".join(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status":"preflight_ok", "details":{}}))
    assert cli.main(["preflight", "--role", "gpu", "--known-hosts", str(known), "--password-auth"], run=run) == 0
    assert events == [("opened", "gpu"), ("closed", "gpu")]
    assert "synthetic-helper" not in capsys.readouterr().out


def test_managed_ssh_isolates_user_config_and_quotes_space_path(tmp_path):
    from deployment.core import ssh_argv
    folder = tmp_path / "folder with spaces"
    folder.mkdir(mode=0o700)
    known = folder / "known_hosts"
    known.write_text("synthetic host key\n")
    known.chmod(0o600)
    argv = ssh_argv({"private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":22}, known, "/usr/bin/python3")
    assert argv[argv.index("-F")+1] == "/dev/null"
    assert 'UserKnownHostsFile="'+str(known)+'"' in argv
    completed = subprocess.run(argv[:1]+["-G"]+argv[1:-1], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    for setting in ("stricthostkeychecking true", "verifyhostkeydns false"):
        assert setting in completed.stdout.splitlines()


@pytest.mark.parametrize("name", ['known"hosts', "known%hosts", "known$hosts", "known\\hosts", "known\nhosts"])
def test_ssh_config_path_expansions_are_refused(tmp_path, name):
    from deployment.core import ssh_argv, DeploymentError
    known = tmp_path / name
    known.write_text("synthetic host key\n")
    known.chmod(0o600)
    with pytest.raises(DeploymentError):
        ssh_argv({"private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":22}, known, "/usr/bin/python3")


@pytest.mark.parametrize("change", ["owner", "writable_parent"])
def test_verified_host_store_cannot_be_replaced_by_other_users(tmp_path, monkeypatch, change):
    import deployment.core as core
    folder = tmp_path / "trusted"
    folder.mkdir(mode=0o700)
    known = folder / "known_hosts"
    known.write_text("synthetic key\n")
    known.chmod(0o600)
    if change == "owner":
        original_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: original_uid + 1)
    else:
        folder.chmod(0o777)
    with pytest.raises(core.DeploymentError):
        core.ssh_argv({"private_ip":"192.0.2.10", "username":"synthetic", "ssh_port":22}, known, "/usr/bin/python3")


@pytest.mark.parametrize("change", ["symlink", "writable"])
def test_helper_metadata_is_never_created_in_unsafe_cache(private_access, tmp_path, change):
    directory = tmp_path / "cache"
    directory.mkdir(mode=0o700)
    cache = directory
    if change == "symlink":
        cache = tmp_path / "cache-link"
        cache.symlink_to(directory)
    else:
        directory.chmod(0o777)
    with pytest.raises(PasswordAuthError):
        with password_session(private_access, "gpu", cache_root=cache):
            pytest.fail("unsafe cache accepted")
    assert list(directory.iterdir()) == []


def test_closed_metadata_cannot_reactivate_expired_callback(private_access, tmp_path, monkeypatch, capsys):
    import deployment.askpass as askpass
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        context = Path(env["PII_DEPLOY_AUTH_CONTEXT"])
    monkeypatch.setenv("PII_DEPLOY_AUTH_CONTEXT", str(context.with_name("closed.json")))
    assert askpass.main(["synthetic@192.0.2.10's password:"]) == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("arguments", [[], ["prompt", "extra"]])
def test_direct_helper_misuse_is_silent(arguments, capsys):
    import deployment.askpass as askpass
    assert askpass.main(arguments) == 1
    assert capsys.readouterr() == ("", "")


def test_session_preserves_metadata_moved_by_owner(private_access, tmp_path):
    with password_session(private_access, "gpu", cache_root=tmp_path) as env:
        context = Path(env["PII_DEPLOY_AUTH_CONTEXT"])
        preserved = context.with_name("preserved.json")
        context.rename(preserved)
    assert preserved.is_file() and private_access.is_file()
