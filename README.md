# AI PII Gateway

코디세이 해커톤 프리덤 팀의 **한국어 AI 양방향 개인정보 보호** 프로젝트입니다.

Codex 같은 AI 코딩 도구를 쓰다 보면 주민등록번호, 카드번호, API 키가 프롬프트나 파일 내용에 섞여 외부 LLM으로 나갈 수 있습니다. 이 프로젝트는 그런 값을 전송 전에 찾아 막고, 무엇을 막았는지 대시보드로 보여줍니다.

| 구성 | 위치 | 역할 |
|---|---|---|
| Codex 플러그인 훅 | `dev/launcher/plugin/` | 입력과 파일·명령 결과를 전송 전에 검사하고 차단 |
| 차단 사건 대시보드 | `dev/observability/` | 멈춘 전송과 운영 현황을 브라우저에 실시간 표시 |
| 게이트웨이 | `dev/gateway/` | 요청과 응답을 모두 검사한 뒤 전달하는 로컬 프록시 |
| 검사 API | `dev/detector/` | GPU 서버에서 탐지 모델을 실행 |
| 모델 학습·평가 | `model/` | 한국어 PII 탐지 모델 학습과 평가 |

## 빠른 시작: Codex 플러그인과 대시보드

가장 빠르게 동작을 확인하는 경로입니다. 설정 없이 규칙 검사 모드로 동작합니다.

### 1. 설치

필요한 것은 macOS, Git, [Codex CLI](https://github.com/openai/codex), `/usr/bin/python3`(Xcode 명령줄 도구)입니다. 검증한 Codex CLI 버전은 `0.166.0`입니다. Python 패키지는 설치하지 않습니다.

```zsh
git clone https://github.com/stevenkim18/2026_codyssey_freedom.git
cd 2026_codyssey_freedom
codex plugin marketplace add "$PWD"
codex plugin add pii-gateway@codyssey-security
```

### 2. 환경 설정

규칙 검사 모드는 설정이 필요 없습니다. 아래 항목은 선택 사항입니다.

| 설정 | 기본값 | 설명 |
|---|---|---|
| `~/.config/ai-pii-gateway/client.json` | 없음 | 있으면 검사 API를 함께 쓰는 모델 + 규칙 모드로 동작. 형식은 [플러그인 훅 가이드](docs/PLUGIN_HOOK_GUIDE.md) 참고 |
| `PII_DASHBOARD_PORT` | `8787` | 대시보드 포트. `off`면 자동 실행을 끔 |

### 3. 실행

```zsh
codex
```

처음 실행하면 **Hooks need review** 화면이 나옵니다. **2. Trust all and continue**를 선택하세요. 승인하지 않으면 훅이 동작하지 않아 입력이 검사 없이 전송됩니다.

첫 입력에서 `PII 보호 켜짐` 표시가 나오면 정상입니다. 대시보드는 자동으로 실행되며, 표시된 주소 `http://127.0.0.1:8787`을 브라우저에서 엽니다. 그다음 가상의 값으로 시험합니다.

| 입력 예시 | Codex 동작 | 대시보드 표시 |
|---|---|---|
| `주민번호 900101-1234567 확인해줘` | 전송 차단 | 차단 |
| `메일 user@example.com 로 보내줘` | 전송을 멈추고 가린 문장 안내 | 중단 |

자동 실행을 끈 경우에는 직접 실행할 수 있습니다.

```zsh
cd dev
python3 -m observability
```

## 게이트웨이 경로

플러그인 훅은 응답을 화면 표시 전에 막을 수 없습니다. 응답까지 전달 전에 검사하려면 게이트웨이를 사용합니다. 이 경로는 배포 아카이브, GPU 검사 API 주소와 토큰, OpenAI API 키가 필요하며 절차는 [macOS 설치 가이드](docs/USER_INSTALLATION_GUIDE.md)에 있습니다.

## 테스트

Python 3.12 환경에서 실행합니다. 패키지 목록은 `dev/environment.yml`에 있습니다.

```zsh
cd dev
python -m pytest observability/tests launcher/tests/test_plugin_hook.py -q
```

## 알려진 한계

- 플러그인은 응답이 화면에 표시된 뒤에만 검사합니다. 대시보드는 이를 `사후`로 구분하고 차단 수에 넣지 않습니다.
- 규칙 모드는 대표 형식만 찾습니다. 이름은 라벨이나 호칭이 붙은 경우만 찾고, 의도적으로 숨긴 값은 찾지 못합니다.
- 대시보드는 이 PC의 기록만 읽습니다. PRD의 중앙 사건 수집, 로그인, SSE, 오프라인 평가 표시는 구현하지 않았습니다.
- 화면의 건수는 운영 기록이며 탐지 정확도를 뜻하지 않습니다.
- 훅은 사용자 권한으로 실행되므로 강제 통제가 아닙니다. 사용자가 승인을 끄거나 플러그인을 제거할 수 있습니다.

항목별 검증 범위는 [플러그인 훅 가이드 §7](docs/PLUGIN_HOOK_GUIDE.md)에 있습니다.

## 문서

- [PRD](docs/AI_PII_Gateway_PRD_v1.md): 요구사항과 설계
- [플러그인 훅 가이드](docs/PLUGIN_HOOK_GUIDE.md): 보호 범위, 탐지 규칙, 실패 시 동작
- [대시보드 문서](dev/observability/README.md): 판정 기준과 운영 현황
- [macOS 설치 가이드](docs/USER_INSTALLATION_GUIDE.md): 게이트웨이 경로
- [모델 문서](model/model_README.md): 학습과 평가

## 팀 역할

| 담당 | 영역 |
|---|---|
| A | 모델 학습·평가 |
| B | 게이트웨이, 검사 API, 배포 |
| C | 대시보드 |
