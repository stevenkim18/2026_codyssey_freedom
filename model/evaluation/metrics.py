"""Exact character-span metrics. Scoring unit: (document_id, type, start, end).

Ends are exclusive. Metric id: ``char_span_exact_v1``. This is *not* the official
BIO token score (see ``bio_entity_exact_v1`` below); the two are never mixed.

Duplicate rule (``duplicates='multiset'``, the default): identical gold tuples
count as separate gold items and identical prediction tuples as separate
predictions; each prediction can match at most one gold copy. TP per tuple is
``min(gold_count, pred_count)``, extra prediction copies are FP and unmatched
gold copies are FN. Nothing is de-duplicated with ``set``. ``duplicates='error'``
rejects any duplicate instead. Spans with the same offsets but different types
are different items; overlapping gold spans are never dropped.

A ratio whose denominator is 0 is ``None`` (not evaluable), never 0 or 1.
"""
from collections import Counter, defaultdict
import math
import random

METRIC_ID = 'char_span_exact_v1'
BIO_METRIC_ID = 'bio_entity_exact_v1'


class MetricError(ValueError):
    pass


def _validate(items, name, lengths=None):
    result = []
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 4:
            raise MetricError(f'invalid_{name}_item')
        document_id, kind, start, end = item
        if (not isinstance(document_id, str) or not document_id or not isinstance(kind, str) or not kind
                or type(start) is not int or type(end) is not int or not 0 <= start < end):
            raise MetricError(f'invalid_{name}_item')
        if lengths is not None and (document_id not in lengths or end > lengths[document_id]):
            raise MetricError(f'{name}_span_outside_document')
        result.append((document_id, kind, start, end))
    return result


def ratio(numerator, denominator):
    return None if denominator == 0 else numerator / denominator


def prf(tp, fp, fn):
    """Precision/recall/F1 with explicit non-evaluable (None) values."""
    return {'precision': ratio(tp, tp + fp), 'recall': ratio(tp, tp + fn), 'f1': ratio(2 * tp, 2 * tp + fp + fn)}


def _counts(gold, pred, duplicates):
    if duplicates not in ('multiset', 'error'):
        raise MetricError('invalid_duplicate_policy')
    g, p = Counter(gold), Counter(pred)
    if duplicates == 'error' and (any(v > 1 for v in g.values()) or any(v > 1 for v in p.values())):
        raise MetricError('duplicate_items_rejected')
    return g, p


def score(gold, predicted, *, duplicates='multiset', document_lengths=None, critical_types=()):
    """Return overall micro metrics, per-type metrics and the evidence counts."""
    gold = _validate(gold, 'gold', document_lengths)
    predicted = _validate(predicted, 'prediction', document_lengths)
    g, p = _counts(gold, predicted, duplicates)
    per = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0, 'gold': 0, 'predicted': 0})
    for key in set(g) | set(p):
        tp = min(g[key], p[key])
        row = per[key[1]]
        row['tp'] += tp
        row['fp'] += p[key] - tp
        row['fn'] += g[key] - tp
        row['gold'] += g[key]
        row['predicted'] += p[key]
    for kind in {k[1] for k in g} | {k[1] for k in p}:
        per[kind]
    total = {k: sum(row[k] for row in per.values()) for k in ('tp', 'fp', 'fn', 'gold', 'predicted')}
    if total['gold'] != len(gold) or total['predicted'] != len(predicted):
        raise MetricError('count_invariant_violated')
    per_type = {}
    for kind in sorted(per):
        row = dict(per[kind])
        row.update(prf(row['tp'], row['fp'], row['fn']))
        per_type[kind] = row
    result = {'metric_id': METRIC_ID, 'duplicates': duplicates, 'document_count': len({k[0] for k in gold} | {k[0] for k in predicted}),
              'overall_micro': dict(total, **prf(total['tp'], total['fp'], total['fn'])), 'per_type': per_type}
    critical = [t for t in critical_types]
    if critical:
        sums = {k: sum(per_type.get(t, {}).get(k, 0) for t in critical) for k in ('tp', 'fp', 'fn', 'gold', 'predicted')}
        result['critical_micro'] = dict(sums, types=critical, **prf(sums['tp'], sums['fp'], sums['fn']))
    return result


def macro_f1(per_type, exclude=()):
    """Mean F1 over types with a defined F1 and not excluded; lists both groups."""
    included = {t: r['f1'] for t, r in per_type.items() if t not in exclude and r['f1'] is not None}
    excluded = sorted(set(per_type) - set(included))
    return {'macro_f1': None if not included else sum(included.values()) / len(included),
            'averaged_types': sorted(included), 'excluded_types': excluded,
            'rule': 'unweighted mean over types with 2TP+FP+FN>0, minus explicit exclusions'}


def wilson_interval(successes, total, z=1.959964):
    """Wilson score interval for a proportion; None when total is 0."""
    if total == 0:
        return None
    if not 0 <= successes <= total:
        raise MetricError('invalid_proportion')
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return {'low': max(0.0, centre - half), 'high': min(1.0, centre + half), 'method': 'wilson', 'z': z}


def _by_document(items):
    grouped = defaultdict(list)
    for item in items:
        grouped[item[0]].append(item)
    return grouped


def bootstrap_interval(gold, predicted, statistic, *, iterations=1000, seed=42, confidence=0.95, duplicates='multiset'):
    """Document-level percentile bootstrap for ``statistic(score_result) -> float|None``.

    Documents (not entities) are resampled with replacement. Resampled copies get
    distinct document ids so repeated documents are counted repeatedly. Samples
    where the statistic is undefined are dropped and counted.
    """
    gold, predicted = _validate(gold, 'gold'), _validate(predicted, 'prediction')
    g, p = _by_document(gold), _by_document(predicted)
    docs = sorted(set(g) | set(p))
    if not docs:
        return None
    rng = random.Random(seed)
    values, undefined = [], 0
    for _ in range(iterations):
        sample_gold, sample_pred = [], []
        for copy_index in range(len(docs)):
            doc = docs[rng.randrange(len(docs))]
            name = f'{doc}#{copy_index}'
            sample_gold += [(name, *x[1:]) for x in g.get(doc, [])]
            sample_pred += [(name, *x[1:]) for x in p.get(doc, [])]
        value = statistic(score(sample_gold, sample_pred, duplicates=duplicates))
        if value is None:
            undefined += 1
        else:
            values.append(value)
    if not values:
        return {'low': None, 'high': None, 'method': 'document_bootstrap', 'iterations': iterations,
                'undefined_samples': undefined, 'seed': seed}
    values.sort()
    alpha = (1 - confidence) / 2
    lo = values[int(alpha * (len(values) - 1))]
    hi = values[int(math.ceil((1 - alpha) * (len(values) - 1)))]
    return {'low': lo, 'high': hi, 'method': 'document_bootstrap', 'iterations': iterations,
            'undefined_samples': undefined, 'seed': seed, 'confidence': confidence}


def paired_bootstrap_delta(gold, pred_a, pred_b, statistic, *, iterations=1000, seed=42, confidence=0.95):
    """CI of ``statistic(B) - statistic(A)`` resampling the same documents for both."""
    gold, pred_a, pred_b = (_validate(x, n) for x, n in ((gold, 'gold'), (pred_a, 'prediction'), (pred_b, 'prediction')))
    g, a, b = _by_document(gold), _by_document(pred_a), _by_document(pred_b)
    docs = sorted(set(g) | set(a) | set(b))
    if not docs:
        return None
    rng = random.Random(seed)
    deltas, undefined = [], 0
    for _ in range(iterations):
        sg, sa, sb = [], [], []
        for copy_index in range(len(docs)):
            doc = docs[rng.randrange(len(docs))]
            name = f'{doc}#{copy_index}'
            sg += [(name, *x[1:]) for x in g.get(doc, [])]
            sa += [(name, *x[1:]) for x in a.get(doc, [])]
            sb += [(name, *x[1:]) for x in b.get(doc, [])]
        va, vb = statistic(score(sg, sa)), statistic(score(sg, sb))
        if va is None or vb is None:
            undefined += 1
        else:
            deltas.append(vb - va)
    deltas.sort()
    if not deltas:
        return {'low': None, 'high': None, 'method': 'paired_document_bootstrap', 'undefined_samples': undefined}
    alpha = (1 - confidence) / 2
    return {'low': deltas[int(alpha * (len(deltas) - 1))], 'high': deltas[int(math.ceil((1 - alpha) * (len(deltas) - 1)))],
            'method': 'paired_document_bootstrap', 'iterations': iterations, 'undefined_samples': undefined,
            'seed': seed, 'confidence': confidence}


# ---- Official-style BIO entity score (separate metric, token level tags) ----

def bio_spans(tags):
    """Entity spans (type, first_token, last_token_inclusive) from a BIO tag list.

    An ``I-X`` after ``O`` or a different type starts a new entity (conlleval
    style); malformed tags raise.
    """
    spans, current = [], None
    for index, tag in enumerate(tags):
        if tag == 'O':
            if current:
                spans.append(tuple(current))
            current = None
            continue
        if not isinstance(tag, str) or len(tag) < 3 or tag[0] not in 'BI' or tag[1] != '-':
            raise MetricError('invalid_bio_tag')
        kind = tag[2:]
        if tag[0] == 'B' or current is None or current[0] != kind:
            if current:
                spans.append(tuple(current))
            current = [kind, index, index]
        else:
            current[2] = index
    if current:
        spans.append(tuple(current))
    return spans


def score_bio(gold_sequences, predicted_sequences):
    """Entity-level exact token-span score; sequences are lists of BIO tag lists."""
    if len(gold_sequences) != len(predicted_sequences):
        raise MetricError('sequence_count_mismatch')
    gold, pred = [], []
    for number, (g, p) in enumerate(zip(gold_sequences, predicted_sequences, strict=True)):
        if len(g) != len(p):
            raise MetricError('sequence_length_mismatch')
        gold += [(str(number), *s[0:1], s[1], s[2] + 1) for s in bio_spans(g)]
        pred += [(str(number), *s[0:1], s[1], s[2] + 1) for s in bio_spans(p)]
    result = score(gold, pred)
    result['metric_id'] = BIO_METRIC_ID
    result['unit'] = 'token index span, not character offsets'
    return result

