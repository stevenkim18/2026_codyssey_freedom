import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from model.evaluation import service_cases as sc
from model.evaluation import service_testset_builder as builder


class RenderTest(unittest.TestCase):
    def test_offsets_exact_and_markers_rendered(self):
        values = builder.Values(builder.random.Random(1))
        text, entities = builder.render('A ⟦PERSON:x⟧님과 ⟦PERSON:x⟧ ⟦SERIAL13⟧ ⟦KR_RRN⟧입니다', values)
        self.assertNotIn('⟦', text)
        self.assertEqual(len(entities), 3)    # same-name PERSON repeated, SERIAL13 is never annotated
        self.assertEqual(entities[0]['surface'], entities[1]['surface'])
        for e in entities:
            self.assertEqual(text[e['start']:e['end']], e['surface'])
        self.assertTrue(all(e['type'] != 'SERIAL13' for e in entities))

    def test_malformed_placeholder_rejected(self):
        with self.assertRaises(ValueError):
            builder.render('⟦PERSON', builder.Values(builder.random.Random(1)))

    def test_avoid_set_rerolls_values(self):
        values = builder.Values(builder.random.Random(1))
        first = builder.render('⟦KR_RRN⟧', values)[0]
        again = builder.render('⟦KR_RRN⟧', builder.Values(builder.random.Random(1)), avoid={first})[0]
        self.assertNotEqual(first, again)

    def test_synthetic_values_follow_safety_conventions(self):
        v = builder.Values(builder.random.Random(7))
        for _ in range(50):
            self.assertTrue(v.EMAIL_ADDRESS().split('@')[1] in builder.DOMAINS)
            self.assertTrue(v.IP_ADDRESS().startswith(('192.0.2.', '198.51.100.', '203.0.113.')))
            self.assertTrue(v.URL().split('/')[2] in builder.HOSTS)
            card = v.CREDIT_CARD().replace(' ', '').replace('-', '')
            total = 0
            for i, ch in enumerate(reversed(card)):
                n = int(ch) * (2 if i % 2 == 1 else 1)
                total += n - 9 if n > 9 else n
            self.assertEqual(total % 10, 0)


class DatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = builder.finalize(builder.select_300(builder.build(), builder.SEEDS['final']), 'final')

    def test_generation_is_deterministic(self):
        again = builder.finalize(builder.select_300(builder.build(), builder.SEEDS['final']), 'final')
        self.assertEqual(self.rows, again)

    def test_draft_passes_mechanical_checks_but_is_not_frozen(self):
        report = sc.validate(self.rows)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['status'], 'draft_needs_review')
        self.assertFalse(report['frozen'])
        self.assertIn('official_overlap_not_checked', report['warnings'])
        self.assertEqual(report['counts']['unreviewed_documents'], 300)

    def test_requirements(self):
        c = sc.validate(self.rows)['counts']
        self.assertEqual((c['documents'], c['by_direction']), (300, {'request': 150, 'response': 150}))
        self.assertGreaterEqual(c['normal_documents'], 100)
        for kind in sc.CRITICAL_TYPES:
            self.assertGreaterEqual(c['entities_by_type'][kind], 30, kind)
        self.assertGreaterEqual(c['entities_by_type']['PERSONAL_ATTRIBUTE'], 10)

    def test_validator_rejects_each_defect(self):
        def errors(mutate):
            rows = copy.deepcopy(self.rows)
            mutate(rows)
            return sc.validate(rows)['errors']
        self.assertTrue(errors(lambda r: r.pop()))
        bad_offset = next(i for i, r in enumerate(self.rows) if r['entities'])
        self.assertTrue(any(e.startswith('invalid_entity') for e in errors(lambda r: r[bad_offset]['entities'][0].update(end=r[bad_offset]['entities'][0]['end'] + 1))))
        self.assertTrue(any(e.startswith('duplicate_texts') for e in errors(lambda r: r[1].update(text=r[0]['text'], entities=r[0]['entities']))))
        self.assertTrue(any(e.startswith('unrendered_placeholder') for e in errors(lambda r: r[0].update(text=r[0]['text'] + ' ⟦X⟧'))))
        # drop all PII from the first 120 gold documents -> critical counts / normals shift
        def strip(rows):
            for r in [x for x in rows if x['entities']][:120]:
                r['entities'] = []
        self.assertTrue(any(e.startswith('critical_entities') for e in errors(strip)))

    def test_official_overlap_detected(self):
        target = next(r for r in self.rows if r['entities'])
        long_entity = next(e for r in self.rows for e in r['entities'] if len(e['surface']) >= sc.MIN_SURFACE_LEN_FOR_LEAK)
        official = ({hashlib.sha256(target['text'].encode()).hexdigest()}, {long_entity['surface']})
        report = sc.validate(self.rows, official)
        self.assertTrue(any(e.startswith('official_text_overlap') for e in report['errors']))
        self.assertTrue(any(e.startswith('official_surface_overlap') for e in report['errors']))
        self.assertEqual(report['status'], 'invalid')

    def test_reviewed_documents_still_need_explicit_freeze(self):
        rows = copy.deepcopy(self.rows)
        for r in rows:
            r['review_status'] = 'reviewed'
        report = sc.validate(rows, (set(), set()))
        self.assertEqual(report['status'], 'ready_to_freeze')
        self.assertFalse(report['frozen'])

    def test_manifest_records_hash_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'set.jsonl'
            path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in self.rows), encoding='utf-8')
            m = sc.manifest(path, sc.validate(self.rows))
            self.assertEqual(m['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertFalse(m['frozen'])
            self.assertIn('never for dev tuning', m['use_policy'])
            self.assertEqual([r['id'] for r in sc.load(path)], [r['id'] for r in self.rows])


if __name__ == '__main__':
    unittest.main()

