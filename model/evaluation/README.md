# 4단계 평가 시스템 (Evaluation System)

본 문서는 **4단계 평가 체계(Evaluation System)**의 지표 정의, 구현 구조, CLI 사용법, 그리고 실제 모델 측정과의 분리 기준을 설명합니다.

> **원칙**: 평가 체계는 모델을 실제로 호출하거나 학습하지 않고도 합성 정답·예측 자료를 통해 오프라인에서 검증할 수 있도록 설계되었습니다. 실제 모델 가중치 측정치나 임의의 점수를 꾸며내지 않으며, 실측 대기 항목은 명확히 `not_executed` 및 `pending` 상태로 구분합니다.

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
PRD §11의 모델 교체 판정 게이트를 자동으로 검사합니다.
```bash
model/.cache/conversion-venv/bin/python -m model.evaluation decide m1_record.json m0_record.json --runtime-accepted
```
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
- **5대 중요 유형 보존**: `KR_RRN`(39개), `CREDIT_CARD`(40개), `KR_PHONE_NUMBER`(81개), `KR_BANK_ACCOUNT`(52개), `EMAIL_ADDRESS`(77개) 각각 최소 30개 이상 확보.
- **특수 스트레스 환경 포함**: 표(table), JSON, 코드 블록, 조사/접미사 경계, 600자 이상 장문 문서(8건), 한영 혼합 언어 등.
- **공식 데이터 결측 유형 검증**: 공식 dev/test셋에 정답이 0개인 `PERSONAL_ATTRIBUTE` 정답 11개 포함.
- **안전성 및 오염 방지**:
  - 실제 개인정보나 실제 인증정보가 포함되지 않도록 RFC 2606 예시 도메인, RFC 5737 테스트 IP 대역, 가상 도로명/회사명을 엄격히 사용.
  - 공식 K-PII-Bench 원본과의 완전 텍스트 중복 0건, 8자 이상 고유 엔터티 표면형 중복 0건 검증 완료.
- **동결 및 버전 관리 방침**:
  - 생성된 300건 파일은 현재 `review_status: needs_review` 상태인 초안(`draft`)입니다.
  - 인간 검토가 완료되어 승인되기 전까지는 최종 동결(`ready_to_freeze`)로 표시하지 않습니다.
  - 본 독립 시험셋은 **최종 모델 평가 전용**이며, dev 튜닝 및 하이퍼파라미터/임계값 탐색에 절대 재사용할 수 없습니다.

---

## 5. 실측 대기 항목 (GPU 접속 전 현황)

| 항목 | 현재 상태 | 비고 |
|---|---|---|
| 평가 지표 및 신뢰구간 산출 코드 | **구현 및 100% 테스트 통과** | `model/tests/test_evaluation_metrics.py` |
| B 정책 연동 (오차단/마스킹잔존) | **구현 및 100% 테스트 통과** | `model/tests/test_evaluation_policy.py` |
| 실험 기록 및 교체 판정 게이트 | **구현 및 100% 테스트 통과** | `model/tests/test_evaluation_records_compare.py` |
| 독립 시험셋 300건 생성 및 검증 | **초안 생성 및 무결성 검증 완료** | `service-test-v1.manifest.json` |
| M0 모델 실제 기준선 추론 | **실측 대기 (Pending GPU)** | 가중치 로딩 및 GPU 인퍼런스 필요 |
| M1 추가 학습 및 dev 임계값 선정 | **실측 대기 (Pending GPU)** | GPU 학습 환경 필요 |
| M0 vs M1 최종 비교 리포트 | **실측 대기 (Pending GPU)** | 실제 평가 결과 기록 후 산출 |

