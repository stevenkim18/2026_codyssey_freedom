"""Actual pinned tokenizer/processor planning, without model weights or inference.

Only B's word splitting, fit checks and window planner are reused. This adapter
does not construct a ModelPackage, claim model readiness or expose prediction.
Heavy dependencies are imported only when load_planner() is explicitly called.
"""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import importlib.metadata
import inspect
import os

from .manifest import MODEL_ROOT, strict_json

MAX_WORDS = 384
MAX_SPAN = 12
MAX_SUBTOKENS = 512
MAX_WINDOWS = 4096
MAX_PLAN_CHECKS = 2048
MAX_INPUT_CODEPOINTS = 16000


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_artifact(path, artifact):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('invalid_planner_artifact')
    data = path.read_bytes()
    if len(data) != artifact['size_bytes']:
        raise ValueError('invalid_planner_artifact')
    spec = artifact['digest']
    if spec['algorithm'] == 'sha256':
        actual = hashlib.sha256(data).hexdigest()
    elif spec['algorithm'] == 'git-blob-sha1':
        actual = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    else:
        raise ValueError('invalid_planner_artifact')
    if actual != spec['value']:
        raise ValueError('invalid_planner_artifact')


class _Planner:
    """Minimal adapter protocol required by B's existing planning methods."""

    def __init__(self, processor, config, labels, backend_type, metadata):
        self.processor = processor
        self.config = config
        self.labels = list(labels)
        self.max_words = MAX_WORDS
        self.max_span = MAX_SPAN
        self.max_subtokens = MAX_SUBTOKENS
        self.max_windows = MAX_WINDOWS
        self.max_plan_checks = MAX_PLAN_CHECKS
        self.metadata = metadata
        self._backend_type = backend_type
        # B's private helper protocol reads model.data_processor. This holder
        # contains only the actual processor; it is not a loaded/fake predictor.
        self.model = SimpleNamespace(data_processor=processor)

    @staticmethod
    def _check_text(text):
        if not isinstance(text, str):
            raise ValueError('invalid_planning_text')
        if len(text) > MAX_INPUT_CODEPOINTS:
            raise ValueError('planning_input_limit')

    def _tokens(self, text):
        return self._backend_type._tokens(self, text)

    def _fits(self, words):
        return self._backend_type._fits(self, words)

    def tokens(self, text):
        self._check_text(text)
        try:
            return self._tokens(text)
        except Exception:
            raise ValueError('planning_incomplete') from None

    def windows(self, text):
        self._check_text(text)
        try:
            return self._backend_type._plan(self, text, [self.max_plan_checks])
        except Exception:
            raise ValueError('planning_incomplete') from None


def load_planner(tokenizer_dir: Path, label_mapping: dict,
                 config_path: Path = MODEL_ROOT/'metadata/m0-gliner_config.json'):
    """Load verified local tokenizer assets and B-compatible planning only.

    Mapping insertion order is retained because label prompts affect the token
    budget. The caller decides the mapping's agreement/experimental status.
    """
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    try:
        supplied = Path(tokenizer_dir)
        if supplied.is_symlink() or not supplied.is_dir():
            raise ValueError('invalid_planner_artifact')
        root = supplied.resolve(strict=True)
        assets = strict_json((MODEL_ROOT/'configs/assets.lock.json').read_bytes())
        expected = {item['path']: item for item in assets['backbone']['artifacts']}
        for name in ('config.json', 'tokenizer_config.json', 'spm.model'):
            _verify_artifact(root/name, expected[name])
        model_config = next(item for item in assets['m0']['artifacts']
                            if item['path'] == 'gliner_config.json')
        _verify_artifact(config_path, model_config)
        configuration = strict_json(Path(config_path).read_bytes())
        if (configuration.get('max_len') != MAX_WORDS
                or configuration.get('max_width') != MAX_SPAN
                or configuration.get('model_name') != assets['backbone']['repo_id']
                or configuration.get('words_splitter_type', 'whitespace') != 'whitespace'):
            raise ValueError('unsupported_planner_configuration')

        import gliner
        import torch
        import transformers
        from gliner.config import GLiNERConfig
        from gliner.data_processing.processor import UniEncoderSpanProcessor
        from transformers import AutoTokenizer
        from dev.detector.gliner_backend import GLiNERBackend, MODEL_TYPES

        if gliner.__version__ != '0.2.29' or transformers.__version__ != '4.57.3':
            raise ValueError('unsupported_planner_library_version')
        if (not isinstance(label_mapping, dict) or set(label_mapping) != MODEL_TYPES
                or any(not isinstance(value, str) or not value or value != value.strip()
                       or len(value) > 256 for value in label_mapping.values())
                or len(set(label_mapping.values())) != len(label_mapping)):
            raise ValueError('invalid_planner_label_mapping')

        # Inline only the already verified local encoder configuration, as B's
        # prepared M0 package does. No encoder object or weights are constructed.
        configured = dict(configuration)
        configured['encoder_config'] = strict_json((root/'config.json').read_bytes())
        config = GLiNERConfig(**configured)
        tokenizer = AutoTokenizer.from_pretrained(
            str(root), local_files_only=True, trust_remote_code=False,
            model_max_length=MAX_SUBTOKENS,
        )
        tokenizer.add_tokens(['[FLERT]', config.ent_token, config.sep_token], special_tokens=True)
        processor = UniEncoderSpanProcessor(config, tokenizer, None)
        if (config.max_len != MAX_WORDS or config.max_width != MAX_SPAN
                or tokenizer.model_max_length != MAX_SUBTOKENS
                or type(tokenizer).__name__ != 'DebertaV2TokenizerFast'):
            raise ValueError('unsupported_planner_configuration')
        source = Path(inspect.getsourcefile(GLiNERBackend))
        labels = list(label_mapping.values())
        metadata = {
            'scope': 'Actual tokenizer/processor and B window planning only; no model weights, predictions, GPU or quality validation.',
            'gliner_version': gliner.__version__,
            'transformers_version': transformers.__version__,
            'torch_version': torch.__version__,
            'tokenizers_version': importlib.metadata.version('tokenizers'),
            'sentencepiece_version': importlib.metadata.version('sentencepiece'),
            'tokenizer_type': type(tokenizer).__name__,
            'processor_type': type(processor).__name__,
            'config_type': type(config).__name__,
            'b_helper_module': 'dev.detector.gliner_backend',
            'b_helper_source_sha256': _digest(source),
            'config_sha256': _digest(config_path),
            'tokenizer_source_files_sha256': {name: _digest(root/name)
                                            for name in ('config.json', 'tokenizer_config.json', 'spm.model')},
            'backbone_revision': assets['backbone']['revision'],
            'configuration_overrides': {'encoder_config_inlined': True, 'model_max_length': MAX_SUBTOKENS},
            'tokenizer_load_options': {'local_files_only': True, 'trust_remote_code': False},
            'added_special_tokens': ['[FLERT]', config.ent_token, config.sep_token],
            'label_order': list(label_mapping),
            'model_label_order': labels[:],
            'max_words': MAX_WORDS, 'max_span': MAX_SPAN, 'max_subtokens': MAX_SUBTOKENS,
            'max_windows': MAX_WINDOWS, 'max_plan_checks': MAX_PLAN_CHECKS,
            'max_input_codepoints': MAX_INPUT_CODEPOINTS,
            'normalization': 'none', 'offset_unit': 'unicode_codepoint',
            'model_weights_loaded': False, 'prediction_available': False,
        }
        return _Planner(processor, config, labels, GLiNERBackend, metadata)
    except Exception:
        # Native/tokenizer exceptions can contain original paths or text. The
        # CLI can report this code without emitting their untrusted messages.
        raise ValueError('planner_initialization_failed') from None
