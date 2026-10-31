import unittest

from model.evaluation.metrics import (MetricError, bio_spans, bootstrap_interval, macro_f1, paired_bootstrap_delta,
                                      prf, score, score_bio, wilson_interval)


def S(doc, kind, a, b):
    return (doc, kind, a, b)


class MetricsTest(unittest.TestCase):
    def test_exact_match(self):
        r = score([S('d', 'PERSON', 0, 3)], [S('d', 'PERSON', 0, 3)])
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fp'], r['overall_micro']['fn']), (1, 0, 0))
        self.assertEqual(r['overall_micro']['f1'], 1.0)

    def test_miss_false_positive_type_mismatch_and_off_by_one(self):
        gold = [S('d', 'PERSON', 0, 3), S('d', 'KR_RRN', 5, 19), S('d', 'EMAIL_ADDRESS', 20, 30)]
        pred = [S('d', 'KR_RRN', 5, 18),              # one char short -> FP + FN
                S('d', 'PERSON', 0, 3),               # TP
                S('d', 'KR_PHONE_NUMBER', 20, 30),    # wrong type -> FP + FN
                S('d', 'URL', 40, 50)]                # pure FP
        r = score(gold, pred)
        self.assertEqual(r['overall_micro'], dict(r['overall_micro'], tp=1, fp=3, fn=2, gold=3, predicted=4))
        self.assertAlmostEqual(r['overall_micro']['precision'], 1 / 4)
        self.assertAlmostEqual(r['overall_micro']['recall'], 1 / 3)
        self.assertAlmostEqual(r['overall_micro']['f1'], 2 / (2 + 3 + 2))
        self.assertEqual(r['per_type']['KR_RRN']['fn'], 1)
        self.assertEqual(r['per_type']['URL']['fp'], 1)

    def test_empty_gold_and_prediction_are_not_evaluable(self):
        r = score([], [])
        self.assertIsNone(r['overall_micro']['precision'])
        self.assertIsNone(r['overall_micro']['recall'])
        self.assertIsNone(r['overall_micro']['f1'])
        r = score([], [S('d', 'PERSON', 0, 2)])
        self.assertIsNone(r['overall_micro']['recall'])
        self.assertEqual(r['overall_micro']['precision'], 0.0)
        r = score([S('d', 'PERSON', 0, 2)], [])
        self.assertIsNone(r['overall_micro']['precision'])
        self.assertEqual(r['overall_micro']['recall'], 0.0)

    def test_same_span_two_types_are_distinct_and_overlaps_kept(self):
        gold = [S('d', 'PERSON', 0, 3), S('d', 'USERNAME', 0, 3), S('d', 'ORGANIZATION', 1, 5)]
        r = score(gold, [S('d', 'PERSON', 0, 3)])
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fn']), (1, 2))

    def test_duplicates_are_counted_not_silently_removed(self):
        gold = [S('d', 'PERSON', 0, 3), S('d', 'PERSON', 0, 3)]
        r = score(gold, [S('d', 'PERSON', 0, 3)])
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fn'], r['overall_micro']['gold']), (1, 1, 2))
        r = score([S('d', 'PERSON', 0, 3)], [S('d', 'PERSON', 0, 3)] * 3)
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fp']), (1, 2))
        with self.assertRaises(MetricError):
            score(gold, [], duplicates='error')

    def test_same_position_in_different_documents_is_distinct(self):
        r = score([S('a', 'PERSON', 0, 3), S('b', 'PERSON', 0, 3)], [S('a', 'PERSON', 0, 3)])
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fn']), (1, 1))

    def test_unicode_codepoint_positions_and_length_bounds(self):
        text = '😀김가상'
        self.assertEqual(text[1:4], '김가상')
        self.assertEqual(score([S('d', 'PERSON', 1, 4)], [S('d', 'PERSON', 1, 4)], document_lengths={'d': len(text)})['overall_micro']['tp'], 1)
        with self.assertRaises(MetricError):
            score([S('d', 'PERSON', 1, 9)], [], document_lengths={'d': len(text)})

    def test_invalid_items_rejected(self):
        for bad in ([('d', 'P', 3, 3)], [('d', 'P', -1, 2)], [('', 'P', 0, 1)], [('d', '', 0, 1)],
                    [('d', 'P', 0.0, 1)], [('d', 'P', True, 2)], [('d', 'P', 0)]):
            with self.assertRaises(MetricError, msg=bad):
                score(bad, [])

    def test_critical_micro_and_macro_rules(self):
        gold = [S('d', 'KR_RRN', 0, 14), S('d', 'EMAIL_ADDRESS', 20, 30), S('d', 'PERSON', 40, 43)]
        pred = [S('d', 'KR_RRN', 0, 14)]
        r = score(gold, pred, critical_types=['KR_RRN', 'EMAIL_ADDRESS', 'CREDIT_CARD'])
        self.assertEqual((r['critical_micro']['tp'], r['critical_micro']['fn'], r['critical_micro']['gold']), (1, 1, 2))
        macro = macro_f1(r['per_type'])
        self.assertEqual(macro['averaged_types'], ['EMAIL_ADDRESS', 'KR_RRN', 'PERSON'])
        self.assertAlmostEqual(macro['macro_f1'], (0 + 1 + 0) / 3)
        self.assertEqual(macro_f1(r['per_type'], exclude=['PERSON'])['excluded_types'], ['PERSON'])

    def test_prf_formula(self):
        v = prf(3, 1, 2)
        self.assertAlmostEqual(v['f1'], 6 / 9)
        self.assertEqual(prf(0, 0, 0), {'precision': None, 'recall': None, 'f1': None})

    def test_wilson_interval(self):
        self.assertIsNone(wilson_interval(0, 0))
        w = wilson_interval(30, 30)
        self.assertAlmostEqual(w['high'], 1.0)
        self.assertLess(w['low'], 0.9)   # 30/30 is not certainty
        mid = wilson_interval(5, 10)
        self.assertAlmostEqual(mid['low'], 0.2366, places=3)
        self.assertAlmostEqual(mid['high'], 0.7634, places=3)

    def test_bootstrap_is_deterministic_and_resamples_documents(self):
        gold = [S(f'd{i}', 'PERSON', 0, 3) for i in range(20)]
        pred = gold[:15]
        stat = lambda r: r['overall_micro']['recall']
        a = bootstrap_interval(gold, pred, stat, iterations=200, seed=1)
        b = bootstrap_interval(gold, pred, stat, iterations=200, seed=1)
        self.assertEqual(a, b)
        self.assertLess(a['low'], 0.75)
        self.assertGreater(a['high'], 0.75)
        d = paired_bootstrap_delta(gold, pred, gold, stat, iterations=200, seed=1)
        self.assertGreaterEqual(d['low'], 0)
        self.assertIsNone(bootstrap_interval([], [], stat))

    def test_bio_metric_is_separate_and_token_based(self):
        self.assertEqual(bio_spans(['B-PERSON', 'I-PERSON', 'O', 'I-URL']), [('PERSON', 0, 1), ('URL', 3, 3)])
        r = score_bio([['B-PERSON', 'I-PERSON', 'O']], [['B-PERSON', 'O', 'O']])
        self.assertEqual(r['metric_id'], 'bio_entity_exact_v1')
        self.assertEqual((r['overall_micro']['tp'], r['overall_micro']['fp'], r['overall_micro']['fn']), (0, 1, 1))
        with self.assertRaises(MetricError):
            score_bio([['X']], [['O']])
        with self.assertRaises(MetricError):
            score_bio([['O']], [['O', 'O']])


if __name__ == '__main__':
    unittest.main()

