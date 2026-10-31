# A 파트: 모델 추가 학습 준비

먼저 [쉬운 전체 작업 안내](process.md)를 읽으면 됩니다. 이 폴더는 기존 개인정보 탐지 모델 M0를 한국어 데이터로 추가 학습하고, 후보 M1을 평가하기 위한 A 작업 공간입니다.

## 현재 진행 상태

**3단계 변환 도구와 전체 자료 진단을 구현했으나, 학습 자료 호환성 해결이 필요합니다.** 현재 B의 단어 분할 방식으로 모든 정답을 보존할 수 있는 train 문서는 2,635건이며 중복 제외 2,615건입니다. PRD의 5,000건에 못 미치고 7개 유형의 정답도 남지 않아 학습을 시작하지 않았습니다. [3단계 결과와 B 공동 확인 항목](reports/conversion-summary.md)을 먼저 참고하세요.

변환 테스트를 포함한 기본 테스트 104개와 실제 토크나이저·GLiNER 학습 배치 형식 검증을 통과했습니다. 모델 가중치 없이 로컬 CPU 전용 환경에서 검사했으며, B의 운영 코드나 GPU 서버 환경은 변경하지 않았습니다. 코드·형식 검증 통과와 학습 자료 준비 완료를 구분합니다.

**1단계 기준 고정에 이어 2단계 데이터 다운로드·전체 검사를 실행했습니다.** 고정한 데이터 3개 파일의 크기·SHA-256을 확인하고 전체 300,000건을 검사했습니다. 모델 가중치 다운로드와 학습은 아직 진행하지 않았습니다.

정답 위치 오류·파일 손상·분할 간 겹침은 없었으나, 각 분할 내부의 동일 문장 재등장 4,035건이 있어 결과 상태는 **검토 필요(`needs_review`)**입니다. 중복 문장의 정답 불일치는 없었습니다. [쉬운 결과 보고서](reports/data-audit-summary.md)와 [상세 결과 JSON](reports/data-audit-v2.json)을 참고하세요.

서버의 기존 학습 환경은 선임 팀원이 준비했다고 보고한 상태입니다. 이번 작업에서는 VPN·SSH를 사용하거나 서버 패키지를 변경하지 않았습니다. 설치된 전체 도구 목록과 GPU 동작 검증은 서버 접속이 가능해지면 확인합니다.

## 어떤 버전을 사용하나요?

| 대상 | 이름·버전 | 고정 리비전 |
|---|---|---|
| 기존 모델 M0 | `urchade/gliner_multi_pii-v1` | `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` |
| 학습 데이터 | `woohyun212/k-pii-bench`, 원본 통계의 표기 `v4-corrected` | `844f92f7beba98fa964f957ba324017e82451701` |
| M0 기반 모델 설정·토크나이저 | `microsoft/mdeberta-v3-base` | `a0484667b22365f84929a935b5e50a51f71f159d` |
| GLiNER 코드 | `0.2.29`, B와 동일 | `3e1723b49539d518f76d4d8282f1e05d8b6e74f0` |

리비전은 특정 판본의 식별자입니다. 이후 다운로드 프로그램은 `main`이나 최신 버전 대신 위 값을 사용하도록 만듭니다. 모델의 기반이 되는 토크나이저도 같은 원칙으로 고정합니다.

데이터 제작자는 이 버전을 과거 위치 오류와 분할 문제를 수정한 릴리스라고 설명합니다. 2단계에서 원본 30만 건의 구조·정답 위치·분할 중복을 직접 검사했으며, 결과와 검토 사항을 별도 보고서에 남겼습니다. 정답 위치가 맞는 것과 개인정보 유형의 의미까지 올바른 것은 구분합니다. 논문의 이전 데이터 기준 결과를 우리 실험 결과로 가져오지 않습니다. 출처는 [고정 데이터 설명서](https://huggingface.co/datasets/woohyun212/k-pii-bench/blob/844f92f7beba98fa964f957ba324017e82451701/data/README.md)입니다.

## 도구 버전은 어디에 있나요?

[도구 계획](configs/toolchain-plan.json)에 직접 사용할 패키지 11개의 버전을 선정해 기록했습니다. GLiNER·Transformers 등은 B가 기록한 버전을 우선 사용하고, 추가 학습용 Accelerate는 `1.11.0`을 선정했습니다. 정확한 패키지 이름과 버전은 [버전 제약 파일](configs/constraints-candidate.txt)에 있습니다.

| 주요 항목 | 선정한 기준 | 근거·상태 |
|---|---|---|
| Python | `3.12.16` | 선임 팀원의 준비 완료 보고 기준 |
| PyTorch | `2.10.0+cu130` | 보고된 CUDA 13.0 환경에 맞는 공식 Linux/Python 3.12 배포 파일을 확인 |
| GLiNER | `0.2.29` | B의 로더와 동일 버전 |
| Transformers | `4.57.3` | B의 의존성 검증 기록에 있는 버전 |
| Hugging Face Hub | `0.36.0` | B 기록과 GLiNER·Transformers의 선언된 버전 범위를 고려 |
| Accelerate | `1.11.0` | 학습 보조 도구의 공개 패키지 버전·요구 조건 확인 |

**이 목록은 설치 후보의 직접 버전 기준이며, 전체 환경을 재생성할 수 있는 완성된 설치 명세는 아닙니다.** 각 도구가 추가로 요구하는 하위 패키지와 서버의 실제 설치 상태는 별도로 확인해야 합니다. 선정한 버전이 모두 함께 import되고 GPU 학습까지 되는지 아직 검증하지 않았습니다.

특히 일반 PyPI의 PyTorch 2.10.0 Linux 배포본과 CUDA 13.0용 배포본은 다릅니다. PyPI의 기본 배포본 메타데이터는 CUDA 12.8 계열 의존성을 선언하고 있어, 이를 그대로 설치하면 기존 환경과 달라질 수 있습니다. [공식 CUDA 13.0 배포 목록](https://download.pytorch.org/whl/cu130/torch/)에서 찾은 `2.10.0+cu130` 파일과 확인값을 도구 계획에 기록했습니다. 해당 파일을 받거나 설치한 것은 아닙니다.

서버 접속 후에는 기존 `prd-a-py312`를 확인하고 보존하는 것이 먼저입니다. 이 파일들을 일괄 설치 명령으로 사용하지 않습니다. 실제 설치·import·GPU 검증이 끝난 뒤 하위 패키지, 빌드와 설치 출처를 포함한 전체 환경 명세를 추가합니다.

초기 데이터 검증은 고정 JSONL 파일을 직접 읽는 방식으로 준비합니다. 따라서 현재 단계에서는 `datasets`·`evaluate` 라이브러리를 추가로 선정하지 않았습니다.

## 실험의 시작 설정

[실험 계획](configs/experiment-plan.json)은 PRD의 초기 기준과 이번에 선정한 출발값을 기록합니다.

- 표본 선택·학습 난수 시드: `42`. 아직 실제 표본을 선택하지 않았습니다.
- 첫 학습 자료: 공식 `train`에서 분야·유형을 고려한 5,000건.
- 첫 batch: 1, 초기 최대 epoch: 3, 출발 학습률: `0.00001`.
- PRD의 초기 탐색 범위: 1~3 epoch, 학습률 `0.00001~0.00005`. 모든 조합을 자동 실행한다는 뜻은 아닙니다.
- 체크포인트·탐지 임계값 선택: `dev`. 최종 시험셋으로 설정을 고르지 않습니다.
- 독립 서비스 시험셋: 학습 전에 300건 고정. 아직 구성하지 않았습니다.
- 라벨 설명 매핑과 최종 임계값은 B와 맞춘 뒤 확정합니다. 임시 설정을 합의 완료로 표시하지 않습니다.

GPU 메모리에 따른 누적 계산 설정과 학습 중단의 세부 기준은 실제 학습 실행 전에 확정합니다. 출발 설정 선정과 실행·성능 검증은 별개입니다.

후속 평가의 실험 기록·공정한 비교 요구도 계획에 추가했습니다. M0·M1 모두 dev에서 설정을 고르고, 실험 ID·변경 사항·라벨·임계값·규칙·분할 설정·성능 변화(%p)를 기록하도록 설계합니다. 누적 설정 변경의 효과와 추가 학습만의 효과를 구분합니다. 현재는 계획이며, 관련 실험·평가 코드는 아직 실행하지 않았습니다.

## 파일을 찾는 방법

| 파일·폴더 | 담긴 내용 |
|---|---|
| [configs/assets.lock.json](configs/assets.lock.json) | 모델·데이터 리비전, 다음 단계에서 받을 원본 파일의 크기·확인값 |
| [configs/toolchain-plan.json](configs/toolchain-plan.json) | 도구 버전 선정과 서버 검증 대기 항목 |
| [configs/constraints-candidate.txt](configs/constraints-candidate.txt) | 직접 패키지 버전 제약. 전체 환경 잠금 파일 아님 |
| [configs/experiment-plan.json](configs/experiment-plan.json) | 학습·평가의 출발 설정과 PRD 교체 기준 |
| [metadata/sources.json](metadata/sources.json) | 보관한 설명서·설정·라이선스의 출처와 파일 확인값 |
| [metadata/python-package-metadata.json](metadata/python-package-metadata.json) | PyPI의 공개 요구 조건과 공식 PyTorch 배포 정보에서 추린 내용 |
| [metadata/stage1-validation.json](metadata/stage1-validation.json) | 로컬에서 수행한 1단계 기록 대조 결과 |
| [licenses/NOTICE.md](licenses/NOTICE.md) | 자원별 출처·라이선스와 이번 변경 내용 |
| [data/README.md](data/README.md) | 데이터 다운로드·검증·테스트 실행 방법 |
| [reports/data-audit-summary.md](reports/data-audit-summary.md) | 2단계 실제 검사 결과와 검토 사항 |
| [data/CONVERSION.md](data/CONVERSION.md) | 3단계 변환 형식·실행 방법·진단 출력의 한계 |
| [reports/conversion-summary.md](reports/conversion-summary.md) | 3단계 전체 변환 결과와 한국어 경계 호환성 문제 |
| [configs/conversion-plan.json](configs/conversion-plan.json) | 진단용 임시 라벨 매핑·전체 문서 거절 정책 |
| [metadata/conversion-local-environment.json](metadata/conversion-local-environment.json) | 실제 설치·검증한 Mac CPU 환경. Linux/CUDA용이 아님 |

`assets.lock.json`은 A의 다운로드 기준입니다. 파일 안의 다운로드 여부·검증 상태는 1단계 선정 시점의 기록이며, 현재 실행 상태는 2단계 보고서로 확인합니다. 고정된 자산 기준은 원본 그대로 보존했습니다. B의 배포용 `manifest.json`과 용도가 다릅니다. B의 현재 M0 전용 로더에서 후보 M1을 인수하는 조건은 이후 함께 맞춰야 합니다.

원본 다운로드 위치는 `model/.cache/` 아래입니다. 이 경로는 루트 `.gitignore`에서 제외됩니다. 학습용 JSONL·모델 가중치와 로그인 정보도 Git 제외 여부를 확인합니다. `.secrets`의 값은 설정이나 보고서에 복사하지 않습니다.

## 다음 단계

**다음은 A/B 공통 한국어 분할·원문 위치 처리 방식의 합의와 재검증입니다.** 원문 정답을 조용히 넓히거나 삭제하지 않고, 학습과 B의 실제 검사에서 같은 분할을 사용해야 합니다. 후보 선택은 train/dev로 하고 M0·M1의 비교 조건을 함께 기록합니다. 현재의 진단 부분집합으로 5,000건 요구나 공식 시험셋을 대체하지 않습니다.

공통 방식이 정해지면 전체 변환과 유형별 분포를 다시 검증한 뒤 학습 표본을 선정합니다. 중복 문장은 원본 분할에서 유지했고 진단 결과에 고유 문장 수도 함께 기록했습니다. 학습 표본 선정 단계에서 처리 기준과 선택 목록을 확정합니다.

dev·test에는 `PERSONAL_ATTRIBUTE` 정답이 없고 모든 분할에 개인정보 없는 정상 문장이 없습니다. 이 한계를 평가 계획에 반영하고 별도 서비스 시험셋을 준비해야 합니다. 시험셋의 구조·정답 품질만 검사했으며, 모델 예측이나 시험 점수로 학습 설정을 고르지 않았습니다.
