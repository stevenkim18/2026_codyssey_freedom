import json
import tempfile
import unittest
from pathlib import Path

from model.data import candidate_supplement as cs
from model.tests.test_segmentation import FakePlanner
from model.segmentation.splitters import ko_atomic_suffix_v3, whitespace_default_v1


def span(label, text, value):
    a = text.index(value)
    return {'label': label, 'value': value, 'start': a, 'end': a + len(value), 'label_index': 1}


def row(i, text, *spans):
    return {'id': i, 'source_text': text, 'privacy_mask': list(spans), 'language': 'ko', 'region': 'KR', 'split': 'train'}


class MappingTest(unittest.TestCase):
    def test_every_candidate_label_has_exactly_one_decision(self):
        stats_labels = {'ACCOUNT_NUMBER', 'ADDRESS', 'AGE', 'ALIEN_NUMBER', 'BIRTHDATE', 'BLOOD_TYPE', 'CARD_NUMBER', 'CITY',
                        'DEPARTMENT', 'DRIVER_LICENSE', 'EMAIL', 'EMPLOYEE_ID', 'GENDER', 'HEIGHT', 'IP_ADDRESS', 'MAJOR',
                        'MEMBER_ID', 'NAME', 'NATIONALITY', 'NICKNAME', 'PARTICIPANT_ID', 'PASSPORTNUM', 'PHONE', 'POSITION',
                        'RELIGION', 'RRN', 'SCHOOL', 'URL', 'USER_ID', 'VEHICLE_NUMBER', 'WEIGHT', 'WORKPLACE', 'ZIPCODE'}
        self.assertEqual(set(cs.LABEL_MAP), stats_labels)
        self.assertEqual(len(cs.LABEL_MAP), 33)
        for label, (kind, status, _) in cs.LABEL_MAP.items():
            self.assertIn(status, ('confirmed', 'tentative', 'unmapped'))
            self.assertEqual(kind is None, status == 'unmapped', label)

    def test_targets_are_project_types(self):
        plan = json.loads((cs.MODEL_ROOT / 'configs/conversion-plan.json').read_text())['label_mapping']
        for kind, status, _ in cs.LABEL_MAP.values():
            if kind:
                self.assertIn(kind, plan)


class RowTest(unittest.TestCase):
    def test_raw_validation_uses_text_not_other_models_bio(self):
        text = '이름은 김가상입니다'
        self.assertEqual(cs.validate_raw_row(row(1, text, span('NAME', text, '김가상'))), [])
        bad = row(1, text, dict(span('NAME', text, '김가상'), end=9))
        self.assertEqual(cs.validate_raw_row(bad), ['surface_mismatch'])
        self.assertEqual(cs.validate_raw_row(row(1, text, dict(span('NAME', text, '김가상'), start=-1))), ['invalid_span_range'])
        self.assertEqual(cs.validate_raw_row(row(1, text, {'label': 'X', 'value': 'a', 'start': 0, 'end': 1})), ['unknown_label'])
        self.assertEqual(cs.validate_raw_row({'id': 'x'}), ['invalid_row_schema'])

    def test_unmapped_gold_rejects_document_instead_of_deleting_gold(self):
        text = '사번 STAFF-12345 김가상'
        r = row(1, text, span('EMPLOYEE_ID', text, 'STAFF-12345'), span('NAME', text, '김가상'))
        mapped, reason = cs.to_project_row(r, cs.POLICIES['confirmed_and_tentative'])
        self.assertIsNone(mapped)
        self.assertEqual(reason, 'label_not_mapped:EMPLOYEE_ID')

    def test_tentative_policy_gate(self):
        text = '별명 brave_owl8 김가상'
        r = row(1, text, span('NICKNAME', text, 'brave_owl8'), span('NAME', text, '김가상'))
        self.assertIsNone(cs.to_project_row(r, cs.POLICIES['confirmed_only'])[0])
        mapped = cs.to_project_row(r, cs.POLICIES['confirmed_and_tentative'])[0]
        self.assertEqual([e['type'] for e in mapped['entities']], ['USERNAME', 'PERSON'])
        self.assertEqual(mapped['entities'][0]['surface'], 'brave_owl8')

    def test_no_gold_document_is_kept_as_empty_not_inferred_clean(self):
        mapped, reason = cs.to_project_row(row(1, '평범한 문장'), cs.POLICIES['confirmed_only'])
        self.assertEqual((mapped, reason), ({'text': '평범한 문장', 'entities': []}, None))


class AnalyseTest(unittest.TestCase):
    def test_counts_and_overlap_with_fake_planner(self):
        t1 = '이름은 김가상님께서 연락'
        t2 = '사번 STAFF-1 확인'
        dup = '이름은 김가상님께서 연락'
        rows = {'train': [row(1, t1, span('NAME', t1, '김가상')), row(2, t2, span('EMPLOYEE_ID', t2, 'STAFF-1')),
                          row(3, dup, span('NAME', dup, '김가상'))],
                'validation': [row(4, t1, span('NAME', t1, '김가상'))], 'test': []}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            for split, rel in cs.SPLIT_FILES.items():
                (root / rel).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows[split]), encoding='utf-8')
            planners = {'default': FakePlanner(whitespace_default_v1), 'v3': FakePlanner(ko_atomic_suffix_v3)}
            report = cs.analyse(root, planners)
        train = report['splits']['train']
        self.assertEqual((train['documents'], train['unique_texts'], train['duplicate_extra_occurrences']), (3, 2, 1))
        policy = train['policies']['confirmed_only']
        self.assertEqual(policy['rejected_for_unmapped_labels'], 1)
        self.assertEqual(policy['by_planner']['default']['convertible_documents'], 0)
        self.assertEqual(policy['by_planner']['default']['rejected_by_conversion'], {'unaligned_entity_boundary': 2})
        self.assertEqual(policy['by_planner']['v3']['convertible_documents'], 2)
        self.assertEqual(policy['by_planner']['v3']['unique_convertible_texts'], 1)
        self.assertEqual(report['cross_split_duplicate_texts']['train+validation'], 1)
        # no raw text in the report
        self.assertNotIn('김가상', json.dumps(report, ensure_ascii=False))


class LockTest(unittest.TestCase):
    def test_lock_pins_revision_license_and_files(self):
        lock = json.loads(cs.LOCK_PATH.read_text())
        self.assertEqual(lock['revision'], cs.CANDIDATE_REVISION)
        self.assertEqual(len(lock['revision']), 40)
        self.assertEqual(lock['license']['id'], 'CC-BY-4.0')
        self.assertEqual(lock['status'], 'candidate_not_adopted')
        self.assertEqual({f['path'] for f in lock['files']}, {
            'LICENSE', 'README.md', 'dataset_stats.json', 'label_map.json',
            'data/train.jsonl', 'data/validation.jsonl', 'data/test.jsonl'})
        for f in lock['files']:
            self.assertEqual(len(f['sha256']), 64)
            self.assertIn(cs.CANDIDATE_REVISION, f['url'])

    def test_integrity_check_detects_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.txt').write_bytes(b'x')
            import hashlib
            lock = {'files': [{'path': 'a.txt', 'size_bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}]}
            self.assertEqual(cs.verify_files(root, lock), [])
            (root / 'a.txt').write_bytes(b'y')
            self.assertEqual(cs.verify_files(root, lock), ['mismatch:a.txt'])
            (root / 'a.txt').unlink()
            self.assertEqual(cs.verify_files(root, lock), ['missing:a.txt'])


if __name__ == '__main__':
    unittest.main()

