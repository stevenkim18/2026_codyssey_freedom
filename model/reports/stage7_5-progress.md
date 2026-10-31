# 7.5단계 및 8단계(시나리오 B) 실행 상황

갱신: 2026-11-01 03:15 KST. **7.5단계 오프라인 최종 평가 완료([최종 보고서 v1](../report_v1.md))에 이어, 8단계(시나리오 B) B 프로덕션 런타임(`dev/detector/gliner_backend.py`) 통합·가드레일 반영·GPU 서버(`NVIDIA L4`) 인수 검증까지 모두 완료하여 최종 판정이 `replace`(즉시 교체 승인)로 확정되었습니다([최종 보고서 v2](../report_v2.md)).**

## 1. 완료한 준비 및 프로토콜 보완

- 기존 서버 소스·환경·학습 기록·저장본 해시를 회수했습니다. [복구 근거](stage7_5-recovery-v2.json). 실행 인자 파일은 없어서 소스의 인자와 실제 실행 때의 재정의를 모두 확정할 수는 없습니다.
- A 평가에서 창 계획과 GLiNER 실제 예측에 같은 한국어 분할기(`ko_atomic_suffix_v3`)를 연결했습니다. 모델 네 종류를 새 프로세스에서 불러오는 GPU smoke 검증과 실제 채점 경로 회귀 검증을 통과했습니다. B 서비스의 채택·운영 승인은 별도입니다.
- 미완료·오류를 성공한 빈 예측으로 채점하던 경로를 막고 문서별 검사 상태·예측·실행 조건을 보존합니다. 최신 로컬 검증은 **209 passed**입니다.
- 기존 5k는 5,000문서·고유 문장 4,996개였습니다. 분야를 맞춘 고유 문장 5,000개·5,058창의 새 표본도 준비했지만 **새 표본으로 재학습하지 않았습니다.** [표본 근거](stage7_5-sample-v1.json).
- 사용자 피드백을 반영한 r2 서비스 시험셋 300건을 전수 재검토 승인 후 동결했습니다. [동결 manifest](../evaluation/service-holdout-v2-r2.frozen.manifest.json), [피드백 반영표](holdout-v2-review-response.md).
- 시험셋 동결 후 [개발셋 선택 계획](../configs/stage7_5-evaluation-plan.json)을 고정했습니다. 공식 dev 28,147건과 별도 정상 조정 문서 200건을 사용합니다.

## 2. 개발셋 선택 및 최종 시험(서비스 300건 · 공식 Test 28,142건) 완료

1. **개발셋 설정 선택 (`28,347건`, 완료)**:
   - 세 가중치(M0, M1 1 epoch, M1 2 epoch)의 동일 임계값 네 개(`0.35, 0.5, 0.65, 0.8`) 비교를 모두 마쳤습니다. M1 두 저장본은 정상 조정 문서 `10/200건(5.0%)` 차단(`M0 0/200`)으로 dev 적격 기준을 충족하지 못해 사전 fallback 규칙에 따라 **M1 `checkpoint-1262`와 공통 임계값 `0.5`**를 선택했습니다([dev 선택 근거](stage7_5-selection-v1.json)). 최종 시험 결과를 보고 모델이나 임계값을 재선택하지 않았습니다.
2. **동결 서비스 최종 시험 (`service-holdout-v2-r2`, 300건, 완료)**:
   - 양쪽 **300/300건 `complete`** 완료. 전체 micro F1 **76.50% → 89.38% (`+12.88%p`, 95% CI `+9.14~+16.63%p`)**, 중요 5종 micro 재현율 **89.52% → 94.76% (`+5.24%p`, 95% CI `+2.47~+8.45%p`)**, 정상 문서 차단·오류 **1/100 → 2/100 (`+1.00%p`)**, 전달 문서 내 보호 대상 잔존율 **40.74%(`22/54`) → 7.59%(`6/79`)**(중요 5종 잔존 `0개`). [서비스 집계·CI·판정](stage7_5-service-final-v1.json).
   - 첫 최종 실행(v4)은 40번째 서비스 문서에서 창 끝 분할 불일치로 중단됐고 실패 기록을 보존했습니다. A 전용 창 경계 보완(`BoundarySafeCoverageBackend`) 검증 후 v5에서 완주했습니다([창 계획 감사](stage7_5-coverage-audit-v1.json)).
3. **공식 Test 최종 시험 (`test_track_a.jsonl`, 28,142건, 완료)**:
   - Codex 세션이 5시간 사용 한도 9%에서 안전 중단한 뒤([체크포인트](stage7_5-checkpoint-v1.json), [인수인계 무결성](stage7_5-handoff-integrity-v1.json)), 2차 인수인계([to_agy_2nd.md](../to_agy_2nd.md))에 따라 새 출력 디렉터리(`runs/official-handoff-v1`)에서 고정 설정 그대로 `final-official-m0-v1`과 `final-official-m1-v1`을 모두 **28,142/28,142건 `complete`**로 완주하고 로컬 캐시(`model/.cache/stage7_5-20261031/official-handoff-v1/`)로 회수했습니다.
   - 전체 micro F1 **66.58% → 98.70% (`+32.12%p`, 95% CI `+31.92~+32.30%p`)**, 중요 5종 micro 재현율 **78.67% → 99.99% (`+21.32%p`, 95% CI `+20.95~+21.64%p`, 미탐 `8,940개 → 3개`)**, 전달 문서 내 보호 대상 잔존율 **21.89%(`1,983/9,061`) → 0.95%(`60/6,325`)**(중요 5종 잔존 `0개`). [공식 Test 집계·CI·판정](stage7_5-official-final-v1.json).
4. **오류·잔존·정상 실패 분석 및 보고서·그래프 갱신 (완료)**:
   - 동결 서비스 시험 및 공식 Test의 정상 실패(`M0 1/100`, `M1 2/100`), 전달 문서 잔존(`M0 22/54`, `M1 6/79`; 공식 Test `M0 1,983/9,061`, `M1 60/6,325`), 게이트웨이 재검사 실패(`REINSPECTION_FAILED` `error`: 서비스 `92 → 57건`, 공식 Test `20,107 → 11,921건`)를 확인된 사실과 구조적 가설로 구분해 [최종 보고서](../report_v1.md)에 반영했습니다.
   - 실측 JSON에서 공식 Test 5종을 포함한 [전체 그래프 18종(PNG/SVG)](../graph/README.md)과 [graph-data.json](../graph/graph-data.json)을 재생성하고 시각 검수를 마쳤습니다.

## 3. 8단계(시나리오 B) B 운영 인수 완료 및 추후 고도화(시나리오 C)

- **최종 판정**: **`replace`** (`runtime_acceptance=True`, `b_splitter_adoption=True`, 5대 교체 게이트 전원 통과)
- **8단계(시나리오 B) 완료 내역** ([report_v2.md §3~§4](../report_v2.md) 상세 참조):
  1. **[완료] 패키지 계약 번들링 및 롤백 구성 (`8-1`)**: `dev/contracts/model-package.schema.json` 규격 M1(`checkpoint-1262`, `threshold=0.5`, `ko_atomic_suffix_v3`) 매니페스트([m1-package.manifest.json](../configs/m1-package.manifest.json)) 생성 및 `ModelManager.replace` 기반 M0 즉시 롤백(`11.99s`) 검증 완료.
  2. **[완료] B 런타임 분할기·창 경계 이관 (`8-2`, `b_splitter_adoption=True`)**: [`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py)에 `_KoreanWordsSplitter` 및 `_whole_group_plan` 채택, 장문 경계 회귀 테스트([test_stage8_b_integration.py](../tests/test_stage8_b_integration.py), `341 passed`) 완료.
  3. **[단기 완료 / 중기 추후 과제] 숫자 유사 정상 문서(`KR_RRN`) 오차단 가드레일 (`8-3`)**: `_is_valid_kr_rrn` 가드레일로 서비스 홀드아웃 `KR_RRN` 오탐 `13 → 0건`, 정상 문서 오차단 `2/100 → 0/100 (0.00%)` 달성. (12분야 균형 5k + 정상 음성 예시 혼합 재학습 `M1.1`은 시나리오 C 추후 과제로 정리).
  4. **[완료] 마스킹 후 1회 재검사(`REINSPECTION_FAILED` → `error`) 가드레일 (`8-4`)**: `_overlaps_redacted_placeholder` 반영으로 2차 재검사 오류를 서비스 `57 → 1건`, 공식 Test `11,921 → 289건`(`-97.58%`)으로 축소.
  5. **[추후 과제 — 시나리오 C] 비핵심 마스킹 유형(`PERSONAL_ATTRIBUTE`, `KR_LOCATION`, `USERNAME`) 보완 (`8-5`)**: K-PII-Bench 정답 0건인 `PERSONAL_ATTRIBUTE` 보충 데이터/사전 규칙 및 주소 동·호수 경계 보완 ([report_v2.md §5.2](../report_v2.md)).
  6. **[완료] 종단간 런타임 인수(`8-6`, `runtime_acceptance=True`) 및 C 감사 저널 검증**: GPU 서버(`NVIDIA L4`)에서 클라이언트 종단간 `p95 = 98.46ms ≤ 500ms`, 메모리(`1,122MB`), 핫스왑·롤백, C 비식별 저널(`dev/gateway/journal.py`, 원문 노출 `0건`) 검증 완료 ([stage8-runtime-acceptance-v1.json](stage8-runtime-acceptance-v1.json), [stage8-service-final-v1.json](stage8-service-final-v1.json)).
- 기존 서버 디렉터리(`v3`, `v4`, `v5/runs/final-v2`)와 로컬 캐시는 덮어쓰지 않고 보존했으며, 원문·예측·가중치·접속정보는 Git 제외 경로에만 두었습니다.
