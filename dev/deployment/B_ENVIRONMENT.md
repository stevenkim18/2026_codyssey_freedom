# B 검사 API 환경 설치 계획

2026-10-31 사전 조사·dry-run 후 작은 GPU 학습 시험에 필요한 의존성 환경을 설치했다. 사용자 지시에 따라 **GPU 검사 API 서비스 배포·기동은 수행하지 않는다.** A 기본 환경은 그대로 유지한다. 이 문서의 `py312`는 유지되는 Python 환경이며 시험 자산 정리 대상이 아니다.

대상은 기존 Conda 관리자 `$HOME/miniforge3` 아래 새 환경 `py312`다. A의 `prd-a-py312`와 base는 변경하지 않는다. detector 운영 의존성과 최소 검증 도구를 대상으로 하며 모델 다운로드·서비스 기동·GPU 작업은 이 계획에서 수행하지 않았다.

## 검토된 Conda 계획

실제 설정 채널은 `conda-forge`다. `gliner=0.2.29`는 해당 채널 linux-64/noarch 검색에서 없었으며 나머지 요청 버전은 검색되거나 dry-run으로 해결되었다.

| 구성 | 계획 버전 |
|---|---|
| Python | 3.12.16 |
| PyTorch | 2.10.0, `cuda130_generic_py312_hbdc3359_201` |
| CUDA / libnvjitlink / cuDNN | 13.0 / 13.0.88 / 9.18.0.77 |
| FastAPI / Uvicorn | 0.116.1 / 0.35.0 |
| Transformers / Hugging Face Hub | 4.57.3 / 0.36.0 |
| SentencePiece / seqeval | 0.2.1 / 1.2.2 |
| HTTPX / NumPy / safetensors | 0.28.1 / 1.26.4 / 0.7.0 |
| datasets / Arrow | 4.4.2 / 23.0.0 CPU build |

FastAPI는 불필요한 CLI·standard extras를 추가하지 않는 `fastapi-core=0.116.1` Conda 패키지를 선택했다. Transformers의 Conda recipe가 `datasets`를 요구하므로 해당 라이브러리가 전이 의존성으로 포함된다. 데이터셋 자체를 내려받는 작업은 아니다. 오래된 datasets 2.14.4와 최신 Arrow 조합을 피하도록 datasets 4.4.2를 지정했으며 Arrow는 CPU build를 선택했다.

최종 dry-run은 **새 패키지 214개**, 기존 환경 `UNLINK=[]`, 추가 다운로드 **114,237,282 bytes**로 성공했다. A 설치의 공용 패키지 캐시를 재사용하는 예상치이며 설치 디스크 사용량과는 다르다. 새 B 환경의 Protobuf 6.31.1 / SentencePiece 0.2.1 native build 조합은 solver가 허용했지만 실제 import 순서·ABI 검증은 설치 후 필요하다.

## GLiNER pip fallback 계획

Conda에 요청 버전이 없어 GLiNER만 pip fallback 대상이다. 관리자 Python으로 실행한 `pip install --dry-run --ignore-installed --no-deps --only-binary=:all: --report ... gliner==0.2.29`는 단일 wheel만 설치 대상으로 보고했다. 아직 존재하지 않는 B 환경에서 실행한 전체 resolver 검증은 아니다.

공식 PyPI wheel SHA256:
`0c8cfb9f5c2daf7aff329ebb5ab1609052d7ad0aca0ef26049b9476b018b3fde`

wheel의 8개 기본 의존성(torch, transformers, huggingface_hub, numpy, packaging, safetensors, tqdm, sentencepiece)에 대해 Conda 계획 버전이 요구 범위를 모두 충족함을 `packaging.Requirement`로 확인했다. 추가 extra는 요청하지 않는다. 향후 설치 시에는 새 B 환경의 Python으로 검토한 wheel을 `--no-deps` 설치하고, 의존성 자동 업그레이드를 허용하지 않는다.

서버 `~/.cache/prd-b-bootstrap/`에 `search-*.json`, `b-solve.json`, `gliner-pip-plan.json`을 보존했다. 설치 승인 후 정확한 빌드 목록을 고정하고, 새 프로세스·양방향 native import 순서·pip check·실제 detector 경로·별도 독립 검증을 수행해야 한다. 최초 계획과 아래 설치·검증 결과를 구분한다. 모델 다운로드·학습·추론은 이 의존성 설치 검증에 포함하지 않는다.

## 작은 학습 시험용 의존성 설치 결과

기존 214개 고정 빌드에 Conda 채널에서 확인한 `accelerate=1.15.0`, `psutil=7.2.2`만 추가한 최종 216개 계획은 추가 다운로드 114,746,414 bytes, `UNLINK=[]`로 해결되었다. 새 `$HOME/miniforge3/envs/py312`에 해당 정확한 빌드를 설치했다. A 기본 환경과 관리자 base의 패키지는 변경하지 않았다.

새 환경의 Python으로 GLiNER `--dry-run --no-deps`를 다시 실행하고 실제 설치된 8개 의존성의 버전 범위를 확인했다. 고정 SHA256에 맞는 공식 wheel만 다운로드하여 같은 Python의 `pip install --no-deps --no-index`로 설치했다.

설치 프로세스와 분리된 두 새 프로세스에서 `torch → sentencepiece → gliner`, `sentencepiece → torch → gliner` import 순서, `gliner.training.Trainer/TrainingArguments`, datasets 및 accelerate import를 확인했다. 실제 실행 파일은 위 `py312/bin/python`이며, PyTorch 2.10.0 / GLiNER 0.2.29 / Transformers 4.57.3 / SentencePiece 0.2.1 / Accelerate 1.15.0 / datasets 4.4.2가 일치했다. `python -I -B -m pip check`는 종료 0, 의존성 문제 없음으로 통과했다.

이 단계에서는 GPU 연산이나 API 서비스를 실행하지 않았다. A 담당자는 필요한 경우 이 유지된 환경을 후속 학습용으로 사용할 수 있으나 실제 데이터 학습 준비 전체가 검증된 것은 아니다. 이 추가 환경에 대한 설치 담당자 외 독립 실행 검증은 별도 완료 근거가 없으므로 완료로 주장하지 않는다.

사용자가 후속 모델·데이터 다운로드와 실제 학습을 A 담당자에게 넘기도록 취소했다. 이번 M0 다운로드·1-step 학습·시험 자산 삭제는 **미실행**이다. 마지막 읽기 전용 확인에서 시험용 `prd-m0-smoke-*` 디렉터리와 관련 프로세스가 모두 없었으며 삭제할 자산도 없었다. `py312`는 유지되는 의존성 환경으로 인계하고, GPU API 배포·기동이나 추가 GPU 시험은 진행하지 않는다.
