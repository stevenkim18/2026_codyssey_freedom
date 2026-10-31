# macOS 사용자 설치·실행 가이드

사용자 PC에 **Python·Conda·pip를 설치하지 않고** 실행하는 macOS ARM64 배포판의 절차다. 패키지에 Python 실행 환경과 게이트웨이 의존성을 함께 넣는다. Codex CLI는 별도로 준비해야 한다. 터미널 앱이며, GUI 앱이나 단일 파일 앱이 아니다.

> ARM64 번들(약 77MiB)을 실제 빌드했고, 격리된 macOS 새 프로세스에서 설치·기동·실제 Codex 요청 차단 등 12개 검사를 통과했다. **공개 릴리스 URL은 아직 없다.** 아래 명령은 운영자가 검증한 아카이브와 SHA-256을 제공한 뒤 실행한다. 새 Mac의 공개 다운로드·Gatekeeper 검증과 실제 OpenAI 전체 왕복은 미완료다. 배포 상태는 [운영자 가이드](MAC_DISTRIBUTION_GUIDE.md)를 확인한다.

## 1. 준비물과 보호 범위

- Apple Silicon Mac(ARM64), zsh 터미널. Intel Mac·Windows·Linux용 바이너리는 제공하지 않았다. 현재 빌드 호스트 외 macOS 버전 호환성은 별도 검증 대상이다.
- PATH에서 실행되는 승인된 **Codex CLI 0.166.0**. `codex --version` 출력이 정확히 `codex-cli 0.166.0`이어야 한다. 다른 버전은 거부한다.
- 운영자가 전달한 배포 아카이브 또는 HTTPS 다운로드 URL과 **별도 신뢰 경로로 확인한 SHA-256**.
- 운영자가 검증한 GPU 검사 API의 HTTPS origin, 검사 토큰, 필요 시 CA 인증서 PEM 파일. PC에서 검사 서버로 연결할 네트워크 경로도 필요하다.
- 본인의 OpenAI API 키와 계정에서 사용 가능한 모델 ID.

사용자 PC의 게이트웨이는 질문 원문을 **원격 GPU 검사 서버에 HTTPS로 전송**한다. 검사 후 허용·마스킹한 요청만 PC에서 OpenAI로 전달하고, 응답도 검사 후 표시한다. OpenAI 키는 사용자 PC에서 관리하며 검사 서버에 보내지 않는다. 외부 API 사용량·비용은 본인 계정에 발생할 수 있다.

일반 서버의 사건 수집 API·대시보드는 아직 배포하지 않았다. 없어도 로컬 사건 저널을 사용해 보호 요청을 처리하지만, 중앙 대시보드 연동까지 완료된 상태는 아니다. M0 API 운영 성공은 한국어 탐지 품질 승인이나 A의 학습 모델 인수와도 다르다.

## 2. 설치 방법 선택

Git 저장소의 Codex 플러그인을 통해 설치 도우미를 받거나, 운영자가 제공하는 동일 아카이브를 직접 받는다. 두 방법 모두 최종적으로 같은 `pii-gateway` 실행 폴더를 설치한다. 사용자에게 Python 패키지를 설치시키는 절차는 없다.

### 방법 A: Git 저장소의 플러그인 사용

운영자가 플러그인 포함 버전을 게시한 뒤 제공한 저장소 주소를 입력한다.

```zsh
read "MARKETPLACE_SOURCE?운영자가 제공한 marketplace Git 저장소 주소: "
codex plugin marketplace add "$MARKETPLACE_SOURCE" --ref main
codex plugin add pii-gateway@codyssey-security
codex plugin list
unset MARKETPLACE_SOURCE
```

새 Codex 세션에서 플러그인 목록에 `pii-gateway`가 설치·활성 상태로 보이는지 확인한다. `/plugins` 또는 플러그인 관리 화면에서도 확인할 수 있다. 위 marketplace 등록→설치→새 프로세스 목록→제거는 실제 CLI와 격리 HOME에서 검증했다. 모델 세션에서 설치 도우미가 실제 활성화되는 단계는 아직 검증하지 않았다. 보호 요청용 CLI 0.166.0 검증과 플러그인 호스트의 설치 지원 여부는 별개다.

설치 도우미 `install-pii-gateway`를 실행한다. 아카이브 위치 또는 HTTPS URL, 운영자가 확인한 SHA-256을 제공한다. **OpenAI 키·검사 토큰·질문 원문은 플러그인 대화에 입력하지 않는다.** 키 입력은 아래 로컬 `configure` 단계에서 한다.

플러그인 훅은 승인 후 평소 Codex의 입력과 파일·명령 결과를 전송 전에 검사하지만, 응답을 표시 전에 막거나 입력을 고쳐 보내지는 못한다. 범위와 승인 절차는 [플러그인 훅 가이드](PLUGIN_HOOK_GUIDE.md)를 따른다. 응답까지 사전 검사가 필요한 요청은 아래 전용 실행 명령을 사용한다.

### 방법 B: 아카이브를 직접 설치

소스 체크아웃이 있으면 설치 스크립트의 절대 경로를 사용한다. Python 환경이나 전체 소스 빌드는 필요 없다. 체크아웃이 없으면 운영자에게 동일한 검토된 `install-macos.sh` 파일을 별도로 받는다. 스크립트 자체의 출처도 확인하며, 원격 스크립트를 `curl | sh`로 실행하지 않는다.

```zsh
read "INSTALLER?검토된 install-macos.sh 절대 경로: "
read "ARCHIVE?운영자가 제공한 tar.gz 절대 경로: "
read "EXPECTED_SHA256?별도 확인한 SHA-256 (64자리): "
/bin/bash "$INSTALLER" --archive "$ARCHIVE" --sha256 "$EXPECTED_SHA256"
```

운영자가 일반 서버의 정적 HTTPS 다운로드 또는 검증된 릴리스 URL을 제공했다면 **위 아카이브 설치 명령 대신** 다음 명령으로 받으면서 설치할 수 있다. 두 설치 명령 중 하나만 사용한다. 사건 수집 API가 없어도 정적 파일 배포는 가능하지만, 실제 URL 게시가 먼저 필요하다.

```zsh
read "DOWNLOAD_URL?운영자가 제공한 HTTPS 아카이브 URL: "
/bin/bash "$INSTALLER" --url "$DOWNLOAD_URL" --sha256 "$EXPECTED_SHA256"
unset DOWNLOAD_URL EXPECTED_SHA256 ARCHIVE INSTALLER
```

기본 설치 위치는 `~/Library/Application Support/AI PII Gateway/`이다. 임의 위치는 `--destination /absolute/path`로 지정한다. 실행 파일만 복사하지 말고 **같은 폴더의 포함 런타임·라이브러리 전체**를 유지한다.

현재 엔지니어링 배포판은 Apple Developer ID 서명·공증이 완료되지 않았다. 다운로드한 새 Mac에서 Gatekeeper에 차단될 수 있다. `xattr` 제거·Gatekeeper 비활성화로 우회하지 말고 운영자에게 서명·공증된 배포판을 요청한다. 로컬 빌드 실행 성공이 다운로드 설치 성공을 보장하지 않는다.

## 3. 실행 파일·Codex 버전 확인

기본 위치에 설치했다면 터미널에서 다음을 실행한다. 사용자 지정 위치를 썼다면 `APP`만 바꾼다.

```zsh
export APP="$HOME/Library/Application Support/AI PII Gateway/pii-gateway"
"$APP" version
codex --version
```

패키지 버전 출력은 `{"name": "pii-gateway", "version": "0.1.0-engineering.1"}` 형식이다. 이는 실행 파일이 시작됐다는 증거이며, GPU·OpenAI 연결 성공을 뜻하지 않는다. Codex 출력은 정확히 `codex-cli 0.166.0`이어야 한다.

## 4. 비밀 설정 입력

```zsh
"$APP" configure
```

순서대로 다음을 입력한다.

1. **Detector HTTPS origin:** 운영자가 확인한 `https://호스트[:포트]`. `/v1/inspect` 경로는 프로그램이 붙이므로 입력하지 않는다.
2. **OpenAI model:** 본인 계정에서 사용 가능한 모델 ID. 기본값은 `gpt-6.1-sol`이다.
3. **Detector CA PEM absolute path:** 사설 CA라면 승인된 PEM 파일의 절대 경로. 공인 CA면 Enter. 서버 개인 키를 넣지 않는다.
4. **Detector bearer token:** 운영자가 제공한 GPU 검사 토큰. 입력은 화면에 표시하지 않는다.
5. **OpenAI API key:** 본인의 API 키. 입력은 화면에 표시하지 않는다.

성공 시 `{"status": "configured", "remote_detector": "unverified"}`가 출력된다. 원격 호출은 아직 하지 않는다. 로컬 인증 토큰은 자동 생성한다. 기본 설정 파일은 `~/.config/ai-pii-gateway/client.json`, 디렉터리 권한은 `0700`, 파일 권한은 `0600`이다. 설정에는 실제 키가 있으므로 공유·Git 추가·화면 출력하지 않는다. 기존 파일이 있으면 덮어쓰지 않는다.

CA는 승인된 공개 인증서 파일이어야 하며 절대 경로·소유권·상위 경로·심볼릭 링크·타인 쓰기 권한을 검사한다. CA 파일 `0644`는 허용하지만 검증 해제는 지원하지 않는다. 운영자에게 인증서 호스트명까지 확인받는다.

별도 설정 위치를 쓰려면 **모든 명령에서 하위 명령 앞에** 같은 절대 경로를 넣는다.

```zsh
"$APP" --settings /absolute/private/path/client.json configure
"$APP" --settings /absolute/private/path/client.json install
```

## 5. 보호 프로필 설치

```zsh
"$APP" install
"$APP" doctor
```

설치 성공 시 `{"status": "installed", "cli_version": "0.166.0"}`가 출력된다. 기본 전용 프로필 위치는 `~/.local/share/ai-pii-gateway/codex-home`이다. 기존 전역 Codex `config.toml`은 수정하지 않는다.

`doctor`의 `installed`, `profile_verified`, `cli_verified`가 `true`인지 확인한다. 네트워크 검사는 아직 `unverified`다. 프로필·번들 지침 파일을 직접 수정하거나 설치 후 앱 폴더를 이동하면 무결성 검증이 실패할 수 있다.

## 6. 두 터미널에서 실행

**터미널 1**에서 게이트웨이를 시작하고 그대로 열어 둔다.

```zsh
export APP="$HOME/Library/Application Support/AI PII Gateway/pii-gateway"
"$APP" serve
```

게이트웨이는 `127.0.0.1:8765`에서만 수신한다. 정상 실행 시 터미널을 점유하며 기본 로그 수준 때문에 시작 메시지가 없을 수 있다. 자동 시작·백그라운드 서비스 등록은 수행하지 않는다.

**터미널 2**에서 진단 후 짧은 합성 질문을 실행한다.

```zsh
export APP="$HOME/Library/Application Support/AI PII Gateway/pii-gateway"
"$APP" doctor --probe
"$APP" run -- exec --ephemeral --skip-git-repo-check --sandbox read-only \
  "개인정보 없이 짧게 인사해 주세요."
echo $?
```

진단에서 `local_gateway_reachable`, `local_gateway_authorized`까지 `true`면 로컬 연결·인증을 확인한 것이다. `remote_detector_readiness: "unverified"`는 정상적인 한계 표시다. 진단은 GPU·OpenAI를 호출하지 않는다. 이어지는 `run`은 실제 원격 GPU와 OpenAI를 사용하며, 정상 응답과 종료 코드 `0`을 확인한다.

패키지는 설정을 직접 읽는다. `source`·키 환경변수 입력은 필요 없다. Codex 자식 프로세스에는 로컬 인증 토큰만 전달하며 OpenAI 키·GPU 검사 토큰을 전달하지 않는다.

평소에도 같은 `serve`·`run` 명령을 사용한다. 보호는 **전용 `run` 요청에만** 적용된다. 일반 Codex 실행·다른 앱·기본 플러그인 대화는 자동 보호하지 않는다. 실제 비밀을 명령행·프롬프트·시험 질문에 넣지 않는다. 명령에 직접 넣은 질문은 셸 히스토리·프로세스 인자에 남을 수 있다.

## 7. 지원 범위와 오류 해결

현재는 **텍스트 전용 `exec`** 경로다. 대화형 Codex·이미지·음성·파일 입력·도구 실행/결과·저장 대화 참조·암호화 이력은 지원하지 않는다. 개인 지침·프로젝트 문서 대신 전용 텍스트 지침을 쓴다. 보호 설정을 바꾸는 `--config`, `--profile`, `--enable` 등은 거부한다.

전체 텍스트·지침·필수 CLI 정의의 합산 한도는 16,000 코드포인트이며, 질문에 쓸 수 있는 길이는 더 짧다. 주민번호·카드번호·API 키·개인 키는 차단 대상이고 탐지된 이름·이메일·전화번호 등은 `[REDACTED]`로 마스킹 후 재검사한다. 탐지 품질은 별도이며, 검사 장애·인증 실패·불완전 검사 시 요청을 통과시키지 않는다. 응답은 전체 검사 후 표시한다.

| 증상·오류 코드 | 조치 |
|---|---|
| 실행 불가·macOS 보안 차단 | ARM64 대상·운영자 SHA-256·서명/공증 상태를 확인한다. OS 보안을 끄지 않는다. |
| `settings_already_exist` | 비공개 설정이 이미 있다. 덮어쓰지 말고 운영자의 키 갱신 절차를 따른다. |
| `unsafe_settings_path/directory/file` | 절대 경로, 디렉터리 `0700`·파일 `0600`·현재 사용자 소유·심볼릭 링크 여부를 확인한다. |
| `client_operation_refused` | 잘못된 설정·CA·보호 설정 override 등으로 거부할 수 있다. 비밀 값을 출력하지 말고 운영자에게 고정 오류 코드만 전달한다. |
| `codex_executable_missing`·`unsupported_codex_version` | `command -v codex`, `codex --version`으로 실제 PATH와 0.166.0을 확인한다. |
| `protected_profile_refused` | 앱·프로필의 변경/이동 여부를 확인하고 운영자에게 복구를 요청한다. |
| `gateway_probe_failed` | 터미널 1의 `serve` 실행 여부·8765 포트 점유를 확인한다. |
| `gateway_authorization_unverified` | 두 터미널이 같은 설정 파일을 쓰는지 확인한다. |
| `INSPECTION_FAILED/INVALID`·`MODEL_CHANGED` | 운영자에게 GPU 서비스·토큰·CA·모델 교체 상태 확인을 요청한다. 직접 OpenAI 전송으로 우회하지 않는다. |
| `UPSTREAM_FAILED` | 본인 OpenAI 키·사용량·모델 권한·연결 상태를 확인한다. |
| `POLICY_BLOCK` | 정책 차단이다. 실제 비밀을 제거한 새 질문을 작성한다. |
| `UNSUPPORTED_PROTOCOL/OUTPUT` | 텍스트 전용 지원 범위·길이를 확인한다. |
| `JOURNAL_UNAVAILABLE` | 디스크·권한·DB 상한을 확인하고 운영자에게 복구를 요청한다. 사건을 임의 삭제하지 않는다. |
| `GATEWAY_BUSY` | 진행 중 요청이 끝난 뒤 다시 시도한다. 자동 반복 전송하지 않는다. |

기존 셸에 다음 변수가 있으면 보호 설정 변경으로 거부할 수 있다. 이 앱 전용 터미널에서만 제거한 뒤 다시 실행한다.

```zsh
unset OPENAI_BASE_URL OPENAI_API_BASE PII_UPSTREAM_URL PII_GATEWAY_HOST
unset PII_DETECTOR_VERIFY_TLS PII_VERIFY_TLS PYTHONHTTPSVERIFY
```

사건 저널은 기본 `~/.local/state/ai-pii-gateway/events.sqlite3`에 원문 없는 판단·탐지 유형/개수·버전 등을 기록한다. 기본 DB 상한은 **100MiB**이며 임시 rollback 공간도 필요하다. 현재 중앙 전송이 없어 사건이 계속 쌓이고 자동 만료·회전 삭제되지 않는다. 저장 불가 시 보호 요청도 거부하므로 장기 사용 전 운영자와 보관·회수 계획을 정한다.

## 8. 종료·제거·업데이트

진행 중 요청이 끝난 뒤 터미널 1에서 `Ctrl-C`로 `serve`를 종료한다. 보호 프로필 제거는 다음과 같다.

```zsh
"$APP" uninstall
```

성공 출력은 `{"status": "uninstalled"}`다. 설치 전 프로필은 검증된 백업으로 복구하며, 설치 후 변경된 프로필을 덮어쓰지 않는다. **키 설정·사건 저널·앱 폴더는 삭제하지 않는다.** 키 폐기는 발급처의 폐기 절차와 운영자의 비공개 파일 처리 절차를 따른다. Codex 플러그인 제거도 앱·키·저널 제거와 별개다.

업데이트는 자동으로 하지 않는다. 앱 이동·교체 전 기존 앱으로 `uninstall`하고 `serve`를 종료한다. 운영자가 제공한 검증된 새 패키지로 교체한 뒤 `version`→`install`→`doctor --probe`→합성 `run`을 다시 확인한다. 설치 스크립트가 기존 목적지를 거부하면 강제 덮어쓰지 말고 운영자에게 새 위치·복구 절차를 확인한다.

플러그인 자체만 제거하려면 `codex plugin remove pii-gateway@codyssey-security`를 사용한다. 새 프로세스의 목록에서 제거를 확인하며, 앱·키·저널은 별도로 관리한다.

확인한 번들 검사는 해시 설치, Python 없는 PATH의 새 프로세스 `version`·도움말, 합성 키 설정과 `0600`, 비밀 출력 없음, 실제 Codex 프로필 설치, 오프라인 진단 실패, 잘못된 권한 거부, `serve` health·인증 진단, 합성 HTTPS 검사기를 통한 실제 Codex 요청 차단, 프로필 제거다. 외부 API 호출은 0회였고 시험 프로세스·격리 루트를 정리했다. frozen 앱의 정상 공급자 응답·응답 마스킹 전체 왕복은 미검증이다. 기존 소스 실행에서는 모의 서버와 실제 CLI 13개 검증을 통과했다.

고급 개발자의 소스 실행은 [기존 런처 문서](../dev/launcher/README.md)를 따른다. 그 경로에는 Anaconda `py312`와 의존성이 필요하며, 위 일반 사용자 배포판과 구분한다.
