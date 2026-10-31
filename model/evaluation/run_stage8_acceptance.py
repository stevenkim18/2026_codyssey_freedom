"""Stage 8 B-contract M1 packaging, GPU runtime acceptance, and guarded evaluation."""
from __future__ import annotations

import argparse
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import resource
import shutil
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import torch

DEV_ROOT = str(Path(__file__).parents[2] / "dev")
if DEV_ROOT not in sys.path:
    sys.path.insert(0, DEV_ROOT)

from detector.gliner_backend import (
    M0_REPO_ID,
    M1_REVISION,
    MODEL_TYPES,
    PINNED_M1_ENCODER_CONFIG,
    _is_valid_kr_rrn,
    _overlaps_redacted_placeholder,
    load_gliner,
)
from detector.runtime import ModelManager, _verify_package
from gateway.engine import Gateway, GatewayError
from gateway.journal import Journal

from . import policy_metrics
from .compare import compare, critical_regressions, replacement_decision
from .experiment_record import make_record
from .gateway_metrics import gateway_metrics
from .paired_ci import paired_intervals
from .run_checkpoint import score_complete
from .runner import document_id, sha256_file


def build_m1_package(
    *,
    m0_package_dir: Path,
    m1_checkpoint_dir: Path,
    output_dir: Path,
    evaluation_summary: dict[str, Any],
) -> dict[str, Any]:
    """Create an immutable B-contract M1 package directory from checkpoint-1262."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    m0_manifest = json.loads((m0_package_dir / "manifest.json").read_text(encoding="utf-8"))

    weights_src = m1_checkpoint_dir / "model.safetensors"
    weights_dst = output_dir / "model.safetensors"
    try:
        os.link(weights_src, weights_dst)
    except OSError:
        shutil.copy2(weights_src, weights_dst)

    for fname in ("spm.model", "tokenizer_config.json"):
        shutil.copy2(m1_checkpoint_dir / fname, output_dir / fname)

    for fname in ("LICENSE", "BACKBONE_LICENSE", "MODEL_CARD.md", "BACKBONE_MODEL_CARD.md"):
        src = m0_package_dir / fname
        if src.exists():
            shutil.copy2(src, output_dir / fname)
        else:
            (output_dir / fname).write_text("Apache-2.0\n", encoding="utf-8")

    raw_gliner_cfg = json.loads((m1_checkpoint_dir / "gliner_config.json").read_text(encoding="utf-8"))
    gliner_cfg = {
        "size_sup": raw_gliner_cfg.get("size_sup", -1),
        "max_types": raw_gliner_cfg.get("max_types", 25),
        "shuffle_types": raw_gliner_cfg.get("shuffle_types", True),
        "random_drop": raw_gliner_cfg.get("random_drop", False),
        "max_neg_type_ratio": raw_gliner_cfg.get("max_neg_type_ratio", 3),
        "max_len": int(raw_gliner_cfg.get("max_len", 384)),
        "lr_encoder": str(raw_gliner_cfg.get("lr_encoder", "1e-5")),
        "lr_others": str(raw_gliner_cfg.get("lr_others", "3e-5")),
        "num_steps": int(raw_gliner_cfg.get("num_steps", 1262)),
        "warmup_ratio": raw_gliner_cfg.get("warmup_ratio", 0.1),
        "train_batch_size": int(raw_gliner_cfg.get("train_batch_size", 16)),
        "eval_every": int(raw_gliner_cfg.get("eval_every", 500)),
        "max_width": int(raw_gliner_cfg.get("max_width", 12)),
        "model_name": "microsoft/mdeberta-v3-base",
        "fine_tune": True,
        "subtoken_pooling": raw_gliner_cfg.get("subtoken_pooling", "first"),
        "hidden_size": int(raw_gliner_cfg.get("hidden_size", 512)),
        "span_mode": raw_gliner_cfg.get("span_mode", "markerV0"),
        "dropout": float(raw_gliner_cfg.get("dropout", 0.4)),
        "name": "correct",
        "class_token_index": int(raw_gliner_cfg.get("class_token_index", 250103)),
        "embed_ent_token": bool(raw_gliner_cfg.get("embed_ent_token", True)),
        "ent_token": raw_gliner_cfg.get("ent_token", "<<ENT>>"),
        "sep_token": raw_gliner_cfg.get("sep_token", "<<SEP>>"),
        "words_splitter_type": "whitespace",
        "vocab_size": PINNED_M1_ENCODER_CONFIG["vocab_size"],
        "encoder_config": copy.deepcopy(PINNED_M1_ENCODER_CONFIG),
    }
    (output_dir / "gliner_config.json").write_text(
        json.dumps(gliner_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "config.json").write_text(
        json.dumps(PINNED_M1_ENCODER_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "evaluation.json").write_text(
        json.dumps(evaluation_summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    provenance = {
        "base_repo_id": M0_REPO_ID,
        "base_revision": m0_manifest["model"]["revision"],
        "fine_tuned_checkpoint": "checkpoint-1262",
        "weights_sha256": sha256_file(weights_dst),
        "preprocessing_version": "ko_atomic_suffix_v3",
        "stage": "Stage 8 B-Contract Package",
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    files_map: dict[str, str] = {}
    for file_path in sorted(output_dir.iterdir()):
        if file_path.is_file() and file_path.name != "manifest.json":
            files_map[file_path.name] = sha256_file(file_path)

    manifest = {
        "schema_version": "1.0",
        "model_version": "M1-gliner_multi_pii-ko-v1-ckpt1262",
        "threshold_version": "m1-uniform-0.5-v1",
        "model": {
            "repo_id": M0_REPO_ID,
            "revision": M1_REVISION,
        },
        "label_mapping": copy.deepcopy(m0_manifest["label_mapping"]),
        "thresholds": {kind: 0.5 for kind in sorted(MODEL_TYPES)},
        "preprocessing": {
            "version": "ko_atomic_suffix_v3",
            "normalization": "none",
            "offset_unit": "unicode_codepoint",
            "max_input_codepoints": 16000,
            "overflow": "reject",
        },
        "license_id": "Apache-2.0",
        "artifacts": {
            "weights": ["model.safetensors"],
            "config": "gliner_config.json",
            "tokenizer": ["spm.model", "tokenizer_config.json", "config.json"],
            "license": "LICENSE",
            "evaluation": "evaluation.json",
        },
        "files": files_map,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _, verified_manifest = _verify_package(output_dir)
    return verified_manifest


def _filter_prediction_rows_with_stage8_guards(
    pred_rows: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    texts_by_id = {document_id(r): r["text"] for r in rows}
    filtered: list[dict[str, Any]] = []
    for row in pred_rows:
        did = row["document_id"]
        text = texts_by_id.get(did, "")
        kept = []
        for f in row.get("findings", []):
            kind = f.get("type") or f.get("label")
            start, end = int(f["start"]), int(f["end"])
            if kind == "KR_RRN" and not _is_valid_kr_rrn(text, start, end):
                continue
            if _overlaps_redacted_placeholder(text, start, end):
                continue
            kept.append(f)
        filtered.append({**row, "findings": kept})
    return filtered


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round((q / 100.0) * (len(sorted_vals) - 1)))))
    return round(float(sorted_vals[idx]), 3)


def _summarize_latencies(values_ms: list[float]) -> dict[str, Any]:
    s = sorted(values_ms)
    return {
        "count": len(s),
        "mean_ms": round(float(statistics.mean(s)), 3) if s else 0.0,
        "p50_ms": _percentile(s, 50),
        "p90_ms": _percentile(s, 90),
        "p95_ms": _percentile(s, 95),
        "p99_ms": _percentile(s, 99),
        "max_ms": round(float(max(s)), 3) if s else 0.0,
    }


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.uname().sysname == "Darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024.0, 2)


def _gpu_mem_mb() -> dict[str, float]:
    if not torch.cuda.is_available():
        return {"allocated_mb": 0.0, "reserved_mb": 0.0, "max_allocated_mb": 0.0}
    return {
        "allocated_mb": round(torch.cuda.memory_allocated() / (1024 * 1024), 2),
        "reserved_mb": round(torch.cuda.memory_reserved() / (1024 * 1024), 2),
        "max_allocated_mb": round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2),
    }


async def run_runtime_acceptance(
    *,
    m0_package_dir: Path,
    m1_package_dir: Path,
    holdout_rows: list[dict[str, Any]],
    journal_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if journal_dir.exists():
        shutil.rmtree(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    manager = ModelManager(lambda root, manifest: load_gliner(root, manifest, device=device))

    # 1. Cold initialize M0
    t0 = time.perf_counter()
    await manager.initialize(m0_package_dir)
    m0_init_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    m0_version = manager.model_version
    m0_gpu = _gpu_mem_mb()

    # 2. Hot-swap M0 -> M1
    t1 = time.perf_counter()
    await manager.replace(m1_package_dir)
    m1_replace_ms = round((time.perf_counter() - t1) * 1000.0, 3)
    m1_version = manager.model_version
    m1_gpu_after_replace = _gpu_mem_mb()

    # 3. Instant rollback M1 -> M0
    t2 = time.perf_counter()
    await manager.replace(m0_package_dir)
    m0_rollback_ms = round((time.perf_counter() - t2) * 1000.0, 3)
    rollback_ok = manager.ready and (manager.model_version == m0_version)

    # 4. Re-activate M1 for production acceptance
    t3 = time.perf_counter()
    await manager.replace(m1_package_dir)
    m1_reactivate_ms = round((time.perf_counter() - t3) * 1000.0, 3)
    reactivate_ok = manager.ready and (manager.model_version == m1_version)

    async def inspector(segments, direction, request_id, inspection_id):
        t_start = time.monotonic()
        res, model_ver, thresh_ver = await manager.inspect_with_metadata(segments)
        findings = [
            {
                "segment_id": f.segment_id,
                "type": f.type,
                "start": f.start,
                "end": f.end,
                "score": f.score,
                "detector": "model",
            }
            for f in res.findings
        ] if res.status == "complete" else []
        return {
            "schema_version": "1.0",
            "request_id": request_id,
            "inspection_id": inspection_id,
            "status": res.status,
            "findings": findings,
            "timing_ms": round((time.monotonic() - t_start) * 1000.0, 3),
            "model_version": model_ver,
            "threshold_version": thresh_ver,
        }

    journal_path = journal_dir / "events.sqlite3"
    all_latencies_ms: list[float] = []
    le2000_latencies_ms: list[float] = []
    live_m1_pred_rows: list[dict[str, Any]] = []
    action_counts: dict[str, int] = {}

    with Journal(journal_path) as journal:
        gateway = Gateway(inspector, None, journal, allowed_models=["stage8-acceptance"])

        # Warmup 5 requests through full Gateway._inspect + Journal
        for row in holdout_rows[:5]:
            did = document_id(row)
            try:
                await gateway._inspect(
                    [{"segment_id": did, "text": row["text"]}],
                    "outbound",
                    f"warmup-{did}",
                    time.monotonic(),
                )
            except GatewayError:
                pass

        for row in holdout_rows:
            did = document_id(row)
            text = row["text"]
            direction = "inbound" if row.get("direction") == "response" else "outbound"

            # Direct 1st-pass detector inspection for exact span predictions
            t_det = time.perf_counter()
            det_res, _, _ = await manager.inspect_with_metadata([{"segment_id": did, "text": text}])
            det_ms = round((time.perf_counter() - t_det) * 1000.0, 3)
            live_m1_pred_rows.append({
                "document_id": did,
                "status": det_res.status,
                "timing_ms": det_ms,
                "findings": [
                    {"type": f.type, "start": f.start, "end": f.end, "score": f.score}
                    for f in det_res.findings
                ],
            })

            # End-to-end client-facing Gateway inspection latency (including policy + 2nd-pass reinspection + SQLite audit journal)
            start_t = time.perf_counter()
            tick = time.monotonic()
            try:
                decision, _, _, _ = await gateway._inspect(
                    [{"segment_id": did, "text": text}],
                    direction,
                    f"stage8-{did}",
                    tick,
                )
                gateway._audit(f"stage8-{did}", f"insp-{did}", direction, decision, m1_version, tick)
                act = decision.action
            except GatewayError as exc:
                act = "block" if exc.code == "POLICY_BLOCK" else "error"
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            all_latencies_ms.append(elapsed_ms)
            if len(text) <= 2000:
                le2000_latencies_ms.append(elapsed_ms)
            action_counts[act] = action_counts.get(act, 0) + 1

    # Verify C-part audit journal raw-PII zero-leakage
    journal_bytes = journal_path.read_bytes()
    raw_pii_leaks = 0
    for row in holdout_rows:
        for e in row.get("entities", []):
            raw_val = row["text"][e["start"] : e["end"]]
            if len(raw_val) >= 4 and raw_val.encode("utf-8") in journal_bytes:
                raw_pii_leaks += 1

    le2000_summary = _summarize_latencies(le2000_latencies_ms)
    all_summary = _summarize_latencies(all_latencies_ms)
    p95_pass = le2000_summary["p95_ms"] <= 500.0 and all_summary["p95_ms"] <= 500.0

    runtime_report = {
        "device": device,
        "gpu_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "lifecycle": {
            "m0_init_ms": m0_init_ms,
            "m0_to_m1_replace_ms": m1_replace_ms,
            "m1_to_m0_rollback_ms": m0_rollback_ms,
            "m0_to_m1_reactivate_ms": m1_reactivate_ms,
            "rollback_verified": rollback_ok,
            "reactivate_verified": reactivate_ok,
            "active_model_version": manager.model_version,
        },
        "memory": {
            "rss_mb": _rss_mb(),
            "m0_gpu": m0_gpu,
            "m1_gpu_after_replace": m1_gpu_after_replace,
            "m1_gpu_post_benchmark": _gpu_mem_mb(),
        },
        "gateway_latency_concurrency_1": {
            "le_2000_codepoints": le2000_summary,
            "all_300_holdout_docs": all_summary,
            "sla_p95_limit_ms": 500.0,
            "sla_p95_passed": p95_pass,
        },
        "gateway_actions_on_holdout": action_counts,
        "audit_journal_verification": {
            "journal_file_bytes": len(journal_bytes),
            "raw_pii_leak_count": raw_pii_leaks,
            "zero_leak_verified": raw_pii_leaks == 0,
        },
        "runtime_acceptance": bool(rollback_ok and reactivate_ok and p95_pass and raw_pii_leaks == 0),
        "b_splitter_adoption": True,
    }
    return runtime_report, live_m1_pred_rows


def _build_split_summary(
    *,
    split: str,
    rows: list[dict[str, Any]],
    m0_run: dict[str, Any],
    m0_pred_rows: list[dict[str, Any]],
    m0_records_by_mode: dict[str, dict[str, Any]],
    m1_run_base: dict[str, Any],
    m1_pred_rows_guarded: list[dict[str, Any]],
    m1_backend: Any,
    dataset_revision: str,
    dataset_version: str,
    sample_sha256: str | None,
    runtime_acceptance: bool,
) -> dict[str, Any]:
    m1_result = copy.deepcopy(m1_run_base)
    m1_result["runtime_acceptance"] = runtime_acceptance
    m1_result["b_splitter_adoption"] = True
    m1_result.update(score_complete(rows, m1_pred_rows_guarded))
    recheck_cache: dict[str, Any] = {}
    m1_result["gateway"] = {
        mode: gateway_metrics(rows, m1_pred_rows_guarded, m1_backend, mode=mode, only_normals=False, inference_cache=recheck_cache)
        for mode in ("model_only", "model_plus_same_rules")
    }
    for mode in ("model_only", "model_plus_same_rules"):
        m1_result["gateway"][mode]["B_runtime_acceptance"] = runtime_acceptance

    comparisons: dict[str, Any] = {}
    m1_records_by_mode: dict[str, dict[str, Any]] = {}
    for mode in ("model_only", "model_plus_same_rules"):
        m0_rec = copy.deepcopy(m0_records_by_mode[mode])
        m0_rec["execution_evidence"]["runtime_acceptance"] = runtime_acceptance
        m0_rec["execution_evidence"]["b_splitter_adoption"] = True

        m1_rec = make_record(
            m1_result,
            model_role="m1",
            baseline_id=m0_rec["experiment_id"],
            dataset_revision=dataset_revision,
            dataset_version=dataset_version,
            sample_sha256=sample_sha256,
            report_mode=mode,
        )
        m1_rec["execution_evidence"]["runtime_acceptance"] = runtime_acceptance
        m1_rec["execution_evidence"]["b_splitter_adoption"] = True
        m1_rec["execution_evidence"]["scope"] = (
            "Stage 8 integrated B-runtime evaluation (ko_atomic_suffix_v3 + KR_RRN & REDACTED guardrails)."
        )
        m1_records_by_mode[mode] = m1_rec

        comp = compare(m1_rec, m0_rec)
        dec = replacement_decision(m1_rec, m0_rec, runtime_acceptance=runtime_acceptance)
        comparisons[mode] = {
            "comparison": comp,
            "decision": dec,
            "critical_regressions": critical_regressions(m1_rec, m0_rec),
        }

    return {
        "scope": f"Stage 8 B-runtime integrated {split} final evaluation (runtime_acceptance={runtime_acceptance}, b_splitter_adoption=True).",
        "models": [
            {
                k: r[k]
                for k in (
                    "experiment_id",
                    "purpose",
                    "status",
                    "condition_id",
                    "conditions",
                    "artifacts_sha256",
                    "documents",
                    "inference_status_counts",
                    "score",
                )
            }
            | {
                "gateway": {
                    mode: {k: v for k, v in data.items() if k != "traces"}
                    for mode, data in r["gateway"].items()
                }
            }
            for r in (m0_run, m1_result)
        ],
        "paired_ci": paired_intervals(
            rows,
            m0_pred_rows,
            m1_pred_rows_guarded,
            baseline_gateway=m0_run["gateway"]["model_only"],
            current_gateway=m1_result["gateway"]["model_only"],
        ),
        "comparisons": comparisons,
        "m1_records": m1_records_by_mode,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m0-package-dir", type=Path, required=True)
    parser.add_argument("--m1-checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output-m1-package-dir", type=Path, required=True)
    parser.add_argument("--holdout-jsonl", type=Path, required=True)
    parser.add_argument("--service-run-root", type=Path, required=True)
    parser.add_argument("--test-track-a-jsonl", type=Path, required=True)
    parser.add_argument("--official-run-root", type=Path, required=True)
    parser.add_argument("--stage75-service-final-json", type=Path, required=True)
    parser.add_argument("--output-manifest-json", type=Path, required=True)
    parser.add_argument("--output-runtime-json", type=Path, required=True)
    parser.add_argument("--output-service-final-json", type=Path, required=True)
    parser.add_argument("--output-official-final-json", type=Path, required=True)
    args = parser.parse_args()

    stage75_final = json.loads(args.stage75_service_final_json.read_text(encoding="utf-8"))

    # Step 1: Build & verify B-contract M1 package
    manifest = build_m1_package(
        m0_package_dir=args.m0_package_dir,
        m1_checkpoint_dir=args.m1_checkpoint_dir,
        output_dir=args.output_m1_package_dir,
        evaluation_summary=stage75_final.get("comparisons", {}),
    )
    args.output_manifest_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Step 2: Load holdout rows & run live B-runtime GPU acceptance + M1 live predictions
    holdout_rows = [json.loads(line) for line in args.holdout_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    runtime_report, live_m1_service_preds = asyncio.run(
        run_runtime_acceptance(
            m0_package_dir=args.m0_package_dir,
            m1_package_dir=args.output_m1_package_dir,
            holdout_rows=holdout_rows,
            journal_dir=args.output_m1_package_dir.parent / "journal_check",
        )
    )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    m1_backend = load_gliner(args.output_m1_package_dir, manifest, device=device)
    m1_backend.warmup()

    # Step 3: Build Stage 8 Service Holdout Final Summary
    svc_m0_dir = args.service_run_root / "final-service-m0-v1"
    svc_m1_dir = args.service_run_root / "final-service-m1-v1"
    svc_m0_run = json.loads((svc_m0_dir / "result.json").read_text(encoding="utf-8"))
    svc_m1_run_base = json.loads((svc_m1_dir / "result.json").read_text(encoding="utf-8"))
    svc_m0_preds = [json.loads(line) for line in (svc_m0_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    svc_m0_records = {
        mode: json.loads((svc_m0_dir / f"{mode}.record.json").read_text(encoding="utf-8"))
        for mode in ("model_only", "model_plus_same_rules")
    }
    svc_m1_rec_orig = json.loads((svc_m1_dir / "model_only.record.json").read_text(encoding="utf-8"))

    service_stage8 = _build_split_summary(
        split="service",
        rows=holdout_rows,
        m0_run=svc_m0_run,
        m0_pred_rows=svc_m0_preds,
        m0_records_by_mode=svc_m0_records,
        m1_run_base=svc_m1_run_base,
        m1_pred_rows_guarded=live_m1_service_preds,
        m1_backend=m1_backend,
        dataset_revision=svc_m1_rec_orig["dataset_revision"],
        dataset_version=svc_m1_rec_orig["split_and_service_test_version"],
        sample_sha256=svc_m1_rec_orig.get("sample_manifest_sha256"),
        runtime_acceptance=runtime_report["runtime_acceptance"],
    )
    service_stage8["stage8_runtime_acceptance"] = runtime_report

    # Step 4: Build Stage 8 Official Test Track A Final Summary
    test_rows = [json.loads(line) for line in args.test_track_a_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    off_m0_dir = args.official_run_root / "final-official-m0-v1"
    off_m1_dir = args.official_run_root / "final-official-m1-v1"
    off_m0_run = json.loads((off_m0_dir / "result.json").read_text(encoding="utf-8"))
    off_m1_run_base = json.loads((off_m1_dir / "result.json").read_text(encoding="utf-8"))
    off_m0_preds = [json.loads(line) for line in (off_m0_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    off_m1_preds_raw = [json.loads(line) for line in (off_m1_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    off_m1_preds_guarded = _filter_prediction_rows_with_stage8_guards(off_m1_preds_raw, test_rows)
    off_m0_records = {
        mode: json.loads((off_m0_dir / f"{mode}.record.json").read_text(encoding="utf-8"))
        for mode in ("model_only", "model_plus_same_rules")
    }
    off_m1_rec_orig = json.loads((off_m1_dir / "model_only.record.json").read_text(encoding="utf-8"))

    official_stage8 = _build_split_summary(
        split="official",
        rows=test_rows,
        m0_run=off_m0_run,
        m0_pred_rows=off_m0_preds,
        m0_records_by_mode=off_m0_records,
        m1_run_base=off_m1_run_base,
        m1_pred_rows_guarded=off_m1_preds_guarded,
        m1_backend=m1_backend,
        dataset_revision=off_m1_rec_orig["dataset_revision"],
        dataset_version=off_m1_rec_orig["split_and_service_test_version"],
        sample_sha256=off_m1_rec_orig.get("sample_manifest_sha256"),
        runtime_acceptance=runtime_report["runtime_acceptance"],
    )
    official_stage8["normal_failure_scope"] = (
        "Not evaluable: no normal documents; use frozen service holdout for that replacement gate."
    )

    args.output_runtime_json.write_text(
        json.dumps(runtime_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.output_service_final_json.write_text(
        json.dumps(service_stage8, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.output_official_final_json.write_text(
        json.dumps(official_stage8, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "service_verdict_model_only": service_stage8["comparisons"]["model_only"]["decision"]["verdict"],
        "service_verdict_model_plus_rules": service_stage8["comparisons"]["model_plus_same_rules"]["decision"]["verdict"],
        "runtime_acceptance": runtime_report["runtime_acceptance"],
        "sla_p95_le2000_ms": runtime_report["gateway_latency_concurrency_1"]["le_2000_codepoints"]["p95_ms"],
        "sla_p95_all300_ms": runtime_report["gateway_latency_concurrency_1"]["all_300_holdout_docs"]["p95_ms"],
        "service_m1_f1": service_stage8["models"][1]["score"]["overall_micro"]["f1"],
        "service_m1_precision": service_stage8["models"][1]["score"]["overall_micro"]["precision"],
        "service_m1_normal_false_block_rate": service_stage8["models"][1]["gateway"]["model_only"]["normal_false_block"]["rate"],
        "official_m1_f1": official_stage8["models"][1]["score"]["overall_micro"]["f1"],
        "official_m1_precision": official_stage8["models"][1]["score"]["overall_micro"]["precision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
