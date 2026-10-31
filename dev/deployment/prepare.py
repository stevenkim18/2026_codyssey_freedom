"""Local-only plan/build/verify of deployment packets. Never connects or applies."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile

from .core import Spec, bundle
from .remote import validate_archive
from .model_stage import inspect_archive, ModelStageError
from detector.runtime import ModelPackage, _verify_package

MAX_MODEL_BYTES = 2 * 1024**3
APPLY = """# Prepared packet: not deployed

This packet contains no approval to access or change a server. Obtain explicit
user authorization before any future apply. Verify this packet immediately before
use and compare its manifest digest against your trusted preparation record.

Prerequisites: independently verified SSH host keys; operator-owned credentials
and environment file outside release trees; correct application Python environment;
validated TLS certificate/key/CA and listener configuration; owned service root;
healthy rollback target and a maintenance window coordinated with other owners.
Never place tokens, private keys, journal data or server-access files in this packet.

General service: use only the actually shared C implementation and approved wire
contract. Missing C code or contract blocks application deployment.
GPU service: model package integrity does not establish model quality or A approval.
Model quality approval is false/unconfirmed. GPU access remains separately gated.
No model is loaded, no GPU is used and no service is started during preparation.

The existing deployment tools require separate operator inputs and authorization.
This document intentionally contains no automatic apply or arbitrary SSH command.
"""


def _read_bounded(path,limit=1024*1024):
    with path.open("rb") as handle:
        data=handle.read(limit+1)
    if len(data)>limit: raise ValueError("input_size_limit")
    return data


def _json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError("duplicate_metadata")
            result[key]=value
        return result
    def finite(value):
        number=float(value)
        if not math.isfinite(number): raise ValueError("nonfinite_metadata")
        return number
    def constant(value): raise ValueError("nonfinite_metadata")
    return json.loads(data,object_pairs_hook=unique,parse_float=finite,parse_constant=constant)


def _bytes(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()


def _digest(data): return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()


def _safe_path(path):
    path=Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in [path,*path.parents]): raise ValueError("unsafe_path")
    return path


def _model_name(name):
    if not isinstance(name,str) or not name or "\\" in name or any(ord(c)<33 or ord(c)>126 for c in name): return False
    path=PurePosixPath(name)
    return ( path.as_posix()==name and not path.is_absolute()
            and ".." not in path.parts and not any(p.startswith(".") for p in path.parts)
            and path.suffix.lower() not in {".key",".pem",".crt",".env",".journal",".db",".sqlite",".sqlite3"}
            and not any(p.lower() in {"private","secrets","journal","server-access.local.json"} for p in path.parts))


def _entry(data,spec):
    members=validate_archive(data,_digest(data),spec.role)
    names={member.name for member in members}; entry=spec.module.replace(".","/")
    if not {entry+".py",entry+"/__main__.py"} & names: raise ValueError("service_entrypoint_missing")


def _inputs(source,spec,model):
    spec=Spec.parse(spec)
    if len(_bytes(spec.as_dict()))>1024*1024: raise ValueError("spec_limit")
    source=_safe_path(source)
    data,digest=bundle(source,spec.role)
    _entry(data,spec)
    model_root=None; model_files=None
    if model is not None:
        if spec.role != "gpu": raise ValueError("general_model_forbidden")
        supplied=_safe_path(model)
        if not supplied.is_dir(): raise ValueError("invalid_model")
        files=[]; total=0
        for index,path in enumerate(supplied.rglob("*")):
            if index>=2000 or path.is_symlink(): raise ValueError("model_limit")
            if path.is_dir(): continue
            if not path.is_file(): raise ValueError("unsafe_model_file")
            total+=path.stat().st_size
            if len(files)>=1000 or total>MAX_MODEL_BYTES: raise ValueError("model_limit")
            if path.stat().st_nlink!=1 or not _model_name(path.relative_to(supplied).as_posix()): raise ValueError("unsafe_model_file")
            files.append(path)
        archive_bytes=1024
        for path in files:
            member=tarfile.TarInfo(path.relative_to(supplied).as_posix()); member.size=path.stat().st_size
            member.tobuf(format=tarfile.USTAR_FORMAT)
            archive_bytes+=512+512*((member.size+511)//512)
        if 10240*((archive_bytes+10239)//10240)>MAX_MODEL_BYTES: raise ValueError("model_limit")
        model_root,manifest=_verify_package(supplied)
        model_files={"manifest.json":_file_hash(model_root / "manifest.json"),**manifest["files"]}
    return spec,data,digest,model_root,model_files


def plan(source,spec,*,model=None):
    spec,data,digest,root,files=_inputs(source,spec,model)
    return {"status":"planned","role":spec.role,"application_sha256":digest,"application_bytes":len(data),
            "model_file_count":len(files) if files else 0,"network_enabled":False,
            "quality_approved":False,"application_deployed":False}


def _write(path,data):
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(),0o600); handle.write(data)


def build(source,spec,output,*,model=None):
    spec,data,digest,model_root,model_files=_inputs(source,spec,model)
    output=_safe_path(output)
    if output.is_relative_to(_safe_path(source)) or (model_root is not None and output.is_relative_to(model_root)):
        raise ValueError("output_inside_source")
    # Every input is verified before an output directory is created. Preserve any
    # new partial packet after a later failure; never clean or overwrite evidence.
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    os.chmod(output,0o700)
    _write(output / "application.tar",data)
    spec_data=_bytes(spec.as_dict()); _write(output / "deploy.json",spec_data)
    _write(output / "APPLY.md",APPLY.encode())
    metadata=None
    if model_root is not None:
        archive_path=output / "model-assets.tar"
        with archive_path.open("xb") as handle:
            os.fchmod(handle.fileno(),0o600)
            with tarfile.open(fileobj=handle,mode="w",format=tarfile.USTAR_FORMAT) as archive:
                for name in sorted(model_files):
                    path=model_root / name
                    if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1: raise ValueError("changed_model")
                    member=tarfile.TarInfo(name); member.mode=0o600; member.size=path.stat().st_size
                    with path.open("rb") as content: archive.addfile(member,content)
            if handle.tell()>MAX_MODEL_BYTES: raise ValueError("model_limit")
        metadata={"archive":"model-assets.tar","sha256":_file_hash(archive_path),
                  "manifest_sha256":model_files["manifest.json"],"files":model_files}
    files={path.name:_file_hash(path) for path in output.iterdir()}
    manifest={"schema_version":"1.0","role":spec.role,
              "application":{"archive":"application.tar","sha256":digest,"bytes":len(data)},
              "spec":{"file":"deploy.json","sha256":_digest(spec_data)},"model":metadata,"files":files}
    _write(output / "release-manifest.json",_bytes(manifest))
    return verify(output)


def _model_archive(path,metadata):
    if not isinstance(metadata,dict) or set(metadata)!={"archive","sha256","manifest_sha256","files"}: raise ValueError("model_metadata")
    if metadata["archive"]!="model-assets.tar" or path.stat().st_size>MAX_MODEL_BYTES: raise ValueError("model_limit")
    expected=metadata["files"]
    if not isinstance(expected,dict) or not 1<=len(expected)<=1000 or not all(_model_name(n) for n in expected): raise ValueError("model_files")
    try:
        # Shared physical-header/hash parser is also used by the future stager.
        # Preparation additionally validates B's full package semantic contract.
        ModelPackage.model_validate(inspect_archive(path,metadata))
    except ModelStageError:
        raise ValueError("model_integrity") from None


def verify(output,*,expected_manifest_sha256=None):
    output=_safe_path(output)
    if not output.is_dir() or output.stat().st_uid!=os.getuid() or stat.S_IMODE(output.stat().st_mode)!=0o700: raise ValueError("packet_directory")
    paths=list(output.iterdir())
    if any(p.is_symlink() or not p.is_file() or p.stat().st_nlink!=1 or p.stat().st_uid!=os.getuid() or stat.S_IMODE(p.stat().st_mode)!=0o600 for p in paths): raise ValueError("packet_files")
    manifest_path=output / "release-manifest.json"
    if manifest_path.stat().st_size>1024*1024: raise ValueError("manifest_limit")
    manifest_data=_read_bounded(manifest_path)
    if expected_manifest_sha256 is not None:
        if not isinstance(expected_manifest_sha256,str) or not re.fullmatch(r"[a-f0-9]{64}",expected_manifest_sha256) or _digest(manifest_data)!=expected_manifest_sha256:
            raise ValueError("trusted_manifest_mismatch")
    manifest=_json(manifest_data)
    if not isinstance(manifest,dict) or set(manifest)!={"schema_version","role","application","spec","model","files"} or manifest["schema_version"]!="1.0": raise ValueError("packet_metadata")
    expected={"application.tar","deploy.json","APPLY.md"} | ({"model-assets.tar"} if manifest["model"] is not None else set())
    if not isinstance(manifest["files"],dict) or set(manifest["files"])!=expected or {p.name for p in paths}!=expected|{"release-manifest.json"}: raise ValueError("packet_membership")
    for name,expected_hash in manifest["files"].items():
        if not isinstance(expected_hash,str) or not re.fullmatch(r"[a-f0-9]{64}",expected_hash): raise ValueError("packet_hash")
        limit=MAX_MODEL_BYTES if name=="model-assets.tar" else 20*1024*1024 if name=="application.tar" else 1024*1024
        if (output / name).stat().st_size>limit or _file_hash(output / name)!=expected_hash: raise ValueError("packet_integrity")
    spec_data=_read_bounded(output / "deploy.json"); spec=Spec.parse(_json(spec_data))
    if spec.role!=manifest["role"] or _json(spec_data)!=spec.as_dict(): raise ValueError("packet_role")
    data=_read_bounded(output / "application.tar",20*1024*1024); _entry(data,spec)
    if not isinstance(manifest["application"],dict) or type(manifest["application"].get("bytes")) is not int: raise ValueError("application_metadata")
    if manifest["application"]!={"archive":"application.tar","sha256":_digest(data),"bytes":len(data)}: raise ValueError("application_metadata")
    if manifest["spec"]!={"file":"deploy.json","sha256":_digest(spec_data)}: raise ValueError("spec_metadata")
    if _read_bounded(output / "APPLY.md")!=APPLY.encode(): raise ValueError("apply_instructions")
    if manifest["model"] is not None:
        if spec.role!="gpu" or manifest["model"].get("sha256")!=manifest["files"]["model-assets.tar"]: raise ValueError("model_role")
        _model_archive(output / "model-assets.tar",manifest["model"])
    return manifest


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self,message): self.exit(2,"invalid_prepare_arguments\n")
    parser=Parser(description=__doc__)
    parser.add_argument("mode",nargs="?",default="plan",choices=("plan","build","verify"))
    parser.add_argument("--source",type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument("--spec",type=Path)
    parser.add_argument("--model",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--expected-manifest-sha256")
    args=parser.parse_args(argv)
    try:
        if args.mode=="verify":
            if args.output is None: raise ValueError()
            value=verify(args.output,expected_manifest_sha256=args.expected_manifest_sha256); result={"status":"verified","role":value["role"],"application_deployed":False}
        else:
            if args.expected_manifest_sha256 is not None: raise ValueError()
            if args.spec is None: raise ValueError()
            spec=_json(_read_bounded(args.spec))
            if args.mode=="plan": result=plan(args.source,spec,model=args.model)
            else:
                if args.output is None: raise ValueError()
                value=build(args.source,spec,args.output,model=args.model)
                result={"status":"built","role":value["role"],"manifest_sha256":_file_hash(args.output / "release-manifest.json"),"application_deployed":False}
        print(json.dumps(result,sort_keys=True)); return 0
    except Exception:
        print(json.dumps({"status":"error","code":"local_prepare_failed"})); return 2


if __name__=="__main__": raise SystemExit(main())
