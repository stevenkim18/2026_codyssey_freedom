import json
import random
import re
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from model.evaluation.holdout_v2 import build, write_draft, freeze, FreshValues
from model.evaluation.service_cases import TYPES
from model.evaluation.runner import InferenceRunError, require_frozen_holdout, sha256_file
from model.evaluation.service_cases import validate


class HoldoutTests(unittest.TestCase):
    def test_no_canonical_schema_keys_in_document_text(self):
        for row in build():
            for kind in TYPES:
                self.assertIsNone(re.search(r'\b'+kind+r'\b',row['text']),row['id'])

    def test_particle_tags_require_actual_entity_adjacent_particle(self):
        for row in build():
            if 'particle' in row['tags']:
                self.assertTrue(any(row['text'][e['end']:e['end']+1] in set('은는이가을를와과')
                                    for e in row['entities']),row['id'])

    def test_normal_families_and_attribute_vocabulary_are_diverse(self):
        rows=build()
        self.assertGreaterEqual(len({r['generation'].get('normal_family') for r in rows if not r['entities']}),8)
        for kind in ['NRP','PERSONAL_ATTRIBUTE']:
            self.assertGreaterEqual(len({e['surface'] for r in rows for e in r['entities'] if e['type']==kind}),12)
        for kind in ['KR_RRN','KR_BANK_ACCOUNT','CREDIT_CARD','KR_PHONE_NUMBER','EMAIL_ADDRESS']:
            self.assertGreaterEqual(len({r['generation'].get('format_id') for r in rows
                                        if any(e['type']==kind for e in r['entities'])}),8)

    def test_synthetic_identifier_format_regressions(self):
        values=FreshValues(random.Random(42))
        for _ in range(300):
            self.assertRegex(values.KR_PASSPORT(),r'^M(?:[0-9]{8}|[0-9]{3}[A-HJ-NP-Z][0-9]{4})$')
            rrn=values.KR_RRN().replace('-','')
            self.assertIn(rrn[6],'34' if int(rrn[:2])<26 else '12')
            license=values.KR_DRIVER_LICENSE().split('-')
            self.assertNotEqual(license[0],'27')
            self.assertIn(int(license[1]),range(20,26))
            plate=values.KR_VEHICLE_PLATE()
            prefix=re.match(r'\d+',plate).group()
            self.assertFalse(len(prefix)==3 and prefix.startswith('0'))
            phone=values.KR_PHONE_NUMBER()
            digits=re.sub(r'\D','',phone)
            middle=(digits[4:] if phone.startswith('+82') else digits[2:] if digits.startswith('02') else digits[3:])
            self.assertIn(middle[0],'23456789')

    def setup_files(self,root):
        official=root/'official';official.mkdir()
        for split in ['train','dev','test_track_a']:
            (official/(split+'.jsonl')).write_text(json.dumps({'text':'unrelated fixture '+split,'entities':[]})+'\n')
        v1=root/'v1.jsonl';v1.write_text('')
        return official,v1

    def test_builder_is_deterministic_and_does_not_review_cases(self):
        rows=build()
        self.assertEqual(rows,build())
        result=validate(rows)
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['counts']['documents'],300)
        self.assertEqual(result['counts']['normal_documents'],100)
        self.assertEqual(result['counts']['unreviewed_documents'],300)

    def test_draft_cannot_freeze_with_unreviewed_decisions(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);official,v1=self.setup_files(root)
            manifest=write_draft(root/'draft',official,v1)
            self.assertIs(manifest['frozen'],False)
            with self.assertRaises(InferenceRunError):
                freeze(root/'draft',root/'draft/review-decisions.json',root/'frozen',official)
            self.assertFalse((root/'frozen').exists())

    def test_fixture_reviews_freeze_new_hash_and_require_all_ids(self):
        # These are temporary synthetic test attestations, never real holdout review.
        with TemporaryDirectory() as tmp:
            root=Path(tmp);official,v1=self.setup_files(root)
            manifest=write_draft(root/'draft',official,v1)
            decisions_path=root/'draft/review-decisions.json'
            decisions=json.loads(decisions_path.read_text());decisions['reviewer']='unit-test-fixture'
            for item in decisions['decisions']:item['approved']=True
            missing=dict(decisions,decisions=decisions['decisions'][:-1])
            decisions_path.write_text(json.dumps(missing))
            with self.assertRaises(InferenceRunError):freeze(root/'draft',decisions_path,root/'frozen',official)
            decisions_path.write_text(json.dumps(decisions))
            result=freeze(root/'draft',decisions_path,root/'frozen',official)
            digest=sha256_file(root/'frozen/service-holdout-v2.frozen.jsonl')
            require_frozen_holdout(result,digest)
            self.assertNotEqual(result['sha256'],manifest['sha256'])
            self.assertEqual(result['validation']['counts']['unreviewed_documents'],0)
