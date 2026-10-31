# A → B 모델 인수 준비 체크리스트

2026-10-31 기준 **B의 로컬 배포 전 준비 자료**다. 새 A/B 합의 계약이나 품질 승인서가 아니다.
현재 [B05](../BACKLOG.md)는 평가 산출물·인수 계약 합의 대기다. 이 문서는 서버 접속·설치·배포·모델 로딩을 지시하지 않는다.
기준은 [PRD R07·교체 기준](../../docs/AI_PII_Gateway_PRD_v1.md), [로컬 계약 설명](README.md),
[ModelPackage 구현](../detector/runtime.py), [M0 통합 기록](../detector/M0_INTEGRATION_NOTES.md)이다.

## 확인된 상태와 경계

| 항목 | 현재 확인 범위 | 남은 입력·판정 |
|---|---|---|
| A 브랜치 | `origin/model@fa88b1c032e592bbc9092b49671efb894b5c4f41`의 작은 메타데이터를 읽기 전용으로 확인한 스냅샷. [A README](https://github.com/stevenkim18/2026_codyssey_freedom/blob/fa88b1c032e592bbc9092b49671efb894b5c4f41/model/README.md)는 data-audit 단계라고 명시 | 이후 A 변경·현재 GPU 작업 상태는 이 스냅샷으로 판단하지 않음 |
| A 자산 잠금 | [assets.lock.json](https://github.com/stevenkim18/2026_codyssey_freedom/blob/fa88b1c032e592bbc9092b49671efb894b5c4f41/model/configs/assets.lock.json)의 원본 M0·backbone·GLiNER pin과 18 canonical 유형, codepoint/end-exclusive 규약은 B와 일치 | 라벨 집합의 일치이며 최종 매핑/임계값 합의가 아님. 자산 잠금 파일은 B의 `manifest.json`이 아님 |
| 후보 M1 | 해당 스냅샷에 M1 가중치·B manifest·export 구현 없음 | A의 실제 후보 패키지·최종 매핑·임계값·평가 identity·export 필요 |
| B engineering M0 | 로컬 CPU 로딩·합성 예열 등은 M0 기록에 별도 기재 | 라벨/0.5 임계값은 미합의, 평가 `not_evaluated`, 품질 gate `blocked` |
| B 기본 loader | [load_gliner](../detector/gliner_backend.py)는 고정 M0 identity·설정만 허용 | 현재 M1 로딩 지원 없음. 구조 검증 통과로 M1 로딩 성공을 주장할 수 없음 |

위 A 확인에는 데이터셋·가중치 열람, 학습·평가 실행을 포함하지 않는다.
[A 실험 계획](https://github.com/stevenkim18/2026_codyssey_freedom/blob/fa88b1c032e592bbc9092b49671efb894b5c4f41/model/configs/experiment-plan.json)은 매핑/임계값·서비스 시험셋·평가 실행을 대기로 구분한다.
[A 단계 기록](https://github.com/stevenkim18/2026_codyssey_freedom/blob/fa88b1c032e592bbc9092b49671efb894b5c4f41/model/reports/stage2-checks.json)의 검증은 데이터 검사 범위이며 M1 품질 평가가 아니다.
M1을 M0 revision으로 표시하거나 기존 identity·설정 방어를 완화해 통과시키지 않는다.
M1 지원은 사실에 맞는 identity·export 방식과 별도 A/B 합의·B 구현/회귀 검증이 먼저다.

## 역할과 전달물

| 담당 | 준비·판정 책임 | 임의로 확정하지 않는 사항 |
|---|---|---|
| A | 학습/평가, 후보 identity·lineage, 최종 라벨·임계값 근거, 패키지 export·라이선스·회귀 사례 | B 운영 loader·서비스 배포 |
| B | 기존 schema/파일/해시 검사, 합의 후 loader 적합성·offset·예열·복구·성능 검증 | A의 품질 평가나 훈련 이력 |
| A/B | 최종 라벨/임계값, 후보/기준선 identity, 재현 가능한 평가와 교체 판정 합의 | 단순 해시 성공을 품질 승인으로 해석 |
| C | 사건 wire 계약·수집/조회 표시 | B 모델 schema가 C 사건 계약까지 합의했다는 해석 |

## A가 실제 후보와 함께 제공할 정보

- [ ] 후보의 `model_version`, `threshold_version`, 실제 `repo_id`와 40자리 고정 `revision`을 제공한다.
  원본 M0·backbone·학습 코드·데이터 revision, 변환/학습/export 변경 이력과 후보 가중치의 연결 근거도 보존한다.
  현재 schema로 정직하게 표현할 수 없는 identity는 A/B 협의 대상으로 남긴다.
- [ ] 18 canonical 유형 각각에 모델 라벨/설명과 임계값을 연결하고 최종 A/B 합의 여부를 표시한다.
  매핑과 임계값의 key 집합은 동일하고 모델 라벨 값은 중복되지 않아야 한다. `API_KEY`, `PRIVATE_KEY`는 별도 규칙이다.
- [ ] 전처리 버전, `normalization=none`, `offset_unit=unicode_codepoint`, `overflow=reject`를 명시한다.
  API의 방향별 전체 텍스트 한도는 16,000 codepoint이며 패키지 한도는 그 이하의 양의 정수다.
  원문 segment 기준 end-exclusive 위치를 유지하고 Unicode·이모지·조사·공백·분할 경계를 회귀 사례로 제공한다.
- [ ] 가중치, 모델 설정, 오프라인 tokenizer/config, 라이선스 사본/고지, 평가 결과와 합성 회귀 사례를 제공한다.
  외부 캐시나 Hub 자동 다운로드에 의존하지 않는 export여야 하며 원본·수정 파일의 출처를 구분한다.
- [ ] 라이선스 identity와 모델/데이터/backbone/코드의 적용 고지 및 변경 이력을 구분한다.
  라이선스 문자열이나 파일 해시 확인은 배포 권리의 실질 검토를 대신하지 않는다.
- [ ] 평가에 기준선·후보·임계값·데이터/평가 코드 revision, seed, 분할, 실행 환경·명령을 연결한다.
  dev 선택과 최종 시험을 분리하고 공식 BIO 지표와 자체 문자 span 지표를 혼용하지 않는다.
  모델 단독/동일 규칙 결합 결과, 유형별 지표·중요 유형별 미탐 분모/건수/변화·신뢰구간,
  정상 문장 오차단율과 서비스 시험셋 구성 근거를 제공한다. 미측정 항목은 미측정으로 남긴다.
  분모가 0인 유형은 평가 불가로 표시한다. 현재 A 기록의 공식 dev/test에는 `PERSONAL_ATTRIBUTE` 정답이 없다.
  추가 학습 자체의 효과를 주장하면 라벨·임계값·규칙·분할·후처리 등 다른 비교 조건을 고정한다.
- [ ] 교체 판정은 기존 PRD 기준을 적용한다. 중요 유형 합산 재현율·전체 F1은 기준선 이상,
  정상 오차단율 증가는 1%p 이하이며 유형별 퇴행은 원인 해소 전 교체 보류다.
  운영 지연·메모리와 관련 TDD 통합 검증도 필요하다. 새로운 수치 기준·승인 서명을 이 문서에서 만들지 않는다.

18개 유형은 [B 타입 정의](../detector/schemas.py)의 전체 유형에서 두 비밀키 규칙을 제외한 것이다:
`KR_RRN`, `CREDIT_CARD`, `PERSON`, `EMAIL_ADDRESS`, `KR_PHONE_NUMBER`, `KR_BANK_ACCOUNT`,
`KR_LOCATION`, `DATE_OF_BIRTH`, `KR_DRIVER_LICENSE`, `KR_PASSPORT`, `KR_NHIS`, `KR_VEHICLE_PLATE`,
`USERNAME`, `PERSONAL_ATTRIBUTE`, `ORGANIZATION`, `URL`, `IP_ADDRESS`, `NRP`.

## B의 단계별 확인

| Gate | 확인 내용 | 통과가 뜻하지 않는 것 |
|---|---|---|
| 1. 구조 | [model-package.schema.json](model-package.schema.json)의 schema `1.0`, 버전·identity·매핑·전처리·artifact/file 구조 | A/B 합의, 평가 내용의 타당성 |
| 2. 실파일 | runtime이 root의 `manifest.json` + `files` 선언 파일 집합의 정확한 일치와 각 SHA-256을 확인. 누락·추가 파일·symlink·경로 이탈 거절 | 가중치의 출처/안전성·정확도 승인 |
| 3. loader | 실제 후보 identity·config 지원, 고정 라이브러리/환경, 오프라인 의존성·전처리/offset 검증 | schema만으로 임의 M1을 현재 M0 loader에 투입 가능 |
| 4. 품질 | A 평가 근거와 PRD 교체 기준을 A/B가 확인 | engineering 평가 placeholder를 승인으로 취급 |
| 5. 실행·운영 | 합의된 후속 범위에서 예열·재검증·readiness·drain/실패 복구·GPU·전체 입력·성능 확인 | 로컬 CPU·합성 검증을 실제 서버 검증으로 확장 |

JSON Schema는 구조 설명이고 `ModelPackage`의 교차 필드 검증과 `_verify_package()`의 파일/해시 검증은 별도다.
`ModelPackage` 자체는 18개 전체를 강제하지 않지만 현재 GLiNER adapter는 18개 전체를 요구한다.
runtime은 평가 파일의 SHA-256을 확인할 뿐 평가 의미나 승인 상태를 판정하지 않는다.
기본 GLiNER pin은 `0.2.29`이며 실제 기본 loader는 **가중치 역직렬화 전에 안정판 PyTorch ≥2.10.0**을 요구한다.
upstream의 `torch>=2.0` 의존성 선언은 B 환경에서 낮은 버전을 사용해도 된다는 허가가 아니다.
환경·native 의존성 검증 범위와 제한은 [의존성 기록](../detector/DEPENDENCIES.md)을 따른다.

## 네트워크 없는 로컬 준비 명령

`dev`에서 실행한다. 아래는 이 작업 환경의 실제 **Anaconda `py312` Python**을 절대 경로로 지정한다.
첫 명령으로 실행 경로를 확인하고 다른 Conda/base/별도 venv로 대체하지 않는다.
설치·접속 없이 애플리케이션만 준비하는 예시이며 `--model`을 생략하므로 모델 파일을 읽지 않는다.
출력은 source 밖인 `/private/tmp`의 존재하지 않는 새 경로를 사용한다. 아래 경로가 이미 있으면
build/verify의 경로를 함께 새 이름으로 바꾼다. source 안의 `.cache`는 출력 경로로 허용되지 않는다.
결과/해시는 별도 신뢰 기록에 보존한다.

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -c "import sys; print(sys.executable, sys.prefix)"
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m deployment.prepare plan --source . --spec deployment/examples/gpu.json
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m deployment.prepare build --source . --spec deployment/examples/gpu.json --output /private/tmp/model-handoff-packet-20261031-01
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m deployment.prepare verify --output /private/tmp/model-handoff-packet-20261031-01
```

실제 A 패키지를 받으면 Gate 1·2 범위의 입력을 검토한 뒤 `plan`/`build`의 `--model`에 해당 로컬 디렉터리를 지정할 수 있다.
이는 모델을 로딩하지 않으며 품질 승인을 만들지 않는다. `verify`에는 build가 반환한 manifest SHA-256을
`--expected-manifest-sha256`으로 주어 별도 신뢰 기록과 비교할 수 있다.
모델을 포함해 만든 별도 packet에만 다음 **읽기 전용 plan**을 사용할 수 있다:

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m deployment.model_stage --archive /private/tmp/model-handoff-with-model/model-assets.tar --metadata /private/tmp/model-handoff-with-model/release-manifest.json
```

`model_stage` 기본 plan은 archive/manifest 무결성을 검사하지만 전체 `ModelPackage` 의미 검증·모델 로딩은 하지 않는다.
`prepare`가 수행하는 전체 package 검증과 구분한다. 위 모델 없는 packet에는 이 명령을 사용하지 않는다.
현재 범위에는 `--apply`, 서버 접근·서비스 시작, 실제 M1 로딩·GPU 검증이 없다.
상세 준비 도구는 [배포 가이드](../deployment/README.md), M0 전용 offline builder는 [prepare_m0.py](../detector/prepare_m0.py)를 따른다.
M0 builder의 engineering 매핑·평가 placeholder를 A의 최종 M1 인수물로 재사용하지 않는다.
