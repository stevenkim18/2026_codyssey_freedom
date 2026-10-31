"""Gold-independent word splitters returning original-text codepoint offsets.

Every splitter is a pure function of the text: ``split(text) -> [(token, start,
end)]`` with exclusive ends and ``token == text[start:end]``. No normalization
is applied. Gold spans, BIO tags, templates and document IDs are never inputs.

``whitespace_default_v1`` reproduces GLiNER's default splitter regex used by
B's M0 loader. The other splitters are *candidates*; nothing here is adopted by
B or used in production code.
"""
import re

SPLITTER_VERSIONS = {}

_DEFAULT = re.compile(r'\w+(?:[-_]\w+)*|\S')
_HANGUL = re.compile(r'[\uac00-\ud7a3]')
# Hangul/non-Hangul alternation inside one word. Digits, Latin, jamo and other
# word characters stay with their own script; only Hangul-syllable boundaries cut.
_SCRIPT = re.compile(r'[\uac00-\ud7a3]+|[^\uac00-\ud7a3]+')

# Frozen v1 Korean postposition/ending suffix list (hand-written, no external
# dictionary). Longest match wins. Single-syllable entries that are also very
# common word endings are deliberately omitted to limit over-splitting.
PARTICLES_V1 = tuple(sorted({
    '에게서', '으로부터', '로부터', '이라고', '에게는', '에게도', '한테서', '으로서', '으로써',
    '에서는', '에서도', '에서의', '이라는', '이라도', '이에요', '입니다', '이며', '이고', '이나',
    '이랑', '에게', '한테', '께서', '에서', '으로', '까지', '부터', '에는', '에도', '에의',
    '로서', '로써', '라고', '라는', '처럼', '보다', '마저', '조차', '밖에', '이다', '이야',
    '은', '는', '이', '가', '을', '를', '의', '에', '와', '과', '도', '만', '로', '께',
}, key=lambda s: (-len(s), s)))


# v2 adds honorific and copula/ending suffixes found as frequent residual cut
# points when diagnosing the *train* split (never dev/test), and strips suffixes
# repeatedly (at most MAX_STRIPS times), e.g. ``김가상님과`` -> 김가상/님/과.
EXTRA_SUFFIXES_V2 = (
    '님께서', '님께', '이시고', '이시며', '이군요', '이고요', '생인데', '이었던', '이신', '이시',
    '인데', '님', '인',
)
PARTICLES_V2 = tuple(sorted(set(PARTICLES_V1) | set(EXTRA_SUFFIXES_V2), key=lambda s: (-len(s), s)))
MAX_STRIPS = 3


def _check(text, tokens):
    previous = 0
    for token, start, end in tokens:
        if not previous <= start < end <= len(text) or text[start:end] != token or text[previous:start].strip():
            raise ValueError('invalid_segmentation')
        previous = end
    if text[previous:].strip():
        raise ValueError('invalid_segmentation')
    return tokens


def whitespace_default_v1(text):
    return _check(text, [(m.group(), m.start(), m.end()) for m in _DEFAULT.finditer(text)])


def _split_particle(piece, offset, particles):
    for particle in particles:
        if len(piece) > len(particle) and piece.endswith(particle):
            cut = len(piece) - len(particle)
            return [(piece[:cut], offset, offset + cut), (particle, offset + cut, offset + len(piece))]
    return [(piece, offset, offset + len(piece))]


def _split_repeated(piece, offset, particles, strips):
    tail = []
    for _ in range(strips):
        parts = _split_particle(piece, offset, particles)
        if len(parts) == 1:
            break
        (piece, offset, _), suffix = parts
        tail.append(suffix)
    return [(piece, offset, offset + len(piece))] + tail[::-1]


def _particle_tokens(text, base, particles, script_cut, strips=1):
    result = []
    for token, start, _ in base:
        pieces = [(m.group(), start + m.start()) for m in _SCRIPT.finditer(token)] if script_cut else [(token, start)]
        for piece, offset in pieces:
            if _HANGUL.fullmatch(piece[-1]) and all(_HANGUL.fullmatch(c) for c in piece):
                result.extend(_split_repeated(piece, offset, particles, strips))
            else:
                result.append((piece, offset, offset + len(piece)))
    return _check(text, result)


def ko_particle_v1(text):
    """Default tokens, then split a trailing particle from all-Hangul words."""
    return _particle_tokens(text, whitespace_default_v1(text), PARTICLES_V1, False)


def ko_script_particle_v1(text):
    """Default tokens, cut Hangul/non-Hangul boundaries, then split particles."""
    return _particle_tokens(text, whitespace_default_v1(text), PARTICLES_V1, True)


# v3: URLs and e-mail addresses become single words so that a whole URL (which
# otherwise splits into 15+ words and exceeds M0's 12-word span limit) stays a
# representable span. Pure ASCII patterns; trailing sentence punctuation is not
# part of the match because the last character must be URL-safe alphanumeric.
_ATOMIC = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]*[A-Za-z0-9/_=\-%#]"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")


def ko_atomic_suffix_v3(text):
    """v2 on the gaps between atomic URL/e-mail words, which stay one word each."""
    result, last = [], 0
    for match in _ATOMIC.finditer(text):
        result.extend(_gap_tokens(text, last, match.start()))
        result.append((match.group(), match.start(), match.end()))
        last = match.end()
    result.extend(_gap_tokens(text, last, len(text)))
    return _check(text, result)


def _gap_tokens(text, start, end):
    gap = text[start:end]
    return [(t, a + start, b + start) for t, a, b in ko_script_suffix_v2(gap)] if gap.strip() else []


def ko_script_suffix_v2(text):
    """v1 plus honorific/copula suffixes, stripped repeatedly (<= 3 times)."""
    return _particle_tokens(text, whitespace_default_v1(text), PARTICLES_V2, True, MAX_STRIPS)


SPLITTERS = {
    'whitespace_default_v1': whitespace_default_v1,
    'ko_particle_v1': ko_particle_v1,
    'ko_script_particle_v1': ko_script_particle_v1,
    'ko_script_suffix_v2': ko_script_suffix_v2,
    'ko_atomic_suffix_v3': ko_atomic_suffix_v3,
}
