"""Offline actual-tokenizer planning probe, WITHOUT model weights or inference.

Run from dev: python -m integration.verify_m0_tokenizer PACKAGE_DIRECTORY
The fake predictor only records planned windows; complete is NOT a clean verdict.
"""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import resource
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
import warnings


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "invalid_cli_arguments\n")


def _require(condition, code):
    if not condition:
        raise AssertionError(code)


def check_expected_status(name, status):
    expected = {
        "korean_particles": "complete", "emoji_zwj": "complete",
        "combining": "complete", "decomposed_hangul": "complete",
        "16000_codepoints": "complete", "multi_segment_16000": "complete",
        "word_boundaries": "complete", "long_single_word": "incomplete",
        "over_limit_16001": "rejected_input_limit",
    }
    _require(type(name) is str and name in expected and status == expected[name], "unexpected_probe_status")


def check_coverage(status, texts, windows, prediction_calls, fit_checks):
    """Independent original-codepoint oracle, active even under Python -O."""
    try:
        _require(type(fit_checks) is int and 0 <= fit_checks <= 2048, "invalid_fit_checks")
        _require(type(prediction_calls) is int and prediction_calls >= 0, "invalid_prediction_calls")
        _require(status in {"complete", "incomplete"}, "invalid_probe_status")
        _require(isinstance(texts, list) and texts and all(isinstance(text, str) for text in texts), "invalid_probe_texts")
        _require(isinstance(windows, list), "invalid_probe_windows")
        if status == "incomplete":
            _require(prediction_calls == 0, "prediction_before_full_planning")
            return
        _require(len(texts) == len(windows), "invalid_window_count")
        _require(prediction_calls == sum(map(len, windows)), "invalid_prediction_count")
        for text, spans in zip(texts, windows, strict=True):
            covered = set()
            for start, end in spans:
                _require(type(start) is int and type(end) is int and 0 <= start < end <= len(text), "invalid_window_span")
                covered.update(range(start, end))
            _require(all(char.isspace() or index in covered for index, char in enumerate(text)), "nonwhite_codepoint_omitted")
    except (TypeError, ValueError, KeyError, IndexError):
        raise AssertionError("invalid_probe_record") from None


def run(package):
    """Process-scoped probe; do not run concurrently with other networking work."""
    environment = os.environ.copy()
    sockets = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection)
    try:
        return _run(package)
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = sockets
        os.environ.clear()
        os.environ.update(environment)


def _run(package):
    started = time.monotonic()
    # A fresh directory is preserved for inspection; do not touch existing caches.
    hf_home = tempfile.mkdtemp(prefix="m0-tokenizer-offline-")
    os.environ.update(HF_HOME=hf_home, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                      MPLCONFIGDIR=str(Path(hf_home) / "mpl"), XDG_CACHE_HOME=str(Path(hf_home) / "xdg"))
    attempts = []

    def deny(*args, **kwargs):
        attempts.append("connection_attempt")
        raise RuntimeError("network denied by offline tokenizer probe")

    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    import gliner
    import transformers
    from gliner.config import GLiNERConfig
    from gliner.data_processing.processor import UniEncoderSpanProcessor
    from transformers import AutoTokenizer, DebertaV2Tokenizer
    from detector.gliner_backend import GLiNERBackend, ModelBackendError, _guard_builtin_m0
    from detector.runtime import _verify_package

    _require(gliner.__version__ == "0.2.29", "unsupported_gliner_version")
    _require(transformers.__version__ == "4.57.3", "unsupported_transformers_version")
    root, manifest = _verify_package(package)
    _guard_builtin_m0(root, manifest)
    config = GLiNERConfig(**json.loads((root / "gliner_config.json").read_text()))
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
    # Same special tokens/order as BaseGLiNER._get_special_tokens and resize path.
    tokenizer.add_tokens(["[FLERT]", config.ent_token, config.sep_token], special_tokens=True)
    processor = UniEncoderSpanProcessor(config, tokenizer, None)
    slow_tokenizer = DebertaV2Tokenizer.from_pretrained(str(root), local_files_only=True)
    calls = []

    def predict(text, *args, **kwargs):
        calls.append(text)
        return []

    model = SimpleNamespace(config=config, data_processor=processor, eval=lambda: None, predict_entities=predict)
    backend = GLiNERBackend(model, manifest)
    backend.warmup()  # Actual planner, fake prediction: no neural warmup.
    checks = [0]
    plans = []
    original_fit, original_plan = backend._fits, backend._plan

    def counted(words):
        checks[0] += 1
        return original_fit(words)

    def recorded(text, remaining):
        spans = original_plan(text, remaining)
        plans.append(spans)
        return spans

    backend._fits, backend._plan = counted, recorded
    cases = {
        "korean_particles": ["홍길동에게 김민수의 연락처를 전달했다."],
        "emoji_zwj": ["김민수 👩‍💻🏳️‍🌈😀 연락처"],
        "combining": ["Jose\u0301 e\u0301 A\u030a 담당자"],
        "decomposed_hangul": ["홍길동에게 연락"],
        "long_single_word": ["가나다라마바사" * 2000],
        "16000_codepoints": [("홍길동에게 연락처 01012345678 😀 " * 700)[:16000]],
        "multi_segment_16000": [("김민수 연락 " * 1200)[:8000], ("서울 전화 " * 1400)[:8000]],
        "word_boundaries": [" ".join(f"word{n}" for n in range(900))],
        "over_limit_16001": ["가 " * 8000 + "나"],
    }
    results = []
    for name, texts in cases.items():
        calls.clear()
        plans.clear()
        checks[0] = 0
        begin = time.monotonic()
        try:
            result = backend.inspect([{"segment_id": str(n), "text": text} for n, text in enumerate(texts)])
            status = result.status
            check_coverage(status, texts, plans, len(calls), checks[0])
            if status == "complete":
                # Independent span containment check at every possible boundary.
                for text, spans in zip(texts, plans, strict=True):
                    words = list(processor.words_splitter(text))
                    for index in range(len(words)):
                        span_end = words[min(index + config.max_width, len(words)) - 1][2]
                        _require(any(start <= words[index][1] and end >= span_end for start, end in spans), "boundary_span_omitted")
        except ModelBackendError:
            _require(name == "over_limit_16001" and not calls, "unexpected_model_error")
            status = "rejected_input_limit"
        check_expected_status(name, status)
        # Probe word-level unknowns separately; this is NOT a quality score.
        encoded = tokenizer(texts, truncation=False)
        slow_encoded = slow_tokenizer(texts, truncation=False)
        results.append({"case": name, "codepoints": sum(map(len, texts)), "status": status,
                        "windows": sum(map(len, plans)), "fake_prediction_calls": len(calls), "fit_checks": checks[0],
                        "unk_tokens": sum(ids.count(tokenizer.unk_token_id) for ids in encoded["input_ids"]),
                        "slow_unk_tokens": sum(ids.count(slow_tokenizer.unk_token_id) for ids in slow_encoded["input_ids"]),
                        "seconds": round(time.monotonic() - begin, 4)})
    _require(not attempts, "network_attempt_detected")
    return {"scope": "actual tokenizer and processor; planning only; NO model inference/quality/GPU", "package": str(root),
            "python": sys.executable, "gliner": gliner.__version__, "gliner_path": gliner.__file__,
            "transformers": transformers.__version__, "transformers_path": transformers.__file__,
            "tokenizer": type(tokenizer).__name__, "processor": type(processor).__name__, "hf_home": hf_home,
            "network_attempts": len(attempts), "warnings": [str(w.message) for w in emitted], "cases": results,
            "seconds": round(time.monotonic() - started, 4), "max_rss_native": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


if __name__ == "__main__":
    parser = SafeParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, help="new JSON evidence file (native libraries may also write stdout)")
    arguments = parser.parse_args()
    with redirect_stdout(sys.stderr):
        report = run(arguments.package)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if arguments.output:
        with arguments.output.open("x") as destination:
            destination.write(serialized + "\n")
    print(serialized)
