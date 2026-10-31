"""Offline package builder; downloads/installation are never implicit.

Pinned source assets must already exist. Output is a new directory only; errors
preserve all source files and any partial output. Engineering labels are not an
A/B agreement or evidence of quality, safety, GPU readiness, or release approval.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil

from .gliner_backend import GLINER_VERSION, M0_REPO_ID, M0_REVISION
from .runtime import ModelPackage, _verify_package


BACKBONE_REPO_ID = "microsoft/mdeberta-v3-base"
BACKBONE_REVISION = "a0484667b22365f84929a935b5e50a51f71f159d"
DOWNLOAD_BUDGET_BYTES = 5 * 1024 ** 3
BACKBONE_LICENSE_COMMIT = "4d7fe0bd4fb3c7d4f4005a7cafabde9800372098"
BACKBONE_LICENSE_SHA256 = "c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383"


class PackagePreparationError(Exception):
    def __init__(self):
        super().__init__("model_package_preparation_failed")


@dataclass(frozen=True)
class AssetSpec:
    source: str
    name: str
    size: int
    algorithm: str
    digest: str


PINNED_ASSETS = (
    AssetSpec("m0", "pytorch_model.bin", 1155900362, "sha256", "3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d"),
    AssetSpec("m0", "gliner_config.json", 478, "git-sha1", "17fd7dcfe79421099b83c49ea325dbb9d2148791"),
    AssetSpec("m0", "README.md", 3040, "git-sha1", "167f44c6a24bc89ef4fb0492c2e612544df6d661"),
    AssetSpec("backbone", "config.json", 579, "git-sha1", "27b905d788d20826414d27ce9fa9765623e38ea7"),
    AssetSpec("backbone", "tokenizer_config.json", 52, "git-sha1", "acfd94e399c5659e4bed75f91b4ee24b111fc7a6"),
    AssetSpec("backbone", "spm.model", 4305025, "sha256", "13c8d666d62a7bc4ac8f040aab68e942c861f93303156cc28f5c7e885d86d6e3"),
    AssetSpec("backbone", "README.md", 3667, "git-sha1", "779d99e0b02b1f278e35c2ad228417192dec4eba"),
)

ENGINEERING_LABELS = {
    "KR_RRN": "Korean resident registration number", "CREDIT_CARD": "credit card number",
    "PERSON": "person", "EMAIL_ADDRESS": "email address", "KR_PHONE_NUMBER": "phone number",
    "KR_BANK_ACCOUNT": "bank account number", "KR_LOCATION": "address", "DATE_OF_BIRTH": "date of birth",
    "KR_DRIVER_LICENSE": "driver's license number", "KR_PASSPORT": "passport number",
    "KR_NHIS": "national health insurance number", "KR_VEHICLE_PLATE": "license plate number",
    "USERNAME": "username", "PERSONAL_ATTRIBUTE": "personal attribute", "ORGANIZATION": "organization",
    "URL": "URL", "IP_ADDRESS": "IP address", "NRP": "nationality religious or political group",
}


def _sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _asset(root, spec):
    if (root.is_symlink() or not root.is_dir() or spec.source not in {"m0", "backbone"}
            or Path(spec.name).name != spec.name or spec.name in {"", ".", ".."}
            or type(spec.size) is not int or spec.size < 1 or spec.algorithm not in {"sha256", "git-sha1"}):
        raise ValueError
    path = root / spec.name
    if path.is_symlink() or not path.is_file() or path.stat().st_size != spec.size:
        raise ValueError
    digest = hashlib.sha256() if spec.algorithm == "sha256" else hashlib.sha1()
    if spec.algorithm == "git-sha1":
        digest.update(f"blob {spec.size}\0".encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != spec.digest:
        raise ValueError
    return path, _sha256(path)


def build_package(m0_dir, backbone_dir, output_dir, license_file, *, assets=PINNED_ASSETS, backbone_license_file=None):
    """Verify fixed local assets, then create a portable package in a new path.

    The assets argument permits explicit tiny synthetic fixtures; production
    callers must use the pinned default. No existing output is overwritten.
    """
    try:
        roots = {"m0": Path(m0_dir), "backbone": Path(backbone_dir)}
        output = Path(output_dir)
        if output.exists() or output.is_symlink() or sum(asset.size for asset in assets) > DOWNLOAD_BUDGET_BYTES:
            raise ValueError
        expected = {(item.source, item.name) for item in PINNED_ASSETS}
        if {(item.source, item.name) for item in assets} != expected or len(assets) != len(expected):
            raise ValueError
        verified = {(item.source, item.name): _asset(roots[item.source], item) for item in assets}
        config = json.loads(verified["m0", "gliner_config.json"][0].read_text())
        encoder = json.loads(verified["backbone", "config.json"][0].read_text())
        tokenizer = json.loads(verified["backbone", "tokenizer_config.json"][0].read_text())
        if (config.get("model_name") != BACKBONE_REPO_ID or config.get("auto_map") or config.get("trust_remote_code")
                or encoder.get("model_type") != "deberta-v2" or encoder.get("auto_map")
                or tokenizer.get("auto_map") or tokenizer.get("trust_remote_code")):
            raise ValueError
        positions = encoder.get("max_position_embeddings")
        if type(positions) is not int or not 1 <= positions <= 8192:
            raise ValueError
        license_path = Path(license_file)
        if license_path.is_symlink() or "Apache License" not in license_path.read_text():
            raise ValueError
        mit_path = Path(backbone_license_file) if backbone_license_file is not None else None
        if mit_path is not None and (mit_path.is_symlink() or "MIT License" not in mit_path.read_text()
                or (assets is PINNED_ASSETS and _sha256(mit_path) != BACKBONE_LICENSE_SHA256)):
            raise ValueError
        config["encoder_config"] = encoder
        tokenizer["model_max_length"] = positions
        tokenizer["tokenizer_class"] = "DebertaV2Tokenizer"
        output.mkdir()

        def write_json(name, value):
            with (output / name).open("x", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")

        def copy_asset(source, name, target):
            path, expected_sha = verified[source, name]
            shutil.copyfile(path, output / target)
            if _sha256(output / target) != expected_sha:
                raise ValueError

        copy_asset("m0", "pytorch_model.bin", "pytorch_model.bin")
        copy_asset("m0", "README.md", "MODEL_CARD.md")
        copy_asset("backbone", "README.md", "BACKBONE_MODEL_CARD.md")
        copy_asset("backbone", "config.json", "config.json")
        copy_asset("backbone", "spm.model", "spm.model")
        shutil.copyfile(license_path, output / "LICENSE")
        if mit_path is not None:
            shutil.copyfile(mit_path, output / "BACKBONE_LICENSE")
        write_json("gliner_config.json", config)
        write_json("tokenizer_config.json", tokenizer)
        write_json("evaluation.json", {"status": "not_evaluated", "a_b_label_agreement": False,
                                      "quality_gate": "blocked", "actual_gpu_verified": False})
        write_json("provenance.json", {
            "model": {"repo_id": M0_REPO_ID, "revision": M0_REVISION, "license_id": "Apache-2.0"},
            "backbone": {"repo_id": BACKBONE_REPO_ID, "revision": BACKBONE_REVISION, "license_id": "MIT"},
            "library_version": GLINER_VERSION, "license_copy_sha256": _sha256(license_path),
            "assets": [{"source": item.source, "name": item.name, "size": item.size,
                        "algorithm": item.algorithm, "digest": item.digest,
                        "sha256": verified[item.source, item.name][1]} for item in assets],
            "changes": ["inline pinned backbone encoder_config", "set bundled tokenizer class and finite model_max_length"],
            "label_mapping_status": "provisional_engineering_only_not_agreed_with_A",
            "asset_verification": "official_pinned_metadata" if assets is PINNED_ASSETS else "explicit_supplied_fixture_specs",
            "backbone_full_license_copy": "included" if mit_path is not None else "pending_before_distribution",
            "backbone_license_sha256": _sha256(mit_path) if mit_path is not None else None,
            "backbone_license_source": f"https://github.com/microsoft/DeBERTa/blob/{BACKBONE_LICENSE_COMMIT}/LICENSE"
                if mit_path is not None and assets is PINNED_ASSETS else None,
        })
        manifest = ModelPackage.model_validate({
            "schema_version": "1.0", "model_version": f"M0-{M0_REVISION[:12]}-engineering",
            "threshold_version": "engineering-0.5-not-evaluated-v1",
            "model": {"repo_id": M0_REPO_ID, "revision": M0_REVISION},
            "label_mapping": ENGINEERING_LABELS, "thresholds": {kind: 0.5 for kind in ENGINEERING_LABELS},
            "preprocessing": {"version": "gliner029-word-window-v1", "normalization": "none",
                              "offset_unit": "unicode_codepoint", "max_input_codepoints": 16000, "overflow": "reject"},
            "license_id": "Apache-2.0", "artifacts": {"weights": ["pytorch_model.bin"], "config": "gliner_config.json",
                "tokenizer": ["spm.model", "tokenizer_config.json", "config.json"], "license": "LICENSE", "evaluation": "evaluation.json"},
            "files": {path.name: _sha256(path) for path in output.iterdir() if path.is_file()},
        }).model_dump()
        write_json("manifest.json", manifest)
        _verify_package(output)
        return manifest
    except Exception:
        raise PackagePreparationError() from None
