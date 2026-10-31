# Hand-off to Codex (Track A: 모델 학습 및 평가)

본 문서는 **Antigravity (Gemini 3.8 Flash)**에서 작업한 내용을 **Codex** 또는 후속 작업자가 언제든지 즉시 인계받아 이어서 진행할 수 있도록 정리한 인수인계서입니다.

- **현재 브랜치**: `model`
- **현재 진행 상태**: **1~5단계 완료 (100% 실측 완료)** $\rightarrow$ **6단계 (M1 5,000건 파인튜닝) 착수 대기**
- **로컬 테스트 현황**: 173개 단위/통합 테스트 전원 통과 (`173 passed in 0.23s`)
- **참조 문서**: 메인 가이드 [`model_README.md`](model_README.md) (메인 디렉터리 README와 구분하기 위해 이름 변경됨), 전체 작업 흐름 [`process.md`](process.md)

---

## 1. 완료된 핵심 작업 요약 (1~5단계)

### 1.1 작업 A: 한국어 분할 호환성 해결 (`model/segmentation`)
- **문제**: 기존 whitespace 분할기 사용 시 조사 결합(예: `김가상에게`, `010-1234-5678로`)으로 인해 24.3만 건 중 **1.08%(2,615건)**만 변환 가능하고 7개 레이블이 소실되는 치명적 버그 존재.
- **해결안**: 원문 텍스트와 골드 오프셋을 일체 변경하지 않고, 한글 자모 유니코드 기반 조사 분리 및 원자적 URL/이메일 보존 알고리즘인 **`ko_atomic_suffix_v3`** 구현.
- **결과**: 변환 가능 문서 **233,724건 (95.9%)** 확보, 18종 전 레이블 손실 0건 보존 달성.
- **주요 파일**: [`model/segmentation/splitters.py`](segmentation/splitters.py), [`model/segmentation/README.md`](segmentation/README.md), [`model/reports/segmentation-diagnosis-v2.json`](reports/segmentation-diagnosis-v2.json)

### 1.2 작업 B: 4단계 평가 시스템 및 독립 300건 테스트셋 (`model/evaluation`)
- **평가 엔진**: 문자 스팬 엄밀 일치(`char_span_exact_v1`), Wilson 95% 신뢰구간, Bootstrap CI, division-by-zero 방어(`None`).
- **게이트웨이 연계**: B팀 정책(`dev/gateway/policy.py`) 어댑터 연계로 `false_block_rate` 및 잔존 마스킹 측정.
- **공정 비교 및 교체 판정 게이트**: PRD §11 명세(Critical Recall 저하 금지, F1 향상, 정상문서 오탐차단율 증가 $\le +1.0\%p$) 구현.
- **독립 서비스 평가셋**: 300건(정상음성 100건, 요청/응답 1:1, 핵심 5종 $\ge 30$건, 마크다운/표/JSON/장문 스트레스 케이스) 구축 완료.
- **주요 파일**: [`model/evaluation/metrics.py`](evaluation/metrics.py), [`model/evaluation/compare.py`](evaluation/compare.py), [`model/evaluation/service_testset/service-test-v1.draft.jsonl`](evaluation/service_testset/service-test-v1.draft.jsonl)

### 1.3 작업 C: 후보 보충 데이터셋 감사 (`model/data/candidate_supplement.py`)
- `townboy/korean-pii-dataset` 11,732건 전수 감사 결과, 확정 레이블 매핑 기준 고유 문서 수가 1,780건에 불과하여 PRD 5,000건 기준 미달로 공식 제외 판정(`candidate_not_adopted`).
- **주요 파일**: [`model/reports/candidate-supplement-summary.md`](reports/candidate-supplement-summary.md)

### 1.4 작업 D (5단계): GPU 서버 M0 베이스라인 실측 완료 (`model/reports`)
- 네이버 클라우드 GPU 서버(NVIDIA L4 24GB)에서 M0 모델(`urchade/gliner_multi_pii-v1`) 실측 완료:
  - **독립 서비스 평가셋 (300건)**: **Overall F1 = 49.16%**, **Critical Recall = 51.88%**, **False Block Rate = 1.00%** (소요시간 6.8초)
  - **K-PII-Bench Dev (28,147건 전수)**: **Overall F1 = 19.61%**, **Critical Recall = 16.89%** (소요시간 10.4분)
- **주요 파일**:
  - [`model/reports/m0-baseline-record.json`](reports/m0-baseline-record.json): 공식 베이스라인 레코드 (검증 통과)
  - [`model/reports/m0-dev-record.json`](reports/m0-dev-record.json): Dev 전수 실측 레코드
  - [`model/reports/m0-baseline-summary.md`](reports/m0-baseline-summary.md): 상세 실측 분석 보고서

---

## 2. 서버 접속 및 실행 환경 안내

### 2.1 하드웨어 및 네트워크
- **서버**: `inno-gpu-012` (Private IP: `10.1.2.17`, Port: 22, Ubuntu 24.04)
- **GPU**: NVIDIA L4 24GB (Driver 580.159.04, CUDA 13.0)
- **VPN**: Mac에서 네이버 클라우드 SSL-VPN 연결 필요.

### 2.2 간편 SSH 접속 스크립트 (암호 입력 불필요)
전용 SSH 키가 서버 `authorized_keys`에 등록되어 있어 아래 스크립트로 즉시 무암호 접속 및 파일 전송이 가능합니다:
```bash
# SSH 명령어 원격 실행
bash model/.cache/ssh_gpu.sh "<명령어>"

# SCP 파일 전송
bash model/.cache/scp_to_gpu.sh <로컬파일> ncloud@10.1.2.17:<원격경로>
```

### 2.3 서버 내 작업 디렉터리 및 Python 환경
- **작업 경로**: `/home1/ncloud/work_model`
- **Python 인터프리터**: `/home1/ncloud/miniforge3/envs/py312/bin/python`
  - PyTorch 2.10.0 (CUDA 지원), GLiNER 0.2.29, Transformers 4.57.3, Datasets 4.4.2
- **데이터셋 위치**: `/home1/ncloud/data/k-pii-bench/dev.jsonl` (SHA-256 검증 완료)
- **M0 패키지 위치**: `/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0`

---

## 3. 후속 작업: 6단계 (M1 5,000건 파인튜닝) 실행 가이드

### 3.1 목표
변환 성공한 233,724건의 한국어 훈련 데이터 중 **5,000건**을 샘플링하여 GLiNER 모델을 L4 GPU에서 1~3 epoch 추가 학습(Fine-tuning)하고, dev set을 이용해 레이블별 최적 임계값을 탐색합니다.

### 3.2 단계별 실행 순서
1. **훈련 데이터 5,000건 표본 추출**:
   - `model/data/conversion.py`와 `ko_atomic_suffix_v3`를 사용해 변환된 데이터 중, 시드 42로 5,000건을 샘플링하여 `train_5k.jsonl` 생성.
   - 샘플 명세 SHA-256을 `sample_manifest_sha256`에 기록.
2. **GPU 서버에서 파인튜닝 실행**:
   - Backbone: `microsoft/mdeberta-v3-base` (가중치는 M0의 `pytorch_model.bin`에서 출발)
   - 배치 크기: 4 ~ 8 (L4 24GB VRAM 활용)
   - 학습률: 1e-5 ~ 3e-5
   - 에포크: 2 ~ 3 epoch
3. **Dev 기반 임계값 튜닝**:
   - 각 에포크별 체크포인트에 대해 dev set 28,147건(또는 dev 대표 샘플)으로 F1 및 Critical Recall을 평가하여 유형별 최적 임계값 도출.
4. **산출물**:
   - `m1-weights/` (학습된 가중치 및 config)
   - `model/reports/m1-dev-record.json`

---

## 4. 후속 작업: 7단계 (M0 vs M1 공정 비교 및 교체 판정)

M1 학습이 완료되면 이미 완비된 평가 CLI를 통해 즉시 판정할 수 있습니다:

```bash
# 1. 수치 비교 (델타 및 신뢰구간)
python -m model.evaluation compare model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json

# 2. PRD §11 교체 판정 게이트 실행
python -m model.evaluation decide model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json --runtime-accepted
```

### 교체 판정 통과 기준 (PRD §11)
- [ ] 핵심 5종 Micro Recall $\ge 51.88\%$ (베이스라인 이상)
- [ ] 전체 Micro F1 $\ge 49.16\%$ (베이스라인 이상)
- [ ] 정상문서 오탐 차단율 증가 $\le +1.0\%p$ (즉, $\le 2.00\%$)
- [ ] 미해결 치명적 퇴행(Unresolved Critical Regression) 0건
- [ ] B팀 런타임 메모리/지연시간 수락

---

## 5. 유의사항 및 보안 수칙
- **보안**: `model/.secrets` 내 비밀번호, OTP, 키값은 절대 커밋하거나 로그/응답에 평문 출력하지 마십시오.
- **대용량 파일**: 모델 가중치 파일(`.bin`, `.pt`, `.safetensors`) 및 원본/중간 데이터셋(`.jsonl`)은 Git에 커밋하지 않습니다 (`.gitignore` 설정 완료).
- **테스트 무결성**: 코드 수정 시 반드시 `model/.cache/conversion-venv/bin/python -m pytest model/tests -q`로 173개 테스트 통과를 확인하십시오.

