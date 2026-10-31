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
from model.segmentation.runtime import attach_splitter

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
    """Prepare a NEW domain/type-aware unique sample; never reuse legacy prefix cache."""
    from model.data.prepare_sample import prepare_sample
    mapping=json.loads((REPO_ROOT/'model/configs/conversion-plan.json').read_text())['label_mapping']
    audit=json.loads((REPO_ROOT/'model/reports/data-audit-v2.json').read_text())
    lock=json.loads((REPO_ROOT/'model/configs/assets.lock.json').read_text())['dataset']
    expected=next(x['digest']['value'] for x in lock['artifacts'] if x['path']=='data/train.jsonl')
    planner=load_candidate_planner(Path('/home1/ncloud/data/tokenizer_mdeberta'),mapping,'ko_atomic_suffix_v3')
    output=DATA_CACHE_DIR/f'stage7_5-train-{target_count}-seed{seed}'
    manifest=prepare_sample(TRAIN_DATA_PATH,output,planner,mapping,
                            domains=audit['splits']['train']['domain_counts'],count=target_count,
                            seed=seed,source_sha256=expected)
    examples=[json.loads(line) for line in (output/'train.gliner.jsonl').read_text().splitlines()]
    return manifest['files_sha256']['train.sample.jsonl'], examples


def train_m1(gliner_examples, *, holdout_manifest, holdout_dataset):
    from model.evaluation.runner import require_frozen_holdout
    require_frozen_holdout(json.loads(Path(holdout_manifest).read_text()),sha256_file(holdout_dataset))
    print("=" * 60)
    print("M1 Fine-Tuning on NVIDIA L4 GPU")
    print("=" * 60)

    if (FINAL_MODEL_DIR / 'gliner_config.json').exists():
        raise RuntimeError('Existing model is preserved; evaluate it with verified artifact hashes, or configure a new training experiment directory.')

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


# Both historical entry points use the complete-only implementation.
from model.evaluation.runner import run_inference, validate_prediction_coverage


def evaluate_and_record(rows, predictions, pred_rows, *, experiment_id, split_version,
                        sample_sha, started_at, finished_at, parent_id='m0-baseline-service-test-v1'):
    validate_prediction_coverage(rows, pred_rows)
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
        comparison_condition_id='legacy-unverified-do-not-compare',
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
    raise SystemExit('Historical entry point disabled. Use python -m model.evaluation.run_checkpoint --help with a fresh experiment ID and verified artifacts.')


if __name__ == '__main__':
    raise SystemExit('Historical train+test pipeline disabled: recover existing checkpoints, use run_checkpoint; new training requires a frozen holdout and verified sample/config manifest.')
