# 사용자 PC에서 배포 HTTPS 계약 확인

`verify_deployed_services.py`는 실제 배포 URL과 자격증명을 Git에서 제외된 `dev/.private/` JSON에서 읽는다. 기본 CLI는 HTTPS만 허용하며 HTTP 우회 옵션이 없다. URL에는 사용자명·비밀번호·쿼리·fragment·경로를 넣지 않는다. 토큰은 명령 인자로 받거나 출력하지 않는다.

GPU 역할은 `/healthz`, `/readyz`, 잘못된 인증의 `/v1/inspect` 401을 확인하고, 기존 `gateway.transport.InspectionClient`로 작은 합성 Unicode 입력을 한 번 검사한다. 재시도·리다이렉트·OpenAI 호출은 하지 않으며 기존 검사 제한 10초를 유지한다. complete 응답의 ID·코드포인트 범위·유한한 시간·정확한 모델/임계값 버전을 확인한다. 개인정보 탐지 품질이나 GPU 배치·성능을 증명하는 도구는 아니다.

일반 서버 역할은 C 담당자가 확정한 health/ready GET 계약만 확인한다. 경로·정확한 상태 코드·JSON·Bearer 사용 여부를 운영자가 입력한다. 기본 엔드포인트나 사건 ACK 계약을 추측하지 않으며 사건 쓰기·전달은 수행하지 않는다. `authentication: "bearer"`는 해당 GET에 자격증명을 보낸다는 뜻이다. 일반 서버의 잘못된 인증 거절 정책까지 검증했다는 의미는 아니다. C 계약이 없으면 `general` 항목을 넣지 않는다.

설정 예시의 주소·토큰·버전은 실제 운영값으로 교체한다. 아래 내용을 그대로 실행 가능한 배포 설정으로 취급하지 않는다. `gpu` 또는 `general` 중 필요한 역할만 포함할 수 있다.

```json
{
  "schema_version": "1.0",
  "gpu": {
    "origin": "https://your-gpu-service.example",
    "token": "REPLACE_IN_PRIVATE_FILE",
    "model_version": "EXACT_EXPECTED_MODEL_VERSION",
    "threshold_version": "EXACT_EXPECTED_THRESHOLD_VERSION"
  },
  "general": {
    "origin": "https://your-general-service.example",
    "token": "REPLACE_IN_PRIVATE_FILE",
    "health": {
      "path": "/operator-confirmed-health",
      "status": 200,
      "json": {"status": "operator-confirmed-value"},
      "authentication": "none"
    },
    "ready": {
      "path": "/operator-confirmed-ready",
      "status": 200,
      "json": {"status": "operator-confirmed-value"},
      "authentication": "bearer"
    }
  }
}
```

설정은 예를 들어 `dev/.private/deployed-services.local.json`에 저장한다. 파일 소유자는 현재 사용자, 파일 모드는 정확히 `0600`, `.private` 디렉터리는 정확히 `0700`이어야 한다. symlink 설정 파일이나 `.private` 디렉터리는 거절한다. 기존 디렉터리/파일 권한은 도구가 자동 변경하지 않는다. 프로젝트의 루트 `.gitignore`가 `.private`를 제외하는지 확인한다.

인증서 검증은 항상 활성화되어 있다. 기본값은 시스템 CA 검증이며, 사설 CA는 해당 역할에 `"ca_file": "/absolute/path/to/approved-ca.pem"`을 추가한다. 이 경로는 명시한 신뢰 앵커 파일이어야 한다. CA 로딩·인증서 체인·호스트명 검증 실패를 우회하지 않는다. 시스템 trust store를 수정하지 않으며 `trust_env=False`로 프록시와 환경 기반 자격증명/CA override를 사용하지 않는다.

작업 디렉터리 `dev`에서 실행한다. 출력 파일은 새 경로여야 하며 기존 증거를 덮어쓰지 않는다.

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m integration.verify_deployed_services .private/deployed-services.local.json --output .cache/deployed-services-evidence-01.json
```

종료 코드 0은 지정한 역할의 계약 검증 통과다. 실패는 1로 종료하고 정제된 오류 코드만 출력한다. 증거에는 원문·토큰·URL·헤더를 넣지 않고 상태·개수·버전·시간을 기록한다. `tls_verified=true`는 이 실행이 실제 HTTPS 인증서/호스트명 검증을 통과했다는 뜻이며, GPU 장치·사건 전달·배포 전체 완료를 의미하지 않는다.

테스트:

```sh
rtk proxy /Users/oliverjoo/Dev/Anaconda/anaconda3/envs/py312/bin/python -m pytest integration/tests/test_deployed_services_probe.py -q
```

합성 HTTP transport 테스트와 별도로 시스템 `/usr/bin/openssl`로 하루짜리 로컬 인증서를 만들고 `127.0.0.1` HTTPS fixture를 실제 CLI로 호출한다. 명시 CA 검증 성공, 기본 CA에서 self-signed 인증서 거절, 환경 프록시 무시를 확인한다. fixture 설정/증거의 `test_only=true`는 배포 증거와 구분하기 위한 표시이며 이 모드의 호스트는 loopback만 허용한다. 인증서·키·증거는 pytest 자동 정리와 분리된 `deployed-tls-fixture-*` 임시 디렉터리에 보존하고 시스템 인증서 저장소를 변경하지 않는다. 샌드박스가 loopback bind를 차단하면 해당 테스트 명령의 실행 권한이 필요하다. 이 fixture는 실제 서버·실제 모델 검증을 대신하지 않는다.
