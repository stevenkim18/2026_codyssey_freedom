"""Policy-applied metrics: real allow/mask/block decisions, not raw NER false positives.

B's ``dev.gateway.policy.evaluate`` is reused unchanged. Optional rule detectors
are injected as adapters so ``model_only`` and ``model_plus_same_rules`` stay
separate and rule effects are never reported as fine-tuning gains.

Inputs are plain dicts: ``{'document_id', 'text', 'gold': [(type,start,end)]}``
and predictions ``{document_id: [{'type','start','end','score'}]}``.
"""
from collections import Counter

from .metrics import MetricError, ratio, wilson_interval

POLICY_METRICS_VERSION = 'policy-metrics-v1'
MODES = ('model_only', 'model_plus_same_rules')


def default_policy():
    from dev.gateway import policy
    return policy


def secret_rule_adapter(text, document_id):
    """Findings of B's shared secret rules for one document (dicts, detector='rule')."""
    from dev.gateway.secret_rules import detect
    return [dict(f) for f in detect([{'segment_id': document_id, 'text': text}])]


def decide(document, findings, *, policy=None, rule_adapter=None):
    """Apply B's policy to model findings plus optional rule findings.

    Returns the Decision; an invalid combination yields action 'error' (fail
    closed) exactly as B's policy does.
    """
    policy = policy or default_policy()
    document_id = document['document_id']
    merged = [{'segment_id': document_id, 'type': f['type'], 'start': f['start'], 'end': f['end'],
               'score': f.get('score', 1.0), 'detector': 'model'} for f in findings]
    if rule_adapter is not None:
        merged += list(rule_adapter(document['text'], document_id))
    inspection = {'status': 'complete', 'findings': merged, 'model_version': 'evaluation',
                  'threshold_version': 'evaluation', 'timing_ms': 0}
    return policy.evaluate([{'segment_id': document_id, 'text': document['text']}], inspection)


def false_block_metrics(documents, predictions, *, mode='model_only', rule_adapter=None, policy=None):
    """Normal-document false block rate under B's policy.

    A normal document has no gold entity. ``false_block`` = decision ``block``.
    ``error`` decisions are counted separately (fail-closed), not hidden as allow.
    """
    if mode not in MODES or (mode == 'model_plus_same_rules') != (rule_adapter is not None):
        raise MetricError('mode_adapter_mismatch')
    actions = Counter()
    normal = 0
    for document in documents:
        if document['gold']:
            continue
        normal += 1
        decision = decide(document, predictions.get(document['document_id'], []), policy=policy,
                          rule_adapter=rule_adapter)
        actions[decision.action] += 1
    blocks = actions['block']
    return {'metrics_version': POLICY_METRICS_VERSION, 'mode': mode, 'normal_documents': normal,
            'actions': {k: actions[k] for k in ('allow', 'mask', 'block', 'error')},
            'false_block_count': blocks, 'false_block_rate': ratio(blocks, normal),
            'false_block_rate_wilson_95': wilson_interval(blocks, normal),
            'false_block_or_error_rate': ratio(blocks + actions['error'], normal)}


def masking_residual(documents, predictions, *, mode='model_only', rule_adapter=None, policy=None,
                     mask_types=None, reinspect=None):
    """Residual rate of gold PII characters left visible after B's masking.

    Contract: for each document whose decision is ``mask``, a gold entity of a
    maskable type is *residual* if any of its characters is outside the union of
    the masked spans. Blocked/error decisions forward no text and are counted
    separately. ``reinspect(masked_text) -> list[findings]`` is an optional
    injected re-check of the masked output; its finding count is reported.
    """
    policy = policy or default_policy()
    mask_types = set(mask_types if mask_types is not None else policy.MASK_TYPES)
    gold_total = residual = 0
    decisions = Counter()
    reinspection_findings = 0
    for document in documents:
        relevant = [g for g in document['gold'] if g[0] in mask_types]
        decision = decide(document, predictions.get(document['document_id'], []), policy=policy,
                          rule_adapter=rule_adapter if mode == 'model_plus_same_rules' else None)
        decisions[decision.action] += 1
        if not relevant:
            continue
        if decision.action != 'mask':
            continue
        covered = set()
        for f in decision.findings:
            if f['type'] in policy.MASK_TYPES:
                covered.update(range(f['start'], f['end']))
        gold_total += len(relevant)
        residual += sum(1 for _, start, end in relevant if not set(range(start, end)) <= covered)
        if reinspect is not None:
            reinspection_findings += len(reinspect(decision.segments[0]['text']))
    return {'metrics_version': POLICY_METRICS_VERSION, 'mode': mode, 'decisions': dict(decisions),
            'gold_entities_in_masked_documents': gold_total, 'residual_entities': residual,
            'residual_rate': ratio(residual, gold_total),
            'residual_rate_wilson_95': wilson_interval(residual, gold_total),
            'reinspection_findings': None if reinspect is None else reinspection_findings,
            'scope': 'documents with decision mask only; gold entities in blocked/error documents are not forwarded'}

