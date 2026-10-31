# A 파트: 모델 추가 학습 준비 및 평가 체계

먼저 [쉬운 전체 작업 안내](process.md)를 읽으면 됩니다. 이 폴더는 기존 개인정보 탐지 모델 M0를 한국어 데이터로 추가 학습하고, 후보 M1을 공정하게 평가하기 위한 A 작업 공간입니다.

---

## 현재 진행 상태

### 1. 작업 A: 한국어 분할 호환성 해결안 도출 (후보 검증 완료, A/B 공동 채택 대기)
- **문제 진단**: B의 기존 공백 분할기(`whitespace_default_v1`) 적용 시, 한글 조사/접미사 결합(`김가상에게`, `010...로`)으로 인해 전체 243,711개 train 문서 중 2,615건(1.08%)만 표현 가능하여 `blocked_compatibility` 상태였습니다.
- **해결안 구현**:
  - 원문만 보고 결정하는 순수 분할기 `ko_script_suffix_v2` 및 URL 원자성 보호 분할기 `ko_atomic_suffix_v3` 구현.
  - train 243,711건 중 **201,274건(82.6%) 고유 문서 확보** 및 URL을 포함한 18개 전 유형 정답 보존 달성 (PRD 5,000건 요건 초과 충족).
  - B 파트 연동을 위한 규격 제안서([model/segmentation/README.md](segmentation/README.md)) 작성 완료. (A/B 공동 채택 전 운영 적용 금지 원칙 준수).

### 2. 작업 B: 4단계 평가 시스템 구현 및 독립 서비스 시험셋 구성 (구현 및 검증 완료)
- **문자 스팬 단위 지표 (`char_span_exact_v1`)**:
  - 채점 단위 `(document_id, type, start, end)` 기준 TP/FP/FN, precision, recall, F1 구현.
  - 분모 0일 때 `null` 처리, `duplicates='multiset'` 정책(임의 set 축소 방지), Wilson 95% 신뢰구간, 문서 단위 부트스트랩 구현.
  - 공식 CoNLL BIO 평가 지표(`bio_entity_exact_v1`)와 명확히 분리.
- **B 정책 결합 지표 (`policy-metrics-v1`)**:
  - B의 [policy.py](../dev/gateway/policy.py)를 재사용하여 정상 문장 오차단율(`false_block_rate`) 및 마스킹 잔존율(`masking_residual`) 산출.
  - 모델 단독(`model_only`)과 B 비밀키 규칙 결합(`model_plus_same_rules`) 모드 분리 (규칙 효과를 파인튜닝 성과로 전용 금지).
- **실험 기록 및 교체 판정 게이트**:
  - [experiment-plan.json](configs/experiment-plan.json) 스키마를 엄격히 검증하는 [records.py](evaluation/records.py) 구현.
  - 동일 평가 조건 검증, %p 델타 계산 및 PRD §11 교체 기준(재현율/F1 유지, 오차단율 +1.0%p 이하, 5대 중요유형 퇴행 방지, B 런타임 수용)을 자동 판정하는 [compare.py](evaluation/compare.py) 구현.
- **독립 서비스 시험셋 300건 (B4)**:
  - 요청 150건 + 응답 150건 = 300건 초안 생성 ([service-test-v1.manifest.json](evaluation/service-test-v1.manifest.json)).
  - 정상 문서 100건, 5대 중요 유형 각 30개 이상(`KR_RRN` 39, `KR_BANK_ACCOUNT` 52, `CREDIT_CARD` 40, `KR_PHONE_NUMBER` 81, `EMAIL_ADDRESS` 77) 확보.
  - K-PII-Bench 원본과의 완전 텍스트 중복 0건, 8자 이상 엔터티 표면형 중복 0건 검증 완료.
  - 전수 인간 검토 전까지 `review_status: needs_review` 초안 상태 유지 (최종 시험셋이며 dev 튜닝 재사용 금지).
- **평가 CLI**: `score`, `check-record`, `compare`, `decide` 커맨드 제공 ([model/evaluation/README.md](evaluation/README.md)).

### 3. 작업 C: 후보 보충 데이터셋(`townboy/korean-pii-dataset`) 검증 (검증 완료, 공식 채택 보류)
- **리비전 및 라이선스 고정**: 리비전 `076419f...`, CC-BY-4.0 고정 ([candidate-townboy-lock.json](configs/candidate-townboy-lock.json)).
- **품질 감사**: 11,732건 원문 스팬 100% 무결성 검증 (오류 0건), K-PII-Bench 중복 0건.
- **라벨 매핑 분석**: 33개 라벨 중 확정 15개, 주의 13개, 미매핑 5개 분류.
- **결론**: 무손실 거절 정책 하에서 확정 15개 라벨만 적용 시 유효 train 문서는 1,780건에 불과하며, K-PII-Bench 분할 해결만으로도 20만 건 이상 확보 가능하므로 **공식 채택 보류(`candidate_not_adopted`)** 판정 ([보고서](reports/candidate-supplement-summary.md)).

### 4. 테스트 현황
- 단위/통합 테스트 **173개 전체 통과** (`pytest model/tests -q`). 로컬 CPU 환경에서 네트워크/GPU 없이 전수 통과.

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
| [configs/assets.lock.json](configs/assets.lock.json) | M0 및 K-PII-Bench 고정 자산 명세 |
| [configs/candidate-townboy-lock.json](configs/candidate-townboy-lock.json) | 후보 보충 데이터셋 파일 크기·SHA-256 및 CC-BY-4.0 명세 |
| [configs/experiment-plan.json](configs/experiment-plan.json) | 학습·평가 출발 설정, 교체 판정 게이트, 실험 기록 명세 |
| [segmentation/splitters.py](segmentation/splitters.py) | 한국어 단어 분할기 후보군 (`whitespace_default_v1`, `ko_script_suffix_v2`, `ko_atomic_suffix_v3`) |
| [segmentation/README.md](segmentation/README.md) | 분할 호환성 분석 및 B 파트 연동 규격 제안서 |
| [evaluation/README.md](evaluation/README.md) | 4단계 평가 시스템 구조, 지표 정의, CLI 가이드 |
| [evaluation/service-test-v1.manifest.json](evaluation/service-test-v1.manifest.json) | 독립 서비스 시험셋 300건 무결성·통계·중복 검증 명세 |
| [reports/segmentation-diagnosis-v2.json](reports/segmentation-diagnosis-v2.json) | K-PII-Bench 분할기 후보별 변환율/유형보존 진단 결과 (ko_atomic_suffix_v3 95.9% 달성) |
| [reports/candidate-supplement-summary.md](reports/candidate-supplement-summary.md) | 후보 보충 데이터셋 검증 결과 요약 보고서 (공식 제외) |
| [reports/m0-baseline-summary.md](reports/m0-baseline-summary.md) | **M0 GPU 베이스라인 실측 결과 보고서 (5단계 완료)** |
| [reports/m0-baseline-record.json](reports/m0-baseline-record.json) | 독립 300건 공식 베이스라인 검증 레코드 (F1 49.16%, Critical Recall 51.88%) |
| [to_codex.md](to_codex.md) | **Codex 인수인계 문서 (현재 진행 상황 및 6~8단계 실행 가이드)** |

---

## 단계별 진행 현황 및 남은 작업

1. [x] **서버 환경 확인 (5단계)**: NVIDIA L4 24GB (Driver 580.159, CUDA 13.0, py312), VRAM 21.6GB 여유 확인 및 SSH 키 인증 구성 완료.
2. [x] **M0 기준선(Baseline) 실측 (5단계)**: 독립 서비스 시험셋 300건 및 Dev 28,147건 전수 GPU 실측 완료 (`m0-baseline-record.json` 생성).
3. [ ] **B와 분할기 공동 채택**: `ko_atomic_suffix_v3`를 A/B 공동 분할기로 최종 합의.
4. [ ] **학습 표본 5,000건 추출 (6단계)**: 변환 성공 train 233,724건 중 분야·유형 분포를 고려하여 시드 42로 5,000건 표본 선정 및 해시 기록.
5. [ ] **M1 추가 학습 및 dev 임계값 선정 (6단계)**: batch 4~8, LR 1e-5~3e-5, 2~3 epoch GPU 파인튜닝 및 dev 임계값 탐색.
6. [ ] **최종 공정 비교 및 교체 판정 게이트 (7단계)**: M0 vs M1 교체 판정 게이트(`compare.py`) 실행.
7. [ ] **모델 패키지 구성 및 B 인계 (8단계)**: 모델 아티팩트 번들링 및 최종 핸드오프.
