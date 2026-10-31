# Hand-off to Codex (Track A: 모델 학습 및 평가)

본 문서는 **Antigravity (Gemini 3.8 Flash)**에서 작업한 내용을 **Codex** 또는 후속 작업자가 언제든지 즉시 인계받아 이어서 진행할 수 있도록 정리한 인수인계서입니다.

- **현재 브랜치**: `model`
- **현재 진행 상태**: **1~7단계 완료 (GPU M1 파인튜닝, Dev 전수 평가, PRD §11 교체 판정 100% 실측 완료)** $\rightarrow$ **8단계 (모델 패키지 조립 및 B 파트 핸드오프) 착수 대기**
- **로컬 테스트 현황**: 173개 단위/통합 테스트 전원 통과 (`173 passed in 0.23s`)
- **참조 문서**: 메인 가이드 [`model_README.md`](model_README.md), 전체 작업 흐름 [`process.md`](process.md), M1 종합 보고서 [`reports/m1-training-summary.md`](reports/m1-training-summary.md)

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

### 1.5 작업 E (6단계): GPU M1 5k 파인튜닝 완료
- 변환 성공한 train 문서 중 5,000건(5,048 윈도우, 시드 42)을 추출하여 L4 GPU에서 2 에포크 학습 완료 (Loss 8.61 $\rightarrow$ 1.22).
- 모델 가중치 및 config: GPU 서버 `/home1/ncloud/m1_model` (497MB `pytorch_model.bin`, `gliner_config.json`).
- 소요 시간: 350.8초 (5.8분).

### 1.6 작업 F (7단계): M0 vs M1 공정 비교 및 PRD §11 교체 판정
- **독립 서비스 평가셋 (300건)**:
  - Overall F1: 49.16% $\rightarrow$ **58.31% (+9.16%p)**
  - Critical Recall: 51.88% $\rightarrow$ **61.43% (+9.56%p)**
  - 주민등록번호(KR_RRN) Recall: 9.52% $\rightarrow$ **69.05% (+59.52%p)** (미탐 25건 감소)
  - 운전면허증/여권번호: 100% 정밀도 및 재현율
  - 정상문서 오차단율: 1.00% (1/100) $\rightarrow$ **8.00% (8/100) (+7.00%p)**
- **K-PII-Bench Dev (28,147건 전수)**:
  - Overall F1: 19.61% $\rightarrow$ **30.02% (+10.41%p)**
  - Critical Recall: 16.89% $\rightarrow$ **24.41% (+7.52%p)**
  - KR_RRN TP: 1,673건 $\rightarrow$ **4,379건 (2.6배)**
- **교체 판정 (`model/reports/m1-vs-m0-decision.json`)**:
  - `verdict`: **`reject`** (F1 및 Critical Recall은 크게 통과했으나, 오차단율 증가(+7.0%p)가 허용 한도 +1.0%p를 초과).
- **상세 보고서**: [`model/reports/m1-training-summary.md`](reports/m1-training-summary.md)

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

### 2.3 서버 내 작업 디렉터리 및 모델 위치
- **작업 경로**: `/home1/ncloud/work_model`
- **Python 인터프리터**: `/home1/ncloud/miniforge3/envs/py312/bin/python`
- **M0 패키지 위치**: `/home1/ncloud/b-gpu-trial-3c76ce4d6f8a4525a2f4bb3072ccd671/models/49a065fb3f08ca1291b56c514ec0dd6f11d906890d2d60d1d8c79b22800775c0`
- **M1 모델 가중치 위치**: `/home1/ncloud/m1_model`
- **데이터셋 위치**: `/home1/ncloud/data/k-pii-bench/` (`train.jsonl`, `dev.jsonl`, `train_5k.jsonl`)

---

## 3. 후속 작업: 8단계 (모델 패키지 구성 및 B 파트 인계)

### 3.1 목표
B 파트의 [model-package.schema.json](../dev/contracts/model-package.schema.json) 규격에 맞추어 M1 모델 패키지를 패키징하고, 오차단율을 1% 이하로 통제하기 위한 임계값 프로파일과 함께 B 파트에 공식 인계(Hand-off)합니다.

### 3.2 단계별 실행 계획
1. **임계값 캘리브레이션 프로파일 생성**:
   - 오차단율 8% $\rightarrow$ 1% 이하 억제를 위해 임계값을 상향(예: 0.65)하거나 유형별 차등 임계값(`configs/thresholds-calibrated.json`) 구성.
   - B 게이트웨이 정규식/비밀키 룰(`model_plus_same_rules`) 결합 시 오탐 방어 효과 명시.
2. **모델 패키지 디렉터리 번들링**:
   - 가중치: `pytorch_model.bin`
   - 모델 설정: `gliner_config.json`
   - 토크나이저: `microsoft/mdeberta-v3-base` (52바이트 config, spm.model)
   - 패키지 메타데이터: `package.json`, SHA-256 매니페스트
3. **B 파트 로더(`load_gliner`) 확장 협의**:
   - 현재 B의 `load_gliner`는 M0 고정 해시만 허용하도록 하드코딩되어 있으므로, M1 패키지 해시를 B의 검증기에 등록하는 PR/패치 제안.
4. **G2 인수 검증 테스트 실행**:
   - B 환경에서 M1 패키지 로드 및 추론 지연 시간(Latency < 30ms/doc), 메모리(VRAM < 4GB) 통합 검증.

---

## 4. 유용한 검증 명령어 모음

```bash
# 1. 로컬 단위/통합 테스트 (173개) 실행
model/.cache/conversion-venv/bin/python -m pytest model/tests -q

# 2. M1 평가 레코드 무결성 검증
python -m model.evaluation check-record model/reports/m1-service-test-record.json
python -m model.evaluation check-record model/reports/m1-dev-record.json

# 3. M0 vs M1 비교 및 PRD 판정 재현
python -m model.evaluation compare model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json
python -m model.evaluation decide model/reports/m1-service-test-record.json model/reports/m0-baseline-record.json --runtime-accepted
```

---

## 5. 유의사항 및 보안 수칙
- **보안**: `model/.secrets` 내 비밀번호, OTP, 키값은 절대 커밋하거나 로그/응답에 평문 출력하지 마십시오.
- **대용량 파일**: 모델 가중치 파일(`.bin`, `.pt`, `.safetensors`) 및 원본/중간 데이터셋(`.jsonl`)은 Git에 커밋하지 않습니다 (`.gitignore` 설정 완료).
- **테스트 무결성**: 코드 수정 시 반드시 `model/.cache/conversion-venv/bin/python -m pytest model/tests -q`로 173개 테스트 통과를 확인하십시오.

