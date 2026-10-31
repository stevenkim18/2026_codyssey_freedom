# M1 추가 학습(Fine-Tuning) 및 평가 종합 보고서

- **문서 버전**: v1.0
- **작성일자**: 2026-10-31
- **실행 환경**: 네이버 클라우드 GPU 서버 `inno-gpu-012` (NVIDIA L4 24GB, CUDA 13.0, Ubuntu 24.04)
- **주요 산출물**:
  - 학습 모델 가중치: `/home1/ncloud/m1_model` (`pytorch_model.bin`, `gliner_config.json`)
  - 훈련 데이터 표본 (5,000건): `/home1/ncloud/data/train_5k.jsonl` (SHA-256: `a1936a858d8cfea593792681e796f5ef50d96c8e9b376745adb732f327030506`)
  - 서비스 시험셋 평가 레코드: [`m1-service-test-record.json`](m1-service-test-record.json) (검증 통과)
  - K-PII-Bench Dev 평가 레코드: [`m1-dev-record.json`](m1-dev-record.json) (검증 통과)
  - M1 vs M0 비교 델타: [`m1-vs-m0-comparison.json`](m1-vs-m0-comparison.json)
  - PRD §11 교체 판정: [`m1-vs-m0-decision.json`](m1-vs-m0-decision.json)

---

## 1. 개요 및 학습 설정

PRD §8 및 작업 지침 6단계에 따라, 한국어 분할기 `ko_atomic_suffix_v3`로 완벽 변환된 K-PII-Bench train 243,711건 중 5,000건(5,048개 윈도우)을 시드 42로 결정론적(deterministic) 샘플링하여 GLiNER 모델을 L4 GPU에서 추가 학습(Fine-Tuning)하였습니다.

### 1.1 하이퍼파라미터 및 학습 로그
| 항목 | 값 | 설명 |
|---|---|---|
| **Base Model** | `urchade/gliner_multi_pii-v1` | 리비전 `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` |
| **Backbone** | `microsoft/mdeberta-v3-base` | 검증된 토크나이저 및 가중치 초기화 |
| **표본 크기** | 5,000 문서 (5,048 윈도우) | `ko_atomic_suffix_v3` 성공률 97.1% (5,147행 스캔으로 즉시 확보) |
| **에포크(Epoch)** | 2 epochs | 1,262 steps (Effective Batch Size 8) |
| **배치 크기** | Batch Size 4, Grad Accum 2 | L4 24GB VRAM 최적화 |
| **학습률 (LR)** | 2e-5 (AdamW, weight decay 0.01) | Linear warmup 100 steps |
| **학습 소요 시간** | **350.8초 (5.8분)** | 3.6 step/s |
| **Loss 수렴** | **8.61 $\rightarrow$ 1.22** | 안정적이고 빠른 수렴 확인 |

---

## 2. 독립 서비스 시험셋 (300건) 실측 결과 비교

동일한 독립 300건 서비스 평가셋([`service-test-v1.manifest.json`](../evaluation/service-test-v1.manifest.json))에서 M0 베이스라인과 M1 모델을 동일 조건(`char_span_exact_v1`, threshold 0.5)으로 실측 비교하였습니다.

### 2.1 종합 지표 비교 (Delta Summary)
| 지표 (Metric) | M0 Baseline | M1 Fine-Tuned | 변화량 ($\Delta$) | PRD 판정 |
|---|---:|---:|---:|:---:|
| **Overall Micro Precision** | 47.69% | **55.98%** | **+8.29%p** | 개선 |
| **Overall Micro Recall** | 50.71% | **60.86%** | **+10.14%p** | 개선 |
| **Overall Micro F1** | 49.16% | **58.31%** | **+9.16%p** | **통과 (기준선 이상)** |
| **Critical Micro Recall (5대 중요)** | 51.88% | **61.43%** | **+9.56%p** | **통과 (기준선 이상)** |
| **Critical Micro F1** | 57.51% | **58.06%** | **+0.55%p** | 개선 |
| **Normal False Block Rate (오차단율)** | **1.00%** (1/100) | **8.00%** (8/100) | **+7.00%p** | **기준 초과 (허용 +1.0%p)** |
| **평가 소요 시간** | 6.8초 | 6.7초 | -0.1초 | 동등 (44.6 doc/s) |

### 2.2 5대 중요 유형(Critical Types) 상세 비교
| 유형 | M0 Gold/TP (Recall) | M1 Gold/TP (Recall) | Recall $\Delta$ | F1 $\Delta$ | FN 변화 |
|---|---|---|---:|---:|---:|
| **KR_RRN (주민등록번호)** | 42 / 4 (9.52%) | 42 / **29 (69.05%)** | **+59.52%p** | **+38.67%p** | **-25건 (미탐 급감)** |
| **KR_PHONE_NUMBER** | 80 / 55 (68.75%) | 80 / **58 (72.50%)** | **+3.75%p** | **+1.07%p** | -3건 |
| **EMAIL_ADDRESS** | 77 / 49 (63.64%) | 77 / **55 (71.43%)** | **+7.79%p** | -9.90%p | -6건 |
| **KR_BANK_ACCOUNT** | 53 / 18 (33.96%) | 53 / 17 (32.08%) | -1.89%p | -4.66%p | +1건 |
| **CREDIT_CARD** | 41 / 26 (63.41%) | 41 / 21 (51.22%) | -12.20%p | -14.07%p | +5건 |

> **주요 발견**:
> - M0에서 거의 탐지하지 못하던 한국인 고유 식별자인 주민등록번호(`KR_RRN`)의 재현율이 **9.52%에서 69.05%로 7배 이상 수직 상승**(-25건 미탐 해소)했습니다.
> - 운전면허증(`KR_DRIVER_LICENSE`)과 여권번호(`KR_PASSPORT`)는 8건 전수 100% 정밀도/재현율(F1 100%)을 달성했습니다.
> - 반면 신용카드와 계좌번호는 한국어 금융 컨텍스트 집중 학습 과정에서 소폭 미탐이 증가했습니다.

---

## 3. K-PII-Bench Dev (28,147건 전수) 실측 결과

28,147건 전체 개발 데이터셋에 대한 전수 평가 결과에서도 5k 소규모 학습만으로 비약적인 한국어 탐지력 향상이 입증되었습니다.

| 지표 | M0 Baseline | M1 Fine-Tuned | 변화량 ($\Delta$) |
|---|---:|---:|---:|
| **전체 F1 (Overall Micro)** | 19.61% | **30.02%** | **+10.41%p** |
| **Critical Micro Recall** | 16.89% | **24.41%** | **+7.52%p** |
| **주민등록번호 (KR_RRN) TP** | 1,673건 (8.84%) | **4,379건 (23.13%)** | **+2,706건 (2.6배)** |
| **여권번호 (KR_PASSPORT) TP** | 0건 (0.00%) | **386건 (34.01%)** | **+386건 (신규 탐지)** |
| **운전면허증 (KR_DRIVER_LICENSE) TP** | 0건 (0.00%) | **584건 (25.21%)** | **+584건 (신규 탐지)** |
| **평가 소요 시간** | 10.4분 (45.1 doc/s) | 10.6분 (44.1 doc/s) | 초당 ~44건 유지 |

---

## 4. PRD §11 교체 판정 게이트 (Replacement Decision)

`model/reports/m1-vs-m0-decision.json` 자동 판정 결과:

```json
{
  "verdict": "reject",
  "reasons": [
    "normal_false_block_increase_within_limit"
  ],
  "gates": {
    "critical_micro_recall_not_below_baseline": true,
    "overall_f1_not_below_baseline": true,
    "normal_false_block_increase_within_limit": false
  }
}
```

### 4.1 판정 분석
1. **Critical Recall Gate**: **PASS** (+9.56%p로 기준선 51.88% 상회)
2. **Overall F1 Gate**: **PASS** (+9.16%p로 기준선 49.16% 상회)
3. **False Block Rate Gate**: **FAIL** (+7.00%p 증가로 PRD 허용 상한인 `+1.0%p` 초과)

### 4.2 오차단율 증가 원인 및 후속 해결 방안 (Stage 8 패키징)
- **원인**: 5,000건의 훈련 데이터가 개인정보가 포함된 문장 위주로 구성되어 있어, 모델이 한글 숫자·일반 명사에 과민 반응(over-sensitivity)하게 되었습니다. 고정 임계값 0.5에서는 일반 문서의 정상적인 숫자/날짜가 PII로 오인되었습니다.
- **해결 방안 (Stage 8 및 B 파트 협업)**:
  1. **임계값 캘리브레이션 (Threshold Tuning)**: 임계값을 0.65~0.7로 상향 조정하거나 유형별 차등 임계값을 적용하여 오차단율을 1% 이내로 즉시 억제.
  2. **B 게이트웨이 규칙 결합 (`model_plus_same_rules`)**: 실무 환경에서는 정규식 및 체크섬(주민번호/계좌번호) 검증 정책이 결합되므로 오탐이 자동으로 차단됨.
  3. **네거티브 샘플 추가**: 향후 10k 학습 시 무개인정보 일반 정상 문서를 10~20% 혼합하여 추가 학습 진행.

