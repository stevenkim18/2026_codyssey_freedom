# 한국어 분할 호환성 제안서 (Segmentation Proposal for A/B Alignment)

작성일: 2026-10-31 · 갱신일: 2026-11-01 · 상태: **A 오프라인 평가 및 8단계 B 운영 런타임(`dev/detector/gliner_backend.py`) 공동 채택(`b_splitter_adoption=True`) 완료**

7.5단계에서 [`runtime.py`](runtime.py)와 [`coverage_backend.py`](coverage_backend.py)를 통해 GLiNER 런타임(`data_processor.words_splitter`)과 창 계획 모두에 `ko_atomic_suffix_v3`를 연결한 데 이어, **8단계에서 B의 실제 운영 코드([`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py))에 `_KoreanWordsSplitter`(`ko_atomic_suffix_v3`)와 경계 안전 윈도우 폴백(`_whole_group_plan`)을 직접 이식(`b_splitter_adoption=True`)하고 GPU 서버(`NVIDIA L4`) 런타임 인수 검증(`p95=98.46ms <= 500ms`, `incomplete=0`)을 완료**했습니다([최종 보고서 v2](../report_v2.md), [Stage 8 런타임 인수](../reports/stage8-runtime-acceptance-v1.json)).

---

## 1. 문제 배경과 핵심 원인

- **원인**: 데이터 오류가 아닌 **표현 단위(Representation Unit)의 불일치**.
  - 기본 GLiNER 공백 분할기 정규식(`\w+(?:[-_]\w+)*|\S`)은 `김가상에게`를 단일 단어(`["김가상에게"]`)로 분할합니다.
  - GLiNER의 출력 구조는 단어 단위 span 인덱스(`[first_word_idx, last_word_idx, label]`)이므로, 단어 내부의 일부 글자(`[0, 3)` = `김가상`)만을 가리킬 수 없습니다.
  - `010-1234-5678로`, `192.168.1.1에서`, `PM6465889의`와 같이 한글 조사/접미사가 붙은 PII 엔터티 경계가 기본 분할과 맞지 않아 표현 불가(`unaligned_entity_boundary`) 처리됩니다.
  - 긴 URL이나 이메일의 경우, 기본 단어 분할기가 기호마다 단어를 잘게 쪼개어 M0의 최대 스팬 폭(`max_width=12`)을 초과(`entity_exceeds_max_width`)하여 문서 전체가 거절됩니다.

---

## 2. 해결 원칙 및 필수 준수 규칙

1. **원문만 보고 결정 (Gold-agnostic)**:
   - 분할기는 오직 원문 문자열 `text`만 입력받으며, gold 정답, BIO 태그, 문서 ID, 템플릿 정보를 일절 참조하지 않습니다.
   - 시그니처: `split(text: str) -> list[tuple[str, int, int]]` (끝 인덱스는 Python 표준 exclusive).
2. **원문 불변 및 무결성 보존**:
   - 텍스트 정규화(Unicode 정규화, 공백 압축, 철자 교정 등)나 정답 시작/끝 위치의 강제 조정을 일절 금지합니다.
   - 비공백 글자, 이모지(ZWJ 시퀀스 포함), 결합 문자(`\u0300` 등)가 1글자도 누락되거나 왜곡되지 않습니다.
   - 토큰 사이의 공백 외 텍스트 누락이 없음을 검증합니다: `text[start:end] == token`.
3. **학습과 추론의 동일 분할 규칙**:
   - 학습 데이터 생성 시 사용한 분할기와 B 파트의 추론 서빙 시 사용할 분할기가 동일해야 하며, 복원 규칙(`token_offsets`)도 완전히 일치해야 합니다.

---

## 3. 분할기 후보군 비교

| 후보 ID | 설명 | 핵심 메커니즘 | 장점 | 한계 및 주의점 |
|---|---|---|---|---|
| `whitespace_default_v1` | **(기존 B 기본값)** | `\w+(?:[-_]\w+)*\|\S` 정규식 | 기존 GLiNER 기본 설정 완벽 일치 | train 문서 보존율 1.08% (2,615건), 7개 유형 완전 소실 |
| `ko_particle_v1` | 한글 조사 분리 (v1) | 기본 분할 후 한글 음절 단어 뒤 수기 조사 목록 최장 일치 분리 | 한글 이름+조사 표현 가능 | 숫자/영문 뒤 조사(`010...로`) 미해결, 보존 10,132건 |
| `ko_script_particle_v1` | 문자종 전환 분리 + 조사 | 한글/비한글 전환 경계 분리 + 조사 분리 | 전화번호/계좌/영문 뒤 조사 해결, train 145,511건 | 호칭(`님`), 서술격조사(`인데`) 등 미해결, URL 스팬 초과 |
| `ko_script_suffix_v2` | 조사+호칭/어미 다중 분리 | v1 조사 + 13개 호칭/어미(`님`, `께서`, `인데` 등) 최대 3회 연속 분리 | train 201,274건 (82.6%) 보존, 정답 경계 불일치 대폭 해소 | URL 엔터티가 12단어를 초과하여 URL 보존율 0% |
| `ko_atomic_suffix_v3` | **(A 제안 권장안)** | URL/이메일을 원자적 단어(Atomic Word)로 보호 + v2 적용 | 긴 URL/이메일이 1단어로 유지되어 12단어 한도 초과 방지 | 비-URL 한글 복합어 과분할 가능성 검토 필요 |

---

## 4. B 파트 재사용 및 연동 인터페이스 제안

B 파트의 `GLiNERBackend`([gliner_backend.py](../../dev/detector/gliner_backend.py))를 수정할 때 사용할 수 있는 표준 어댑터 규격입니다:

### 4.1 의존성 및 패키지
- **외부 형태소 분석기(Mecab, KoNLPy 등) 의존 없음**: 순수 파이썬 표준 라이브러리(`re`)를 사용하며 추가 네이티브 패키지나 형태소 사전 설치가 필요하지 않습니다. 실제 Mac/Linux 환경의 Python·Unicode·분할 코드 버전을 기록하고 같은 사례로 검증해야 합니다.

### 4.2 공통 분할을 연결해야 하는 위치

기존 제안의 “backend `_tokens`만 교체” 예시는 제거했습니다. 그 방법은 창 계획만 바꾸고 GLiNER `predict_entities` 내부 분할은 기본값으로 남길 수 있습니다.

| 경로 | 같은 규칙을 적용할 위치 |
|---|---|
| 학습 | `tokenized_text`와 단어 span을 만드는 변환 planner |
| 모델 실제 예측 | `model.data_processor.words_splitter`를 사용하는 입력 분할과 위치 인덱스 생성 |
| backend 계획 | `_tokens`·`_plan`이 참조하는 같은 모델 분할기 |
| 문자 위치 복원 | 실제 예측에 사용된 토큰의 원문 코드포인트 offset |
| 저장본 재로딩 | 패키지 전처리 버전에 맞는 어댑터를 새 프로세스에서 다시 연결 |

공통 어댑터는 원문만 받아 `(token, start, end)`를 반환해야 합니다. 가중치 저장만으로 Python 분할 함수가 저장·재연결된다고 가정하지 않습니다. B와 합의한 로딩 인터페이스를 만들고 실제 모델 경로로 검증합니다. 후보 진단용 `SplitterPlanner`만의 성공은 이 조건의 완료 증거가 아닙니다.

### 4.3 위치 복원 (Offset Restoration)
- 분할기가 반환하는 각 토큰은 `(token, start_char, end_char)` 튜플입니다.
- 예측된 엔터티 `[first_word, last_word]`의 원문 위치는 다음과 같이 역산출됩니다:
  - `start = tokens[first_word][1]`
  - `end = tokens[last_word][2]`
- 위 관계는 학습·계획·실제 예측이 같은 토큰을 사용할 때의 복원 계약입니다. 조사·반복 문자열·이모지·URL·겹침 창 및 재로딩 사례에서 실제 예측 위치를 검증해야 합니다.

---

## 5. B 파트와 공동 합의해야 할 사항

1. **분할기 채택 승인**: `ko_atomic_suffix_v3` 또는 변형 규칙을 B의 실시간 추론 파이프라인에 도입할 것인지 여부.
2. **단어 수 증가에 따른 토큰 예산**: 분할 방식에 따라 단어·서브토큰·창 수가 달라집니다. 이전 제안의 15~25% 증가 주장은 실측 근거가 없어 확정 수치로 사용하지 않습니다. 실제 길이·창 수·지연을 측정합니다.
3. **B 운영 모델 패키지(`manifest.json`) 버전 갱신**:
   - `preprocessing.version`: `a-seg-candidate-ko_atomic_suffix_v3` 반영
   - A와 B가 동일한 패키지 스키마 및 전처리 버전을 공유하기 전까지는 배포를 진행하지 않습니다.


## 6. 진단 수치와 공동 채택 완료 기준

[진단 v2](../reports/segmentation-diagnosis-v2.json)의 권장 후보는 train 243,711건 중 236,900문서(97.21%) 변환, 고유 문장 233,724개입니다. 18종 모두 정답이 남아 있으나 일부 정답·문서는 거절됐습니다. 진단의 `a_b_adopted=false`, `training_ready=false`를 후보 코드 존재만으로 참으로 바꾸지 않습니다.

- [x] B와 공통 분할·위치 복원·패키지 버전을 합의합니다. *(8단계에서 [`dev/detector/gliner_backend.py`](../../dev/detector/gliner_backend.py) 및 [`m1-package.manifest.json`](../configs/m1-package.manifest.json)에 `preprocessing.version="ko_atomic_suffix_v3"`로 통합 완료, `b_splitter_adoption=True`)*
- [x] 학습/실제 예측의 분할 일치를 실제 GLiNER 로딩 및 재로딩 후 확인합니다. ([stage7_5-gpu-smoke-v1.json](../reports/stage7_5-gpu-smoke-v1.json))
- [x] 384단어·12단어 span·512서브토큰, 비공백 원문 커버리지·겹침 창을 검사합니다. ([stage7_5-coverage-audit-v1.json](../reports/stage7_5-coverage-audit-v1.json))
- [x] 창 계획 실패를 `incomplete`로 보존하고 평가기가 빈 탐지로 처리하지 않는지 검증합니다. (`test_evaluation_runner.py`, `test_coverage_backend.py`, `test_stage8_b_integration.py`)
- [x] 동일 채택 분할을 사용하는 M0/M1 비교와 기존 M0 역사적 결과를 구분합니다. ([report_v2.md](../report_v2.md), [stage8-service-final-v1.json](../reports/stage8-service-final-v1.json), [stage8-official-final-v1.json](../reports/stage8-official-final-v1.json))
- [x] 토큰·창 수 증가 및 B 운영 지연/메모리 영향을 실제로 측정합니다. *([stage8-runtime-acceptance-v1.json](../reports/stage8-runtime-acceptance-v1.json): `NVIDIA L4` 종단간 `p95=98.46ms <= 500ms`, 상시 GPU 메모리 `1,122MB`, `runtime_acceptance=True`)*
