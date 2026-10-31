# 보호 프로필 설치·실행·복구 CLI

`dev`에서 Anaconda `py312`의 `python -m launcher`로 기존 공개 프로필 API를
실행한다. 패키지 설치나 Codex 업데이트, 서버 기동은 수행하지 않는다.
run은 준비된 로컬 게이트웨이를 통해 사용자 요청을 전달한다.
기본 모델은 `gpt-6.1-sol`, 로컬 게이트웨이 포트는 `8765`다.

```sh
python -m launcher install
# PII_GATEWAY_LOCAL_TOKEN은 게이트웨이와 일치하도록 환경에서 제공한다.
python -m launcher run -- exec "질문"
python -m launcher uninstall
```

`doctor`는 파일을 변경하지 않고 설치 상태, 보호 프로필 무결성 및 PATH의 실제
Codex 버전을 개별 JSON 필드로 진단한다. 기본 동작은 네트워크를 사용하지 않는다.

```sh
python -m launcher doctor
python -m launcher doctor --gateway-url http://127.0.0.1:8765
```

`--gateway-url`을 명시하면 검증된 설치 프로필의 주소와 정확히 일치하는 loopback
게이트웨이만 검사한다. `/healthz` 응답과 인증 후 JSON 검증 오류를 확인하는
의도적인 잘못된 JSON 요청으로 도달 여부와 인증 여부를 분리한다. 검사기·공급자
호출은 발생하지 않으며 프록시·리다이렉트·원격 주소를 사용하지 않는다.
토큰은 환경변수로만 제공한다. JSON에는 토큰·파일 경로·원본 오류를 출력하지 않는다.

성공 종료 코드 `0`은 요청한 로컬 진단 통과를 뜻하고, 실패는 `2`다.
`installed`, `profile_verified`, `cli_verified`, `local_gateway_reachable`,
`local_gateway_authorized`는 서로 다른 증거이며 미실행 네트워크 검사는
`unverified`다. `remote_detector_readiness`는 항상 `unverified`로 남는다.
이 결과는 보호 경로 전체의 준비 완료, 원격 모델 readiness, 추론 품질 또는 실제
CLI 요청의 종단 간 성공을 뜻하지 않는다.

기본 전용 디렉터리는 `$CODEX_HOME/pii-gateway-home`이다. `CODEX_HOME`이 없으면
`~/.codex/pii-gateway-home`을 사용한다. 기존 전역 `config.toml`은 수정하지 않는다.
위치를 직접 지정하려면 각 명령에 `--codex-home /absolute/dedicated/path`를
사용한다. 상대 경로·심볼릭 링크·경로 역참조는 거부한다.

```sh
python -m launcher install --codex-home /absolute/dedicated/path --port 8765 --model gpt-6.1-sol
python -m launcher run --codex-home /absolute/dedicated/path -- exec "질문"
python -m launcher uninstall --codex-home /absolute/dedicated/path
```

install/run은 PATH에서 찾은 **동일한 Codex 실행 경로**의 `--version`을 실제로
실행하고 정확히 `codex-cli 0.166.0`인지 확인한다. 사용자가 버전 문자열을
주장하는 옵션은 없다. 버전이 다르거나 실행 파일이 없으면 설치·실행을 거부한다.

run은 `--` 뒤의 모든 인수를 기존 `launch_arguments()`에 전달한다. 현재 검증
범위는 `exec`이며 대화형 Codex, provider/profile/config 변경 및 보호 해제
인수는 거부한다. 프로필 manifest와 82바이트 지침 파일을 검사한 뒤
`--ignore-user-config`와 검증된 보호 설정을 명시적으로 전달한다. 인증 토큰은
`PII_GATEWAY_LOCAL_TOKEN` 환경변수로만 받고 명령행·프로필에는 저장하지 않는다.
실행되는 Codex의 일반 입출력은 사용자의 터미널로 연결한다.

uninstall은 Codex가 없어도 동작한다. 설치 전 존재한 프로필은 검증된 백업으로
복구하고, 설치 후 사용자가 변경한 프로필은 덮어쓰지 않는다. 설치 상태가 이미
없으면 기존 독립 프로필을 그대로 두고 성공하므로 반복 호출할 수 있다.

이 CLI는 로컬 보호 프로필을 준비한다. 게이트웨이·검사 서버·인증·CLI 프로토콜
호환성 및 실제 모델 품질 검증은 별도 게이트이며 프로필 설치만으로 완료되지
않는다. `launcher/tests`는 실제 사용자 홈과 분리된 임시 디렉터리 및 새 프로세스의
가짜 Codex 실행 파일을 사용한다. 별도 `integration/run_smoke.py`는 실제
Codex 0.166.0으로 이 CLI의 설치→실행→제거와 모의 HTTP/CLI 검증 13개를 통과했다.
해당 실행은 loopback 모의 검사기·공급자를 사용했고 외부 OpenAI 호출은 0회다.

```sh
python -m pytest launcher/tests -q
```
