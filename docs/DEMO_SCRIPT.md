# 시연 순서

목표 시간은 약 2분 55초입니다. 모든 값은 가상입니다.

## 1. 시작 폴더와 화면

- 터미널 하나만 씁니다. 어느 폴더에서 시작해도 됩니다. 2단계가 끝나면 `~/demo-install`로 이동합니다.
- 왼쪽에 터미널, 오른쪽에 브라우저를 둡니다.
- 브라우저 주소창에 `127.0.0.1:8787`을 입력만 해 두고 Enter는 누르지 않습니다.
- 터미널 글자를 크게 키웁니다.

| 폴더 | 용도 |
|---|---|
| `~/demo-install` | 플러그인을 내려받아 설치하는 곳 |
| `~/demo-shop` | Codex를 실행하는 곳, 시연용 로그가 있는 곳 |
| `~/codex-demo` | 시연 전용 Codex 환경, 로그인 정보가 있는 곳 |

## 2. 기존 것 지우기

리허설과 본 발표 직전마다 실행합니다. Codex가 켜져 있으면 Ctrl+C로 먼저 종료합니다.

```zsh
export CODEX_HOME=~/codex-demo
kill $(lsof -nP -t -iTCP:8787 -sTCP:LISTEN) 2>/dev/null
rm -rf ~/demo-install/2026_codyssey_freedom ~/codex-demo/plugins
rm -f ~/.codex/plugins/data/pii-gateway-codyssey-security/events.sqlite3{,-wal,-shm}
cp ~/codex-demo/config.base.toml ~/codex-demo/config.toml
cd ~/demo-install && clear
```

대시보드 기록은 시연 환경과 개인 Codex 환경에 따로 쌓이므로 둘 다 지웁니다. 개인 환경의 대시보드 기록도 함께 사라집니다.

이후 3단계까지 **같은 터미널**을 씁니다. 새 터미널을 열면 개인 Codex 환경으로 돌아갑니다.

## 3. 설치

> "로그인만 해 둔 빈 Codex입니다. 명령 몇 줄이면 설치가 끝납니다."

아래 5줄을 한 번에 붙여 넣고 Enter를 누릅니다.

```zsh
git clone https://github.com/stevenkim18/2026_codyssey_freedom.git
cd 2026_codyssey_freedom
codex plugin marketplace add "$PWD"
codex plugin add pii-gateway@codyssey-security
cd ~/demo-shop && codex
```

Codex가 켜지면 승인 화면 두 개를 넘깁니다.

1. `Folder access`: Enter
2. `Hooks need review`: `2`를 누르고 Enter

> "훅을 승인해야 보호가 켜집니다."

**`3`을 고르면 보호 없이 그대로 전송됩니다.**

## 4. 프롬프트 예시 5개

메모장에서 복사해 하나씩 붙여 넣습니다. 첫 번째 답변이 나오면 브라우저에서 Enter를 눌러 대시보드를 엽니다.

### 4-1. 정상 질문

```text
결제 실패 재시도 로직을 세 줄로 알려줘
```

- 화면: `PII 보호 켜짐`, 답변, 대시보드 숫자 0
- 대사: "평범한 질문은 그대로 갑니다."

### 4-2. 민감정보 단어만 있는 질문

```text
주민등록번호를 로그에 남기면 왜 위험한지 한 줄로 알려줘
```

- 화면: 답변, 대시보드 숫자 0 유지
- 대사: "단어가 아니라 실제 값만 봅니다."

### 4-3. 주민번호와 카드번호

```text
이 고객 결제가 왜 실패했는지 봐줘. 주민번호 900101-1234567, 카드 4111 1111 1111 1111
```

- 화면: `전송을 막았습니다: 주민등록번호 1건, 카드번호 1건`, **차단** 도장, 숫자 1
- 대사: "실제 값이 들어가면 서버에 가기 전에 이 PC에서 멈춥니다."

### 4-4. GPT·Claude API 키

```text
API 호출이 401로 실패해. 설정은 OPENAI_API_KEY=sk-proj-demo000000000000000000000000000000 이고 ANTHROPIC_API_KEY=sk-ant-api03-demo0000000000000000000000000000 이야
```

- 화면: `전송을 막았습니다: API 키·비밀번호 2건`, **차단** 도장, 숫자 2
- 대사: "개발자가 가장 자주 붙여 넣는 값입니다. 개인정보뿐 아니라 GPT와 Claude의 API 키도 전송 전에 막습니다."
- 위 문장의 가짜 키만 씁니다. 실제 키는 넣지 않습니다.

### 4-5. 이름

```text
김가상에게 보낼 결제 실패 안내 메일 써줘
```

- 화면: `[REDACTED]에게 보낼 결제 실패 안내 메일 써줘` 안내, **중단** 도장, 숫자 3
- 대사: "이름은 가린 문장으로 다시 보내도록 안내합니다."

## 5. 로그 읽기 요청

```text
logs/payment_error.log 읽고 결제 실패 원인을 한 줄로 알려줘
```

- 화면: `customer=[REDACTED] phone=[REDACTED]`, 카드 만료 답변, **가림** 도장, 숫자 4
- 대사: "그럼 붙여 넣지 말고 파일을 읽게 시키면 어떨까요? 이때도 AI는 고객 정보를 함께 보내게 됩니다. 답은 제대로 받았고, 이메일과 전화번호만 나가지 않았습니다."
- 화면의 `Blocked by hook`은 원래 결과를 막고 그 아래의 가린 내용으로 바꿨다는 뜻입니다. 설명할 때는 "이 내용이 LLM이 받은 전부입니다"라고 말합니다.
- `Show details`는 누르지 않습니다.

이어서 AI에게 직접 확인합니다.

```text
파일은 다시 열지 말고, 방금 받은 로그 기준으로 고객 이메일 주소가 뭐였는지 말해줘
```

- 화면: 가려져 있어 알 수 없다는 답변
- 대사: "AI는 이 값을 본 적이 없습니다."

## 6. 대시보드 보기

오른쪽 화면을 가리키며 마무리합니다.

- 큰 숫자: 원문 전송을 멈춘 횟수 4
- 사건 기록: 차단, 중단, 가림 도장
- 탐지 유형: 유형과 건수만 표시

> "보안 담당자는 원문을 보지 않고 무엇이 멈췄는지 확인합니다. AI 사용을 막지 않으면서, 나가면 안 되는 정보만 멈춰 세웁니다."

## 시간표

| 시간 | 내용 |
|---|---|
| 0:00–0:25 | 도입, 설치 |
| 0:25–0:35 | 승인 화면 |
| 0:35–1:50 | 프롬프트 예시 5개 |
| 1:50–2:35 | 로그 읽기 요청, AI에게 확인 |
| 2:35–2:55 | 대시보드 보기 |

2분으로 제한되면 AI에게 확인하는 질문과 4-2 순서로 뺍니다.

## 문제가 생기면

| 상황 | 대응 |
|---|---|
| 로그 파일을 못 찾음 | Codex를 종료하고 `cd ~/demo-shop && codex`로 다시 실행 |
| 대시보드가 안 열리거나 숫자가 안 움직임 | 2단계 초기화를 빠뜨린 경우. 시연 후 다시 초기화 |
| `Hooks need review` 화면이 안 뜸 | 개인 Codex 환경으로 켜진 경우. Codex를 종료하고 2단계부터 같은 터미널에서 다시 진행 |
| GitHub 접속 실패 | 미리 받아 둔 저장소로 이동해 `codex plugin marketplace add "$PWD"`부터 진행 |
| LLM 연결 실패 | 제출한 MP4로 전환 |

## 예상 질문

- **학습한 모델이 시연에 쓰였나요?** 이 시연은 규칙 검사 모드입니다. 학습한 모델은 GPU 검사 API에서 인수 시험을 통과했고, 플러그인 연결은 다음 단계입니다.
- **사용자가 플러그인을 끄면요?** 현재는 강제 통제가 아니라 실수를 막는 계층입니다. 강제 적용은 중앙 관리 단계의 과제입니다.
- **응답도 막나요?** 플러그인은 표시 뒤에 탐지해 기록하고 재요청합니다. 표시 전 검사는 게이트웨이 경로에서 합니다.
- **PC에 원문이 전혀 안 남나요?** 대시보드 기록에는 남지 않습니다. Codex 자체 세션 파일에는 명령 출력이 남습니다.

## 다른 PC에서 처음 준비할 때

이 PC는 이미 준비되어 있어 건너뜁니다.

```zsh
mkdir -p ~/codex-demo ~/demo-install ~/demo-shop/logs
cat > ~/codex-demo/config.base.toml <<'EOF'
model = "gpt-6.1-sol"
model_reasoning_effort = "low"
approval_policy = "never"
EOF
cat > ~/demo-shop/logs/payment_error.log <<'EOF'
2026-11-02 09:12:03 ERROR payment failed order=1042 code=CARD_EXPIRED customer=minsu.kim@example.com phone=010-1234-5678
2026-11-02 09:12:04 WARN retry skipped order=1042 reason=NO_VALID_CARD
EOF
git -C ~/demo-shop init -q
CODEX_HOME=~/codex-demo codex login
```
