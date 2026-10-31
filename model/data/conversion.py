"""Exact original-character to GLiNER word-span conversion, without gold repair."""


class ConversionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _tokens(planner, text):
    try:
        tokens = list(planner.tokens(text))
        previous = 0
        for token, start, end in tokens:
            if (not isinstance(token, str) or type(start) is not int or type(end) is not int
                    or not previous <= start < end <= len(text) or text[start:end] != token
                    or text[previous:start].strip()):
                raise ValueError
            previous = end
        if text[previous:].strip():
            raise ValueError
        return tokens
    except Exception:
        raise ConversionError('invalid_token_coverage') from None


def convert_record(row, planner, label_mapping):
    """Return all valid windows or raise; no partial document success is emitted.

    Input character ends are exclusive. GLiNER `ner` word ends are inclusive.
    Full source text and every gold span must survive without normalization,
    expanding to token edges, copying old BIO tokens or gold-aware token splits.
    """
    if (not isinstance(label_mapping, dict) or not label_mapping
            or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v.strip()
                   for k, v in label_mapping.items())
            or len(set(label_mapping.values())) != len(label_mapping)):
        raise ConversionError('invalid_label_mapping')
    if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not isinstance(row.get('entities'), list):
        raise ConversionError('invalid_record_schema')
    text = row['text']
    if len(text) > 16000:
        raise ConversionError('input_codepoint_limit')
    if type(getattr(planner, 'max_span', None)) is not int or planner.max_span < 1:
        raise ConversionError('invalid_planner')
    tokens = _tokens(planner, text)
    if not tokens:
        raise ConversionError('empty_token_sequence')
    starts = {a: i for i, (_, a, _) in enumerate(tokens)}
    ends = {b: i for i, (_, _, b) in enumerate(tokens)}
    mapped = []
    for entity in row['entities']:
        if (not isinstance(entity, dict) or not isinstance(entity.get('type'), str)
                or entity['type'] not in label_mapping):
            raise ConversionError('unknown_entity_type')
        a, b = entity.get('start'), entity.get('end')
        if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
                or entity.get('surface') != text[a:b]):
            raise ConversionError('invalid_gold_span')
        if a not in starts or b not in ends:
            raise ConversionError('unaligned_entity_boundary')
        left, right = starts[a], ends[b]
        if right - left + 1 > planner.max_span:
            raise ConversionError('entity_exceeds_max_width')
        mapped.append((left, right, label_mapping[entity['type']]))
    try:
        windows = list(planner.windows(text))
    except Exception:
        raise ConversionError('planning_incomplete') from None
    examples, ledger, covered_gold, covered_words = [], [], set(), set()
    previous_start = -1
    for span in windows:
        if (not isinstance(span, (list, tuple)) or len(span) != 2
                or any(type(x) is not int for x in span)):
            raise ConversionError('invalid_window')
        a, b = span
        if a not in starts or b not in ends or not 0 <= a < b <= len(text) or a <= previous_start:
            raise ConversionError('invalid_window')
        previous_start = a
        first, last = starts[a], ends[b]
        words = tokens[first:last + 1]
        if _tokens(planner, text[a:b]) != [(word, left-a, right-a) for word,left,right in words]:
            raise ConversionError('invalid_token_coverage')
        ner, indices = [], []
        for index, (entity, (left, right, label)) in enumerate(zip(row['entities'], mapped, strict=True)):
            start, end = entity['start'], entity['end']
            if max(a,start) < min(b,end):
                if not a <= start < end <= b:
                    raise ConversionError('partial_entity_in_window')
                ner.append([left-first, right-first, label])
                indices.append(index)
                covered_gold.add(index)
        examples.append({'tokenized_text': [word for word,_,_ in words], 'ner': ner,
                         'ner_labels': list(label_mapping.values())})
        ledger.append({'char_start': a, 'char_end': b, 'word_offsets': [[left,right] for _,left,right in words],
                       'gold_entity_indices': indices})
        covered_words.update(range(first,last+1))
    if covered_gold != set(range(len(mapped))):
        raise ConversionError('incomplete_gold_coverage')
    if covered_words != set(range(len(tokens))):
        raise ConversionError('invalid_token_coverage')
    return {'examples': examples, 'windows': ledger, 'source_entity_count': len(mapped)}
