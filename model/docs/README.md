# 📚 A 파트 모델·데이터·평가·B 런타임 통합 문서실 (`model/docs/`)

이 폴더(`model/docs/`)는 한국어 개인정보(PII) 탐지 모델 **M0 기준선 진단 → 한국어 조사 분리기(`ko_atomic_suffix_v3`) 설계 → M1 미세조정(`checkpoint-1262`) → 7.5단계 엄밀 재평가 → 8단계 B 프로덕션 런타임 통합 및 GPU 인수 검증(`verdict: "replace"`)**에 이르는 전체 과정 중 **사람(팀원, 리뷰어, 발표자, 운영자)이 읽어야 할 모든 핵심 문서를 번호순으로 총정리한 통합 문서실**입니다.

---

## 🗂️ 문서 읽기 순서 및 목차

| 번호 | 문서 파일 | 대상 독자 | 핵심 내용 요약 |
|:---:|---|---|---|
| **01** | **[01-final-report-v2.md](01-final-report-v2.md)**<br>*(원본: [../report_v2.md](../report_v2.md))* | **전원 필독**<br>(발표·최종 검수) | **Stage 8 프로덕션 통합 완료 최종 보고서 (`verdict: "replace"`)**.<br>- 동결 서비스 300건 F1 `76.50% → 90.76%`(`+14.26%p`), 정상 문서 오차단 `0/100 (0.00%)`<br>- 공식 Test 28,142건 F1 `66.58% → 98.70%`(`+32.12%p`), 2차 재검사 `error` `-97.58%` 감소(`11,921 → 289건`)<br>- `NVIDIA L4` GPU 종단간 지연 `p95 = 98.46ms <= 500ms`, 무중단 롤백·저널 무누출 검증 및 시나리오 C(추후 개발 과제) 로드맵 |
| **02** | **[02-process-and-guide.md](02-process-and-guide.md)**<br>*(원본: [../process.md](../process.md), [../model_README.md](../model_README.md))* | 신규 합류자·발표자·기획자 | **1단계~8단계 전체 작업 흐름 및 개념 안내서**.<br>- 스팬(span)·토크나이저·윈도우 분할·1회 마스킹 재검사 등 핵심 개념을 쉬운 예시로 설명하고 1~8단계 체크리스트 완료 현황을 정리 |
| **03** | **[03-troubleshooting-and-stage7_5-8.md](03-troubleshooting-and-stage7_5-8.md)**<br>*(원본: [../stage7_5.md](../stage7_5.md), [../reports/stage7_5-progress.md](../reports/stage7_5-progress.md))* | 엔지니어·아키텍트·코드 리뷰어 | **6대 핵심 트러블슈팅(Troubleshooting) 및 7.5~8단계 복구·통합 상세 기록**.<br>- ① 한국어 조사 결합 미탐 → ② 장문 경계 `incomplete` → ③ 초기 7단계 보고 폐기 및 게이트웨이 재검사 채점기 도입 → ④ 홀드아웃 누수 차단(`r2` 재검토 동결) → ⑤ 임계값 가설 기각 → ⑥ `KR_RRN` 숫자 유사 및 `[REDACTED]` 2차 재검사 가드레일 해결 과정 전수 수록 |
| **04** | **[04-data-and-segmentation.md](04-data-and-segmentation.md)**<br>*(원본: [../segmentation/README.md](../segmentation/README.md), [../data/](../data/README.md))* | 데이터·모델 엔지니어 | **30만 건 데이터 전수 감사, 엄밀 변환, 한국어 분리기(`ko_atomic_suffix_v3`) 기술 문서**.<br>- K-PII-Bench 300,000건 무결성 감사, 기본 공백 분할기에서 98.9%가 거절된 구조적 원인과 `ko_atomic_suffix_v3`(97.21% 보존) 알고리즘 상세, 보충 데이터(`townboy`) 미채택 근거 |
| **05** | **[05-evaluation-and-graphs.md](05-evaluation-and-graphs.md)**<br>*(원본: [../evaluation/README.md](../evaluation/README.md), [../graph/README.md](../graph/README.md))* | 평가·시각화·발표 담당자 | **평가 체계(`char_span_exact_v1` + `B-gateway-one-reinspection-v1`) 및 시각 그래프 21종 해설서**.<br>- 채점 프로토콜, 쌍체 부트스트랩 신뢰구간, 동결 홀드아웃 피드백 반영 내역(`holdout-v2-r2`) 및 **21종 그래프(PNG/SVG) 전체 목록과 해석 가이드** |
| **06** | **[06-pr-description.md](06-pr-description.md)**<br>*(원본: [../PR_DESCRIPTION.md](../PR_DESCRIPTION.md))* | Git / PR 리뷰어 | **`model` 브랜치 Pull Request (PR) 설명서**.<br>- 변경 파일 요약, 핵심 성과 수치표, 트러블슈팅 요약, 테스트 검증 명령(`341 passed`), 보안·무누출 체크리스트 |
| **07** | **[07-stage7_5-report-v1-archive.md](07-stage7_5-report-v1-archive.md)**<br>*(원본: [../report_v1.md](../report_v1.md))* | 이력 대조·감사용 | **7.5단계 오프라인 평가 완료 시점 보고서 (v1 보존본)**.<br>- 8단계 B 런타임 가드레일 통합 직전(`pending_runtime_acceptance`)의 오프라인 측정 기준선을 원형 그대로 보존하여 Stage 7.5 vs Stage 8 효과를 투명하게 대조 가능 |
| **08** | **[08-licenses-and-provenance.md](08-licenses-and-provenance.md)**<br>*(원본: [../licenses/NOTICE.md](../licenses/NOTICE.md))* | 배포·법무·컴플라이언스 | **외부 모델·데이터·코드 출처 및 오픈소스 라이선스 고지서**.<br>- `urchade/gliner_multi_pii-v1`(Apache-2.0), `mdeberta-v3-base`(MIT), `K-PII-Bench`(CC BY 4.0) 출처 및 1~8단계 변경 이력 고지 |

---

## 📊 핵심 성과 한눈에 보기 (M0 vs Stage 7.5 M1 vs Stage 8 M1)

| 구분 | 핵심 지표 | M0 (기존 모델) | M1 (7.5단계 오프라인) | **M1 (8단계 B런타임 통합 최종)** | **최종 개선폭 (`Stage 8 - M0`)** |
|---|---|---:|---:|---:|---:|
| **동결 서비스 홀드아웃** (`300건`) | 전체 Micro F1 | `76.50%` | `89.38%` | **`90.76%`** (`P 88.25%, R 93.41%`) | **`+14.26%p`** |
| | 중요 5종 Micro Recall | `89.52%` (미탐 `22`) | `94.76%` (미탐 `11`) | **`94.76%`** (미탐 **`11`**) | **`+5.24%p`** (미탐 `-50%`) |
| | `KR_RRN` Precision / Recall / F1 | `100.0% / 80.0% / 88.9%` | `75.0% / 97.5% / 84.8%` (`fp=13`) | **`100.0% / 97.5% / 98.7%`** (`fp=0`) | **오탐 `0건`, Recall `+17.50%p`** |
| | **정상 문서 오차단·오류율 (`100건`)** | `1/100` (`1.00%`) | `2/100` (`2.00%`) | **`0/100` (`0.00%`)** | **`-1.00%p`** |
| | 게이트웨이 `error` (재검사 실패) | `92 / 300건` | `57 / 300건` | **`1 / 300건`** (`mask 120건`) | **`-91건` (7.5단계 대비 `-56건`)** |
| **공식 Test 벤치마크** (`28,142건`) | 전체 Micro F1 | `66.58%` | `98.70%` | **`98.70%`** (`P 98.13%, R 99.27%`) | **`+32.12%p`** |
| | 중요 5종 Micro Recall | `78.67%` (미탐 `8,940`) | `99.99%` (미탐 `3`) | **`99.99%`** (미탐 **`3`**) | **`+21.32%p`** (미탐 `-8,937개`) |
| | 게이트웨이 `error` (재검사 실패) | `20,107 / 28,142건` | `11,921 / 28,142건` | **`289 / 28,142건`** (`mask 14,108건`) | **7.5단계 대비 `-11,632건` (`-97.58%`)** |
| | 전달 문서 내 보호 대상 잔존율 | `21.89%` (`1,983/9,061`) | `0.95%` (`60/6,325`) | **`0.25%` (`127/51,010`)** | **`-21.64%p`** |
| **GPU 프로덕션 인수** (`NVIDIA L4`) | 종단간 게이트웨이 지연 (`p50 / p95 / p99`) | — | — | **`28.47 / 98.46 / 194.20 ms`** | **`p95 <= 500ms` SLA 통과** |
| | 무중단 핫스왑 / 즉시 롤백 (`M0 ↔ M1`) | — | — | **교체 `10.16s` / 롤백 `11.99s`** | **`rollback_verified: true`** |
| | C 파트 감사 저널 원문 무누출 | — | — | **`raw_pii_leak_count = 0`** | **`zero_leak_verified: true`** |
| **PRD 교체 게이트 판정** | `replacement_decision` | 기준선 (Baseline) | `pending_runtime_acceptance` | **`replace` (5대 게이트 전원 `True`)** | **최종 프로덕션 교체 승인** |
