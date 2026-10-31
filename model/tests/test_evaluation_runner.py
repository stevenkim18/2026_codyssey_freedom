"""Regression tests for the unsafe paths observed in stage 7."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from model.evaluation.runner import (InferenceRunError, condition_id, run_inference,
                                     validate_prediction_coverage, require_frozen_holdout)
from model.segmentation.runtime import attach_splitter
from model.evaluation.run_checkpoint import verify_artifacts, score_complete, main as checkpoint_main
from model.evaluation.runner import sha256_file


class Backend:
    def __init__(self, status='complete', raises=False):
        self.status, self.raises = status, raises

    def inspect(self, segments):
        if self.raises:
            raise RuntimeError('private input must not appear in output')
        return SimpleNamespace(status=self.status, findings=[])


ROWS = [{'id': 'a', 'text': 'normal synthetic text', 'entities': []}]


class RunnerTests(unittest.TestCase):
    def test_incomplete_is_not_a_clean_prediction(self):
        for status in ('incomplete', 'error', 'unknown'):
            with self.subTest(status=status), self.assertRaises(InferenceRunError):
                run_inference(Backend(status), ROWS)

    def test_error_is_preserved_without_exception_or_input_text(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'predictions.jsonl'
            with self.assertRaises(InferenceRunError):
                run_inference(Backend(raises=True), ROWS, prediction_path=path)
            row = json.loads(path.read_text())
            self.assertEqual(row['status'], 'error')
            self.assertNotIn('private', path.read_text())
            self.assertNotIn('synthetic text', path.read_text())

    def test_normal_documents_are_explicitly_complete(self):
        predictions, rows = run_inference(Backend(), ROWS)
        self.assertEqual(predictions, {'a': []})
        self.assertEqual(rows[0]['status'], 'complete')
        validate_prediction_coverage(ROWS, rows)

    def test_missing_duplicate_or_incomplete_predictions_cannot_be_scored(self):
        for predictions in ([], [{'document_id': 'a', 'status': 'incomplete', 'findings': []}],
                            [{'document_id': 'a', 'status': 'complete', 'findings': []}] * 2):
            with self.subTest(predictions=predictions), self.assertRaises(InferenceRunError):
                validate_prediction_coverage(ROWS, predictions)

    def test_duplicate_input_ids_rejected_before_inference(self):
        with self.assertRaises(InferenceRunError):
            run_inference(Backend(), ROWS * 2)

    def test_existing_predictions_are_never_overwritten(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'predictions.jsonl'
            path.write_text('existing')
            with self.assertRaises(FileExistsError):
                run_inference(Backend(), ROWS, prediction_path=path)
            self.assertEqual(path.read_text(), 'existing')

    def test_condition_is_independent_of_model_and_sensitive_to_real_settings(self):
        base = {'labels': ['person', 'email address'], 'thresholds': {'PERSON': 0.5}, 'data_sha256': 'a'*64}
        self.assertEqual(condition_id(base), condition_id(dict(reversed(list(base.items())))))
        self.assertNotEqual(condition_id(base), condition_id(dict(base, thresholds={'PERSON': 0.6})))
        self.assertNotEqual(condition_id(base), condition_id(dict(base, labels=list(reversed(base['labels'])))))

    def test_draft_cannot_be_used_as_final_holdout(self):
        with self.assertRaises(InferenceRunError):
            require_frozen_holdout({'frozen': False}, 'a'*64)

    def test_freeze_needs_review_hash_and_freeze_time(self):
        manifest = {'dataset_version':'service-holdout-v2-frozen','frozen': True, 'sha256': 'a'*64, 'reviewer': 'human reviewer',
                    'frozen_at_utc': '2026-10-30T14:00:00+00:00',
                    'validation': {'status': 'ready_to_freeze', 'errors': [], 'warnings': [],
                                   'counts': {'documents': 300, 'unreviewed_documents': 0}}}
        require_frozen_holdout(manifest, 'a'*64)
        for key,value in [('sha256','b'*64),('reviewer',''),('frozen_at_utc',None),
                          ('dataset_version','service-test-v1-draft')]:
            with self.subTest(key=key), self.assertRaises(InferenceRunError):
                require_frozen_holdout(dict(manifest, **{key:value}), 'a'*64)

    def test_changed_checkpoint_is_rejected_before_model_loading(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('model.safetensors','gliner_config.json','tokenizer_config.json','spm.model'):
                (root/name).write_text('fixture')
            evidence={'artifacts':{str(p):{'exists':True,'bytes':p.stat().st_size,'sha256':sha256_file(p)}
                                    for p in root.iterdir()}}
            verify_artifacts(root,evidence)
            (root/'model.safetensors').write_text('changed')
            with self.assertRaises(InferenceRunError):
                verify_artifacts(root,evidence)

    def test_dev_cannot_run_without_frozen_holdout_and_reserves_no_output(self):
        with TemporaryDirectory() as tmp:
            output=Path(tmp)/'run'
            with self.assertRaises(SystemExit):
                checkpoint_main(['--model-dir',tmp,'--manifest',tmp,'--evidence',tmp,
                                 '--output-dir',str(output),'--experiment-id','test','--purpose','dev'])
            self.assertFalse(output.exists())

    def test_scoring_refuses_missing_normal_document(self):
        with self.assertRaises(InferenceRunError):
            score_complete(ROWS,[])


class RuntimeSplitterTests(unittest.TestCase):
    def test_model_processor_is_the_shared_splitter_source(self):
        processor = SimpleNamespace(words_splitter=lambda text: [('old', 0, 3)])
        model = SimpleNamespace(data_processor=processor)
        attach_splitter(model, 'ko_atomic_suffix_v3')
        self.assertEqual(list(processor.words_splitter('김가상에게')),
                         [('김가상', 0, 3), ('에게', 3, 5)])
        self.assertEqual(processor.words_splitter.splitter_id, 'ko_atomic_suffix_v3')
        # B._tokens and GLiNER.prepare_inputs both call this same attribute.
        restored = [(s,e) for _,s,e in processor.words_splitter('김가상에게')]
        self.assertIn((0,3), restored)

    def test_unknown_splitter_cannot_silently_fall_back(self):
        with self.assertRaises(ValueError):
            attach_splitter(SimpleNamespace(data_processor=SimpleNamespace()), 'unknown')
