# A 파트: 모델 추가 학습 준비 및 평가 체계 (`model_README.md`)

먼저 [쉬운 전체 작업 안내](process.md) 및 [Codex 인수인계 문서](to_codex.md)를 읽으면 됩니다. 이 폴더는 기존 개인정보 탐지 모델 M0를 한국어 데이터로 추가 학습하고, 후보 M1을 공정하게 평가하기 위한 A 작업 공간입니다.

---

## 현재 진행 상태 (1~7단계 완료)

### 1. 작업 A: 한국어 분할 호환성 해결안 도출 (완료, 95.9% 달성)
- **문제 진단**: B의 기존 공백 분할기(`whitespace_default_v1`) 적용 시, 한글 조사/접미사 결합(`김가상에게`, `010...로`)으로 인해 전체 243,711개 train 문서 중 2,615건(1.08%)만 표현 가능하여 `blocked_compatibility` 상태였습니다.
- **해결안 구현**:
  - 원문 텍스트와 골드 오프셋을 일체 변경하지 않고 한글 자모 및 특수문자 규칙을 적용하는 `ko_atomic_suffix_v3` 분할기 구현.
  - train 243,711건 중 **233,724건(95.9%) 고유 문서 확보** 및 URL/이메일을 포함한 18개 전 유형 정답 보존 달성 (PRD 5,000건 요건 초과 충족).
  - B 파트 연동을 위한 규격 제안서([model/segmentation/README.md](segmentation/README.md)) 작성 완료.

### 2. 작업 B: 4단계 평가 시스템 구현 및 독립 서비스 시험셋 구성 (완료)
- **문자 스팬 단위 지표 (`char_span_exact_v1`)**:
  - 채점 단위 `(document_id, type, start, end)` 기준 TP/FP/FN, precision, recall, F1 구현.
  - 분모 0일 때 `null` 처리, `duplicates='multiset'` 정책, Wilson 95% 신뢰구간, 문서 단위 부트스트랩 구현.
- **B 정책 결합 지표 (`policy-metrics-v1`)**:
  - B의 [policy.py](../dev/gateway/policy.py)를 재사용하여 정상 문장 오차단율(`false_block_rate`) 및 마스킹 잔존율(`masking_residual`) 산출.
  - 모델 단독(`model_only`)과 B 비밀키 규칙 결합(`model_plus_same_rules`) 모드 분리.
- **실험 기록 및 교체 판정 게이트**:
  - [experiment-plan.json](configs/experiment-plan.json) 스키마를 엄격히 검증하는 [records.py](evaluation/records.py) 구현.
  - 동일 평가 조건 검증, %p 델타 계산 및 PRD §11 교체 기준(재현율/F1 유지, 오차단율 +1.0%p 이하, 5대 중요유형 퇴행 방지, B 런타임 수용)을 자동 판정하는 [compare.py](evaluation/compare.py) 구현.
- **독립 서비스 시험셋 300건 (B4)**:
  - 요청 150건 + 응답 150건 = 300건 구축 ([service-test-v1.manifest.json](evaluation/service-test-v1.manifest.json)).
  - 정상 문서 100건, 5대 중요 유형 각 30개 이상 확보. K-PII-Bench 중복 0건.
- **평가 CLI**: `score`, `check-record`, `compare`, `decide` 커맨드 제공 ([model/evaluation/README.md](evaluation/README.md)).

### 3. 작업 C: 후보 보충 데이터셋(`townboy/korean-pii-dataset`) 검증 (공식 채택 제외)
- 11,732건 원문 스팬 100% 무결성 검증 (오류 0건).
- 확정 15개 라벨 적용 시 유효 train 문서는 1,780건에 불과하여 PRD 5,000건 기준 미달로 **공식 채택 보류(`candidate_not_adopted`)** 판정 ([보고서](reports/candidate-supplement-summary.md)).

### 4. 작업 D (5단계): GPU M0 베이스라인 실측 완료 (완료)
- 원격 GPU 서버(NVIDIA L4 24GB, CUDA 13.0)에서 M0 모델(`urchade/gliner_multi_pii-v1`) 실측 완료 ([상세 보고서](reports/m0-baseline-summary.md)):
  - **독립 서비스 평가셋 (300건)**: **Overall F1 = 49.16%**, **Critical Recall = 51.88%**, **Normal False Block Rate = 1.00%** (소요시간 6.8초).
  - **K-PII-Bench Dev (28,147건 전수)**: **Overall F1 = 19.61%**, **Critical Recall = 16.89%** (소요시간 10.4분).
- 주민등록번호(RRN 7.15%), 여권/건보(0%) 등 한국어 식별자 부재 실증 $\rightarrow$ M1 파인튜닝의 필요성과 목표치 명확화.
- 생성 레코드: [`m0-baseline-record.json`](reports/m0-baseline-record.json), [`m0-dev-record.json`](reports/m0-dev-record.json).

### 5. 작업 E (6~7단계): GPU M1 파인튜닝 및 PRD §11 교체 판정 완료 (완료)
- 원격 GPU 서버에서 5,000건 표본 2 에포크 학습 완료 (Loss 8.61 $\rightarrow$ 1.22, 가중치: `/home1/ncloud/m1_model`, [상세 보고서](reports/m1-training-summary.md)):
  - **독립 서비스 평가셋 (300건)**: **Overall F1 = 58.31% (+9.16%p)**, **Critical Recall = 61.43% (+9.56%p)**, **False Block Rate = 8.00% (+7.00%p)**.
  - **주민등록번호(KR_RRN)**: 재현율 9.52% $\rightarrow$ **69.05%** (+59.52%p, 미탐 25건 감소).
  - **운전면허증/여권번호**: 100% 정밀도 및 재현율 달성.
  - **K-PII-Bench Dev (28,147건 전수)**: **Overall F1 = 30.02% (+10.41%p)**, **Critical Recall = 24.41% (+7.52%p)**.
- **PRD §11 판정 결과**: F1/재현율 기준 통과했으나, 오차단율 증가(+7.0%p)로 인해 **`reject`** 판정 (`m1-vs-m0-decision.json`). Stage 8에서 임계값 캘리브레이션 및 게이트웨이 정규식 룰 결합으로 오차단율 억제 계획 수립.

### 6. 테스트 현황
- 단위/통합 테스트 **173개 전체 통과** (`pytest model/tests -q`). 로컬 CPU 환경에서 0.23초 만에 전수 통과.

---

## 어떤 버전을 사용하나요?

| 대상 | 이름·버전 | 고정 리비전 |
|---|---|---|
| 기존 모델 M0 | `urchade/gliner_multi_pii-v1` | `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` |
| 학습 데이터 | `woohyun212/k-pii-bench`, 원본 통계의 표기 `v4-corrected` | `844f92f7beba98fa964f957ba324017e82451701` |
| M0 기반 모델 설정·토크나이저 | `microsoft/mdeberta-v3-base` | `a0484667b22365f84929a935b5e50a51f71f159d` |
| GLiNER 코드 | `0.2.29`, B와 동일 | `3e1723b49539d518f76d4d8282f1e05d8b6e74f0` |
| 후보 보충 데이터 | `townboy/korean-pii-dataset` (채택 보류) | `076419f07f7073b6b28cdfdb6fbd8b75e1278053` |

---

## 파일을 찾는 방법

| 파일·폴더 | 담긴 내용 |
|---|---|
| [to_codex.md](to_codex.md) | **Codex 인수인계 문서 (현재 진행 상황 및 6~8단계 실행 가이드)** |
| [process.md](process.md) | 쉬운 단계별 전체 작업 흐름 및 종합 체크리스트 |
| [configs/assets.lock.json](configs/assets.lock.json) | M0 및 K-PII-Bench 고정 자산 명세 |
| [configs/candidate-townboy-lock.json](configs/candidate-townboy-lock.json) | 후보 보충 데이터셋 파일 크기·SHA-256 및 CC-BY-4.0 명세 |
| [configs/experiment-plan.json](configs/experiment-plan.json) | 학습·평가 출발 설정, 교체 판정 게이트, 실험 기록 명세 |
| [segmentation/splitters.py](segmentation/splitters.py) | 한국어 단어 분할기 (`ko_atomic_suffix_v3` 권장 구현) |
| [segmentation/README.md](segmentation/README.md) | 분할 호환성 분석 및 B 파트 연동 규격 제안서 |
| [evaluation/README.md](evaluation/README.md) | 4단계 평가 시스템 구조, 지표 정의, CLI 가이드 |
| [evaluation/evaluate_m0.py](evaluation/evaluate_m0.py) | GPU 서버 전용 M0 베이스라인 실측 자동화 스크립트 |
| [evaluation/service-test-v1.manifest.json](evaluation/service-test-v1.manifest.json) | 독립 서비스 시험셋 300건 무결성·통계·중복 검증 명세 |
| [reports/segmentation-diagnosis-v2.json](reports/segmentation-diagnosis-v2.json) | 분할기별 변환율/유형보존 진단 결과 (ko_atomic_suffix_v3 95.9% 달성) |
| [reports/candidate-supplement-summary.md](reports/candidate-supplement-summary.md) | 후보 보충 데이터셋 검증 결과 요약 보고서 (공식 제외) |
| [reports/m0-baseline-summary.md](reports/m0-baseline-summary.md) | **M0 GPU 베이스라인 실측 결과 보고서 (5단계 완료)** |
| [reports/m0-baseline-record.json](reports/m0-baseline-record.json) | 독립 300건 공식 베이스라인 검증 레코드 (F1 49.16%, Critical Recall 51.88%) |
| [reports/m0-dev-record.json](reports/m0-dev-record.json) | Dev 28,147건 전수 실측 레코드 (F1 19.61%) |
| [reports/m1-training-summary.md](reports/m1-training-summary.md) | **M1 GPU 파인튜닝 및 평가 종합 보고서 (6~7단계 완료)** |
| [reports/m1-service-test-record.json](reports/m1-service-test-record.json) | M1 서비스 시험셋 검증 레코드 (F1 58.31%, Critical Recall 61.43%) |
| [reports/m1-dev-record.json](reports/m1-dev-record.json) | M1 Dev 28,147건 전수 실측 레코드 (F1 30.02%) |
| [reports/m1-vs-m0-comparison.json](reports/m1-vs-m0-comparison.json) | M1 vs M0 지표 델타 레코드 |
| [reports/m1-vs-m0-decision.json](reports/m1-vs-m0-decision.json) | PRD §11 교체 판정 결과 (`reject`: 오차단율 증가) |

---

## 단계별 진행 현황 및 남은 작업

1. [x] **서버 환경 확인 (5단계)**: NVIDIA L4 24GB (Driver 580.159, CUDA 13.0, py312), VRAM 21.6GB 여유 확인 및 SSH 키 인증 구성 완료.
2. [x] **M0 기준선(Baseline) 실측 (5단계)**: 독립 서비스 시험셋 300건 및 Dev 28,147건 전수 GPU 실측 완료 (`m0-baseline-record.json` 생성).
3. [x] **학습 표본 5,000건 추출 (6단계)**: 변환 성공 train 233,724건 중 시드 42로 5,000건 표본 선정 및 해시 기록 (`train_5k.jsonl`).
4. [x] **M1 추가 학습 및 dev 평가 (6단계)**: batch 4, LR 2e-5, 2 epoch GPU 파인튜닝 완료 (Loss 8.61 $\rightarrow$ 1.22, 가중치 저장 완료).
5. [x] **최종 공정 비교 및 교체 판정 게이트 (7단계)**: M0 vs M1 교체 판정 게이트(`compare.py`) 실행 (F1 +9.16%p, Critical Recall +9.56%p 달성, 오차단율 +7.0%p로 `reject` 판정 및 임계값 튜닝 후속 조치 수립).
6. [ ] **B와 분할기 공동 채택**: `ko_atomic_suffix_v3`를 A/B 공동 분할기로 최종 합의.
7. [ ] **모델 패키지 구성 및 B 인계 (8단계)**: 모델 아티팩트 번들링, 임계값 캘리브레이션 프로파일 포함 및 최종 핸드오프.
