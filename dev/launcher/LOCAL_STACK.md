# 로컬 CPU 검사기와 게이트웨이 함께 실행

`dev`에서 Anaconda `py312` Python으로 실행한다. 이 도구는 foreground에서
로컬 CPU detector와 gateway 두 프로세스만 소유한다. GPU 서버·일반 서버에
접속하지 않으며 패키지 설치, 모델 다운로드, 인증서 생성, 설정 파일 덮어쓰기를
수행하지 않는다. 실행 전 별도로 준비한 모델 패키지와 인증서가 필요하다.

```sh
python -m launcher.local_stack plan \
  --package /absolute/model-package --state-dir /absolute/private-state \
  --cert /absolute/detector.pem --key /absolute/detector.key \
  --ca /absolute/detector-ca.pem --model-id gpt-6.1-sol
```

`plan`은 manifest 구조와 TLS 파일·신뢰·`127.0.0.1` 인증서 SAN을 오프라인
검증한다. 모델 가중치를 읽거나 Torch를 불러오지 않는다. 해시 검증, 모델 로딩·예열,
포트 가용성 및 서비스 readiness는 아직 확인하지 않았다고 명시한다. 경로는 절대
경로여야 하며 심볼릭 링크는 거부한다. 개인키는 현재 사용자 소유 `0600`이어야 한다.

실행할 때는 호출 환경에 `OPENAI_API_KEY`와 `PII_GATEWAY_LOCAL_TOKEN`을 제공하고
같은 명령의 `plan`을 `run`으로 바꾼다. 인증값을 명령행에 넣지 않는다.

```sh
python -m launcher.local_stack run \
  --package /absolute/model-package --state-dir /absolute/private-state \
  --cert /absolute/detector.pem --key /absolute/detector.key \
  --ca /absolute/detector-ca.pem --model-id gpt-6.1-sol
```

모델 패키지는 로컬 GLiNER 검사 모델이다. `--model-id`는 게이트웨이가 허용할
**외부 OpenAI 생성 모델**이다. 실제 질문은 검사 후 OpenAI API로 전송되므로
OpenAI 키와 네트워크가 필요하다. 이 시작 명령 자체는 OpenAI 요청을 보내지 않으며
readiness 성공도 API 키 유효성·외부 연결·모델 품질·전체 보호 경로를 증명하지 않는다.
공급자 URL 변경과 TLS 검증 우회 옵션은 제공하지 않는다.

검사기는 `https://127.0.0.1:8766`, 게이트웨이는 `http://127.0.0.1:8765`로 바인딩한다.
부모 실행기가 두 listening socket을 먼저 확보하고 `launcher.local_child` 내부
bootstrap에 해당 descriptor만 상속한다. bootstrap은 기존 detector/gateway의
설정·앱 factory를 사용한다. 준비 확인 전후의 포트 재선점 경쟁을 막으며 자식의
생존 상태도 확인한다. HTTP readiness는 헤더 수신부터 전체 응답까지 총 시간
제한을 적용하고 취소 시 연결을 닫는다.
`--detector-port`, `--gateway-port`로 서로 다른 포트를 지정할 수 있다. detector 인증
토큰은 실행마다 생성하여 두 자식 프로세스 환경으로만 전달한다. OpenAI 키와 로컬
게이트웨이 토큰은 detector에 전달하지 않는다. 프로세스 로그는 원본 모델 오류나
비밀 경로 유출 방지를 위해 숨기며 실행기는 고정 JSON 상태·오류 코드만 출력한다.

상태 디렉터리의 상위 경로는 미리 존재해야 한다. 실행기가 상태 디렉터리를 `0700`으로
만들거나 기존 디렉터리 권한·소유자를 확인한다. `0600` 잠금 파일의 배타 잠금으로
중복 실행을 거부한다. 잠금 파일과 사건 journal은 종료 후에도 보존된다. C 사건 전송은
구성하지 않으므로 journal이 누적되며 기존 저장 한도에 도달하면 요청이 거부될 수 있다.

전체 시작 대기 시간은 기본 120초이며 `--startup-timeout`으로 최대 600초까지 지정한다.
CPU 추론에는 기존 10초 검사 deadline이 적용된다. CPU 실행은 성능 SLO 통과를
의미하지 않으며 큰 모델·긴 입력은 timeout으로 차단될 수 있다. 시작 중 실패나 자식
종료 시 이 실행기가 생성한 자식만 정리한다. Ctrl-C·SIGTERM도 terminate→wait 후
필요하면 kill 순서로 정리한다. 기존 데몬이나 PID 파일을 이용해 다른 프로세스를
종료하지 않는다. foreground 실행 중인 터미널을 유지한다.
정리 중 추가 SIGINT·SIGTERM은 무시하고, 정리 후 이전 signal handler를 복원한다.

게이트웨이가 준비되면 별도 터미널에서 기존 보호 프로필 명령을 사용한다. 동일한
`PII_GATEWAY_LOCAL_TOKEN`을 전달하고, 사용자 지정 게이트웨이 포트는 설치 포트와
맞춘다. 이 runner는 Codex 설치나 프로필 설정을 대신하지 않는다.

```sh
python -m launcher install --port 8765 --model gpt-6.1-sol
python -m launcher doctor --gateway-url http://127.0.0.1:8765
python -m launcher run -- exec "질문"
```

자동 테스트는 합성 manifest·로컬 인증서와 주입된 프로세스/readiness를 사용한다.
실제 모델·OpenAI 요청 성공 검증과 구분한다.
