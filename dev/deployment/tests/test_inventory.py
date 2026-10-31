"""R11: fixed read-only server inventory, synthetic transport only."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deployment import inventory


def report():
    return {"schema_version":"1.0","status":"observed","role":"gpu","os":"Linux","ubuntu_version":"24.04",
            "python":"3.12.3","cpu_count":4,"load":[0.0,1.0,2.0],"memory_kib":{"total":8192,"available":4096},
            "disk_bytes":{"root":{"total":10000,"available":5000},"home":{"total":10000,"available":5000}},
            "conda":[{"candidate":label,"present":False,"base_python":False,"py312_python":False,"env_count":0,"truncated":False} for label in inventory.CONDA_CANDIDATES],
            "gpu":{"status":"unavailable","devices":[]},
            "processes":{"observed":20,"python":1,"conda":0,"nvidia":0,"truncated":False},"scope":"read_only_snapshot_not_idle_proof"}


@pytest.mark.parametrize("variant",["extra","role","secret_os","negative","bool_cpu","nan","gpu_secret","conda_escape","process_extra"])
def test_untrusted_response_rejected(variant):
    value=report()
    if variant == "extra": value["secret"]="PRIVATE"
    elif variant == "role": value["role"]="general"
    elif variant == "secret_os": value["os"]="PRIVATE"
    elif variant == "negative": value["memory_kib"]["total"]=-1
    elif variant == "bool_cpu": value["cpu_count"]=True
    elif variant == "nan": value["load"][0]=float("nan")
    elif variant == "gpu_secret": value["gpu"]={"status":"ok","devices":[{"name":"PRIVATE","driver":"555.1","memory_total_mib":1,"memory_used_mib":0,"utilization_percent":0}]}
    elif variant == "conda_escape": value["conda"][0]["candidate"]="/home/other/secret"
    else: value["processes"]["argv"]="PRIVATE"
    with pytest.raises(ValueError): inventory.validate_report(value,"gpu")


def test_known_report_accepted():
    assert inventory.validate_report(report(),"gpu") == report()


@pytest.fixture
def transport(monkeypatch):
    seen={}
    monkeypatch.setattr(inventory,"load_access",lambda path,role:{"hostname":"gpu-host","private_ip":"10.0.0.1","username":"ncloud","ssh_port":22,"password":"PRIVATE"})
    def ssh(access,known,python,**options):
        seen["ssh"]=(python,options)
        return ["ssh","fixed"]
    monkeypatch.setattr(inventory,"ssh_argv",ssh)
    @contextmanager
    def password(*args):
        seen["auth"]=True
        yield {"AUTH":"one-use"}
    monkeypatch.setattr(inventory,"password_session",password)
    return seen


def test_cli_fixed_transport_and_private_evidence(tmp_path,transport,capsys):
    path=tmp_path / "evidence.json"
    def run(args,**kw):
        assert args == ["ssh","fixed"] and kw["timeout"] == 45
        assert kw["env"] == {"AUTH":"one-use"} and "PRIVATE" not in kw["input"]
        assert "gpu-host" in kw["input"]
        return SimpleNamespace(returncode=0,stdout=json.dumps(report()),stderr="PRIVATE")
    assert inventory.main(["--role","gpu","--output",str(path)],run=run) == 0
    assert path.stat().st_mode & 0o777 == 0o600
    assert transport["ssh"] == ("/usr/bin/python3",{"password":True})
    assert "PRIVATE" not in capsys.readouterr().out
    before=path.read_bytes()
    assert inventory.main(["--role","gpu","--output",str(path)],run=lambda *a,**kw:pytest.fail("existing evidence must block SSH")) == 2
    assert path.read_bytes() == before


def test_remote_failure_is_sanitized(transport,capsys):
    def fail(*args,**kwargs): raise RuntimeError("PRIVATE")
    assert inventory.main(["--role","gpu"],run=fail) == 2
    assert "PRIVATE" not in capsys.readouterr().out


def test_remote_source_is_fixed_and_has_no_package_import_or_process_arguments():
    source=inventory.REMOTE_SOURCE
    assert "import torch" not in source and "cmdline" not in source and "environ" not in source
    assert 'timeout=5' in source and '/usr/bin/nvidia-smi' in source
    assert 'comm' in source and 'gethostname' in source


@pytest.mark.parametrize("gpu_status",["ok","timeout","malformed","missing"])
def test_fixed_remote_collector_with_synthetic_resources(monkeypatch,gpu_status):
    import subprocess
    namespace={}; exec(inventory.REMOTE_SOURCE,namespace)
    monkeypatch.setattr(namespace["platform"],"system",lambda:"Linux")
    monkeypatch.setattr(namespace["socket"],"gethostname",lambda:"gpu-host")
    monkeypatch.setattr(namespace["os"],"getloadavg",lambda:(0.0,0.1,0.2))
    class Entries:
        def __enter__(self): return iter([SimpleNamespace(name="123",path="/proc/123")])
        def __exit__(self,*args): pass
    monkeypatch.setattr(namespace["os"],"scandir",lambda path:Entries())
    def text(path,limit=65536):
        if str(path) == "/etc/os-release": return 'ID=ubuntu\nVERSION_ID="24.04"\n'
        if str(path) == "/proc/meminfo": return 'MemTotal: 8192 kB\nMemAvailable: 4096 kB\n'
        return "python3\n"
    namespace["text"]=text
    def run(args,**kwargs):
        assert args[0] == "/usr/bin/nvidia-smi" and kwargs["timeout"] == 5
        if gpu_status == "timeout": raise subprocess.TimeoutExpired(args,5)
        if gpu_status == "missing": raise FileNotFoundError()
        return SimpleNamespace(returncode=0,stdout="NVIDIA L4, 550.54.15, 23034, 0, 0\n" if gpu_status == "ok" else "PRIVATE")
    monkeypatch.setattr(namespace["subprocess"],"run",run)
    result=namespace["collect"]("gpu","gpu-host")
    assert inventory.validate_report(result,"gpu")["processes"]["python"] == 1
    assert result["gpu"]["status"] == (gpu_status if gpu_status in {"ok","timeout"} else "unavailable")
    assert "PRIVATE" not in json.dumps(result)
    if gpu_status == "ok": assert result["gpu"]["devices"][0]["name"] == "L4"


def test_remote_hostname_gate_precedes_filesystem_reads(monkeypatch):
    namespace={}; exec(inventory.REMOTE_SOURCE,namespace)
    monkeypatch.setattr(namespace["platform"],"system",lambda:"Linux")
    monkeypatch.setattr(namespace["socket"],"gethostname",lambda:"wrong-host")
    namespace["text"]=lambda *a:pytest.fail("host gate must precede read")
    with pytest.raises(ValueError): namespace["collect"]("gpu","gpu-host")


CANDIDATE_LABELS = ("home_anaconda3", "home_miniconda3", "home_miniforge3", "opt_conda")


def test_remote_conda_candidates_use_actual_home_without_disclosing_paths(tmp_path):
    namespace = {}; exec(inventory.REMOTE_SOURCE, namespace)
    home = tmp_path / "home1/ncloud"
    root = home / "miniforge3"
    (root / "bin").mkdir(parents=True)
    (root / "bin/python3.12").write_bytes(b"synthetic")
    (root / "bin/python").symlink_to("python3.12")
    (root / "envs/py312/bin").mkdir(parents=True)
    (root / "envs/py312/bin/python").write_bytes(b"synthetic")
    result = namespace["conda_candidates"](home=home)
    assert tuple(item["candidate"] for item in result) == CANDIDATE_LABELS
    found = result[2]
    assert found == {"candidate":"home_miniforge3", "present":True, "base_python":True,
                     "py312_python":True, "env_count":1, "truncated":False}
    assert str(home) not in json.dumps(result) and all("root" not in item for item in result)


def test_remote_conda_candidates_reject_symlink_roots_and_python_escape(tmp_path):
    namespace = {}; exec(inventory.REMOTE_SOURCE, namespace)
    home = tmp_path / "home1/ncloud"; home.mkdir(parents=True)
    outside = tmp_path / "outside"; (outside / "bin").mkdir(parents=True)
    (outside / "bin/python").write_bytes(b"synthetic")
    (home / "anaconda3").symlink_to(outside, target_is_directory=True)
    root = home / "miniconda3"; (root / "bin").mkdir(parents=True)
    (root / "bin/python").symlink_to(outside / "bin/python")
    (root / "envs").mkdir()
    (root / "envs/py312").symlink_to(outside, target_is_directory=True)
    result = namespace["conda_candidates"](home=home)
    assert result[0]["present"] is False and result[0]["base_python"] is False
    assert result[1]["present"] is True
    assert result[1]["base_python"] is False and result[1]["py312_python"] is False
    assert result[1]["env_count"] == 0
    linked_home = tmp_path / "linked-home"; linked_home.symlink_to(home, target_is_directory=True)
    linked = namespace["conda_candidates"](home=linked_home)
    assert all(item["present"] is False for item in linked[:3])


def test_public_candidate_schema_is_exact_and_ordered():
    from copy import deepcopy
    value = report()
    value["conda"] = [{"candidate":label,"present":False,"base_python":False,
                       "py312_python":False,"env_count":0,"truncated":False} for label in CANDIDATE_LABELS]
    assert inventory.validate_report(value,"gpu") == value
    for variant in ("order", "label", "path", "missing"):
        invalid = deepcopy(value)
        if variant == "order": invalid["conda"].reverse()
        elif variant == "label": invalid["conda"][0]["candidate"] = "PRIVATE"
        elif variant == "path": invalid["conda"][0]["root"] = "/home1/PRIVATE"
        else: invalid["conda"].pop()
        with pytest.raises(ValueError): inventory.validate_report(invalid,"gpu")


@pytest.mark.parametrize("failure", ["invalid_json", "exception", "oversized", "timeout", "exit_status"])
def test_failure_evidence_records_fixed_error_and_preserves_existing(tmp_path, transport, capsys, failure):
    path = tmp_path / "failure.json"
    def run(*args, **kwargs):
        if failure == "exception": raise RuntimeError("PRIVATE")
        if failure == "timeout":
            import subprocess
            raise subprocess.TimeoutExpired("PRIVATE",45,output="PRIVATE",stderr="PRIVATE")
        if failure == "oversized": return SimpleNamespace(returncode=0,stdout="PRIVATE"*5000,stderr="PRIVATE")
        if failure == "exit_status": return SimpleNamespace(returncode=2,stdout="PRIVATE",stderr="PRIVATE")
        return SimpleNamespace(returncode=0, stdout="PRIVATE invalid JSON", stderr="PRIVATE")
    assert inventory.main(["--role","gpu","--output",str(path)],run=run) == 2
    expected = {"status":"error","code":"inventory_failed"}
    assert json.loads(path.read_text()) == expected
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(capsys.readouterr().out) == expected
    before = path.read_bytes()
    assert inventory.main(["--role","gpu","--output",str(path)],run=lambda *a,**kw:pytest.fail("no overwrite")) == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("path,invalid", [
    (("conda",0,"present"),1),
    (("conda",0,"base_python"),"PRIVATE"),
    (("conda",0,"py312_python"),None),
    (("conda",0,"truncated"),0),
    (("conda",0,"env_count"),101),
    (("conda",0,"env_count"),-1),
    (("conda",0,"env_count"),True),
    (("gpu","status"),"PRIVATE"),
    (("gpu","status"),"unavailable"),
    (("gpu","devices"),"PRIVATE"),
    (("gpu","devices",0,"driver"),"PRIVATE"),
    (("gpu","devices",0,"driver"),123),
    (("gpu","devices",0,"driver"),"1"*25),
    (("gpu","devices",0,"utilization_percent"),101),
    (("gpu","devices",0,"utilization_percent"),True),
    (("gpu","devices",0,"memory_total_mib"),2**30+1),
    (("gpu","devices",0,"memory_used_mib"),23035),
    (("processes","observed"),4097),
    (("processes","python"),21),
    (("processes","conda"),-1),
    (("processes","nvidia"),True),
    (("processes","truncated"),1),
    (("load",),[0,1]),
    (("load",),"PRIVATE"),
    (("load",0),float("inf")),
    (("load",0),float("nan")),
    (("load",0),True),
    (("load",0),"PRIVATE"),
    (("load",0),-0.1),
    (("load",0),1000001),
    (("disk_bytes","root","available"),10001),
    (("disk_bytes","home","total"),True),
    (("memory_kib","total"),2**63+1),
    (("ubuntu_version",),"PRIVATE"),
    (("ubuntu_version",),24.04),
    (("python",),"PRIVATE"),
    (("python",),312),
    (("cpu_count",),65537),
])
def test_metadata_boundary_rejections_are_sanitized_evidence(tmp_path,transport,capsys,path,invalid):
    value=report()
    value["gpu"]={"status":"ok","devices":[{"name":"L4","driver":"580.159.04",
        "memory_total_mib":23034,"memory_used_mib":0,"utilization_percent":0}]}
    target=value
    for key in path[:-1]: target=target[key]
    target[path[-1]]=invalid
    evidence=tmp_path / "rejected.json"
    def run(*args,**kwargs):
        return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr="PRIVATE")
    assert inventory.main(["--role","gpu","--output",str(evidence)],run=run)==2
    expected={"status":"error","code":"inventory_failed"}
    assert json.loads(capsys.readouterr().out)==expected
    assert json.loads(evidence.read_text())==expected
    assert evidence.stat().st_mode & 0o777 == 0o600


def test_gpu_device_limit_accepts_eight_and_rejects_nine():
    from copy import deepcopy
    value=report()
    device={"name":"L4","driver":"580.159.04","memory_total_mib":23034,
            "memory_used_mib":23034,"utilization_percent":100}
    value["gpu"]={"status":"ok","devices":[deepcopy(device) for _ in range(8)]}
    assert inventory.validate_report(value,"gpu")==value
    value["gpu"]["devices"].append(deepcopy(device))
    with pytest.raises(ValueError): inventory.validate_report(value,"gpu")


def test_unknown_resources_and_bounded_counts_are_valid_not_guessed():
    value=report()
    value.update({"cpu_count":None,"ubuntu_version":None,"load":[0,1000000,0.5]})
    value["memory_kib"]={"total":None,"available":None}
    value["disk_bytes"]["home"]={"total":None,"available":None}
    value["conda"][0].update({"present":True,"env_count":100,"truncated":True})
    value["processes"].update({"observed":4096,"python":4096,"truncated":True})
    assert inventory.validate_report(value,"gpu")==value
