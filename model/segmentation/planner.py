"""Plug a gold-independent splitter into B's unchanged window planner.

B's ``GLiNERBackend._plan``/``_fits`` are reused verbatim; only the word
splitting step (``_tokens``) is replaced by a candidate splitter that sees
original text only. B's runtime code is not modified.
"""
from ..data.gliner_planner import _Planner
from .splitters import SPLITTERS

PREPROCESSING_VERSION_PREFIX = 'a-seg-candidate-'


class SplitterPlanner(_Planner):
    def __init__(self, base, splitter_id):
        if splitter_id not in SPLITTERS:
            raise ValueError('unknown_splitter')
        super().__init__(base.processor, base.config, base.labels, base._backend_type, dict(base.metadata))
        self.splitter_id = splitter_id
        self._splitter = SPLITTERS[splitter_id]
        self.metadata.update({
            'splitter_id': splitter_id,
            'preprocessing_version': PREPROCESSING_VERSION_PREFIX + splitter_id,
            'splitter_input': 'original text only',
            'scope': base.metadata['scope'] + ' Candidate splitter ' + splitter_id + ' replaces only word splitting.',
        })

    def _tokens(self, text):
        tokens = list(self._splitter(text))
        previous = 0
        for token, start, end in tokens:
            if (not isinstance(token, str) or type(start) is not int or type(end) is not int
                    or not previous <= start < end <= len(text) or text[start:end] != token
                    or text[previous:start].strip()):
                raise ValueError('invalid_token_coverage')
            previous = end
        if text[previous:].strip():
            raise ValueError('invalid_token_coverage')
        return tokens


def load_candidate_planner(tokenizer_dir, label_mapping, splitter_id):
    """Load the pinned tokenizer/processor and attach the named splitter."""
    from ..data.gliner_planner import load_planner
    return SplitterPlanner(load_planner(tokenizer_dir, label_mapping), splitter_id)

