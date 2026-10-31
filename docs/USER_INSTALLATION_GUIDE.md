# 사용자 PC 설치·실행 가이드

이 가이드는 **macOS의 zsh, Anaconda `py312`(Python 3.12), Codex CLI 0.166.0**을 기준으로 한다. PR #3의 `main` 병합을 확인했으며 신규 사용자는 **`main` 브랜치**를 받는다. Windows·Linux 사용자 PC의 설치 절차는 여기서 검증하지 않았다.

## 1. 무엇을 설치하나요?

현재 배포 형태는 **소스 체크아웃 + 사용자 PC의 게이트웨이 + Codex 전용 보호 프로필**이다. 마켓플레이스 플러그인이나 MCP 서버를 등록하는 방식은 아니며, 원클릭 설치 프로그램도 아직 없다. `launcher install`은 보호 프로필을 만들 뿐, Python 패키지·Codex·검사 서버를 설치하거나 실행하지 않는다.

사용자는 PC에서 게이트웨이를 실행하고, `launcher run -- exec`로 질문한다. 질문의 검사 대상 원문은 운영자의 **원격 GPU 검사 서버에 HTTPS로 전달**된다. 검사 후 허용·마스킹한 요청만 PC에서 OpenAI로 전달하며, 응답도 검사 후 표시한다. 따라서 이 구성은 모든 원문을 PC 안에만 두는 방식이 아니다. OpenAI API 키는 사용자 PC에서 관리하고 GPU 검사 서버에 보내지 않는다.

| 구성요소 | 누가 준비하나요? | 사용자에게 필요한 것 |
|---|---|---|
| GPU 검사 API | 운영자 | 검증된 HTTPS 주소·검사 토큰·필요 시 CA 인증서 |
| PC 게이트웨이·보호 프로필 | 사용자 | 이 문서의 설치·실행 절차 |
| 일반 서버의 사건 수집·대시보드 | C 담당·운영자 | 현재 미배포이며 이 절차의 필수조건은 아님 |

일반 서버가 없어도 로컬 사건 저널에 기록하며 보호 요청을 처리할 수 있다. 기본 실행은 중앙 사건 전송을 켜지 않는다. 중앙 대시보드까지 설치·연동됐다고 해석하면 안 된다.

## 2. 시작 전에 받을 것

운영자에게 다음을 받는다. 실제 주소·키를 채팅 프롬프트나 Git에 붙여 넣지 않는다.

- 저장소의 사용할 `main` 리비전 또는 준비된 소스 폴더.
- 현재 사용 가능한 GPU 검사 API의 HTTPS origin(호스트와 필요 시 포트), 검사 토큰. PC에서 해당 서버로 접속할 네트워크 경로도 확인한다.
- 사설 인증서를 쓴다면 승인된 **CA 인증서 PEM 파일**과 검증된 서버 호스트명. 서버 개인 키는 받지 않는다.
- 외부 OpenAI API에서 사용 가능한 모델 ID. 이 값은 아래 `PII_MODEL_ID`와 설치 모델에 동일하게 사용한다.
- PATH에서 실행할 수 있는 승인된 Codex CLI **0.166.0** 실행 파일. 현재 런처는 다른 버전을 거부한다. 이 문서는 확인되지 않은 패키지 배포 명령을 제시하지 않는다.

사용자 본인의 OpenAI API 키도 준비한다. 실제 외부 생성 요청은 계정 사용량·비용을 발생시킬 수 있다. M0 API 배포 성공은 한국어 개인정보 탐지 품질 승인이나 A의 학습 모델 인수를 뜻하지 않는다.

## 3. 소스와 Python 경로 지정

다음 명령은 일반 zsh 명령이다. RTK 설치는 사용자 실행의 필수조건이 아니다. 경로에 공백이 있어도 되며, 따옴표를 유지한다.

이미 소스가 있으면 복제 단계는 생략한다. 새로 받는 경우 아래 명령으로 `main`을 받는다. 대상 폴더가 이미 있으면 덮어쓰지 말고 기존 설치 경로를 확인한다.

```zsh
git clone --branch main --single-branch \
  https://github.com/stevenkim18/2026_codyssey_freedom.git \
  "$HOME/ai-pii-gateway"
```

소스 루트와 Anaconda 설치 경로를 입력한다. 예를 들어 소스 루트는 `$HOME/ai-pii-gateway`, Anaconda는 `$HOME/anaconda3`일 수 있다. **개발자의 PC 경로를 그대로 사용할 필요는 없다.**

```zsh
read "REPO?저장소 루트의 절대 경로: "
read "ANACONDA?사용 중인 Anaconda 설치 절대 경로: "
export REPO ANACONDA
CONDA="$ANACONDA/bin/conda"
"$CONDA" info --envs
"$CONDA" config --show channels
```

실제 목록에서 `py312` 경로를 확인한다. 없다면 아래 설치 계획을 먼저 검토하고, 안전한 경우에만 환경을 만든다.

```zsh
"$CONDA" create -n py312 python=3.12 --dry-run
# 위 계획이 적절한 경우에만 실행
"$CONDA" create -n py312 python=3.12
```

목록에 표시된 **실제 `py312/bin/python` 절대 경로**를 입력한다. 다른 Conda 배포판·base·프로젝트 가상환경을 선택하지 않는다.

```zsh
read "PYTHON?py312/bin/python 절대 경로: "
export PYTHON
PY312_PREFIX="$("$CONDA" run -n py312 python -c 'import sys; print(sys.prefix)')"
export PY312_PREFIX
"$PYTHON" -c 'import os, sys; from pathlib import Path; print(sys.executable); print(sys.version); assert sys.version_info[:2] == (3, 12); assert Path(sys.prefix).resolve() == Path(os.environ["PY312_PREFIX"]).resolve(), "Conda 설치 대상과 Python 환경이 다릅니다."'
cd "$REPO/dev"
codex --version
```

마지막 출력은 정확히 `codex-cli 0.166.0`이어야 한다. 다른 버전이면 운영자에게 승인된 실행 파일을 받아 PATH를 정리한 뒤 다시 확인한다. 보호 실행을 위해 사용 중인 다른 Codex를 무작정 업데이트·덮어쓰지 않는다.

## 4. PC 게이트웨이 의존성 준비

사용자 PC에서는 원격 GPU를 이용하므로 PyTorch·GLiNER·모델 가중치를 설치할 필요가 없다. 직접 의존성 기준은 [environment.yml](../dev/environment.yml)의 FastAPI `0.116.1`, Starlette `0.47.3`, Pydantic `2.12.5`, HTTPX `0.28.1`, Uvicorn `0.35.0`이다. 전체 파일에는 개발용 테스트 패키지도 포함된다.

현재 채널에서 필요한 버전을 먼저 검색한다. 이어서 기존 환경을 보존하는 설치 계획을 확인한다.

```zsh
for spec in fastapi=0.116.1 starlette=0.47.3 pydantic=2.12.5 httpx=0.28.1 uvicorn=0.35.0; do
  "$CONDA" search "$spec"
done
"$CONDA" install -n py312 --freeze-installed \
  fastapi=0.116.1 starlette=0.47.3 pydantic=2.12.5 \
  httpx=0.28.1 uvicorn=0.35.0 --dry-run
# 기존 패키지 교체·충돌 범위를 검토한 뒤 같은 계획만 적용
"$CONDA" install -n py312 --freeze-installed \
  fastapi=0.116.1 starlette=0.47.3 pydantic=2.12.5 \
  httpx=0.28.1 uvicorn=0.35.0
```

충돌이나 중요한 기존 패키지 교체가 제안되면 적용을 중단하고 운영자에게 계획을 확인받는다. 채널에 패키지 또는 해당 버전이 없을 때만 그 사유를 기록하고 **같은 `$PYTHON`의** pip 대안을 검토한다. 예를 들어 HTTPX만 없다면 다음처럼 계획을 확인한다. 이 예시를 전체 패키지의 무조건 pip 설치로 확대하지 않는다.

```zsh
"$PYTHON" -m pip install --dry-run 'httpx==0.28.1'
# 의존성 변경 범위가 적절한 경우에만 실행
"$PYTHON" -m pip install 'httpx==0.28.1'
```

설치 후 새 Python 프로세스에서 실제 버전·경로를 확인한다.

```zsh
"$PYTHON" -c 'import sys, fastapi, starlette, pydantic, httpx, uvicorn; print(sys.executable); [print(m.__name__, m.__version__, m.__file__) for m in (fastapi, starlette, pydantic, httpx, uvicorn)]'
"$PYTHON" -m pip check
```

현재 개발 PC에는 일부 패키지의 import 버전과 배포 metadata 불일치가 기록되어 있다([의존성 조사](../dev/detector/DEPENDENCIES.md)). `environment.yml`은 모든 새 PC에서 재현 검증한 lockfile이 아니다. import 성공만으로 설치 완료를 판단하지 말고, 아래 실제 `doctor`·보호 요청까지 확인한다.

## 5. 비밀 설정 준비 — 터미널 1

게이트웨이는 `.env` 파일을 자동으로 읽지 않는다. 아래 코드는 키를 화면·명령행·셸 히스토리에 직접 쓰지 않고 입력받아, 저장소 밖 사용자 홈의 설정 파일에 저장한다. 화면 녹화·터미널 로깅을 끄고 실행한다. OpenAI 키와 검사 토큰을 서로 바꾸지 않는다.

CA가 필요하면 운영자에게 받은 PEM 파일을 신뢰할 수 있는 위치에 보관한다. 아래 질문에는 파일의 **절대 경로**를 입력한다. CA 파일과 상위 경로는 심볼릭 링크와 타인 쓰기 권한이 없어야 하며 root 또는 현재 사용자 소유여야 한다(코드가 허용한 macOS 표준 경로 별칭 예외). CA는 공개 인증서여서 파일 `0644`도 허용한다. 공인 CA로 검증되는 서버면 빈칸으로 둔다. 인증서 검증을 끄는 대안은 없다.

```zsh
umask 077
"$PYTHON" -c '
import getpass, os, secrets, shlex
from pathlib import Path
from gateway.settings import Settings
from gateway.tls import detector_tls_context
folder = Path.home() / ".config" / "ai-pii-gateway"
if any(p.is_symlink() for p in (folder, *folder.parents)):
    raise SystemExit("설정 디렉터리와 상위 경로에 심볼릭 링크를 사용할 수 없습니다.")
folder.mkdir(parents=True, exist_ok=True, mode=0o700)
if folder.stat().st_uid != os.getuid():
    raise SystemExit("현재 사용자 소유의 설정 디렉터리가 필요합니다.")
folder.chmod(0o700)
origin = input("검증된 GPU HTTPS origin (경로 제외): ").strip().rstrip("/")
model = input("운영자가 확인한 외부 OpenAI 모델 ID: ").strip()
ca = input("GPU용 CA PEM 절대 경로 (불필요하면 Enter): ").strip()
if ca and not Path(ca).is_absolute():
    raise SystemExit("CA 파일은 절대 경로로 입력해야 합니다.")
values = {
    "REPO": os.environ["REPO"], "PYTHON": os.environ["PYTHON"],
    "PII_GATEWAY_LOCAL_TOKEN": secrets.token_hex(32),
    "PII_DETECTOR_URL": origin + "/v1/inspect",
    "PII_DETECTOR_TOKEN": getpass.getpass("GPU 검사 토큰 (숨김): "),
    "OPENAI_API_KEY": getpass.getpass("본인 OpenAI API 키 (숨김): "),
    "PII_MODEL_ID": model, "PII_GATEWAY_PORT": "8765",
    "PII_STATE_DIR": str(Path.home() / ".local/state/ai-pii-gateway"),
}
if ca:
    values["PII_DETECTOR_CA_FILE"] = ca
try:
    checked = Settings.from_env(values)
    detector_tls_context(checked.detector_ca_file)
except Exception:
    raise SystemExit("설정 검증에 실패했습니다. 주소·모델·인증값·CA를 확인하세요. 값은 출력하지 않습니다.")
path = folder / "session.env"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write("unset PII_DETECTOR_CA_FILE\n")
    output.write("\n".join("export " + k + "=" + shlex.quote(v) for k, v in values.items()) + "\n")
print("비공개 설정 파일을 생성했습니다. 값은 출력하지 않았습니다.")
'
source "$HOME/.config/ai-pii-gateway/session.env"
cd "$REPO/dev"
```

설정 디렉터리는 `0700`, 파일은 `0600`이다. 파일에는 실제 키가 있으므로 공유·Git 추가·`cat` 출력하지 않는다. 이미 파일이 있으면 덮어쓰지 않고 실패한다. 갱신이 필요하면 비공개 파일의 내용을 안전하게 갱신하고 두 터미널에서 다시 로드한다. 로컬 토큰을 변경하면 게이트웨이도 재시작한다.

기존 셸에 `OPENAI_BASE_URL`, `OPENAI_API_BASE`, `PII_UPSTREAM_URL`, `PII_GATEWAY_HOST`, `PII_DETECTOR_VERIFY_TLS`, `PII_VERIFY_TLS`가 있으면 시작이 거부된다. 이 게이트웨이용 터미널에서만 다음과 같이 제거한다.

```zsh
unset OPENAI_BASE_URL OPENAI_API_BASE PII_UPSTREAM_URL PII_GATEWAY_HOST
unset PII_DETECTOR_VERIFY_TLS PII_VERIFY_TLS PYTHONHTTPSVERIFY
"$PYTHON" -m gateway
```

정상 실행 시 터미널이 점유된다. 기본 로그 수준 때문에 시작 메시지가 없을 수 있다. **이 터미널을 그대로 열어 둔다.** 게이트웨이는 `127.0.0.1:8765`에서만 수신하며, 다음 터미널의 진단으로 시작 상태를 확인한다.

## 6. 보호 프로필 설치·진단 — 터미널 2

새 zsh 터미널을 열고 다음을 실행한다. 두 터미널은 같은 설정 파일을 사용하므로 로컬 인증 토큰이 일치한다.

```zsh
source "$HOME/.config/ai-pii-gateway/session.env"
unset OPENAI_API_KEY PII_DETECTOR_TOKEN
cd "$REPO/dev"
export PROTECTED_HOME="$HOME/.codex/pii-gateway-home"
"$PYTHON" -m launcher install --codex-home "$PROTECTED_HOME" \
  --port "$PII_GATEWAY_PORT" --model "$PII_MODEL_ID"
"$PYTHON" -m launcher doctor --codex-home "$PROTECTED_HOME" \
  --gateway-url "http://127.0.0.1:$PII_GATEWAY_PORT"
```

터미널 2에서는 로컬 인증 토큰만 Codex에 필요하다. 위 `unset`은 게이트웨이 전용 OpenAI 키·GPU 검사 토큰이 Codex 자식 프로세스에 상속되지 않도록 한다. 터미널 1의 실행 중인 게이트웨이 환경에는 영향을 주지 않는다.

설치 성공 출력은 `{"status": "installed", "cli_version": "0.166.0"}`이다. 기존 전역 Codex `config.toml`은 수정하지 않는다. 전용 경로는 절대 경로여야 하며 심볼릭 링크는 사용할 수 없다. 소스 폴더에는 검증된 지침 파일이 있으므로 설치 뒤 해당 파일을 편집하지 않는다. 소스를 이동해야 한다면 이동 전에 `uninstall`하고 새 경로에서 다시 `install`한다.

진단에서 `installed`, `profile_verified`, `cli_verified`, `local_gateway_reachable`, `local_gateway_authorized`가 모두 `true`면 요청한 로컬 검사를 통과한 것이다. 종료 코드는 성공 `0`, 실패 `2`다. `remote_detector_readiness: "unverified"`는 정상적인 한계 표시다. `doctor`는 GPU·OpenAI를 호출하지 않으므로 원격 준비 완료나 전체 보호 경로 성공을 증명하지 않는다.

## 7. 첫 보호 요청과 평소 사용

먼저 개인정보 없는 합성 질문으로 확인한다. 이 단계부터 원격 GPU와 실제 OpenAI를 사용한다.

```zsh
"$PYTHON" -m launcher run --codex-home "$PROTECTED_HOME" -- \
  exec --ephemeral --skip-git-repo-check --sandbox read-only \
  "개인정보 없이 짧게 인사해 주세요."
echo $?
```

정상 응답과 종료 코드 `0`을 확인한다. 응답 문구는 모델에 따라 달라진다. 실패했다면 아래 표로 원인을 확인하고, 키나 실제 개인정보를 포함한 명령·응답 로그를 공유하지 않는다. 실제 비밀을 검사 시험용 질문에 넣지 않는다.

보호는 **위 런처로 실행한 요청**에 적용된다. 평소에도 터미널 1에서 게이트웨이를 실행하고 터미널 2에서 위 실행 명령을 쓴다. `--ephemeral`은 이번 실행의 세션 이력 저장을 줄이기 위한 설정이며, 셸 명령에 직접 넣은 질문은 히스토리·프로세스 인자에 남을 수 있다. 실제 개인정보·비밀을 명령행이나 질문에 넣지 않는다. 일반 `codex` 실행, 다른 앱, 다른 프로필의 요청까지 자동으로 보호하지 않는다.

현재는 텍스트 전용 `exec` 경로다. 대화형 Codex, 이미지·음성·파일 입력, 도구 실행·도구 결과, 저장된 대화 참조·암호화 이력은 지원하지 않는다. 개인 지침·프로젝트 문서 대신 검토된 전용 텍스트 지침을 사용한다. `-c`, `--config`, `--profile`, `--enable` 등의 보호 변경 옵션은 거부한다. 입력·지침·필수 CLI 도구 정의를 합쳐 16,000 코드포인트 한도가 있으므로 사용자가 쓸 수 있는 질문 길이는 그보다 짧다.

정책상 주민번호·카드번호·API 키·개인 키는 차단 대상이고, 탐지된 이름·이메일·전화번호 등은 `[REDACTED]`로 마스킹한다. 마스킹 후 재검사를 수행한다. 탐지 품질에 따라 결과가 달라질 수 있으며, 검사 서버 장애·인증 실패·불완전 검사는 통과시키지 않는다. 응답은 전체 검사 후 표시하므로 생성 즉시 글자가 흘러나오는 방식보다 대기 시간이 길 수 있다.

## 8. 문제 해결

| 증상·안전한 오류 코드 | 확인·조치 |
|---|---|
| `No module named launcher` 또는 `gateway` | `cd "$REPO/dev"`인지, `$PYTHON`이 실제 `py312` Python인지 확인한다. |
| `codex_executable_missing` | 새 터미널에서 `command -v codex`로 승인된 실행 파일이 PATH에 있는지 확인한다. |
| `unsupported_codex_version` | `codex --version`이 정확히 `codex-cli 0.166.0`인지 확인한다. |
| `protected_profile_refused` | 전용 경로·소스 지침 파일·설치 후 프로필 변경을 확인한다. 직접 설정을 우회하지 말고 운영자에게 복구를 요청한다. |
| `gateway_local_token_required` | 터미널 2에서 비공개 설정 파일을 다시 `source`한다. 토큰을 인자로 넣지 않는다. |
| `Gateway startup rejected...` | 필수 환경변수, HTTPS `/v1/inspect` 주소, CA 절대 경로, 보호 설정 override, 포트 점유·저널 접근 권한을 확인한다. 원문 설정값은 출력하지 않는다. |
| `gateway_probe_failed` 또는 도달 여부 `false` | 터미널 1이 실행 중인지, 포트가 일치하는지 확인한다. `localhost` 대신 설치한 `127.0.0.1` 주소를 쓴다. |
| `gateway_authorization_unverified` | 두 터미널에서 같은 설정 파일을 로드했는지 확인한다. 다른 프로세스가 해당 포트를 쓰는지도 확인한다. |
| `INSPECTION_FAILED`·`INSPECTION_INVALID`·`MODEL_CHANGED` 등 | 운영자에게 GPU 서비스·검사 토큰·인증서/호스트명·모델 교체 상태 확인을 요청한다. TLS 검증 해제나 직접 OpenAI 전송으로 우회하지 않는다. |
| `UPSTREAM_FAILED` | 본인 API 키·계정 사용량·모델 접근 권한·OpenAI 연결 상태를 확인한다. |
| `POLICY_BLOCK` | 정책에 따른 차단이다. 실제 비밀을 제거한 뒤 새 질문을 작성한다. |
| `UNSUPPORTED_PROTOCOL`·`UNSUPPORTED_OUTPUT` | 텍스트 전용 지원 범위·길이를 확인한다. 임의 도구 옵션을 켜지 않는다. |
| `JOURNAL_UNAVAILABLE` | 저널 디렉터리 권한·디스크 여유·DB 상한을 확인하고 운영자에게 복구를 요청한다. 임의 삭제로 사건 기록을 잃지 않는다. |
| `GATEWAY_BUSY` | 진행 중 요청이 끝난 뒤 다시 실행한다. 자동 반복 전송하지 않는다. |

로컬 사건 저널 기본 경로는 `~/.local/state/ai-pii-gateway/events.sqlite3`이다. 원문 대신 판단·탐지 유형/개수·버전 등의 메타데이터를 기록한다. DB 기본 상한은 **100MiB**이며 임시 rollback 파일용 여유 공간도 필요하다. 중앙 전송이 없는 현재 구성에서는 미승인 사건이 계속 쌓이고 자동 만료·회전 삭제되지 않는다. 저장 불가 시 요청도 거부하므로 장기 사용 전 보관·회수 계획을 운영자와 정한다.

## 9. 종료와 보호 프로필 제거

실행 중인 요청이 끝난 뒤 터미널 1에서 `Ctrl-C`를 누르면 게이트웨이를 종료한다. 다시 쓰려면 설정을 로드하고 `"$PYTHON" -m gateway`를 실행한다. 자동 시작·백그라운드 서비스 등록은 이 절차에 포함되지 않는다.

보호 프로필을 제거하려면 터미널 2에서 실행한다.

```zsh
"$PYTHON" -m launcher uninstall --codex-home "$PROTECTED_HOME"
unset PII_GATEWAY_LOCAL_TOKEN PII_DETECTOR_TOKEN OPENAI_API_KEY
```

성공 출력은 `{"status": "uninstalled"}`다. 검증된 설치 전 프로필은 복구하며, 설치 후 변경된 파일은 덮어쓰지 않고 거부한다. Codex 바이너리가 없어도 제거할 수 있다. 제거 명령은 비공개 설정 파일·로컬 사건 저널·Python 패키지를 지우지 않는다. **`session.env`에는 키와 로컬 토큰이 계속 남는다.** 사용을 끝내면 두 터미널을 종료하고, 키 발급처의 폐기 절차와 운영자가 승인한 비공개 설정 파일 폐기·저널 보관 절차를 따른다.

## 10. 플러그인으로 설치하는 방식은 가능한가요?

가능하다. OpenAI는 skills·MCP 등을 묶은 플러그인과 Git 저장소 기반 marketplace 배포를 지원한다([공식 패키징 문서](https://developers.openai.com/plugins/build/plugins)). 이 프로젝트에도 설치·설정 안내, 상태 확인, 보호 실행 진입점을 묶는 플러그인을 추가할 수 있다. 현재 `main`에는 그 플러그인 manifest·marketplace 등록·사용자 설치 검증이 없으므로 위 절차는 소스 기반 설치다.

추가 배포 작업은 플러그인 패키지·설정 마법사, Python 런타임/의존성 준비, 비밀 저장, gateway 수명주기, 업데이트·제거·복구, 새 사용자 세션에서의 실제 설치 검증이다. 검사·마스킹·차단의 강제 경로는 현재 게이트웨이와 전용 CLI 프로필을 사용하도록 설계해야 한다. **플러그인 설치만으로 기본 대화의 모든 요청·응답을 자동으로 가로채 보호한다고 보장할 수는 없다.** 이는 현재 구현과 공식 플러그인 패키징 범위를 대조한 설계 판단이며, 별도 구현·검증이 필요하다.

현재 보호 프로필은 plugins·MCP·도구 실행을 비활성화한다. 따라서 플러그인을 설치·설정 도우미로 사용하고 보호 실행을 기존 런처로 연결하거나, 플러그인을 켠 새 실행 경로의 보안 경계를 다시 구현·검증해야 한다. 외부 LLM이 이미 받은 프롬프트를 나중에 MCP 검사 도구에 보내는 방식은 ‘외부 전송 전 검사’ 요구를 충족하지 않는다.

## 11. 이 가이드에서 확인한 범위

실제 Codex 0.166.0의 설치→보호 실행→해제와 양방향 마스킹·차단을 로컬 모의 검사기·공급자로 재검증해 **13개 통과**, 외부 API 호출 0회를 확인했다. 별도 새 프로세스로 실제 gateway 기동→로컬 인증 `doctor`→해제→종료와 저널 무결성도 확인했다. 신규 사용자 PC 전체 패키지 설치, 실제 OpenAI 종단 간 호출, 한국어 탐지 품질, C 대시보드 연동은 이 문서 작성으로 검증되지 않았다. 세부 계약은 [런처 설명](../dev/launcher/README.md), [PC 환경변수 예시](../dev/gateway/.env.example), [중앙 사건 전송 범위](../dev/gateway/REPLAY.md)를 참고한다.
