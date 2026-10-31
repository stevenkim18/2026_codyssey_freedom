# M0 통합 준비 기록

확인일: 2026-10-31 UTC. 초기 조사·합성 어댑터 검증과 후속 실제 자산·CPU 검증을 기록한다. 최신 상태는 마지막 추가 검증 절을 따른다. 실제 GPU·한국어 품질·서비스 지연 검증은 미완료다.

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

`load_gliner(package_dir, manifest)`는 전체 해시 검증과 M0 revision 일치를 먼저 확인하고 `local_files_only=True`, `strict=True`로 호출한다. 실제 GLiNER가 지원하지 않는 `trust_remote_code` 옵션 대신 아래 추가 검증 절의 내장 설정 고정·경로 검증을 적용한다. root의 `tokenizer_config.json`이 없거나 config가 remote-code 매핑을 요구하면 거절한다. 기본 로더는 위 GLiNER 버전과 일치해야 하며 로딩 직후 자동 readiness를 선언하지 않는다. 예열과 ModelManager의 재검증이 뒤따라야 한다.

## 초기 합성 검증 이력과 당시 남은 게이트

- TDD: 미구현 모듈 import 실패를 확인한 뒤 구현했다. 최초 합성 검사 17개 통과 후 경계를 확장해 **46개 통과**했다. 재실행 명령은 `rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest detector/tests/test_gliner_backend.py -q`다.
- 합성 검증에는 16,000코드포인트 전체 창 계획과 마지막 위치 탐지, 이모지·한국어 원문 위치, 경계 스팬·중복, 토큰 누락·재토큰화·큰 단일 단어의 불완전 결과, 개별 임계값·오류 정제, 로컬 로더 인수·라이브러리 pin 거절이 포함된다. 커버리지는 `.cache/coverage-gliner.json`에 기록한다.
- 실제 라이브러리 설치·환경 잠금은 parent 작업이다. 사용자 지침에 따라 **Conda 검색·설치 우선**, 필요한 경우 별도 승인된 fallback을 사용하고 fresh-process·잘못된 경로/버전·사용자 실행 경로를 검증해야 한다. 이 문서는 설치 완료를 주장하지 않는다.
- `gliner==0.2.29`는 소스로 API를 확인한 **호환성 후보**다. M0의 오래된 `markerV0` 설정, 현재 transformers/tokenizer 동작, GLiNER special token 처리, `word_ids`의 실제 반환과 총 입력 한도가 함께 호환되는지는 실제 패키지로 확인해야 한다.
- tokenizer/backbone 설정·가중치 의존성을 패키지에 포함하고 각각의 revision·해시·라이선스를 잠가야 한다. 캐시를 비운 fresh-process offline cold start로 숨은 Hub/cache 의존성이 없는지 확인해야 한다. `local_files_only`만으로 전체 의존성의 패키지 내 독립성을 증명했다고 보고하지 않는다.
- A/B 최종 라벨 설명·임계값, 독립 한국어 평가 및 교체 판정은 미합의/미검증이다. 원본 모델 카드와 라이선스 사본·추가 자산의 고지·패키징 변경 이력도 실제 배포물에 포함해야 한다.
- 실제 CPU/GPU 모델 로딩·예열·readiness, L4 메모리·CUDA·지연, 전체 16,000자·동시 요청, 실제 CLI 및 TLS 배포는 **미검증**이다. 수치 성능이나 한국어 품질 향상을 추정하지 않는다.

## 2026-10-31 UTC 추가 검증: 실제 자산과 CPU cold start

아래 내용은 앞의 초기 조사 이후 실제로 수행한 범위를 추가한 기록이다. GPU·한국어 품질·서비스 성능·배포에 관한 미검증 상태는 유지한다.

- 공식 HF metadata를 다시 조회하여 M0 revision `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d`, backbone `microsoft/mdeberta-v3-base` revision `a0484667b22365f84929a935b5e50a51f71f159d`를 고정했다. [M0 metadata](https://huggingface.co/api/models/urchade/gliner_multi_pii-v1?blobs=true), [backbone metadata](https://huggingface.co/api/models/microsoft/mdeberta-v3-base?blobs=true)
- 공개 M0 가중치 **1,155,900,362 bytes**, SHA-256 `3003753fba99e40645cf088c7367a2c6211fc174897dc64f1f9c147c29d18d2d`와 backbone tokenizer `spm.model` **4,305,025 bytes**, SHA-256 `13c8d666d62a7bc4ac8f040aab68e942c861f93303156cc28f5c7e885d86d6e3`를 내려받아 HF LFS 메타데이터와 크기·해시가 모두 일치함을 확인했다. 별도 backbone 가중치는 필요하지 않아 다운로드하지 않았다. 총 다운로드는 5GiB 예산 안이다.
- 모든 원본·partial·패키지는 `dev/.cache/m0-assets/`의 SHA별 디렉터리에 보존했다. 기존 파일 삭제나 덮어쓰기는 하지 않았다. 모델 가중치는 Git 공유 대상이 아니다.
- `prepare_m0.build_package()`는 다운로드를 수행하지 않는 offline builder다. 고정 크기·LFS SHA-256 또는 Git blob SHA-1을 확인하고 새 출력 디렉터리만 생성한다. M0 설정에 고정 backbone `encoder_config`를 인라인하고, root `config.json`·`spm.model`·`tokenizer_config.json`을 함께 배치한다. tokenizer class는 내장 DebertaV2, 길이는 backbone 한도 512로 명시한다. 모델 카드·라이선스·변경 내역·출처·파일 해시를 manifest에 연결한다.
- 완전한 Apache-2.0 사본과 Microsoft MIT 사본을 포함한 로컬 후보는 `1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/package-engineering-02`다. MIT 사본 출처는 Microsoft DeBERTa commit `4d7fe0bd4fb3c7d4f4005a7cafabde9800372098`, SHA-256 `c2cfccb812fe482101a8f04597dfc5a9991a6b2748266c47ac91b6a5aae15383`다. [Microsoft 공식 LICENSE](https://github.com/microsoft/DeBERTa/blob/4d7fe0bd4fb3c7d4f4005a7cafabde9800372098/LICENSE)
- 18개 라벨 설명과 0.5 임계값은 **미합의 engineering 값**이다. 패키지 `evaluation.json`은 `status=not_evaluated`, `a_b_label_agreement=false`, `quality_gate=blocked`, `actual_gpu_verified=false`를 명시한다. 이 패키지를 평가 통과 후보로 인수하거나 배포 승인된 것으로 취급하지 않는다.

### 실제 API 불일치와 안전한 로딩 경계 수정

설치된 실제 GLiNER 0.2.29에서 `from_pretrained(..., trust_remote_code=False)`는 해당 옵션을 내부 모델 생성자에 전달하여 `TypeError`를 발생시켰다. 합성 loader가 임의 kwargs를 받던 초기 검증으로는 이 오류를 발견하지 못했다. 실제 지원 signature를 모방한 실패 테스트를 먼저 추가한 뒤 기본 라이브러리 호출에서 지원하지 않는 옵션을 제거했다.

옵션 제거와 함께 M0 전용 설정 경계를 강화했다. 고정 backbone ID, 원본 DeBERTa encoder 설정의 전체 값, M0의 주요 모델 치수, 내장 tokenizer class만 허용한다. 세 설정 JSON의 재귀 remote-code 매핑, attention/kernel 옵션, 파일·경로 override, auxiliary tokenizer 디렉터리, Python 파일, CWD의 잠재적 adapter 경로는 거절한다. 독립 검토에서는 실제 작은 DeBERTa 생성자의 Hub attention-kernel 경로를 sentinel로 재현하고 거절 회귀를 확인했다. 주입한 테스트 loader와 실제 GLiNER의 지원 인수는 구분한다.

### CPU에서 수행한 범위

새 프로세스에서 빈 별도 `HF_HOME`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 사용하고 socket 연결도 거절한 상태로 실제 로컬 후보를 읽었다. **UniEncoderSpanGLiNER / DebertaV2TokenizerFast / tokenizer 한도 512 / CPU**로 cold loading에 성공했다. 이후 메모리 확인을 거쳐 CPU 스레드를 2개로 제한하고 `warmup` 합성 문자열로 실제 `predict_entities`를 **1회** 실행했다. `warmed=true`, 네트워크 연결 시도 **0회**, 해당 프로세스 최대 RSS **3,362,996,224 bytes**를 확인했다.

단일 실행에서 로딩 17.352초·예열 8.999초를 관찰했다. 이는 초기화·CPU 조건의 한 번 측정이며 p50/p95, GPU 성능, PRD의 L4 검사 지연 목표 판정이나 한국어 정확도 평가가 아니다. 폰트 캐시를 처음 생성한 별도 cold-load에서는 총 30.609초가 관찰되어 초기화 환경 차이도 있다. 실제 tokenizer의 byte-fallback 및 regex 경고가 남아 있으므로 Unicode·장문 전체 범위와 품질 평가는 별도로 수행해야 한다.

Builder TDD는 미구현 import 실패에서 시작해 **17개 합성 테스트**를 통과했으며 라인·분기 커버리지 100%를 기록했다. 실제 로딩 signature 오류, 동적 attention 옵션, 과도한 모델 치수는 각각 실패 회귀를 확인하고 수정했다. 실제 CPU 결과가 있어도 L4/GPU·TLS·서버 배포·실제 CLI·16,000자 모델 추론·A의 최종 라벨/임계값 인수는 계속 미완료다.


## 2026-10-31 UTC 추가 검증: 실제 tokenizer·processor의 Unicode/장문 계획

`integration/verify_m0_tokenizer.py`는 실제 GLiNER 0.2.29의 `UniEncoderSpanProcessor`와 transformers 4.57.3의 `DebertaV2TokenizerFast`를 package-engineering-02에서 독립 생성한다. 가중치를 로드하지 않고 공개 `GLiNERBackend.warmup/inspect` 경로를 사용하되 예측 함수만 기록용 가짜 함수로 대체한다. **아래 complete는 창 계획 커버리지이며 개인정보 없음·모델 추론·예열·한국어 품질·GPU 검증을 뜻하지 않는다.** 모델의 특수 토큰은 설치된 BaseGLiNER와 같은 순서로 추가한다.

새 py312 프로세스·새 빈 HF_HOME·HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE·socket 연결 거절 조건에서 네트워크 시도 0회를 확인했다. 실제 설치 경로는 `/Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/lib/python3.12/site-packages/`다. 모든 complete 사례에서 원문 비공백 코드포인트가 창에 포함되고, splitter 기준 최대 12개 연속 단어 스팬이 적어도 한 창에 통째로 포함됨을 별도 oracle로 확인했다. 원문을 정규화하거나 변경하지 않는다.

| 입력 | 코드포인트 | 계획 상태 | 창/가짜 예측 호출 | 적합성 검사 |
|---|---:|---|---:|---:|
| 한국어 조사 | 21 | complete | 1 | 1 |
| 이모지·ZWJ | 16 | complete | 1 | 1 |
| 결합 부호 | 15 | complete | 1 | 1 |
| 분해된 한글 자모 | 14 | complete | 1 | 1 |
| 긴 단일 한국어 단어 | 14,000 | incomplete | 0 | 1 |
| 한국어·숫자·이모지 장문 | 16,000 | complete | 18 | 154 |
| 두 segment 합계 | 16,000 | complete | 22 | 138 |
| 900개 단어 경계 | 7,089 | complete | 5 | 37 |
| 입력 한도 초과 | 16,001 | rejected_input_limit | 0 | 0 |

모든 사례의 적합성 검사는 2,048회 이하였다. incomplete와 입력 거절에서 가짜 예측도 0회다. 경계 oracle 추가 후 실제 실행은 총 25.21초·최대 RSS 1,287,094,272 bytes(macOS ru_maxrss)를 관찰했다. 개별 계획 사례는 최대 0.195초였으나 실제 신경망 추론이나 서비스 지연 측정값이 아니다.

byte-fallback 경고는 실제 차이로 확인했다. 분해 한글 입력은 fast tokenizer에서 UNK 2개, 동일 자산의 실제 slow `DebertaV2Tokenizer`에서 UNK 0개였다. 다른 위 사례는 둘 다 UNK 0개다. 창/word ID 커버리지는 토큰의 표현 품질을 보장하지 않는다. tokenizer 교체나 정규화는 수행하지 않았고, A와의 한국어 품질 검증 게이트는 계속 미완료다.

regex 경고는 설치된 transformers `tokenization_utils_base.py`의 `_patch_mistral_regex` 경로를 확인했다. 어휘 100,000 초과인 local fast tokenizer에 config.json이 있고 transformers_version이 없으면 model_type이 deberta-v2여도 `mistral_config_detected=True`가 된다. 따라서 이 경고 자체는 실제 Mistral regex 결함의 증거가 아니다. fix_mistral_regex 옵션이나 tokenizer 설정은 변경하지 않았다. 장문 길이 경고는 truncation=False로 한도를 조사한 결과이며 해당 긴 단일 단어는 생산 어댑터가 모델 호출 전에 incomplete 처리했다.

재현(작업 디렉터리 dev, --output은 새 파일이어야 함):

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m integration.verify_m0_tokenizer .cache/m0-assets/1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/package-engineering-02 --output .cache/m0-tokenizer-evidence.json
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest integration/tests/test_m0_tokenizer_probe.py -q
```

oracle TDD는 미구현 import RED 후 최초 4개 GREEN이었다. 독립 검토에서 Python 최적화 옵션으로 검사가 사라지고 실패 시 socket·환경변수가 복원되지 않는 결함을 재현했다. 명시적 검사·finally 복원·사례별 기대 상태를 추가하여 RED 17개에서 **22개 GREEN**으로 검증했고, 독립 리뷰어도 같은 22개를 통과했다. 비공백 누락·공백 간격·불완전 검사 중 예측 호출·계획 예산 초과와 전체 사례가 incomplete로 퇴행하는 경우를 검출한다.

수정 후 새 프로세스의 실제 자산 검증은 9개 기대 상태를 모두 통과했다. 네트워크 시도 0회·24.3096초·최대 RSS 1,288,257,536 bytes를 관찰했다. 실제 자산 probe는 별도의 수동 통합 검증이며 합성 unit test의 커버리지 수치로 대체하지 않는다. native dependency가 stdout에도 메시지를 출력할 수 있어 기계 판독 증거는 --output JSON 파일을 사용한다. 모든 기존 캐시와 자산을 보존했다.


## 2026-10-31 UTC 추가 검증: 재사용 가능한 실제 CPU ASGI runtime probe

`integration/verify_m0_runtime.py`는 `ServerSettings.from_env → create_server → 기본 load_gliner → ModelManager`와 실제 ASGI lifespan을 사용한다. HTTPX ASGITransport로 외부 listener 없이 health·readiness·인증·검사 HTTP 계약을 검증한다. CLI는 CPU/CUDA를 명시적으로 선택하며 실제 가중치를 한 번 로드한다. 검증 전용 가짜 모델로 대체하지 않는다. CUDA 실행 경로는 구현했으나 아래에서 실행한 장치는 CPU뿐이다.

독립 메모리/프로세스 점검 후 새 py312 프로세스 하나에서 CPU 스레드 2개, 새 빈 HF_HOME, offline 환경변수, socket 연결 거절 상태로 실행했다. 기본 서비스의 검사 제한 10초는 변경하지 않았다. package-engineering-02의 실제 가중치 로드·예열 후 다음을 확인했다.

- 초기 `/readyz` 503 → 실제 lifespan 초기화 후 200. `/healthz` 200.
- 잘못된 Bearer 토큰은 401과 정제된 `unauthorized` 본문만 반환한다.
- `public demonstration` 및 원문 그대로의 `공개 예시 é 👩‍💻` 두 요청 모두 HTTP 200·complete. 모델/규칙 finding은 모두 0개였다. 이 합성 예시의 무탐지는 탐지 품질 증거가 아니다.
- 응답 ID, schema, 모델/임계값 버전, 유한한 timing, finding이 있을 경우 원문 코드포인트 범위를 검증한다. 이번 실제 요청은 finding 0개이므로 **실제 양성 span 정확도를 검증한 결과는 아니다**. 양성/잘못된 span oracle은 합성 테스트로만 검증했다.
- 실제 모델 parameter 장치는 CPU였고 `gpu_verified=false`, 네트워크 연결 시도 0회였다.
- 모델 버전 `M0-1fcf13e85f4e-engineering`, 임계값 버전 `engineering-0.5-not-evaluated-v1`. A/B 최종 라벨·임계값 합의와 품질 평가는 미완료다.

관찰값은 startup 39.8784초, 전체 59.1847초, 최대 RSS 3,303,424,000 bytes(macOS), 실제 두 검사 8,773.68ms 및 9,355.45ms였다. 두 요청이 10초 안에 끝났지만 여유가 작으며, 단일 CPU 관찰값은 p95·L4 성능·서비스 SLO 판정이 아니다. 실제 16,000자 신경망 검사, 동시 요청 부하, CUDA/L4, 서버 간 TLS 및 외부 CLI 배포는 수행하지 않았다. byte-fallback/regex 경고는 앞 절과 동일하게 남아 있다.

증거 파일은 Git 제외 경로 `dev/.cache/m0-runtime-cpu-evidence-01.json`이며 stdout/stderr는 같은 디렉터리의 `m0-runtime-cpu-01.log`, `m0-runtime-cpu-01.stderr`에 보존했다. 파일에는 원문이나 finding 본문, 인증 토큰을 저장하지 않고 상태·개수·버전·시간·자원값을 기록한다. 기존 증거를 덮어쓰지 않는다. 실패 시 정제된 오류 코드로 종료하며 정상 증거로 처리하지 않는다. 종료/실패 모두 환경변수·socket 메서드·CPU thread 설정을 복원한다.

재현(작업 디렉터리 dev, 출력은 새 경로 사용):

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m integration.verify_m0_runtime .cache/m0-assets/1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d/package-engineering-02 --device cpu --output .cache/m0-runtime-cpu-evidence-02.json
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest integration/tests/test_m0_runtime_probe.py -q
```

TDD: 미구현 import 실패 → 22개 합성 테스트 통과. 잘못된/불완전 응답, stale ID/버전, 범위 밖 Unicode span, 비유한 timing/score, API timeout, Python -O 검증 유지, 환경/socket 복원, 정상/실패 ASGI lifespan을 검증한다. 별도 검토자의 22개 테스트 재실행도 통과했다. 합성 테스트와 위 실제 CPU 실행은 구분한다.
