# A 파트 인수인계 — 7.5단계 완료 및 8단계 B 인수 대기

> **7.5단계 오프라인 평가 및 보고서 완료(2026-11-01):** [2차 인수인계서](to_agy_2nd.md)에 따라 동결 서비스 시험(300건)과 공식 Test(`test_track_a`, 28,142건)의 M0·M1 최종 평가, 문서 짝지은 95% 신뢰구간, 오류·잔존·정상 실패 원인 분류, [최종 보고서 v1](report_v1.md) 및 [전체 그래프 18종](graph/README.md) 작성을 모두 완료했습니다. 현재 최종 판정은 `pending_runtime_acceptance`(A 오프라인 수치 게이트 통과, 8단계 B 운영 인수 전까지 운영 기본 모델 M0 유지)입니다. 최신 요약은 [stage7_5-progress.md](reports/stage7_5-progress.md), [서비스 최종 집계](reports/stage7_5-service-final-v1.json), [공식 Test 최종 집계](reports/stage7_5-official-final-v1.json)를 우선 확인하세요.

갱신일: 2026-11-01 · 기준 브랜치: `model` · 최초 검토 대상 실행 기록: `a1d9177aff01fcf6f3081aae4ebb737cf83dedfc`

**아래 본문은 7.5단계 착수 시점의 인수인계 기록입니다.** 이후 7.5단계 실행·최종 평가·보고서 작성이 모두 완료되었으며, 최신 결과는 [report_v1.md](report_v1.md)에 정리되어 있습니다.

먼저 [7.5단계 계획](stage7_5.md), [검토 근거](reports/stage7-review-v1.md), [쉬운 전체 흐름](process.md)을 읽으세요. 세부 체크리스트는 `stage7_5.md`가 기준입니다.

## 1. 이미 있는 자산과 재사용할 것

| 자산 | 상태·주의점 |
|---|---|
| 모델·데이터 고정 기준 | [assets.lock.json](configs/assets.lock.json). 원본 revision은 유지 |
| 원본 30만 건 감사 | [data-audit-v2.json](reports/data-audit-v2.json). 정상 문서 0건, dev/test의 PERSONAL_ATTRIBUTE gold 0건 |
| 엄밀 변환기·분할 후보 | [분할 제안서](segmentation/README.md). 후보 진단 통과와 실제 운영 채택을 구분 |
| 평가 지표·정책·비교 모듈 | [평가 안내](evaluation/README.md). 실행기 상태 처리·기록 보완 필요 |
| 서비스 v1 300건 | 전수 미검토·미동결. 이미 점수를 확인했으므로 탐색/회귀용으로 보존 |
| M0/M1 score·record JSON | 개수·비율 내부 일관성 확인. 최종 평가 승인과 구분 |
| M1/M0 비교·판정 | 기존 레코드로 비교 재현, `reject`. 오차단 외 계좌·카드 퇴행도 있음 |
| 보충 데이터 후보 | townboy는 미채택. 조용히 합치거나 기준 데이터를 교체하지 않음 |

기존 테스트 **173 passed in 0.23s**는 이전 검토 결과입니다. 새 작업에서는 필요한 회귀 검증을 추가하되 개수 자체를 완료 기준으로 삼지 않습니다.

## 2. 잠정 결과와 정정 사항

| 서비스 v1 지표 | M0 | M1 |
|---|---:|---:|
| 전체 micro F1 | 49.16% | 58.31% |
| 중요 5종 micro recall | 51.88% | 61.43% |
| 중요 5종 micro F1 | 59.49% | 58.06% |
| 정상 오차단 | 1/100 | 8/100 |

- 중요 유형 계좌·카드 재현율은 각각 −1.89%p·−12.20%p입니다. 미해결 퇴행이 있으면 교체 보류입니다.
- dev 주민번호 TP는 **1,354 → 4,379 (+3,025)**입니다. dev 운전면허증 TP는 **584 → 584**입니다.
- 서비스 v1에서 여권은 M0도 이미 F1 100%이며, 운전면허증은 F1 **69.57% → 100%**입니다. 각 유형 gold는 8개뿐입니다.
- 보고서의 batch 4·누적 2·LR 2e-5와 제출 코드의 batch 8·누적 1·LR 3e-5가 다릅니다. 실제 GPU 인자로 확정해야 합니다.
- 임계값 0.65~0.7 또는 규칙 결합으로 오차단 1% 이하를 보장하지 않습니다. 현재 PRD 기준은 M0 대비 증가 +1%p 이하입니다.

## 3. 재개 순서

1. **증거 회수:** 실행 당시 소스·환경·인자·표본·epoch 저장본·로그·가중치 해시부터 보존합니다. 현재 코드와 서버 코드가 다르면 차이를 기록합니다.
2. **실행기 수정:** 공통 분할을 GLiNER 실제 예측까지 연결하고 미완료/오류 상태를 보존합니다. 비교 ID·캐시 검증·문서별 예측 보존·B 인수 미확정 상태도 고칩니다.
3. **dev 선택:** 기존 저장본을 우선 평가하고 M0/M1에 같은 절차로 임계값을 선택합니다. 별도 정상 문서 조정 자료를 사용하며 최종 시험을 튜닝에 쓰지 않습니다.
4. **원인 분석:** 오차단과 계좌·카드 미탐 원인을 확인하고 필요시에만 표본 보완·재학습합니다. loss 감소만 보고 epoch를 늘리지 않습니다.
5. **최종 평가:** 새 서비스 holdout을 선택 과정 전에 사람 검토·동결하고, 선택 완료 후 공식 `test_track_a`와 함께 평가합니다. 모델 단독/동일 규칙 결합, 마스킹 잔존, 실패, CI를 보고합니다.
6. **판정·납품:** 신뢰할 수 있는 결과로 교체/보류/거절을 기록한 뒤 보고서·그래프를 만듭니다. 기준 미달이면 M0 유지로 마무리할 수 있습니다. M1 인계는 B 인수 조건을 따릅니다.

현재 M1 실행기를 그대로 재실행하면 서비스 시험부터 평가하고, 비교 조건 ID도 저장된 서비스 레코드와 다르게 생성합니다. 수정 전 스크립트를 새 공식 실험으로 사용하지 마세요. `runtime_acceptance=True` 고정 전달도 실제 B 승인 근거가 아닙니다.

## 4. 서버 자산 찾기 — 이전 실행자가 보고한 위치

아래는 접속·존재를 이번 문서 작업에서 확인한 값이 아니라 **회수할 후보 위치**입니다. 기존 파일을 덮어쓰지 말고 실제 존재·해시·실행 환경을 확인합니다.

| 항목 | 이전 보고 위치 |
|---|---|
| 작업 디렉터리 | `/home1/ncloud/work_model` |
| 실행 Python | `/home1/ncloud/miniforge3/envs/py312/bin/python` |
| M0 패키지 | `/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0` |
| 최종 M1 | `/home1/ncloud/m1_model` |
| 중간 저장본 | `/home1/ncloud/m1_checkpoints` (제출 코드 기준, 존재 미확인) |
| 원본 train/dev | `/home1/ncloud/data/k-pii-bench/` |
| 5k 원본·변환 표본 | `/home1/ncloud/data/train_5k.jsonl`, `/home1/ncloud/data/train_5k_gliner.jsonl` |

선임 팀원의 최초 `prd-a-py312` 환경 보고와 이후 실행자의 `py312` 사용 보고를 구분해 실제 환경을 확인합니다. GPU는 L4 24GB, CUDA 13.0으로 보고됐습니다. 로컬 Mac CPU 가상환경을 서버 CUDA 환경으로 간주하지 않습니다.

기존 로컬 접속 보조 파일은 `model/.cache/` 아래에 있으나 Git에 포함되지 않습니다. VPN 최대 접속 인원과 GPU 사용 시간을 팀원과 조율해야 합니다. 이 문서 정리는 서버 접속·파일 전송을 수행한 기록이 아닙니다. 자격증명·키 내용을 출력하거나 문서로 옮기지 않습니다.

## 5. 로컬 집계 검증 명령

저장소 루트에서 실행합니다. 아래 명령은 기존 JSON의 형식·집계·판정을 확인할 뿐, 실제 학습이나 시험 동결을 인증하지 않습니다.

```bash
model/.cache/conversion-venv/bin/python -m pytest model/tests -q

python3 -m model.evaluation check-record model/reports/m1-service-test-record.json
python3 -m model.evaluation check-record model/reports/m1-dev-record.json
python3 -m model.evaluation compare model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json
python3 -m model.evaluation decide model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json
```

B의 지연·메모리·통합 인수 증거가 없는 현재는 `--runtime-accepted`를 붙이지 않습니다. schema 검증 통과는 해시 진위·공정 선택·실행 완료를 자동 증명하지 않습니다.

## 6. 8단계로 넘어가는 조건 및 세부 체크리스트

[패키지 계약](../dev/contracts/model-package.schema.json)에 맞게 가중치·설정·토크나이저·라벨·임계값·공통 분할 규격·버전·해시·라이선스·평가 증거·회귀 사례를 연결합니다. M0 전용 로더의 제한은 B와 계약을 확장해 해결하며 우회하지 않습니다.

- [ ] **8-1. 패키지 계약 번들링 및 M0 롤백 구성**: M1 `checkpoint-1262`(`model.safetensors` SHA-256 `ed2546b8...`), `threshold=0.5`, `preprocessing.version="ko_atomic_suffix_v3"`, 창 설정(`384/64/12/512`) 매니페스트 패키징 및 M0(`49a065fb...`) 즉시 롤백 구성.
- [ ] **8-2. B 런타임 분할기 및 경계 안전 창 처리 채택 (`b_splitter_adoption`)**: `dev/detector/gliner_backend.py`에 `attach_words_splitter` 및 `BoundarySafeCoverageBackend` 통합, 40번째 장문 경계 회귀 테스트(`incomplete=0`) 반영.
- [ ] **8-3. 숫자 유사 정상 문서(`KR_RRN`) 오차단 가드레일 및 M1.1 보완**: 하이픈 자산코드·긴 정수열의 `KR_RRN` 오차단(`서비스 2/100`, `Dev 조정 10/200`) 완화를 위한 B 후처리/정책 단계 주민등록번호 형식·체크섬 보조 검증 또는 12분야 균형 5k([stage7_5-sample-v1.json](reports/stage7_5-sample-v1.json)) + 정상 음성 예시 혼합 재학습.
- [ ] **8-4. 마스킹 후 1회 재검사(`REINSPECTION_FAILED` → `error`) 정책 정교화**: `[MASKED]` 치환 후 2차 재검사 오류(`서비스 57/300`, `공식 Test 11,921/28,142`) 완화를 위해 기마스킹 구간 제외 또는 플레이스홀더 포맷 개선 협의.
- [ ] **8-5. 비핵심 마스킹 유형(`PERSONAL_ATTRIBUTE`, `KR_LOCATION`, `USERNAME`) 보완**: K-PII-Bench 정답 0건인 `PERSONAL_ATTRIBUTE`(서비스 잔존 6건 중 4건) 보충 데이터/사전 규칙 및 주소 동·호수 접미부 경계 보완.
- [ ] **8-6. 종단간 런타임 인수(`runtime_acceptance`) 및 C 저널·카나리 배포**: 지정 조건 클라이언트 측 `p95 ≤ 500ms/회`, 메모리, fail-closed 복구, C 비식별 감사 저널(`dev/gateway/journal.py`) 연동 및 Shadow/Canary 검증 후 `runtime_acceptance=True` 전환. (과거 인계서의 30ms·4GB는 확정된 PRD 기준으로 사용하지 않습니다.)

커밋·푸시는 별도 사용자 요청을 따릅니다. 기존 JSON·원본 자료·검토 스냅샷은 보존하고, 새 실험은 새 ID와 출력 경로로 기록합니다.
