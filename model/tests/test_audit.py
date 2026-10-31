import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from model.data.audit import audit_dataset, write_report
from model.tests.helpers import change_lock, dataset_fixture, record


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def audit(self, records=None, raw=None, sample_limit=20):
        lock = dataset_fixture(self.root, records, raw)
        return audit_dataset(self.root, lock, sample_limit=sample_limit)

    def error(self, result, name):
        self.assertEqual(result['status'], 'failed')
        self.assertGreater(result['errors'].get(name, 0), 0)

    def test_complete_valid_dataset_and_distributions(self):
        result = self.audit({'train': [record()], 'dev': [record('d2', 't2', '이가상')],
                             'test_track_a': [record('d3', 't3', '박가상')]})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['total_documents'], 3)
        self.assertEqual(result['splits']['train']['label_counts']['PERSON'], 1)
        self.assertEqual(result['splits']['dev']['domain_counts']['chat'], 1)
        self.assertTrue(all(x['integrity']['matches'] for x in result['splits'].values()))

    def test_unicode_codepoint_offsets_preserve_emoji_and_combining_text(self):
        text = '👩‍💻 e\u0301 김가상'
        start = text.index('김가상')
        row = record(text=text, entities=[{'type':'PERSON','start':start,'end':start+3,'surface':'김가상'}])
        result = self.audit({'train':[row]})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['splits']['train']['text_length']['max'], len(text))

    def test_repeated_surface_uses_supplied_offset_not_first_occurrence(self):
        row = record(text='김가상과 김가상', entities=[{'type':'PERSON','start':5,'end':8,'surface':'김가상'}])
        self.assertEqual(self.audit({'train':[row]})['status'], 'passed')

    def test_negative_example_is_counted(self):
        result = self.audit({'train':[record(text='맑은 하늘',entities=[])]})
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['splits']['train']['negative_documents'], 1)

    def test_incorrect_surface(self):
        row = record()
        row['entities'][0]['surface'] = '틀린값'
        self.error(self.audit({'train':[row]}), 'entity_surface_mismatch')

    def test_invalid_offsets_and_boolean_offsets(self):
        for start,end in [(-1,3),(0,4),(2,1),(1,1),(False,3),(0,True)]:
            with self.subTest(start=start,end=end):
                folder = self.root / str(len(list(self.root.iterdir())))
                folder.mkdir()
                row = record()
                row['entities'][0].update(start=start,end=end)
                lock = dataset_fixture(folder, {'train':[row]})
                self.error(audit_dataset(folder,lock), 'invalid_entity_span')

    def test_korean_particle_in_surface_is_detected_by_gold_slice(self):
        row=record(text='김가상에게')
        row['entities'][0]['end']=5
        self.error(self.audit({'train':[row]}),'entity_surface_mismatch')

    def test_unknown_entity_type(self):
        row=record()
        row['entities'][0]['type']='UNKNOWN_CANARY'
        self.error(self.audit({'train':[row]}),'unknown_entity_type')

    def test_duplicate_entity_requires_review(self):
        row=record()
        row['entities'].append(copy.deepcopy(row['entities'][0]))
        result=self.audit({'train':[row]})
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['warnings']['duplicate_entity'],1)

    def test_overlapping_entities_are_reported_without_removal(self):
        row=record()
        row['entities'].append({'type':'PERSON','start':1,'end':3,'surface':'가상'})
        result=self.audit({'train':[row]})
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['warnings']['overlapping_entity_pair'],1)
        self.assertEqual(result['splits']['train']['entity_count'],2)

    def test_same_split_template_reuse_is_allowed(self):
        result=self.audit({'train':[record(),record('d2','t1','이가상')]})
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['splits']['train']['unique_template_ids'],1)

    def test_same_split_duplicate_text_requires_review(self):
        result=self.audit({'train':[record(),record('d2','t1')]})
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['warnings']['duplicate_text_within_split'],1)
        self.assertEqual(result['splits']['train']['duplicate_text_groups'],1)
        self.assertEqual(result['splits']['train']['duplicate_text_occurrences'],1)
        self.assertEqual(result['splits']['train']['duplicate_annotation_conflicts'],0)

    def test_duplicate_text_with_different_gold_is_blocking(self):
        other=record('d2','t1')
        other['entities'][0]['type']='EMAIL_ADDRESS'
        other['bio_tags']=['B-EMAIL_ADDRESS']
        self.error(self.audit({'train':[record(),other]}),'duplicate_text_annotation_conflict')

    def test_annotation_order_does_not_create_false_conflict(self):
        entities=[{'type':'PERSON','start':0,'end':3,'surface':'김가상'},
                  {'type':'PERSON','start':4,'end':7,'surface':'이가상'}]
        first=record(text='김가상 이가상',entities=entities)
        other=record('d2','t1','김가상 이가상',list(reversed(entities)))
        result=self.audit({'train':[first,other]})
        self.assertNotIn('duplicate_text_annotation_conflict',result['errors'])

    def test_missing_label_support_is_explicitly_zero(self):
        result=self.audit({'train':[record()]})
        self.assertEqual(result['splits']['train']['label_counts']['EMAIL_ADDRESS'],0)
        self.assertEqual(result['splits']['train']['labels_without_entities'],['EMAIL_ADDRESS'])

    def test_duplicate_document_id_within_split_is_error(self):
        self.error(self.audit({'train':[record(),record('d1','t2','이가상')]}),'duplicate_doc_id_within_split')

    def test_cross_split_template_ids_and_skeletons_are_blocking(self):
        result=self.audit({'train':[record()], 'dev':[record('d2','t1','이가상')]})
        self.error(result,'cross_split_template_id')
        self.error(result,'cross_split_template_text')
        self.assertEqual(result['cross_split_overlap']['train__dev']['template_id'],1)

    def test_same_skeleton_with_different_ids_is_detected(self):
        other=record('d2','t2','이가상')
        other['template_text']=record()['template_text']
        result=self.audit({'train':[record()], 'dev':[other]})
        self.error(result,'cross_split_template_text')
        self.assertEqual(result['cross_split_overlap']['train__dev']['template_id'],0)

    def test_cross_split_text_and_document_ids_are_detected(self):
        result=self.audit({'train':[record()], 'test_track_a':[record('d1','t2')]})
        self.error(result,'cross_split_text')
        self.error(result,'cross_split_doc_id')

    def test_template_id_must_keep_same_skeleton(self):
        row=record('d2','t1','이가상')
        row['template_text']='다른 문장 틀'
        self.error(self.audit({'train':[record(),row]}),'template_id_skeleton_conflict')

    def test_invalid_json_utf8_duplicate_keys_and_nonfinite_numbers(self):
        for i,content in enumerate([b'{oops}\n',b'\xff\n',b'{"text":"a","text":"b"}\n',b'{"value":NaN}\n']):
            with self.subTest(content=content):
                folder=self.root/str(i);folder.mkdir()
                lock=dataset_fixture(folder,raw={'train':content})
                self.error(audit_dataset(folder,lock),'invalid_json_or_utf8')

    def test_missing_required_fields_and_nonobject_record(self):
        row=record();del row['entities']
        self.error(self.audit({'train':[row]}),'invalid_record_schema')

    def test_nonobject_record_is_rejected(self):
        self.error(self.audit(raw={'train':b'[]\n'}),'invalid_record_schema')

    def test_escaped_surrogate_identifier_is_reported_without_crashing(self):
        row=record(doc_id='\ud800')
        lock=dataset_fixture(self.root)
        content=(json.dumps(row)+'\n').encode()
        path=self.root/'data/train.jsonl';path.write_bytes(content)
        def update(value):
            value['dataset']['splits']['train']['expected_documents_from_upstream_metadata']=1
            value['dataset']['artifacts'][0].update(size_bytes=len(content),digest={'algorithm':'sha256','value':hashlib.sha256(content).hexdigest()})
        change_lock(lock,update)
        self.error(audit_dataset(self.root,lock),'invalid_unicode_string')

    def test_exponent_overflow_is_not_accepted_as_finite_json(self):
        self.error(self.audit(raw={'train':b'{"value":1e9999}\n'}),'invalid_json_or_utf8')

    def test_upstream_distribution_disagreement_is_reported(self):
        lock=dataset_fixture(self.root,{'train':[record()]})
        path=self.root/'statistics.json'
        path.write_text(json.dumps({'splits':{split:{'domains':{},'skeleton_count':0} for split in ('train','dev','test_track_a')}}))
        result=audit_dataset(self.root,lock,statistics_path=path)
        self.error(result,'domain_statistics_mismatch')
        self.error(result,'skeleton_statistics_mismatch')

    def test_invalid_field_types_are_not_coerced(self):
        row=record();row['entities']='not a list'
        self.error(self.audit({'train':[row]}),'invalid_record_schema')

    def test_token_lengths_tags_and_offsets(self):
        cases=[('token_alignment_length',lambda r:r.update(bio_tags=[])),
               ('unknown_bio_tag',lambda r:r.update(bio_tags=['B-UNKNOWN'])),
               ('invalid_token_offset',lambda r:r.update(token_offsets=[[0,99]])),
               ('invalid_token_offset',lambda r:r.update(token_offsets=[[False,3]]))]
        for i,(error,mutate) in enumerate(cases):
            folder=self.root/str(i);folder.mkdir()
            row=record();mutate(row)
            lock=dataset_fixture(folder,{'train':[row]})
            self.error(audit_dataset(folder,lock),error)

    def test_unreplaced_placeholder_in_text_is_error(self):
        row=record(text='<<PERSON:1>>',entities=[])
        self.error(self.audit({'train':[row]}),'unreplaced_placeholder')

    def test_unknown_domain_and_locale_are_errors(self):
        row=record();row.update(domain='UNKNOWN',locale='en-US')
        result=self.audit({'train':[row]})
        self.error(result,'unknown_domain');self.error(result,'unexpected_locale')

    def test_file_digest_and_size_mismatch_are_blocking(self):
        lock=dataset_fixture(self.root,{'train':[record()]})
        path=self.root/'data/train.jsonl'
        path.write_bytes(path.read_bytes()+b'\n')
        self.error(audit_dataset(self.root,lock),'file_integrity_mismatch')

    def test_wrong_row_count_is_detected(self):
        lock=dataset_fixture(self.root,{'train':[record()]})
        change_lock(lock,lambda x:x['dataset']['splits']['train'].update(expected_documents_from_upstream_metadata=2))
        self.error(audit_dataset(self.root,lock),'document_count_mismatch')

    def test_report_contains_no_text_entity_values_or_per_record_hash(self):
        canary='PRIVATE_CANARY_DO_NOT_LOG'
        result=self.audit({'train':[record(text=canary,entities=[{'type':'PERSON','start':0,'end':1,'surface':'WRONG_CANARY'}])]})
        rendered=json.dumps(result,ensure_ascii=False)
        for forbidden in [canary,'WRONG_CANARY',hashlib.sha256(canary.encode()).hexdigest()]:
            self.assertNotIn(forbidden,rendered)

    def test_issue_samples_are_bounded_but_all_errors_are_counted(self):
        rows=[]
        for i in range(30):
            row=record(str(i),str(i),'가상'+str(i));row['entities'][0]['surface']='wrong'
            rows.append(row)
        result=self.audit({'train':rows},sample_limit=2)
        self.assertEqual(result['errors']['entity_surface_mismatch'],30)
        self.assertLessEqual(len(result['issue_examples']),2)

    def test_missing_file_produces_failed_report(self):
        lock=dataset_fixture(self.root,{'train':[record()]})
        (self.root/'data/dev.jsonl').unlink()
        self.error(audit_dataset(self.root,lock),'missing_file')

    def test_report_refuses_to_overwrite_existing_evidence(self):
        result=self.audit({'train':[record()]})
        path=self.root/'report.json'
        write_report(result,path)
        with self.assertRaises(FileExistsError): write_report(result,path)

    def test_long_text_is_counted_in_full(self):
        row=record(text='가'*16001,entities=[])
        result=self.audit({'train':[row]})
        self.assertEqual(result['splits']['train']['text_length']['max'],16001)
        self.assertEqual(result['splits']['train']['over_gateway_limit_documents'],1)


if __name__=='__main__': unittest.main()
