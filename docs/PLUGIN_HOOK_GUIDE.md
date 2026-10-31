# Codex 플러그인 훅 사용 가이드

평소 Codex를 쓰면서 민감정보가 **입력이나 파일·명령 결과를 통해** LLM으로 가는 것을 전송 전에 막는 플러그인이다. 설치 후 훅을 승인해야 동작한다. 응답을 화면 표시 전에 막으려면 [게이트웨이 전용 경로](USER_INSTALLATION_GUIDE.md)를 쓴다.

## 1. 보호 범위

| 경로 | 동작 | 사전 차단 |
|---|---|---|
| 사용자 입력 | 주민번호·카드·키는 차단, 이메일·전화 등은 가린 문장을 안내하고 전송 중단 | 가능 |
| 파일·명령 결과 | 차단 유형은 고정 안내문으로, 마스킹 유형은 가린 내용으로 대체 | 가능 |
| LLM 응답 | 표시 후 탐지하면 사건 기록과 함께 가린 답변을 1회 재요청 | **불가** |

Codex 훅에는 입력이나 응답을 고치는 기능이 없다. 그래서 입력은 자동으로 가려서 보낼 수 없고, 응답은 원문이 먼저 표시된다. 도구 결과 차단도 이미 실행된 명령을 되돌리지 못한다.

**검사하지 않는 경로:** AGENTS.md, 메모리, 웹 검색, 이미지, 첨부, 훅이 지원되지 않는 도구.

## 2. 탐지 방식

| 모드 | 조건 | 탐지 |
|---|---|---|
| 규칙 검사 | 설정 파일 없음 | 아래 표의 대표 형식 |
| 모델 + 규칙 | 설정 파일 있음 | 대표 형식 + GPU 검사 API의 탐지 유형 |

| 규칙 대상 | 탐지 조건 | 처리 |
|---|---|---|
| 주민번호 | 하이픈·공백 형식. 붙여 쓴 13자리는 앞에 "주민번호" 등이 있을 때만 | 차단 |
| 카드번호 | Luhn 검증 통과. 붙여 쓴 번호는 앞에 "카드" 등이 있을 때만 | 차단 |
| API·개인 키 | OpenAI, GitHub, AWS, Slack, Google 형식, PEM 머리글 | 차단 |
| 비밀번호 | `password=값`, `비밀번호: 값`처럼 값이 함께 있고 숫자나 기호 포함 | 차단 |
| 전화, 이메일, 운전면허, 차량번호, 도로명 주소 | 형식 일치 | 마스킹 안내 |
| 계좌, 여권, 건강보험, 생년월일 | 앞에 "계좌", "여권" 등 관련 단어가 있을 때 | 마스킹 안내 |
| 이름 | `이름: 김가상`처럼 라벨이 있거나 `김가상에게`, `홍길동 과장님`처럼 호칭·조사가 붙은 세 글자 | 마스킹 안내 |

전각 숫자, 긴 대시, 줄바꿈으로 나뉜 값도 찾는다. 규칙은 대표 형식만 다루므로 **이름은 위 형식이 아니면 놓치고**, 드물게 정상 문장을 이름으로 오인할 수 있다. Base64, 한글 숫자, 글자 사이 공백처럼 의도적으로 숨긴 값은 찾지 못한다. 모델 모드의 설정은 `~/.config/ai-pii-gateway/client.json`(권한 `0600`)을 읽는다.

```json
{
  "detector_url": "https://검사서버:8766/v1/inspect",
  "detector_token": "운영자가 제공한 토큰",
  "detector_ca_file": "/absolute/path/detector-ca.pem"
}
```

설정이 있는데 검사 서버에 연결할 수 없으면 입력과 도구 결과를 차단한다. 모델 모드는 한 번에 16,000 코드포인트까지만 검사하므로, 더 긴 파일 내용은 잘라서 보내지 않고 차단한다.

## 3. 설치

Python이나 패키지 설치는 필요 없지만 macOS의 `/usr/bin/python3`(Xcode 명령줄 도구)가 있어야 한다. 없으면 입력이 모두 차단된다.

```zsh
codex plugin marketplace add /absolute/path/to/2026_codyssey_freedom
codex plugin add pii-gateway@codyssey-security
codex
```

`codex`를 실행하면 **Hooks need review** 화면이 나온다. 화살표 키로 **2. Trust all and continue**를 고르고 Enter를 누른다. 첫 질문에서 `PII 보호 켜짐` 표시가 나오면 정상이다.

> **이 표시가 없으면 보호되지 않는 상태다.** "Continue without trusting"을 고르거나 승인 전에 `codex exec`를 쓰면 훅이 실행되지 않고 내용이 그대로 전송된다. 플러그인 내용이 바뀌면 승인이 무효가 되어 시작 화면이 다시 나오므로 다시 승인한다. 승인하지 않고 넘어가면 화면에 `Hook failed`가 표시되고 내용은 그대로 전송된다.

제거:

```zsh
codex plugin remove pii-gateway@codyssey-security
codex plugin marketplace remove codyssey-security
```

## 4. 상태 확인

```zsh
/usr/bin/python3 "$HOME"/.codex/plugins/cache/codyssey-security/pii-gateway/*/hook_runtime/main.py --doctor
```

`self_test`와 `journal_writable`이 `true`면 검사기와 기록이 동작한다. `trust_recorded`는 승인 기록의 존재만 뜻하며, 현재 훅과 일치하는지는 Codex의 시작 화면으로 확인한다.

## 5. 실패 시 동작

Codex는 훅이 오류, 잘못된 출력, 시간 초과로 끝나면 내용을 **그대로 전송한다.** 플러그인은 이를 다음처럼 보완한다.

| 실패 | 처리 |
|---|---|
| 검사 오류, 검사 서버 장애, 사건 기록 실패 | 차단 |
| Python 실행 실패, 비정상 종료 | `run.sh`가 차단 코드로 변환 |
| 처리 지연 | 25초에 내부 시한으로 차단(Codex 제한 30초) |
| 훅 프로세스 자체가 시작되지 못하거나 Codex가 먼저 종료 | **보호 불가** |

훅은 사용자 권한으로 실행되며 네트워크 강제 통제가 아니다. 사용자가 승인을 끄거나 플러그인을 제거할 수 있다.

## 6. 사건 기록

`~/.codex/plugins/data/pii-gateway-codyssey-security/events.sqlite3`에 게이트웨이와 같은 18필드로 기록한다. 원문은 저장하지 않고 정책 버전은 `plugin-hook-v1`이다. 응답에서 표시 후 탐지된 경우 `error_code=POST_DISPLAY_FINDING`이므로 사전 차단으로 집계하면 안 된다. 사건은 자동 삭제되지 않고 저널 상한 100MiB에 도달하면 입력이 차단되며, 중앙 전송은 아직 연결하지 않았다.

## 7. 검증 범위 (2026-10-31)

| 항목 | 결과 |
|---|---|
| 훅 단위·실패 경로·탐지 규칙 102개 | 통과. 실제 `/bin/sh`와 시스템 Python 3.9.6으로 실행 |
| Codex CLI 0.166.0 대화형 | 격리 환경과 모의 LLM으로 승인, 차단, 파일 대체, 응답 사후 처리 확인 |
| `codex exec` | 동작하지만 차단 사유가 표시되지 않음. 시연은 대화형을 사용 |
| 실제 ChatGPT 로그인과 실제 LLM | 미검증 |
| ChatGPT 데스크톱(Codex) | 미검증 |
| 실제 GPU 검사 API | 미검증. 로컬 TLS 모의 서버로만 확인 |

테스트: `dev`에서 `python -m pytest launcher/tests/test_plugin_hook.py -q`.
