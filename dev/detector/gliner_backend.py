"""Conservative local GLiNER adapter; unknown coverage never means clean text.

Synchronous inspect/warmup are intended for ModelManager's thread executor.
Coverage is relative to the loaded word splitter and model max_width, not a
claim that the model detects every PII entity or preserves long-range context.
"""
import importlib
import json
from pathlib import Path
import re
import warnings
from typing import get_args
from packaging.version import Version

from . import timing as stage_timing
from .runtime import ModelPackage, _verify_package
from .schemas import BackendResult, Finding, PIIType, Segment


M0_REPO_ID = "urchade/gliner_multi_pii-v1"
M0_REVISION = "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"
M1_REVISION = "ed2546b8b6a721be18a6a9e644fe6fd423016c7a"
ALLOWED_MODEL_IDENTITIES = frozenset({
    (M0_REPO_ID, M0_REVISION),
    (M0_REPO_ID, M1_REVISION),
})
GLINER_VERSION = "0.2.29"
MIN_TORCH_VERSION = Version("2.10.0")
MODEL_TYPES = frozenset(get_args(PIIType)) - {"API_KEY", "PRIVATE_KEY"}
PINNED_ENCODER_CONFIG = {
    "model_type": "deberta-v2", "attention_probs_dropout_prob": 0.1, "hidden_act": "gelu",
    "hidden_dropout_prob": 0.1, "hidden_size": 768, "initializer_range": 0.02, "intermediate_size": 3072,
    "max_position_embeddings": 512, "relative_attention": True, "position_buckets": 256,
    "norm_rel_ebd": "layer_norm", "share_att_key": True, "pos_att_type": "p2c|c2p", "layer_norm_eps": 1e-7,
    "max_relative_positions": -1, "position_biased_input": False, "num_attention_heads": 12,
    "num_hidden_layers": 12, "type_vocab_size": 0, "vocab_size": 251000,
}
PINNED_M1_ENCODER_CONFIG = PINNED_ENCODER_CONFIG | {"vocab_size": 250105}
KOREAN_PREPROCESSING_VERSIONS = frozenset({"ko_atomic_suffix_v3", "a-seg-candidate-ko_atomic_suffix_v3"})
GUARDED_PREPROCESSING_VERSIONS = frozenset({"ko_atomic_suffix_v3"})

_DEFAULT_WORD_RE = re.compile(r"\w+(?:[-_]\w+)*|\S")
_HANGUL_CHAR_RE = re.compile(r"[\uac00-\ud7a3]")
_SCRIPT_RUN_RE = re.compile(r"[\uac00-\ud7a3]+|[^\uac00-\ud7a3]+")
_ATOMIC_PII_RE = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*[A-Za-z0-9/_=\-%#]"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+"
)
_PARTICLES_V2 = tuple(sorted({
    "에게서", "으로부터", "로부터", "이라고", "에게는", "에게도", "한테서", "으로서", "으로써",
    "에서는", "에서도", "에서의", "이라는", "이라도", "이에요", "입니다", "이며", "이고", "이나",
    "이랑", "에게", "한테", "께서", "에서", "으로", "까지", "부터", "에는", "에도", "에의",
    "로서", "로써", "라고", "라는", "처럼", "보다", "마저", "조차", "밖에", "이다", "이야",
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만", "로", "께",
    "님께서", "님께", "이시고", "이시며", "이군요", "이고요", "생인데", "이었던", "이신", "이시",
    "인데", "님", "인",
}, key=lambda s: (-len(s), s)))
_KR_RRN_PATTERN = re.compile(r"(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[- ]?([1-8]\d{6})")
_REDACTED_LITERAL = "[REDACTED]"


def _split_particle_once(piece, offset):
    for particle in _PARTICLES_V2:
        if len(piece) > len(particle) and piece.endswith(particle):
            cut = len(piece) - len(particle)
            return [(piece[:cut], offset, offset + cut), (particle, offset + cut, offset + len(piece))]
    return [(piece, offset, offset + len(piece))]


def _split_hangul_suffixes(piece, offset, strips=3):
    tail = []
    for _ in range(strips):
        parts = _split_particle_once(piece, offset)
        if len(parts) == 1:
            break
        (piece, offset, _), suffix = parts
        tail.append(suffix)
    return [(piece, offset, offset + len(piece))] + tail[::-1]


def _ko_script_suffix_v2(text):
    result = []
    for match in _DEFAULT_WORD_RE.finditer(text):
        token, start = match.group(), match.start()
        for sub in _SCRIPT_RUN_RE.finditer(token):
            piece, offset = sub.group(), start + sub.start()
            if _HANGUL_CHAR_RE.fullmatch(piece[-1]) and all(_HANGUL_CHAR_RE.fullmatch(c) for c in piece):
                result.extend(_split_hangul_suffixes(piece, offset))
            else:
                result.append((piece, offset, offset + len(piece)))
    return result


def ko_atomic_suffix_v3(text):
    """Pure gold-independent Korean word splitter shared between A and B."""
    result, last = [], 0
    for match in _ATOMIC_PII_RE.finditer(text):
        gap = text[last:match.start()]
        if gap.strip():
            result.extend((t, a + last, b + last) for t, a, b in _ko_script_suffix_v2(gap))
        result.append((match.group(), match.start(), match.end()))
        last = match.end()
    tail = text[last:]
    if tail.strip():
        result.extend((t, a + last, b + last) for t, a, b in _ko_script_suffix_v2(tail))
    return result


class _KoreanWordsSplitter:
    version = "ko_atomic_suffix_v3"

    def __call__(self, text):
        return iter(ko_atomic_suffix_v3(text))


def _is_valid_kr_rrn(text, start, end):
    span = text[start:end]
    match = _KR_RRN_PATTERN.fullmatch(span)
    if not match:
        return False
    month, day = int(match.group(2)), int(match.group(3))
    if (month in {4, 6, 9, 11} and day > 30) or (month == 2 and day > 29):
        return False
    if start > 0 and text[start - 1].isascii() and (text[start - 1].isalnum() or text[start - 1] in "-_"):
        return False
    if end < len(text) and text[end].isascii() and (text[end].isalnum() or text[end] in "-_"):
        return False
    return True


def _overlaps_redacted_placeholder(text, start, end):
    if _REDACTED_LITERAL not in text:
        return False
    pos = text.find(_REDACTED_LITERAL)
    while pos != -1:
        p_end = pos + len(_REDACTED_LITERAL)
        if start < p_end and end > pos:
            return True
        pos = text.find(_REDACTED_LITERAL, pos + 1)
    return False


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
            prep_version = self.manifest["preprocessing"]["version"]
            self.korean_preprocessing = prep_version in KOREAN_PREPROCESSING_VERSIONS
            self.korean_guards = prep_version in GUARDED_PREPROCESSING_VERSIONS
            if self.korean_preprocessing:
                current_splitter = getattr(model.data_processor, "words_splitter", None)
                if not hasattr(current_splitter, "version"):
                    model.data_processor.words_splitter = _KoreanWordsSplitter()
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

    def _primary_plan(self, text, checks_remaining):
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

    def _whole_group_plan(self, text, checks_remaining):
        tokens = self._tokens(text)
        if not tokens:
            return []
        ends = {m.end() for m in re.finditer(r"\S+", text)}
        boundaries = [0] + [i + 1 for i, token in enumerate(tokens) if token[2] in ends]
        if boundaries[-1] != len(tokens):
            raise _CoverageError
        windows = []
        start = 0
        while start < len(tokens):
            options = [e for e in boundaries if start < e <= start + self.max_words]
            best = None
            low, high = 0, len(options) - 1
            while low <= high:
                index = (low + high) // 2
                end = options[index]
                if checks_remaining[0] <= 0:
                    raise _CoverageError
                checks_remaining[0] -= 1
                begin_char, end_char = tokens[start][1], tokens[end - 1][2]
                expected = [(word, c - begin_char, d - begin_char) for word, c, d in tokens[start:end]]
                fits = self._tokens(text[begin_char:end_char]) == expected and self._fits([t[0] for t in tokens[start:end]])
                if fits:
                    best = end
                    low = index + 1
                else:
                    high = index - 1
            if best is None:
                raise _CoverageError
            windows.append((tokens[start][1], tokens[best - 1][2]))
            if len(windows) > self.max_windows:
                raise _CoverageError
            if best == len(tokens):
                break
            limit = best - self.max_span + 1
            choices = [b for b in boundaries if start < b <= limit]
            if not choices:
                raise _CoverageError
            start = max(choices)
        return windows

    def _plan(self, text, checks_remaining):
        try:
            return self._primary_plan(text, checks_remaining)
        except _CoverageError:
            if not getattr(self, "korean_preprocessing", False):
                raise
            return self._whole_group_plan(text, checks_remaining)

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
            with stage_timing.stage("preprocessing"):
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
                    with stage_timing.stage("model_call"):
                        with warnings.catch_warnings(record=True) as emitted:
                            warnings.simplefilter("always")
                            raw = self.model.predict_entities(text, self.labels[:], flat_ner=False, multi_label=True,
                                                              threshold=min(self.thresholds.values()))
                    with stage_timing.stage("postprocessing"):
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
                            if getattr(self, "korean_guards", False):
                                if kind == "KR_RRN" and not _is_valid_kr_rrn(segment.text, finding.start, finding.end):
                                    continue
                                if _overlaps_redacted_placeholder(segment.text, finding.start, finding.end):
                                    continue
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
               "model_name", "fine_tune", "subtoken_pooling", "hidden_size", "span_mode", "dropout", "name", "encoder_config",
               "class_token_index", "embed_ent_token", "ent_token", "fuse_layers", "id_to_classes", "model_type",
               "neg_spans_ratio", "num_post_fusion_layers", "num_rnn_layers", "post_fusion_schema",
               "precomputed_prompts_mode", "represent_spans", "sep_token", "span_encoder_config",
               "span_loss_coef", "token_loss_coef", "transformers_version", "vocab_size", "words_splitter_type"}
    if (not isinstance(config, dict) or set(config) - allowed
            or any(config.get(key, expected) != expected for key, expected in (("hidden_size", 512), ("max_width", 12), ("max_len", 384)))
            or config.get("model_type") is not None
            or config.get("span_encoder_config") is not None
            or config.get("precomputed_prompts_mode") is not None
            or config.get("model_name") != "microsoft/mdeberta-v3-base"
            or config.get("span_mode") != "markerV0" or config.get("encoder_config") != encoder
            or encoder not in (PINNED_ENCODER_CONFIG, PINNED_M1_ENCODER_CONFIG)
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
                if key == "model_type" and item is not None and item != "deberta-v2":
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
    """Load a verified M0/M1 package locally; injected loader is for offline tests.

The default GLiNER pin uses weights_only=True for .bin deserialization and
requires a stable PyTorch >=2.10.0 (GHSA-63cw-57p8-fm3p) before loading weights. A
bundled tokenizer_config.json is mandatory to prevent tokenizer Hub fallback.
Actual backbone/config cache independence requires the separate cold-start gate.
"""
    try:
        root, checked = _verify_package(package_dir)
        if checked != ModelPackage.model_validate(manifest).model_dump():
            raise ValueError
        identity = (checked["model"].get("repo_id"), checked["model"].get("revision"))
        if identity not in ALLOWED_MODEL_IDENTITIES:
            raise ValueError
        if device not in {"cpu", "cuda"} or not (root / "tokenizer_config.json").is_file():
            raise ValueError
        _guard_builtin_m0(root, checked)
        options = {"local_files_only": True, "strict": True, "map_location": device, "load_tokenizer": True}
        if loader is None:
            library = importlib.import_module("gliner")
            if library.__version__ != GLINER_VERSION:
                raise ValueError
            torch = importlib.import_module("torch")
            if not isinstance(torch.__version__, str):
                raise ValueError
            torch_version = Version(torch.__version__)
            if (torch_version.epoch != 0 or torch_version.is_prerelease
                    or torch_version < MIN_TORCH_VERSION):
                raise ValueError
            loader = library.GLiNER.from_pretrained
        else:
            # Explicit test-loader interface; GLiNER itself does not accept this
            # option at its model constructor boundary. Builtin config guards
            # above keep its default tokenizer/config path non-custom and local.
            options["trust_remote_code"] = False
        if device == "cuda":
            torch = importlib.import_module("torch")
            if not torch.cuda.is_available():
                raise ValueError
            selected_index = torch.cuda.current_device()
        model = loader(str(root), **options)
        if device == "cuda":
            # GLiNER 0.2.29 moves its core module after loading weights. Check
            # actual tensors, not map_location or a model.device declaration.
            parameters = list(model.parameters())
            buffers = list(model.buffers())
            if not parameters or any(tensor.device.type != "cuda" or tensor.device.index != selected_index
                                     for tensor in parameters + buffers):
                raise ValueError
        return GLiNERBackend(model, checked)
    except Exception:
        raise ModelBackendError("gliner_load_failed") from None
