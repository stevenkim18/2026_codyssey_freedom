# 로컬 detector 의존성 설치 계획 검토

2026-10-31 UTC의 macOS arm64 Anaconda `py312` 환경에 대한 **설치 계획 조사 및 독립 검증 기록**이다. 첫 조사는 읽기 전용/dry-run으로 수행했고, 아래 독립 검증은 별도 설치 담당자가 최소 계획을 적용했다고 보고한 후 수행했다. 이 문서 작성자는 패키지 설치, 기존 패키지 변경 및 전역 환경 수리를 수행하지 않았다. Linux/GPU 환경의 lockfile이 아니며 `dev/environment.yml`의 새 환경 생성·전체 solve 재현도 검증하지 않았다.

## 최소 Conda 계획

SentencePiece는 버전뿐 아니라 build를 고정해야 한다. Python 바인딩과 native 라이브러리의 아래 build 조합이 현재 설치된 Protobuf·Abseil 계열과 선언상 호환된다.

| 추가 대상 | 정확한 build | 관련 의존 선언 |
|---|---|---|
| `sentencepiece-python=0.2.1` | `py312h8bbdcba_3` | Python 3.12/cp312, `libsentencepiece=0.2.1=h9466b84_3`, `libprotobuf >=6.33.5,<6.33.6.0a0`, `libcxx >=19` |
| `libsentencepiece=0.2.1` | `h9466b84_3` | `libprotobuf >=6.33.5,<6.33.6.0a0`, `libabseil >=20260107.0,<20260108.0a0`의 cxx17 build, `libcxx >=19` |
| `seqeval=1.2.2` | `pyhd3deb0d_0` (noarch) | Python, NumPy, scikit-learn |

SentencePiece는 아래 GLiNER 0.2.29 wheel이 선언한 runtime 의존성이다. seqeval은 보조 평가·기능 검증용으로 설치했으며 해당 wheel의 필수 runtime 의존성은 아니다.

확인한 기존 Conda 기록은 PyTorch `2.12.0 cpu_generic_py312_ha6aee4d_0`, Python `3.12.2 hdf0ec26_0_cpython`, libprotobuf `6.33.5 hb82ec63_2`, libabseil `20260107.1 cxx17_h2062a1b_0`, libcxx `21.1.2 hf598326_0`, scikit-learn `1.7.2 py312h79e0ffc_0`이다. NumPy `1.26.4`와 Python protobuf `5.29.5`는 `pypi_0`로 기록되어 있다. 이 목록은 패키지 관리 기록이며 모든 import의 실제 버전을 대조한 결과가 아니다. Python protobuf와 native libprotobuf도 별개 패키지다.

SentencePiece Python build 0–2는 libprotobuf 6.31.1 계열, build 4–7은 7.35.1 계열을 요구하여 현재 native Protobuf를 보존하려는 계획에는 맞지 않았다. `sentencepiece` 메타패키지는 `sentencepiece-spm` CLI도 추가하므로, Python detector용 후보에서는 바인딩과 native 라이브러리만 요청했다. 별도 SentencePiece CLI 사용까지 검증하거나 지원한다고 주장하지 않는다.

실행하여 확인한 최소 **dry-run** 명령:

```bash
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/bin/conda install \
  -n py312 --freeze-installed --override-channels -c conda-forge \
  'sentencepiece-python=0.2.1=py312h8bbdcba_3' \
  'libsentencepiece=0.2.1=h9466b84_3' \
  'seqeval=1.2.2=pyhd3deb0d_0' \
  'certifi=2026.10.18=pyhd8ed1ab_1' \
  'ca-certificates=2026.10.18=hbd8a1cb_0' \
  --dry-run --json
```

결과는 `success=true`, `LINK` 정확히 위 추가 대상 3개, `UNLINK` 없음이었다. 따라서 이 solve의 계획에는 기존 PyTorch/Python/Protobuf/Abseil/libcxx/인증서/pip 교체가 없다. certifi와 CA 핀은 이 환경의 기존 버전을 보존하기 위한 것으로 일반적인 권장 버전 고정이 아니다. 다른 PC나 Linux에 그대로 적용하지 않는다.

비교 검토한 계획은 다음과 같다.

| 요청 | 실제 dry-run 결과 |
|---|---|
| 버전만 지정한 넓은 설치 | 부모 작업자의 선행 조사에서 PyTorch 2.12.1 및 여러 native 의존성 교체가 제안되어 적용하지 않음 |
| 위 3개 대상에 Python·kernel build를 직접 요청 | kernel 교체는 피했으나 pip 26.2.2 추가 및 기존 인증서 2개 교체 제안 |
| 위 요청에 기존 인증서와 pip 25.3도 지정 | 기존 패키지 UNLINK는 없으나 Conda pip 25.3까지 총 4개 LINK |
| 최종 최소 명령 | 대상 3개만 LINK, UNLINK 없음, pip 추가 없음 |

현재 pip 25.3은 PyPI 관리 상태다. 따라서 동일 버전 Conda pip 추가도 단순한 무변경으로 보지 않았다. 최종 계획에는 해당 추가가 없다. Python을 직접 요청하는 경우에만 pip 추가가 나타난 이유는 확인하지 않았으며, 전역 Conda 설정을 변경하지 않았다.

## 적용 전후 경계

설치 담당자는 실제 적용 직전에 같은 dry-run의 `LINK`/`UNLINK`를 다시 확인해야 한다. 채널 메타데이터와 환경 상태가 바뀔 수 있으므로 위 결과가 미래 solve까지 보장하지 않는다. 예상 외 기존 패키지 교체가 생기면 적용하지 않고 대안을 검토한다. bare `--no-deps`는 이 계획의 대안이 아니다. Conda 문서상 의존성 확인을 생략하며 환경을 깨뜨릴 수 있다. [`conda install` 옵션](https://docs.conda.io/projects/conda/en/stable/commands/install.html)

실제 설치 전 확인은 **의존 선언 및 교체 계획 검토**까지다. 아직 설치하지 않은 native extension의 로딩·실행 성공은 보장할 수 없다. 적용 후 별도 fresh process에서 정확한 Anaconda Python 경로와 `sentencepiece`/`seqeval`의 모듈 경로·버전을 확인하고, 작은 synthetic SentencePiece 학습/인코딩 및 seqeval metric 호출로 native 실행과 기본 기능을 검증해야 한다. 기존 PyTorch import 및 detector 테스트의 회귀도 별도 확인한다. 설치자가 아닌 독립 검증과 실제 사용자 실행 경로 검증까지 마치기 전에는 설치 완료로 보고하지 않는다.

GLiNER 0.2.29의 Conda 채널 부재에 따른 pip fallback 판단은 별도 패키지 조사다. SentencePiece와 seqeval은 Conda에 존재하므로, solver 변경을 피한다는 이유만으로 두 패키지를 pip로 전환하지 않는다. GLiNER 모델 다운로드·로딩, 실제 GPU 추론, 배포 서버 ABI 및 성능은 이 dry-run으로 검증되지 않는다.

## 설치 후 독립 기능 검증

2026-10-31 05:00 UTC, 설치 담당자와 별도 에이전트가 아래 실제 사용자 Python 경로를 직접 실행했다. 설치 프로세스를 재사용하지 않는 두 개의 fresh process였고, 각 프로세스는 다음 항목을 모두 확인한 뒤 종료 코드 0으로 끝났다.

```text
/Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -c ...
```

- 첫 프로세스: `torch` → `sentencepiece` 순서로 import.
- 둘째 프로세스: `sentencepiece` → `torch` 순서로 import. C++ native 라이브러리 로딩 순서에 따른 충돌을 겨냥한 확인이다.
- 두 프로세스 모두 `sentencepiece 0.2.1`, `seqeval 1.2.2`, `torch 2.12.0`을 올바른 Anaconda `py312/lib/python3.12/site-packages`에서 로딩했다.
- 임시 디렉터리의 작은 합성 영어 corpus로 BPE 모델을 실제 학습하고 `hello gateway`의 encode/decode 원문 왕복을 확인했다. 학습 모델과 corpus는 검증 후 삭제했다.
- `seqeval.metrics.f1_score`의 합성 정답/예측 일치 사례에서 `1.0`을 확인했다.
- PyTorch CPU tensor `[1, 2] + [3, 4]`가 `[4, 6]`임을 확인했다.
- Starlette 실제 import 버전 `0.47.3`, Uvicorn 실제 import 버전 `0.35.0` 유지와 모듈 경로를 확인했다.

반면 Python 배포 메타데이터에는 다음 불일치가 남아 있다. 별도의 fresh process가 디렉터리와 `METADATA`의 Name/Version 필드만 읽어 확인했으며 파일을 수정하지 않았다.

| 패키지 | 실제 import | `importlib.metadata.version()` | 확인한 상태 |
|---|---|---|---|
| Starlette | `0.47.3` | `0.27.0` | `starlette-0.27.0.dist-info`와 `starlette-0.47.3.dist-info`가 모두 존재하고 각각 해당 버전을 선언 |
| Uvicorn | `0.35.0` | `None` | `uvicorn-0.52.1.dist-info`에는 METADATA가 없고 `uvicorn-0.35.0.dist-info`의 METADATA는 `0.35.0`을 선언 |

따라서 Conda inventory 갱신과 Python 배포 메타데이터의 정합성은 같은 의미가 아니다. 이 설치가 위 중복·불완전 메타데이터의 원인이라고 확정하지 않는다. 환경 전체 정상화나 fresh solve 재현 성공으로 보고하지 않으며 자동 전역 수리를 하지 않았다.

이 독립 확인은 추가한 SentencePiece/seqeval의 기본 실제 실행, PyTorch CPU 연산 및 두 native import 순서까지 통과했다는 근거다. GLiNER 자체 로딩·실모델 추론, M0 승인, GPU/원격 서버, 전체 서비스 회귀 및 성능 검증은 포함하지 않는다.

## GLiNER wheel 설치 후 독립 검증

설치 담당자는 Conda 채널에 0.2.29가 없음을 조사한 뒤 검토한 단일 wheel을 `--no-deps --no-index`로 설치했다고 보고했다. 이 선택은 이미 설치된 아래 8개 runtime 의존성이 wheel의 요구 범위에 맞는지 먼저 확인한 후 이뤄졌다. seqeval은 이 목록에 없다.

| 실제 wheel의 runtime 요구 | 독립 fresh process가 확인한 배포 버전 |
|---|---|
| `torch>=2.0.0` | `2.12.0` |
| `transformers>=4.51.3,<5.17.0` | `4.57.3` |
| `huggingface_hub>=0.21.4` | `0.36.0` |
| `numpy` | `1.26.4` |
| `packaging` | `24.2` |
| `safetensors` | `0.7.0` |
| `tqdm` | `4.67.1` |
| `sentencepiece` | `0.2.1` |

독립 검증자는 2026-10-31 05:07 UTC에 다음을 직접 확인했다.

- 캐시된 `gliner-0.2.29-py3-none-any.whl`의 SHA-256은 `0c8cfb9f5c2daf7aff329ebb5ab1609052d7ad0aca0ef26049b9476b018b3fde`이며, 설치된 `gliner/` Python 소스 54개가 해당 wheel 파일과 byte 단위로 일치했다. 이는 검토 artifact와 설치 내용의 일치 근거이며 upstream 작성자의 신원을 보증하는 별도 서명 검증은 아니다.
- 실제 사용자 경로의 Anaconda Python과 `python -I -B` 격리 실행을 별도 fresh process로 사용했다. `gliner.__version__`과 `importlib.metadata.version('gliner')`는 모두 `0.2.29`, 모듈 경로는 해당 `py312/lib/python3.12/site-packages/gliner/`였다. 위 실제 wheel의 8개 요구 조건도 모두 충족했다.
- `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 지정하고 Python socket 연결 함수를 실패시키는 guard를 적용했다. 모델 다운로드나 원격 서버 접속 없이 실행했다.
- 실제 `GLiNERConfig(max_len=384, max_width=12)`가 값을 유지했다. 공개 `WordsSplitter('whitespace')`에 합성 한국어·이모지·개행 문자열을 전달하여 반환된 7개 token의 원문 슬라이스, Python codepoint offset, 순서 및 비공백 누락 없음을 확인했다.
- 실제 `UniEncoderSpanProcessor` 객체의 `prepare_inputs([words], labels)`를 호출하여 두 label의 prompt 길이 5와 이후 원단어 suffix 보존을 확인했다. tokenizer에는 이 함수가 사용하지 않는 최소 special-token 속성만 제공했다. 실제 Transformer tokenizer의 subtoken/word ID 대응은 이 검증에 포함되지 않는다.
- 설치된 0.2.29의 `GLiNER`는 factory다. `predict_entities`를 factory 클래스에서 직접 조회하는 최초 검증 가정은 `AttributeError`로 실패했다. 소스와 config dispatch를 확인한 후 실제 `UniEncoderSpanGLiNER.predict_entities`의 어댑터 인자와 공개 `GLiNER.from_pretrained`의 로딩 인자 signature binding을 확인했다. 이 단계에서는 함수 signature만 검증했으며 두 함수로 모델을 로드하거나 추론하지 않았다. `SpanProcessor`라는 이름 대신 실제 wheel의 `UniEncoderSpanProcessor`를 사용했다.

첫 import 과정에는 Fontconfig의 sandbox 밖 캐시 쓰기 불가 경고가 있었고 기능 오류는 아니었다. 이후 `XDG_CACHE_HOME`과 `MPLCONFIGDIR`를 프로젝트의 무시되는 `dev/.cache` 아래로 지정한 fresh process는 위 기능 확인을 종료 코드 0으로 마쳤다. 패키지나 전역 캐시 설정을 수정하지 않았다.

이로써 GLiNER 패키지의 독립 cold-process 발견·버전/경로 확인, 검토 artifact 일치, 공개 config/splitter와 실제 processor의 기본 실행을 확인했다. 이 독립 설치 검증에는 M0 가중치 로딩·예열을 포함하지 않았다. 후속 별도 프로세스에서 수행한 실제 M0 CPU cold-load·합성 예열 1회는 [M0 통합 기록](M0_INTEGRATION_NOTES.md)의 추가 검증 절에 기록했다. GPU·전체 detector 서비스 경로·한국어 품질·성능 및 배포 서버 검증은 미완료이며 위 Starlette/Uvicorn 메타데이터 불일치도 그대로 남아 있다.
