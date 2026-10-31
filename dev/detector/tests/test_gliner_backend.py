"""Synthetic R03/R07/R11 checks. No actual GLiNER weights or GPU needed."""
import copy
import hashlib
import json
import re
from types import SimpleNamespace

import pytest

from detector.gliner_backend import GLiNERBackend, ModelBackendError, MODEL_TYPES, M0_REPO_ID, M0_REVISION, GLINER_VERSION, load_gliner
from detector.schemas import Segment
from detector.tests.test_runtime import package


class Tokenizer:
    model_max_length = 256
    def __call__(self, inputs, **kwargs):
        assert kwargs["truncation"] is False and kwargs["is_split_into_words"] is True
        class Encoded(dict):
            def word_ids(self, batch_index=0):
                return [None] + [i for i, word in enumerate(inputs[0]) for _ in word] + [None]
        return Encoded(input_ids=[[0] * (2 + sum(len(word) for word in inputs[0]))])


class Processor:
    transformer_tokenizer = Tokenizer()
    @staticmethod
    def words_splitter(text):
        return [(match.group(), match.start(), match.end()) for match in re.finditer(r"\w+|\S", text)]
    @staticmethod
    def prepare_inputs(texts, labels):
        return [["L"] * (len(labels) * 2 + 1) + texts[0]], [len(labels) * 2 + 1]


class Model:
    config = SimpleNamespace(max_len=6, max_width=3)
    data_processor = Processor()
    def __init__(self, entities=None):
        self.calls = []
        self.entities = entities
        self.evaluated = False
    def eval(self):
        self.evaluated = True
    def predict_entities(self, text, labels, **kwargs):
        self.calls.append((text, labels, kwargs))
        assert kwargs["flat_ner"] is False and kwargs["multi_label"] is True
        if self.entities is not None:
            return self.entities(text)
        findings = []
        for match in re.finditer(r"홍길동|target boundary", text):
            findings.append({"start": match.start(), "end": match.end(), "text": match.group(), "label": "person", "score": 0.9})
        return findings


@pytest.fixture
def manifest(tmp_path):
    _, data = package(tmp_path)
    data["label_mapping"] = {kind: ("person" if kind == "PERSON" else kind.lower()) for kind in sorted(MODEL_TYPES)}
    data["thresholds"] = {kind: 0.5 for kind in MODEL_TYPES}
    return data


def backend(manifest, model=None):
    model = model or Model()
    loaded = GLiNERBackend(model, manifest)
    loaded.warmup()
    model.calls.clear()
    return loaded, model


def test_t07_warmup_required_and_uses_actual_inference(manifest):
    model = Model()
    loaded = GLiNERBackend(model, manifest)
    with pytest.raises(ModelBackendError):
        loaded.inspect([Segment(segment_id="s", text="synthetic")])
    loaded.warmup()
    assert model.evaluated and model.calls


def test_t05_unicode_original_positions_and_no_source_value_in_findings(manifest):
    loaded, _ = backend(manifest)
    text = "😀 홍길동은 홍길동"
    result = loaded.inspect([{"segment_id": "s", "text": text}])
    assert result.status == "complete"
    assert [(f.start, f.end) for f in result.findings] == [(2, 5), (7, 10)]
    assert all("text" not in f.model_dump() and f.type == "PERSON" for f in result.findings)


def test_t05_full_16000_codepoints_are_windowed_and_last_token_inspected(manifest):
    loaded, model = backend(manifest)
    text = "x " * 7997 + "홍길동" + " " * 3
    assert len(text) == 16000
    result = loaded.inspect([{"segment_id": "long", "text": text}])
    assert result.status == "complete" and len(model.calls) > 1
    assert any(text[f.start:f.end] == "홍길동" and f.start == 15994 for f in result.findings)


def test_t05_overlap_preserves_entities_straddling_window_boundary(manifest):
    loaded, model = backend(manifest)
    text = "a b c d e target boundary z"
    result = loaded.inspect([{"segment_id": "s", "text": text}])
    assert result.status == "complete"
    assert any(text[f.start:f.end] == "target boundary" for f in result.findings)
    assert len({(f.start, f.end, f.type) for f in result.findings}) == len(result.findings)


def test_t06_unfit_single_word_never_becomes_complete_empty(manifest):
    loaded, model = backend(manifest)
    result = loaded.inspect([{"segment_id": "s", "text": "x" * 16000}])
    assert result.status == "incomplete" and result.findings == []
    assert model.calls == []


def test_t06_plan_every_segment_before_any_inference(manifest):
    loaded, model = backend(manifest)
    result = loaded.inspect([{"segment_id": "s", "text": "홍길동"}, {"segment_id": "long", "text": "x" * 1000}])
    assert result.status == "incomplete" and not model.calls


def test_t06_missing_supported_types_prevents_ready_backend(manifest):
    del manifest["label_mapping"]["KR_RRN"]
    del manifest["thresholds"]["KR_RRN"]
    with pytest.raises(ModelBackendError):
        GLiNERBackend(Model(), manifest)


@pytest.mark.parametrize("malformed", [
    {"label": "unknown"}, {"start": True}, {"start": -1}, {"end": 100},
    {"score": float("nan")}, {"score": True}, {"text": "wrong source"},
])
def test_t06_invalid_entity_results_fail_closed(manifest, malformed):
    loaded, model = backend(manifest)
    model.entities = lambda text: [{"label": "person", "start": 0, "end": 1, "text": text[:1], "score": 0.9} | malformed]
    with pytest.raises(ModelBackendError):
        loaded.inspect([{"segment_id": "s", "text": "synthetic"}])


def test_t07_each_label_threshold_is_applied_without_early_suppression(manifest):
    manifest["thresholds"]["PERSON"] = 0.8
    loaded, model = backend(manifest)
    model.entities = lambda text: [{"label": "person", "start": 0, "end": 1, "text": text[:1], "score": 0.7}]
    result = loaded.inspect([{"segment_id": "s", "text": "x"}])
    assert result.status == "complete" and result.findings == []
    assert model.calls[0][2]["threshold"] == 0.5


def test_t06_splitter_nonwhitespace_coverage_gaps_are_incomplete(manifest):
    loaded, model = backend(manifest)
    model.data_processor = copy.copy(model.data_processor)
    model.data_processor.words_splitter = lambda text: [("x", 0, 1)]
    result = loaded.inspect([{"segment_id": "s", "text": "x hidden"}])
    assert result.status == "incomplete" and not model.calls


def test_t10_inference_exception_is_sanitized(manifest):
    loaded, model = backend(manifest)
    def raises(text):
        raise RuntimeError("SYNTHETIC-RAW-VALUE")
    model.entities = raises
    with pytest.raises(ModelBackendError) as caught:
        loaded.inspect([{"segment_id": "s", "text": "synthetic"}])
    assert "SYNTHETIC-RAW-VALUE" not in str(caught.value)


@pytest.mark.parametrize("setting,value", [("max_len", 0), ("max_width", 7), ("max_width", True), ("model_max_length", 1000000000)])
def test_t06_unsafe_or_unknown_model_limits_cannot_be_used(manifest, setting, value):
    model = Model()
    model.config = copy.copy(model.config)
    model.data_processor = copy.copy(model.data_processor)
    model.data_processor.transformer_tokenizer = copy.copy(model.data_processor.transformer_tokenizer)
    target = model.data_processor.transformer_tokenizer if setting == "model_max_length" else model.config
    setattr(target, setting, value)
    with pytest.raises(ModelBackendError):
        GLiNERBackend(model, manifest)


@pytest.mark.parametrize("segments", [[], [{"segment_id": "s", "text": "a"}, {"segment_id": "s", "text": "b"}],
    [{"segment_id": "s", "text": "a" * 16001}]])
def test_t06_invalid_input_is_rejected_without_inspection(manifest, segments):
    loaded, model = backend(manifest)
    with pytest.raises(ModelBackendError):
        loaded.inspect(segments)
    assert not model.calls


def test_t05_whitespace_only_text_is_complete_without_losing_nonwhitespace(manifest):
    loaded, model = backend(manifest)
    result = loaded.inspect([{"segment_id": "s", "text": " \n\t"}])
    assert result.status == "complete" and not result.findings and not model.calls


@pytest.mark.parametrize("mode", ["wrong_span", "bad_prompt", "bad_ids", "missing_word_ids", "lost_word", "wrong_word_ids"])
def test_t06_unprovable_tokenization_is_incomplete(manifest, mode):
    loaded, model = backend(manifest)
    processor = copy.copy(model.data_processor)
    model.data_processor = processor
    if mode == "wrong_span":
        processor.words_splitter = lambda text: [("wrong", 0, len(text))]
    elif mode == "bad_prompt":
        processor.prepare_inputs = lambda texts, labels: ([[]], [-1])
    else:
        class BrokenTokenizer(Tokenizer):
            def __call__(self, inputs, **kwargs):
                encoded = super().__call__(inputs, **kwargs)
                if mode == "bad_ids":
                    encoded["input_ids"] = []
                elif mode == "missing_word_ids":
                    return dict(encoded)
                elif mode == "lost_word":
                    encoded.word_ids = lambda **kwargs: [None] * len(encoded["input_ids"][0])
                else:
                    encoded.word_ids = lambda **kwargs: [True] * len(encoded["input_ids"][0])
                return encoded
        processor.transformer_tokenizer = BrokenTokenizer()
    result = loaded.inspect([{"segment_id": "s", "text": "synthetic"}])
    assert result.status == "incomplete" and not model.calls


def test_t06_window_local_retokenization_cannot_change_original_boundaries(manifest):
    loaded, model = backend(manifest)
    processor = copy.copy(model.data_processor)
    model.data_processor = processor
    def splitter(text):
        if text.startswith("a b"):
            return [(text, 0, len(text))]
        return Processor.words_splitter(text)
    processor.words_splitter = splitter
    result = loaded.inspect([{"segment_id": "s", "text": "x y z q a b c d e"}])
    assert result.status == "incomplete" and not model.calls


def test_t06_too_many_windows_fail_closed_before_any_prediction(manifest):
    model = Model()
    loaded = GLiNERBackend(model, manifest, max_windows=1)
    loaded.warmup()
    model.calls.clear()
    assert loaded.inspect([{"segment_id": "s", "text": "a b c d e f g"}]).status == "incomplete"
    assert not model.calls
    assert loaded.inspect([{"segment_id": "a", "text": "x"}, {"segment_id": "b", "text": "y"}]).status == "incomplete"
    assert not model.calls


def test_t06_warmup_does_not_set_ready_if_inference_is_unprovable(manifest):
    model = Model()
    model.data_processor = copy.copy(model.data_processor)
    model.data_processor.transformer_tokenizer = copy.copy(model.data_processor.transformer_tokenizer)
    model.data_processor.transformer_tokenizer.model_max_length = 1
    loaded = GLiNERBackend(model, manifest)
    with pytest.raises(ModelBackendError):
        loaded.warmup()
    assert not loaded.warmed


@pytest.mark.parametrize("mode", ["warning", "not_list"])
def test_t06_truncation_warning_or_unexpected_result_is_an_error(manifest, mode):
    loaded, model = backend(manifest)
    def predict(text):
        if mode == "warning":
            import warnings
            warnings.warn("input has been truncated: synthetic source", stacklevel=1)
            return []
        return {}
    model.entities = predict
    with pytest.raises(ModelBackendError):
        loaded.inspect([{"segment_id": "s", "text": "synthetic"}])


def test_t05_overlap_duplicate_keeps_maximum_score(manifest):
    loaded, model = backend(manifest)
    def predict(text):
        at = text.find("홍길동")
        return [] if at < 0 else [{"label": "person", "start": at, "end": at + 3, "text": "홍길동", "score": 0.6 if len(model.calls) == 1 else 0.9}]
    model.entities = predict
    result = loaded.inspect([{"segment_id": "s", "text": "a b c d 홍길동 e f g"}])
    assert len(result.findings) == 1 and result.findings[0].score == 0.9


@pytest.fixture
def local_package(tmp_path, manifest):
    root = tmp_path / "mock-v1"
    (root / "config.json").rename(root / "gliner_config.json")
    manifest["files"]["gliner_config.json"] = manifest["files"].pop("config.json")
    manifest["artifacts"]["config"] = "gliner_config.json"
    (root / "tokenizer_config.json").write_bytes(b"{}")
    manifest["files"]["tokenizer_config.json"] = hashlib.sha256(b"{}").hexdigest()
    manifest["artifacts"]["tokenizer"].append("tokenizer_config.json")
    manifest["model"] = {"repo_id": M0_REPO_ID, "revision": M0_REVISION}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, manifest


def test_t07_local_loader_is_pinned_offline_and_does_not_implicitly_warm(local_package):
    root, manifest = local_package
    observed = []
    def loader(path, **kwargs):
        observed.append((path, kwargs))
        return Model()
    loaded = load_gliner(root, manifest, loader=loader)
    assert observed == [(str(root), {"local_files_only": True, "trust_remote_code": False, "strict": True,
                                    "map_location": "cpu", "load_tokenizer": True})]
    assert not loaded.warmed


@pytest.mark.parametrize("mode", ["manifest_mismatch", "wrong_revision", "device", "missing_tokenizer", "remote_code", "loader_error", "wrong_version", "default_loader"])
def test_t07_factory_rejects_unsafe_packages_and_incompatible_libraries(local_package, monkeypatch, mode):
    import detector.gliner_backend as module
    root, manifest = local_package
    def loader(*args, **kwargs):
        if mode == "loader_error":
            raise RuntimeError("SYNTHETIC-RAW-SECRET")
        return Model()
    if mode == "manifest_mismatch":
        manifest = manifest | {"threshold_version": "different"}
    if mode == "wrong_revision":
        manifest["model"]["revision"] = "b" * 40
        (root / "manifest.json").write_text(json.dumps(manifest))
    if mode in {"missing_tokenizer", "remote_code"}:
        if mode == "missing_tokenizer":
            (root / "tokenizer_config.json").unlink()
            del manifest["files"]["tokenizer_config.json"]
            manifest["artifacts"]["tokenizer"].remove("tokenizer_config.json")
        else:
            content = b'{"auto_map":{"AutoModel":"custom.py"}}'
            (root / "gliner_config.json").write_bytes(content)
            manifest["files"]["gliner_config.json"] = hashlib.sha256(content).hexdigest()
        (root / "manifest.json").write_text(json.dumps(manifest))
    if mode in {"wrong_version", "default_loader"}:
        monkeypatch.setattr(module.importlib, "import_module", lambda name: SimpleNamespace(
            __version__="wrong" if mode == "wrong_version" else GLINER_VERSION,
            GLiNER=SimpleNamespace(from_pretrained=loader)))
        supplied_loader = None
    else:
        supplied_loader = loader
    if mode == "default_loader":
        assert isinstance(load_gliner(root, manifest), GLiNERBackend)
    else:
        with pytest.raises(ModelBackendError) as caught:
            load_gliner(root, manifest, loader=supplied_loader, device="remote" if mode == "device" else "cpu")
        assert "SYNTHETIC-RAW-SECRET" not in str(caught.value)


@pytest.mark.parametrize("quota", [0, -1, True, 2.5, float("inf"), float("nan")])
def test_t06_planning_quota_requires_positive_strict_integer(manifest, quota):
    with pytest.raises(ModelBackendError):
        GLiNERBackend(Model(), manifest, max_plan_checks=quota)


def test_t06_planning_quota_is_shared_by_all_segments_and_precedes_predictions(manifest):
    model = Model()
    loaded = GLiNERBackend(model, manifest, max_plan_checks=1)
    loaded.warmup()
    model.calls.clear()
    result = loaded.inspect([{"segment_id": "a", "text": "x"}, {"segment_id": "b", "text": "y"}])
    assert result.status == "incomplete" and result.findings == [] and not model.calls
    assert loaded.inspect([{"segment_id": "a", "text": "x"}]).status == "complete"


def test_t06_small_subtoken_budget_uses_bounded_prefix_search(manifest):
    model = Model()
    model.config = SimpleNamespace(max_len=384, max_width=3)
    model.data_processor = copy.copy(model.data_processor)
    class CountedTokenizer(Tokenizer):
        model_max_length = 48
        def __init__(self):
            self.checks = 0
        def __call__(self, inputs, **kwargs):
            self.checks += 1
            return super().__call__(inputs, **kwargs)
    tokenizer = CountedTokenizer()
    model.data_processor.transformer_tokenizer = tokenizer
    loaded = GLiNERBackend(model, manifest)
    loaded.warmup()
    model.calls.clear()
    tokenizer.checks = 0
    result = loaded.inspect([{"segment_id": "s", "text": "x " * 1999 + "xx"}])
    assert tokenizer.checks <= 2048
    assert result.status in {"complete", "incomplete"}
    if result.status == "incomplete":
        assert not model.calls and not result.findings
