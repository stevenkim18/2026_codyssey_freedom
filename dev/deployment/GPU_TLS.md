# GPU 검사 API의 직접 TLS 배포 준비

이 문서는 **로컬 준비와 합성 TLS 검증 결과**다. 현재 GPU 서버에는 이 변경을 적용하지 않았으며 API 서비스를 기동하지 않았다. A의 GPU 사용 시간에는 원격 preflight도 실행하지 않는다. preflight의 CUDA 확인은 작은 GPU 연산을 포함한다.

## 호환되는 두 모드

- 기존 설정: `PII_DETECTOR_HOST` 생략, 인증서 설정 생략, HTTP `127.0.0.1`. 기존 배포 계약과 기본값을 유지한다.
- 직접 TLS: `PII_DETECTOR_HOST=0.0.0.0`, 인증서·키 경로를 함께 지정한다. Uvicorn이 HTTPS를 직접 처리하며 별도 nginx 설치가 필요하지 않다. TLS 없는 외부 bind는 시작 전에 거부한다. loopback에만 TLS를 켜는 구성도 가능하다.

환경변수나 CLI 플래그로 인증서 검증을 끄는 옵션은 없다. 검사 API의 Bearer 인증과 요청·응답 JSON 계약은 그대로다. health/ready는 상태 확인용이며 인증 없이 응답한다.

## 운영자가 별도로 준비할 값

[TLS 배포 계약 예제](examples/gpu-tls.json)와 [환경 예제](examples/gpu-tls.env.example)를 비공개 설정으로 복사한 후 절대 경로를 확정한다. 예제의 `/home/service`는 실제 서버 계정 경로가 아니다. 환경 파일은 shell로 source하지 않으며 `${HOME}` 같은 확장은 하지 않는다.

1. 토큰과 검증된 고정 모델 패키지 경로를 환경 파일에 채운다. 예제의 빈 값은 의도적으로 시작을 거부한다. 이 작업은 모델 다운로드나 A의 모델 승인·품질 검증을 대신하지 않는다.
2. TLS 인증서 체인과 개인 키를 릴리스 루트 밖에 보관한다. 키는 서비스 사용자 소유의 일반 파일, 권한 `0600`, hardlink 1개여야 한다. 암호화된 개인 키·심볼릭 링크·심볼릭 링크를 경유하는 경로는 지원하지 않는다. 인증서 자동 발급, 키 생성·전송, 방화벽 변경은 배포 실행기가 수행하지 않는다.
3. 인증서 SAN에는 내부 readiness 검증용 **IP `127.0.0.1`** 및 실제 클라이언트가 접속할 DNS 이름 또는 IP를 포함한다. DNS SAN `localhost`만으로 IP 검증을 대체할 수 없다. 실제 서버 인증서에는 로컬 테스트용 자체 서명 fixture를 사용하지 않는다.
4. 서버 계약의 `tls_ca_file`은 해당 체인을 검증할 신뢰 CA 파일이다. 생략하면 시스템 CA를 사용한다. 사용자 PC의 HTTPS 검증 설정에도 해당 PC의 신뢰 CA 파일을 별도로 지정한다. 개인 키는 사용자 PC의 검증 설정에 필요하지 않다.
5. `bind_host`, 환경의 `PII_DETECTOR_HOST`, health/ready scheme 및 port를 일치시킨다. health/ready 대상 주소는 외부 bind를 사용해도 `127.0.0.1`을 유지한다.

인증서·개인 키·토큰·모델은 코드 묶음에 포함하지 않는다. preflight는 애플리케이션 시작이나 release 변경 전에 키 권한·소유권, 인증서/키의 실제 쌍 로딩, CA 로딩 및 환경/계약 일치를 검사한다. 인증서 체인·SAN에 대한 실제 검증은 HTTPS readiness에서 수행하므로 잘못된 CA나 이름은 준비 완료가 될 수 없다.

## 향후 적용·검증·롤백 순서

현재 허용 범위는 로컬 `plan`까지다. 아래 원격 단계는 A와 GPU 추론 시간대를 조율하고 적용 승인을 받은 뒤 수행한다. 기존 관리 SSH의 strict known-host 검증을 그대로 사용하며 자동 신뢰나 접속 정보 출력은 없다.

```sh
# dev 디렉터리, 프로젝트 지정 Python 3.12 환경
python -m deployment plan --role gpu --spec .private/gpu-tls-deploy.json

# 이후 원격 적용 승인을 받은 경우에만:
python -m deployment preflight --role gpu --spec .private/gpu-tls-deploy.json
python -m deployment deploy --role gpu --spec .private/gpu-tls-deploy.json
python -m deployment verify --role gpu --spec .private/gpu-tls-deploy.json
```

관리 서비스는 `codyssey-pii-gpu.service`이며 기존 release 방식과 shutdown 정책을 유지한다. readiness는 단순 200 응답 외에 main PID의 정확한 Python 명령, current 작업 디렉터리 및 설정된 bind 주소의 포트 소유권을 확인한다. HTTPS 검사에는 명시 CA/시스템 CA, hostname 검증, redirect 거부 및 proxy 환경변수 무시가 적용된다. TLS 연결 실패는 HTTP로 재시도하지 않는다.

모델 로딩·예열 실패는 정상 readiness로 대체하지 않는다. 새 release가 제한 시간 내 healthy/ready가 되지 않으면 기존 코드 release로 복구하고 재검증한다. 수동 코드 복구는 다음 명령을 사용한다.

```sh
python -m deployment rollback --role gpu --spec .private/gpu-tls-deploy.json
```

이 rollback은 코드만 복구한다. 환경 파일·토큰·모델·인증서·CA 변경을 되돌리지 않으므로 해당 외부 파일의 이전 버전 보존과 복원 절차도 필요하다. HTTP→HTTPS 전환은 기존 프로세스의 실제 transport와 검증 계약이 달라질 수 있으므로 기존 실행기에 무중단 전환이 된다고 가정하지 않는다. 첫 TLS 도입 또는 bind/인증서 변경은 별도 운영 전환으로 관리한다.

마지막으로 사용자 PC에서 [기존 HTTPS 계약 검증기](../integration/DEPLOYED_VERIFICATION.md)를 사용해 신뢰 CA·hostname·잘못된 토큰 거절·인증된 합성 검사를 확인한다. 내부 loopback 성공은 실제 클라이언트 경로의 성공을 의미하지 않는다.

## 로컬 검증 근거와 한계

R10/R11 및 T07/T10에 연결한 `deployment/tests/test_gpu_tls.py`는 기능 미구현 상태의 실패 확인 후 작성했다. 실제 로컬 Uvicorn HTTPS 서버와 하루짜리 합성 인증서를 사용하여 trusted CA 성공, 다른 CA/시스템 CA의 거절, 잘못된 인증 401 및 인증된 합성 모델 검사 200을 확인했다. Linux `/proc` 소유권 검사는 합성 파일 트리로 loopback/wildcard 주소가 서로 대체되지 않는지 확인한다.

이 검증은 실제 GPU 모델, 서버 인증서, 원격 네트워크, A의 학습/추론 시간 분리, 운영 처리량 또는 배포 완료를 증명하지 않는다. 인증서 발급·교체와 원격 실행은 미수행이다.
