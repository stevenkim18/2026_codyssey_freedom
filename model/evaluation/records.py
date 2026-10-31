"""Experiment record format and validation (stage 4, B3).

Required fields come from ``configs/experiment-plan.json::experiment_record_plan``
plus ``report_mode`` (model_only | model_plus_same_rules) and ``status``. A
``not_executed`` record carries ``null`` metrics/timestamps: unexecuted items are
never filled with numbers. Rates are stored in [0,1].
"""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECORD_SCHEMA_VERSION = '1.0'
METRICS_PROTOCOL_VERSION = 'char_span_exact_v1+policy-metrics-v1'
COMPARISON_MODES = ('dev_selected_pipeline', 'controlled_weights_only', 'single_factor', 'cumulative')
REPORT_MODES = ('model_only', 'model_plus_same_rules')
CRITICAL_TYPES = ('KR_RRN', 'KR_BANK_ACCOUNT', 'CREDIT_CARD', 'KR_PHONE_NUMBER', 'EMAIL_ADDRESS')
PLAN_PATH = Path(__file__).resolve().parents[1] / 'configs/experiment-plan.json'

METRIC_FIELDS = ('overall_precision_recall_f1', 'per_type_precision_recall_f1_and_denominators',
                 'critical_type_false_negatives_and_gold_counts', 'normal_false_block_rate_and_denominator')
EXTRA_REQUIRED = ('report_mode', 'status', 'record_schema_version')


class RecordError(ValueError):
    def __init__(self, codes):
        self.codes = list(codes)
        super().__init__(','.join(self.codes))


def required_fields():
    plan = json.loads(PLAN_PATH.read_text())
    return list(plan['experiment_record_plan']['required_fields']) + list(EXTRA_REQUIRED)


def _utc(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _rate_ok(value):
    return value is None or (type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1)


def _close(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-9)


def _expected(tp, fp, fn):
    return {'precision': None if tp + fp == 0 else tp / (tp + fp),
            'recall': None if tp + fn == 0 else tp / (tp + fn),
            'f1': None if 2 * tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)}


def _check_row(row, where, errors):
    keys = ('tp', 'fp', 'fn')
    if (not isinstance(row, dict) or any(type(row.get(k)) is not int or row[k] < 0 for k in keys)):
        errors.append(f'invalid_counts:{where}')
        return
    for name, value in _expected(*(row[k] for k in keys)).items():
        if not _rate_ok(row.get(name)) or not _close(row.get(name), value):
            errors.append(f'metric_not_supported_by_counts:{where}:{name}')
    if 'gold' in row and (type(row['gold']) is not int or row['gold'] != row['tp'] + row['fn']):
        errors.append(f'gold_count_mismatch:{where}')


def validate_record(record, *, fields=None):
    """Raise ``RecordError`` listing every problem; return the record when valid."""
    errors = []
    if not isinstance(record, dict):
        raise RecordError(['record_not_object'])
    for field in fields or required_fields():
        if field not in record:
            errors.append(f'missing_field:{field}')
    if errors:
        raise RecordError(errors)
    if record['record_schema_version'] != RECORD_SCHEMA_VERSION:
        errors.append('unsupported_schema_version')
    if record['status'] not in ('executed', 'not_executed'):
        errors.append('invalid_status')
    if record['report_mode'] not in REPORT_MODES:
        errors.append('invalid_report_mode')
    if record['comparison_mode'] not in COMPARISON_MODES:
        errors.append('invalid_comparison_mode')
    for key in ('experiment_id', 'comparison_condition_id', 'dataset_revision', 'split_and_service_test_version',
                'model_repo_revision_or_checkpoint_sha256', 'metrics_protocol_version'):
        if not isinstance(record[key], str) or not record[key].strip():
            errors.append(f'invalid_string:{key}')
    if record['parent_experiment_id'] is not None and record['parent_experiment_id'] == record['experiment_id']:
        errors.append('self_parent')
    if not isinstance(record['changed_factors'], list):
        errors.append('invalid_changed_factors')
    if type(record['seed']) is not int:
        errors.append('invalid_seed')
    if record['status'] == 'not_executed':
        if any(record[f] is not None for f in METRIC_FIELDS + ('started_at_utc', 'finished_at_utc',
                                                              'delta_vs_parent', 'delta_vs_common_baseline')):
            errors.append('not_executed_record_has_results')
        if errors:
            raise RecordError(errors)
        return record
    start, end = _utc(record['started_at_utc']), _utc(record['finished_at_utc'])
    if start is None or end is None or end < start:
        errors.append('invalid_utc_times')
    overall = record['overall_precision_recall_f1']
    _check_row(overall, 'overall', errors)
    per_type = record['per_type_precision_recall_f1_and_denominators']
    if not isinstance(per_type, dict) or not per_type:
        errors.append('invalid_per_type')
    else:
        for kind, row in per_type.items():
            _check_row(row, kind, errors)
        if isinstance(overall, dict) and not errors:
            for key in ('tp', 'fp', 'fn'):
                if sum(row[key] for row in per_type.values()) != overall[key]:
                    errors.append(f'overall_not_sum_of_types:{key}')
    critical = record['critical_type_false_negatives_and_gold_counts']
    if not isinstance(critical, dict) or set(critical) != set(CRITICAL_TYPES):
        errors.append('invalid_critical_types')
    else:
        for kind, row in critical.items():
            if (not isinstance(row, dict) or type(row.get('false_negatives')) is not int
                    or type(row.get('gold')) is not int or not 0 <= row['false_negatives'] <= row['gold']):
                errors.append(f'invalid_critical_row:{kind}')
            elif isinstance(per_type, dict) and kind in per_type and (
                    row['gold'] != per_type[kind].get('gold') or row['false_negatives'] != per_type[kind].get('fn')):
                errors.append(f'critical_row_disagrees_with_per_type:{kind}')
    fb = record['normal_false_block_rate_and_denominator']
    if (not isinstance(fb, dict) or type(fb.get('false_blocks')) is not int or type(fb.get('normal_documents')) is not int
            or not 0 <= fb['false_blocks'] <= fb['normal_documents']
            or not _rate_ok(fb.get('rate'))
            or not _close(fb.get('rate'), None if fb.get('normal_documents') == 0 else fb['false_blocks'] / fb['normal_documents'])):
        errors.append('invalid_false_block')
    if errors:
        raise RecordError(errors)
    return record


def critical_recall(record):
    """Micro recall over the critical types, or None if they have no gold."""
    rows = record['critical_type_false_negatives_and_gold_counts'].values()
    gold = sum(r['gold'] for r in rows)
    return None if gold == 0 else 1 - sum(r['false_negatives'] for r in rows) / gold


def record_digest(record):
    """SHA-256 of the canonical JSON; stored alongside, never inside, the record."""
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def record_from_score(result, *, normal_false_block=None, **fields):
    """Assemble a record from ``metrics.score`` output plus caller-provided metadata."""
    per_type = {k: {key: row[key] for key in ('tp', 'fp', 'fn', 'gold', 'predicted', 'precision', 'recall', 'f1')}
                for k, row in result['per_type'].items()}
    for kind in CRITICAL_TYPES:
        per_type.setdefault(kind, {'tp': 0, 'fp': 0, 'fn': 0, 'gold': 0, 'predicted': 0,
                                   'precision': None, 'recall': None, 'f1': None})
    overall = {key: result['overall_micro'][key] for key in ('tp', 'fp', 'fn', 'precision', 'recall', 'f1')}
    record = dict(fields)
    record.setdefault('metrics_protocol_version', METRICS_PROTOCOL_VERSION)
    record.update({
        'record_schema_version': RECORD_SCHEMA_VERSION, 'status': 'executed',
        'overall_precision_recall_f1': overall,
        'per_type_precision_recall_f1_and_denominators': per_type,
        'critical_type_false_negatives_and_gold_counts': {
            k: {'false_negatives': per_type[k]['fn'], 'gold': per_type[k]['gold']} for k in CRITICAL_TYPES},
        'normal_false_block_rate_and_denominator': normal_false_block,
    })
    return record
