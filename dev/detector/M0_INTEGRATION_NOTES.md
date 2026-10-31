# M0 통합 준비 기록

확인일: 2026-10-31 UTC. 이 문서는 공식 메타데이터·소스 조사와 **합성 모델을 주입한 어댑터 검증**을 기록한다. 실제 가중치 다운로드, GLiNER 설치, CPU/GPU 추론, 한국어 품질·지연 측정은 이 작업에서 수행하지 않았다.

## 확인한 고정 자산과 출처

| 항목 | 확인 결과 | 1차 출처 |
|---|---|---|
| M0 저장소 | `urchade/gliner_multi_pii-v1` | [HF 모델 카드](https://huggingface.co/urchade/gliner_multi_pii-v1) |
| 조회 시 revision | `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d` | [HF 공식 metadata API](https://huggingface.co/api/models/urchade/gliner_multi_pii-v1) — 작은 JSON 직접 조회 |
| 모델 라이선스 표기 | `apache-2.0` | [HF 모델 카드](https://huggingface.co/urchade/gliner_multi_pii-v1) |
| HF 파일 목록 | `.gitattributes`, `README.md`, `gliner_config.json`, `pytorch_model.bin`; 별도 LICENSE·tokenizer 파일 없음 | [HF 공식 metadata API](https://huggingface.co/api/models/urchade/gliner_multi_pii-v1) |
| M0 설정 | `max_len=384`, `max_width=12`, `model_name=microsoft/mdeberta-v3-base` | [revision 고정 설정 JSON](https://huggingface.co/urchade/gliner_multi_pii-v1/raw/1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/gliner_config.json) — 작은 파일 직접 조회 |
| 조사한 라이브러리 pin 후보 | `gliner==0.2.29`, tag commit `3e1723b49539d518f76d4d8282f1e05d8b6e74f0` | [공식 Git tag API](https://api.github.com/repos/urchade/GLiNER/git/ref/tags/v0.2.29), [고정 버전 선언](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/gliner/__init__.py) |
| 해당 pin의 의존성 선언 | Python ≥3.10, torch ≥2.0, transformers ≥4.51.3 및 <5.17.0. 이 범위는 재현 가능한 전체 환경 잠금이 아니다. | [고정 pyproject.toml](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/pyproject.toml) |
| 코드 라이선스 원본 | Apache License 2.0 파일 존재. 모델 카드의 고지와 코드 저장소 라이선스는 구분해 보관한다. | [고정 LICENSE](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/LICENSE) |

모델 카드는 영어·프랑스어·독일어·스페인어·포르투갈어·이탈리아어를 표기하고 다양한 PII 라벨의 예시를 제시한다. **한국어 검증 결과를 제공한다고 해석하지 않는다.** 한국형 라벨의 이름·설명·임계값은 A/B 합의와 독립 서비스 평가가 필요하다. [HF 모델 카드](https://huggingface.co/urchade/gliner_multi_pii-v1)

## 공식 API에서 확인한 동작

조사한 pin은 `predict_entities(text, labels, flat_ner, threshold, multi_label)`를 제공하고, 결과의 `start/end/text/label/score`를 원문 Python 문자열 위치와 슬라이스로 구성한다. `.bin` 로딩 분기는 `torch.load(..., weights_only=True)`를 사용한다. 로컬 디렉터리와 `local_files_only`를 지원하지만 tokenizer가 없으면 backbone 식별자를 참조할 수 있다. 이 소스 확인은 실제 M0 체크포인트의 로딩 성공 증거가 아니다. [고정 model.py](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/gliner/model.py)

Span processor는 단어 수가 `max_len`을 넘으면 경고 후 잘라내며 transformer tokenization에도 `truncation=True`를 사용한다. 따라서 16,000코드포인트를 한 번 호출하는 구현으로는 전체 검사를 보장할 수 없다. [고정 processor.py](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/gliner/data_processing/processor.py)

기본 whitespace splitter는 Python 정규식 매치 위치를 반환한다. 지원 splitter가 달라지면 원문 일치·누락 여부를 다시 확인해야 한다. [고정 tokenizer.py](https://github.com/urchade/GLiNER/blob/3e1723b49539d518f76d4d8282f1e05d8b6e74f0/gliner/data_processing/tokenizer.py)

## 구현한 합성 검증 경계

`GLiNERBackend(model, manifest)`는 동기 `warmup()`과 `inspect(segments)`를 제공한다. ModelManager의 동기 실행 경로가 별도 스레드에서 호출하도록 구성하며, 어댑터가 이벤트 루프에서 직접 실행되는 것은 허용된 배치 방식이 아니다. `inspect()`는 `BackendResult`를 반환하며 예열 전에는 거절한다.

1. B의 기존 `ModelPackage` 계약을 검증하고 비밀키 규칙 두 유형을 제외한 18개 canonical 유형의 매핑·임계값을 모두 요구한다. 테스트용 매핑은 합성값이며 A의 최종 매핑이 아니다.
2. 원문 splitter 결과의 순서·문자열·코드포인트 위치를 검증한다. 공백 이외의 누락이나 창에서의 재토큰화 불일치는 불완전 검사로 처리한다.
3. 실제 라벨 prompt를 포함한 tokenizer 입력을 `truncation=False`로 만들고 `word_ids()`로 모든 prompt·텍스트 단어가 살아 있는지 확인한다. `max_len`과 tokenizer의 유한한 `model_max_length`를 동시에 지킨다. 알 수 없는 tokenizer API나 매우 큰 sentinel 한도는 허용하지 않는다.
4. 인접 창은 **최소 `max_width-1`개의 단어를 겹친다**. 경계에 걸친 최대 `max_width` 단어 스팬이 어느 한 창에 전부 존재하도록 한다. 다음 창으로 진행할 수 없거나 전체 계획을 입증할 수 없으면 `incomplete`이며, 그 검사에서는 모델 호출을 시작하지 않는다. 순수 공백은 별도 처리한다.
   창 크기는 적합한 prefix를 이진 탐색하고, 모든 segment가 공유하는 적합성 검사 예산은 기본 2,048회다. 비단조 tokenizer에서 최적의 창을 찾는다고 보장하지 않으며, 찾은 창의 실제 적합성과 전체 범위를 확인할 수 없거나 예산을 넘으면 모델 호출 없이 전체 `incomplete`로 처리한다. 합성 역경계 입력에서 선형 축소의 과도한 CPU 호출을 재현한 뒤 이 예산을 추가했다.
5. 모든 segment를 먼저 계획한다. 창 예산도 검사 전체에 적용한다. 라벨별 최소 임계값으로 겹치는 예측을 요청한 뒤 각 유형 임계값을 적용하고, 원문 span/text 일치·라벨·점수 검증 후 중복은 최대 점수로 합친다. 잘라내기 경고나 잘못된 예측은 오류로 처리한다.

이 증명은 **현재 splitter 및 모델이 열거하는 최대 스팬 범위 내의 입력 커버리지**다. 모든 개인정보 탐지, `max_width`를 넘는 엔터티 검출, 창 바깥 문맥 보존이나 원래 문서와 동일한 점수를 보장하지 않는다. 단일 단어의 subtoken 수가 한도를 넘는 한국어 장문 등은 현재 불완전 검사로 차단될 수 있다.

`load_gliner(package_dir, manifest)`는 전체 해시 검증과 M0 revision 일치를 먼저 확인하고 `local_files_only=True`, `trust_remote_code=False`, `strict=True`로 호출한다. root의 `tokenizer_config.json`이 없거나 config가 remote-code 매핑을 요구하면 거절한다. 기본 로더는 위 GLiNER 버전과 일치해야 하며 로딩 직후 자동 readiness를 선언하지 않는다. 예열과 ModelManager의 재검증이 뒤따라야 한다.

## 검증 결과와 남은 실제 게이트

- TDD: 미구현 모듈 import 실패를 확인한 뒤 구현했다. 최초 합성 검사 17개 통과 후 경계를 확장해 **46개 통과**했다. 재실행 명령은 `rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest detector/tests/test_gliner_backend.py -q`다.
- 합성 검증에는 16,000코드포인트 전체 창 계획과 마지막 위치 탐지, 이모지·한국어 원문 위치, 경계 스팬·중복, 토큰 누락·재토큰화·큰 단일 단어의 불완전 결과, 개별 임계값·오류 정제, 로컬 로더 인수·라이브러리 pin 거절이 포함된다. 커버리지는 `.cache/coverage-gliner.json`에 기록한다.
- 실제 라이브러리 설치·환경 잠금은 parent 작업이다. 사용자 지침에 따라 **Conda 검색·설치 우선**, 필요한 경우 별도 승인된 fallback을 사용하고 fresh-process·잘못된 경로/버전·사용자 실행 경로를 검증해야 한다. 이 문서는 설치 완료를 주장하지 않는다.
- `gliner==0.2.29`는 소스로 API를 확인한 **호환성 후보**다. M0의 오래된 `markerV0` 설정, 현재 transformers/tokenizer 동작, GLiNER special token 처리, `word_ids`의 실제 반환과 총 입력 한도가 함께 호환되는지는 실제 패키지로 확인해야 한다.
- tokenizer/backbone 설정·가중치 의존성을 패키지에 포함하고 각각의 revision·해시·라이선스를 잠가야 한다. 캐시를 비운 fresh-process offline cold start로 숨은 Hub/cache 의존성이 없는지 확인해야 한다. `local_files_only`만으로 전체 의존성의 패키지 내 독립성을 증명했다고 보고하지 않는다.
- A/B 최종 라벨 설명·임계값, 독립 한국어 평가 및 교체 판정은 미합의/미검증이다. 원본 모델 카드와 라이선스 사본·추가 자산의 고지·패키징 변경 이력도 실제 배포물에 포함해야 한다.
- 실제 CPU/GPU 모델 로딩·예열·readiness, L4 메모리·CUDA·지연, 전체 16,000자·동시 요청, 실제 CLI 및 TLS 배포는 **미검증**이다. 수치 성능이나 한국어 품질 향상을 추정하지 않는다.
