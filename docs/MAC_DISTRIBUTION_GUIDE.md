# macOS ARM64 배포 운영 가이드

이 문서는 사용자 PC에 Python·Conda·pip를 설치하지 않는 엔지니어링 배포판의 빌드·배포·검증 절차다. 사용자는 [사용자 설치 가이드](USER_INSTALLATION_GUIDE.md)를 따른다. **빌드 PC만 Anaconda `py312`와 PyInstaller를 사용**한다. 서버 접속·게시·사용자 계정 키 발급은 이 문서 작성으로 수행하지 않는다.

## 1. 현재 배포 상태

| 항목 | 현재 범위 |
|---|---|
| 대상 | macOS ARM64(Apple Silicon), 터미널 실행 프로그램 |
| 패키지 | PyInstaller `onedir`: 실행 파일 + 포함 Python 런타임·라이브러리 폴더 |
| 버전 | `0.1.0-engineering.1` |
| 실제 네이티브 빌드 | ARM64 번들 실제 빌드 완료, 약 77MiB. 독립 macOS 격리 검사 12개 통과 |
| Git 배포 | 기존 보호 코드 PR #3은 `main` 병합됨. 이번 패키징·플러그인 변경은 별도 병합·게시 확인 필요 |
| 공개 다운로드 | 아직 게시한 공식 릴리스 URL 없음 |
| Apple 서명·공증 | 사용 가능한 Developer ID 인증서 확인 0개. Developer ID 서명·공증 미완료, ad-hoc 서명 수준의 엔지니어링 배포 |
| 신규 Mac 다운로드 설치 | 미검증. 로컬 실행과 별도 |
| GPU·실제 OpenAI 종단 간 호출 | 이번 패키징 검증에서는 미수행 |
| 중앙 사건 서버 | C 서비스 미배포. 정적 파일 호스팅 준비와 구분 |

검증한 아카이브는 `pii-gateway-macos-arm64-0.1.0-engineering.1.tar.gz`, SHA-256은 `9bd80eb44e9083f15fb53111ff9482f1f06423a5d5fd92e19038df4d5aed1727`이다. 파일이 바뀌면 이 해시·검증 기록을 재사용하지 않는다. strict `codesign --verify --deep --strict`는 종료 0이었으며 서명은 `adhoc`, TeamIdentifier는 없다. Apple 공증·새 Mac의 다운로드 실행 허용을 뜻하지 않는다.

Windows·Intel Mac·Linux용 바이너리, Universal2, GUI `.app`, 자동 업데이트, 서비스 자동 시작을 제공한다고 주장하지 않는다. 빌드 호스트 외 macOS 버전의 최소 지원 범위는 별도 검증해야 한다.

## 2. 빌드 경계

실제 빌드 진입점은 [`dev/launcher/distribution.py`](../dev/launcher/distribution.py), 번들 진입점은 [`freeze_entry.py`](../dev/launcher/freeze_entry.py), 사용자 명령은 [`standalone.py`](../dev/launcher/standalone.py)다.

빌더는 게이트웨이 코드, 보호 런처, 전용 지침 파일, `detector.schemas`만 별도 스테이지에 복사한다. GPU 모델·학습 코드·원본 저장소의 비공개 설정을 배포하지 않는다. `detector.schemas` 때문에 Pydantic 2가 필요하지만 Torch·GLiNER·가중치는 사용자 앱에 필요 없다. 빌드 제외 목록은 코드로 관리하며 실제 산출물 검사로 누락·혼입 여부를 확인한다.

`onedir` 배포이므로 `dist/pii-gateway/pii-gateway` 실행 파일 하나만 전달하면 안 된다. `dist/pii-gateway/` 전체를 아카이브로 배포한다. PyInstaller 모드의 일반 설명은 [공식 operating mode 문서](https://www.pyinstaller.org/en/stable/operating-mode.html)를 참고한다.

빌더는 환경변수에서 키를 전달하지 않도록 별도 HOME·TMPDIR·PyInstaller 설정 디렉터리를 사용한다. 기존 출력 폴더는 덮어쓰지 않는다. 모델 다운로드·패키지 설치·서명 identity 탐색·공증·외부 게시를 수행하는 명령이 아니다.

## 3. 빌드 환경과 계획 검토

빌드 호스트가 macOS ARM64인지 확인한다. Python은 실제 Anaconda `py312` 절대 경로를 사용한다. 아래 사용자 입력값에는 키를 넣지 않는다.

```zsh
uname -sm
read "REPO?저장소 루트 절대 경로: "
read "BUILD_PYTHON?Anaconda py312/bin/python 절대 경로: "
read "BUILD_OUTPUT?새 빌드 출력 폴더 절대 경로 (아직 없어야 함): "
cd "$REPO/dev"
"$BUILD_PYTHON" -c 'import sys, platform; print(sys.executable); print(platform.platform()); print(platform.machine()); assert sys.version_info[:2] == (3, 12)'
"$BUILD_PYTHON" -m launcher.distribution --output "$BUILD_OUTPUT" --plan
```

필수 빌드 패키지에는 PyInstaller·hooks-contrib와 FastAPI·Starlette·Uvicorn·Pydantic/Pydantic-core·HTTPX/HTTPCore·AnyIO·h11이 있다. 패키지가 없다면 실제 Anaconda 경로·`py312` prefix·채널을 먼저 확인하고 Conda 검색→dry-run으로 기존 패키지 교체·native 의존성·인증서 변경 범위를 검토한다. 안전한 계획만 적용한다. 채널 부재가 확인된 패키지/버전에만 같은 Python의 pip fallback을 검토하고 이유를 기록한다.

현재 개발 환경에는 일부 import 버전과 배포 metadata 불일치가 기록되어 있다. 빌드 영수증의 metadata 목록만으로 런타임 정합성이나 재현성을 보장하지 않는다. 새 프로세스의 실제 import 경로·버전, 패키지 정합성, 번들 실행을 모두 확인한다. `environment.yml`은 새 환경 전체 solve를 검증한 lockfile이 아니다.

## 4. 실제 빌드와 산출물

계획을 검토한 뒤 실행한다. 기존 폴더를 지우거나 재사용하지 않고 새 출력 경로를 쓴다.

```zsh
"$BUILD_PYTHON" -m launcher.distribution --output "$BUILD_OUTPUT"
"$BUILD_OUTPUT/dist/pii-gateway/pii-gateway" version
```

| 파일 | 용도 |
|---|---|
| `dist/pii-gateway/` | 설치할 전체 앱 폴더 |
| `pii-gateway-macos-arm64-0.1.0-engineering.1.tar.gz` | 사용자 배포 아카이브 |
| 같은 이름의 `.tar.gz.sha256` | 아카이브 SHA-256 |
| `source-evidence.json` | 포함 소스별 해시·바이트 수 |
| `build-receipt.json` | Python·의존성 metadata·빌드 상태·아카이브 해시 |
| `artifact-manifest.json` | 버전·대상·해시와 검증 상태 |
| `build.log`, `source/`, `work/`, `spec/` 등 | 운영자 로컬 빌드 조사용. 사용자 공개 대상 아님 |

`status: built`는 빌드 및 아카이브 생성 성공이다. 현재 코드의 `signature_verification`·`runtime_verification`은 별도 검증 전 `not_performed`이며, `notarized`는 `false`다. 이 값을 실제 시험 없이 성공으로 바꾸지 않는다. 검증 결과는 빌드 출력과 연결한 별도 보고서에 남긴다.

새 프로세스에서 해시를 다시 계산한다.

```zsh
ARTIFACT=pii-gateway-macos-arm64-0.1.0-engineering.1.tar.gz
(cd "$BUILD_OUTPUT" && shasum -a 256 -c "$ARTIFACT.sha256")
/usr/bin/file "$BUILD_OUTPUT/dist/pii-gateway/pii-gateway"
/usr/bin/codesign --verify --deep --strict --verbose=2 \
  "$BUILD_OUTPUT/dist/pii-gateway/pii-gateway"
```

ad-hoc 서명의 무결성 검사 통과는 Apple 신원 확인·공증·Gatekeeper 허용과 다르다. 현재 Developer ID 인증서가 없으므로 정식 서명·공증 완료로 보고하지 않는다. 후속 정식 배포는 담당자의 Apple 자격증명 관리·Developer ID 서명·공증·다운로드 후 Gatekeeper 확인까지 별도 수행한다. `xattr` 제거·Gatekeeper 해제로 성공 판정을 만들지 않는다.

## 5. 설치 플러그인·다운로드 배포

설치 플러그인 계약은 다음과 같다. 실제 게시 전 manifest와 marketplace 항목이 같은 리비전을 가리키는지 확인한다.

- 플러그인: `dev/launcher/plugin/.codex-plugin/plugin.json`.
- 설치 도우미: `dev/launcher/plugin/skills/install-pii-gateway/SKILL.md`.
- 설치 스크립트: `dev/launcher/plugin/scripts/install-macos.sh`.
- Git marketplace catalog: `.agents/plugins/marketplace.json`, marketplace 이름 `codyssey-security`. 소스 파일 존재와 원격 `main` 게시 완료는 구분한다.

Codex marketplace 등록은 `codex plugin marketplace add SOURCE --ref main`, 설치는 `codex plugin add pii-gateway@codyssey-security`를 사용한다. 새 프로세스의 `codex plugin list`에서 설치·활성 상태를 확인한다. 제거는 `codex plugin remove pii-gateway@codyssey-security`다. 이 수명주기는 실제 CLI의 격리 HOME/CODEX_HOME에서 검증했고, 캐시된 SKILL 바이트 일치와 제거 후 빈 목록·정리를 확인했다. 모델 세션의 도우미 활성화는 미검증이다. 패키지 구조의 배경은 [공식 플러그인 문서](https://developers.openai.com/plugins/build/plugins)를 참고한다.

설치 스크립트는 아카이브 또는 HTTPS URL과 필수 SHA-256을 받는다.

```zsh
INSTALLER="$REPO/dev/launcher/plugin/scripts/install-macos.sh"
read "EXPECTED_SHA256?독립적으로 검증한 아카이브 SHA-256: "
/bin/bash "$INSTALLER" --archive "$BUILD_OUTPUT/$ARTIFACT" \
  --sha256 "$EXPECTED_SHA256"
# 위 archive 방식의 대안. HTTPS 게시가 실제로 끝난 뒤 새 목적지에 사용
read "DOWNLOAD_URL?게시·검증된 HTTPS 다운로드 URL: "
/bin/bash "$INSTALLER" --url "$DOWNLOAD_URL" --sha256 "$EXPECTED_SHA256"
```

기본 설치 목적지는 `~/Library/Application Support/AI PII Gateway/`, 실행 파일은 그 안의 `pii-gateway`다. `--destination`으로 별도 절대 경로를 쓸 수 있다. 관리자 권한·pip·Conda 설치 없이 진행해야 한다. 아카이브 traversal·외부로 나가는 링크·기존 목적지 덮어쓰기·해시 불일치·HTTP URL 등 실제 거부 범위를 테스트와 대조한다. 현재 다운로드는 HTTPS 리다이렉트를 허용하고 HTTPS 외 프로토콜 전환은 거부하므로 최종 배포 경로도 검증한다.

배포 아카이브·해시 파일·공개용 manifest만 GitHub Release 또는 일반 서버의 정적 HTTPS 다운로드에 게시할 수 있다. **이 문서 작성으로 게시하지 않았다.** 일반 서버 정적 다운로드는 C의 수집 API·대시보드 배포와 별개다. 실제 주소·인증서·접근 권한·파일 가용성을 구성하고 PC에서 다운로드 검증을 끝낸 뒤 사용자에게 URL을 제공한다.

해시는 다운로드 손상·교체를 확인하지만 동일한 공격자가 파일과 해시를 함께 바꿀 수 있다. 별도 신뢰 경로로 해시를 전달하거나 검증된 서명 체계를 갖춘다. 비공개 키·토큰·`client.json`·`session.env`·서버 접속 정보·사건 DB·개인 HOME은 아카이브에 넣지 않는다.

## 6. 실제 사용자 경로 검증

설치 완료는 빌더의 exit code나 파일 존재만으로 선언하지 않는다. 빌드 프로세스와 분리된 새 프로세스·격리 HOME에서 검증하고, 별도 검토자가 증거를 확인한다. 실제 공개 다운로드는 새 Mac·새 세션 또는 동등한 독립 환경에서 추가 검증한다.

| 검증 | 완료 조건·증거 | 현재 기록 |
|---|---|---|
| 소스 테스트 | 설정·비밀 분리·CLI 위임·빌드 allowlist·실패 경로 포함 전체 회귀 | 실제 macOS sandbox: 2,150 통과·2 skip·의존성 경고 2개, 종료 0 |
| 네이티브 cold start | Python/Conda/pip 없는 PATH·별도 HOME의 `version`·도움말 | 독립 번들 검사 통과 |
| 아카이브 설치 | 정상 해시 설치·새 프로세스 실행 | 독립 번들 검사 통과 |
| 적대적 설치·설정 | 설치기 거부 경로·잘못된 설정 권한 | 설치기 전용 sandbox 테스트 4개 통과, 번들 권한 거부 통과 |
| 실제 frozen 실행 | 합성 설정 `0600`·비밀 출력 없음·실제 Codex 설치·오프라인 진단 실패·serve health·인증 진단·제거 | 독립 번들 검사 통과 |
| frozen 보호 동작 | 합성 HTTPS 검사기를 통한 실제 Codex 요청 `POLICY_BLOCK` | 요청 차단 통과. 정상 공급자/응답 마스킹 전체 왕복은 미검증 |
| 기존 소스 보호 경로 | 모의 공급자·검사기와 실제 고정 CLI 정상·마스킹·차단 | 기존 소스 검증 13개 통과. frozen 전체 왕복과 구분 |
| OS 네트워크 격리 | 외부 전송 차단·합성 경로만 사용 | 독립 12검사 통과·외부 API 0회·시험 루트/프로세스 정리 확인 |
| 플러그인 CLI 수명주기 | marketplace 등록→add→새 list→캐시 SKILL 대조→remove→새 빈 list | 실제 CLI 통과·정리 확인. 모델 세션 활성화 미검증 |
| 다운로드·Gatekeeper | 실제 HTTPS 다운로드 후 서명·공증·호스트 OS의 실행 허용 | 미완료 |
| 운영 GPU·OpenAI | 승인된 실제 자격증명으로 양방향 보호 요청 | 이번 작업에서 미수행 |

재사용할 독립 번들 검증 도구는 [`verify_macos_bundle.py`](../dev/integration/verify_macos_bundle.py)다. 전체 테스트의 skip 2개는 Darwin `/private/var` 별칭 fixture와 Linux `/proc` fixture이며, 실제 번들은 별도 `/private/tmp` 격리 루트에서 검증했다. 커버리지 수치는 별도 측정 결과와 연결하며 테스트 개수로 대체하지 않는다.

모의 TLS 검증은 실제 GPU 추론·한국어 품질·외부 OpenAI 검증을 대체하지 않는다. 격리 시험은 합성 키·합성 입력만 썼고 운영 서버에 새로 접속하지 않았다. 시험 산출물은 자기 격리 루트에만 생성하고 시험 루트·프로세스를 정리했다. 사용자 HOME·기존 프로필·사건 저널은 건드리지 않았다.

정상 사용자 명령의 계약은 다음과 같다. `--settings ABS`는 필요 시 하위 명령 앞에 둔다.

```text
pii-gateway [--settings ABS] version
pii-gateway [--settings ABS] configure
pii-gateway [--settings ABS] install
pii-gateway [--settings ABS] doctor [--probe]
pii-gateway [--settings ABS] serve
pii-gateway [--settings ABS] run -- exec --ephemeral --skip-git-repo-check --sandbox read-only "합성 질문"
pii-gateway [--settings ABS] uninstall
```

설정은 기본 `~/.config/ai-pii-gateway/client.json`(`0600`, 부모 `0700`), 프로필은 `~/.local/share/ai-pii-gateway/codex-home`, 저널은 `~/.local/state/ai-pii-gateway/events.sqlite3`이다. `configure`는 토큰을 숨겨 입력받고 기존 설정을 덮어쓰지 않는다. `run`은 OpenAI 키·검사 토큰을 Codex 자식에 전달하지 않고 로컬 토큰만 전달한다. TLS 검증·fail-closed 정책은 유지한다.

## 7. 게시 전 확인·운영 인계

공개 릴리스 전에 버전·소스 리비전·아카이브 해시·빌드 호스트/OS·독립 실행 증거·서명/공증·플러그인 설치 결과를 하나의 배포 기록으로 연결한다. 미완료 항목은 그대로 기재하고, 엔지니어링 시험 배포와 일반 사용자 정식 배포를 구분한다. 지원하지 않는 OS·아키텍처·서명 상태의 실행을 보장하지 않는다.

운영자는 GPU HTTPS origin·검증된 CA·검사 토큰·지원 모델 ID를 사용자에게 안전하게 전달한다. 실제 키를 CLI 인자·Git·플러그인 대화에 넣지 않는다. M0 가용성과 한국어 탐지 품질 승인, A 학습/평가, C 사건 서버 통합을 별도 상태로 유지한다.

중앙 사건 전송은 현재 기본 비활성이다. 100MiB 로컬 DB가 계속 쌓이며 자동 만료하지 않고, 기록 실패 시 요청도 거부한다. 사용자 인계 전에 보관·회수·용량 경보·키 갱신·앱 업데이트·복구 절차를 정한다. `uninstall`은 보호 프로필을 복구할 뿐 설정·키·저널·앱을 삭제하지 않는다. 앱 이동·업데이트 전 기존 앱의 프로필 해제와 `serve` 종료를 확인한다.
