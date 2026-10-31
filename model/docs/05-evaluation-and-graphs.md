# 05. 평가 프로토콜(`char_span_exact_v1` + `B-gateway-one-reinspection-v1`) 및 시각 그래프 21종 해설서

> 본 문서는 [`model/evaluation/README.md`](../evaluation/README.md)와 [`model/graph/README.md`](../graph/README.md)(총 21종 그래프)를 통합한 평가·시각화 해설서입니다.

---

# 그래프 재현 및 해석 (Stage 7.5 + Stage 8 통합판)

실측 JSON으로 작성합니다. 새 표본은 학습 전이고 dev는 설정 선택용입니다. 최종 시험 결과를 보고 설정을 바꾸지 않습니다. 8단계 B 프로덕션 런타임 통합(`ko_atomic_suffix_v3` + `KR_RRN` 및 `[REDACTED]` 가드레일)과 `NVIDIA L4` GPU 인수 검증(`verdict: "replace"`) 결과까지 모두 반영되어 있습니다. 원문·문서 ID·키는 그래프에 넣지 않습니다.

실행: `model/.cache/conversion-venv/bin/python -m model.evaluation.build_graphs --dev-root <dev-run-directory> [--final-root <final-run-directory>]`

Matplotlib 3.10.0, NumPy 2.5.4. PNG와 글꼴을 경로로 변환한 SVG를 함께 제공합니다. 입력 SHA-256과 수치: [graph-data.json](../graph/graph-data.json).

| 그림 | PNG | SVG |
|---|---|---|
| 원본 trainer_state 기록 — 성공한 optimizer update 수와 같다고 단정하지 않음 | [PNG](../graph/training-history.png) | [SVG](../graph/training-history.svg) |
| 학습에 사용한 기존 표본과 새로 준비한 표본: 분야 분포 | [PNG](../graph/sample-domains.png) | [SVG](../graph/sample-domains.svg) |
| 표본 유형별 gold 개수 — 새 표본은 아직 학습에 사용하지 않음 | [PNG](../graph/sample-labels.png) | [SVG](../graph/sample-labels.svg) |
| Dev 설정 선택 자료 — 최종 시험 결과가 아님 | [PNG](../graph/dev-thresholds.png) | [SVG](../graph/dev-thresholds.svg) |
| service 최종 시험: 전체 엔터티 성능 (M0 vs 7.5단계 vs 8단계) | [PNG](../graph/service-overall.png) | [SVG](../graph/service-overall.svg) |
| service 유형별 성능 — gold 0인 유형은 N/A 표시 (M0 vs 7.5단계 vs 8단계) | [PNG](../graph/service-per-type.png) | [SVG](../graph/service-per-type.svg) |
| service B 게이트웨이 정책 경로 결과: 1회 재검사 및 8단계 가드레일 효과 | [PNG](../graph/service-actions.png) | [SVG](../graph/service-actions.svg) |
| 서버 내부 측정: 네트워크 제외 (8단계 종단간 SLA는 stage8-runtime-latency-sla 참조) | [PNG](../graph/service-latency.png) | [SVG](../graph/service-latency.svg) |
| service 정상 문서 실패율과 Wilson 95% 구간 (8단계 0/100 달성) | [PNG](../graph/service-normal-failure.png) | [SVG](../graph/service-normal-failure.svg) |
| 차단·오류 문서는 분모에서 제외 — allow/mask 전달 문서의 보호 대상만 계산 | [PNG](../graph/service-residual.png) | [SVG](../graph/service-residual.svg) |
| official 최종 시험: 전체 엔터티 성능 (M0 vs 7.5단계 vs 8단계) | [PNG](../graph/official-overall.png) | [SVG](../graph/official-overall.svg) |
| official 유형별 성능 — gold 0인 유형은 N/A 표시 (M0 vs 7.5단계 vs 8단계) | [PNG](../graph/official-per-type.png) | [SVG](../graph/official-per-type.svg) |
| official B 게이트웨이 정책 경로 결과: 1회 재검사 및 8단계 가드레일 효과 | [PNG](../graph/official-actions.png) | [SVG](../graph/official-actions.svg) |
| 서버 내부 측정: 네트워크 제외 (8단계 종단간 SLA는 stage8-runtime-latency-sla 참조) | [PNG](../graph/official-latency.png) | [SVG](../graph/official-latency.svg) |
| official 정상 문서 실패율과 Wilson 95% 구간 (8단계 0/100 달성) | [PNG](../graph/official-normal-failure.png) | [SVG](../graph/official-normal-failure.svg) |
| 차단·오류 문서는 분모에서 제외 — allow/mask 전달 문서의 보호 대상만 계산 | [PNG](../graph/official-residual.png) | [SVG](../graph/official-residual.svg) |
| 동결 서비스 300건 (7.5단계): 문서 짝지은 변화량 구간 | [PNG](../graph/service-paired-deltas.png) | [SVG](../graph/service-paired-deltas.svg) |
| 공식 Test 28,142건 (7.5/8단계): 문서 짝지은 변화량 구간 — 정상 문서 없음(N/A) | [PNG](../graph/official-paired-deltas.png) | [SVG](../graph/official-paired-deltas.svg) |
| 동결 서비스 300건 (8단계 B통합 최종): 문서 짝지은 변화량 95% 신뢰구간 | [PNG](../graph/stage8-service-paired-deltas.png) | [SVG](../graph/stage8-service-paired-deltas.svg) |
| 8단계 B 게이트웨이 종단간 지연시간 (NVIDIA L4, 동시성=1, <=2,000 코드포인트) | [PNG](../graph/stage8-runtime-latency-sla.png) | [SVG](../graph/stage8-runtime-latency-sla.svg) |
| 8단계 B 런타임 무중단 핫스왑·롤백 및 메모리 실측 (NVIDIA L4) | [PNG](../graph/stage8-lifecycle-and-memory.png) | [SVG](../graph/stage8-lifecycle-and-memory.svg) |


---

# 4단계 평가 시스템 (Evaluation System)

본 문서는 **4단계 평가 체계(Evaluation System)**의 지표 정의, 구현 구조, CLI 사용법, 그리고 실제 모델 측정과의 분리 기준을 설명합니다.

> **원칙**: 평가 체계는 모델을 실제로 호출하거나 학습하지 않고도 합성 정답·예측 자료를 통해 오프라인에서 검증할 수 있도록 설계되었습니다. 실제 모델 가중치 측정치나 임의의 점수를 꾸며내지 않으며, 실측 대기 항목은 명확히 `not_executed` 및 `pending` 상태로 구분합니다.

## 현재 상태: 7.5단계 및 8단계(시나리오 B) 최종 완료 (2026-11-01)

7.5단계 오프라인 평가 보완에 이어, **8단계(시나리오 B)**에서 B 프로덕션 백엔드([`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py))에 `ko_atomic_suffix_v3`, 경계 안전 윈도우 폴백(`_whole_group_plan`), `KR_RRN` 포맷·생년월일·경계 가드레일(`_is_valid_kr_rrn`), 2차 재검사 `[REDACTED]` 플레이스홀더 중첩 가드레일(`_overlaps_redacted_placeholder`)을 통합하고 GPU 서버(`NVIDIA L4`) 런타임 인수 검증([`run_stage8_acceptance.py`](run_stage8_acceptance.py))을 완료했습니다.

- **최종 판정**: **`replace`** (`runtime_acceptance=True`, `b_splitter_adoption=True`, 5대 교체 게이트 전원 통과)
- 최신 결과와 근거는 **[최종 보고서 v2 (`report_v2.md`)](../report_v2.md)**(7.5단계 보존본 [보고서 v1](../report_v1.md)), [Stage 8 서비스 최종 집계](../reports/stage8-service-final-v1.json), [Stage 8 공식 Test 최종 집계](../reports/stage8-official-final-v1.json), [Stage 8 런타임 인수](../reports/stage8-runtime-acceptance-v1.json)를 확인하세요. (초기 서비스 v1 미동결본은 탐색·회귀 자료로만 보존합니다.)

---

## 1. 지표 정의 (Metrics Definition)

### 1.1 문자 위치 기준 스팬 일치 (`char_span_exact_v1`)
- **채점 단위**: `(document_id, type, start, end)`
  - `start` (0-based inclusive), `end` (exclusive Unicode codepoint index)
  - 엔터티 종류(`type`)와 두 위치(`start`, `end`)가 모두 정확히 일치해야 **TP (True Positive)**로 인정됩니다.
  - 다른 문서에 동일한 문장이 등장하더라도 `document_id`별로 독립적으로 채점됩니다.
  - 동일한 문자 구간에 다른 유형 라벨이 있는 경우(multi-type span) 각각 별개의 정답/예측으로 취급되며, 겹치는 정답을 임의로 제거하지 않습니다.
- **중복 정답/예측 처리 (Multiset Policy)**:
  - `duplicates='multiset'` (기본값): 동일 튜플이 여러 개 존재할 경우, 각 예측은 최대 하나의 정답과 1:1 매칭됩니다.
    - $TP = \min(\text{gold\_count}, \text{pred\_count})$
    - $FP = \max(0, \text{pred\_count} - \text{gold\_count})$
    - $FN = \max(0, \text{gold\_count} - \text{pred\_count})$
  - 집합(`set`) 변환으로 중복을 무단 제거하여 점수를 왜곡하지 않습니다.
  - `duplicates='error'` 옵션을 통해 중복 발생 시 즉시 예외를 발생시킬 수도 있습니다.
- **분모 0 처리 (Zero Denominator Policy)**:
  - Precision: $TP / (TP + FP)$ (분모 0이면 `null` / `None`)
  - Recall: $TP / (TP + FN)$ (분모 0이면 `null` / `None`)
  - F1: $2 \cdot TP / (2 \cdot TP + FP + FN)$ (분모 0이면 `null` / `None`)
  - 정답이나 예측이 없는 경우 임의로 0%나 100%를 부여하지 않고 명시적 평가 불가(`null`)로 기록합니다. (예: 원본 dev/test에서 `PERSONAL_ATTRIBUTE`는 정답 0건이므로 recall은 `null`).
- **전체 Micro 및 Macro 지표**:
  - `overall_micro`: 모든 엔터티 유형의 TP, FP, FN 합계를 기준으로 단일 precision/recall/F1 계산.
  - `critical_micro`: 5대 중요 유형(`KR_RRN`, `KR_BANK_ACCOUNT`, `CREDIT_CARD`, `KR_PHONE_NUMBER`, `EMAIL_ADDRESS`)만의 합산 마이크로 지표.
  - `macro_f1`: $2 \cdot TP + FP + FN > 0$인 유형들에 대한 비가중 산술 평균이며, 계산 대상 및 제외 유형 목록(`averaged_types`, `excluded_types`)을 항상 함께 명시합니다.
- **공식 BIO 점수와의 분리**:
  - 공식 CoNLL/GLiNER 토큰 단위 평가는 `bio_entity_exact_v1` 메트릭 식별자로 완전히 분리되어 관리됩니다.

### 1.2 신뢰구간 및 불확실성 산출
- **Wilson Score Interval**: 개별 비율(재현율, 오차단율 등)에 대해 표본 수 $N$을 고려한 95% Wilson 신뢰구간(`wilson_interval`) 제공.
- **문서 단위 부트스트랩 (Document-level Percentile Bootstrap)**: 엔터티 단위가 아닌 문서 단위 리샘플링(1000회)으로 F1/Recall의 95% 신뢰구간 산출.
- **쌍체 부트스트랩 델타 (Paired Bootstrap Delta)**: 동일 문서 리샘플링 기반으로 두 모델 간 지표 차이의 신뢰구간 산출.

### 1.3 B 정책 결합 지표 (`policy-metrics-v1`)
B 파트의 fail-closed 운영 정책([policy.py](../../dev/gateway/policy.py)) 및 비밀키 검출 규칙([rules.py](../../dev/detector/rules.py))을 직접 재사용합니다.
- **정상 문장 오차단율 (`false_block_rate`)**:
  - 정상 문장(정답 PII가 0개인 문서)에 대해 B 정책 판단 결과가 `block`이 된 비율.
  - 모델의 단순 NER 오탐이 `mask` 대상인 경우 실제 차단(`block`)이 되지 않으므로, 정책 적용 오차단율과 단순 NER 오탐률을 엄격히 구분합니다.
  - 정책 판정 에러(`error`)는 차단과 동일하게 fail-closed로 취급하여 `false_block_or_error_rate`에 반영.
- **마스킹 잔존율 (`masking_residual`)**:
  - 마스킹 판정(`mask`)된 문서에서 마스킹 대상 정답 엔터티의 글자 구간 중 `[REDACTED]`로 가려지지 않고 노출된 글자가 1글자라도 있는 엔터티 비율.
- **모델 단독 vs 규칙 결합 모드 분리**:
  - `model_only`: 모델 추론 결과만으로 정책 평가.
  - `model_plus_same_rules`: 동일한 정규식/비밀키 규칙을 모델 출력과 결합하여 평가.
  - 비밀키 규칙의 성과를 모델 파인튜닝 성과로 전용하여 기록하는 것을 엄격히 차단합니다.

---

## 2. 모듈 구조

```text
model/evaluation/
├── __init__.py
├── __main__.py               # CLI 엔트리포인트 (python -m model.evaluation)
├── cli.py                    # score, check-record, compare, decide 서브커맨드
├── metrics.py                # char_span_exact_v1, bio_entity_exact_v1, Wilson, Bootstrap
├── policy_metrics.py         # B 정책 연동 (false_block_metrics, masking_residual)
├── records.py                # experiment-plan.json 스키마 기반 실험 기록 및 검증
├── compare.py                # 두 실험 기록의 공정 비교 및 PRD 교체 판정 게이트
├── service_cases.py          # 독립 시험셋 300건 구조/중복/누수 검증기
├── service_testset_builder.py# 독립 시험셋 300건 결정론적 생성기
├── service_testset/
│   └── service-test-v1.draft.jsonl  # 300건 검토용 초안 데이터셋 (*.jsonl은 gitignore 대상)
└── service-test-v1.manifest.json    # 시험셋 해시, 통계, 출처, 검증 기록 manifest (Git 커밋 대상)
```

---

## 3. CLI 사용법

### 3.1 지표 채점 (`score`)
합성 정답과 예측 JSONL 파일을 비교하여 지표를 산출합니다.
```bash
# 기본 문자 스팬 지표 산출
model/.cache/conversion-venv/bin/python -m model.evaluation score \
  --gold path/to/gold.jsonl \
  --pred path/to/pred.jsonl

# B 정책(정상 문장 오차단율, 마스킹 잔존율) 및 비밀키 규칙 결합 모드 포함
model/.cache/conversion-venv/bin/python -m model.evaluation score \
  --gold path/to/gold.jsonl \
  --pred path/to/pred.jsonl \
  --with-policy --rules
```

### 3.2 실험 기록 무결성 검증 (`check-record`)
실험 기록 JSON이 [experiment-plan.json](../configs/experiment-plan.json)의 명세를 준수하는지 검증합니다.
```bash
model/.cache/conversion-venv/bin/python -m model.evaluation check-record record.json
```

### 3.3 공정 조건 비교 (`compare`)
동일한 평가 조건(데이터 리비전, 시험셋 버전, 지표 프로토콜, 정책, 리포트 모드 등) 하에서 측정된 두 실험의 델타(%p 차이 및 건수 차이)를 산출합니다.
```bash
model/.cache/conversion-venv/bin/python -m model.evaluation compare m1_record.json m0_record.json
```

### 3.4 PRD 교체 판정 (`decide`)
PRD §6의 모델 교체 지표를 검사합니다. 시험 동결·dev 선택·실제 모델 해시·검사 완료 여부는 현재 CLI가 자동 인증하지 않습니다.
```bash
model/.cache/conversion-venv/bin/python -m model.evaluation decide m1_record.json m0_record.json
```
B의 실제 로딩·지연·메모리·통합 수용 근거가 있을 때만 `--runtime-accepted`를 사용합니다. 현재는 붙이지 않습니다. `decide`의 종료 코드는 `replace`일 때 0, 나머지 판정일 때 3입니다. 기존 결과의 `reject`는 정상적인 거절 출력이며 실행 실패와 구분합니다.

- **판정 결과 (`verdict`)**:
  - `replace`: 모든 기준 통과 및 B 운영 수용 확인 완료.
  - `pending_runtime_acceptance`: 성능 지표는 통과했으나 B 파트의 지연시간/메모리/통합 검증 승인이 미완료됨.
  - `hold`: 5대 중요 유형 중 미해결된 성능 퇴행(`unresolved_critical_regression`)이 존재함.
  - `reject`: F1, 5대 중요 유형 재현율이 기준선 미만이거나 정상 문장 오차단율이 1.0%p 초과 증가함.
  - `pending`: 미실행 상태이거나 필수 지표가 평가 불가(`null`)임.
  - `not_comparable`: 비교 조건이 불일치함.

---

## 4. 독립 서비스 시험셋 300건 (B4)

- **구성**: 요청 150건 + 응답 150건 = 총 300건
- **정상 사례**: 개인정보가 전혀 없는 순수 정상 문서 100건 (오차단율 평가용)
- **5대 중요 유형 보존**: `KR_RRN`(42개), `CREDIT_CARD`(41개), `KR_PHONE_NUMBER`(80개), `KR_BANK_ACCOUNT`(53개), `EMAIL_ADDRESS`(77개) 각각 최소 30개 이상 확보.
- **특수 스트레스 환경 포함**: 표(table), JSON, 코드 블록, 조사/접미사 경계, 600자 이상 장문 문서(9건), 한영 혼합 언어 등.
- **공식 데이터 결측 유형 검증**: 공식 dev/test셋에 정답이 0개인 `PERSONAL_ATTRIBUTE` 정답 10개 포함.
- **안전성 및 오염 방지**:
  - 실제 개인정보나 실제 인증정보가 포함되지 않도록 RFC 2606 예시 도메인, RFC 5737 테스트 IP 대역, 가상 도로명/회사명을 엄격히 사용.
  - 공식 K-PII-Bench 원본과의 완전 텍스트 중복 0건, 8자 이상 고유 엔터티 표면형 중복 0건 검증 완료.
- **동결 및 버전 관리 방침**:
  - 생성된 300건 파일은 현재 `review_status: needs_review` 상태인 초안(`draft`)입니다.
  - `ready_to_freeze`는 기계적 검사·사람 검토 후 동결 가능한 상태이며, 동결 자체는 별도로 시점·버전·해시를 기록해야 합니다. 현재 manifest는 `frozen=false`입니다.
  - 원래 최종 평가용으로 계획했으나 미동결 상태에서 이미 점수를 확인했습니다. 기존 v1은 탐색/회귀용으로 보존합니다. 새 독립 최종 시험은 train/dev·조정 자료와 분리해 선택 과정 전에 사람 검토·동결하고, 후보·임계값 선택에 사용하지 않습니다.

---

## 5. 현재 구현·실행·검증 범위

| 항목 | 현재 상태 | 7.5단계에서 필요한 것 |
|---|---|---|
| 스팬 지표·CI 계산 함수 | 구현·합성 테스트 있음 | 최종 실측에 유형별 CI·분모 출력. 함수 존재를 실제 CI 보고 완료로 간주하지 않음 |
| B 정책 어댑터 | 합성 테스트, M0 서비스 오차단 재현 | M0/M1 동일 규칙 결합·잔존율·실패 지표 실제 보고 |
| 실험 기록·교체 판정 | 저장 JSON 내부 일관성 및 `reject` 재현 | 실제 실행 설정·해시 연결, 공통 비교 ID, B 수용 상태 바로잡기 |
| 서비스 v1 | 300건 초안·기계적 검사, 전수 미검토·미동결 | 기존 v1 보존, 새 최종 holdout 사람 검토·사전 동결 |
| M0 초기 추론 | 결과 JSON 및 로컬 서비스 예측 있음 | 수정된 실행기·공통 분할·dev 절차에서 기준선 확인 |
| M1 초기 학습·추론 | 실행 보고·결과 JSON 있음 | 실제 인자·저장본/로그 회수, 공통 분할 연결, dev 선택 |
| 최종 비교 | 잠정 집계, 오차단 +7%p 및 중요 유형 퇴행 | 공식 test·새 서비스 최종 평가, 모든 교체 조건과 한계 보고 |

## 6. 실행기를 보완할 때 지킬 계약

1. `complete/incomplete/error`를 문서마다 보존합니다. 미완료를 빈 탐지로 바꾸거나 합격 평가로 승인하지 않습니다.
2. 학습 입력·GLiNER 실제 예측·backend 창 계획·위치 복원에 같은 분할을 적용하고 재로딩 후에도 검증합니다.
3. 데이터·시험·표본·모델·설정·소스 해시와 실행 시각을 기록합니다. 문서별 예측과 상태는 원문 노출을 줄인 Git 제외 경로에 보존합니다.
4. `comparison_condition_id`를 실제 공통 조건에 맞게 생성합니다. ID 일치나 스키마 통과만으로 공정성이 성립하지 않습니다.
5. M0/M1 모두 dev와 별도 정상 조정 자료에서 같은 절차·예산으로 설정을 선택합니다. 최종 시험으로 선택하지 않습니다.
6. CI는 방법·단위·표본 수를 기록합니다. paired 비교에는 동일 문서별 M0/M1 예측이 필요합니다. gold 0인 유형의 recall은 평가 불가로 표시합니다.

현재 `model_plus_same_rules`는 모델 탐지에 B 비밀키 규칙 탐지를 더하는 어댑터입니다. 모델의 오탐을 자동으로 제거하는 체크섬 필터가 아닙니다. 임계값 상향의 효과도 dev에서 측정하기 전에는 보장하지 않습니다.
