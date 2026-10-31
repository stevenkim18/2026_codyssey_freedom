"""Offline whole-document conversion/export and aggregate-report oracles."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from model.data.convert_dataset import run_conversion
from model.tests.helpers import change_lock, dataset_fixture, record
from model.tests.test_conversion import FakePlanner, LABELS, gold


class DiagnosticPlanner(FakePlanner):
    metadata = {'scope': 'synthetic interface fake; no actual tokenizer'}


class ConversionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.output = self.root / 'output'
        self.plan = self.root / 'plan.json'
        self.plan.write_text(json.dumps({
            'label_mapping': LABELS,
            'status': 'diagnostic_only_A_B_mapping_not_adopted',
            'duplicate_policy': 'preserve',
            'required_unique_training_documents': 5000,
        }))

    def prepare(self, rows=None):
        self.lock = dataset_fixture(self.source, rows)

    def convert(self, planner=None, labels=None, progress=None):
        return run_conversion(self.source, self.output, planner or DiagnosticPlanner(),
                              LABELS if labels is None else labels, self.lock, self.plan, progress)

    def exported(self, split='train', kind='diagnostic'):
        path = self.output / (split + '.' + kind + '.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()]

    def test_source_document_and_gold_ledgers_balance(self):
        self.prepare({'train': [record()], 'dev': [record('d2', 't2', '이가상')],
                      'test_track_a': [record('d3', 't3', '박가상')]})
        result = self.convert()
        for split, summary in result['splits'].items():
            self.assertEqual(summary['source_documents'], 1)
            self.assertEqual(summary['source_documents'],
                             summary['converted_documents'] + summary['rejected_documents'])
            self.assertEqual(summary['source_gold_entities'],
                             summary['converted_source_gold_entities'] + summary['rejected_source_gold_entities'])
            self.assertEqual(len(self.exported(split, 'ledger')), 1)

    def test_a_single_unaligned_gold_rejects_the_whole_source_row(self):
        text = '김가상 이가상에게'
        row = record(text=text, entities=[gold(text, 0, 3), gold(text, 4, 7)])
        self.prepare({'train': [row]})
        result = self.convert()
        summary = result['splits']['train']
        self.assertEqual(summary['converted_documents'], 0)
        self.assertEqual(summary['rejected_documents'], 1)
        self.assertEqual(summary['rejected_source_gold_entities'], 2)
        self.assertEqual(summary['converted_source_gold_entities'], 0)
        self.assertEqual(self.exported(), [])
        self.assertEqual(self.exported(kind='ledger'), [
            {'source_line': 1, 'status': 'rejected', 'reason': 'unaligned_entity_boundary'}])

    def test_mixed_good_and_bad_rows_have_explicit_source_lines(self):
        bad = record('d2', 't2', '이가상에게', [gold('이가상에게', 0, 3)])
        self.prepare({'train': [record(), bad]})
        result = self.convert()
        summary = result['splits']['train']
        self.assertEqual((summary['source_documents'], summary['converted_documents'],
                          summary['rejected_documents']), (2, 1, 1))
        ledger = self.exported(kind='ledger')
        self.assertEqual([x['source_line'] for x in ledger], [1, 2])
        self.assertEqual([x['status'] for x in ledger], ['converted', 'rejected'])
        self.assertEqual(ledger[0]['first_output_line'], 1)
        self.assertEqual(ledger[0]['example_count'], 1)

    def test_original_source_files_are_byte_identical_after_conversion(self):
        self.prepare({'train': [record()]})
        before = {p.name: p.read_bytes() for p in (self.source/'data').glob('*.jsonl')}
        self.convert()
        after = {p.name: p.read_bytes() for p in (self.source/'data').glob('*.jsonl')}
        self.assertEqual(before, after)

    def test_every_file_integrity_is_checked_before_creating_output(self):
        self.prepare({'train': [record()], 'dev': [record('d2', 't2', '이가상')]})
        path = self.source/'data/dev.jsonl'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'dataset_integrity_mismatch'):
            self.convert()
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved(self):
        self.prepare({'train': [record()]})
        self.output.mkdir()
        marker = self.output/'keep.txt'
        marker.write_text('existing evidence')
        with self.assertRaisesRegex(ValueError, 'output_already_exists'):
            self.convert()
        self.assertEqual(marker.read_text(), 'existing evidence')
        self.assertEqual(len(list(self.output.iterdir())), 1)

    def test_mismatched_labels_are_rejected_before_output(self):
        self.prepare({'train': [record()]})
        labels = dict(LABELS, PERSON='different name')
        with self.assertRaisesRegex(ValueError, 'mapping_plan_mismatch'):
            self.convert(labels=labels)
        self.assertFalse(self.output.exists())

    def test_reordered_labels_do_not_change_fixed_prompt_provenance(self):
        self.prepare({'train': [record()]})
        labels = dict(reversed(list(LABELS.items())))
        with self.assertRaisesRegex(ValueError, 'mapping_plan_mismatch'):
            self.convert(labels=labels)
        self.assertFalse(self.output.exists())

    def test_fixed_label_inventory_is_exported_even_if_row_uses_one_type(self):
        self.prepare({'train': [record()]})
        self.convert()
        self.assertEqual(self.exported()[0]['ner_labels'], list(LABELS.values()))
        self.assertEqual(set(self.exported()[0]), {'tokenized_text', 'ner', 'ner_labels'})

    def test_diagnostic_export_never_claims_training_ready(self):
        self.prepare({'train': [record()]})
        result = self.convert()
        self.assertFalse(result['training_ready'])
        self.assertFalse(result['training_performed'])
        self.assertEqual(result['status'], 'blocked_compatibility')
        self.assertEqual(result['label_mapping_status'], 'diagnostic_only_A_B_mapping_not_adopted')
        self.assertFalse(result['gates']['a_b_label_mapping_adopted'])
        self.assertFalse(result['gates']['minimum_train_count_met'])
        self.assertTrue(result['gates']['all_source_documents_representable'])

    def test_unicode_offset_ledger_roundtrips_to_original_gold(self):
        text = '👩\u200d💻 e\u0301 김가상'
        start = text.index('김가상')
        entity = gold(text, start, start+3)
        self.prepare({'train': [record(text=text, entities=[entity])]})
        self.convert()
        example = self.exported()[0]
        window = self.exported(kind='ledger')[0]['windows'][0]
        offsets = window['word_offsets']
        for token, (left, right) in zip(example['tokenized_text'], offsets):
            self.assertEqual(text[left:right], token)
        for first, last, label in example['ner']:
            self.assertEqual((offsets[first][0], offsets[last][1], label),
                             (entity['start'], entity['end'], 'person'))

    def test_aggregate_report_contains_no_document_text_values_or_record_hashes(self):
        text = 'PRIVATE_SOURCE_CANARY'
        self.prepare({'train': [record(doc_id='PRIVATE_DOCUMENT_ID', text=text,
                                       entities=[gold(text, 0, len(text))])]})
        result = self.convert()
        rendered = json.dumps(result, ensure_ascii=False)
        for excluded in (text, 'PRIVATE_DOCUMENT_ID', hashlib.sha256(text.encode()).hexdigest()):
            self.assertNotIn(excluded, rendered)
        self.assertEqual(self.exported()[0]['tokenized_text'], [text])

    def test_negative_example_is_explicit_and_has_no_invented_gold(self):
        self.prepare({'train': [record(text='맑은 하늘', entities=[])]})
        result = self.convert()
        self.assertEqual(result['splits']['train']['source_gold_entities'], 0)
        self.assertEqual(result['splits']['train']['converted_documents'], 1)
        self.assertEqual(self.exported()[0]['ner'], [])

    def test_duplicate_policy_preserves_rows_and_counts_unique_texts(self):
        self.prepare({'train': [record(), record('d2', 't1')]})
        result = self.convert()
        summary = result['splits']['train']
        self.assertEqual(result['duplicate_policy'], 'preserve')
        self.assertEqual(summary['converted_documents'], 2)
        self.assertEqual(summary['unique_converted_texts'], 1)
        self.assertEqual(len(self.exported()), 2)

    def test_partial_gold_window_never_becomes_negative_training_example(self):
        text = 'a b c d e f g h i'
        self.prepare({'train': [record(text=text, entities=[gold(text, 8, 13)])]})
        planner = DiagnosticPlanner(max_span=3, windows=[(0, 11), (6, 17)])
        result = self.convert(planner)
        self.assertEqual(result['splits']['train']['rejected_documents'], 1)
        self.assertEqual(self.exported(), [])
        self.assertEqual(self.exported(kind='ledger')[0]['reason'], 'partial_entity_in_window')

    def test_max_width_rejection_and_alignment_counts_are_visible(self):
        text = ' '.join('w'+str(i) for i in range(13))
        self.prepare({'train': [record(text=text, entities=[gold(text, 0, len(text))])]})
        result = self.convert()
        summary = result['splits']['train']
        self.assertEqual(summary['alignment_by_type']['too_wide']['PERSON'], 1)
        self.assertEqual(summary['rejection_reasons']['entity_exceeds_max_width'], 1)
        self.assertEqual(summary['rejected_source_gold_entities'], 1)

    def test_artifact_hashes_and_completion_match_written_export(self):
        self.prepare({'train': [record()]})
        result = self.convert()
        completion = json.loads((self.output/'completion.json').read_text())
        self.assertEqual(completion, result)
        for summary in result['splits'].values():
            for artifact in summary['diagnostic_artifacts'].values():
                path = self.output/artifact['filename']
                self.assertEqual(path.stat().st_size, artifact['size_bytes'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), artifact['sha256'])

    def test_declared_document_count_mismatch_has_no_completion_marker(self):
        self.prepare({'train': [record()]})
        change_lock(self.lock, lambda x: x['dataset']['splits']['train'].update(
            expected_documents_from_upstream_metadata=2))
        with self.assertRaisesRegex(ValueError, 'source_document_count_mismatch'):
            self.convert()
        self.assertFalse((self.output/'completion.json').exists())

    def test_planner_window_failure_is_an_explicit_document_rejection(self):
        class BrokenWindows(DiagnosticPlanner):
            def windows(self, text):
                raise ValueError('untrusted planner content')
        self.prepare({'train': [record()]})
        result = self.convert(BrokenWindows())
        self.assertEqual(result['splits']['train']['rejected_documents'], 1)
        self.assertEqual(self.exported(kind='ledger')[0]['reason'], 'planning_incomplete')

    def test_planner_token_failure_is_an_explicit_document_rejection(self):
        class BrokenTokens(DiagnosticPlanner):
            def tokens(self, text):
                raise ValueError('untrusted planner content')
        self.prepare({'train': [record()]})
        result = self.convert(BrokenTokens())
        summary = result['splits']['train']
        self.assertEqual(summary['rejected_documents'], 1)
        self.assertEqual(summary['rejected_source_gold_entities'], 1)
        ledger = self.exported(kind='ledger')
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]['status'], 'rejected')
        self.assertNotIn('untrusted planner content', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
