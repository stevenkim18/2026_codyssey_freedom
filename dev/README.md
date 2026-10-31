# B 담당 개발·검증

현재 상태는 **모의 환경 개발 중**이다. 고정 CLI의 모의 서버 왕복은 검증했으며,
운영 모델·실제 외부 LLM·서버 배포 완료를 의미하지 않는다. 요구사항 기준은
[PRD](../docs/AI_PII_Gateway_PRD_v1.md)다.

## 실행 환경

Anaconda `py312`(Python 3.12)를 사용한다. PATH에 다른 Conda 배포판이 있으면
Anaconda의 `conda` 경로를 명시한다. 패키지는 Conda 채널에서 먼저 검색·설치하고,
패키지나 필요한 버전이 없는 경우에만 이 환경의 `python -m pip`를 사용한다.
`environment.yml`은 현재 `py312`에서 import한 직접 의존성 버전을 기록한 명세다.
새 환경에서 Conda resolver로 전체 의존성을 해결하거나 재생성하는 검증은 아직
수행하지 않았다. 설치·재현 성공을 보장하는 lock 파일이나 자동 설치 스크립트가 아니다.
별도 새 프로세스에서 실제 import 경로·버전과 Hypothesis 기반 pytest 실행은 확인했다.
기존 환경에는 Starlette의 import 버전(0.47.3)과 패키지 metadata(0.27.0),
Uvicorn의 import 버전(0.35.0)과 metadata(`None`) 차이가 남아 있다.
`environment.yml`은 실제 import 버전을 기록하며 이 기존 환경의 metadata 정합성이나
새 Conda 환경 설치 검증을 대신하지 않는다.

```sh
conda activate py312
cd dev
rtk proxy python -m pytest -q --cov=gateway --cov=detector --cov=launcher --cov=deployment \
  --cov=integration.verify_deployed_services --cov-branch \
  --cov-report=term-missing --cov-report=json:.cache/coverage.json
```

외부 API 키·외부 네트워크·GPU 없이 기본 테스트를 실행한다. 배포·HTTPS 검증에는
로컬 loopback HTTP(S) 소켓을 사용한다. 제품 소스와 지정한 HTTPS 검증 도구만 커버리지에
포함하며 테스트 파일은 제외한다. 설치된 CLI 프로토콜 조사만 별도 명령으로 실행한다.

```sh
rtk proxy python integration/probe_cli.py
```

이 조사는 임시 Codex 홈과 합성 입력을 사용하는 loopback 모의 공급자 연결이다.
실제 OpenAI를 호출하지 않으며 실제 키를 전달하지 않는다. 보호 프록시의 수용
테스트와 구분한다. 원문 대신 필드 이름·유형·길이만 조사 결과에 포함한다.

## 폴더·소유권

- `gateway/`: 요청·응답 텍스트 어댑터, 정책·마스킹, HTTP 통제, 원문 없는 저널
- `detector/`: 인증 검사 API, 검사 계약 검증, 모델 패키지 인수·교체 기반
- `launcher/`: 보호 전용 CLI 프로필·백업·복구와 검증된 설정을 고정하는 실행 인자
- `deployment/`: 서버별 배포·검증·롤백과 읽기 전용 SSH/복구 진단
- `integration/`: 실제 CLI 조사·통합 검증
- `contracts/`: B 소유 검사·모델 패키지 계약. 사건 서버 계약은 C 소유다.

코드·테스트·개발/배포 스크립트는 `dev/`에 두고, 제외 규칙은 저장소 루트
`.gitignore`에서 관리한다. A의 학습·평가와 C의 수집·화면 구현은 이 사이클 범위 밖이다.

## 사이클 1 — 전송 통제 기반

완료 조건: 모의 검사기·공급자와 실제 ASGI 경계에서 요청/응답 통제, 인증,
오류 차단, 취소, 기록 내구성 및 검사 계약의 필수 테스트를 통과한다.
이 사이클은 G0 완료를 주장하지 않는다. 설치·운영 GLiNER·원격 서버는 별도다.

| 요구·테스트 | 구현·증거 | 남은 검증 |
|---|---|---|
| R02/T03,T11 | 텍스트 전체 추출, 고정 CLI 도구 정의의 검사·정규화, 상태 참조·한도 거절 | 실제 외부 공급자 검증 |
| R03/T05,T07 | 코드포인트 스팬·검사 상태/버전/ID, 토큰 창·공유 규칙, 짧은 실제 M0 CPU 요청 | GPU·장문 실제 추론·품질 |
| R04/T03,T05 | 차단 우선, 겹침 병합·역순 마스킹·최종 1회 재검사 | 실제 모델 품질 |
| R05/T04 | 공급자 JSON 전체 수신·검사 후 정제된 JSON/SSE 재구성; 조기 응답 0바이트 | 실제 공급자 연결·고정 CLI 수용 |
| R06/T06 | 타임아웃·취소·원문 오류 정제, 자동 외부 재전송 없음 | 다중 프로세스·네트워크 단절 실측 |
| R07/T07 | 패키지 SHA-256·경로 검증, drain·예열·교체 실패 복귀 모의 검증, 실제 M0 CPU 로딩 | A 평가 기준 통과·실제 GPU 모델 로딩 |
| R08/T08 | 원문 없는 저널, FULL commit, 제한된 재전송·부분 ack·재시작 복구 | C HTTP 계약·실제 중앙 전송 |
| R10/T10 | 로컬 HTTP 토큰·origin/host 제한, 원격 HTTPS 목적지 제한 | loopback 실행 설정·실제 TLS·인증서·키 운영 |
| R11/T06,T12 | 검사 실행 1개·대기 8개·총시간 제한, 모의 구간 계측 | GPU 상주·예열·메모리·p50/p95 |

`gateway.transport.UpstreamClient`는 공급자에게 `stream=False`를 보내고 최대
1MiB의 JSON 응답을 수신한다. 현재 공급자 SSE를 수신·조립하는 구현은 없다.
CLI가 SSE를 요청하면 전체 응답 검사·저널 기록 후 안전한 텍스트만 SSE로 재구성한다.
전송기는 redirect·자동 retry를 수행하지 않고, 검사·생성 서버 토큰을 각각 받는다.
검사 서버는 HTTPS URL을 명시하며 생성 목적지는 OpenAI Responses URL로 고정한다.
모의 검증용 HTTP 예외는 명시적으로 켠 loopback 목적지에만 적용된다.
`rtk proxy python integration/run_smoke.py`는 실제 loopback TCP의 3개 모의 서버를 사용해
양방향 처리와 고정 CLI의 정상·마스킹·차단·도구 출력 거절을 확인한다.
모의 HTTP 6개와 실제 CLI 7개, 총 13개 검증이며 원격 TLS 검증은 별도다.

`detector.runtime.ModelManager`는 로컬 패키지 검증, 진행 추론 drain,
loader 호출·예열·교체 실패 복귀를 위한 기반이다. 로컬 GLiNER 어댑터와 검사 서버
진입점은 구현했고, 실제 engineering 패키지의 오프라인 CPU cold load·합성 예열과
ASGI 인증·readiness·짧은 검사 요청 2회를 확인했다. [실제 CPU 결과·미완료 게이트](detector/M0_INTEGRATION_NOTES.md),
[의존성 독립 검증·메타데이터 한계](detector/DEPENDENCIES.md),
[운영 준비 절차](detector/DEPLOYMENT.md)를 참고한다. 현재 서버 기본 loader는 CPU이며
`PII_DETECTOR_DEVICE=cuda`로 GPU를 명시할 수 있다. CUDA 사용 불가·잘못된 tensor
배치는 준비 실패로 처리한다. 실제 GPU·원격 배포 검증은 남아 있다. `detector.app.create_app`은
`manager=` 주입 시 readiness와 교체 상태를 동적으로 반영하고, 실제 검사한
모델·임계값 버전을 응답한다. 기존 backend 직접 주입도 가능하다. `/readyz` 성공은 실제 모델
패키지 로딩·GPU 예열·가중치와 버전의 일치를 증명하지 않는다. 검사 실행·대기열
한도도 단일 API 프로세스 안에서 검증한 값이다.

저널은 JSONL 대신 SQLite의 원자적 transaction과 `synchronous=FULL`을 사용한다.
부분 마지막 줄 복구는 미commit transaction 강제 종료·복구 검증으로 대체했다.
확인되지 않은 사건은 만료·회전 삭제하지 않는다. 기본 100MiB는 주 DB 상한이며,
임시 rollback 파일 공간은 추가로 필요하다. C가 수신 확인하기 전 ack하지 않는다.
현재 저널 DTO는 PRD를 바탕으로 한 로컬 allowlist다. C의 사건 API와 합의·연동한
계약으로 간주하지 않는다. `LocalEventShipper`는 최대 100건·1MiB의 커밋된 사건을
주입된 전송 어댑터로 보내고, 검증된 receipt의 ID만 삭제한다. 취소·시간초과 뒤 늦은
ack는 버리며 미확인 사건을 유지한다. 자동 재전송은 명시적 어댑터 주입 시에만
동작하며, C의 확정 codec·실제 중앙 연동·보관 운영은 별도다.
HTTP `delivered`는 ASGI 서버가 안전한 전체 응답을 수락한 상태다. TCP 수신이나
CLI 화면 표시를 별도 승인받았다는 뜻은 아니다. 전달 전 `unknown` 기록을 보존한다.

상시 재전송과 인증 HTTP 전송 구성은 [R08 재전송](gateway/REPLAY.md)을 따른다.
서버 수명주기에 명시적 어댑터를 주입할 수 있으며 기본 실행에서는 비활성이다.
합성 codec/중앙 저장 fixture로 부분 ACK·장애·재시작을 검증했지만 C의 실제
durable ACK 계약을 채택한 운영 연동은 남아 있다.

## 보안 위험에 따른 커버리지

| 범위 | 목표 라인 / 분기 | 수치 외 필수 검증 |
|---|---|---|
| 정책·마스킹·프로토콜·전송 통제·인증·저널·보호 프로필·환경설정 | 95% / 90% 이상 | 차단 시 공급자 수신 0건, 검사 전 CLI 0바이트, 원문/키 미기록, 취소 후 전송 금지, 보호 경로 우회·원본 설정 손실 방지 |
| 검사 API·모델 패키지 상태 전이 | 95% / 90% 이상 | 손상 스팬·부분 검사·과부하 차단, 교체 실패/취소 복구 |
| 설치·운영 보조 도구 | 85% / 80% 이상 | 설정 백업/복구·실패 원자성·보호 우회 방지 |

커버리지는 모의 실행 경로의 지표다. GPU 품질·TLS·실제 CLI·배포를 대신하지 않는다.
정량보고에는 측정 범위·라인·분기·미검증 위험을 함께 적으며, 수치 달성을 위해
보안 검사를 제거하거나 범위를 숨기지 않는다.
테스트 수와 커버리지 수치는 실행 결과에서 다시 산출한다. 이 문서는 고정된
테스트 개수를 완료 기준으로 사용하지 않는다.

## 고정 텍스트 프로필의 모의 G0

설치된 `codex-cli 0.166.0`은 격리된 설정에서도 `additional_tools` 항목과
16,000 코드포인트를 넘는 기본 지시문을 보냈다. 모의 공급자와 직접 SSE 왕복은
확인했고, 기본 설정의 과대 요청은 계속 거절한다. PRD §2는 불가피한 도구 정의를
허용하면서 도구 출력과 자동 실행을 차단하도록 한다. 도구 정의 0개 자체가 PRD의
필수 조건이라는 초기 해석은 정정했다.

현재 검증 범위는 `codex-cli 0.166.0`, `gpt-6.1-sol`, API 키 방식의 보호 전용
`exec` 텍스트 프로필이다. 검토된 82바이트 기본 지시문을 CLI에서 명시적으로 사용하고
프로젝트 문서·개인 스킬·도구 발견을 비활성화한다. 사용자의 요청·대화 이력을
게이트웨이가 삭제하거나 잘라내는 방식이 아니다. 실제 요청에 남는 `exec`/`wait`
선언은 표준 Responses `tools`로 옮기며 설명·문법·스키마의 키·문자열·숫자까지
전체 검사한다. 이 고정 오버헤드도 방향당 합산 16,000코드포인트에 포함한다.

`tool_choice=none`을 강제하고 모든 도구 출력·불투명 이력·저장 참조를 거절한다.
telemetry는 허용 키·타입·길이를 검사한 뒤 외부 요청에서 제거한다. 문법·식별자·키를
마스킹해야 하는 경우는 전송하지 않고 거절하며 설명 annotation만 안전하게 치환한다.
고정 CLI와 모의 검사기·공급자의 정상/마스킹/차단/SSE/장애 검증은 통과했다.
실제 외부 API·GPU·서버 TLS 검증, 일반 Codex 코딩·도구 실행 지원을 뜻하지 않는다.
원본 custom Lark 문법의 의미 컴파일은 검증하지 않으며 변경 없이 검사·보호한다.

보호 프로필 파일은 `launcher.profiles.install()`로 지정 Codex 홈에 별도로 생성하고,
기존 파일은 비공개 백업 후 `uninstall()`로 복구한다. 사용자가 수정한 파일은 덮어쓰지
않는다. `launch_arguments(..., codex_home=...)`는 검증된 설치만 사용하고 모든 공급자
설정을 명시적인 `-c` 인자로 고정한다. `0.166.0`의 `exec --ignore-user-config`는
프로필 파일도 무시하므로 프로필 이름만으로 보호 경로를 고정하면 안 된다.
현재 실행 지원은 `exec` 검증 경로에 한정하며 대화형 CLI는 검증하지 않았다.

사용자용 명령은 [보호 프로필 CLI](launcher/README.md)의
`python -m launcher install|run|uninstall`을 사용한다. 버전을 확인한 실행 경로와
전용 홈으로 보호 정책을 적용한다. 실제 Codex와 모의 서버의 설치·실행·제거는
`integration/run_smoke.py`로 검증하며 개인 설정은 변경하지 않는다.

초기 TCP 검증 중 이 프로필 동작을 놓쳐 기본 OpenAI 주소에 인증 없는 합성 요청이
전송되고 401로 거절된 일이 있었다. 실제 API 키·실사용 입력은 사용하지 않았다.
회귀 테스트와 필수 `-c` 고정 후 재검증은 로컬 게이트웨이에 도달했다.
최신 왕복 검증은 모의 공급자만 사용했다. 재검증 CLI에는 외부 주소를 로컬 거절 프록시로
돌리는 환경도 적용했다. 처음 실패한 실행을 '외부 통신 없음'으로 집계하지 않는다.

`gateway`는 검증된 환경변수와 실제 HTTPS 검사 서버가 필요한 실행 진입점이다.
예시는 `gateway/.env.example`에 있으며 dotenv를 자동으로 읽지 않는다. 프로세스는
loopback에만 바인딩하고 접근 로그를 끈다. 실제 GPU 검사 서버가 준비되지 않은 현재
상태에서 정상 서비스 준비를 완료했다고 간주하지 않는다.

`dev` 디렉터리에서 필요한 환경변수를 별도로 설정한 뒤 실행한다. 실행 인자로
목적지·바인딩·TLS 설정을 바꾸는 옵션은 없다.

```sh
rtk proxy python -m gateway
```

필수 환경변수는 `PII_GATEWAY_LOCAL_TOKEN`, `PII_DETECTOR_URL`,
`PII_DETECTOR_TOKEN`, `OPENAI_API_KEY`, `PII_MODEL_ID`다. 검사 URL은 HTTPS의
`/v1/inspect`여야 한다. `PII_GATEWAY_PORT` 기본값은 `8765`, `PII_STATE_DIR`
기본값은 사용자 홈의 `.local/state/ai-pii-gateway`이며 재지정 시 절대 경로를 쓴다.
생성 목적지는 `https://api.openai.com/v1/responses`로 고정하고, 두 HTTP 클라이언트는
`verify=True`, `trust_env=False`로 인증서 검증을 유지하며 환경 프록시를 사용하지 않는다.
공급자 URL·바인딩·TLS 변경용 환경변수는 거절한다. 로컬 게이트웨이 `/readyz`는
결과 저널의 기록 건강 상태를 나타내며 원격 검사 서버의 가용성·모델 준비를 조회하지 않는다.

다음 사이클은 설치 흐름의 사용자 환경 검증 → 사건 재전송(C 계약 합의)
→ 실제 M0 패키지·GPU 통합 → 실제 서버 배포·통합·성능 측정 순으로 진행한다.
모의 G0와 실제 외부 서비스 검증을 구분하며 자체 API 클라이언트로 CLI를 대체하지 않는다.

배포 대상은 Ubuntu 24.04-gpu `gp1l4-g3`(L4 24GB)와 Ubuntu 24.04-base
`s2-g3`(2 vCPU/8GB)다. 로컬 프록시·OpenAI 키는 사용자 PC에 둔다.
GPU/CLI/서버 검증 진척도는 모의 개발 진척도와 분리해 보고한다.

서버별 반복 작업은 [배포·검증 실행기](deployment/README.md)를 사용한다.
`python -m deployment`는 `plan/preflight/deploy/verify/rollback`을 제공한다.
GPU는 검사 서버, 일반 서버는 명시한 C 서비스 계약에만 적용한다. C 구현이 없는
현재는 일반 서버의 읽기 전용 사전 확인만 가능하다. SSH 호스트 키·환경·모델은
별도로 준비하며 서버 데이터나 패키지를 자동 삭제·설치하지 않는다.
사용자 PC에서 서버 TLS·인증·실제 검사 응답을 확인하는 절차는
[배포 HTTPS 검증](integration/DEPLOYED_VERIFICATION.md)을 따른다.
로컬 실제 모델 검증은 `python -m integration.verify_m0_runtime`, 가중치 없이
Unicode·장문 구간 계획만 확인하려면 `python -m integration.verify_m0_tokenizer`를
사용한다. 각 도구는 검증 범위를 구분하여 증거 파일을 새로 생성한다.

GPU 성능 측정의 기본 계획과 명시적 실행은
[검사 프로파일러](integration/DETECTOR_PROFILING.md)를 따른다. 실제 L4 측정은 미완료다.
개발·안정성 작업의 수락 기준과 미해결 제약은 [BACKLOG.md](BACKLOG.md)에 기록한다.

공식 프로토콜 참고: [Responses 게이트웨이 요구사항](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility),
[SSE 이벤트](https://developers.openai.com/api/reference/resources/responses/streaming-events).
