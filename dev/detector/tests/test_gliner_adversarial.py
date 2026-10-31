"""Independent synthetic R03/R07/R10/R11 GLiNER and startup fault probes.

No actual GLiNER library, model weights, external service, or GPU is used.
"""
from copy import copy, deepcopy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from detector.gliner_backend import GLiNERBackend, GLINER_VERSION, MODEL_TYPES, M0_REPO_ID, M0_REVISION, ModelBackendError, load_gliner
from detector.schemas import Segment
from detector.server import DetectorConfigError, ServerSettings, settings_from_env
from detector.tests.test_gliner_backend import Model, Processor, Tokenizer
from detector.tests.test_runtime import package


@pytest.fixture
def complete_manifest(tmp_path):
    _, manifest = package(tmp_path)
    manifest["label_mapping"] = {kind: ("person" if kind == "PERSON" else kind.lower()) for kind in sorted(MODEL_TYPES)}
    manifest["thresholds"] = {kind: 0.5 for kind in MODEL_TYPES}
    return manifest


def initialized(manifest, model=None):
    model = Model() if model is None else model
    model.config = copy(model.config)
    model.data_processor = copy(model.data_processor)
    backend = GLiNERBackend(model, manifest)
    backend.warmup()
    model.calls.clear()
    return backend, model


@pytest.mark.parametrize("separator", ["\u200b", "\u200d", "\ufeff", "\u0301"])
def test_unrepresented_nonwhitespace_codepoints_cannot_be_silently_skipped(complete_manifest, separator):
    backend, model = initialized(complete_manifest)
    model.data_processor.words_splitter = lambda text: [("x", 0, 1), ("y", 2, 3)]
    result = backend.inspect([{"segment_id": "s", "text": "x" + separator + "y"}])
    assert result.status == "incomplete" and result.findings == [] and model.calls == []


@pytest.mark.parametrize("tokens", [[("x", -1, 1)], [("x", True, 1)], [("x", 0, 1), ("x", 0, 1)],
    [("x", 0, 1), ("y", 3, 4)], [("xy", 0, 1)], [("x", 0, 100)], [("y", 2, 3), ("x", 0, 1)]])
def test_splitter_offset_corruption_stops_all_predictions(complete_manifest, tokens):
    backend, model = initialized(complete_manifest)
    model.data_processor.words_splitter = lambda text: tokens
    result = backend.inspect([{"segment_id": "s", "text": "x y"}])
    assert result.status == "incomplete" and result.findings == [] and model.calls == []


def test_special_token_overhead_is_included_in_the_window_budget(complete_manifest):
    backend, model = initialized(complete_manifest)
    class SpecialTokenOverflow(Tokenizer):
        def __call__(self, inputs, **kwargs):
            encoded = super().__call__(inputs, **kwargs)
            previous = encoded.word_ids()
            encoded["input_ids"][0] += [0] * 300
            encoded.word_ids = lambda **kwargs: previous + [None] * 300
            return encoded
    model.data_processor.transformer_tokenizer = SpecialTokenOverflow()
    result = backend.inspect([{"segment_id": "s", "text": "synthetic"}])
    assert result.status == "incomplete" and not model.calls


def test_16000_nonbmp_codepoints_include_the_final_sensitive_span(complete_manifest):
    backend, model = initialized(complete_manifest)
    text = "😀 x " * 3999 + "홍길동 "
    assert len(text) == 16000
    result = backend.inspect([{"segment_id": "s", "text": text}])
    assert result.status == "complete"
    assert [(finding.start, finding.end) for finding in result.findings] == [(15996, 15999)]
    assert any("홍길동" in window for window, _, _ in model.calls)


@pytest.mark.parametrize("prefix_words", range(9))
def test_max_width_entity_is_retained_at_every_overlap_alignment(complete_manifest, prefix_words):
    backend, model = initialized(complete_manifest)
    phrase = "one target boundary"
    text = " ".join(["x"] * prefix_words + phrase.split() + ["z"] * 9)
    def predict(window):
        return [{"label": "person", "start": match.start(), "end": match.end(), "text": match.group(), "score": 0.9}
                for match in re.finditer(phrase, window)]
    model.entities = predict
    result = backend.inspect([{"segment_id": "s", "text": text}])
    assert result.status == "complete"
    assert [(finding.start, finding.end) for finding in result.findings] == [(text.index(phrase), text.index(phrase) + len(phrase))]


def test_actual_codepoint_offsets_cannot_be_replaced_by_utf16_positions(complete_manifest):
    backend, model = initialized(complete_manifest)
    model.entities = lambda text: [{"label": "person", "start": 2, "end": 5, "text": "홍길동", "score": 0.9}]
    with pytest.raises(ModelBackendError, match="gliner_inference_failed"):
        backend.inspect([{"segment_id": "s", "text": "😀홍길동"}])


def test_constructed_invalid_segment_is_revalidated_and_original_is_unchanged(complete_manifest):
    backend, model = initialized(complete_manifest)
    invalid = Segment.model_construct(segment_id="s", text=123)
    with pytest.raises(ModelBackendError):
        backend.inspect([invalid])
    assert not model.calls and invalid.text == 123
    source = {"segment_id": "s", "text": "😀 홍길동"}
    original = deepcopy(source)
    result = backend.inspect([source])
    assert result.status == "complete" and source == original


def test_extra_model_entity_content_never_enters_wire_findings(complete_manifest):
    backend, model = initialized(complete_manifest)
    model.entities = lambda text: [{"label": "person", "start": 0, "end": 1, "text": text[:1], "score": 0.9,
                                   "raw_private": "SYNTHETIC_PRIVATE_EXTRA"}]
    result = backend.inspect([{"segment_id": "s", "text": "x"}])
    assert result.status == "complete"
    assert "SYNTHETIC_PRIVATE_EXTRA" not in result.model_dump_json()
    assert set(result.findings[0].model_dump()) == {"segment_id", "type", "start", "end", "score", "detector"}


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_surrogate_package_path_is_rejected_before_any_model_startup(surrogate):
    path = "/synthetic/" + surrogate
    with pytest.raises(DetectorConfigError):
        ServerSettings("synthetic-token", Path(path))
    with pytest.raises(DetectorConfigError):
        settings_from_env({"PII_DETECTOR_TOKEN": "synthetic-token", "PII_MODEL_PACKAGE": path})


class CountedTokenizer:
    """Small valid token budget: every prompt/input word has an ID."""
    model_max_length = 48

    def __init__(self):
        self.calls = 0

    def __call__(self, inputs, **kwargs):
        assert kwargs["truncation"] is False
        self.calls += 1
        size = len(inputs[0])
        class Encoded(dict):
            def word_ids(self, batch_index=0):
                return [None] + list(range(size)) + [None]
        return Encoded(input_ids=[[0] * (size + 2)])


def counted_backend(manifest, *, max_plan_checks=2048):
    model = Model(entities=lambda text: [])
    model.config = SimpleNamespace(max_len=384, max_width=3)
    model.data_processor = Processor()
    tokenizer = CountedTokenizer()
    model.data_processor.transformer_tokenizer = tokenizer
    backend = GLiNERBackend(model, manifest, max_plan_checks=max_plan_checks)
    backend.warmup()
    tokenizer.calls = 0
    model.calls.clear()
    return backend, model, tokenizer


def test_pathological_16000_codepoint_plan_cannot_exceed_its_tokenizer_quota(complete_manifest):
    backend, model, tokenizer = counted_backend(complete_manifest, max_plan_checks=64)
    result = backend.inspect([{"segment_id": "s", "text": "x " * 7999 + "xx"}])
    assert result.status == "incomplete" and result.findings == []
    assert tokenizer.calls <= 64
    assert model.calls == []


def test_planning_quota_is_shared_across_every_segment_before_any_prediction(complete_manifest):
    backend, model, tokenizer = counted_backend(complete_manifest, max_plan_checks=2)
    result = backend.inspect([{"segment_id": f"s-{i}", "text": "x"} for i in range(3)])
    assert result.status == "incomplete" and result.findings == []
    assert tokenizer.calls <= 2 and model.calls == []


def test_planning_quota_resets_for_each_new_inspection(complete_manifest):
    backend, model, tokenizer = counted_backend(complete_manifest, max_plan_checks=1)
    for index in range(3):
        tokenizer.calls = 0
        result = backend.inspect([{"segment_id": f"s-{index}", "text": "x"}])
        assert result.status == "complete" and tokenizer.calls == 1
    assert len(model.calls) == 3


@pytest.mark.parametrize("budget", [0, -1, True, 1.0, "100", None, float("nan"), float("inf")])
def test_planning_quota_configuration_is_a_strict_positive_integer(complete_manifest, budget):
    with pytest.raises(ModelBackendError):
        GLiNERBackend(Model(), complete_manifest, max_plan_checks=budget)


@pytest.mark.parametrize("words,allowed_lengths", [(7, {1, 4, 7}), (10, {1, 4, 6})])
def test_nonmonotonic_tokenizer_fit_never_causes_an_unverified_window_to_be_inferred(complete_manifest, words, allowed_lengths):
    backend, model = initialized(complete_manifest)
    class NonMonotonicTokenizer(Tokenizer):
        def __call__(self, inputs, **kwargs):
            encoded = super().__call__(inputs, **kwargs)
            input_words = len(inputs[0]) - (2 * len(MODEL_TYPES) + 1)
            if input_words not in allowed_lengths:
                previous = encoded.word_ids()
                excess = self.model_max_length + 1 - len(encoded["input_ids"][0])
                encoded["input_ids"][0] += [0] * excess
                encoded.word_ids = lambda **kwargs: previous + [None] * excess
            return encoded
    model.data_processor.transformer_tokenizer = NonMonotonicTokenizer()
    result = backend.inspect([{"segment_id": "s", "text": " ".join(["x"] * words)}])
    if result.status == "incomplete":
        assert result.findings == [] and model.calls == []
    else:
        assert result.status == "complete" and model.calls
        assert all(len(window.split()) in allowed_lengths for window, _, _ in model.calls)


def strict_local_package(tmp_path, manifest, *, mutation=None, auxiliary=None):
    root = tmp_path / "mock-v1"
    # Independent snapshot of the pinned backbone config, rather than importing
    # the production guard's constant into its own expected-value fixture.
    encoder = {"model_type": "deberta-v2", "attention_probs_dropout_prob": 0.1, "hidden_act": "gelu",
        "hidden_dropout_prob": 0.1, "hidden_size": 768, "initializer_range": 0.02, "intermediate_size": 3072,
        "max_position_embeddings": 512, "relative_attention": True, "position_buckets": 256,
        "norm_rel_ebd": "layer_norm", "share_att_key": True, "pos_att_type": "p2c|c2p", "layer_norm_eps": 1e-7,
        "max_relative_positions": -1, "position_biased_input": False, "num_attention_heads": 12,
        "num_hidden_layers": 12, "type_vocab_size": 0, "vocab_size": 251000}
    configs = {
        "gliner_config.json": {"model_name": "microsoft/mdeberta-v3-base", "span_mode": "markerV0",
                               "encoder_config": deepcopy(encoder)},
        "config.json": deepcopy(encoder),
        "tokenizer_config.json": {"tokenizer_class": "DebertaV2Tokenizer", "model_max_length": 512},
    }
    if mutation:
        name, patch = mutation
        configs[name].update(patch)
        # Rehash coordinated root/inline edits too: the recursive guards must
        # not rely solely on the two copies disagreeing.
        if name == "config.json":
            configs["gliner_config.json"]["encoder_config"] = deepcopy(configs[name])
        elif name == "gliner_config.json" and "encoder_config" in patch:
            if isinstance(configs[name]["encoder_config"], dict):
                configs[name]["encoder_config"] = encoder | configs[name]["encoder_config"]
            configs["config.json"] = deepcopy(configs[name]["encoder_config"])
    if auxiliary:
        configs[auxiliary + "/tokenizer_config.json"] = {"tokenizer_class": "DebertaV2Tokenizer"}
    manifest["model"] = {"repo_id": M0_REPO_ID, "revision": M0_REVISION}
    manifest["artifacts"]["config"] = "gliner_config.json"
    manifest["artifacts"]["tokenizer"].append("tokenizer_config.json")
    for name, value in configs.items():
        content = json.dumps(value).encode()
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
        manifest["files"][name] = hashlib.sha256(content).hexdigest()
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root, manifest


def fake_library(monkeypatch, loader):
    import detector.gliner_backend as module
    library = SimpleNamespace(__version__=GLINER_VERSION, GLiNER=SimpleNamespace(from_pretrained=loader))
    monkeypatch.setattr(module.importlib, "import_module", lambda name: SimpleNamespace(__version__="2.10.0") if name == "torch" else library)


def test_default_factory_uses_supported_gliner_keywords_after_config_guards(tmp_path, complete_manifest, monkeypatch):
    root, manifest = strict_local_package(tmp_path, complete_manifest)
    observed = []
    # GLiNER .29 forwards an unknown trust_remote_code kwarg to its model
    # constructor, where it raises TypeError. Reject that unsupported argument.
    def loader(path, *, local_files_only, strict, map_location, load_tokenizer):
        observed.append((path, local_files_only, strict, map_location, load_tokenizer))
        return Model()
    fake_library(monkeypatch, loader)
    backend = load_gliner(root, manifest)
    assert isinstance(backend, GLiNERBackend)
    assert observed == [(str(root), True, True, "cpu", True)]


@pytest.mark.parametrize("name,patch", [
    ("gliner_config.json", {"encoder_config": {"model_type": "bert"}}),
    ("gliner_config.json", {"encoder_config": None}),
    ("gliner_config.json", {"encoder_config": {"model_type": "deberta-v2", "nested": {"auto_map": {"AutoModel": "malicious.Custom"}}}}),
    ("gliner_config.json", {"model_name": "../../SYNTHETIC_PRIVATE_OUTSIDE"}),
    ("gliner_config.json", {"model_name": "https://synthetic.invalid/model"}),
    ("gliner_config.json", {"model_type": "gliner_bi_encoder_span"}),
    ("gliner_config.json", {"labels_encoder": "SYNTHETIC_PRIVATE_OUTSIDE"}),
    ("gliner_config.json", {"decoder_model": "SYNTHETIC_PRIVATE_OUTSIDE"}),
    ("gliner_config.json", {"hidden_size": 1000000000}),
    ("gliner_config.json", {"max_width": 1000000000}),
    ("gliner_config.json", {"max_len": 1000000000}),
    ("config.json", {"model_type": "bert"}),
    ("config.json", {"auto_map": {"AutoConfig": "malicious.Custom"}}),
    ("config.json", {"encoder": {"trust_remote_code": True}}),
    ("config.json", {"attn_implementation": "synthetic/kernel"}),
    ("config.json", {"_attn_implementation": "synthetic/kernel"}),
    ("config.json", {"_attn_implementation_internal": "synthetic/kernel"}),
    ("tokenizer_config.json", {"tokenizer_class": "UnknownCustomTokenizer"}),
    ("tokenizer_config.json", {"auto_map": {"AutoTokenizer": ["malicious.Slow", "malicious.Fast"]}}),
    ("tokenizer_config.json", {"trust_remote_code": True}),
    ("tokenizer_config.json", {"tokenizer_file": "../../SYNTHETIC_PRIVATE_OUTSIDE/tokenizer.json"}),
    ("tokenizer_config.json", {"vocab_file": "/SYNTHETIC_PRIVATE_OUTSIDE/spm.model"}),
    ("tokenizer_config.json", {"tokenizer_file": "https://synthetic.invalid/tokenizer.json"}),
    ("tokenizer_config.json", {"tokenizer_file": "tokenizer.json"}),
])
def test_default_factory_rejects_rehashed_remote_architecture_and_path_config_before_loading(tmp_path, complete_manifest, monkeypatch, name, patch):
    root, manifest = strict_local_package(tmp_path, complete_manifest, mutation=(name, patch))
    called = []
    def loader(*args, **kwargs):
        called.append(True)
        return Model()
    fake_library(monkeypatch, loader)
    with pytest.raises(ModelBackendError) as failure:
        load_gliner(root, manifest)
    assert not called
    assert "SYNTHETIC_PRIVATE_OUTSIDE" not in str(failure.value)


@pytest.mark.parametrize("auxiliary", ["labels_tokenizer", "decoder_tokenizer"])
def test_default_m0_factory_rejects_auxiliary_tokenizer_autodiscovery(tmp_path, complete_manifest, monkeypatch, auxiliary):
    root, manifest = strict_local_package(tmp_path, complete_manifest, auxiliary=auxiliary)
    called = []
    def loader(*args, **kwargs):
        called.append(True)
        return Model()
    fake_library(monkeypatch, loader)
    with pytest.raises(ModelBackendError):
        load_gliner(root, manifest)
    assert not called


def test_pinned_backbone_name_cannot_autoload_a_cwd_adapter_outside_the_package(tmp_path, complete_manifest, monkeypatch):
    root, manifest = strict_local_package(tmp_path, complete_manifest)
    outside = tmp_path / "microsoft" / "mdeberta-v3-base"
    outside.mkdir(parents=True)
    (outside / "adapter_config.json").write_text('{"synthetic": true}')
    monkeypatch.chdir(tmp_path)
    called = []
    def loader(*args, **kwargs):
        called.append(True)
        return Model()
    fake_library(monkeypatch, loader)
    with pytest.raises(ModelBackendError):
        load_gliner(root, manifest)
    assert not called
