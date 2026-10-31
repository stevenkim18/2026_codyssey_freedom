import unittest

from model.evaluation.metrics import MetricError
from model.evaluation.policy_metrics import false_block_metrics, masking_residual, secret_rule_adapter

PERSON_TEXT = '김가상에게 연락'


def doc(i, text, gold=()):
    return {'document_id': f'd{i}', 'text': text, 'gold': list(gold)}


class PolicyMetricsTest(unittest.TestCase):
    def test_false_block_uses_policy_not_raw_ner_fp(self):
        docs = [doc(1, '정상 문장입니다 1234'), doc(2, '정상 문장입니다 abcd'), doc(3, '다른 정상 문장'), doc(4, '또 다른 문장')]
        predictions = {
            'd1': [{'type': 'KR_RRN', 'start': 0, 'end': 2, 'score': 0.9}],      # block type -> false block
            'd2': [{'type': 'PERSON', 'start': 0, 'end': 2, 'score': 0.9}],      # mask -> NER FP but not a block
            'd3': [{'type': 'URL', 'start': 0, 'end': 2, 'score': 0.9}],         # allow type
        }
        r = false_block_metrics(docs, predictions)
        self.assertEqual(r['normal_documents'], 4)
        self.assertEqual(r['actions'], {'allow': 2, 'mask': 1, 'block': 1, 'error': 0})
        self.assertEqual(r['false_block_rate'], 0.25)
        self.assertEqual(r['mode'], 'model_only')

    def test_gold_documents_are_not_normal_documents(self):
        docs = [doc(1, PERSON_TEXT, [('PERSON', 0, 3)]), doc(2, '정상')]
        r = false_block_metrics(docs, {'d1': [{'type': 'KR_RRN', 'start': 0, 'end': 2, 'score': 1}]})
        self.assertEqual(r['normal_documents'], 1)
        self.assertEqual(r['false_block_count'], 0)

    def test_no_normal_documents_is_undefined_not_zero(self):
        r = false_block_metrics([doc(1, PERSON_TEXT, [('PERSON', 0, 3)])], {})
        self.assertIsNone(r['false_block_rate'])
        self.assertIsNone(r['false_block_rate_wilson_95'])

    def test_invalid_finding_is_error_not_allow(self):
        r = false_block_metrics([doc(1, '짧은')], {'d1': [{'type': 'PERSON', 'start': 0, 'end': 99, 'score': 1}]})
        self.assertEqual(r['actions']['error'], 1)
        self.assertEqual(r['actions']['allow'], 0)
        self.assertEqual(r['false_block_or_error_rate'], 1.0)

    def test_mode_and_adapter_must_agree_and_rules_are_separate(self):
        with self.assertRaises(MetricError):
            false_block_metrics([doc(1, 'x')], {}, mode='model_plus_same_rules')
        with self.assertRaises(MetricError):
            false_block_metrics([doc(1, 'x')], {}, rule_adapter=secret_rule_adapter)
        fake_rule = lambda text, document_id: [{'segment_id': document_id, 'type': 'API_KEY', 'start': 0, 'end': 1,
                                                 'score': 1.0, 'detector': 'rule'}]
        r = false_block_metrics([doc(1, '정상')], {}, mode='model_plus_same_rules', rule_adapter=fake_rule)
        self.assertEqual(r['false_block_count'], 1)
        self.assertEqual(false_block_metrics([doc(1, '정상')], {})['false_block_count'], 0)

    def test_real_secret_rules_do_not_flag_plain_text(self):
        r = false_block_metrics([doc(1, '평범한 문장입니다')], {}, mode='model_plus_same_rules', rule_adapter=secret_rule_adapter)
        self.assertEqual(r['actions']['allow'], 1)

    def test_masking_residual_counts_uncovered_gold_characters(self):
        text = '김가상 010-1234-5678 연락'
        gold = [('PERSON', 0, 3), ('KR_PHONE_NUMBER', 4, 17)]
        findings = {'d1': [{'type': 'PERSON', 'start': 0, 'end': 3, 'score': 1},
                           {'type': 'KR_PHONE_NUMBER', 'start': 4, 'end': 12, 'score': 1}]}   # phone only partly masked
        r = masking_residual([doc(1, text, gold)], findings)
        self.assertEqual((r['gold_entities_in_masked_documents'], r['residual_entities']), (2, 1))
        self.assertEqual(r['residual_rate'], 0.5)

    def test_masking_residual_blocked_documents_not_counted_and_undefined_when_none(self):
        gold = [('PERSON', 0, 3), ('KR_RRN', 4, 18)]
        findings = {'d1': [{'type': 'KR_RRN', 'start': 4, 'end': 18, 'score': 1}]}
        r = masking_residual([doc(1, '김가상 900101-1234567', gold)], findings)
        self.assertEqual(r['decisions'], {'block': 1})
        self.assertIsNone(r['residual_rate'])

    def test_reinspection_adapter_receives_masked_text(self):
        seen = []

        def reinspect(text):
            seen.append(text)
            return [1] if '김' in text else []
        r = masking_residual([doc(1, '김가상', [('PERSON', 0, 3)])],
                             {'d1': [{'type': 'PERSON', 'start': 0, 'end': 3, 'score': 1}]}, reinspect=reinspect)
        self.assertEqual(seen, ['[REDACTED]'])
        self.assertEqual(r['reinspection_findings'], 0)


if __name__ == '__main__':
    unittest.main()

