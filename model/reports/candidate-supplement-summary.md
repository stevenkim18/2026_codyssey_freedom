# 작업 C: 후보 보충 데이터셋 검증 보고서 (`townboy/korean-pii-dataset`)

작성일: 2026-10-31 · 상태: **검증 완료, 공식 학습 데이터 채택 보류 (`candidate_not_adopted`)**  
근거 데이터: [candidate-townboy-v1.json](candidate-townboy-v1.json) · 잠금 명세: [candidate-townboy-lock.json](../configs/candidate-townboy-lock.json)

현재 K-PII-Bench를 유지하며 이 후보는 미채택입니다. 7.5단계 및 8단계를 통해 K-PII-Bench 기반 M1이 프로덕션 교체 승인(`verdict: "replace"`)을 완료했으며, `PERSONAL_ATTRIBUTE` 등 희소 유형 보강은 차기 마일스톤(시나리오 C, [report_v2.md §5.2](../report_v2.md)) 추후 개발 과제로 관리합니다.

---

## 1. 개요 및 목적

본 조사는 K-PII-Bench **dev/test의** `PERSONAL_ATTRIBUTE` 정답 부재 및 특정 라벨 수량 보충 가능성을 검토하기 위해 공개 데이터셋 [townboy/korean-pii-dataset](https://huggingface.co/datasets/townboy/korean-pii-dataset)을 독립적으로 검증한 결과입니다.
- **원칙**: 본 데이터는 *후보 조사*이며, PRD 공식 기준인 K-PII-Bench를 임의로 대체하거나 조용히 합치지 않습니다.
- **분할 원칙**: 보충 데이터의 `test` 분할은 학습에 절대 사용하지 않으며, 자체 `train` 및 `validation` 분할 체계를 독립적으로 보존합니다.

---

## 2. 출처 및 라이선스 고정

- **Hugging Face 저장소**: `townboy/korean-pii-dataset`
- **고정 리비전 (Revision)**: `076419f07f7073b6b28cdfdb6fbd8b75e1278053` (2026-08-09 기준)
- **라이선스**: **Creative Commons Attribution 4.0 International (CC BY 4.0)**
  - 원저작자 표시 의무: `"townboy/korean-pii-dataset, Korean Synthetic PII Dataset."`
  - 상업적 이용 및 수정 배포가 허용되나, 도입 시 고지 파일([licenses/NOTICE.md](../licenses/NOTICE.md))에 출처 표기가 필수적입니다.
- **다운로드 검증**:
  - `data/train.jsonl` (8.83MB, SHA-256: `2b87eaa15...`)
  - `data/validation.jsonl` (926KB, SHA-256: `9ba3b1efd...`)
  - `data/test.jsonl` (474KB, SHA-256: `53c96d407...`)
  - `label_map.json`, `dataset_stats.json`, `LICENSE`, `README.md` 전체 7개 파일의 SHA-256 해시를 [candidate-townboy-lock.json](../configs/candidate-townboy-lock.json)에 고정 완료.
  - 다른 모델(KPF-BERT)용 BIO 전처리 데이터(`bio/*.jsonl`)는 읽지 않고 순수 원문 문자 위치 스팬(`raw`)만 검증했습니다.

---

## 3. 원본 데이터 품질 검사 (Audit)

전체 11,732건(`train` 9,227 + `validation` 1,510 + `test` 995)에 대한 기계적 검증 결과:
- **문자 스팬 일치율**: 100% (`raw_defects`: 0건)
  - `source_text[start:end] == value` 불일치 0건.
  - 음수 위치, 끝 위치 초과, 알 수 없는 라벨 0건.
- **동일 문서 내 겹치는 스팬 (`overlapping_spans`)**: 0건.
- **분할 간 동일 문장 중복**:
  - `train` + `validation`: **1건** 발견. (`train`+`test`, `validation`+`test`는 0건).
- **기존 프로젝트 데이터셋과의 중복 검사**:
  - K-PII-Bench 30만 건 원본과의 완전 텍스트 중복: **0건**.
  - 독립 서비스 시험셋 300건과의 완전 텍스트 중복: **0건**.
  - 8글자 이상 고유 엔터티 표면형 우연 일치: `train` 62건, `validation` 29건, `test` 17건 (매우 미미하며 합성 생성 규칙 유사성에 기인).

---

## 4. 33개 라벨과 프로젝트 18개 유형 매핑

| 분류 | 수량 | 라벨 목록 | 매핑 대상 및 비고 |
|---|---:|---|---|
| **확정 (`confirmed`)** | **15개** | `NAME`→`PERSON`, `EMAIL`→`EMAIL_ADDRESS`, `PHONE`→`KR_PHONE_NUMBER`, `ACCOUNT_NUMBER`→`KR_BANK_ACCOUNT`, `CARD_NUMBER`→`CREDIT_CARD`, `RRN`→`KR_RRN`, `ADDRESS`→`KR_LOCATION`, `BIRTHDATE`→`DATE_OF_BIRTH`, `DRIVER_LICENSE`→`KR_DRIVER_LICENSE`, `PASSPORTNUM`→`KR_PASSPORT`, `VEHICLE_NUMBER`→`KR_VEHICLE_PLATE`, `IP_ADDRESS`→`IP_ADDRESS`, `URL`→`URL`, `NATIONALITY`→`NRP`, `RELIGION`→`NRP` | 의미와 형식이 1:1로 명확히 부합함. |
| **미확정/주의 (`tentative`)** | **13개** | `BLOOD_TYPE`, `HEIGHT`, `WEIGHT`, `AGE`, `GENDER`, `MAJOR`, `POSITION` (7종) → `PERSONAL_ATTRIBUTE` 후보<br>`CITY` → `KR_LOCATION`<br>`USER_ID`, `NICKNAME` → `USERNAME`<br>`WORKPLACE`, `SCHOOL`, `DEPARTMENT` → `ORGANIZATION` | 세부 의미 정의가 다름. K-PII-Bench 및 PRD 기준과 정확히 일치하는지 A/B 합의 필요. |
| **미매핑 (`unmapped`)** | **5개** | `ZIPCODE`, `ALIEN_NUMBER`, `EMPLOYEE_ID`, `MEMBER_ID`, `PARTICIPANT_ID` | 프로젝트 18개 유형에 대응하는 라벨이 없음. |

### 무손실 거절 정책 준수
개인정보가 포함된 문장에서 미매핑 라벨만을 조용히 지워 정상 문장으로 둔갑시키는 행위를 일절 금지하고, 미매핑 라벨이 포함된 문장은 문서 전체를 거절(`rejected_for_unmapped_labels`)했습니다.

---

## 5. 변환 가능 수량 및 분할 호환성 결합 결과

### 5.1 정책별 매핑 가능 문서 수 (Train 9,227건 중)
- **`confirmed_only` (확정 15개만 허용)**:
  - 매핑 성공: **1,843건 (20.0%)**
  - 거절 (미매핑 라벨 포함): **7,384건 (80.0%)**
- **`confirmed_and_tentative` (주의 13개까지 허용)**:
  - 매핑 성공: **5,021건 (54.4%)**
  - 거절 (순수 미매핑 5종 포함): **4,206건 (45.6%)**

### 5.2 분할기별 실제 GLiNER 배치 변환 가능 수량 (Train 기준)
| 매핑 정책 | 분할기 (Splitter) | 변환 성공 문서 | 거절 문서 (사유) | 변환 성공 고유 문장 수 |
|---|---|---:|---:|---:|
| `confirmed_only` | `whitespace_default_v1` | 1,482건 | 361건 (경계불일치 358, 12단어초과 3) | **1,433건** |
| `confirmed_only` | `ko_atomic_suffix_v3` | 1,837건 | 6건 (윈도우불완전 5, 12단어초과 1) | **1,780건** |
| `confirmed_and_tentative` | `whitespace_default_v1` | 3,795건 | 1,226건 (경계불일치 846, 12단어초과 377) | **3,743건** |
| `confirmed_and_tentative` | `ko_atomic_suffix_v3` | 4,800건 | 221건 (12단어초과 179, 경계불일치 33) | **4,728건** |

### 5.3 분석 시사점
1. **분할 호환성 해결 없는 데이터 증강은 무의미**:
   - 다른 데이터를 가져오더라도 기존 공백 분할기(`whitespace_default_v1`)를 사용하면 조사 결합 등으로 인해 846건 이상의 정답 경계가 어긋나 대거 거절됩니다.
   - 한국어 분할 호환성(`ko_atomic_suffix_v3`)이 결합되어야만 94% 이상의 문서를 온전히 보존할 수 있습니다.
2. **`PERSONAL_ATTRIBUTE` 결측 보충 잠재력**:
   - `confirmed_and_tentative` 채택 시, `PERSONAL_ATTRIBUTE` 후보 엔터티 4,201개를 추가할 수 있습니다. K-PII-Bench train에는 이미 2,460개가 있으며 dev/test에만 정답이 없습니다. 학습 보충과 독립 평가 자료 확보는 별개의 문제입니다.
   - 단, 혈액형/신장/체중/전공 등이 실제 프로젝트 운영 정책에서 의도한 민감 개인속성과 일치하는지 사전 정의가 필수적입니다.

---

## 6. 최종 판정 및 권고사항

### 판정: **공식 학습 데이터 채택 보류 (`candidate_not_adopted`)**

1. **이유**:
   - 엄격한 1:1 확정 매핑(`confirmed_only`) 적용 시 유효한 학습 가능 고유 문서는 **1,780건**에 불과하여 PRD 5,000건 요건을 충족하지 못합니다.
   - 4,728건을 확보하려면 13개 미확정(`tentative`) 라벨을 모두 수용해야 하나, 이는 라벨 정의 변경을 수반하므로 B 파트 및 PRD 요구사항 검토 없이 단독 채택할 수 없습니다.
   - K-PII-Bench 원본만으로도 한국어 분할기 개선(`ko_atomic_suffix_v3`) 적용 시 **20만 건 이상의 학습 가능 고유 문서**가 확보되므로, 즉각적인 외부 데이터 추가의 시급성이 낮습니다.
2. **향후 보충 데이터 활용 가이드**:
   - 7.5단계에서 평가의 신뢰성을 보완한 뒤 `PERSONAL_ATTRIBUTE` 유형의 성능을 특화 보강할 근거가 있는 경우에 한하여 A/B 협의 하에 독립 서브셋으로 추가하는 방안을 권장합니다.

