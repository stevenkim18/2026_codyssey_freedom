"""Local deployment packets: synthetic models only, no network or GPU."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest
from deployment import prepare
from deployment.tests.test_deployment import spec
from detector.tests.test_runtime import package


@pytest.fixture
def source(tmp_path):
    root=tmp_path / "source"; (root / "detector").mkdir(parents=True)
    (root / "detector/__main__.py").write_text("# synthetic\n")
    (root / "detector/.env").write_text("PRIVATE")
    return root


def test_plan_has_no_output_and_build_roundtrip_preserves_model(tmp_path,source):
    model,_=package(tmp_path)
    config=spec(tmp_path).as_dict(); output=tmp_path / "packet"
    result=prepare.plan(source,config,model=model)
    assert result["network_enabled"] is False and result["quality_approved"] is False
    assert not output.exists()
    manifest=prepare.build(source,config,output,model=model)
    assert prepare.verify(output) == manifest
    assert manifest["model"]["manifest_sha256"] == hashlib.sha256((model / "manifest.json").read_bytes()).hexdigest()
    assert output.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in output.iterdir())
    assert b"PRIVATE" not in (output / "application.tar").read_bytes()
    assert (model / "weights.bin").read_bytes() == b"synthetic-weights"


def test_missing_general_source_or_entrypoint_never_creates_output(tmp_path,source):
    config=spec(tmp_path).as_dict() | {"role":"general","module":"observability.app","device":"cpu"}
    output=tmp_path / "packet"
    with pytest.raises(Exception): prepare.build(source,config,output)
    assert not output.exists()
    (source / "observability").mkdir(); (source / "observability/util.py").write_text("# helper")
    with pytest.raises(Exception): prepare.build(source,config,output)
    assert not output.exists()


def test_existing_output_is_preserved(tmp_path,source):
    output=tmp_path / "packet"; output.mkdir(); (output / "sentinel").write_text("keep")
    with pytest.raises(Exception): prepare.build(source,spec(tmp_path).as_dict(),output)
    assert (output / "sentinel").read_text() == "keep"


@pytest.mark.parametrize("variant",["application_bytes","extra_file","manifest_extra","symlink","duplicate_json"])
def test_tampered_packet_rejected(tmp_path,source,variant):
    output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output)
    if variant == "application_bytes": (output / "application.tar").write_bytes(b"bad")
    elif variant == "extra_file": (output / "unknown").write_text("bad")
    elif variant == "manifest_extra":
        path=output / "release-manifest.json"; value=json.loads(path.read_text()); value["unknown"]=True; path.write_text(json.dumps(value))
    elif variant == "duplicate_json":
        path=output / "release-manifest.json"; path.write_text(path.read_text().replace('"role": "gpu"','"role": "general", "role": "gpu"'))
    else:
        path=output / "application.tar"; path.rename(output / "saved"); path.symlink_to(output / "saved")
    with pytest.raises(Exception): prepare.verify(output)


@pytest.mark.parametrize("name,kind",[("../escape",tarfile.REGTYPE),("detector/__main__.py",tarfile.SYMTYPE)])
def test_rehashed_hostile_application_archive_still_rejected(tmp_path,source,name,kind):
    output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output)
    with tarfile.open(output / "application.tar","w") as archive:
        entry=tarfile.TarInfo(name); entry.type=kind; entry.linkname="/outside"; archive.addfile(entry)
    path=output / "release-manifest.json"; value=json.loads(path.read_text())
    digest=hashlib.sha256((output / "application.tar").read_bytes()).hexdigest()
    value["files"]["application.tar"]=digest; value["application"]["sha256"]=digest
    value["application"]["bytes"]=(output / "application.tar").stat().st_size
    path.write_text(json.dumps(value))
    with pytest.raises(Exception): prepare.verify(output)


def rehash(output,name):
    path=output / "release-manifest.json"; value=json.loads(path.read_text())
    value["files"][name]=hashlib.sha256((output / name).read_bytes()).hexdigest()
    if name=="model-assets.tar": value["model"]["sha256"]=value["files"][name]
    path.write_text(json.dumps(value))


def test_model_archive_is_ustar_and_pax_metadata_is_rejected(tmp_path,source):
    model,_=package(tmp_path); output=tmp_path / "packet"
    prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert (output / "model-assets.tar").read_bytes()[257:265] == b"ustar\x0000"
    with tarfile.open(output / "model-assets.tar","w",format=tarfile.PAX_FORMAT) as archive:
        for item in sorted(model.iterdir()):
            entry=tarfile.TarInfo(item.name); entry.size=item.stat().st_size
            entry.pax_headers={"comment":"untrusted"}
            archive.addfile(entry,io.BytesIO(item.read_bytes()))
    rehash(output,"model-assets.tar")
    with pytest.raises(ValueError): prepare.verify(output)


def test_unrepresentable_model_name_fails_before_output(tmp_path,source):
    model,manifest=package(tmp_path)
    name="a"*110+".bin"
    (model / name).write_bytes(b"x"); manifest["files"][name]=hashlib.sha256(b"x").hexdigest()
    (model / "manifest.json").write_text(json.dumps(manifest))
    output=tmp_path / "packet"
    with pytest.raises(ValueError): prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert not output.exists()


def test_float_byte_count_is_not_integer_evidence(tmp_path,source):
    output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output)
    path=output / "release-manifest.json"; value=json.loads(path.read_text())
    value["application"]["bytes"]=float(value["application"]["bytes"])
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError): prepare.verify(output)


@pytest.mark.parametrize("value",['NaN','Infinity','-Infinity','1e999'])
def test_metadata_json_rejects_nonfinite_numbers(value):
    with pytest.raises(ValueError): prepare._json('{"unexpected":'+value+'}')


def test_output_inside_model_cannot_modify_verified_source(tmp_path,source):
    model,_=package(tmp_path); before={p.name:p.read_bytes() for p in model.iterdir()}
    with pytest.raises(ValueError): prepare.build(source,spec(tmp_path).as_dict(),model / "packet",model=model)
    assert {p.name:p.read_bytes() for p in model.iterdir()} == before


def test_partial_write_failure_is_preserved_without_false_manifest(tmp_path,source,monkeypatch):
    output=tmp_path / "packet"; write=prepare._write
    def fail(path,data):
        if path.name=="deploy.json": raise OSError("synthetic")
        write(path,data)
    monkeypatch.setattr(prepare,"_write",fail)
    with pytest.raises(OSError): prepare.build(source,spec(tmp_path).as_dict(),output)
    assert (output / "application.tar").exists()
    assert not (output / "release-manifest.json").exists()
    with pytest.raises(Exception): prepare.verify(output)


@pytest.mark.parametrize("variant",["traversal","duplicate","symlink","directory","trailing","truncated","missing","changed_bytes"])
def test_hostile_model_archive_rejected_even_when_outer_hash_updated(tmp_path,source,variant):
    model,_=package(tmp_path); output=tmp_path / "packet"
    prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    archive_path=output / "model-assets.tar"
    if variant in {"trailing","truncated"}:
        data=archive_path.read_bytes()
        archive_path.write_bytes(data+b"hidden" if variant=="trailing" else data[:512])
    else:
        with tarfile.open(archive_path,"w",format=tarfile.USTAR_FORMAT) as archive:
            for path in sorted(model.iterdir()):
                if variant=="missing" and path.name=="weights.bin": continue
                data=path.read_bytes(); entry=tarfile.TarInfo(path.name); entry.size=len(data)
                if path.name=="weights.bin":
                    if variant=="traversal": entry.name="../weights.bin"
                    elif variant=="symlink": entry.type=tarfile.SYMTYPE; entry.linkname="/outside"; entry.size=0
                    elif variant=="directory": entry.type=tarfile.DIRTYPE; entry.size=0
                    elif variant=="changed_bytes": data=b"x"*len(data)
                archive.addfile(entry,io.BytesIO(data))
                if variant=="duplicate" and path.name=="weights.bin": archive.addfile(entry,io.BytesIO(data))
    rehash(output,"model-assets.tar")
    with pytest.raises(ValueError): prepare.verify(output)


@pytest.mark.parametrize("variant",["model_secret","model_symlink","model_hardlink","model_changed","model_limit","output_symlink","source_overlap"])
def test_untrusted_inputs_fail_before_output_creation(tmp_path,source,monkeypatch,variant):
    import os
    model,manifest=package(tmp_path); output=tmp_path / "packet"
    if variant=="model_secret":
        path=model / "private.key"; path.write_bytes(b"PRIVATE"); manifest["files"][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        (model / "manifest.json").write_text(json.dumps(manifest))
    elif variant=="model_symlink": (model / "link").symlink_to(model / "weights.bin")
    elif variant=="model_hardlink": os.link(model / "weights.bin",tmp_path / "preserved-hardlink")
    elif variant=="model_changed": (model / "weights.bin").write_bytes(b"changed")
    elif variant=="model_limit": monkeypatch.setattr(prepare,"MAX_MODEL_BYTES",1024)
    elif variant=="output_symlink":
        parent=tmp_path / "alias"; parent.symlink_to(tmp_path); output=parent / "packet"
    else: output=source / "nested-packet"
    with pytest.raises(Exception): prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert not output.exists()


@pytest.mark.parametrize("variant",["role","files_hash","application_meta","spec_meta","apply_text","file_mode","root_mode","model_meta","model_manifest_hash"])
def test_descriptor_and_files_cross_checks(tmp_path,source,variant):
    model,_=package(tmp_path); output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    path=output / "release-manifest.json"; value=json.loads(path.read_text())
    if variant=="role": value["role"]="general"
    elif variant=="files_hash": value["files"]["application.tar"]="bad"
    elif variant=="application_meta": value["application"]["archive"]="outside.tar"
    elif variant=="spec_meta": value["spec"]["sha256"]="a"*64
    elif variant=="apply_text": (output / "APPLY.md").write_text("Execute PRIVATE"); rehash(output,"APPLY.md"); value=json.loads(path.read_text())
    elif variant=="file_mode": (output / "deploy.json").chmod(0o644)
    elif variant=="root_mode": output.chmod(0o755)
    elif variant=="model_meta": value["model"]["extra"]=True
    else: value["model"]["manifest_sha256"]="b"*64
    path.write_text(json.dumps(value))
    with pytest.raises(Exception): prepare.verify(output)


def test_cli_plan_build_verify_and_errors_are_local_sanitized(tmp_path,source,capsys,monkeypatch):
    import socket
    monkeypatch.setattr(socket,"create_connection",lambda *a,**kw:pytest.fail("no network"))
    config=tmp_path / "spec.json"; config.write_text(json.dumps(spec(tmp_path).as_dict()))
    output=tmp_path / "packet"
    args=["--source",str(source),"--spec",str(config),"--output",str(output)]
    assert prepare.main(["plan",*args])==0 and not output.exists()
    assert prepare.main(["build",*args])==0
    assert prepare.main(["verify","--output",str(output)])==0
    assert prepare.main(["build",*args])==2
    assert prepare.main(["verify"])==2 and prepare.main(["plan"])==2
    config.write_text("PRIVATE")
    assert prepare.main(["plan",*args])==2
    assert "PRIVATE" not in capsys.readouterr().out


def test_general_shared_implementation_can_be_prepared_without_inventing_service(tmp_path,source):
    (source / "observability").mkdir(); (source / "observability/app.py").write_text("# supplied synthetic C fixture")
    config=spec(tmp_path).as_dict() | {"role":"general","module":"observability.app","device":"cpu"}
    output=tmp_path / "packet"
    assert prepare.build(source,config,output)["role"] == "general"
    model,_=package(tmp_path)
    with pytest.raises(ValueError): prepare.plan(source,config,model=model)


def test_trusted_manifest_digest_detects_consistently_rehashed_spec_change(tmp_path,source):
    output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output)
    trusted=hashlib.sha256((output / "release-manifest.json").read_bytes()).hexdigest()
    config_path=output / "deploy.json"; config=json.loads(config_path.read_text())
    config["root"]="/operator/changed-release-root"; config_path.write_text(json.dumps(config))
    rehash(output,"deploy.json")
    manifest_path=output / "release-manifest.json"; value=json.loads(manifest_path.read_text())
    value["spec"]["sha256"]=value["files"]["deploy.json"]; manifest_path.write_text(json.dumps(value))
    assert prepare.verify(output)["spec"]["sha256"] == value["spec"]["sha256"]
    with pytest.raises(ValueError): prepare.verify(output,expected_manifest_sha256=trusted)


def test_oversize_spec_is_rejected_before_output(tmp_path,source):
    config=spec(tmp_path).as_dict(); config["health_json"]={"status":"x"*(1024*1024)}
    output=tmp_path / "packet"
    with pytest.raises(ValueError): prepare.build(source,config,output)
    assert not output.exists()


def test_build_preserves_source_bytes_mtime_and_verify_is_readonly(tmp_path,source):
    model,_=package(tmp_path); output=tmp_path / "packet"
    def snapshot(root):
        return {p.relative_to(root).as_posix():(p.stat().st_mtime_ns,p.stat().st_mode,p.read_bytes())
                for p in root.rglob("*") if p.is_file()}
    original=snapshot(model); app_original=snapshot(source)
    prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert snapshot(model)==original and snapshot(source)==app_original
    packet=snapshot(output); prepare.verify(output)
    assert snapshot(output)==packet


def test_fresh_cli_process_can_verify_local_packet(tmp_path,source):
    import subprocess
    import sys
    output=tmp_path / "packet"; prepare.build(source,spec(tmp_path).as_dict(),output)
    trusted=hashlib.sha256((output / "release-manifest.json").read_bytes()).hexdigest()
    result=subprocess.run([sys.executable,"-m","deployment.prepare","verify","--output",str(output),
                           "--expected-manifest-sha256",trusted],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and json.loads(result.stdout)["application_deployed"] is False


def test_cli_defaults_to_plan_without_creating_output(tmp_path,source):
    config=tmp_path / "spec.json"; config.write_text(json.dumps(spec(tmp_path).as_dict()))
    output=tmp_path / "packet"
    assert prepare.main(["--source",str(source),"--spec",str(config),"--output",str(output)])==0
    assert not output.exists()


def test_cli_spec_read_is_bounded_before_json_parse(tmp_path,source,monkeypatch):
    config=tmp_path / "spec.json"; data=json.dumps(spec(tmp_path).as_dict()).encode(); reads=[]
    original=Path.open
    class Reader(io.BytesIO):
        def read(self,size=-1): reads.append(size); return super().read(size)
    def opened(path,*args,**kwargs):
        return Reader(data) if path==config else original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",opened)
    assert prepare.main(["plan","--source",str(source),"--spec",str(config)])==0
    assert reads==[1024*1024+1]


def test_actual_builder_packet_stages_and_retries_in_isolated_local_fixture(tmp_path,source):
    from deployment import model_stage
    model,_=package(tmp_path); output=tmp_path / "packet"
    descriptor=prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    archive=output / "model-assets.tar"; metadata=output / "release-manifest.json"
    assert model_stage.plan(archive,metadata)["file_count"] == len(descriptor["model"]["files"])
    home=tmp_path / "fixture-home"; home.mkdir(mode=0o700); root=home / "models"
    identity={"system":lambda:"Linux","hostname":lambda:"synthetic-host","home":lambda:home}
    first=model_stage.apply(archive,metadata,root,"synthetic-host",**identity)
    target=Path(first["model_path"])
    before={p.relative_to(root).as_posix():(p.stat().st_mtime_ns,p.read_bytes()) for p in root.rglob("*") if p.is_file()}
    second=model_stage.apply(archive,metadata,root,"synthetic-host",**identity)
    assert first["status"]=="applied" and second["status"]=="already_present" and second["mutation"] is False
    assert {p.relative_to(root).as_posix():(p.stat().st_mtime_ns,p.read_bytes()) for p in root.rglob("*") if p.is_file()} == before
    assert (target / "weights.bin").read_bytes()==(model / "weights.bin").read_bytes()
    assert prepare.verify(output)==descriptor


def test_model_replaced_by_link_after_validation_preserves_partial_packet(tmp_path,source,monkeypatch):
    model,_=package(tmp_path); output=tmp_path / "packet"; write=prepare._write
    original=(model / "weights.bin").read_bytes()
    def replace_after_validation(path,data):
        write(path,data)
        if path.name=="APPLY.md":
            preserved=tmp_path / "preserved-weights.bin"
            (model / "weights.bin").rename(preserved)
            (model / "weights.bin").symlink_to(preserved)
    monkeypatch.setattr(prepare,"_write",replace_after_validation)
    with pytest.raises(ValueError,match="changed_model"):
        prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert (tmp_path / "preserved-weights.bin").read_bytes()==original
    assert (output / "application.tar").is_file()
    assert not (output / "release-manifest.json").exists()


@pytest.mark.parametrize("name",["private.key","secrets/data.bin","bad\\name.bin"])
def test_consistently_rehashed_sensitive_model_files_still_rejected(tmp_path,source,name):
    model,model_manifest=package(tmp_path); output=tmp_path / "packet"
    prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    content=b"synthetic-sensitive-material"; digest=hashlib.sha256(content).hexdigest()
    model_manifest["files"][name]=digest
    manifest_bytes=json.dumps(model_manifest).encode()
    with tarfile.open(output / "model-assets.tar","w",format=tarfile.USTAR_FORMAT) as archive:
        for path in sorted(model.iterdir()):
            data=manifest_bytes if path.name=="manifest.json" else path.read_bytes()
            entry=tarfile.TarInfo(path.name); entry.size=len(data); entry.mode=0o600
            archive.addfile(entry,io.BytesIO(data))
        entry=tarfile.TarInfo(name); entry.size=len(content); entry.mode=0o600
        archive.addfile(entry,io.BytesIO(content))
    rehash(output,"model-assets.tar")
    metadata=output / "release-manifest.json"; value=json.loads(metadata.read_text())
    value["model"]["files"][name]=digest
    value["model"]["manifest_sha256"]=hashlib.sha256(manifest_bytes).hexdigest()
    value["model"]["files"]["manifest.json"]=value["model"]["manifest_sha256"]
    metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="model_files"):
        prepare.verify(output)


@pytest.mark.parametrize("variant",["missing","regular_file","fifo"])
def test_missing_or_nonregular_model_source_never_creates_packet(tmp_path,source,variant):
    import os
    model=tmp_path / "model"; output=tmp_path / "packet"
    if variant=="regular_file": model.write_text("not a package")
    elif variant=="fifo": model.mkdir(); os.mkfifo(model / "weights.bin")
    with pytest.raises(ValueError): prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert not output.exists()


def test_archive_padding_counts_against_model_budget_before_output(tmp_path,source,monkeypatch):
    model,_=package(tmp_path); output=tmp_path / "packet"
    assert sum(path.stat().st_size for path in model.iterdir()) < 9000
    monkeypatch.setattr(prepare,"MAX_MODEL_BYTES",9000)
    with pytest.raises(ValueError,match="model_limit"):
        prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert not output.exists()


@pytest.mark.parametrize("variant",["extra_private_file","missing_descriptor_member","wrong_archive_digest"])
def test_packet_membership_and_model_descriptor_cannot_disagree(tmp_path,source,variant):
    model,_=package(tmp_path); output=tmp_path / "packet"
    prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    path=output / "release-manifest.json"; value=json.loads(path.read_text())
    if variant=="extra_private_file":
        extra=output / "unexpected"; extra.write_text("preserve"); extra.chmod(0o600)
    elif variant=="missing_descriptor_member": value["files"].pop("APPLY.md")
    else: value["model"]["sha256"]="a"*64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match="packet_membership|model_role"):
        prepare.verify(output)


def test_nested_model_asset_roundtrip_preserves_safe_relative_paths(tmp_path,source):
    model,manifest=package(tmp_path)
    nested=model / "tokenizer/vocab.json"; nested.parent.mkdir(); nested.write_bytes(b"{}")
    manifest["files"]["tokenizer/vocab.json"]=hashlib.sha256(b"{}").hexdigest()
    (model / "manifest.json").write_text(json.dumps(manifest))
    output=tmp_path / "packet"; value=prepare.build(source,spec(tmp_path).as_dict(),output,model=model)
    assert value["model"]["files"]["tokenizer/vocab.json"]==hashlib.sha256(b"{}").hexdigest()
    assert prepare.verify(output)==value


def test_oversize_operator_json_fails_before_parsing_or_output(tmp_path,source,capsys):
    config=tmp_path / "spec.json"; config.write_bytes(b" "*(1024*1024+1))
    output=tmp_path / "packet"
    assert prepare.main(["build","--source",str(source),"--spec",str(config),"--output",str(output)])==2
    assert not output.exists() and "local_prepare_failed" in capsys.readouterr().out
