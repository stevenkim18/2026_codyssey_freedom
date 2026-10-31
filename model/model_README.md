# A 파트: 한국어 개인정보 탐지 모델 학습·평가

> **7.5단계 오프라인 평가 및 보고서 완료(2026-11-01):** [2차 인수인계서](to_agy_2nd.md)에 따라 동결 서비스 시험(300건)과 공식 Test(`test_track_a`, 28,142건)의 M0·M1 최종 평가, 문서 짝지은 95% 신뢰구간, 오류·잔존·정상 실패 원인 분류, [최종 보고서 v1](report_v1.md) 및 [전체 그래프 18종](graph/README.md) 작성을 모두 완료했습니다.
> - **동결 서비스 시험(300건)**: 전체 F1 `76.50% → 89.38%`(`+12.88%p`), 중요 5종 재현율 `89.52% → 94.76%`(`+5.24%p`), 정상 차단·오류 `1/100 → 2/100`(`+1.00%p`). ([stage7_5-service-final-v1.json](reports/stage7_5-service-final-v1.json))
> - **공식 Test(`test_track_a`, 28,142건)**: 전체 F1 `66.58% → 98.70%`(`+32.12%p`), 중요 5종 재현율 `78.67% → 99.99%`(`+21.32%p`, 미탐 `8,940 → 3`). ([stage7_5-official-final-v1.json](reports/stage7_5-official-final-v1.json))
> - **현재 최종 판정**: `pending_runtime_acceptance` (A 오프라인 수치 게이트 통과, 8단계 B 런타임·분할기·재검사 운영 인수 전까지 운영 기본 모델 M0 유지). 최신 요약은 [stage7_5-progress.md](reports/stage7_5-progress.md)와 [report_v1.md](report_v1.md)를 우선 확인하세요.

이 폴더는 기존 모델 M0를 한국어 데이터로 추가 학습해 후보 M1을 만들고 평가하는 A 작업 공간입니다. 쉬운 설명은 [process.md](process.md), 7.5단계 기준은 [7.5단계 계획](stage7_5.md), 최종 보고서는 [report_v1.md](report_v1.md)를 읽으세요.

## 현재 상태 — 2026-11-01

**7.5단계 오프라인 재검증·최종 시험(서비스 300건·공식 Test 28,142건)·보고서·그래프 작성이 모두 완료되었습니다. 최종 판정은 `pending_runtime_acceptance`이며, B 운영 인수 완료 전까지 운영 기본 모델은 M0를 유지합니다.**

기존 초기 실행(서비스 v1 미동결본) 기록과 7.5단계 보완 후 동결 최종 시험 결과를 명확히 구분합니다.

| 단계 | 확인된 산출물 | 남은 조건 |
|---|---|---|
| 1. 버전·출처 고정 | 모델/데이터/도구 기준, 라이선스 기록 | [stage7_5-recovery-v2.json](reports/stage7_5-recovery-v2.json)으로 GPU 환경·해시 연결 완료 |
| 2. 데이터 검증 | 원본 30만 건 구조·위치·중복 감사 | 기존 5k(고유 4,996) 확인 및 새 12분야 균형 5k([stage7_5-sample-v1.json](reports/stage7_5-sample-v1.json)) 준비 완료 |
| 3. 변환·분할 후보 | 엄밀 변환기, 한국어 분할 후보(`ko_atomic_suffix_v3`) | A 평가 경로 연동 완료; **B 운영 런타임 채택(`b_splitter_adoption`) 별도 대기** |
| 4. 평가 준비 | 채점기·게이트웨이 정책·1회 재검사, 동결 서비스 r2 300건 | 미완료 검사 차단 완료, 사람 전수 재검토 동결 완료([service-holdout-v2-r2.frozen.manifest.json](evaluation/service-holdout-v2-r2.frozen.manifest.json)) |
| 5. M0 측정 | Dev·동결 서비스·공식 Test M0 완주 결과 | 공통 분할(`ko_atomic_suffix_v3`) 및 `threshold=0.5` 기준선 확정 |
| 6. M1 학습·선택 | 서버 epoch 1·2 저장본 회수 및 Dev 임계값 탐색 | `checkpoint-1262` + `threshold=0.5` 사전 선택 고정([stage7_5-selection-v1.json](reports/stage7_5-selection-v1.json)) |
| 7. 비교·판정 | 초기 서비스 v1 비교(`reject`) 보존 | 7.5단계 동결 시험 결과로 갱신 |
| **7.5. 신뢰성 보완** | [최종 보고서](report_v1.md), [그래프 18종](graph/README.md), [서비스 최종](reports/stage7_5-service-final-v1.json), [공식 최종](reports/stage7_5-official-final-v1.json) | **A 오프라인 평가·보고서 완료 (`pending_runtime_acceptance`)** |
| 8. 인계 | B 패키지 계약 존재 | **B 런타임 메모리·p95 지연·분할기·재검사 정책 인수 대기** |

## 기존 결과는 어떻게 읽나요?

아래는 저장된 JSON의 **잠정 결과**입니다. 서비스 v1은 300건 전수 미검토·미동결이며 이미 결과를 확인했습니다. 최종 성능 증거로 재사용하지 않고 탐색/회귀 자료로 보존합니다.

| 지표 | M0 | M1 | 변화 |
|---|---:|---:|---:|
| 서비스 v1 전체 micro F1 | 49.16% | 58.31% | +9.16%p |
| 서비스 v1 중요 5종 micro recall | 51.88% | 61.43% | +9.56%p |
| 서비스 v1 중요 5종 micro F1 | 59.49% | 58.06% | **−1.43%p** |
| 서비스 v1 정상 오차단 | 1/100 | 8/100 | **+7%p, 허용 +1%p 초과** |
| dev 28,147건 전체 micro F1 | 19.61% | 30.02% | +10.41%p |

계좌·카드 재현율도 각각 −1.89%p·−12.20%p 하락했습니다. 합산 개선만으로 교체할 수 없습니다. 임계값 상향이나 비밀키 규칙 추가가 오차단을 자동 해결한다는 근거는 없습니다.

[7단계 검토](reports/stage7-review-v1.md)에서 확인한 핵심 문제는 학습/실제 추론 분할 불일치, 미완료 검사 무시, 시험 미동결, epoch별 dev 선택 부재, 학습 인자·일부 보고 수치 불일치입니다. 기존 173개 테스트 통과는 이 문제들의 해결 증거가 아닙니다.

## 완료된 7.5단계와 남은 8단계 할 일

### 7.5단계 완료 항목
- [x] 1. 서버의 실제 학습 설정·로그·표본 manifest·epoch 저장본·가중치 해시 확보 ([stage7_5-recovery-v2.json](reports/stage7_5-recovery-v2.json))
- [x] 2. 공통 분할(`ko_atomic_suffix_v3`)과 검사 상태 처리(`incomplete` 차단), 비교 조건·캐시·예측 보존 수정 및 회귀 검증 (`209 passed`)
- [x] 3. 기존 저장본부터 dev(28,347건)에서 평가하고 M0/M1에 같은 절차로 임계값 선택 (`checkpoint-1262`, `threshold=0.5`, [stage7_5-selection-v1.json](reports/stage7_5-selection-v1.json))
- [x] 4. 오차단·계좌·카드 퇴행 원인 확인 (공통 분할 결합만으로 계좌·카드 퇴행 해소, 숫자 유사 정상 문서 오차단 원인 규명)
- [x] 5. 새 서비스 holdout(`service-holdout-v2-r2`, 300건) 사람 검토·동결 후 공식 시험셋(`test_track_a`, 28,142건)과 함께 최종 평가 완료 ([stage7_5-service-final-v1.json](reports/stage7_5-service-final-v1.json), [stage7_5-official-final-v1.json](reports/stage7_5-official-final-v1.json))
- [x] 6. 판정(`pending_runtime_acceptance`)·실행 이력 확정 및 [`model/report_v1.md`](report_v1.md)와 [`model/graph/`](graph/README.md) 18종 작성 완료

### 8단계 남은 할 일 (B 파트 인계 및 운영 인수)
- [ ] 1. B 런타임(`dev/detector/gliner_backend.py`)에 `ko_atomic_suffix_v3` 분할기 및 경계 안전 창 처리(`BoundarySafeCoverageBackend`) 공동 채택 합의 및 반영 (`b_splitter_adoption`)
- [ ] 2. B 모델 패키지 계약(`dev/contracts/model-package.schema.json`)에 맞춘 M1(`checkpoint-1262`, `threshold=0.5`, 전처리 버전 `ko_atomic_suffix_v3`) 최종 패키지 매니페스트 및 해시 인계
- [ ] 3. B 서빙 환경에서 모델 로딩·메모리·클라이언트 종단간 지연(`p95 ≤ 500ms`)·마스킹 후 1회 재검사 실패(`REINSPECTION_FAILED`) 운영 정책 및 숫자 유사 코드(`KR_RRN`) 보조 검증 인수 확인 (`runtime_acceptance`)

체크리스트와 완료 증거는 [stage7_5.md](stage7_5.md)와 [report_v1.md](report_v1.md)가 기준입니다. B의 운영 인수는 별도이며 확인 전에 `runtime_acceptance=True`로 표시하지 않습니다.

## 어떤 버전을 사용하나요?

| 대상 | 이름·버전 | 고정 리비전 |
|---|---|---|
| 기존 모델 M0 | `urchade/gliner_multi_pii-v1` | `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` |
| 학습 데이터 | `woohyun212/k-pii-bench`, `v4-corrected` | `844f92f7beba98fa964f957ba324017e82451701` |
| backbone 설정·토크나이저 | `microsoft/mdeberta-v3-base` | `a0484667b22365f84929a935b5e50a51f71f159d` |
| GLiNER 코드 | `0.2.29` | `3e1723b49539d518f76d4d8282f1e05d8b6e74f0` |
| 보충 데이터 후보 | `townboy/korean-pii-dataset` (미채택) | `076419f07f7073b6b28cdfdb6fbd8b75e1278053` |

M1 레코드의 `m1-ko-atomic-5k-v1`은 이름이며 가중치 SHA-256이 아닙니다. 실제 체크포인트 확인값은 7.5단계에서 회수합니다.

## 파일 안내

| 파일 | 역할 |
|---|---|
| [process.md](process.md) | 처음 접하는 사람을 위한 작업 흐름 |
| [stage7_5.md](stage7_5.md) | 현재 보완 작업·완료 기준·담당 구분 |
| [to_codex.md](to_codex.md) | 현재 인계 정보와 재개 순서 |
| [to_agy.md](to_agy.md) | 3단계 직후 최초 인계 지침의 역사적 기록 |
| [reports/stage7-review-v1.md](reports/stage7-review-v1.md) | 발견 문제와 근거 |
| [reports/stage7-review-checks-v1.json](reports/stage7-review-checks-v1.json) | 검토 시점 검사 결과·입력 확인값 |
| [configs/assets.lock.json](configs/assets.lock.json) | 원본 자산 고정 기준 |
| [configs/experiment-plan.json](configs/experiment-plan.json) | 기존 계획·기록 스키마. 실행 상태 일부가 오래되어 후속 동기화 필요 |
| [data/README.md](data/README.md), [data/CONVERSION.md](data/CONVERSION.md) | 다운로드·감사·엄밀 변환 재현 |
| [segmentation/README.md](segmentation/README.md) | 분할 후보와 공통 연동 조건 |
| [reports/segmentation-diagnosis-v2.json](reports/segmentation-diagnosis-v2.json) | 후보별 변환 수·유형 보존. 탐지 성능이 아님 |
| [evaluation/README.md](evaluation/README.md) | 지표·정책·CLI와 현재 실행기 제약 |
| [evaluation/service-test-v1.manifest.json](evaluation/service-test-v1.manifest.json) | 서비스 v1 초안의 해시·구성·미검토 상태 |
| [reports/m0-baseline-summary.md](reports/m0-baseline-summary.md) | M0 초기 측정의 잠정 수치·제한 |
| [reports/m1-training-summary.md](reports/m1-training-summary.md) | M1 초기 실행 보고·정정 수치·제한 |
| [reports/m1-vs-m0-decision.json](reports/m1-vs-m0-decision.json) | 과거 레코드 기준 `reject` 판정 |
| [reports/data-audit-summary.md](reports/data-audit-summary.md) | 원본 감사 이력 |
| [reports/candidate-supplement-summary.md](reports/candidate-supplement-summary.md) | 보충 데이터 후보 미채택 근거 |
| [licenses/NOTICE.md](licenses/NOTICE.md) | 출처·라이선스·단계별 변경 기록 |

한국어 분할 후보는 train 243,711문서 중 236,900문서(97.21%) 변환, 고유 문장 233,724개를 기록했습니다. 고유 수를 전체 문서 수로 나눈 95.90%와 문서 변환율은 다릅니다. 유형별 정답은 일부 거절됐으므로 “18종 모두 남음”과 “모든 정답 보존”도 구분합니다.

## 로컬 확인

```bash
model/.cache/conversion-venv/bin/python -m pytest model/tests -q
```

직전 검토 결과는 **173 passed in 0.23s**입니다. 이 문서 개정에서 GPU 작업이나 코드 수정을 수행한 것은 아닙니다. 원문·모델 가중치·가상환경·접속정보는 Git 제외 경로에 보존합니다.
