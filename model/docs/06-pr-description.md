# [PR] 한국어 PII 탐지 모델 M1 개선, 7.5단계 엄밀 재검증 및 8단계 B 프로덕션 런타임 통합 (`verdict: "replace"`)

## 1. 개요 (Summary)

본 PR은 기존 다국어 PII 탐지 모델 **M0 (`urchade/gliner_multi_pii-v1`)**의 한국어 조사 결합 미탐 및 주요 식별자(`KR_RRN`, `KR_PASSPORT`, `KR_DRIVER_LICENSE`, `PERSON` 등) 성능 한계를 해결하기 위해 수행한 **1단계~8단계(시나리오 B) 전 과정의 코드, 패키지 매니페스트, 실측 평가 리포트, 시각 그래프(21종), 그리고 통합 문서실(`model/docs/`)**을 포함합니다.

- **핵심 결론**: B 파트의 프로덕션 검출기([`dev/detector/gliner_backend.py`](../dev/detector/gliner_backend.py))에 한국어 조사 분리기(`ko_atomic_suffix_v3`), 경계 안전 윈도우 플래너(`_whole_group_plan`), `KR_RRN` 숫자 유사 가드레일(`_is_valid_kr_rrn`), 2차 재검사 `[REDACTED]` 플레이스홀더 중첩 가드레일(`_overlaps_redacted_placeholder`)을 통합하고 GPU 서버(`NVIDIA L4`)에서 실제 `ModelManager` + `Gateway` + `Journal` 종단간 인수 검증을 완료하여 **PRD 5대 교체 게이트 전 항목 통과 및 최종 판정 `verdict: "replace"`**를 달성했습니다.

---

## 2. 주요 성과 지표 (Key Empirical Results)

| 평가 구분 | 지표 | M0 (`gliner_multi_pii-v1`) | M1 Stage 7.5 (오프라인) | **M1 Stage 8 (B 프로덕션 통합)** | **최종 변화 (`Stage 8 - M0`)** |
|---|---|---:|---:|---:|---:|
| **동결 서비스 홀드아웃** (`300건`, `service-holdout-v2-r2`) | 전체 Micro Precision | `70.12%` | `85.68%` | **`88.25%`** | **`+18.13%p`** |
| | 전체 Micro Recall | `84.15%` | `93.41%` | **`93.41%`** | **`+9.27%p`** |
| | **전체 Micro F1** | `76.50%` | `89.38%` | **`90.76%`** | **`+14.26%p`** (95% CI `+10.58~+17.89%p`) |
| | **중요 5종 Micro Recall** | `89.52%` (미탐 `22`) | `94.76%` (미탐 `11`) | **`94.76%`** (미탐 **`11`**) | **`+5.24%p`** (95% CI `+2.47~+8.45%p`) |
| | `KR_RRN` Precision / Recall / F1 | `100.0% / 80.0% / 88.9%` | `75.0% / 97.5% / 84.8%` (`fp=13`) | **`100.0% / 97.5% / 98.7%`** (`fp=0`) | **오탐 `0건`, Recall `+17.50%p`** |
| | **정상 문서 오차단·오류율 (`100건`)** | `1/100` (`1.00%`) | `2/100` (`2.00%`) | **`0/100` (`0.00%`)** | **`-1.00%p`** (95% CI `-3.00~0.00%p`) |
| | 게이트웨이 동작 (`allow / mask / block / error`) | `98 / 34 / 76 / 92` | `93 / 57 / 93 / 57` | **`98 / 120 / 81 / 1`** | **`error -91건` (`57 → 1건`), `mask +86건`** |
| **공식 Test 벤치마크** (`28,142건`, `test_track_a`) | **전체 Micro F1** | `66.58%` | `98.70%` | **`98.70%`** (`P 98.13%, R 99.27%`) | **`+32.12%p`** (95% CI `+31.92~+32.30%p`) |
| | **중요 5종 Micro Recall** | `78.67%` (미탐 `8,940`) | `99.99%` (미탐 `3`) | **`99.99%`** (미탐 **`3`**) | **`+21.32%p`** (미탐 **`-8,937개`**) |
| | 게이트웨이 동작 (`allow / mask / block / error`) | `0 / 2,874 / 5,161 / 20,107` | `0 / 2,476 / 13,745 / 11,921` | **`0 / 14,108 / 13,745 / 289`** | **2차 재검사 `error -11,632건` (`-97.58%`)** |
| | 전달 문서 내 보호 대상 잔존율 | `21.89%` (`1,983/9,061`) | `0.95%` (`60/6,325`) | **`0.25%` (`127/51,010`)** | **`-21.64%p`** |
| **B 프로덕션 런타임 인수** (`NVIDIA L4`) | 종단간 게이트웨이 지연 (`p50 / p95 / p99`) | — | — | **`28.47 / 98.46 / 194.20 ms`** | **SLA (`p95 <= 500ms`) 통과** |
| | 무중단 핫스왑 / 즉시 롤백 (`M0 ↔ M1`) | — | — | **교체 `10.16s` / 롤백 `11.99s`** | **`rollback_verified: true`** |
| | GPU / 프로세스 메모리 | — | — | **GPU 상시 `1,122MB` (피크 `2,240MB`) / RSS `4,166MB`** | `L4 24GB` 대비 안정적 점유 |
| | C 파트 감사 저널 원문 무누출 검증 | — | — | **`raw_pii_leak_count = 0`** (`442KB`) | **`zero_leak_verified: true`** |
| **PRD 최종 교체 판정** | `replacement_decision` | 기준선 (Baseline) | `pending_runtime_acceptance` | **`replace` (5대 게이트 전원 통과)** | **프로덕션 기본 패키지 승격 승인** |

---

## 3. 6단계 핵심 트러블슈팅 요약 (Troubleshooting Journey)

1. **[2~3단계] 한국어 조사 결합으로 인한 스팬 경계 불일치 (`PERSON` 정렬률 0%, train 98.9% 거절)**
   - **해결**: 외부 형태소 분석기 의존성 없이 65개 한국어 조사·어미 접미 분리 + URL/이메일 원자 보존 분리기 **`ko_atomic_suffix_v3`** 구현으로 train 문서 보존율 `1.08% → 97.21%` 달성.
2. **[6~7.5단계] 장문 어절 다중 분리 시 `max_len=384` 윈도우 경계 절단(`incomplete`) 발생**
   - **해결**: 원문 어절의 조사 분리 그룹 전체를 한 윈도우에 보존하는 경계 안전 폴백 플래너(`_whole_group_plan`)를 구현하여 모든 평가셋(`28,347` Dev + `300` Service + `28,142` Official Test)에서 **`incomplete = 0` (`100% complete`)** 달성.
3. **[7.5단계] 초기 7단계 약식 평가 폐기 및 B 실제 게이트웨이 1회 재검사 채점기 도입**
   - **해결**: 초기 7단계 보고를 폐기하고 B의 실제 `Gateway._inspect` 경로와 1:1로 대응하는 `B-gateway-one-reinspection-v1` 채점기([`model/evaluation/gateway_metrics.py`](../evaluation/gateway_metrics.py)) 및 해시 잠금 실행기([`model/evaluation/run_checkpoint.py`](../evaluation/run_checkpoint.py)) 구축.
4. **[7.5단계] 서비스 홀드아웃 데이터 누수 및 품질 피드백 전수 반영 (`service-holdout-v2-r2`)**
   - **해결**: 여권 번호 자릿수, 주민번호 세기/성별 코드, 한국어 받침 조사 정합성, 장문 창 경계 스팬 배치, 10개 분야 정상 문서 100건을 전면 재설계하여 학습/Dev/공식 Test와 중복 0건인 동결 홀드아웃(`service-holdout-v2-r2.frozen.manifest.json`) 확립.
5. **[7.5단계] “임계값 상향(`0.65~0.8`)으로 오차단 해결” 가설 실측 기각**
   - **해결**: Dev 임계값 그리드(`0.35, 0.5, 0.65, 0.8`) 탐색 결과, 계산용 13자리 정수 등 숫자 유사(`numeric_lookalikes`) 오탐은 점수가 `>0.8`이어서 임계값 상향으로 해결되지 않음을 실증적으로 기각.
6. **[8단계] B 프로덕션 런타임 내 `KR_RRN` 포맷·달력·경계 가드레일 및 `[REDACTED]` 2차 재검사 가드레일 내장**
   - **해결**: [`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py)에 `_is_valid_kr_rrn`과 `_overlaps_redacted_placeholder`를 내장하여 정답 손실 `0건`으로 서비스 홀드아웃 정상 문서 오차단을 **`2/100 → 0/100 (0.00%)`**으로 해소하고, 공식 Test 2차 재검사 `error`를 **`11,921건 → 289건`(`-97.58%`)**으로 줄임.

---

## 4. 주요 변경 파일 (Changed & Added Files)

### 4.1 B 프로덕션 런타임 및 패키지 계약 (`dev/` & `model/configs/`)
- [`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py):
  - `M1_REVISION` (`ed2546b8b6a721be18a6a9e644fe6fd423016c7a`), `ALLOWED_MODEL_IDENTITIES`, `PINNED_M1_ENCODER_CONFIG` (`vocab_size: 250105`) 추가 (M0/M1 듀얼 패키지 로드 및 즉시 롤백 지원).
  - `ko_atomic_suffix_v3` (`_KoreanWordsSplitter`) 및 경계 안전 윈도우 플래너(`_whole_group_plan`) 내장 (`b_splitter_adoption=True`).
  - `_is_valid_kr_rrn` (`KR_RRN` 숫자 유사 오탐 차단) 및 `_overlaps_redacted_placeholder` (`[REDACTED]` 2차 재검사 오탐 차단) 내장.
- [`model/configs/m1-package.manifest.json`](../configs/m1-package.manifest.json): `dev/contracts/model-package.schema.json` 규격을 준수하는 M1 프로덕션 패키지 매니페스트.

### 4.2 평가·시각화·검증 코드 및 테스트 (`model/evaluation/`, `model/tests/`, `model/graph/`)
- [`model/evaluation/run_stage8_acceptance.py`](../evaluation/run_stage8_acceptance.py): B-계약 M1 패키지 빌드, `NVIDIA L4` GPU 런타임 인수(핫스왑/롤백/지연/메모리/저널 무누출), Stage 8 최종 성적표 산출 스크립트.
- [`model/evaluation/build_graphs.py`](../evaluation/build_graphs.py) 및 [`model/graph/`](../graph/README.md): M0 vs Stage 7.5 vs Stage 8 3자 비교 및 GPU 런타임 SLA/메모리 그래프를 포함한 **총 21종 그래프(PNG + SVG)** 및 [`graph-data.json`](../graph/graph-data.json).
- [`model/tests/test_stage8_b_integration.py`](../tests/test_stage8_b_integration.py): Stage 8 분리기 일치성, 경계 안전 윈도우 폴백, `KR_RRN`/`[REDACTED]` 가드레일, `ModelManager.replace` 핫스왑·롤백 단위 테스트.

### 4.3 보고서 및 통합 문서실 (`model/report_v2.md`, `model/docs/`)
- [`model/report_v2.md`](../report_v2.md): Stage 8 완료 및 시나리오 C(추후 개발 과제) 로드맵을 담은 최종 개정판 보고서 ([`model/report_v1.md`](../report_v1.md) 보존).
- [`model/docs/`](README.md): 사람이 읽기 좋은 모든 문서를 번호순(`01`~`08`)으로 정리한 통합 문서실.
- [`model/reports/stage8-runtime-acceptance-v1.json`](../reports/stage8-runtime-acceptance-v1.json), [`model/reports/stage8-service-final-v1.json`](../reports/stage8-service-final-v1.json), [`model/reports/stage8-official-final-v1.json`](../reports/stage8-official-final-v1.json): Stage 8 실측 결과 JSON.

---

## 5. 검증 및 재현 방법 (Verification & Tests)

```bash
# 1. 전체 단위·통합 테스트 실행 (model/tests + dev/detector/tests)
PYTHONPATH=dev:. model/.cache/conversion-venv/bin/python -m pytest --noconftest \
  model/tests \
  dev/detector/tests/test_gliner_backend.py \
  dev/detector/tests/test_prepare_m0.py \
  dev/detector/tests/test_runtime.py -q
# 결과: 341 passed in 0.75s

# 2. 그래프 21종(PNG/SVG) 및 graph-data.json 재생성
PYTHONPATH=dev:. model/.cache/conversion-venv/bin/python -m model.evaluation.build_graphs \
  --dev-root model/.cache/stage7_5-20261031/dev-recovered-v1/runs \
  --final-root model/.cache/stage7_5-20261031/final-combined-v1 \
  --output model/graph

# 3. Git 공백/포맷 무결성 확인
git diff --check
```

---

## 6. 보안 및 데이터 무누출 체크리스트 (Security & Privacy Checklist)
- [x] 원본 데이터셋(`*.jsonl`), 원시 예측(`predictions.jsonl`), 모델 가중치(`*.safetensors`, `*.bin`), 서버 접속 정보(`model/.cache/`, `model/.secrets/`)가 Git 추적 대상에 1건도 포함되지 않음을 확인했습니다.
- [x] 커밋된 모든 JSON/MD/PNG/SVG 산출물에 실제 개인정보 원문이나 개별 문서 식별자(`document_id`)가 노출되지 않음을 검증했습니다.
- [x] C 파트 SQLite 감사 저널(`events.sqlite3`) 바이트 스캔 결과 원문 PII 누출 `0건`(`zero_leak_verified: true`)을 확인했습니다.
