import copy
import unittest

from model.evaluation.compare import compare, critical_regressions, mismatches, pp, replacement_decision
from model.evaluation.metrics import score
from model.evaluation.records import (CRITICAL_TYPES, RecordError, record_digest, record_from_score, required_fields,
                                      validate_record)

BASE = dict(
    experiment_id='m0-base', parent_experiment_id=None, common_baseline_experiment_id='m0-base', changed_factors=[],
    comparison_mode='dev_selected_pipeline', comparison_condition_id='cond-1',
    model_repo_revision_or_checkpoint_sha256='1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d',
    dataset_revision='844f92f7beba98fa964f957ba324017e82451701', split_and_service_test_version='dev-v1',
    sample_manifest_sha256=None, seed=42, inference_configuration={'flat_ner': False}, label_mapping_version='l1',
    threshold_version='t1', rules_version='r1', policy_version='p1', chunking_and_overlap_version='c1',
    span_postprocessing_version='s1', started_at_utc='2026-11-01T00:00:00Z', finished_at_utc='2026-11-01T00:05:00+00:00',
    delta_vs_parent=None, delta_vs_common_baseline=None, report_mode='model_only')


def make(experiment_id='m0-base', hits=24, total=30, false_blocks=1, normal=100, **over):
    gold = [('d%d' % i, kind, 0, 5) for kind in CRITICAL_TYPES for i in range(total)]
    pred = [('d%d' % i, kind, 0, 5) for kind in CRITICAL_TYPES for i in range(hits)]
    # distinct documents per type to avoid cross-type coupling
    gold = [(f'{k}-{i}', k, 0, 5) for k in CRITICAL_TYPES for i in range(total)]
    pred = [(f'{k}-{i}', k, 0, 5) for k in CRITICAL_TYPES for i in range(hits)]
    fb = {'false_blocks': false_blocks, 'normal_documents': normal, 'rate': false_blocks / normal}
    record = record_from_score(score(gold, pred), normal_false_block=fb, **dict(BASE, experiment_id=experiment_id, **over))
    return record


class RecordTest(unittest.TestCase):
    def test_valid_record_and_digest_stable(self):
        record = validate_record(make())
        self.assertEqual(record_digest(record), record_digest(copy.deepcopy(record)))

    def test_required_fields_come_from_plan(self):
        fields = required_fields()
        for name in ('experiment_id', 'delta_vs_common_baseline', 'report_mode', 'metrics_protocol_version'):
            self.assertIn(name, fields)

    def test_missing_and_inconsistent_values_rejected(self):
        record = make()
        del record['seed']
        with self.assertRaises(RecordError) as caught:
            validate_record(record)
        self.assertIn('missing_field:seed', caught.exception.codes)
        record = make()
        record['overall_precision_recall_f1']['recall'] = 0.5
        with self.assertRaises(RecordError):
            validate_record(record)
        record = make()
        record['per_type_precision_recall_f1_and_denominators']['KR_RRN']['gold'] += 1
        with self.assertRaises(RecordError):
            validate_record(record)
        record = make()
        record['normal_false_block_rate_and_denominator']['rate'] = 1.5
        with self.assertRaises(RecordError):
            validate_record(record)
        record = make(finished_at_utc='2026-10-31T00:00:00Z')
        with self.assertRaises(RecordError):
            validate_record(record)
        record = make(started_at_utc='2026-11-01T09:00:00+09:00')
        with self.assertRaises(RecordError):
            validate_record(record)

    def test_not_executed_record_cannot_carry_numbers(self):
        planned = dict(make(), status='not_executed')
        with self.assertRaises(RecordError):
            validate_record(planned)
        for key in ('overall_precision_recall_f1', 'per_type_precision_recall_f1_and_denominators',
                    'critical_type_false_negatives_and_gold_counts', 'normal_false_block_rate_and_denominator',
                    'started_at_utc', 'finished_at_utc'):
            planned[key] = None
        validate_record(planned)

    def test_zero_gold_type_keeps_undefined_recall(self):
        record = make(total=0, hits=0)
        self.assertIsNone(record['per_type_precision_recall_f1_and_denominators']['KR_RRN']['recall'])
        validate_record(record)


class CompareTest(unittest.TestCase):
    def test_pp_units(self):
        self.assertAlmostEqual(pp(0.75, 0.5), 25.0)
        self.assertIsNone(pp(None, 0.5))

    def test_delta_in_percentage_points_and_counts(self):
        ref, cur = make('m0', hits=24), make('m1', hits=27, parent_experiment_id='m0')
        result = compare(cur, ref)
        self.assertTrue(result['comparable'])
        self.assertAlmostEqual(result['delta']['critical_micro_recall_pp'], 10.0)
        self.assertEqual(result['delta']['per_type']['KR_RRN']['false_negative_count_difference'], -3)

    def test_incomparable_conditions_publish_no_delta(self):
        ref = make('m0')
        for key, value in (('dataset_revision', 'x'), ('split_and_service_test_version', 'dev-v2'),
                           ('metrics_protocol_version', 'other'), ('report_mode', 'model_plus_same_rules'),
                           ('policy_version', 'p2')):
            cur = make('m1', **{key: value})
            result = compare(cur, ref)
            self.assertFalse(result['comparable'])
            self.assertIsNone(result['delta'])
            self.assertIn('differs:' + key, result['mismatches'])

    def test_controlled_weights_only_requires_fixed_settings(self):
        ref = make('m0', comparison_mode='controlled_weights_only')
        cur = make('m1', comparison_mode='controlled_weights_only', threshold_version='t2')
        self.assertIn('differs:threshold_version', mismatches(cur, ref))
        # the same change is allowed in a complete-pipeline comparison
        self.assertEqual(mismatches(make('m1', threshold_version='t2'), make('m0')), [])

    def test_not_executed_never_compared(self):
        planned = dict(make('m1'), status='not_executed')
        for key in ('overall_precision_recall_f1', 'per_type_precision_recall_f1_and_denominators',
                    'critical_type_false_negatives_and_gold_counts', 'normal_false_block_rate_and_denominator',
                    'started_at_utc', 'finished_at_utc'):
            planned[key] = None
        self.assertFalse(compare(planned, make('m0'))['comparable'])
        self.assertEqual(replacement_decision(planned, make('m0'))['verdict'], 'pending')


class ReplacementTest(unittest.TestCase):
    def test_all_gates_but_runtime_acceptance_pending(self):
        d = replacement_decision(make('m1', hits=27), make('m0', hits=24))
        self.assertEqual(d['verdict'], 'pending_runtime_acceptance')
        self.assertEqual(replacement_decision(make('m1', hits=27), make('m0', hits=24), runtime_acceptance=True)['verdict'], 'replace')

    def test_recall_f1_and_false_block_gates(self):
        self.assertEqual(replacement_decision(make('m1', hits=20), make('m0', hits=24))['verdict'], 'reject')
        # +1.0pp is the allowed limit, +2pp rejects
        ok = replacement_decision(make('m1', hits=27, false_blocks=2), make('m0', hits=24, false_blocks=1), runtime_acceptance=True)
        self.assertEqual(ok['verdict'], 'replace')
        bad = replacement_decision(make('m1', hits=27, false_blocks=3), make('m0', hits=24, false_blocks=1), runtime_acceptance=True)
        self.assertEqual(bad['verdict'], 'reject')
        self.assertIn('normal_false_block_increase_within_limit', bad['reasons'])

    def test_unresolved_critical_regression_holds(self):
        base, cand = make('m0', hits=24), make('m1', hits=27)
        cand['critical_type_false_negatives_and_gold_counts']['KR_RRN']['false_negatives'] = 10
        cand['per_type_precision_recall_f1_and_denominators']['KR_RRN'].update(fn=10, tp=20)
        cand['per_type_precision_recall_f1_and_denominators']['KR_RRN']['recall'] = 20 / 30
        cand['per_type_precision_recall_f1_and_denominators']['KR_RRN']['f1'] = 40 / (40 + 0 + 10)
        cand['per_type_precision_recall_f1_and_denominators']['KR_RRN']['precision'] = 1.0
        cand['overall_precision_recall_f1'].update(tp=sum(r['tp'] for r in cand['per_type_precision_recall_f1_and_denominators'].values()),
                                                  fn=sum(r['fn'] for r in cand['per_type_precision_recall_f1_and_denominators'].values()))
        tp, fp, fn = (cand['overall_precision_recall_f1'][k] for k in ('tp', 'fp', 'fn'))
        cand['overall_precision_recall_f1'].update(precision=tp / (tp + fp), recall=tp / (tp + fn), f1=2 * tp / (2 * tp + fp + fn))
        self.assertEqual(critical_regressions(cand, base), ['KR_RRN'])
        self.assertEqual(replacement_decision(cand, base, runtime_acceptance=True)['verdict'], 'hold')
        resolved = replacement_decision(cand, base, runtime_acceptance=True, resolved_regressions={'KR_RRN': 'rule covers'})
        self.assertEqual(resolved['verdict'], 'replace')

    def test_undefined_metric_gives_pending_not_pass(self):
        d = replacement_decision(make('m1', total=0, hits=0), make('m0', total=0, hits=0), runtime_acceptance=True)
        self.assertEqual(d['verdict'], 'pending')


if __name__ == '__main__':
    unittest.main()

