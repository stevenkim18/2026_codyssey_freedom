"""M0 Baseline Evaluation Runner.

Runs real M0 inference on GPU for:
1) Independent Service Test Set (300 cases) -> model/reports/m0-baseline-record.json
2) K-PII-Bench Dev Set (28,147 cases) -> model/reports/m0-dev-record.json

Validates generated records against schema and generates human-readable summary.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'dev'))

from detector.gliner_backend import load_gliner
from model.segmentation.runtime import attach_splitter
from model.evaluation import metrics, policy_metrics, records

MODEL_PACKAGE_DIR = Path('/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0')
SERVICE_TEST_PATH = REPO_ROOT / 'model/evaluation/service_testset/service-test-v1.draft.jsonl'
DEV_DATA_PATH = Path('/home1/ncloud/data/k-pii-bench/dev.jsonl')
REPORTS_DIR = REPO_ROOT / 'model/reports'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_dataset(path):
    rows = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


# Both historical entry points use the complete-only implementation.
from model.evaluation.runner import run_inference, validate_prediction_coverage


def evaluate_set(dataset_rows, predictions, pred_rows, *, experiment_id, split_version, started_at, finished_at):
    validate_prediction_coverage(dataset_rows, pred_rows)
    lengths = {(r.get('document_id') or r.get('id') or r.get('doc_id')): len(r['text']) for r in dataset_rows}
    gold = [((r.get('document_id') or r.get('id') or r.get('doc_id')), e['type'], e['start'], e['end'])
            for r in dataset_rows for e in r.get('entities', [])]
    pred = [(r['document_id'], f['type'], f['start'], f['end']) for r in pred_rows for f in r['findings']]

    score_result = metrics.score(gold, pred, document_lengths=lengths, critical_types=records.CRITICAL_TYPES)

    norm_docs = [{'document_id': (r.get('document_id') or r.get('id') or r.get('doc_id')),
                  'text': r['text'],
                  'gold': [(e['type'], e['start'], e['end']) for e in r.get('entities', [])]}
                 for r in dataset_rows]
    fb_result = policy_metrics.false_block_metrics(norm_docs, predictions)
    normal_fb = {
        'false_blocks': fb_result['false_block_count'],
        'normal_documents': fb_result['normal_documents'],
        'rate': fb_result['false_block_rate'],
    }

    record = records.record_from_score(
        score_result,
        normal_false_block=normal_fb,
        experiment_id=experiment_id,
        parent_experiment_id=None,
        common_baseline_experiment_id=experiment_id,
        changed_factors=[],
        comparison_mode='dev_selected_pipeline',
        comparison_condition_id='m0-baseline',
        model_repo_revision_or_checkpoint_sha256='1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d',
        dataset_revision='844f92f7beba98fa964f957ba324017e82451701',
        split_and_service_test_version=split_version,
        sample_manifest_sha256=None,
        seed=42,
        inference_configuration={'batch_size': 1, 'device': 'cuda:0', 'windowing': 'b-gliner-planner-v1'},
        label_mapping_version='gliner029-m0-18labels-v1',
        threshold_version='engineering-0.5-v1',
        rules_version='none',
        policy_version='b-gateway-policy-v1',
        chunking_and_overlap_version='gliner029-word-window-v1',
        span_postprocessing_version='raw_whitespace_split_m0',
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        delta_vs_parent=None,
        delta_vs_common_baseline=None,
        report_mode='model_only',
    )

    records.validate_record(record)
    return record, score_result


def main():
    raise SystemExit('Historical entry point disabled. Use python -m model.evaluation.run_checkpoint --help with a fresh experiment ID and verified artifacts.')


if __name__ == '__main__':
    raise SystemExit('Historical entry point disabled: use python -m model.evaluation.run_checkpoint --help; outputs must have a new experiment ID.')

