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


def run_inference(backend, rows, name='dataset'):
    print(f"[{name}] Starting inference for {len(rows)} documents on GPU...", flush=True)
    predictions = {}
    pred_rows = []
    t0 = time.time()
    for idx, row in enumerate(rows):
        doc_id = row.get('document_id') or row.get('id') or row.get('doc_id')
        text = row['text']
        res = backend.inspect([{'segment_id': doc_id, 'text': text}])
        findings = [{'type': f.type, 'start': f.start, 'end': f.end, 'score': f.score} for f in res.findings]
        predictions[doc_id] = findings
        pred_rows.append({'document_id': doc_id, 'findings': findings})
        if (idx + 1) % 1000 == 0 or (idx + 1) == len(rows):
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed
            eta = (len(rows) - (idx + 1)) / rate if rate > 0 else 0
            print(f"[{name}] {idx + 1}/{len(rows)} ({((idx + 1)/len(rows))*100:.1f}%) - {rate:.1f} doc/s - ETA: {eta:.0f}s", flush=True)
    return predictions, pred_rows


def evaluate_set(dataset_rows, predictions, pred_rows, *, experiment_id, split_version, started_at, finished_at):
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
    print("=" * 60)
    print("M0 Baseline Measurement on NVIDIA L4 GPU")
    print(f"Model package: {MODEL_PACKAGE_DIR}")
    print("=" * 60)

    # 1. Load model
    manifest = json.loads((MODEL_PACKAGE_DIR / 'manifest.json').read_text())
    backend = load_gliner(MODEL_PACKAGE_DIR, manifest)
    backend.model.to('cuda')
    backend.warmup()
    print("M0 backend initialized on GPU successfully.\n")

    # 2. Evaluate Service Test Set (300 cases)
    print(">>> Phase 1: Evaluating Independent Service Test Set (300 cases)...")
    service_rows = load_dataset(SERVICE_TEST_PATH)
    t_start = datetime.now(timezone.utc).isoformat()
    service_preds, service_pred_rows = run_inference(backend, service_rows, name='service-test')
    t_end = datetime.now(timezone.utc).isoformat()

    service_record, service_score = evaluate_set(
        service_rows, service_preds, service_pred_rows,
        experiment_id='m0-baseline-service-test-v1',
        split_version='service-test-v1',
        started_at=t_start,
        finished_at=t_end,
    )

    # Save service test outputs
    with open(REPORTS_DIR / 'm0-service-test-preds.jsonl', 'w', encoding='utf-8') as f:
        for r in service_pred_rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    (REPORTS_DIR / 'm0-baseline-record.json').write_text(
        json.dumps(service_record, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (REPORTS_DIR / 'm0-service-test-score.json').write_text(
        json.dumps(service_score, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"Service test record saved to {REPORTS_DIR / 'm0-baseline-record.json'}")
    print(f"Overall F1: {service_record['overall_precision_recall_f1']['f1']}")
    print(f"Critical Recall: {records.critical_recall(service_record)}")
    print(f"Normal False Block Rate: {service_record['normal_false_block_rate_and_denominator']['rate']}\n")

    # 3. Evaluate Dev Set (28,147 cases)
    if DEV_DATA_PATH.exists():
        print(">>> Phase 2: Evaluating K-PII-Bench Dev Set (28,147 cases)...")
        dev_rows = load_dataset(DEV_DATA_PATH)
        t_dev_start = datetime.now(timezone.utc).isoformat()
        dev_preds, dev_pred_rows = run_inference(backend, dev_rows, name='kpii-dev')
        t_dev_end = datetime.now(timezone.utc).isoformat()

        dev_record, dev_score = evaluate_set(
            dev_rows, dev_preds, dev_pred_rows,
            experiment_id='m0-baseline-kpii-dev-v1',
            split_version='kpii-dev-v1',
            started_at=t_dev_start,
            finished_at=t_dev_end,
        )

        with open(REPORTS_DIR / 'm0-dev-preds.jsonl', 'w', encoding='utf-8') as f:
            for r in dev_pred_rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

        (REPORTS_DIR / 'm0-dev-record.json').write_text(
            json.dumps(dev_record, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        (REPORTS_DIR / 'm0-dev-score.json').write_text(
            json.dumps(dev_score, ensure_ascii=False, indent=2), encoding='utf-8'
        )
        print(f"Dev record saved to {REPORTS_DIR / 'm0-dev-record.json'}")
        print(f"Dev Overall F1: {dev_record['overall_precision_recall_f1']['f1']}")
        print(f"Dev Critical Recall: {records.critical_recall(dev_record)}")
        print(f"Dev Normal False Block Rate: {dev_record['normal_false_block_rate_and_denominator']['rate']}\n")
    else:
        print(f"Warning: {DEV_DATA_PATH} not found, skipping dev set evaluation.")

    print("=" * 60)
    print("M0 Baseline Measurement Finished Successfully!")
    print("=" * 60)


if __name__ == '__main__':
    main()

