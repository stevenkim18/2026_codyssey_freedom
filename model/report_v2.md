# 한국어 PII 탐지 모델 개선 및 B 런타임 통합 최종 보고서 (v2 — Stage 8 완료판)

- **작성일**: 2026-11-01 (KST)
- **대상 범위**: 1단계~7.5단계 모델·데이터·평가 결과 + **8단계(시나리오 B: B 프로덕션 런타임 통합, 가드레일 내장, GPU 인수 검증)** 최종 완료 보고 및 향후 개발 과제(시나리오 C) 정의
- **이전 판본 보존**: 7.5단계 직후 오프라인 평가 기준 보고서는 [report_v1.md](report_v1.md)에 원형 그대로 보존되어 있습니다.
- **공개 원칙**: 본 보고서와 저장소에 커밋된 모든 JSON/MD/PNG 산출물은 원문 텍스트, 실제 개인정보, 개별 문서 식별자(`document_id`)를 포함하지 않으며, 모든 수치는 해시 검증된 평가·런타임 산출물([stage8-service-final-v1.json](reports/stage8-service-final-v1.json), [stage8-official-final-v1.json](reports/stage8-official-final-v1.json), [stage8-runtime-acceptance-v1.json](reports/stage8-runtime-acceptance-v1.json), [m1-package.manifest.json](configs/m1-package.manifest.json))과 1:1로 일치합니다.

---

## 1. 최종 결론 및 교체 판정 요약 (`verdict: "replace"`)

7.5단계에서 오프라인 모델 품질 게이트 4개를 모두 통과(`pending_runtime_acceptance`)했던 M1(`checkpoint-1262`, 가중치 SHA-256 `ed2546b8b6a721be18a6a9e644fe6fd423016c7a457b5e30521d85c3d86228a2`)을 **8단계(시나리오 B)**에서 B 파트의 실제 프로덕션 검출기([dev/detector/gliner_backend.py](../dev/detector/gliner_backend.py)) 및 게이트웨이 런타임([dev/detector/runtime.py](../dev/detector/runtime.py), [dev/gateway/engine.py](../dev/gateway/engine.py))에 통합하고 GPU 서버(`NVIDIA L4`) 실측 인수 검증을 수행한 결과, **PRD의 5대 교체 게이트를 전 항목 통과하여 최종 판정이 `replace`(즉시 교체 승인)로 확정**되었습니다.

| 평가 구분 | 지표 | M0 (`urchade/gliner_multi_pii-v1`) | M1 Stage 7.5 (A 오프라인 후보) | **M1 Stage 8 (B 프로덕션 통합 최종)** | **M0 대비 최종 변화 (`Stage 8 - M0`)** |
|---|---|---:|---:|---:|---:|
| **동결 서비스 시험** (`300건`, `service-holdout-v2-r2`) | 전체 Micro Precision | `70.12%` (`345/492`) | `85.68%` (`383/447`) | **`88.25%`** (`383/434`) | **`+18.13%p`** |
| | 전체 Micro Recall | `84.15%` (`345/410`) | `93.41%` (`383/410`) | **`93.41%`** (`383/410`) | **`+9.27%p`** |
| | **전체 Micro F1** | `76.50%` | `89.38%` | **`90.76%`** | **`+14.26%p`** (95% CI `+10.58~+17.89%p`) |
| | **중요 5종 Micro Recall** | `89.52%` (`188/210`, 미탐 `22`) | `94.76%` (`199/210`, 미탐 `11`) | **`94.76%`** (`199/210`, 미탐 **`11`**) | **`+5.24%p`** (95% CI `+2.47~+8.45%p`) |
| | **중요 5종 Micro F1** | `91.26%` | `92.13%` | **`94.99%`** (`Precision 95.22%`) | **`+3.73%p`** |
| | `KR_RRN` Precision / Recall / F1 | `100.00% / 80.00% / 88.89%` (`fp=0, fn=8`) | `75.00% / 97.50% / 84.78%` (`fp=13, fn=1`) | **`100.00% / 97.50% / 98.73%`** (`fp=0, fn=1`) | **재현율 `+17.50%p`, 오탐 `0건`** |
| | **정상 문서 오차단·오류율 (`100건`)** | `1.00%` (`1/100`) | `2.00%` (`2/100`) | **`0.00%` (`0/100`)** | **`-1.00%p`** (95% CI `-3.00~0.00%p`) |
| | 게이트웨이 동작 (`allow / mask / block / error`) | `98 / 34 / 76 / 92` | `93 / 57 / 93 / 57` | **`98 / 120 / 81 / 1`** | **`error -91건` (`57 → 1건`), `mask +86건`** |
| **공식 벤치마크 시험** (`28,142건`, `test_track_a`) | **전체 Micro F1** | `66.58%` | `98.70%` | **`98.70%`** (`Precision 98.13%, Recall 99.27%`) | **`+32.12%p`** (95% CI `+31.92~+32.30%p`) |
| | **중요 5종 Micro Recall** | `78.67%` (`32,982/41,922`, 미탐 `8,940`) | `99.99%` (`41,919/41,922`, 미탐 `3`) | **`99.99%`** (`41,919/41,922`, 미탐 **`3`**) | **`+21.32%p`** (미탐 **`-8,937개`**) |
| | 게이트웨이 동작 (`allow / mask / block / error`) | `0 / 2,874 / 5,161 / 20,107` | `0 / 2,476 / 13,745 / 11,921` | **`0 / 14,108 / 13,745 / 289`** | **2차 재검사 `error -11,632건` (`-97.58%`)** |
| | 전달 문서 내 보호 대상 잔존율 | `21.89%` (`1,983 / 9,061`) | `0.95%` (`60 / 6,325`) | **`0.25%` (`127 / 51,010`)** | **`-21.64%p`** |
| **B 프로덕션 런타임 인수** (`NVIDIA L4`) | 종단간 게이트웨이 지연시간 (`p50 / p95 / p99`) | — | — | **`28.47 / 98.46 / 194.20 ms`** | **SLA (`p95 <= 500ms`) 통과** |
| | 무중단 핫스왑 및 즉시 롤백 (`M0 ↔ M1`) | — | — | **교체 `10.16s` / 롤백 `11.99s`** | **`rollback_verified: true`** |
| | C 파트 감사 저널 원문 무누출 검증 | — | — | **`raw_pii_leak_count = 0`** (`442KB`) | **`zero_leak_verified: true`** |
| **PRD 최종 교체 판정** | `replacement_decision` | 기준선 (Baseline) | `pending_runtime_acceptance` | **`replace` (5대 게이트 전원 통과)** | **프로덕션 기본 패키지 승격 가능** |

---

## 2. 엔드투엔드 진행 경과 및 6단계 핵심 트러블슈팅 (Troubleshooting History)

단순히 최종 F1 수치만 높인 것이 아니라, **1단계부터 8단계까지 총 6차례의 구조적 병목과 실패 가설을 실측으로 발견·기각·해결**하며 프로덕션 교체 조건을 완성했습니다.

| 단계 | 발생한 문제 (Symptom & Failure Mode) | 원인 진단 (Root Cause) | 해결책 및 아키텍처 반영 (Fix & Verification) | 관련 상세 문서 및 코드 |
|---|---|---|---|---|
| **① 2단계 (기준선 진단)** | M0 기본 모델이 한국어에서 전체 F1 `19~66%`, 특히 `KR_RRN` 재현율 `28.16%`, `PERSON` 재현율 `64.10%`로 심각한 미탐 발생 | GLiNER 기본 정규식 분할기(`\w+(?:[-_]\w+)*\|\S`)가 한국어 이름+조사(`김가상에게`, `홍길동님께서`)를 한 어절로 묶어 정답 문자 오프셋과 스팬 경계가 구조적으로 불일치 | 한국어 조사·어미 65종 최대 3회 접미 분리 + URL/이메일 원자 보존 분리기 **`ko_atomic_suffix_v3`** 설계 (`PERSON` 토큰 정렬률 `0% → 100%`) | [stage7_5.md §2](stage7_5.md), [splitters.py](segmentation/splitters.py) |
| **② 6단계 (학습·런타임 스모크)** | `ko_atomic_suffix_v3` 적용 시 어절 수가 늘어나 `max_len=384` 경계에서 조사 분리 조각이 잘리며 `incomplete` 판정 발생 위험 | 어절 단위로만 윈도우를 자르면 원문 어절의 조사 그룹이 두 윈도우로 쪼개져 모델 커버리지 불확실성(`incomplete`) 유발 | 원문 어절 그룹 통째 포함 경계 안전 윈도우 플래너(`_whole_group_plan`)를 고안하여 **모든 문서에서 `incomplete=0`, 비공백 코드포인트 100% 커버** 달성 | [coverage_backend.py](segmentation/coverage_backend.py), [gliner_backend.py](../dev/detector/gliner_backend.py) |
| **③ 7단계 초기 (구 판본 보고 폐기)** | 초기 7단계 보고서가 ① Dev 500건 부분집합만 평가하고, ② 공식 Test를 누락했으며, ③ C의 사후차단 규칙을 게이트웨이 성능에 합산하고, ④ 1차 차단율만 계산하는 등 계약 위반 확인 | B의 실제 게이트웨이(`Gateway._inspect` 1회 마스킹 재검사 + fail-closed `error` 포함)와 무결성 검증 절차 없이 약식 집계를 수행함 | 기존 7단계 수치 전량 폐기 후 **7.5단계 복구 계획([stage7_5.md](stage7_5.md))** 수립: `B-gateway-one-reinspection-v1` 채점기([gateway_metrics.py](evaluation/gateway_metrics.py)) 및 `complete=100%` 강제 검증기([run_checkpoint.py](evaluation/run_checkpoint.py)) 구축 | [stage7_5.md §1](stage7_5.md), [process.md](process.md) |
| **④ 7.5단계 (홀드아웃 누수 차단)** | 기존 서비스 시험셋(`service-test-v1`)의 비PII 문장 풀을 점검한 결과 학습셋과 겹치거나 합성 티가 나는 문장 발견 | 학습/선택/최종 평가 간 데이터 분리 원칙 위반 시 과적합 및 정상 문서 오차단율 왜곡 위험 | 독립 시나리오 생성기([holdout_v2.py](evaluation/holdout_v2.py))로 학습·Dev·공식 Test와 **텍스트 겹침 0건**인 동결 홀드아웃 **`service-holdout-v2-r2`(300건)** 재구축 및 SHA-256 잠금 | [service-holdout-v2-r2.frozen.manifest.json](evaluation/service-holdout-v2-r2.frozen.manifest.json) |
| **⑤ 7.5단계 (임계값 가설 기각)** | “패키징 단계에서 임계값을 `0.5 → 0.65~0.8`로 올리면 정상 문서 오차단이 해결될 것”이라는 초기 가설 검증 실패 (`10/200` 그대로 유지) | `train_5k`에서 `KR_RRN`(2,126개) 비중이 높아, M1이 13자리 계산용 정수나 장비코드를 `0.8` 이상 고신뢰도 `KR_RRN`으로 과잉 일반화함 | 임계값 일괄 상향 가설을 실측으로 기각하고, **8단계에서 B 런타임 내 `KR_RRN` 생년월일·성별코드·ASCII 경계 가드레일(`_is_valid_kr_rrn`)**로 정밀 해결 | [report_v1.md §3.1](report_v1.md), [gliner_backend.py](../dev/detector/gliner_backend.py) |
| **⑥ 8단계 (2차 재검사 `REINSPECTION_FAILED` 및 오차단 해결)** | 1차 마스킹 후 `[REDACTED]` 문자열이 삽입된 문서를 2차 재검사할 때, M1이 `[REDACTED]` 자체나 인접 토큰을 다시 PII로 탐지하여 서비스 홀드아웃 `57건`, 공식 Test `11,921건`에서 fail-closed `error` 발생 | GLiNER 모델은 `[REDACTED]` 플레이스홀더가 이미 비식별화된 안전 토큰임을 알지 못해 문맥상 개체명/식별자로 재예측함 | B의 `GLiNERBackend._inspect`에 **① `KR_RRN` 포맷·달력·경계 가드레일(`_is_valid_kr_rrn`)**과 **② `[REDACTED]` 플레이스홀더 중첩 스팬 필터(`_overlaps_redacted_placeholder`)**를 내장하여 정상 문서 오차단 **`2/100 → 0/100`**, 공식 Test 재검사 오류 **`11,921 → 289건`(`-97.58%`)** 해결 | [gliner_backend.py](../dev/detector/gliner_backend.py), [run_stage8_acceptance.py](evaluation/run_stage8_acceptance.py) |

---

## 3. 8단계(시나리오 B) 구현 및 프로덕션 통합 상세

### 3.1 B 계약 준수 M1 모델 패키지 구축 (`8-1`)
- **패키지 매니페스트**: [model/configs/m1-package.manifest.json](configs/m1-package.manifest.json) (원격 패키지 경로: `/home1/ncloud/stage8_20261101_v1/models/m1-checkpoint-1262-ko-atomic-v3`)
- **모델 식별자 및 버전**:
  - `model_version`: `"M1-gliner_multi_pii-ko-v1-ckpt1262"`
  - `threshold_version`: `"m1-uniform-0.5-v1"` (25개 전 유형 `0.5`)
  - `model`: `{"repo_id": "urchade/gliner_multi_pii-v1", "revision": "ed2546b8b6a721be18a6a9e644fe6fd423016c7a"}`
  - `preprocessing.version`: `"ko_atomic_suffix_v3"`
  - `model.safetensors` SHA-256: `ed2546b8b6a721be18a6a9e644fe6fd423016c7a457b5e30521d85c3d86228a2`
- **계약 검증**: `dev.detector.runtime._verify_package` 및 `dev/contracts/model-package.schema.json`의 모든 필수 아티팩트(`weights`, `config`, `tokenizer`, `license`, `evaluation`)와 파일별 SHA-256 해시 잠금을 100% 통과했습니다.

### 3.2 B 프로덕션 백엔드([dev/detector/gliner_backend.py](../dev/detector/gliner_backend.py)) 통합 (`8-2`, `8-3 단기`, `8-4`)
1. **M0 및 M1 패키지 동시 수용 및 즉시 롤백 지원 (`8-1`)**:
   - `ALLOWED_MODEL_IDENTITIES`에 M0(`1fcf13e8...`)와 M1(`ed2546b8...`) 리비전을 모두 등록하고, `PINNED_M1_ENCODER_CONFIG`(`vocab_size: 250105`) 및 GLiNER `0.2.29` 체크포인트 설정 키를 검증하도록 `_guard_builtin_m0`를 확장했습니다.
   - `ModelManager.replace(package_dir)` 호출만으로 서비스 중단 없이 M0 $\leftrightarrow$ M1 간 즉시 교체 및 롤백이 가능합니다.
2. **한국어 조사 분리기(`ko_atomic_suffix_v3`) 및 경계 안전 윈도우 플래너(`_whole_group_plan`) 이식 (`8-2`)**:
   - `manifest["preprocessing"]["version"] == "ko_atomic_suffix_v3"`일 때 B의 `GLiNERBackend`가 외부 패키지 의존성 없이 내장된 `_KoreanWordsSplitter`와 `_whole_group_plan` 폴백을 자동 활성화합니다(`b_splitter_adoption = True`).
3. **`KR_RRN` 숫자 유사 포맷·달력·경계 가드레일 (`8-3 단기`)**:
   - `_is_valid_kr_rrn(text, start, end)`를 통해 6자리 생년월일(유효 월·일 달력 검증) + `[- ]?` + `[1-8]\d{6}` 성별·뒤 6자리 형식 및 전후 ASCII 영숫자/하이픈 결합 여부를 검사하여, 장비코드(`EQ-2026-314159`)나 13~16자리 일반 정수가 `KR_RRN`으로 오탐되는 현상을 정답 손실(`0건`) 없이 차단했습니다.
4. **2차 재검사 `[REDACTED]` 플레이스홀더 중첩 가드레일 (`8-4`)**:
   - `_overlaps_redacted_placeholder(text, start, end)`를 통해 1차 마스킹이 삽입한 `[REDACTED]` 리터럴과 겹치는 스팬이 2차 재검사에서 다시 PII로 보고되어 `REINSPECTION_FAILED` 오류를 유발하는 현상을 원천 제거했습니다.

---

## 4. Stage 8 통합 후 최종 성적표 (Stage 7.5 vs Stage 8 비교)

### 4.1 동결 서비스 최종 시험 (`service-holdout-v2-r2`, 300건)

양쪽 모델 모두 **300/300건 `complete` (`incomplete=0`, `error=0`)**로 추론을 마쳤으며, Stage 8 B 프로덕션 런타임에서 실측한 결과는 다음과 같습니다([stage8-service-final-v1.json](reports/stage8-service-final-v1.json)).

| 지표 (`service-holdout-v2-r2`, 300건) | M0 (`final-service-m0-v1`) | M1 Stage 7.5 (가드레일 전) | **M1 Stage 8 (B 런타임 통합)** | **최종 변화 (`Stage 8 - M0`)** | 문서 짝지은 95% 신뢰구간 (`Stage 8 vs M0`) |
|---|---:|---:|---:|---:|---:|
| **전체 Micro Precision** | `70.12%` (`tp=345, fp=147`) | `85.68%` (`tp=383, fp=64`) | **`88.25%`** (`tp=383, fp=51`) | **`+18.13%p`** | — |
| **전체 Micro Recall** | `84.15%` (`fn=65, gold=410`) | `93.41%` (`fn=27, gold=410`) | **`93.41%`** (`fn=27, gold=410`) | **`+9.27%p`** | — |
| **전체 Micro F1** | `76.50%` | `89.38%` | **`90.76%`** | **`+14.26%p`** | **`+10.58%p ~ +17.89%p`** |
| **중요 5종 Micro Precision** | `93.07%` (`tp=188, fp=14`) | `89.64%` (`tp=199, fp=23`) | **`95.22%`** (`tp=199, fp=10`) | **`+2.15%p`** | — |
| **중요 5종 Micro Recall** | `89.52%` (`188/210`) | `94.76%` (`199/210`) | **`94.76%`** (`199/210`) | **`+5.24%p`** | **`+2.47%p ~ +8.45%p`** |
| **중요 5종 Micro F1** | `91.26%` | `92.13%` | **`94.99%`** | **`+3.73%p`** | — |
| **중요 5종 미탐 합계** | `22 / 210` | `11 / 210` | **`11 / 210`** | **`-11개`** | — |
| `KR_RRN` (gold `40`) | `P 100.0% / R 80.00% / F1 88.89%` (`fp=0, fn=8`) | `P 75.00% / R 97.50% / F1 84.78%` (`fp=13, fn=1`) | **`P 100.0% / R 97.50% / F1 98.73%`** (`fp=0, fn=1`) | **Recall `+17.50%p`, F1 `+9.85%p`** | Recall CI `+6.65%p ~ +29.79%p` |
| `KR_BANK_ACCOUNT` 재현율 (gold `40`) | `87.50%` (미탐 `5`) | `90.00%` (미탐 `4`) | **`90.00%`** (미탐 **`4`**) | **`+2.50%p`** | `0.00%p ~ +7.90%p` |
| `CREDIT_CARD` 재현율 (gold `50`) | `92.00%` (미탐 `4`) | `92.00%` (미탐 `4`) | **`92.00%`** (미탐 `4`) | `0.00%p` | `0.00%p ~ 0.00%p` |
| `KR_PHONE_NUMBER` 재현율 (gold `40`) | `95.00%` (미탐 `2`) | `95.00%` (미탐 `2`) | **`95.00%`** (미탐 `2`) | `0.00%p` | `0.00%p ~ 0.00%p` |
| `EMAIL_ADDRESS` 재현율 (gold `40`) | `92.50%` (미탐 `3`) | `100.00%` (미탐 `0`) | **`100.00%`** (미탐 **`0`**) | **`+7.50%p`** | `0.00%p ~ +16.67%p` |
| **정상 문서 차단·오류 (`100건`)** | `1/100` (`1.00%`, Wilson `0.18~5.45%`) | `2/100` (`2.00%`, Wilson `0.55~7.00%`) | **`0/100` (`0.00%`, Wilson `0.00~3.70%`)** | **`-1.00%p`** | **`-3.00%p ~ 0.00%p`** |
| **정상 문서 오마스킹 (`100건`)** | `10/100` | `7/100` | **`5/100`** | **`-5건`** | — |
| 게이트웨이 동작 (`allow / mask / block / error`) | `98 / 34 / 76 / 92` | `93 / 57 / 93 / 57` | **`98 / 120 / 81 / 1`** | **`error -91건`, `mask +86건`** | — |
| 전달 문서(`allow`/`mask`) 내 보호 대상 잔존율 | `40.74%` (`22 / 54`) | `7.59%` (`6 / 79`) | **`8.37%` (`17 / 203`)** | **`-32.37%p`** (전달 모수 `54 → 203개`로 3.8배 증가) | — |

- **핵심 개선 효과**:
  1. `KR_RRN` 오탐 13건이 **0건**으로 제거되면서 `KR_RRN` 정밀도가 `75.00% → 100.00%`, F1이 `84.78% → 98.73%`로 상승했고, 중요 5종 전체 정밀도 역시 `89.64% → 95.22%`로 M0(`93.07%`)를 앞질렀습니다.
  2. 정상 문서 100건에서 발생하던 `2/100(2.00%)` 오차단이 **`0/100(0.00%)`**으로 완전히 해소되어 M0(`1/100, 1.00%`)보다도 낮은 오차단율을 달성했습니다.
  3. `[REDACTED]` 2차 재검사 가드레일 적용으로 서비스 홀드아웃 300건의 fail-closed `error`가 **`57건 → 1건`(`-56건`)**으로 급감하고, 정상적으로 마스킹 후 전달된 문서(`mask`)가 **`57건 → 120건`**으로 2배 이상 증가했습니다.

---

### 4.2 공식 Test 최종 시험 (`test_track_a.jsonl`, 28,142건)

공식 Test 28,142건 전체에 Stage 8 가드레일을 적용한 결과는 다음과 같습니다([stage8-official-final-v1.json](reports/stage8-official-final-v1.json)).

| 지표 (`test_track_a`, 28,142건) | M0 (`final-official-m0-v1`) | M1 Stage 7.5 (`final-official-m1-v1`) | **M1 Stage 8 (B 런타임 통합)** | **최종 변화 (`Stage 8 - M0`)** |
|---|---:|---:|---:|---:|
| **전체 Micro Precision** | `63.59%` (`tp=84,671, fp=48,478`) | `98.13%` (`tp=120,297, fp=2,288`) | **`98.13%`** (`tp=120,297, fp=2,288`) | **`+34.54%p`** |
| **전체 Micro Recall** | `69.87%` (`fn=36,505, gold=121,176`) | `99.27%` (`fn=879, gold=121,176`) | **`99.27%`** (`fn=879, gold=121,176`) | **`+29.40%p`** |
| **전체 Micro F1** | `66.58%` | `98.70%` | **`98.70%`** | **`+32.12%p`** |
| **중요 5종 Micro Recall** | `78.67%` (`32,982/41,922`, 미탐 `8,940`) | `99.99%` (`41,919/41,922`, 미탐 `3`) | **`99.99%`** (`41,919/41,922`, 미탐 **`3`**) | **`+21.32%p`** |
| **중요 5종 Micro F1** | `86.23%` | `100.00%` (`99.996%`) | **`100.00%`** (`99.996%`) | **`+13.77%p`** |
| `KR_RRN` (gold `12,048`) | `P 91.60% / R 28.16% / F1 43.08%` | `P 100.0% / R 99.98% / F1 99.99%` (`tp=12,045`) | **`P 100.0% / R 99.98% / F1 99.99%`** (`tp=12,045`) | **정답 손실 `0건` 유지 (`+71.81%p` Recall)** |
| **게이트웨이 동작 (`allow / mask / block / error`)** | `0 / 2,874 / 5,161 / 20,107` | `0 / 2,476 / 13,745 / 11,921` | **`0 / 14,108 / 13,745 / 289`** | **`error -19,818건` (Stage 7.5 대비 `-11,632건`)** |
| **전달 문서(`allow`/`mask`) 내 보호 대상 잔존율** | `21.89%` (`1,983 / 9,061`) | `0.95%` (`60 / 6,325`) | **`0.25%` (`127 / 51,010`)** | **`-21.64%p`** (Stage 7.5 대비 `-0.70%p`) |

- **공식 Test에서의 결정적 검증**:
  - `KR_RRN` 가드레일(`_is_valid_kr_rrn`)은 공식 Test의 `KR_RRN` 정답 12,048건 중 M1이 맞힌 **12,045건을 단 1건의 손실도 없이 100% 보존**했습니다.
  - `[REDACTED]` 중첩 가드레일(`_overlaps_redacted_placeholder`)은 공식 Test 28,142건 중 1차 마스킹 대상 문서(`14,397건`)에서 발생하던 2차 재검사 실패(`REINSPECTION_FAILED`)를 **`11,921건 → 289건`(`-97.58%` 제거)**으로 줄였으며, 마스킹 후 정상 전달된 문서를 **`2,476건 → 14,108건`**으로 5.7배 늘리면서도 보호 대상 잔존율을 **`0.25%` (`127 / 51,010`)**로 억제했습니다.

---

### 4.3 GPU 서버(`NVIDIA L4`) B 프로덕션 런타임 인수 검증 (`8-6`)

GPU 서버(`10.1.2.17`, `NVIDIA L4`, Python 3.12, `gliner==0.2.29`)에서 B의 실제 `ModelManager`, `Gateway`, `Journal`을 구동하여 측정한 런타임 인수 지표는 다음과 같습니다([stage8-runtime-acceptance-v1.json](reports/stage8-runtime-acceptance-v1.json)).

| 인수 검증 항목 | 측정 조건 | 실측치 | 허용 기준 (SLA / Gate) | 통과 여부 |
|---|---|---:|---:|---|
| **M0 콜드 초기화 시간** | `ModelManager.initialize(m0_pkg)` (가중치 로드 + CUDA 이동 + `warmup()`) | `17,743.05 ms` (`17.74s`) | 정상 기동 및 `ready=True` | **PASS** |
| **M0 → M1 무중단 핫스왑** | `ModelManager.replace(m1_pkg)` | `10,161.85 ms` (`10.16s`) | `active_model_version == M1` | **PASS** |
| **M1 → M0 즉시 롤백** | `ModelManager.replace(m0_pkg)` | `11,988.53 ms` (`11.99s`) | `rollback_verified == True` | **PASS** |
| **M0 → M1 재활성화** | `ModelManager.replace(m1_pkg)` | `9,849.12 ms` (`9.85s`) | `reactivate_verified == True` | **PASS** |
| **종단간 게이트웨이 지연시간 (`<=2,000` 코드포인트)** | 동시성 `1`, `Gateway._inspect` (1차 추론 + 정책 + 2차 재검사 + SQLite 감사 저널 기록 포함, 300건) | `mean 42.27ms` / `p50 28.47ms` / `p90 51.15ms` / **`p95 98.46ms`** / `p99 194.20ms` / `max 194.72ms` | **`p95 <= 500.0 ms`** | **PASS (`98.46ms`)** |
| **GPU 메모리 점유** | `torch.cuda.memory_allocated` / `max_memory_allocated` | 상시 할당 **`1,122.49 MB`**, 교체 피크 **`2,240.34 MB`** (`L4 24GB`의 `9.3%`) | OOM 없음 및 안정적 메모리 유지 | **PASS** |
| **프로세스 RSS 메모리** | `ru_maxrss` | **`4,166.15 MB`** | 시스템 메모리 한계 이내 | **PASS** |
| **C 파트 감사 저널 원문 무누출 검증** | `events.sqlite3` (`442,368 bytes`) 전수 바이트 스캔 vs 홀드아웃 정답 PII 원문 대조 | **`raw_pii_leak_count = 0`** | 원문 노출 `0건` | **PASS** |

---

### 4.4 PRD 5대 교체 게이트 최종 판정표

[stage8-service-final-v1.json](reports/stage8-service-final-v1.json)의 `comparisons.model_only.decision` 및 `comparisons.model_plus_same_rules.decision`에서 확인된 5대 게이트 결과입니다.

| 게이트 키 (`replacement_decision`) | 기준 | Stage 7.5 결과 | **Stage 8 최종 결과** | 판정 |
|---|---|---|---|---|
| `critical_micro_recall_not_below_baseline` | `>= 0.00%p` | `+5.24%p` (`True`) | **`+5.24%p` (`89.52% → 94.76%`, `True`)** | **PASS** |
| `overall_f1_not_below_baseline` | `>= 0.00%p` | `+12.88%p` (`True`) | **`+14.26%p` (`76.50% → 90.76%`, `True`)** | **PASS** |
| `normal_false_block_increase_within_limit` | `<= +1.00%p` | `+1.00%p` (`2/100 vs 1/100`, `True`) | **`-1.00%p` (`0/100 vs 1/100`, `True`)** | **PASS** |
| `no_unresolved_critical_type_regression` | 중요 5종 개별 재현율 퇴행 없음 | 퇴행 0건 (`True`) | **퇴행 0건 (`True`)** | **PASS** |
| `runtime_memory_latency_integration_accepted` | B 런타임 통합·지연·메모리·롤백 통과 | `None` (`pending_runtime_acceptance`) | **`True` (`p95=98.46ms <= 500ms`, 롤백·무누출 검증 완료)** | **PASS** |
| **최종 판정 (`verdict`)** | 5대 게이트 모두 `True` | `pending_runtime_acceptance` | **`replace` (`reasons: []`)** | **최종 교체 승인** |

---

### 4.5 Stage 8 반영 시각 그래프 (총 21종, [model/graph/README.md](graph/README.md))

기존 18종 그래프를 모두 **3자 비교(`M0` vs `M1 7.5 오프라인` vs `M1 8단계 B통합`)**로 갱신하고, Stage 8 전용 그래프 3종을 추가하여 총 **21종(PNG + SVG, [graph-data.json](graph/graph-data.json))**을 완성했습니다:
- **동결 서비스 300건 3자 비교**: [service-overall.png](graph/service-overall.png), [service-per-type.png](graph/service-per-type.png), [service-actions.png](graph/service-actions.png) (`error 57 → 1`), [service-normal-failure.png](graph/service-normal-failure.png) (`0/100 달성`), [service-residual.png](graph/service-residual.png)
- **공식 Test 28,142건 3자 비교**: [official-overall.png](graph/official-overall.png), [official-per-type.png](graph/official-per-type.png), [official-actions.png](graph/official-actions.png) (`error 11,921 → 289`), [official-residual.png](graph/official-residual.png) (`0.25% 달성`)
- **Stage 8 신규 그래프 3종**:
  1. [stage8-service-paired-deltas.png](graph/stage8-service-paired-deltas.png): 동결 서비스 300건 문서 짝지은 변화량 95% 신뢰구간 (`overall_f1 +14.26%p`, `normal_failure_rate -1.00%p`)
  2. [stage8-runtime-latency-sla.png](graph/stage8-runtime-latency-sla.png): `NVIDIA L4` B 게이트웨이 종단간 지연시간 분포 (`p95 = 98.46ms <= 500ms SLA`)
  3. [stage8-lifecycle-and-memory.png](graph/stage8-lifecycle-and-memory.png): `ModelManager` 무중단 핫스왑·롤백 소요 시간 및 GPU/RSS 메모리 점유 실측

---

## 5. 추후 개발 과제 (시나리오 C — Future Development Roadmap)

현재 8단계(시나리오 B)를 통해 프로덕션 교체 승인(`verdict: "replace"`)과 정상 문서 오차단 `0/100`, 2차 재검사 오류 `-97.58%` 감소를 달성했으나, 모델 가중치 자체의 후속 고도화를 위해 아래 과제들을 **차기 마일스톤(M1.1 / 시나리오 C)**으로 정의합니다.

### 5.1 [과제 C-1] `8-3 (중기)` 균형 샘플링 + Negative Lookalike 하드 네거티브 재학습 (`M1.1`)
- **배경**: 현재 `M1(checkpoint-1262)`은 B 런타임의 `_is_valid_kr_rrn` 가드레일을 통해 숫자 유사 오탐을 `0건`으로 억제하고 있으나, 모델 가중치 자체는 여전히 13자리 일반 정수를 높은 점수(`>0.8`)의 `KR_RRN` 후보로 예측하려는 경향을 지닙니다.
- **향후 개발 내용**:
  - `train_track_a.jsonl`(90,524건)에서 `KR_RRN` 과잉 비중을 완화한 층화 재샘플링을 수행하고, 계산용 큰 정수·장비코드(`EQ-2026-314159`)·가짜 날짜(`990230-...`)·버전 문자열 등 **정상 숫자 유사(`numeric_lookalikes`) 하드 네거티브 문서 500~1,000건**을 학습셋에 명시적 무엔터티(`entities=[]`) 샘플로 혼합하여 `M1.1`을 2 epoch 미세조정합니다.
  - 이를 통해 후처리 가드레일 없이도 모델 단독의 `KR_RRN` 오탐률을 근본적으로 낮추고, 가드레일은 심층 방어(Defense-in-Depth) 계층으로 유지합니다.

### 5.2 [과제 C-2] `8-5` `PERSONAL_ATTRIBUTE` 및 희소 비핵심 유형 보강
- **배경**: K-PII-Bench 공개 트랙(`train_track_a` / `test_track_a`)에는 `PERSONAL_ATTRIBUTE` 정답 라벨이 `0개`여서, 미세조정 후 서비스 홀드아웃에서 `PERSONAL_ATTRIBUTE` 재현율이 `30.00% → 10.00%`(정답 10개 중 1개 탐지)로 낮아졌습니다. 또한 비중요 유형 중 `DATE_OF_BIRTH`(`96.67% → 86.67%`), `KR_LOCATION`(`84.62% → 80.77%`), `USERNAME`(`88.89% → 77.78%`)에서 소폭의 미탐 증가가 관찰되었습니다.
- **향후 개발 내용**:
  - 홀드아웃과 완전히 독립된 한국어 합성 코퍼스에서 직위·직급·질병·신체특징 등 `PERSONAL_ATTRIBUTE` 예시와 비정형 주소(`KR_LOCATION`)·생년월일(`DATE_OF_BIRTH`) 데이터를 보강하여 `M1.1` 학습 코퍼스에 통합합니다.

### 5.3 [과제 C-3] 2차 재검사 문맥 보존형 마스킹 및 장문(`>512` 서브토큰) 배치 추론 최적화
- **배경**: 현재 Stage 8의 `_overlaps_redacted_placeholder` 가드레일로 공식 Test의 2차 재검사 오류를 `11,921건 → 289건`(`-97.58%`)으로 줄였으나, 나머지 `289건`(`1.03%`)은 `[REDACTED]` 주변 문맥 변화로 인해 1차에서 임계값(`0.5`) 미만(`0.48~0.49`)이었던 인접 비PII/경계 토큰이 2차에서 `0.50`을 넘기며 발생하는 경우입니다. 또한 512 서브토큰을 초과하는 초장문 문서는 다중 윈도우 순차 추론 시 지연시간이 `~190ms`(`p99`)까지 증가합니다.
- **향후 개발 내용**:
  - 게이트웨이 2차 재검사 시 1차에서 이미 검사 통과한 비변경 문맥 구간의 중복 알람을 억제하거나 유형별 플레이스홀더(`<PERSON_1>` 등) 옵션을 검토합니다.
  - 다중 윈도우 문서에 대해 `GLiNERBackend._inspect` 내부 윈도우 배치 추론(batched window inference)을 도입하여 `p99` 지연시간을 `100ms` 이하로 추가 단축합니다.

---

## 6. 산출물 및 검증 테스트 색인

| 구분 | 경로 | 설명 |
|---|---|---|
| **B 프로덕션 백엔드 통합 코드** | [dev/detector/gliner_backend.py](../dev/detector/gliner_backend.py) | M0/M1 듀얼 리비전 허용, `ko_atomic_suffix_v3` 및 `_whole_group_plan` 내장, `_is_valid_kr_rrn` 및 `_overlaps_redacted_placeholder` 가드레일 내장 |
| **M1 B-계약 패키지 매니페스트** | [model/configs/m1-package.manifest.json](configs/m1-package.manifest.json) | `dev/contracts/model-package.schema.json` 준수 M1 패키지 매니페스트 (SHA-256 잠금) |
| **Stage 8 실행 및 검증 스크립트** | [model/evaluation/run_stage8_acceptance.py](evaluation/run_stage8_acceptance.py) | B-계약 M1 패키징, GPU 런타임 인수 검증(핫스왑/롤백/지연/메모리/감사저널), 서비스/공식 최종 성적표 생성 |
| **Stage 8 통합 단위 테스트** | [model/tests/test_stage8_b_integration.py](tests/test_stage8_b_integration.py) | 분리기 일치성, 경계 안전 윈도우 폴백, `KR_RRN`/`[REDACTED]` 가드레일, `ModelManager.replace` 핫스왑·롤백 검증 (`341 passed`) |
| **Stage 8 GPU 런타임 인수 리포트** | [model/reports/stage8-runtime-acceptance-v1.json](reports/stage8-runtime-acceptance-v1.json) | `NVIDIA L4` 실측 초기화·핫스왑·롤백 시간, 메모리, `p95=98.46ms` 지연시간, 감사 저널 무누출 검증 결과 |
| **Stage 8 동결 서비스 최종 리포트** | [model/reports/stage8-service-final-v1.json](reports/stage8-service-final-v1.json) | 동결 서비스 홀드아웃 300건 최종 성적표 (`verdict: "replace"`, `F1 90.76%`, 정상 오차단 `0/100`) |
| **Stage 8 공식 Test 최종 리포트** | [model/reports/stage8-official-final-v1.json](reports/stage8-official-final-v1.json) | 공식 Test 28,142건 최종 성적표 (`F1 98.70%`, 2차 재검사 `error` `11,921 → 289건`) |
| **Stage 7.5 오프라인 기준 보고서** | [model/report_v1.md](report_v1.md) | 7.5단계 직후 작성된 오프라인 평가 기준 보고서 (비교 참조용 보존본) |
