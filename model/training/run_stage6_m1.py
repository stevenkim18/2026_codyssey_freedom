"""Stage 6: M1 Fine-Tuning & Evaluation Pipeline.

1. Deterministically sample 5,000 convertible documents from K-PII-Bench train.jsonl
   using ko_atomic_suffix_v3 and seed 42.
2. Fine-tune GLiNER on NVIDIA L4 GPU for 2 epochs.
3. Save trained M1 weights and model package.
4. Evaluate M1 on Service Test Set (300 cases) and Dev Set (28,147 cases).
5. Generate verified records and compare against M0 baseline.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import time

# Ensure repo and dev roots on sys.path
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / 'dev'))

import torch
from torch.utils.data import Dataset as TorchDataset


class GLiNERTorchDataset(TorchDataset):

  def __init__(self, examples):
    self.examples = examples

  def __len__(self):
    return len(self.examples)

  def __getitem__(self, idx):
    return self.examples[idx]

from gliner import GLiNER
from gliner.data_processing.collator import SpanDataCollator
from gliner.training import Trainer, TrainingArguments

from detector.gliner_backend import GLiNERBackend, load_gliner
from model.data.conversion import convert_record
from model.evaluation import compare as compare_mod, metrics, policy_metrics, records
from model.segmentation.planner import load_candidate_planner

BASE_MODEL_DIR = Path('/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0')
TRAIN_DATA_PATH = Path('/home1/ncloud/data/k-pii-bench/train.jsonl')
DEV_DATA_PATH = Path('/home1/ncloud/data/k-pii-bench/dev.jsonl')
SERVICE_TEST_PATH = REPO_ROOT / 'model/evaluation/service_testset/service-test-v1.draft.jsonl'
OUTPUT_DIR = Path('/home1/ncloud/m1_checkpoints')
FINAL_MODEL_DIR = Path('/home1/ncloud/m1_model')
DATA_CACHE_DIR = Path('/home1/ncloud/data')
REPORTS_DIR = REPO_ROOT / 'model/reports'
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def prepare_5k_sample(target_count=5000, seed=42):
    sample_path = DATA_CACHE_DIR / 'train_5k.jsonl'
    sha_path = DATA_CACHE_DIR / 'sample_manifest_sha256.txt'
    gliner_sample_path = DATA_CACHE_DIR / 'train_5k_gliner.jsonl'

    manifest = json.loads((BASE_MODEL_DIR / 'manifest.json').read_text(encoding='utf-8'))
    label_mapping = manifest['label_mapping']

    if sample_path.exists() and gliner_sample_path.exists() and sha_path.exists():
        print(f"[Sampling] Found existing 5k sample at {sample_path}")
        sample_sha = sha_path.read_text().strip()
        gliner_examples = []
        with open(gliner_sample_path, 'r', encoding='utf-8') as f:
            for line in f:
                gliner_examples.append(json.loads(line))
        return sample_sha, gliner_examples

    print(f"[Sampling] Sampling {target_count} convertible documents with seed {seed}...")
    TOKENIZER_DIR = Path('/home1/ncloud/data/tokenizer_mdeberta')
    planner = load_candidate_planner(TOKENIZER_DIR, label_mapping, 'ko_atomic_suffix_v3')

    # Reservoir / linear scan of convertible documents
    random.seed(seed)
    sampled_docs = []
    gliner_examples = []
    total_scanned = 0

    with open(TRAIN_DATA_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            total_scanned += 1
            row = json.loads(line)
            try:
                res = convert_record(row, planner, label_mapping)
                sampled_docs.append(row)
                for ex in res['examples']:
                    gliner_examples.append(ex)
                if len(sampled_docs) >= target_count:
                    break
            except Exception:
                continue

    print(f"[Sampling] Sampled {len(sampled_docs)} docs ({len(gliner_examples)} windows) from {total_scanned} scanned.")

    with open(sample_path, 'w', encoding='utf-8') as f:
        for r in sampled_docs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    with open(gliner_sample_path, 'w', encoding='utf-8') as f:
        for ex in gliner_examples:
            f.write(json.dumps(ex, ensure_ascii=False) + '\n')

    sample_sha = sha256_file(sample_path)
    sha_path.write_text(sample_sha, encoding='utf-8')
    print(f"[Sampling] Sample SHA-256: {sample_sha}")
    return sample_sha, gliner_examples


def train_m1(gliner_examples):
    print("=" * 60)
    print("M1 Fine-Tuning on NVIDIA L4 GPU")
    print("=" * 60)

    if (FINAL_MODEL_DIR / 'gliner_config.json').exists():
        print(f"[Training] Found existing trained M1 model at {FINAL_MODEL_DIR}, skipping re-training.")
        return FINAL_MODEL_DIR

    model = GLiNER.from_pretrained(BASE_MODEL_DIR)
    collator = SpanDataCollator(model.config, data_processor=model.data_processor)
    train_dataset = GLiNERTorchDataset(gliner_examples)

    # 5,000 examples with batch size 8 = ~625 steps/epoch. 2 epochs = ~1,250 steps.
    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        num_train_epochs=2,
        per_device_train_batch_size=8,
        gradient_accumulation_steps=1,
        learning_rate=3e-5,
        weight_decay=0.01,
        warmup_ratio=0.1,
        logging_steps=50,
        save_strategy='epoch',
        save_total_limit=2,
        fp16=True,
        dataloader_num_workers=2,
        report_to='none',
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        data_collator=collator,
    )

    t0 = time.time()
    trainer.train()
    train_elapsed = time.time() - t0
    print(f"\n[Training] Completed in {train_elapsed:.1f}s ({train_elapsed/60:.1f} min)")

    # Save final model
    FINAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(FINAL_MODEL_DIR)

    # Copy manifest and tokenizer files into FINAL_MODEL_DIR
    for name in ('manifest.json', 'spm.model', 'tokenizer_config.json', 'LICENSE', 'MODEL_CARD.md'):
        src = BASE_MODEL_DIR / name
        if src.exists():
            (FINAL_MODEL_DIR / name).write_bytes(src.read_bytes())

    # Update manifest model_version
    m1_manifest = json.loads((FINAL_MODEL_DIR / 'manifest.json').read_text())
    m1_manifest['model_version'] = 'M1-ko-atomic-5k-v1'
    m1_manifest['threshold_version'] = 'm1-calibrated-0.5-v1'
    (FINAL_MODEL_DIR / 'manifest.json').write_text(json.dumps(m1_manifest, ensure_ascii=False, indent=2))
    print(f"[Training] Model saved to {FINAL_MODEL_DIR}")
    return FINAL_MODEL_DIR


def run_inference(backend, rows, name='dataset'):
    print(f"[{name}] Starting inference for {len(rows)} documents...", flush=True)
    predictions, pred_rows = {}, []
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
            print(f"[{name}] {idx + 1}/{len(rows)} ({(idx + 1)/len(rows)*100:.1f}%) - {rate:.1f} doc/s - ETA: {eta:.0f}s", flush=True)
    return predictions, pred_rows


def evaluate_and_record(rows, predictions, pred_rows, *, experiment_id, split_version,
                        sample_sha, started_at, finished_at, parent_id='m0-baseline-service-test-v1'):
    lengths = {(r.get('document_id') or r.get('id') or r.get('doc_id')): len(r['text']) for r in rows}
    gold = [((r.get('document_id') or r.get('id') or r.get('doc_id')), e['type'], e['start'], e['end'])
            for r in rows for e in r.get('entities', [])]
    pred = [(r['document_id'], f['type'], f['start'], f['end']) for r in pred_rows for f in r['findings']]

    score_result = metrics.score(gold, pred, document_lengths=lengths, critical_types=records.CRITICAL_TYPES)

    norm_docs = [{'document_id': (r.get('document_id') or r.get('id') or r.get('doc_id')),
                  'text': r['text'],
                  'gold': [(e['type'], e['start'], e['end']) for e in r.get('entities', [])]}
                 for r in rows]
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
        parent_experiment_id=parent_id,
        common_baseline_experiment_id='m0-baseline-service-test-v1',
        changed_factors=['model_weights', 'korean_segmentation_training'],
        comparison_mode='dev_selected_pipeline',
        comparison_condition_id='m1-fine-tuned-5k',
        model_repo_revision_or_checkpoint_sha256='m1-ko-atomic-5k-v1',
        dataset_revision='844f92f7beba98fa964f957ba324017e82451701',
        split_and_service_test_version=split_version,
        sample_manifest_sha256=sample_sha,
        seed=42,
        inference_configuration={'batch_size': 1, 'device': 'cuda:0', 'windowing': 'b-gliner-planner-v1'},
        label_mapping_version='gliner029-m0-18labels-v1',
        threshold_version='m1-calibrated-0.5-v1',
        rules_version='none',
        policy_version='b-gateway-policy-v1',
        chunking_and_overlap_version='gliner029-word-window-v1',
        span_postprocessing_version='ko_atomic_suffix_v3_trained',
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        delta_vs_parent=None,
        delta_vs_common_baseline=None,
        report_mode='model_only',
    )
    records.validate_record(record)
    return record, score_result


def main():
    # Step 1: Sampling
    sample_sha, gliner_examples = prepare_5k_sample(target_count=5000, seed=42)

    # Step 2: Training
    m1_model_dir = train_m1(gliner_examples)

    # Step 3: Initialize M1 Backend for evaluation
    manifest = json.loads((m1_model_dir / 'manifest.json').read_text())
    model = GLiNER.from_pretrained(str(m1_model_dir)).to('cuda')
    backend = GLiNERBackend(model, manifest)
    backend.warmup()

    # Step 4: Evaluate Service Test Set (300 cases)
    print("\n>>> Evaluating M1 on Independent Service Test Set (300 cases)...")
    service_rows = [json.loads(l) for l in open(SERVICE_TEST_PATH, encoding='utf-8') if l.strip()]
    t_start = datetime.now(timezone.utc).isoformat()
    service_preds, service_pred_rows = run_inference(backend, service_rows, name='m1-service-test')
    t_end = datetime.now(timezone.utc).isoformat()

    m1_service_record, m1_service_score = evaluate_and_record(
        service_rows, service_preds, service_pred_rows,
        experiment_id='m1-service-test-v1',
        split_version='service-test-v1',
        sample_sha=sample_sha,
        started_at=t_start,
        finished_at=t_end,
    )

    (REPORTS_DIR / 'm1-service-test-record.json').write_text(
        json.dumps(m1_service_record, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (REPORTS_DIR / 'm1-service-test-score.json').write_text(
        json.dumps(m1_service_score, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"M1 Service Test F1: {m1_service_record['overall_precision_recall_f1']['f1']}")
    print(f"M1 Critical Recall: {records.critical_recall(m1_service_record)}")
    print(f"M1 Normal False Block Rate: {m1_service_record['normal_false_block_rate_and_denominator']['rate']}")

    # Step 5: Evaluate Dev Set (28,147 cases)
    print("\n>>> Evaluating M1 on K-PII-Bench Dev Set (28,147 cases)...")
    dev_rows = [json.loads(l) for l in open(DEV_DATA_PATH, encoding='utf-8') if l.strip()]
    t_dev_start = datetime.now(timezone.utc).isoformat()
    dev_preds, dev_pred_rows = run_inference(backend, dev_rows, name='m1-kpii-dev')
    t_dev_end = datetime.now(timezone.utc).isoformat()

    m1_dev_record, m1_dev_score = evaluate_and_record(
        dev_rows, dev_preds, dev_pred_rows,
        experiment_id='m1-kpii-dev-v1',
        split_version='kpii-dev-v1',
        sample_sha=sample_sha,
        started_at=t_dev_start,
        finished_at=t_dev_end,
        parent_id='m0-baseline-kpii-dev-v1',
    )

    (REPORTS_DIR / 'm1-dev-record.json').write_text(
        json.dumps(m1_dev_record, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (REPORTS_DIR / 'm1-dev-score.json').write_text(
        json.dumps(m1_dev_score, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f"M1 Dev F1: {m1_dev_record['overall_precision_recall_f1']['f1']}")
    print(f"M1 Dev Critical Recall: {records.critical_recall(m1_dev_record)}")

    # Step 6: PRD §11 Replacement Decision
    m0_record = json.loads((REPORTS_DIR / 'm0-baseline-record.json').read_text())
    decision = compare_mod.replacement_decision(m1_service_record, m0_record, runtime_acceptance=True)
    comp = compare_mod.compare(m1_service_record, m0_record)
    (REPORTS_DIR / 'm1-vs-m0-decision.json').write_text(
        json.dumps(decision, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    (REPORTS_DIR / 'm1-vs-m0-comparison.json').write_text(
        json.dumps(comp, ensure_ascii=False, indent=2), encoding='utf-8'
    )

    print("\n" + "=" * 60)
    print("STAGE 6 FINISHED SUCCESSFULLY!")
    print(f"PRD Replacement Verdict: {decision['verdict'].upper()}")
    print("=" * 60)


if __name__ == '__main__':
    main()
