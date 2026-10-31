"""Conservative local GLiNER adapter; unknown coverage never means clean text.

Synchronous inspect/warmup are intended for ModelManager's thread executor.
Coverage is relative to the loaded word splitter and model max_width, not a
claim that the model detects every PII entity or preserves long-range context.
"""
import importlib
import json
from pathlib import Path
import warnings
from typing import get_args

from .runtime import ModelPackage, _verify_package
from .schemas import BackendResult, Finding, PIIType, Segment


M0_REPO_ID = "urchade/gliner_multi_pii-v1"
M0_REVISION = "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"
GLINER_VERSION = "0.2.29"
MODEL_TYPES = frozenset(get_args(PIIType)) - {"API_KEY", "PRIVATE_KEY"}
PINNED_ENCODER_CONFIG = {
    "model_type": "deberta-v2", "attention_probs_dropout_prob": 0.1, "hidden_act": "gelu",
    "hidden_dropout_prob": 0.1, "hidden_size": 768, "initializer_range": 0.02, "intermediate_size": 3072,
    "max_position_embeddings": 512, "relative_attention": True, "position_buckets": 256,
    "norm_rel_ebd": "layer_norm", "share_att_key": True, "pos_att_type": "p2c|c2p", "layer_norm_eps": 1e-7,
    "max_relative_positions": -1, "position_biased_input": False, "num_attention_heads": 12,
    "num_hidden_layers": 12, "type_vocab_size": 0, "vocab_size": 251000,
}


class ModelBackendError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _CoverageError(Exception):
    pass


class GLiNERBackend:
    def __init__(self, model, manifest, *, max_windows=4096, max_plan_checks=2048):
        try:
            self.manifest = ModelPackage.model_validate(manifest).model_dump()
            if set(self.manifest["label_mapping"]) != MODEL_TYPES:
                raise ValueError
            self.model = model
            self.labels = list(self.manifest["label_mapping"].values())
            self.canonical = {label: kind for kind, label in self.manifest["label_mapping"].items()}
            self.thresholds = self.manifest["thresholds"]
            self.max_words = model.config.max_len
            self.max_span = model.config.max_width
            self.max_subtokens = model.data_processor.transformer_tokenizer.model_max_length
            if (any(type(value) is not int or value < 1 for value in (self.max_words, self.max_span, self.max_subtokens, max_windows, max_plan_checks))
                    or self.max_span > self.max_words or self.max_subtokens > 8192
                    or not callable(model.predict_entities) or not callable(model.eval)):
                raise ValueError
            self.max_windows = max_windows
            self.max_plan_checks = max_plan_checks
            self.warmed = False
        except Exception:
            raise ModelBackendError("invalid_gliner_backend") from None

    def _tokens(self, text):
        tokens = list(self.model.data_processor.words_splitter(text))
        previous = 0
        for token, start, end in tokens:
            if (not isinstance(token, str) or type(start) is not int or type(end) is not int
                    or not previous <= start < end <= len(text) or token != text[start:end]
                    or text[previous:start].strip()):
                raise _CoverageError
            previous = end
        if text[previous:].strip():
            raise _CoverageError
        return tokens

    def _fits(self, words):
        processor = self.model.data_processor
        prepared, prompts = processor.prepare_inputs([words], self.labels)
        if (len(prepared) != 1 or len(prompts) != 1 or type(prompts[0]) is not int
                or not 0 <= prompts[0] <= len(prepared[0])
                or prepared[0][prompts[0]:] != words):
            raise _CoverageError
        encoded = processor.transformer_tokenizer(prepared, is_split_into_words=True, truncation=False,
                                                  padding=False, return_attention_mask=False)
        ids = encoded["input_ids"]
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], list):
            raise _CoverageError
        word_ids = encoded.word_ids(batch_index=0)
        if (not isinstance(word_ids, list) or len(word_ids) != len(ids[0]) or not ids[0]
                or any(word is not None and (type(word) is not int or not 0 <= word < len(prepared[0])) for word in word_ids)):
            raise _CoverageError
        represented = {word for word in word_ids if word is not None}
        # Every model-facing word, including the label prompts, must survive.
        if not set(range(len(prepared[0]))) <= represented:
            raise _CoverageError
        return len(ids[0]) <= self.max_subtokens

    def _plan(self, text, checks_remaining):
        tokens = self._tokens(text)
        windows = []
        start = 0
        while start < len(tokens):
            def fits(candidate):
                if checks_remaining[0] <= 0:
                    raise _CoverageError
                checks_remaining[0] -= 1
                return self._fits([token[0] for token in tokens[start:candidate]])

            maximum = min(len(tokens), start + self.max_words)
            if fits(maximum):
                end = maximum
            else:
                # Only candidates actually checked as fitting are accepted.
                # Non-monotonic tokenizers may miss a usable larger prefix;
                # that can produce incomplete, never unchecked partial input.
                low, high, end = start + 1, maximum - 1, start
                while low <= high:
                    candidate = (low + high) // 2
                    if fits(candidate):
                        end = candidate
                        low = candidate + 1
                    else:
                        high = candidate - 1
            if end == start:
                raise _CoverageError
            begin_char, end_char = tokens[start][1], tokens[end - 1][2]
            window = text[begin_char:end_char]
            expected = [(token, a - begin_char, b - begin_char) for token, a, b in tokens[start:end]]
            if self._tokens(window) != expected:
                raise _CoverageError
            windows.append((begin_char, end_char))
            if len(windows) > self.max_windows:
                raise _CoverageError
            if end == len(tokens):
                break
            # A span of at most max_span words crossing this edge is wholly
            # present in one window when overlap is at least max_span - 1.
            next_start = end - self.max_span + 1
            if next_start <= start:
                raise _CoverageError
            start = next_start
        return windows

    def warmup(self):
        self.warmed = False
        try:
            self.model.eval()
            result = self._inspect([Segment(segment_id="warmup", text="warmup")])
            if result.status != "complete":
                raise ValueError
            self.warmed = True
        except Exception:
            raise ModelBackendError("gliner_warmup_failed") from None

    def inspect(self, segments):
        if not self.warmed:
            raise ModelBackendError("gliner_not_warmed")
        return self._inspect(segments)

    def _inspect(self, segments):
        try:
            parsed = [Segment.model_validate(segment) for segment in segments]
            if (not parsed or len({segment.segment_id for segment in parsed}) != len(parsed)
                    or sum(len(segment.text) for segment in parsed) > self.manifest["preprocessing"]["max_input_codepoints"]):
                raise ValueError
            try:
                checks_remaining = [self.max_plan_checks]
                plans = [(segment, self._plan(segment.text, checks_remaining)) for segment in parsed]
                if sum(len(windows) for _, windows in plans) > self.max_windows:
                    raise _CoverageError
            except Exception:
                return BackendResult(status="incomplete")
            findings = {}
            for segment, windows in plans:
                for start, end in windows:
                    text = segment.text[start:end]
                    with warnings.catch_warnings(record=True) as emitted:
                        warnings.simplefilter("always")
                        raw = self.model.predict_entities(text, self.labels[:], flat_ner=False, multi_label=True,
                                                          threshold=min(self.thresholds.values()))
                    if any("truncat" in str(warning.message).lower() for warning in emitted):
                        raise ValueError
                    if not isinstance(raw, list):
                        raise ValueError
                    for entity in raw:
                        kind = self.canonical[entity["label"]]
                        finding = Finding(segment_id=segment.segment_id, type=kind, start=entity["start"],
                                          end=entity["end"], score=entity["score"], detector="model")
                        if finding.end > len(text) or entity["text"] != text[finding.start:finding.end]:
                            raise ValueError
                        if finding.score < self.thresholds[kind]:
                            continue
                        finding.start += start
                        finding.end += start
                        key = (finding.segment_id, finding.start, finding.end, finding.type)
                        if key not in findings or finding.score > findings[key].score:
                            findings[key] = finding
            return BackendResult(findings=list(findings.values()))
        except Exception:
            raise ModelBackendError("gliner_inference_failed") from None


def _guard_builtin_m0(root, checked):
    documents = {name: json.loads((root / name).read_text())
                 for name in ("gliner_config.json", "config.json", "tokenizer_config.json")}
    config, encoder, tokenizer = (documents[name] for name in ("gliner_config.json", "config.json", "tokenizer_config.json"))
    allowed = {"size_sup", "max_types", "shuffle_types", "random_drop", "max_neg_type_ratio", "max_len",
               "lr_encoder", "lr_others", "num_steps", "warmup_ratio", "train_batch_size", "eval_every", "max_width",
               "model_name", "fine_tune", "subtoken_pooling", "hidden_size", "span_mode", "dropout", "name", "encoder_config"}
    if (not isinstance(config, dict) or set(config) - allowed
            or any(config.get(key, expected) != expected for key, expected in (("hidden_size", 512), ("max_width", 12), ("max_len", 384)))
            or config.get("model_name") != "microsoft/mdeberta-v3-base"
            or config.get("span_mode") != "markerV0" or config.get("encoder_config") != encoder
            or encoder != PINNED_ENCODER_CONFIG
            or not isinstance(tokenizer, dict) or tokenizer.get("tokenizer_class") not in {"DebertaV2Tokenizer", "DebertaV2TokenizerFast"}
            or tokenizer.get("model_max_length") != encoder.get("max_position_embeddings")
            or type(tokenizer.get("model_max_length")) is not int or not 1 <= tokenizer["model_max_length"] <= 8192
            or Path(config["model_name"]).exists()
            or any((root / name).exists() for name in ("labels_tokenizer", "decoder_tokenizer", "adapter_config.json"))
            or any(Path(name).suffix == ".py" for name in checked["files"])):
        raise ValueError

    def guard(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"auto_map", "trust_remote_code", "architectures", "attn_implementation", "_attn_implementation", "kernel_config"}:
                    raise ValueError
                if key == "tokenizer_class" and item not in {"DebertaV2Tokenizer", "DebertaV2TokenizerFast"}:
                    raise ValueError
                if key == "model_type" and item != "deberta-v2":
                    raise ValueError
                if key.endswith(("_file", "_path", "_name_or_path")) and item is not None:
                    # Even a basename may be interpreted relative to CWD by a
                    # tokenizer constructor. Rely only on resolved package files.
                    raise ValueError
                guard(item)
        elif isinstance(value, list):
            for item in value:
                guard(item)
    for document in documents.values():
        guard(document)


def load_gliner(package_dir, manifest, *, loader=None, device="cpu"):
    """Load a verified M0 package locally; injected loader is for offline tests.

The default GLiNER pin uses weights_only=True for .bin deserialization. A
bundled tokenizer_config.json is mandatory to prevent tokenizer Hub fallback.
Actual backbone/config cache independence requires the separate cold-start gate.
"""
    try:
        root, checked = _verify_package(package_dir)
        if checked != ModelPackage.model_validate(manifest).model_dump():
            raise ValueError
        if checked["model"] != {"repo_id": M0_REPO_ID, "revision": M0_REVISION}:
            raise ValueError
        if device not in {"cpu", "cuda"} or not (root / "tokenizer_config.json").is_file():
            raise ValueError
        _guard_builtin_m0(root, checked)
        options = {"local_files_only": True, "strict": True, "map_location": device, "load_tokenizer": True}
        if loader is None:
            library = importlib.import_module("gliner")
            if library.__version__ != GLINER_VERSION:
                raise ValueError
            loader = library.GLiNER.from_pretrained
        else:
            # Explicit test-loader interface; GLiNER itself does not accept this
            # option at its model constructor boundary. Builtin config guards
            # above keep its default tokenizer/config path non-custom and local.
            options["trust_remote_code"] = False
        model = loader(str(root), **options)
        return GLiNERBackend(model, checked)
    except Exception:
        raise ModelBackendError("gliner_load_failed") from None
