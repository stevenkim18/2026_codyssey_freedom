# 3단계: 정답 위치를 GLiNER 학습 형식으로 바꾸기

**이 문서는 3단계 기본 분할 진단의 실행 안내입니다.** 기본 분할에서는 고유 train 2,615건만 정답을 모두 표현할 수 있어 학습 준비가 차단됐습니다. 이후 한국어 후보로 변환 가능량은 늘었지만 실제 GLiNER 예측 연동과 B 공동 채택은 미완료입니다. [3단계 이력](../reports/conversion-summary.md), [공통 분할 제안](../segmentation/README.md), [현재 7.5단계](../stage7_5.md)를 구분해 읽으세요.

아래 기본 변환 명령은 후보 분할을 실제 예측에 채택하는 명령이 아닙니다. 학습 예시의 분할·backend 창 계획·모델의 `data_processor.words_splitter`·문자 위치 복원을 함께 맞추고 재로딩 검증을 해야 합니다. 진단 부분집합을 정식 5k 또는 공식 시험셋으로 대체하지 않습니다.

## 무엇을 바꾸나요?

원본 정답은 글자 위치입니다. GLiNER 학습 정답은 단어 위치입니다.

```text
원문: 김가상 연락
원본 이름 위치: [0, 3) → 0번부터 3번 직전까지
단어 목록: ["김가상", "연락"]
GLiNER 이름 위치: [0, 0] → 처음과 마지막 단어 번호를 모두 포함
```

실제로 내보내는 한 예시의 형식은 다음과 같습니다.

```json
{
  "tokenized_text": ["김가상", "연락"],
  "ner": [[0, 0, "person"]],
  "ner_labels": ["person", "email address"]
}
```

위는 설명용 두 라벨 예시입니다. 실제 변환은 B의 임시 설명 매핑 18개를 고정 순서로 넣습니다. `ner_labels`는 원문 정답 문자열이 아니라 모델이 찾을 **개인정보 종류 목록**입니다. 원본의 `template_text`, `meta`, 기존 BIO 토큰은 모델 입력으로 넣지 않습니다.

## 정답을 맞출 수 없으면 어떻게 하나요?

`김가상에게`가 단어 하나로 나뉘면 모델의 단어 단위 정답은 전체 단어만 가리킬 수 있습니다. 이름 `김가상`만 가리키는 원본 정답과 맞지 않습니다.

- 정답을 조사까지 넓히지 않습니다.
- 정답을 보고 학습 자료에서만 단어를 쪼개지 않습니다. B의 실제 추론과 다른 기준이 되기 때문입니다.
- 이름 정답만 빼고 나머지를 정상 문장으로 학습시키지 않습니다.
- 그 문서 전체를 거절하고 사유를 기록합니다.

긴 문장을 나눈 창에도 같은 원칙을 적용합니다. 어느 창에 개인정보 일부만 남으면 그 창의 내용을 정답 없는 정상 자료로 내보내지 않습니다. 전체 원문과 정답이 보존된 문서만 진단용 출력으로 만듭니다.

## 기존 B 코드와 어떤 관계인가요?

새로운 운영 검사기를 만들지 않고 B의 `GLiNERBackend._tokens`, `_fits`, `_plan`을 재사용합니다. 실제 고정 GLiNER `0.2.29` processor와 DeBERTa 토크나이저를 사용합니다.

다음 제한을 함께 확인합니다.

- 한 창의 단어 수 최대 384개
- 정답 엔터티 하나의 최대 길이 12단어
- 라벨 설명·특수 토큰을 포함한 최대 512 서브토큰
- 서브토큰 계산 시 `truncation=False`로 단어가 사라지지 않는지 확인
- 창의 원문 위치와 재토큰화 결과가 일치하고, 모든 비공백 원문이 포함되는지 확인

기존 B 코드는 변경하지 않았습니다. 라벨 설명은 합의된 운영 설정이 아니라 [진단용 계획](../configs/conversion-plan.json)에 기록한 engineering 후보입니다.

## 실행 방법

현재 Mac에서 만든 전용 환경은 `model/.cache/conversion-venv`입니다. 로컬 CPU에서 토크나이저와 정답 텐서만 검사하며 신경망 가중치나 GPU 서버를 사용하지 않습니다.

다른 환경에서 재현하려면 Python 3.12 전용 가상환경을 만들고 [macOS arm64용 확인값 포함 패키지 목록](../configs/conversion-macos.lock.txt)을 사용합니다. **이 파일은 Linux/CUDA 서버 설치용이 아닙니다.** 서버 환경 계획과 별도로 보관합니다.

```bash
# 이미 준비한 전용 환경에서 작은 고정 토크나이저 파일 확인/준비
model/.cache/conversion-venv/bin/python -m model.data.prepare_tokenizer

# 기본 테스트: 실제 데이터·네트워크·GPU 없이 실행
model/.cache/conversion-venv/bin/python -m pytest model/tests -q

# 실제 토크나이저와 학습 배치 정답 텐서 검사
model/.cache/conversion-venv/bin/python -m model.data.verify_conversion_runtime \
  --tokenizer-dir model/.cache/tokenizer/a0484667b22365f84929a935b5e50a51f71f159d \
  --output model/reports/conversion-runtime-new.json

# 전체 문서 변환·거절 집계와 진단용 출력
model/.cache/conversion-venv/bin/python -m model.data.convert_dataset \
  --tokenizer-dir model/.cache/tokenizer/a0484667b22365f84929a935b5e50a51f71f159d \
  --output-dir model/.cache/conversion-new \
  --report model/reports/conversion-new.json
```

이미 있는 출력·보고서는 덮어쓰지 않습니다. 다시 실행하려면 새로운 이름을 지정합니다. 현재 기준 결과는 `conversion-v1.json`과 `conversion-runtime-v1.json`입니다.

`blocked_compatibility`와 종료 코드 1은 검사가 끝났으나 학습으로 넘어갈 조건을 충족하지 못했다는 뜻입니다. 입력·실행 오류의 종료 코드 2와 구분합니다.

## 출력 파일과 검사 범위

| 파일 | 내용 |
|---|---|
| `.cache/conversion-v1/<split>.diagnostic.jsonl` | 정답을 완전히 보존한 진단용 GLiNER 예시 |
| `.cache/conversion-v1/<split>.ledger.jsonl` | 원본 행별 변환·거절 상태, 창 위치·원문 역변환 정보 |
| `reports/conversion-v1.json` | 전체 문서·정답 개수, 유형별 불일치, 거절 사유, 실행 환경 |
| `reports/conversion-runtime-v1.json` | 실제 토크나이저·GLiNER 배치 처리기 검증 결과 |

진단용 예시는 정식 학습 표본이나 공식 시험셋을 대체하지 않습니다. 공식 분할과 원본 파일은 보존했고, 같은 분할 내 중복도 진단 출력에서 유지하며 고유 문장 수를 따로 셉니다.

공개 보고서에는 원문·개인정보 값·문서별 문장 해시를 넣지 않습니다. 실제 토큰이 들어 있는 예시는 Git 제외 캐시에만 둡니다. 전체 문서 수는 변환 수+거절 수와 일치해야 하고, 정답 개수도 같은 방식으로 누락 없이 집계합니다.

빠른 토크나이저의 byte-fallback 및 upstream 정규식 경고가 관측되었습니다. B의 기존 설정을 유지했으며, 임의의 자동 수정 플래그를 적용하지 않았습니다. 이번 검사는 위치·형식 검증이며 이러한 경고의 탐지 품질 영향은 별도 평가 대상입니다.
