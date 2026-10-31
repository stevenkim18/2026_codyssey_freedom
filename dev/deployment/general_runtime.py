"""Fixed general-server rootless runtime phases. No application/service deployment."""
import argparse
import inspect
import json
from pathlib import Path
import re
import subprocess
from .askpass import password_session
from .core import load_access, ssh_argv


def summarize_plan(value):
    if not isinstance(value,dict) or value.get("success") is not True: raise ValueError()
    actions=value.get("actions",{})
    if not isinstance(actions,dict) or actions.get("UNLINK"): raise ValueError()
    selected={}
    for item in actions.get("LINK",[]):
        name,version=item.get("name"),item.get("version")
        if not isinstance(name,str) or not re.fullmatch(r"[a-z0-9_-]{1,80}",name): raise ValueError()
        if not isinstance(version,str) or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}",version): raise ValueError()
        if name in {"python","fastapi-core","uvicorn","httpx"}: selected[name]=version
    return selected


REMOTE_SOURCE = r'''
import hashlib, json, os, platform, re, socket, subprocess, sys, urllib.request
from pathlib import Path
URL="https://github.com/conda-forge/miniforge/releases/download/26.9.4-1/Miniforge3-26.9.4-1-Linux-x86_64.sh"
DIGEST="571b410272cc434f28bc6c5c49ca8ffd4a4d1bcba9f57f1b6baccdb46446cfcc"
SPECS=["python=3.12.16","fastapi-core=0.116.1","uvicorn=0.35.0","httpx=0.28.1"]
def command(args,timeout=180):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
    if result.returncode: raise ValueError()
    return result.stdout

def operate(mode,expected):
    if socket.gethostname().split(".")[0] != expected.split(".")[0]: raise ValueError()
    if platform.system() != "Linux" or platform.machine() != "x86_64": raise ValueError()
    home=Path.home()
    if not home.is_absolute() or any(path.is_symlink() for path in [home,*home.parents]) or home.stat().st_uid != os.getuid(): raise ValueError()
    root=home / "miniforge3"; env=root / "envs/py312"
    if any(path.is_symlink() for path in (root,root / "bin",root / "envs",env)): raise ValueError()
    if root.exists() and root.stat().st_uid != os.getuid(): raise ValueError()
    if mode == "inspect":
        return {"status":"observed","runtime_present":root.exists(),"environment_present":env.exists(),"arch":"x86_64","cpu_count":os.cpu_count()}
    if mode == "bootstrap":
        if root.exists(): raise ValueError()
        cache=home / ".cache"
        if cache.is_symlink(): raise ValueError()
        cache.mkdir(mode=0o700,exist_ok=True)
        installer=cache / "codyssey-miniforge-26.9.4-1.sh"
        if installer.is_symlink(): raise ValueError()
        if not installer.exists():
            with urllib.request.urlopen(URL,timeout=30) as response, installer.open("xb") as output:
                os.chmod(installer,0o600)
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total > 200*1024*1024: raise ValueError()
                    output.write(chunk)
        digest=hashlib.sha256()
        with installer.open("rb") as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
        if digest.hexdigest() != DIGEST: raise ValueError()
        command(["/bin/bash",str(installer),"-b","-p",str(root)],300)
        return {"status":"bootstrapped","application_deployed":False}
    conda=root / "bin/conda"
    if not conda.is_file(): raise ValueError()
    if mode == "manager-verify":
        info=json.loads(command([str(conda),"info","--json"]))
        if Path(info["root_prefix"]).resolve() != root.resolve() or info["platform"] != "linux-64": raise ValueError()
        return {"status":"manager_verified","conda_version":info["conda_version"],"application_deployed":False}
    if mode in {"plan","install"}:
        if env.exists(): raise ValueError()
        for spec in SPECS:
            command([str(conda),"search","--json","--override-channels","-c","conda-forge",spec])
        args=[str(conda),"create","--prefix",str(env),"--override-channels","-c","conda-forge",*SPECS,"--json","--yes"]
        plan=json.loads(command(args+["--dry-run"],300))
        selected=summarize_plan(plan)
        if set(selected) != {"python","fastapi-core","uvicorn","httpx"}: raise ValueError()
        if mode == "plan": return {"status":"planned","packages":selected,"unlink_count":0,"application_deployed":False}
        installed=json.loads(command(args,600))
        if installed.get("success") is not True: raise ValueError()
        return {"status":"environment_created","application_deployed":False}
    if mode == "verify":
        python=env / "bin/python"
        probe="import sys,json,sqlite3,importlib.metadata as m; from pathlib import Path; import fastapi,uvicorn,httpx; assert sys.version_info[:2]==(3,12); assert Path(sys.prefix).resolve()==Path(sys.argv[1]).resolve(); c=sqlite3.connect(':memory:'); assert c.execute('select 1').fetchone()==(1,); print(json.dumps({'status':'verified','python':'.'.join(map(str,sys.version_info[:3])),'fastapi':m.version('fastapi'),'uvicorn':m.version('uvicorn'),'httpx':m.version('httpx'),'sqlite':sqlite3.sqlite_version,'application_deployed':False}))"
        result=json.loads(command([str(python),"-I","-c",probe,str(env)],60))
        if result["python"] != "3.12.16" or result["fastapi"] != "0.116.1" or result["uvicorn"] != "0.35.0" or result["httpx"] != "0.28.1": raise ValueError()
        activated=json.loads(command([str(conda),"run","--prefix",str(env),"--no-capture-output","python","-I","-c",probe,str(env)],60))
        if activated != result: raise ValueError()
        command([str(python),"-m","pip","check"],60)
        packages=json.loads(command([str(conda),"list","--prefix",str(env),"--json"]))
        if any(item["name"] in {"torch","pytorch","triton"} or item["name"].startswith(("cuda","nvidia","libtorch")) for item in packages): raise ValueError()
        return result
    raise ValueError()
'''


def main(argv=None, *, run=subprocess.run):
    class Parser(argparse.ArgumentParser):
        def error(self,message): self.exit(2,"invalid_cli_arguments\n")
    parser=Parser(description=__doc__)
    parser.add_argument("mode",choices=("inspect","bootstrap","manager-verify","plan","install","verify"))
    args=parser.parse_args(argv)
    try:
        private=Path(__file__).resolve().parents[1] / ".private"
        access=load_access(private / "server-access.local.json","general")
        source=REMOTE_SOURCE+"\n"+inspect.getsource(summarize_plan)+"\ntry:\n print(json.dumps(operate("+repr(args.mode)+","+repr(access["hostname"])+")))\nexcept Exception:\n raise SystemExit(2)\n"
        command=ssh_argv(access,private / "known_hosts","/usr/bin/python3",password=True)
        with password_session(private / "server-access.local.json","general") as environment:
            result=run(command,input=source,capture_output=True,text=True,timeout=1200,check=False,env=environment)
        if result.returncode or len(result.stdout)>8192: raise ValueError()
        def unique(pairs):
            value={}
            for key,item in pairs:
                if key in value: raise ValueError()
                value[key]=item
            return value
        value=json.loads(result.stdout,object_pairs_hook=unique)
        schemas={
            "inspect":("observed", "status runtime_present environment_present arch cpu_count"),
            "bootstrap":("bootstrapped", "status application_deployed"),
            "manager-verify":("manager_verified", "status conda_version application_deployed"),
            "plan":("planned", "status packages unlink_count application_deployed"),
            "install":("environment_created", "status application_deployed"),
            "verify":("verified", "status python fastapi uvicorn httpx sqlite application_deployed")}
        status,fields=schemas[args.mode]
        if not isinstance(value,dict) or set(value)!=set(fields.split()) or value.get("status")!=status: raise ValueError()
        if "application_deployed" in value and value["application_deployed"] is not False: raise ValueError()
        for key,item in value.items():
            if key in {"python","fastapi","uvicorn","httpx","sqlite","conda_version"} and (not isinstance(item,str) or not re.fullmatch(r"[0-9.]{1,24}",item)): raise ValueError()
            if key in {"runtime_present","environment_present","application_deployed"} and type(item) is not bool: raise ValueError()
            if key in {"cpu_count","unlink_count"} and (type(item) is not int or not 0<=item<=65536): raise ValueError()
            if key == "arch" and item != "x86_64": raise ValueError()
            if key == "packages":
                if not isinstance(item,dict) or set(item)!={"python","fastapi-core","uvicorn","httpx"}: raise ValueError()
                if any(not isinstance(v,str) or not re.fullmatch(r"[0-9.]{1,24}",v) for v in item.values()): raise ValueError()
        expected={"python":"3.12.16","fastapi":"0.116.1","uvicorn":"0.35.0","httpx":"0.28.1"}
        if args.mode == "verify" and any(value[key]!=version for key,version in expected.items()): raise ValueError()
        if args.mode == "plan" and (value["unlink_count"] != 0 or value["packages"] != {"python":"3.12.16","fastapi-core":"0.116.1","uvicorn":"0.35.0","httpx":"0.28.1"}): raise ValueError()
        print(json.dumps(value,sort_keys=True)); return 0
    except Exception:
        print(json.dumps({"status":"error","code":"general_runtime_failed"})); return 2


if __name__ == "__main__": raise SystemExit(main())
