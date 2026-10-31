"""Stage 8 B-runtime integration, Korean splitter adoption, and guardrail tests."""
import asyncio
import copy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from dev.detector.gliner_backend import (
    GLINER_VERSION,
    GLiNERBackend,
    M0_REPO_ID,
    M0_REVISION,
    M1_REVISION,
    MODEL_TYPES,
    PINNED_ENCODER_CONFIG,
    PINNED_M1_ENCODER_CONFIG,
    ko_atomic_suffix_v3 as b_ko_atomic_suffix_v3,
    load_gliner,
)
from dev.detector.runtime import ModelManager
from model.segmentation.splitters import ko_atomic_suffix_v3 as a_ko_atomic_suffix_v3


class _Tokenizer:
    model_max_length = 256

    def __call__(self, inputs, **kwargs):
        assert kwargs["truncation"] is False and kwargs["is_split_into_words"] is True

        class Encoded(dict):
            def word_ids(self, batch_index=0):
                return [None] + list(range(len(inputs[0]))) + [None]

        return Encoded(input_ids=[[0] * (len(inputs[0]) + 2)])


class _Processor:
    def __init__(self):
        self.transformer_tokenizer = _Tokenizer()
        self.words_splitter = lambda text: [(m.group(), m.start(), m.end()) for m in re.finditer(r"\w+|\S", text)]

    @staticmethod
    def prepare_inputs(texts, labels):
        return [["L"] * (len(labels) * 2 + 1) + texts[0]], [len(labels) * 2 + 1]


class _Model:
    def __init__(self, entities=None, max_len=12, max_width=4):
        self.config = SimpleNamespace(max_len=max_len, max_width=max_width)
        self.data_processor = _Processor()
        self.entities = entities
        self.calls = []

    def eval(self):
        pass

    def predict_entities(self, text, labels, **kwargs):
        self.calls.append(text)
        return self.entities(text) if self.entities else []


def _base_manifest(preprocessing_version="ko_atomic_suffix_v3", revision=M1_REVISION):
    labels = {kind: kind.lower() for kind in sorted(MODEL_TYPES)}
    thresholds = {kind: 0.5 for kind in MODEL_TYPES}
    return {
        "schema_version": "1.0",
        "model_version": "M1-checkpoint-1262-v1",
        "threshold_version": "stage8-0.5-v1",
        "model": {"repo_id": M0_REPO_ID, "revision": revision},
        "label_mapping": labels,
        "thresholds": thresholds,
        "preprocessing": {
            "version": preprocessing_version,
            "normalization": "none",
            "offset_unit": "unicode_codepoint",
            "max_input_codepoints": 16000,
            "overflow": "reject",
        },
        "license_id": "Apache-2.0",
        "artifacts": {
            "weights": ["model.safetensors"],
            "config": "gliner_config.json",
            "tokenizer": ["spm.model", "tokenizer_config.json", "config.json"],
            "license": "LICENSE",
            "evaluation": "evaluation.json",
        },
        "files": {
            "model.safetensors": "a" * 64,
            "gliner_config.json": "b" * 64,
            "spm.model": "c" * 64,
            "tokenizer_config.json": "d" * 64,
            "config.json": "e" * 64,
            "LICENSE": "f" * 64,
            "evaluation.json": "0" * 64,
        },
    }


def test_b_and_a_ko_atomic_suffix_v3_splitters_are_identical():
    samples = [
        "김가상에게 연락해 주세요.",
        "김가상님께서에게는 010-1234-5678로 안내드립니다.",
        "연락처 user.name+tag@example.co.kr 및 https://example.org/a/b?x=1&y=2 확인.",
        "👩‍💻 홍길동님과 서울특별시 강남구 테헤란로 123에서 만납니다.",
    ]
    for text in samples:
        assert b_ko_atomic_suffix_v3(text) == a_ko_atomic_suffix_v3(text)


def test_b_gliner_backend_adopts_korean_splitter_and_boundary_safe_window_plan():
    manifest = _base_manifest("ko_atomic_suffix_v3")
    model = _Model(max_len=12, max_width=4)
    backend = GLiNERBackend(model, manifest)
    backend.warmup()
    assert getattr(model.data_processor.words_splitter, "version", None) == "ko_atomic_suffix_v3"
    text = ("첫째 가나님께서에게는 확인합니다. ") * 12
    # Primary planner fails at the 12-word edge due to multi-suffix stripping,
    # while B's integrated whole-group fallback succeeds with complete coverage.
    with pytest.raises( Exception ):
        backend._primary_plan(text, [2048])
    result = backend.inspect([{"segment_id": "s1", "text": text}])
    assert result.status == "complete"


def test_b_gliner_backend_filters_numeric_lookalike_kr_rrn_and_redacted_placeholders():
    manifest = _base_manifest("ko_atomic_suffix_v3")

    def fake_entities(text):
        results = []
        for pattern, label in [
            (r"EQ-2026-314159", "kr_rrn"),
            (r"1234567890123456", "kr_rrn"),
            (r"990230-1234567", "kr_rrn"),
            (r"900101-1234567", "kr_rrn"),
            (r"\[REDACTED\]", "person"),
            (r"김철수", "person"),
        ]:
            for match in re.finditer(pattern, text):
                results.append({
                    "start": match.start(),
                    "end": match.end(),
                    "text": match.group(),
                    "label": label,
                    "score": 0.95,
                })
        return results

    model = _Model(entities=fake_entities, max_len=64, max_width=12)
    backend = GLiNERBackend(model, manifest)
    backend.warmup()

    normal_text = "장비코드 EQ-2026-314159 및 일련번호 1234567890123456, 잘못된 날짜 990230-1234567 확인."
    normal_res = backend.inspect([{"segment_id": "norm", "text": normal_text}])
    assert normal_res.status == "complete" and normal_res.findings == []

    real_rrn_text = "주민번호 900101-1234567 확인."
    real_res = backend.inspect([{"segment_id": "pos", "text": real_rrn_text}])
    assert len(real_res.findings) == 1 and real_res.findings[0].type == "KR_RRN"

    recheck_text = "안녕하세요 [REDACTED] 고객님, 담당자 김철수입니다."
    recheck_res = backend.inspect([{"segment_id": "recheck", "text": recheck_text}])
    assert len(recheck_res.findings) == 1
    assert recheck_text[recheck_res.findings[0].start:recheck_res.findings[0].end] == "김철수"


def test_merged_backend_records_timings_with_guarded_global_offsets():
    from dev.detector import timing

    def entities(text):
        return [{"start": m.start(), "end": m.end(), "text": m.group(),
                 "label": label, "score": 0.95}
                for pattern, label in [(r"010-1234-5678", "kr_rrn"),
                                       (r"900101-1234567", "kr_rrn"),
                                       (r"\[REDACTED\]", "person"),
                                       (r"김철수", "person")]
                for m in re.finditer(pattern, text)]

    model = _Model(entities=entities, max_len=64, max_width=12)
    backend = GLiNERBackend(model, _base_manifest())
    backend.warmup()
    # Force a later window: guards must see document offsets, not window offsets.
    text = "안내 문장입니다. " * 45 + "010-1234-5678 [REDACTED] 주민번호 900101-1234567 김철수"
    reports = []
    model.calls.clear()
    with timing.observe(reports.append):
        result = backend.inspect([{"segment_id": "merged", "text": text}])
        timing.outcome(result.status)
    assert result.status == "complete" and len(model.calls) > 1
    assert {(f.type, text[f.start:f.end]) for f in result.findings} == {
        ("KR_RRN", "900101-1234567"), ("PERSON", "김철수")}
    assert len(result.findings) == 2  # overlapping windows still deduplicate
    stages = reports[0]["stages"]
    assert reports[0]["outcome"] == "complete"
    assert stages["preprocessing"]["completed"] == 1
    assert stages["model_call"]["completed"] == len(model.calls)
    assert stages["postprocessing"]["completed"] == len(model.calls)
    assert all(stage["failed"] == 0 for stage in stages.values())


def _write_package_dir(root, *, revision, encoder_config, preprocessing_version, model_version):
    root.mkdir(parents=True, exist_ok=True)
    manifest = _base_manifest(preprocessing_version=preprocessing_version, revision=revision)
    manifest["model_version"] = model_version
    gliner_cfg = {
        "size_sup": -1,
        "max_types": 25,
        "shuffle_types": True,
        "random_drop": False,
        "max_neg_type_ratio": 3,
        "max_len": 384,
        "lr_encoder": "5e-6",
        "lr_others": "1e-5",
        "num_steps": 30000,
        "warmup_ratio": 5000,
        "train_batch_size": 8,
        "eval_every": 5000,
        "max_width": 12,
        "model_name": "microsoft/mdeberta-v3-base",
        "fine_tune": True,
        "subtoken_pooling": "first",
        "hidden_size": 512,
        "span_mode": "markerV0",
        "dropout": 0.4,
        "name": "correct",
        "class_token_index": 250103,
        "embed_ent_token": True,
        "ent_token": "<<ENT>>",
        "sep_token": "<<SEP>>",
        "words_splitter_type": "whitespace",
        "vocab_size": encoder_config["vocab_size"],
        "encoder_config": copy.deepcopy(encoder_config),
    }
    tok_cfg = {"tokenizer_class": "DebertaV2Tokenizer", "model_max_length": 512}
    payloads = {
        "model.safetensors": b"synthetic-weights-" + model_version.encode(),
        "gliner_config.json": json.dumps(gliner_cfg).encode(),
        "config.json": json.dumps(encoder_config).encode(),
        "tokenizer_config.json": json.dumps(tok_cfg).encode(),
        "spm.model": b"synthetic-spm",
        "LICENSE": b"Apache License 2.0",
        "evaluation.json": b'{"status":"evaluated"}',
    }
    manifest["files"] = {}
    for name, content in payloads.items():
        (root / name).write_bytes(content)
        manifest["files"][name] = hashlib.sha256(content).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return root, manifest


def test_load_gliner_and_model_manager_hot_swap_m0_to_m1_and_rollback(tmp_path):
    m0_dir, _ = _write_package_dir(
        tmp_path / "m0-pkg",
        revision=M0_REVISION,
        encoder_config=PINNED_ENCODER_CONFIG,
        preprocessing_version="gliner029-word-window-v1",
        model_version="M0-baseline-v1",
    )
    m1_dir, _ = _write_package_dir(
        tmp_path / "m1-pkg",
        revision=M1_REVISION,
        encoder_config=PINNED_M1_ENCODER_CONFIG,
        preprocessing_version="ko_atomic_suffix_v3",
        model_version="M1-checkpoint-1262-v1",
    )

    def loader(path, **kwargs):
        return _Model(max_len=384, max_width=12)

    async def exercise():
        manager = ModelManager(lambda root, manifest: load_gliner(root, manifest, loader=loader, device="cpu"))
        await manager.initialize(m0_dir)
        assert manager.ready and manager.model_version == "M0-baseline-v1"
        await manager.replace(m1_dir)
        assert manager.ready and manager.model_version == "M1-checkpoint-1262-v1"
        res, version, _ = await manager.inspect_with_metadata([{"segment_id": "s", "text": "김가상에게 전달"}])
        assert res.status == "complete" and version == "M1-checkpoint-1262-v1"
        # Instant rollback to M0
        await manager.replace(m0_dir)
        assert manager.ready and manager.model_version == "M0-baseline-v1"

    asyncio.run(exercise())

