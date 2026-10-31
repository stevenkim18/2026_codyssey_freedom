"""Independent synthetic R03/R07/R10/R11 GLiNER and startup fault probes.

No actual GLiNER library, model weights, external service, or GPU is used.
"""
from copy import copy, deepcopy
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from detector.gliner_backend import GLiNERBackend, MODEL_TYPES, ModelBackendError
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
