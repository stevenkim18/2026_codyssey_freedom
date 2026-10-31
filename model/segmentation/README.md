# 한국어 분할 호환성 제안서 (Segmentation Proposal for A/B Alignment)

작성일: 2026-10-31 · 상태: **제안 및 후보 검증 단계 (A/B 공동 채택 전 운영 적용 금지)**

---

## 1. 문제 배경과 핵심 원인

- **원인**: 데이터 오류가 아닌 **표현 단위(Representation Unit)의 불일치**.
  - 기본 GLiNER 공백 분할기 정규식(`\w+(?:[-_]\w+)*|\S`)은 `김가상에게`를 단일 단어(`["김가상에게"]`)로 분할합니다.
  - GLiNER의 출력 구조는 단어 단위 span 인덱스(`[first_word_idx, last_word_idx, label]`)이므로, 단어 내부의 일부 글자(`[0, 3)` = `김가상`)만을 가리킬 수 없습니다.
  - `010-1234-5678로`, `192.168.1.1에서`, `PM6465889의`와 같이 한글 조사/접미사가 붙은 모든 PII 엔터티 경계가 동일한 이유로 표현 불가(`unaligned_entity_boundary`) 처리됩니다.
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
| `whitespace_default_v1` | **(기존 B 기본값)** | `\w+(?:[-_]\w+)*|\S` 정규식 | 기존 GLiNER 기본 설정 완벽 일치 | train 문서 보존율 1.08% (2,615건), 7개 유형 완전 소실 |
| `ko_particle_v1` | 한글 조사 분리 (v1) | 기본 분할 후 한글 음절 단어 뒤 47개 조사 최장 일치 분리 | 한글 이름+조사 표현 가능 | 숫자/영문 뒤 조사(`010...로`) 미해결, 보존 10,132건 |
| `ko_script_particle_v1` | 문자종 전환 분리 + 조사 | 한글/비한글 전환 경계 분리 + 조사 분리 | 전화번호/계좌/영문 뒤 조사 해결, train 145,511건 | 호칭(`님`), 서술격조사(`인데`) 등 미해결, URL 스팬 초과 |
| `ko_script_suffix_v2` | 조사+호칭/어미 다중 분리 | v1 조사 + 13개 호칭/어미(`님`, `께서`, `인데` 등) 최대 3회 연속 분리 | train 201,274건 (82.6%) 보존, 정답 경계 불일치 대폭 해소 | URL 엔터티가 12단어를 초과하여 URL 보존율 0% |
| `ko_atomic_suffix_v3` | **(A 제안 권장안)** | URL/이메일을 원자적 단어(Atomic Word)로 보호 + v2 적용 | 긴 URL/이메일이 1단어로 유지되어 12단어 한도 초과 방지 | 비-URL 한글 복합어 과분할 가능성 검토 필요 |

---

## 4. B 파트 재사용 및 연동 인터페이스 제안

B 파트의 `GLiNERBackend`([gliner_backend.py](../../dev/detector/gliner_backend.py))를 수정할 때 사용할 수 있는 표준 어댑터 규격입니다:

### 4.1 의존성 및 패키지
- **외부 형태소 분석기(Mecab, KoNLPy 등) 의존 없음**: 순수 파이썬 표준 라이브러리(`re`)만을 사용하므로 추가 네이티브 패키지나 대용량 사전 설치가 불필요하며, Mac/Linux 크로스 플랫폼에서 100% 동일하게 동작합니다.

### 4.2 인터페이스 구현 예시
```python
# B의 GLiNERBackend 내 _tokens 메서드 연동 제안
from model.segmentation.splitters import ko_atomic_suffix_v3

class GLiNERBackend:
    # ...
    def _tokens(self, text: str):
        # 기존: tokens = list(self.model.data_processor.words_splitter(text))
        tokens = ko_atomic_suffix_v3(text)
        return tokens
```

### 4.3 위치 복원 (Offset Restoration)
- 분할기가 반환하는 각 토큰은 `(token, start_char, end_char)` 튜플입니다.
- 예측된 엔터티 `[first_word, last_word]`의 원문 위치는 다음과 같이 역산출됩니다:
  - `start = tokens[first_word][1]`
  - `end = tokens[last_word][2]`
- 원문 위치가 단어 경계와 1:1로 정확히 일치하므로 복원 오차가 전혀 발생하지 않습니다.

---

## 5. B 파트와 공동 합의해야 할 사항

1. **분할기 채택 승인**: `ko_atomic_suffix_v3` 또는 변형 규칙을 B의 실시간 추론 파이프라인에 도입할 것인지 여부.
2. **단어 수 증가에 따른 토큰 예산**: 조사가 단어로 분리됨에 따라 한 문장의 총 단어 수가 기존 대비 약 15~25% 증가합니다. 이에 따라 윈도우 분할(`_plan`) 시 윈도우 수가 약간 증가할 수 있으므로 지연 시간 영향 검증이 필요합니다.
3. **B 운영 모델 패키지(`manifest.json`) 버전 갱신**:
   - `preprocessing.version`: `a-seg-candidate-ko_atomic_suffix_v3` 반영
   - A와 B가 동일한 패키지 스키마 및 전처리 버전을 공유하기 전까지는 배포를 진행하지 않습니다.

