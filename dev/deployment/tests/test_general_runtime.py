"""General-only rootless runtime preparation; no server traffic in tests."""
import json
from types import SimpleNamespace
import pytest
from deployment import general_runtime as runtime


def test_plan_rejects_existing_package_removals():
    with pytest.raises(ValueError): runtime.summarize_plan({"success":True,"actions":{"UNLINK":[{"name":"python"}],"LINK":[]}})


def test_plan_reports_only_sanitized_versions():
    plan={"success":True,"actions":{"LINK":[{"name":"python","version":"3.12.12","url":"PRIVATE"}]}}
    assert runtime.summarize_plan(plan) == {"python":"3.12.12"}
    plan["actions"]["LINK"][0]["name"]="PRIVATE/path"
    with pytest.raises(ValueError): runtime.summarize_plan(plan)


def test_fixed_remote_guard_blocks_wrong_host_before_mutation(monkeypatch):
    ns={}; exec(runtime.REMOTE_SOURCE,ns)
    monkeypatch.setattr(ns["socket"],"gethostname",lambda:"gp1l4-g3")
    with pytest.raises(ValueError): ns["operate"]("inspect","s2-g3")


def test_cli_always_uses_general_and_never_echoes_remote_errors(monkeypatch,capsys):
    monkeypatch.setattr(runtime,"load_access",lambda path,role: ({"hostname":"s2-g3"} if role == "general" else pytest.fail("wrong role")))
    monkeypatch.setattr(runtime,"ssh_argv",lambda access,known,python,**kwargs:["ssh","general-only"])
    from contextlib import contextmanager
    @contextmanager
    def auth(path,role):
        assert role == "general"; yield {}
    monkeypatch.setattr(runtime,"password_session",auth)
    def run(args,**kwargs):
        assert args == ["ssh","general-only"] and "gp1l4" not in kwargs["input"]
        return SimpleNamespace(returncode=1,stdout="PRIVATE",stderr="PRIVATE")
    assert runtime.main(["inspect"],run=run) == 2
    assert "PRIVATE" not in capsys.readouterr().out


def test_existing_runtime_blocks_bootstrap_before_download(tmp_path,monkeypatch):
    ns={}; exec(runtime.REMOTE_SOURCE,ns)
    monkeypatch.setattr(ns["socket"],"gethostname",lambda:"general-fixture")
    monkeypatch.setattr(ns["platform"],"system",lambda:"Linux")
    monkeypatch.setattr(ns["platform"],"machine",lambda:"x86_64")
    monkeypatch.setattr(ns["Path"],"home",lambda:tmp_path)
    (tmp_path / "miniforge3").mkdir()
    (tmp_path / "miniforge3/sentinel").write_text("preserve")
    monkeypatch.setattr(ns["urllib"].request,"urlopen",lambda *a,**kw:pytest.fail("must not download"))
    with pytest.raises(ValueError): ns["operate"]("bootstrap","general-fixture")
    assert (tmp_path / "miniforge3/sentinel").read_text() == "preserve"


def test_intermediate_envs_symlink_blocks_conda_commands(tmp_path,monkeypatch):
    ns={}; exec(runtime.REMOTE_SOURCE,ns)
    monkeypatch.setattr(ns["socket"],"gethostname",lambda:"general-fixture")
    monkeypatch.setattr(ns["platform"],"system",lambda:"Linux")
    monkeypatch.setattr(ns["platform"],"machine",lambda:"x86_64")
    monkeypatch.setattr(ns["Path"],"home",lambda:tmp_path)
    root=tmp_path / "miniforge3"; root.mkdir()
    outside=tmp_path / "outside"; outside.mkdir()
    (root / "envs").symlink_to(outside)
    ns["command"]=lambda *a,**kw:pytest.fail("must not execute conda")
    with pytest.raises(ValueError): ns["operate"]("plan","general-fixture")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("mode,value",[("verify",{"status":"verified"}),("inspect",{"status":"verified","application_deployed":False}),
    ("bootstrap",{"status":"bootstrapped","application_deployed":True})])
def test_cli_rejects_wrong_or_incomplete_mode_report(monkeypatch,capsys,mode,value):
    from contextlib import contextmanager
    monkeypatch.setattr(runtime,"load_access",lambda *a:{"hostname":"general-fixture"})
    monkeypatch.setattr(runtime,"ssh_argv",lambda *a,**kw:["ssh","general"])
    @contextmanager
    def auth(*args): yield {}
    monkeypatch.setattr(runtime,"password_session",auth)
    result=runtime.main([mode],run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=json.dumps(value)))
    assert result == 2
    assert "general_runtime_failed" in capsys.readouterr().out


@pytest.fixture
def fake_ssh(monkeypatch):
    from contextlib import contextmanager
    monkeypatch.setattr(runtime,"load_access",lambda *a:{"hostname":"general-fixture"})
    monkeypatch.setattr(runtime,"ssh_argv",lambda *a,**kw:["ssh","general"])
    @contextmanager
    def auth(*args): yield {}
    monkeypatch.setattr(runtime,"password_session",auth)
    def invoke(mode,value,raw=False):
        return runtime.main([mode],run=lambda *a,**kw:SimpleNamespace(returncode=0,stdout=value if raw else json.dumps(value)))
    return invoke


def sample_report(mode):
    if mode == "inspect": return dict(status="observed",runtime_present=True,environment_present=False,arch="x86_64",cpu_count=2)
    if mode == "plan": return dict(status="planned",packages={"python":"3.12.16","fastapi-core":"0.116.1","uvicorn":"0.35.0","httpx":"0.28.1"},unlink_count=0,application_deployed=False)
    if mode == "verify": return dict(status="verified",python="3.12.16",fastapi="0.116.1",uvicorn="0.35.0",httpx="0.28.1",sqlite="3.54.1",application_deployed=False)
    return dict(status={"bootstrap":"bootstrapped","install":"environment_created","manager-verify":"manager_verified"}[mode],application_deployed=False,**({"conda_version":"26.9.4"} if mode=="manager-verify" else {}))


@pytest.mark.parametrize("mode",["inspect","plan","verify","bootstrap","install","manager-verify"])
def test_exact_success_reports_are_accepted(fake_ssh,mode):
    assert fake_ssh(mode,sample_report(mode)) == 0


@pytest.mark.parametrize("mode,field,value",[("inspect","cpu_count",True),("inspect","cpu_count",-1),("inspect","cpu_count",float("nan")),
    ("inspect","cpu_count",65537),("inspect","arch","PRIVATE"),("inspect","runtime_present",1),
    ("verify","python","PRIVATE"),("verify","sqlite",None),("manager-verify","conda_version",[]),
    ("plan","packages",[]),("plan","packages",{}),("plan","packages",{"python":"PRIVATE"}),
    ("plan","unlink_count",1),("verify","python","3.11.9"),("verify","fastapi","0.1.0")])
def test_untrusted_fields_never_become_success(fake_ssh,capsys,mode,field,value):
    report=sample_report(mode); report[field]=value
    assert fake_ssh(mode,report) == 2
    assert "PRIVATE" not in capsys.readouterr().out


def test_duplicate_json_cannot_hide_contradictory_evidence(fake_ssh):
    assert fake_ssh("bootstrap",'{"status":"bootstrapped","application_deployed":true,"application_deployed":false}',True) == 2


@pytest.mark.parametrize("value",[None,[],{}, {"success":False},{"success":True,"actions":[]},
    {"success":True,"actions":{"LINK":[{"name":"python","version":[]}]}}])
def test_malformed_solver_results_rejected(value):
    with pytest.raises(ValueError): runtime.summarize_plan(value)


@pytest.mark.parametrize("variant",["os","arch","home_link","home_relative","root_link","bin_link","env_link","missing_conda"])
def test_remote_guard_fixture_never_runs_commands(tmp_path,monkeypatch,variant):
    ns={}; exec(runtime.REMOTE_SOURCE,ns)
    monkeypatch.setattr(ns["socket"],"gethostname",lambda:"fixture")
    monkeypatch.setattr(ns["platform"],"system",lambda:"Darwin" if variant=="os" else "Linux")
    monkeypatch.setattr(ns["platform"],"machine",lambda:"arm64" if variant=="arch" else "x86_64")
    home=tmp_path
    if variant == "home_relative": home=ns["Path"]("relative")
    if variant == "home_link": home=tmp_path / "home-link"; home.symlink_to(tmp_path)
    monkeypatch.setattr(ns["Path"],"home",lambda:home)
    if variant in {"root_link","bin_link","env_link"}:
        root=tmp_path / "miniforge3"
        if variant == "root_link": root.symlink_to(tmp_path)
        else:
            root.mkdir(); path=root / ("bin" if variant=="bin_link" else "envs/py312")
            path.parent.mkdir(exist_ok=True); path.symlink_to(tmp_path)
    ns["command"]=lambda *a,**kw:pytest.fail("guard allowed command")
    with pytest.raises(ValueError): ns["operate"]("plan","fixture")
