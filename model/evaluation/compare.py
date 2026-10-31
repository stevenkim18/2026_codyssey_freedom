"""Fair comparison of two experiment records and the PRD replacement judgement.

No difference is published unless both records are executed and were measured
under the same data, test version, metric protocol, policy and report mode. For
``controlled_weights_only`` the label/threshold/rule/chunking/postprocessing and
inference settings must also match. Rate differences are percentage points:
``pp = 100 * (current - reference)``; counts use count differences.
"""
from .records import critical_recall, validate_record, RecordError

ALWAYS_EQUAL = ('dataset_revision', 'split_and_service_test_version', 'metrics_protocol_version',
                'policy_version', 'report_mode', 'comparison_condition_id')
CONTROLLED_EQUAL = ('label_mapping_version', 'threshold_version', 'rules_version', 'chunking_and_overlap_version',
                    'span_postprocessing_version', 'inference_configuration')


def pp(current, reference):
    return None if current is None or reference is None else 100 * (current - reference)


def mismatches(current, reference):
    """Reasons the two records are not comparable (empty list = comparable)."""
    reasons = []
    for record in (current, reference):
        try:
            validate_record(record)
        except RecordError as exc:
            return [f'invalid_record:{record.get("experiment_id") if isinstance(record, dict) else None}:{code}'
                    for code in exc.codes]
        if record['status'] != 'executed':
            reasons.append(f'not_executed:{record["experiment_id"]}')
    if reasons:
        return reasons
    keys = ALWAYS_EQUAL + (CONTROLLED_EQUAL if current['comparison_mode'] == 'controlled_weights_only' else ())
    reasons += [f'differs:{key}' for key in keys if current[key] != reference[key]]
    if current['comparison_mode'] != reference['comparison_mode'] and 'controlled_weights_only' in (
            current['comparison_mode'], reference['comparison_mode']):
        reasons.append('differs:comparison_mode')
    return reasons


def compare(current, reference):
    """Return ``{'comparable', 'mismatches', 'delta'}``; delta is None when not comparable."""
    reasons = mismatches(current, reference)
    if reasons:
        return {'comparable': False, 'mismatches': reasons, 'delta': None}
    cur_types = current['per_type_precision_recall_f1_and_denominators']
    ref_types = reference['per_type_precision_recall_f1_and_denominators']
    delta = {
        'unit': 'percentage_points for rates; counts for denominators and misses',
        'overall_precision_pp': pp(current['overall_precision_recall_f1']['precision'], reference['overall_precision_recall_f1']['precision']),
        'overall_recall_pp': pp(current['overall_precision_recall_f1']['recall'], reference['overall_precision_recall_f1']['recall']),
        'overall_f1_pp': pp(current['overall_precision_recall_f1']['f1'], reference['overall_precision_recall_f1']['f1']),
        'critical_micro_recall_pp': pp(critical_recall(current), critical_recall(reference)),
        'normal_false_block_rate_pp': pp(current['normal_false_block_rate_and_denominator']['rate'],
                                         reference['normal_false_block_rate_and_denominator']['rate']),
        'normal_documents_count_difference': current['normal_false_block_rate_and_denominator']['normal_documents']
        - reference['normal_false_block_rate_and_denominator']['normal_documents'],
        'per_type': {},
    }
    for kind in sorted(set(cur_types) | set(ref_types)):
        c, r = cur_types.get(kind), ref_types.get(kind)
        delta['per_type'][kind] = None if c is None or r is None else {
            'recall_pp': pp(c['recall'], r['recall']), 'precision_pp': pp(c['precision'], r['precision']),
            'f1_pp': pp(c['f1'], r['f1']), 'false_negative_count_difference': c['fn'] - r['fn'],
            'gold_count_difference': c['gold'] - r['gold']}
    return {'comparable': True, 'mismatches': [], 'delta': delta}


def critical_regressions(candidate, baseline):
    """Critical types whose recall is lower than the baseline, or None if not evaluable."""
    regressions = []
    for kind, row in candidate['critical_type_false_negatives_and_gold_counts'].items():
        base = baseline['critical_type_false_negatives_and_gold_counts'][kind]
        if row['gold'] == 0 or base['gold'] == 0:
            continue
        if 1 - row['false_negatives'] / row['gold'] < 1 - base['false_negatives'] / base['gold'] - 1e-12:
            regressions.append(kind)
    return regressions


def replacement_decision(candidate, baseline, *, resolved_regressions=None, runtime_acceptance=None,
                         max_false_block_increase_pp=1.0):
    """Judge replacing ``baseline`` by ``candidate`` using PRD replacement gates.

    Verdicts: ``not_comparable``, ``pending`` (a required value is missing or the
    record is not executed), ``reject`` (a metric gate fails), ``hold`` (a critical
    type regressed without a recorded resolution), ``pending_runtime_acceptance``
    (metrics pass but B's latency/memory/integration acceptance is not True) and
    ``replace``. ``resolved_regressions`` maps type -> written reason.
    """
    reasons = mismatches(candidate, baseline)
    if any(r.startswith('not_executed') for r in reasons):
        return {'verdict': 'pending', 'reasons': reasons, 'gates': {}}
    if reasons:
        return {'verdict': 'not_comparable', 'reasons': reasons, 'gates': {}}
    delta = compare(candidate, baseline)['delta']
    gates = {}
    for name, value, minimum in (('critical_micro_recall_not_below_baseline', delta['critical_micro_recall_pp'], 0.0),
                                 ('overall_f1_not_below_baseline', delta['overall_f1_pp'], 0.0)):
        gates[name] = None if value is None else value >= -1e-9 * 100
    fb = delta['normal_false_block_rate_pp']
    gates['normal_false_block_increase_within_limit'] = None if fb is None else fb <= max_false_block_increase_pp + 1e-9
    if any(v is None for v in gates.values()):
        return {'verdict': 'pending', 'reasons': ['metric_not_evaluable:' + k for k, v in gates.items() if v is None],
                'gates': gates}
    if not all(gates.values()):
        return {'verdict': 'reject', 'reasons': [k for k, v in gates.items() if not v], 'gates': gates}
    unresolved = [t for t in critical_regressions(candidate, baseline)
                  if not (resolved_regressions or {}).get(t, '').strip()]
    gates['no_unresolved_critical_type_regression'] = not unresolved
    if unresolved:
        return {'verdict': 'hold', 'reasons': ['unresolved_critical_regression:' + t for t in unresolved], 'gates': gates}
    gates['runtime_memory_latency_integration_accepted'] = runtime_acceptance if runtime_acceptance is True else None
    if runtime_acceptance is not True:
        return {'verdict': 'pending_runtime_acceptance', 'reasons': ['B runtime acceptance not recorded'], 'gates': gates}
    return {'verdict': 'replace', 'reasons': [], 'gates': gates}

