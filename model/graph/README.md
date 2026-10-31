# 그래프 재현 및 해석

실측 JSON으로 작성합니다. 새 표본은 학습 전이고 dev는 설정 선택용입니다. 최종 시험 결과를 보고 설정을 바꾸지 않습니다. 원문·문서 ID·키는 그래프에 넣지 않습니다.

실행: `model/.cache/conversion-venv/bin/python -m model.evaluation.build_graphs --dev-root <dev-run-directory> [--final-root <final-run-directory>]`

Matplotlib 3.10.0, NumPy 2.5.4. PNG와 글꼴을 경로로 변환한 SVG를 함께 제공합니다. 입력 SHA-256과 수치: [graph-data.json](graph-data.json).

| 그림 | PNG | SVG |
|---|---|---|
| 원본 trainer_state 기록 — 성공한 optimizer update 수와 같다고 단정하지 않음 | [PNG](training-history.png) | [SVG](training-history.svg) |
| 학습에 사용한 기존 표본과 새로 준비한 표본: 분야 분포 | [PNG](sample-domains.png) | [SVG](sample-domains.svg) |
| 표본 유형별 gold 개수 — 새 표본은 아직 학습에 사용하지 않음 | [PNG](sample-labels.png) | [SVG](sample-labels.svg) |
| Dev 설정 선택 자료 — 최종 시험 결과가 아님 | [PNG](dev-thresholds.png) | [SVG](dev-thresholds.svg) |
| service 최종 시험: 전체 엔터티 성능 | [PNG](service-overall.png) | [SVG](service-overall.svg) |
| service 유형별 성능 — gold 0인 유형은 N/A 표시 | [PNG](service-per-type.png) | [SVG](service-per-type.svg) |
| service 실제 B 오프라인 정책 경로 결과: 1회 재검사 포함 | [PNG](service-actions.png) | [SVG](service-actions.svg) |
| 서버 내부 측정: 네트워크 제외, B 클라이언트 p95 인수와 별개 | [PNG](service-latency.png) | [SVG](service-latency.svg) |
| service 정상 문서 실패율과 Wilson 95% 구간 — 분모 0이면 평가 불가 | [PNG](service-normal-failure.png) | [SVG](service-normal-failure.svg) |
| 차단·오류 문서는 분모에서 제외 — allow/mask 전달 문서의 보호 대상만 계산 | [PNG](service-residual.png) | [SVG](service-residual.svg) |
| official 최종 시험: 전체 엔터티 성능 | [PNG](official-overall.png) | [SVG](official-overall.svg) |
| official 유형별 성능 — gold 0인 유형은 N/A 표시 | [PNG](official-per-type.png) | [SVG](official-per-type.svg) |
| official 실제 B 오프라인 정책 경로 결과: 1회 재검사 포함 | [PNG](official-actions.png) | [SVG](official-actions.svg) |
| 서버 내부 측정: 네트워크 제외, B 클라이언트 p95 인수와 별개 | [PNG](official-latency.png) | [SVG](official-latency.svg) |
| official 정상 문서 실패율과 Wilson 95% 구간 — 분모 0이면 평가 불가 | [PNG](official-normal-failure.png) | [SVG](official-normal-failure.svg) |
| 차단·오류 문서는 분모에서 제외 — allow/mask 전달 문서의 보호 대상만 계산 | [PNG](official-residual.png) | [SVG](official-residual.svg) |
| 동결 서비스 300건: 문서 짝지은 변화량 구간 — 정상 실패 증가는 낮을수록 좋음 | [PNG](service-paired-deltas.png) | [SVG](service-paired-deltas.svg) |
| 공식 Test 28,142건: 문서 짝지은 변화량 구간 — 정상 문서 없음(N/A) | [PNG](official-paired-deltas.png) | [SVG](official-paired-deltas.svg) |
