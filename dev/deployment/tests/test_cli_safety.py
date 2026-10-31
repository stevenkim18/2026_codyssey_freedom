"""R11/T10: local contracts, credentials, transport and machine-output boundary."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from deployment import __main__ as cli
from deployment.core import DeploymentError, Spec, bundle, load_access, ssh_argv
from deployment.tests.test_deployment import spec


SECRET="SYNTHETIC_SECRET"


@pytest.mark.parametrize("change", [
    {"unknown":SECRET}, {"role":"unknown"}, {"module":"other.server"}, {"root":"/"},
    {"root":"/tmp/../outside"}, {"python":"/tmp//python"}, {"env_file":"relative"},
    {"timeout":True},{"timeout":0},{"timeout":601},
    {"health_url":"http://name:secret@127.0.0.1:8766/healthz"},
    {"health_url":"http://127.0.0.1:8766/healthz?token="+SECRET},
    {"ready_url":"http://127.0.0.1:8766/readyz#private"},
    {"ready_url":"https://127.0.0.1:8766/readyz"},
    {"ready_url":"http://127.0.0.1/readyz"},
    {"ready_url":"http://127.0.0.1:999999/readyz"},
    {"ready_url":"http://localhost:8766/readyz"},
    {"ready_url":"http://127.0.0.1:8766/space%20path"},
    {"health_json":{}}, {"health_json":[]}, {"ready_json":{"status":[]}},
    {"ready_json":{1:"ready"}}, {"requirements":{}}, {"requirements":[]},
    {"requirements":{"package;touch":"1.0"}}, {"requirements":{"package":">=1.0"}},
    {"requirements":{"package":None}},
])
def test_contract_rejects_ambiguous_unsafe_or_unpinned_settings(tmp_path,change):
    with pytest.raises(DeploymentError) as caught:
        Spec.parse(spec(tmp_path).as_dict() | change)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("location",["same","descendant"])
def test_environment_must_stay_outside_versioned_release_root(tmp_path,location):
    value=spec(tmp_path).as_dict()
    value["env_file"]=value["root"]+("/secret.env" if location == "descendant" else "")
    with pytest.raises(DeploymentError): Spec.parse(value)


def test_general_contract_accepts_only_explicit_observability_service(tmp_path):
    value=spec(tmp_path).as_dict() | {"role":"general","module":"observability.server"}
    value.pop("device")
    assert Spec.parse(value).device == "cpu"
    for change in ({"module":"observability.bad;module"},{"device":"cuda"},{"module":"detector"}):
        with pytest.raises(DeploymentError): Spec.parse(value | change)


@pytest.fixture
def cli_private(tmp_path):
    directory=tmp_path / "private"; directory.mkdir(mode=0o700)
    path=directory / "server-access.local.json"
    server={"hostname":"gpu-host","private_ip":"10.0.0.2","username":"worker","ssh_port":22,"password":SECRET}
    path.write_text(json.dumps({"servers":{"gpu":server,"base":server | {"hostname":"base-host"}}})); path.chmod(0o600)
    return path


def test_private_access_selects_role_without_printing_credentials(cli_private,capsys):
    assert load_access(cli_private,"gpu")["hostname"] == "gpu-host"
    assert load_access(cli_private,"general")["hostname"] == "base-host"
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("variant",["file_mode","directory_mode","file_symlink","parent_symlink","malformed","missing_servers","bad_hostname"])
def test_private_access_refuses_unsafe_sources_and_sanitizes_errors(cli_private,tmp_path,variant,capsys):
    path=cli_private
    if variant == "file_mode": path.chmod(0o644)
    elif variant == "directory_mode": path.parent.chmod(0o755)
    elif variant == "file_symlink":
        path=cli_private.parent / "link"; path.symlink_to(cli_private)
    elif variant == "parent_symlink":
        directory=tmp_path / "link-dir"; directory.symlink_to(cli_private.parent); path=directory / cli_private.name
    elif variant == "malformed": path.write_text(SECRET)
    elif variant == "missing_servers": path.write_text(json.dumps({"private":SECRET}))
    else: path.write_text(json.dumps({"servers":{"gpu":{"hostname":"bad;"+SECRET}}}))
    with pytest.raises(DeploymentError) as caught: load_access(path,"gpu")
    assert SECRET not in str(caught.value) and SECRET not in capsys.readouterr().out
    assert cli_private.exists()


@pytest.fixture
def known_hosts(tmp_path):
    path=tmp_path / "known_hosts"; path.write_text("synthetic-host ssh-ed25519 synthetic-key\n"); path.chmod(0o600)
    return path


@pytest.mark.parametrize("change",[{"username":"worker;touch"},{"username":"-oProxyCommand"},{"private_ip":"host$(cmd)"},
    {"ssh_port":True},{"ssh_port":0},{"ssh_port":65536},{"ssh_port":"22"}])
def test_ssh_argument_injection_never_reaches_transport(known_hosts,change):
    access={"private_ip":"10.0.0.2","username":"worker","ssh_port":22,"password":SECRET} | change
    with pytest.raises(DeploymentError) as caught: ssh_argv(access,known_hosts,"/opt/py312/bin/python")
    assert SECRET not in str(caught.value)


def test_interactive_ssh_preserves_strict_trust_and_secret_free_argv(known_hosts):
    access={"private_ip":"10.0.0.2","username":"worker","ssh_port":22,"password":SECRET}
    argv=ssh_argv(access,known_hosts,"/opt/py312/bin/python",interactive=True)
    assert "BatchMode=no" in argv and "StrictHostKeyChecking=yes" in argv and SECRET not in repr(argv)
    with pytest.raises(DeploymentError): ssh_argv(access,known_hosts,"/opt/python",interactive=True,password=True)
    with pytest.raises(DeploymentError): ssh_argv(access,known_hosts,"/opt/python;echo")


@pytest.mark.parametrize("variant",["mode","symlink","directory"])
def test_known_hosts_must_be_a_private_regular_file(known_hosts,tmp_path,variant):
    path=known_hosts
    if variant == "mode": path.chmod(0o644)
    elif variant == "symlink":
        path=tmp_path / "link-hosts"; path.symlink_to(known_hosts)
    else: path=tmp_path
    with pytest.raises(DeploymentError): ssh_argv({},path,"/opt/python")


def test_real_plan_needs_neither_credentials_nor_network(tmp_path,monkeypatch,capsys):
    source=tmp_path / "source"; (source / "detector").mkdir(parents=True)
    (source / "detector/__main__.py").write_text("# harmless\n")
    config=tmp_path / "spec.json"; config.write_text(json.dumps(spec(tmp_path).as_dict()))
    def forbidden(*args,**kwargs): pytest.fail("plan must not use credentials or SSH")
    monkeypatch.setattr(cli,"load_access",forbidden)
    assert cli.main(["plan","--role","gpu","--source",str(source),"--spec",str(config)],run=forbidden) == 0
    result=json.loads(capsys.readouterr().out)
    assert result["status"] == "planned" and result["remote_mutation"] is False and len(result["sha256"]) == 64
    assert not Path(spec(tmp_path).root).exists()


@pytest.mark.parametrize("variant",["malformed_json","python_mismatch","python_injection","wrong_source"])
def test_cli_refuses_unsafe_local_inputs_before_reading_access(tmp_path,monkeypatch,capsys,variant):
    config=tmp_path / "spec.json"; config.write_text(json.dumps(spec(tmp_path).as_dict()))
    args=["plan","--role","gpu","--source",str(tmp_path / "absent"),"--spec",str(config)]
    if variant == "malformed_json": config.write_text(SECRET)
    if variant == "python_mismatch": args += ["--python","/different/python"]
    if variant == "python_injection": args=["preflight","--role","general","--python","/tmp/python;"+SECRET]
    def forbidden(*args,**kwargs): pytest.fail("unsafe input must not read credentials or invoke SSH")
    monkeypatch.setattr(cli,"load_access",forbidden)
    assert cli.main(args,run=forbidden) == 2
    assert SECRET not in capsys.readouterr().out


@pytest.fixture
def fake_transport(monkeypatch):
    monkeypatch.setattr(cli,"load_access",lambda *args:{"hostname":"synthetic","password":SECRET})
    monkeypatch.setattr(cli,"ssh_argv",lambda *args,**kwargs:["ssh","synthetic"])


@pytest.mark.parametrize("payload,returncode",[(SECRET,0),("[]",0),('{"status":"preflight_ok","extra":"'+SECRET+'"}',0),
    ('{"status":"preflight_ok"}',1),('{"status":"verified"}',0),('{"status":"error","code":"'+SECRET+'"}',2)])
def test_cli_remote_failure_and_arbitrary_output_are_sanitized(fake_transport,capsys,payload,returncode):
    def transport(*args,**kwargs):
        assert SECRET not in kwargs["input"]
        return SimpleNamespace(stdout=payload,stderr=SECRET,returncode=returncode)
    assert cli.main(["preflight","--role","general"],run=transport) == 2
    assert SECRET not in capsys.readouterr().out


def test_cli_transport_timeout_does_not_echo_secret_diagnostics(fake_transport,capsys):
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired(SECRET,1,output=SECRET,stderr=SECRET)
    assert cli.main(["preflight","--role","general"],run=timeout) == 2
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize("details",[None,{"cpu_count":True,"gpu_count":999999999,"python":SECRET,"application_python":[],"os":SECRET,"contract":1},
    {"cpu_count":2,"gpu_count":1,"python":"3.12.2","application_python":"3.12","os":"Linux","dependencies":"verified","contract":True,"cuda_available":False,"application_python_present":True,"extra":SECRET*10000}])
def test_cli_preflight_reports_only_typed_allowlisted_details(fake_transport,capsys,details):
    result={"status":"preflight_ok","details":details,"release":SECRET}
    assert cli.main(["preflight","--role","general","--python","/opt/py312/bin/python"],run=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(result))) == 0
    output=capsys.readouterr().out
    assert SECRET not in output and len(output) < 1000
    value=json.loads(output)
    if details and details.get("cpu_count") == 2: assert value["details"]["cpu_count"] == 2
    else: assert value["details"] == {}


def test_cli_cross_role_success_must_not_be_relabelled(fake_transport,capsys):
    result={"status":"preflight_ok","role":"gpu","details":{}}
    assert cli.main(["preflight","--role","general"],run=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(result))) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_verified_release_identifier_survives_safe_output(tmp_path,fake_transport,capsys):
    path=tmp_path / "spec.json"; path.write_text(json.dumps(spec(tmp_path).as_dict()))
    result={"status":"verified","role":"gpu","release":"a"*64}
    assert cli.main(["verify","--role","gpu","--spec",str(path)],run=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=json.dumps(result))) == 0
    assert json.loads(capsys.readouterr().out)["release"] == "a"*64


@pytest.mark.parametrize("variant",["root_link","file_link","oversize","too_many"])
def test_bundle_refuses_external_sources_and_resource_exhaustion(tmp_path,variant):
    source=tmp_path / "source"; source.mkdir(); outside=tmp_path / "outside"; outside.mkdir()
    sentinel=outside / "secret.py"; sentinel.write_text(SECRET)
    root=source / "detector"
    if variant == "root_link": root.symlink_to(outside)
    else:
        root.mkdir()
        if variant == "file_link": (root / "secret.py").symlink_to(sentinel)
        elif variant == "oversize": (root / "large.py").write_bytes(b"#"*(16*1024*1024+1))
        else:
            for i in range(1001): (root / f"file{i}.py").write_text("")
    with pytest.raises(DeploymentError): bundle(source,"gpu")
    assert sentinel.read_text() == SECRET
