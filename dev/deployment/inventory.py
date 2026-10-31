"""Fixed, read-only resource snapshot through pinned SSH; no installation or probes importing models."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess

from .askpass import password_session
from .core import load_access, ssh_argv

CONDA_CANDIDATES = ("home_anaconda3", "home_miniconda3", "home_miniforge3", "opt_conda")
REMOTE_SOURCE = r'''
import json, os, platform, re, socket, subprocess
from pathlib import Path

def text(path, limit=65536):
    with open(path) as handle: return handle.read(limit)
def disk(path):
    try:
        v=os.statvfs(path)
        return {"total":v.f_blocks*v.f_frsize,"available":v.f_bavail*v.f_frsize}
    except OSError: return {"total":None,"available":None}
def conda_candidates(home=None):
    home=Path.home() if home is None else Path(home)
    candidates=(("home_anaconda3",home/"anaconda3"),("home_miniconda3",home/"miniconda3"),
                ("home_miniforge3",home/"miniforge3"),("opt_conda",Path("/opt/conda")))
    result=[]
    for label,path in candidates:
        count=0; truncated=False
        present=path.is_dir() and not any(p.is_symlink() for p in [path,*path.parents])
        envs=path / "envs"
        if present and envs.is_dir() and not envs.is_symlink():
            with os.scandir(envs) as entries:
                for inspected,entry in enumerate(entries):
                    if inspected >= 100: truncated=True; break
                    if entry.is_dir(follow_symlinks=False): count+=1
        def python_exists(relative):
            target=path / relative
            return (present and not any(p.is_symlink() for p in target.parents if p != path.parent)
                    and target.resolve().is_relative_to(path) and target.is_file())
        result.append({"candidate":label,"present":present,"base_python":python_exists("bin/python"),
                       "py312_python":python_exists("envs/py312/bin/python"),"env_count":count,"truncated":truncated})
    return result
def collect(role, expected):
    if platform.system() != "Linux" or socket.gethostname().split(".")[0] != expected.split(".")[0]:
        raise ValueError()
    release=dict(line.split("=",1) for line in text("/etc/os-release").splitlines() if "=" in line)
    version=release.get("VERSION_ID","").strip('"') if release.get("ID","").strip('"') == "ubuntu" else None
    if version is not None and not re.fullmatch(r"[0-9]{2}\.[0-9]{2}",version): version=None
    memory={}
    for line in text("/proc/meminfo").splitlines():
        key,_,value=line.partition(":")
        if key in {"MemTotal","MemAvailable"}: memory[key]=int(value.split()[0])
    conda=conda_candidates()
    processes={"observed":0,"python":0,"conda":0,"nvidia":0,"truncated":False}
    inspected=0
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdigit(): continue
            if inspected >= 4096: processes["truncated"]=True; break
            inspected+=1
            try: name=text(Path(entry.path) / "comm",64).strip().lower()
            except OSError: continue
            processes["observed"]+=1
            for category in ("python","conda","nvidia"):
                if name.startswith(category): processes[category]+=1; break
    gpu={"status":"unavailable","devices":[]}
    try:
        result=subprocess.run(["/usr/bin/nvidia-smi","--query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu","--format=csv,noheader,nounits"],capture_output=True,text=True,timeout=5,check=False)
        if result.returncode == 0 and len(result.stdout) <= 8192:
            devices=[]
            for line in result.stdout.splitlines():
                if len(devices) >= 8: raise ValueError()
                name,driver,total,used,util=[part.strip() for part in line.split(",")]
                label=next((label for label in ("L4","T4","A10","A100","H100","H200") if re.search(r"\b"+label+r"\b",name)),"other")
                if not re.fullmatch(r"[0-9.]{1,24}",driver): raise ValueError()
                devices.append({"name":label,"driver":driver,"memory_total_mib":int(total),"memory_used_mib":int(used),"utilization_percent":int(util)})
            gpu={"status":"ok","devices":devices}
    except subprocess.TimeoutExpired: gpu={"status":"timeout","devices":[]}
    except Exception: pass
    return {"schema_version":"1.0","status":"observed","role":role,"os":"Linux","ubuntu_version":version,
            "python":platform.python_version(),"cpu_count":os.cpu_count(),"load":list(os.getloadavg()),
            "memory_kib":{"total":memory.get("MemTotal"),"available":memory.get("MemAvailable")},
            "disk_bytes":{"root":disk("/"),"home":disk(str(Path.home()))},"conda":conda,"gpu":gpu,
            "processes":processes,"scope":"read_only_snapshot_not_idle_proof"}
'''


def validate_report(value, role):
    def keys(item, expected):
        if not isinstance(item, dict) or set(item) != set(expected.split()): raise ValueError()
    def integer(item, maximum=2**63, nullable=False):
        if nullable and item is None: return
        if type(item) is not int or not 0 <= item <= maximum: raise ValueError()
    keys(value,"schema_version status role os ubuntu_version python cpu_count load memory_kib disk_bytes conda gpu processes scope")
    if (value["schema_version"] != "1.0" or value["status"] != "observed" or value["role"] != role
            or value["os"] != "Linux" or value["scope"] != "read_only_snapshot_not_idle_proof"): raise ValueError()
    if value["ubuntu_version"] is not None and not re.fullmatch(r"[0-9]{2}\.[0-9]{2}",value["ubuntu_version"]): raise ValueError()
    if not isinstance(value["python"],str) or not re.fullmatch(r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{1,3}",value["python"]): raise ValueError()
    integer(value["cpu_count"],65536,True)
    if not isinstance(value["load"],list) or len(value["load"]) != 3: raise ValueError()
    for number in value["load"]:
        if type(number) not in {int,float} or not math.isfinite(number) or not 0 <= number <= 1000000: raise ValueError()
    keys(value["disk_bytes"],"root home")
    for item in (value["memory_kib"],*value["disk_bytes"].values()):
        keys(item,"total available")
        for number in item.values(): integer(number,nullable=True)
        if all(v is not None for v in item.values()) and item["available"] > item["total"]: raise ValueError()
    if not isinstance(value["conda"],list) or len(value["conda"]) != len(CONDA_CANDIDATES): raise ValueError()
    for label,item in zip(CONDA_CANDIDATES,value["conda"]):
        keys(item,"candidate present base_python py312_python env_count truncated")
        if item["candidate"] != label: raise ValueError()
        for field in ("present","base_python","py312_python","truncated"):
            if type(item[field]) is not bool: raise ValueError()
        integer(item["env_count"],100)
    keys(value["gpu"],"status devices")
    gpu=value["gpu"]
    if gpu["status"] not in {"ok","unavailable","timeout"} or not isinstance(gpu["devices"],list) or len(gpu["devices"]) > 8: raise ValueError()
    if gpu["status"] != "ok" and gpu["devices"]: raise ValueError()
    for device in gpu["devices"]:
        keys(device,"name driver memory_total_mib memory_used_mib utilization_percent")
        if device["name"] not in {"L4","T4","A10","A100","H100","H200","other"} or not isinstance(device["driver"],str) or not re.fullmatch(r"[0-9.]{1,24}",device["driver"]): raise ValueError()
        integer(device["memory_total_mib"],2**30); integer(device["memory_used_mib"],device["memory_total_mib"])
        integer(device["utilization_percent"],100)
    keys(value["processes"],"observed python conda nvidia truncated")
    processes=value["processes"]
    integer(processes["observed"],4096)
    for field in ("python","conda","nvidia"): integer(processes[field],processes["observed"])
    if type(processes["truncated"]) is not bool: raise ValueError()
    return value


class SafeParser(argparse.ArgumentParser):
    def error(self,message): self.exit(2,"invalid_cli_arguments\n")


def main(argv=None, *, run=subprocess.run):
    parser=SafeParser(description=__doc__)
    parser.add_argument("--role",required=True,choices=("gpu","general"))
    parser.add_argument("--output",type=Path)
    args=parser.parse_args(argv)
    evidence=None
    try:
        if args.output:
            descriptor=os.open(args.output,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
            os.fchmod(descriptor,0o600)
            evidence=os.fdopen(descriptor,"w",encoding="utf-8")
        private=Path(__file__).resolve().parents[1] / ".private"
        access_file=private / "server-access.local.json"
        access=load_access(access_file,args.role)
        command=ssh_argv(access,private / "known_hosts","/usr/bin/python3",password=True)
        source=REMOTE_SOURCE+"\ntry:\n    print(json.dumps(collect("+repr(args.role)+","+repr(access["hostname"])+")))\nexcept Exception:\n    raise SystemExit(2)\n"
        with password_session(access_file,args.role) as environment:
            completed=run(command,input=source,text=True,capture_output=True,timeout=45,check=False,env=environment)
        if completed.returncode or len(completed.stdout) > 32768: raise ValueError()
        result=validate_report(json.loads(completed.stdout),args.role)
        output=json.dumps(result,sort_keys=True)
        if evidence: evidence.write(output+"\n")
        print(output)
        return 0
    except Exception:
        failure=json.dumps({"status":"error","code":"inventory_failed"})
        if evidence:
            try:
                evidence.seek(0)
                evidence.truncate()
                evidence.write(failure+"\n")
            except OSError:
                pass
        print(failure)
        return 2
    finally:
        if evidence: evidence.close()


if __name__ == "__main__":
    raise SystemExit(main())
