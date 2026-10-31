# 반복 가능한 배포·검증 실행기

`dev`에서 Anaconda `py312`의 `python -m deployment`로 실행한다. Python 표준
라이브러리와 OpenSSH만 사용하며 패키지 설치, 모델 다운로드, 서버 데이터 삭제를
수행하지 않는다. 현재 검증은 **로컬 합성 SSH/파일시스템 테스트**다. 실제 서버의
CUDA, systemd, TLS, 성능 및 C 서비스 검증을 완료했다는 뜻이 아니다.

## 역할과 사전 조건

- `gpu`: B의 `python -m detector`. 서비스 JSON에서 `device`를 `cuda` 또는
  `cpu`로 명시한다. 예제는 CUDA이며, CUDA가 없으면 CPU로 대체하지 않는다.
- `general`: C 담당 `observability.*` 모듈, 의존성의 정확한 버전, health/ready
  주소와 예상 JSON을 명시한 서비스 계약이 필요하다. 현재 저장소에는 C 구현이
  없으므로 예제는 **계약 작성용 서식**이다. 소스 없이 계획·배포하면 거부한다.
  계약 없이 가능한 작업은 읽기 전용 `preflight`뿐이다.
- 대상은 Linux 및 Python 3.12 환경이다. 선택한 Python 실행 파일과 `sys.prefix`
  일치, 설치된 정확한 패키지 버전, 사용자 systemd 접근을 검사한다. Ubuntu
  24.04의 드라이버·CUDA 설치 및 사용자 systemd의 로그인 이후 지속 설정은
  운영자가 미리 준비해야 한다. 설치·enable·linger 변경은 수행하지 않는다.
  SSH 실행기 자체는 Ubuntu의 `/usr/bin/python3` 표준 라이브러리로 부트스트랩하며,
  애플리케이션은 별도로 지정한 py312를 자식 프로세스로 검증한다. 계약 없는
  호스트 preflight는 py312 경로를 몰라도 가능하다. 부트스트랩 Python으로
  애플리케이션 환경을 대신하지 않는다.
  CUDA 사전 확인은 작은 실제 GPU 할당·동기화도 수행한다. A의 학습 예약 시간에는
  실행하지 않으며, 먼저 읽기 전용 자원·진행 작업 확인으로 추론 시간대를 확보한다.
  이 실행기가 학습 예약이나 다른 담당자의 작업을 자동 중단하지는 않는다.
- 지정한 환경 파일은 **릴리스 루트 밖**에 존재해야 하고, 소유자는 접속 사용자,
  권한은 `0600`이어야 한다. 빈 줄·주석 또는 `KEY=value`만 허용한다. 값에 공백,
  따옴표, 역슬래시가 있는 형식은 거부한다. 파일을 shell로 source하지 않는다.
  GPU 환경에는 `PII_DETECTOR_TOKEN`, `PII_MODEL_PACKAGE`가 필요하고,
  `PII_DETECTOR_PORT`, `PII_DETECTOR_DEVICE`는 서비스 계약과 일치해야 한다.
  모델 패키지는 별도로 준비한다. 토큰·모델·SQLite·환경 파일은 업로드하지 않는다.
- 기본 bind는 IPv4 `127.0.0.1`이다. GPU 직접 TLS는 `bind_host: "0.0.0.0"`와
  HTTPS health/ready를 함께 설정할 수 있다. 해당 URL의 대상은 항상 loopback이다.
  GPU 환경의 `PII_DETECTOR_HOST` 및 인증서/키 쌍과 계약이 일치해야 하며
  `tls_ca_file`로 신뢰 CA를 지정할 수 있다. [TLS 준비·전환·롤백](GPU_TLS.md)을 따른다.
  서비스의 main PID가
  health/ready 포트를 실제로 소유하는지 `/proc`로 확인한다. fork한 자식만
  포트를 여는 서비스는 이 실행기의 계약에 맞지 않는다. listener 주소도 계약의
  bind와 정확히 일치해야 한다. 실제 클라이언트의 TLS 경로는 별도로 검증한다.

## 호스트 신뢰 및 인증

서버 접속 정보는 Git 제외된 `dev/.private/server-access.local.json`의
`servers.gpu` / `servers.base`에서만 읽는다. 파일 `0600`, 부모 `0700`을 검사하며
원문이나 비밀번호를 출력하지 않는다. `hostname`과 실제 원격 호스트 이름이
일치해야 한다. 지정된 호스트 키는 별도 경로로 fingerprint를 확인한 후
`dev/.private/known_hosts`에 보관한다(권한 `0600`). 빈 키 파일이나
누락 파일은 `verified_known_hosts_required`로 거부한다. 이는 네트워크 장애와
다른 **호스트 신뢰 준비 미완료** 상태다. 키 자동 수집·자동 신뢰는 하지 않는다.
관리 전송은 `-F /dev/null`로 개인·시스템 SSH 설정의 중계·명령·추가 신뢰 경로를
상속하지 않는다. 프로젝트 경로의 공백은 OpenSSH 설정 값 안에서도 인용한다.

기본 인증은 OpenSSH agent/key와 `BatchMode=yes`다. 실제 터미널에서는
`--interactive-auth`로 OpenSSH 자체 인증 프롬프트를 사용할 수 있다.
제공받은 비공개 파일의 비밀번호를 사용할 때는 `--password-auth`를 명시한다.
이 모드는 검증된 호스트 키에 대해서만 native SSH askpass가 일회성으로 해당
서버의 비밀번호를 읽는다. 비밀번호를 인수·환경변수·임시 파일·로그에 넣지 않는다.
키 암호·OTP·VPN 프롬프트나 다른 사용자/호스트 프롬프트에는 응답하지 않으며,
비밀번호 인증은 한 번만 시도한다. context와 helper는 비밀값 없이 `.cache`에
보존하고 세션 종료 후 비활성화한다. 실제 원격 비밀번호 인증은 아직 미검증이다.
인증 및 호스트 키 실패의 원문 stderr는 출력하지 않는다.

## 실행

먼저 예제 JSON을 Git 제외된 로컬 설정으로 복사하고 실제 **비밀이 아닌** 경로,
장치, C 계약을 채운다. 예제의 `/home/service`는 실제 서버 경로가 아니다.

```sh
# dev 작업 디렉터리, 활성 Anaconda py312 환경
python -m deployment plan --role gpu --spec .private/gpu-deploy.json
python -m deployment preflight --role gpu --spec .private/gpu-deploy.json
python -m deployment deploy --role gpu --spec .private/gpu-deploy.json
python -m deployment verify --role gpu --spec .private/gpu-deploy.json
python -m deployment rollback --role gpu --spec .private/gpu-deploy.json

# C 구현/계약이 없어도 대상 호스트와 Python만 읽기 전용으로 확인
python -m deployment preflight --role general
# 후보 애플리케이션 Python 존재 여부도 확인하려면 --python /absolute/py312/bin/python

# C 계약과 소스가 준비된 후
python -m deployment plan --role general --spec .private/general-deploy.json
python -m deployment deploy --role general --spec .private/general-deploy.json
python -m deployment verify --role general --spec .private/general-deploy.json
```

`plan`은 네트워크·접속 정보 없이 소스 묶음 SHA-256과 크기를 확인한다.
`--source`는 `detector/`, `gateway/` 또는 `observability/`를 포함한 디렉터리다.
GPU는 `detector`, `gateway`의 Python 파일을 포함한다. general은
`observability`의 Python과 `static/`, `templates/` 아래의 명시적 확장자
(`html/css/js/svg/png/ico/woff2`)만 포함한다. 다른 리소스가 필요한 C 서비스는
계약과 allowlist를 먼저 확장·검증해야 한다. 숨김 경로, tests, 캐시, 환경 파일,
가중치, SQLite 및 임의 JSON은 포함하지 않는다. 심볼릭 링크 소스는 거부한다.

## 상태, 복구와 보존

원격 실행기는 SSH stdin으로 전달된다. 전달 묶음의 SHA-256, 경로, 중복 항목,
파일 종류와 크기를 검사하고 SHA-256 이름의 새 릴리스에만 파일을 쓴다.
각 파일의 SHA-256을 release manifest에 기록하고, 생성 직후·verify·rollback에
실제 파일 목록과 해시, 역할, 디렉터리 이름을 재검증한다. 파일 추가·변경·누락,
심볼릭 링크 또는 manifest 불일치가 있으면 전환·재시작 전에 거부한다.
이 검증은 신뢰하는 로컬 manifest를 기준으로 변경을 찾는다. 배포 계정이 코드와
manifest를 함께 변경하는 공격에 대한 서명 검증은 아니다. 릴리스 디렉터리는 코드
전용이며 C의 SQLite·업로드·로그 등 실행 중 변하는 데이터는 외부 경로에 둔다.
현재 실행 중인 해시로 deploy를 반복하면 무결성과 정상 상태를 확인한 뒤
파일 변경·재시작 없이 성공한다. 보관된 다른 release는 덮어쓰지 않으며 명시적
`rollback`으로만 복구한다. 실패한 첫 release와 같은 해시의 재배포도 거부한다.

사용자 unit은 `codyssey-pii-gpu.service` 또는 `codyssey-pii-general.service`다.
기존 unit이 생성할 내용과 다르면 거부한다. 관리 표식이 없는 기존 배포 루트도
거부한다. unit 설정 변경이 필요하면 기존 서비스를 임의 덮어쓰지 말고 운영
절차로 별도 검토한다. 서비스 로그는 비밀 노출 방지를 위해 null로 연결한다.

deploy/rollback은 사용자 runtime 디렉터리의 잠금으로 직렬화한다. 기존 release가
있으면 먼저 정상 상태를 확인한다. 새 release 전환 후 제한 시간 동안 health,
ready 및 프로세스·포트 소유권을 검사하고, 실패하면 기존 release로 복구한 뒤
다시 검사한다. 첫 배포 실패는 서비스를 정지하고 실패한 current 링크를 다른
이름으로 보관하므로 새 코드의 배포를 다시 시도할 수 있다. 실패한 코드와
기존 release는 그대로 남는다. **중단·전원 장애의 완전한 트랜잭션 복구까지
보장하지 않는다**. 작업이 강제 종료되면 `state.json`, `current`, unit 상태를
운영자가 확인한 뒤 재개한다.

환경 파일과 모델·데이터 경로는 이 실행기의 수정 대상이 아니다. rollback은
코드 release 전환이며 외부에서 변경한 비밀·환경·DB의 시점 복원은 수행하지
않는다. 실제 이벤트 송수신, GPU 모델 품질·성능, TLS 및 대시보드 기능은 별도
통합 검증이 필요하다.

사용자 PC에서 실제 HTTPS 인증서·인증·응답 계약을 확인하려면
[배포 HTTPS 검증 스크립트](../integration/DEPLOYED_VERIFICATION.md)를 사용한다.
서버 내부 loopback 검증 통과만으로 사용자 PC와의 TLS 연결을 완료 처리하지 않는다.

## 연결·중단 상태의 읽기 전용 진단

`python -m deployment.ssh_diagnose --role all --output .cache/ssh-stage-new.json`은
서버 비밀번호 인증·원격 명령·호스트 키 자동 등록 없이 SSH 실패 단계와 현재
로컬 경로를 확인한다. 증거에는 원문 stderr·주소·자격증명을 저장하지 않는다.
종료 코드 0은 TCP 연결 도달 관찰이며 로그인 성공이 아니다. 1은 연결 미도달,
2는 진단 자체 실패다. socket 상태는 한 시점의 같은 목적지 표본이며 PID에
결합되지 않는다. 빈 표본·timeout만으로 VPN/ACG/서버 중 원인을 확정하지 않는다.

중단된 배포 디렉터리는 [읽기 전용 복구 진단](RECOVERY.md)으로 먼저 확인한다.
상태 기록·링크 전환 사이 중단과 첫 release 부분 기록은 현재 재배포/rollback을
막을 수 있으며 자동 복구는 구현하지 않았다. 기존 파일을 삭제하거나 상태를
추측해 바꾸지 않는다. 관련 미해결 항목은 `dev/BACKLOG.md`의 S04다.

C 계약의 SQLite·업로드·기타 영속 데이터 경로는 반드시 release 트리 밖의
고정된 절대 경로를 사용해야 한다. WorkingDirectory는 release마다 달라지므로
상대 DB 경로의 연속성을 이 실행기가 보장하지 않는다.

## 로컬 검증

```sh
python -m pytest deployment/tests -q
```

R11/T07/T10의 배포 준비, 실패 시 readiness 거부, 안전한 오류 노출을 검증한다.
초기 RED와 복구 경로 RED/GREEN 결과는 `dev/.cache/deployment-*.log`에 기록한다.
