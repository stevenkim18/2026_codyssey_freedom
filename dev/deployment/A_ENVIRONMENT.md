# A 담당 GPU 기본 환경

## 범위와 상태

2026-10-31 읽기 전용 사전 점검 및 의존성 검토 완료. A용 격리 환경 설치, 설치 담당자의 새 SSH 세션 실행 검증 및 별도 검증자의 독립 검증을 마쳤다.

이번 작업은 A가 사용할 Python/CUDA 실행 기반 준비에 한정한다. PRD R07의 데이터 검증·변환·학습·평가, 모델 및 데이터 다운로드, `dev/training/` 구현은 수행하지 않는다. 운영 추론은 B 소유이며 단일 L4의 학습과 실시간 추론은 PRD R11에 따라 시간을 분리한다.

## 관측한 서버 상태

| 항목 | 관측값 |
|---|---|
| 대상 역할 | 비공개 접속 파일로 호스트를 확인한 GPU 서버 |
| OS | Ubuntu 24.04.1 LTS |
| CPU / 메모리 | 4 vCPU / 약 47 GiB |
| 홈 볼륨 여유 | 약 77.5 GiB |
| GPU | NVIDIA L4, 23034 MiB |
| NVIDIA 드라이버 | 580.159.04 |
| 점검 시 GPU 사용 | 0 MiB, 0%, compute process 없음 |
| 시스템 Python | `/usr/bin/python3`, 3.12.3 |
| 로그인 PATH의 Conda | 발견되지 않음 |
| CUDA 경로 | `/usr/local/cuda-13.0`, `/usr/local/cuda/bin/nvcc` |

CUDA 경로의 존재만 확인했으며 `nvcc` 컴파일 동작은 검증하지 않았다. `nvidia-smi`가 표시하는 CUDA 지원 버전은 설치된 Toolkit 검증으로 취급하지 않는다. PyTorch CUDA 런타임은 기존 드라이버와 호환되는 배포본을 선택하며 시스템 드라이버·Toolkit 변경은 범위 밖이다.

## 설치 전 조건

- 다른 담당자의 환경을 수정하지 않는 A 전용 Conda 환경을 사용한다.
- 실제 Conda 경로와 설정 채널을 확인하고, 필요한 버전을 해당 채널에서 먼저 검색한다.
- 기존 패키지 교체 범위와 의존성 해결 결과를 설치 전에 검토한다. Conda에서 필요한 패키지/버전이 없을 때만 해당 환경의 `python -m pip`를 사용하고 근거를 기록한다.
- 기본 Python 3.12 및 CUDA PyTorch 확인만으로 학습 준비 전체나 PRD R07 완료를 주장하지 않는다. 향후 GLiNER가 필요하면 B의 고정 버전 `0.2.29`와 호환성을 맞춘다.

## 완료 검증 기준

설치 프로세스와 분리한 새 프로세스에서 Python 실행 경로·버전, 패키지 버전 및 의존성, CPU 전용 빌드나 잘못된 환경을 사용하는 실패 가능성을 확인한다. GPU가 유휴 상태일 때만 작은 GPU tensor 연산을 수행하고 종료한다. 모델 로딩·학습은 수행하지 않는다. 독립 검증자가 실제 사용자 실행 명령으로 같은 환경을 확인해야 설치 완료로 보고한다.

## 참고

- [PRD](../../docs/AI_PII_Gateway_PRD_v1.md): A/B 책임 경계, R07 및 R11.
- [PyTorch 공식 배포 명령](https://pytorch.org/get-started/previous-versions/): CUDA 런타임별 배포본 확인.
- [Miniforge 공식 릴리스](https://github.com/conda-forge/miniforge/releases): Conda 설치가 필요한 경우 출처 및 체크섬 확인.

## 확정한 설치 구성

- Conda 관리자: `$HOME/miniforge3`, Miniforge 26.9.4-1 / Conda 26.9.4. 로그인 셸 초기화 파일은 변경하지 않는다.
- 공식 설치 파일 SHA256: `571b410272cc434f28bc6c5c49ca8ffd4a4d1bcba9f57f1b6baccdb46446cfcc`.
- A 환경: `$HOME/miniforge3/envs/prd-a-py312`.
- 설정 채널 `conda-forge` 검색 및 dry-run을 거쳐 Python 3.12.16, PyTorch 2.10.0 (`cuda130_generic_py312_hbdc3359_201`), CUDA runtime 13.0, cuDNN 9.18.0.77, libnvjitlink 13.0.88을 선택했다. pip 대체 설치는 사용하지 않는다.
- 89개 새 패키지, 예상 다운로드 2,959,233,947 bytes. 기존 환경에서 제거·교체하는 패키지는 없다. CUDA 도구의 일부가 PyTorch 전이 의존성으로 A 환경 내부에만 설치된다.
- 최신 후보의 cuDNN 9.27 CUDA 13.x는 드라이버 615 이상을 요구하여 제외했다. 선택한 [cuDNN 9.18.0 공식 호환표](https://docs.nvidia.com/deeplearning/cudnn/backend/v9.18.0/reference/support-matrix.html)는 CUDA 13.0 및 Linux driver 580.65.06 이상을 지원하며 실제 580.159.04가 충족한다.
- `conda-meta/pinned`에서 Python 3.12, 해당 PyTorch 빌드, CUDA 13.0, libnvjitlink 13.0, cuDNN 9.18.0을 고정한다. 향후 의존성 변경 시 드라이버 호환성을 다시 검토한다.
- 서버의 `~/.cache/prd-a-bootstrap/`에 설치 파일, dry-run `a-solve-compatible.json`, 전체 빌드 고정 목록 `a-exact-specs.txt`, 설치 결과 `a-install.json`을 보존한다.

## A 담당자 실행 방법

GPU 서버 로그인 후 명시적으로 환경을 활성화한다. base 환경의 Python은 A 개발용 Python이 아니다.

```bash
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate prd-a-py312
python --version
python -c 'import sys, torch; print(sys.executable); print(torch.__version__, torch.version.cuda)'
```

활성화 없이 지정 실행도 가능하다.

```bash
"$HOME/miniforge3/bin/conda" run -n prd-a-py312 python --version
```

GPU 검사나 향후 학습 전에는 B와 GPU 사용 시간을 조율한다. 이 환경에는 GLiNER, 모델, 데이터셋, 학습 코드를 추가하지 않는다.

## 실행 검증 결과

설치와 분리된 새 SSH 세션에서 위 활성화 경로를 사용했다. `python -I`로 사용자 site-packages 및 Python 환경변수 오염을 차단한 상태에서 다음을 확인했다.

| 검사 | 결과 |
|---|---|
| Python 버전·실행 파일·prefix | 3.12.16, A 환경 경로 일치 |
| torch 모듈 경로 | A 환경의 `lib/python3.12/site-packages/torch` |
| CPU 전용 빌드 오류 확인 | CUDA 13.0, `torch.cuda.is_available()` 참 |
| 장치·compute capability | NVIDIA L4, 8.9 |
| cuDNN 실제 로딩 | 91800 (9.18.0) |
| CUDA tensor + autograd | `[2, 3]` 제곱합의 gradient `[4, 6]` 일치 |
| 작은 cuDNN convolution | 합계 36 일치, CUDA synchronize 성공 |
| 동일 환경 `python -I -m pip check` | 종료 0, 의존성 문제 없음 |
| 프로브 종료 뒤 GPU 작업 | compute process 없음 |

학습·모델 다운로드·모델 정확도·학습 처리량 검증은 수행하지 않았다. 기본 CUDA 연산 통과를 학습 전체 검증으로 해석하지 않는다.

독립 검증자는 별도 SSH 연결에서 실제 환경 활성화, Python·torch 경로와 고정 버전, GPU 행렬곱·역전파 및 cuDNN convolution을 다시 확인했다. GPU 작업 수는 검증 전후 모두 0이었다. 작은 프로브의 peak tensor allocation은 16.3 MiB, reserved memory는 22 MiB였으며 CUDA context 메모리는 포함하지 않는다. 시스템 fingerprint 비교는 독립 프로브 실행 전후 범위이며 설치 전체 전후 비교를 의미하지 않는다.

## GPU 인계 상태

사용자가 후속 실제 모델·데이터 다운로드와 1-step 학습 시험을 취소하고 A 담당자가 직접 수행하도록 정했다. 해당 실행 스크립트는 검토 대기 단계에서 멈췄으며 GPU 서버에서 실행하지 않았다. 마지막 읽기 전용 확인에서 이번 시험용 `prd-m0-smoke-*` 디렉터리와 관련 실행 프로세스가 모두 없었다. 삭제할 시험 자산이 없어 원격 삭제도 수행하지 않았다. 기본 A 환경과 추가 의존성 환경 `py312`는 보존하며, GPU 검사 API 서비스는 배포·기동하지 않는다.
