"""Window-edge regression independent of frozen service examples or gold."""
import pytest

from dev.detector.gliner_backend import GLiNERBackend, _CoverageError
from model.segmentation.coverage_backend import CoverageBackend
from model.segmentation.splitters import ko_atomic_suffix_v3


def backend():
    value=object.__new__(CoverageBackend)
    value._tokens=ko_atomic_suffix_v3
    value.max_words=12;value.max_span=3;value.max_windows=4096
    value._fits=lambda words:len(words)<=value.max_words
    return value


def test_context_sensitive_suffix_at_window_edge_preserves_words_and_overlap():
    value=backend();text=('첫째 가나님께서에게는 확인합니다. ')*12
    with pytest.raises(_CoverageError):GLiNERBackend._plan(value,text,[2048])
    tokens=value._tokens(text);windows=value._plan(text,[2048]);previous_end=None;covered=set()
    for a,b in windows:
        original=[(word,c-a,d-a) for word,c,d in tokens if a<=c<d<=b]
        assert value._tokens(text[a:b])==original
        assert len(original)<=value.max_words
        if previous_end is not None:
            assert sum(a<=c<d<=previous_end for _,c,d in tokens)>=value.max_span-1
        previous_end=b;covered.update(range(a,b))
    assert all(i in covered for i,char in enumerate(text) if not char.isspace())


def test_fallback_does_not_accept_exhausted_budget():
    with pytest.raises(_CoverageError):backend()._plan(('첫째 가나님께서에게는 확인합니다. ')*12,[0])


def test_short_input_retains_original_plan():
    value=backend();text='서울님과 함께 확인합니다.'
    assert value._plan(text,[2048])==GLiNERBackend._plan(value,text,[2048])
