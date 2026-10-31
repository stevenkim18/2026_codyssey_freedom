"""R07/R11: offline M0 packaging with tiny synthetic source assets."""
import hashlib
import json
from pathlib import Path

import pytest

from detector.prepare_m0 import AssetSpec, PackagePreparationError, build_package, PINNED_ASSETS, BACKBONE_REVISION
from detector.runtime import _verify_package


@pytest.fixture
def sources(tmp_path):
    m0, backbone = tmp_path / "m0", tmp_path / "backbone"
    m0.mkdir()
    backbone.mkdir()
    content = {
        ("m0", "pytorch_model.bin"): b"SYNTHETIC-WEIGHTS",
        ("m0", "gliner_config.json"): json.dumps({"model_name": "microsoft/mdeberta-v3-base", "max_len": 384, "max_width": 12, "span_mode": "markerV0"}).encode(),
        ("m0", "README.md"): b"---\nlicense: apache-2.0\n---\nSynthetic model card",
        ("backbone", "config.json"): b'{"model_type":"deberta-v2","max_position_embeddings":512,"vocab_size":250000}',
        ("backbone", "tokenizer_config.json"): b'{"do_lower_case":false}',
        ("backbone", "spm.model"): b"SYNTHETIC-TOKENIZER",
        ("backbone", "README.md"): b"---\nlicense: mit\n---\nSynthetic backbone card",
    }
    specs = []
    for (source, name), data in content.items():
        directory = m0 if source == "m0" else backbone
        (directory / name).write_bytes(data)
        specs.append(AssetSpec(source, name, len(data), "sha256", hashlib.sha256(data).hexdigest()))
    license_file = tmp_path / "LICENSE"
    license_file.write_text("Apache License Version 2.0 — synthetic fixture")
    return m0, backbone, license_file, tuple(specs)


def test_t07_pinned_download_set_is_bounded_and_contains_no_backbone_weights():
    assert sum(asset.size for asset in PINNED_ASSETS) < 5 * 1024 ** 3
    assert not any(asset.source == "backbone" and asset.name.endswith(".bin") for asset in PINNED_ASSETS)
    assert len(BACKBONE_REVISION) == 40


def test_t07_builder_creates_self_contained_hashed_engineering_package(sources, tmp_path):
    m0, backbone, license_file, specs = sources
    output = tmp_path / "package"
    manifest = build_package(m0, backbone, output, license_file, assets=specs)
    _, verified = _verify_package(output)
    assert manifest == verified
    config = json.loads((output / "gliner_config.json").read_text())
    assert config["model_name"] == "microsoft/mdeberta-v3-base"
    assert config["encoder_config"]["model_type"] == "deberta-v2"
    assert config["span_mode"] == "markerV0"
    tokenizer = json.loads((output / "tokenizer_config.json").read_text())
    assert tokenizer["model_max_length"] == 512 and tokenizer["tokenizer_class"] == "DebertaV2Tokenizer"
    assert (output / "config.json").read_bytes() == (backbone / "config.json").read_bytes()
    assert len(manifest["label_mapping"]) == 18
    evaluation = json.loads((output / "evaluation.json").read_text())
    assert evaluation["status"] == "not_evaluated" and not evaluation["a_b_label_agreement"]
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["backbone"]["revision"] == BACKBONE_REVISION
    assert provenance["library_version"] == "0.2.29"
    assert (m0 / "pytorch_model.bin").read_bytes() == b"SYNTHETIC-WEIGHTS"


@pytest.mark.parametrize("attack", ["tamper", "missing", "symlink", "existing_output", "unsafe_config", "wrong_backbone"])
def test_t07_builder_rejects_unverified_or_unsafe_inputs_and_preserves_files(sources, tmp_path, attack):
    m0, backbone, license_file, specs = sources
    output = tmp_path / "package"
    if attack == "tamper":
        (m0 / "pytorch_model.bin").write_bytes(b"tampered")
    elif attack == "missing":
        m0 = tmp_path / "absent"
    elif attack == "symlink":
        link = tmp_path / "linked-m0"
        link.symlink_to(m0, target_is_directory=True)
        m0 = link
    elif attack == "existing_output":
        output.mkdir()
        (output / "preserve").write_text("must remain")
    else:
        source, name = ("m0", "gliner_config.json") if attack == "unsafe_config" else ("backbone", "config.json")
        data = b'{"auto_map":{"remote":"python.py"}}' if attack == "unsafe_config" else b'{"model_type":"remote-custom"}'
        path = (m0 if source == "m0" else backbone) / name
        path.write_bytes(data)
        specs = tuple(AssetSpec(item.source, item.name, len(data), "sha256", hashlib.sha256(data).hexdigest())
                      if (item.source, item.name) == (source, name) else item for item in specs)
    with pytest.raises(PackagePreparationError):
        build_package(m0, backbone, output, license_file, assets=specs)
    if attack == "existing_output":
        assert (output / "preserve").read_text() == "must remain"


def test_t07_git_blob_metadata_digest_is_checked(sources, tmp_path):
    m0, backbone, license_file, specs = sources
    data = (m0 / "README.md").read_bytes()
    git_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    changed = tuple(AssetSpec(item.source, item.name, item.size, "git-sha1", git_hash)
                    if (item.source, item.name) == ("m0", "README.md") else item for item in specs)
    assert build_package(m0, backbone, tmp_path / "package", license_file, assets=changed)


def test_t07_optional_backbone_license_is_bundled_and_recorded(sources, tmp_path):
    m0, backbone, license_file, specs = sources
    mit_license = tmp_path / "MIT_LICENSE"
    mit_license.write_text("MIT License\nCopyright synthetic test\nPermission is hereby granted")
    output = tmp_path / "package"
    result = build_package(m0, backbone, output, license_file, assets=specs, backbone_license_file=mit_license)
    assert "BACKBONE_LICENSE" in result["files"]
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["backbone_full_license_copy"] == "included"


@pytest.mark.parametrize("attack", ["wrong_digest", "incomplete_specs", "over_budget", "positions_bool", "license", "mit_license"])
def test_t07_additional_validation_guards(sources, tmp_path, attack):
    m0, backbone, license_file, specs = sources
    kwargs = {}
    if attack == "wrong_digest":
        path = m0 / "pytorch_model.bin"
        data = path.read_bytes()
        path.write_bytes(b"X" + data[1:])
    elif attack == "incomplete_specs":
        specs = specs[:-1]
    elif attack == "over_budget":
        specs = (AssetSpec("m0", "pytorch_model.bin", 6 * 1024 ** 3, "sha256", "a" * 64),) + specs[1:]
    elif attack == "positions_bool":
        data = b'{"model_type":"deberta-v2","max_position_embeddings":true}'
        (backbone / "config.json").write_bytes(data)
        specs = tuple(AssetSpec(item.source, item.name, len(data), "sha256", hashlib.sha256(data).hexdigest())
                      if (item.source, item.name) == ("backbone", "config.json") else item for item in specs)
    elif attack == "license":
        license_file.write_text("unexpected license")
    else:
        bad_mit = tmp_path / "bad-license"
        bad_mit.write_text("unexpected license")
        kwargs["backbone_license_file"] = bad_mit
    with pytest.raises(PackagePreparationError):
        build_package(m0, backbone, tmp_path / "package", license_file, assets=specs, **kwargs)


def test_t07_copied_asset_hash_is_rechecked_and_partial_output_preserved(sources, tmp_path, monkeypatch):
    import detector.prepare_m0 as module
    m0, backbone, license_file, specs = sources
    def corrupted_copy(source, target):
        Path(target).write_bytes(b"changed during copy")
    monkeypatch.setattr(module.shutil, "copyfile", corrupted_copy)
    output = tmp_path / "package"
    with pytest.raises(PackagePreparationError):
        build_package(m0, backbone, output, license_file, assets=specs)
    assert (output / "pytorch_model.bin").read_bytes() == b"changed during copy"
