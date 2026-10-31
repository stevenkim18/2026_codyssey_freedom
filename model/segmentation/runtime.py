"""Explicit offline experiment adapter; does not change B's production loader."""
from .splitters import SPLITTERS


class VersionedWordsSplitter:
    def __init__(self, splitter_id):
        if splitter_id not in SPLITTERS:
            raise ValueError('unknown_splitter')
        self.splitter_id = splitter_id
        self.version = 'a-seg-candidate-' + splitter_id

    def __call__(self, text):
        return iter(SPLITTERS[self.splitter_id](text))


def attach_splitter(model, splitter_id):
    """Attach before backend planning/warmup AND GLiNER.predict_entities.

    Call this again after every model reload. Persist the splitter ID in the
    experiment/package sidecar; model.save_pretrained does not save this object.
    B's adoption remains a separate explicit integration decision.
    """
    splitter = VersionedWordsSplitter(splitter_id)
    model.data_processor.words_splitter = splitter
    return splitter
