"""Local interactive PII detector demo using M1 model package.

Runs entirely offline on CPU/Mac without requiring CUDA or GPU server.
Uses GLiNERBackend from dev/detector/gliner_backend.py directly.
"""
from pathlib import Path
import json
import sys
import time

# Ensure dev module is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "dev"))
sys.path.insert(0, str(REPO_ROOT))

from detector.gliner_backend import load_gliner, GLiNERBackend
from detector.schemas import Segment


def main():
    print("=" * 65)
    print("🚀 Korean PII Gateway - Local M1 Offline Demo (Apple Silicon / CPU)")
    print("=" * 65)

    pkg_dir = REPO_ROOT / "model/.cache/models/m1-checkpoint-1262-ko-atomic-v3"
    manifest_path = pkg_dir / "manifest.json"
    if not pkg_dir.exists() or not manifest_path.exists():
        print(f"[!] Error: Model package not found at {pkg_dir}")
        print("[!] Please ensure model weights exist in model/.cache/models/")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"[*] Loading M1 model package ({manifest['model_version']})...")
    t0 = time.perf_counter()
    backend = load_gliner(pkg_dir, manifest, device="cpu")
    backend.warmup()
    load_time = time.perf_counter() - t0
    print(f"[+] Model loaded and warmed up successfully in {load_time:.2f}s!")
    print(f"[+] Preprocessing: {manifest['preprocessing']['version']} (Korean Word Splitter active)")
    print(f"[+] Labels ({len(backend.labels)} categories): {', '.join(manifest['label_mapping'].keys())}")
    print("-" * 65)

    # Sample sentences
    test_samples = [
        "안녕하세요, 홍길동 고객님. 요청하신 주민등록번호는 900101-1234567 입니다.",
        "문의사항은 test_user@naver.com 으로 메일 주시거나 010-9876-5432로 연락 부탁드립니다.",
        "환불 계좌는 국민은행 123-456-789012 이고, 배송지는 서울시 강남구 테헤란로 123 입니다.",
        "내일 오후 2시에 강남역 3번 출구 스타벅스에서 회의가 예정되어 있습니다."
    ]

    print("[*] Running instant self-test on 4 sample sentences:")
    for idx, sample in enumerate(test_samples, 1):
        t_start = time.perf_counter()
        seg = Segment(segment_id=f"seg_{idx}", text=sample)
        result = backend.inspect([seg])
        latency_ms = (time.perf_counter() - t_start) * 1000

        findings = result.findings if result.status == "complete" else []
        print(f"\n[Sample {idx}] \"{sample}\" (Latency: {latency_ms:.1f}ms, Status: {result.status})")
        if not findings:
            print("  -> (No PII detected - Safe Document)")
        for f in findings:
            span_text = sample[f.start:f.end]
            print(f"  -> [{f.type}] \"{span_text}\" (score: {f.score:.4f}, span: {f.start}:{f.end})")

    print("\n" + "=" * 65)
    print("💬 Interactive Demo Mode: Enter custom text (type 'q' or 'exit' to quit)")
    print("=" * 65)

    try:
        count = 0
        while True:
            text = input("\n[Input Text] > ").strip()
            if not text:
                continue
            if text.lower() in ("q", "exit", "quit"):
                print("Exiting demo. Bye!")
                break

            count += 1
            t_start = time.perf_counter()
            seg = Segment(segment_id=f"user_{count}", text=text)
            result = backend.inspect([seg])
            latency_ms = (time.perf_counter() - t_start) * 1000

            findings = result.findings if result.status == "complete" else []
            print(f"[*] Detected {len(findings)} PII entity/entities in {latency_ms:.1f}ms (Status: {result.status}):")
            if not findings:
                print("    - No PII found.")
            for f in findings:
                span_text = text[f.start:f.end]
                print(f"    - [{f.type}] \"{span_text}\" (score: {f.score:.4f}, span: {f.start}..{f.end})")
    except (KeyboardInterrupt, EOFError):
        print("\nExiting demo.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
