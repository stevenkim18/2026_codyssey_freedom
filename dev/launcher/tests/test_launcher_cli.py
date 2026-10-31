"""R01/T11: executable entrypoint, isolated Codex HOME and real subprocess flow."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from launcher import __main__ as cli
from launcher.profiles import PROFILE, PINNED_CLI, install, launch_arguments


SECRET="SYNTHETIC_LOCAL_TOKEN"


@pytest.fixture
def binary(monkeypatch,tmp_path):
    path=tmp_path / "codex"; path.write_text("# synthetic executable"); path.chmod(0o700)
    monkeypatch.setattr(cli.shutil,"which",lambda name:str(path))
    return path


def good_version(*args,**kwargs):
    return SimpleNamespace(returncode=0,stdout="codex-cli 0.166.0\n",stderr="")


@pytest.mark.parametrize("variant",["wrong","empty","failed","secret","timeout","missing"])
def test_actual_version_probe_required_before_any_installation(tmp_path,monkeypatch,binary,capsys,variant):
    home=tmp_path / "home"
    if variant == "missing": monkeypatch.setattr(cli.shutil,"which",lambda _:None)
    def run(args,**kwargs):
        assert args == [str(binary),"--version"]
        if variant == "timeout": raise subprocess.TimeoutExpired(SECRET,5,output=SECRET)
        return SimpleNamespace(returncode=1 if variant == "failed" else 0,
            stdout={"wrong":"codex-cli 0.166.1","empty":"","secret":SECRET}.get(variant,"codex-cli 0.166.0"),stderr=SECRET)
    assert cli.main(["install","--codex-home",str(home)],run=run) == 2
    assert not home.exists() and SECRET not in repr(capsys.readouterr())


def test_install_default_model_and_run_uses_same_probed_binary(tmp_path,monkeypatch,binary):
    home=tmp_path / "home"; monkeypatch.setenv("PII_GATEWAY_LOCAL_TOKEN",SECRET)
    assert cli.main(["install","--codex-home",str(home)],run=good_version) == 0
    assert 'model = "gpt-6.1-sol"' in (home / PROFILE).read_text()
    calls=[]
    def run(args,**kwargs):
        calls.append((args,kwargs))
        if args[-1] == "--version": return good_version()
        assert args[0] == str(binary) and args[1] == "exec" and "--ignore-user-config" in args
        assert kwargs["env"]["CODEX_HOME"] == str(home)
        assert SECRET not in repr(args) and kwargs["env"]["PII_GATEWAY_LOCAL_TOKEN"] == SECRET
        return SimpleNamespace(returncode=7)
    assert cli.main(["run","--codex-home",str(home),"--","exec","synthetic prompt"],run=run) == 7
    assert len(calls) == 2 and calls[-1][0][-1] == "synthetic prompt"


@pytest.mark.parametrize("args",[["interactive"],["exec","--config","model_provider=other"],["exec","-cmodel=other"],
    ["exec","--profile=other"],["exec","--enable","shell_tool"],["exec","--dangerously-bypass-approvals-and-sandbox"]])
def test_bypass_arguments_never_spawn_exec(tmp_path,monkeypatch,binary,args):
    home=tmp_path / "home"; install(home,port=8765,model="gpt-6.1-sol",cli_version=PINNED_CLI)
    monkeypatch.setenv("PII_GATEWAY_LOCAL_TOKEN",SECRET)
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        assert argv[-1] == "--version"
        return good_version()
    assert cli.main(["run","--codex-home",str(home),"--",*args],run=run) == 2
    assert len(calls) <= 1


def test_tampered_profile_and_missing_token_fail_without_exec(tmp_path,monkeypatch,binary):
    home=tmp_path / "home"; install(home,port=8765,model="gpt-6.1-sol",cli_version=PINNED_CLI)
    monkeypatch.delenv("PII_GATEWAY_LOCAL_TOKEN",raising=False)
    assert cli.main(["run","--codex-home",str(home),"--","exec","prompt"],run=good_version) == 2
    monkeypatch.setenv("PII_GATEWAY_LOCAL_TOKEN",SECRET)
    (home / PROFILE).write_text("# operator changes\n")
    assert cli.main(["run","--codex-home",str(home),"--","exec","prompt"],run=good_version) == 2
    assert (home / PROFILE).read_text() == "# operator changes\n"


def test_default_home_is_dedicated_and_global_config_untouched(tmp_path,monkeypatch,binary):
    monkeypatch.setenv("CODEX_HOME",str(tmp_path))
    config=tmp_path / "config.toml"; config.write_text("# user global config\n")
    assert cli.main(["install"],run=good_version) == 0
    assert (tmp_path / "pii-gateway-home" / PROFILE).is_file()
    assert not (tmp_path / PROFILE).exists() and config.read_text() == "# user global config\n"


def test_uninstall_restores_existing_profile_without_requiring_codex(tmp_path,monkeypatch):
    original=b'# existing operator profile\n'; (tmp_path / PROFILE).write_bytes(original)
    install(tmp_path,port=8765,model="gpt-6.1-sol",cli_version=PINNED_CLI)
    monkeypatch.setattr(cli.shutil,"which",lambda _:None)
    def forbidden(*args,**kwargs): pytest.fail("recovery must not require executable")
    assert cli.main(["uninstall","--codex-home",str(tmp_path)],run=forbidden) == 0
    assert (tmp_path / PROFILE).read_bytes() == original
    assert cli.main(["uninstall","--codex-home",str(tmp_path)],run=forbidden) == 0
    assert (tmp_path / PROFILE).read_bytes() == original


def test_failed_reconfiguration_preserves_original_backup_and_installed_profile(tmp_path,binary):
    profile=tmp_path / PROFILE; original=b"# original\n"; profile.write_bytes(original)
    assert cli.main(["install","--codex-home",str(tmp_path)],run=good_version) == 0
    installed=profile.read_bytes()
    assert cli.main(["install","--codex-home",str(tmp_path),"--port","8877"],run=good_version) == 2
    assert profile.read_bytes() == installed
    assert (tmp_path / ".pii-gateway-install/original.toml").read_bytes() == original


def test_fresh_process_install_validate_run_uninstall_without_real_codex(tmp_path):
    tools=tmp_path / "bin"; tools.mkdir(); executable=tools / "codex"
    executable.write_text("#!"+sys.executable+"\nimport json,os,sys\nfrom pathlib import Path\n"
        "if sys.argv[1:] == ['--version']: print('codex-cli 0.166.0')\n"
        "else: Path(os.environ['TEST_LAUNCH_RECORD']).write_text(json.dumps({'args':sys.argv[1:],'home':os.environ['CODEX_HOME']}))\n")
    executable.chmod(0o700)
    home=tmp_path / "dedicated"; record=tmp_path / "calls.json"
    env=os.environ | {"PATH":str(tools)+os.pathsep+os.environ.get("PATH",""),"PII_GATEWAY_LOCAL_TOKEN":SECRET,"TEST_LAUNCH_RECORD":str(record)}
    for command in (["install","--codex-home",str(home)],["run","--codex-home",str(home),"--","exec","synthetic prompt"],["uninstall","--codex-home",str(home)]):
        result=subprocess.run([sys.executable,"-m","launcher",*command],env=env,capture_output=True,text=True,timeout=15)
        assert result.returncode == 0 and SECRET not in result.stdout+result.stderr
        if command[0] == "install":
            assert launch_arguments(PINNED_CLI,["exec","prompt"],codex_home=home)[0] == "codex"
    value=json.loads(record.read_text())
    assert value["home"] == str(home) and "--ignore-user-config" in value["args"]
    assert not (home / PROFILE).exists() and not (home / ".pii-gateway-install").exists()


def test_unknown_cli_flags_do_not_echo_possible_secret_values(capsys):
    assert cli.main(["install","--token",SECRET],run=good_version) == 2
    assert SECRET not in repr(capsys.readouterr())


@pytest.mark.parametrize("path",["relative","/","/tmp/../other","/tmp/line\nbreak"])
def test_unsafe_home_is_rejected_before_version_or_mutation(path,capsys):
    def forbidden(*args,**kwargs): pytest.fail("unsafe home must fail before version probe")
    assert cli.main(["install","--codex-home",path],run=forbidden) == 2
    assert "invalid_dedicated_home" in capsys.readouterr().err


def test_home_symlink_never_redirects_profile_writes(tmp_path,binary):
    outside=tmp_path / "outside"; outside.mkdir(); home=tmp_path / "link"; home.symlink_to(outside)
    assert cli.main(["install","--codex-home",str(home)],run=good_version) == 2
    assert not list(outside.iterdir())


def test_default_without_codex_home_uses_dedicated_subdirectory(tmp_path,monkeypatch,binary):
    monkeypatch.delenv("CODEX_HOME",raising=False)
    monkeypatch.setattr(cli.Path,"home",lambda:tmp_path)
    assert cli.main(["install"],run=good_version) == 0
    assert (tmp_path / ".codex/pii-gateway-home" / PROFILE).exists()


@pytest.mark.parametrize("failure,expected",[(RuntimeError(SECRET),2),(KeyboardInterrupt(),130)])
def test_execution_failures_are_sanitized(tmp_path,monkeypatch,binary,capsys,failure,expected):
    home=tmp_path / "home"; install(home,port=8765,model="gpt-6.1-sol",cli_version=PINNED_CLI)
    monkeypatch.setenv("PII_GATEWAY_LOCAL_TOKEN",SECRET)
    def run(args,**kwargs):
        if args[-1] == "--version": return good_version()
        raise failure
    assert cli.main(["run","--codex-home",str(home),"--","exec","prompt"],run=run) == expected
    assert SECRET not in repr(capsys.readouterr())
