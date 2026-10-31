# 검사 API 운영 준비

2026-10-31 UTC 기준 준비 절차다. 이 문서 작성 과정에서는 설치·원격 접속·배포를 수행하지 않았다. 로컬 실제 CPU cold load와 합성 예열 1회 결과는 [M0 통합 기록](M0_INTEGRATION_NOTES.md), 패키지 설치·독립 확인과 미해결 배포 메타데이터 충돌은 [의존성 기록](DEPENDENCIES.md)을 따른다. GPU·서버 TLS·품질·운영 성능 완료를 의미하지 않는다.

반복 실행은 [서버별 배포·검증 스크립트](../deployment/README.md)를 사용한다.
현재 스크립트의 합성 검증과 실제 SSH/원격 서버 검증을 구분한다.

## 배치와 사전 확인

| 위치 | 역할·경계 |
|---|---|
| 사용자 PC | 로컬 gateway·보호 CLI, OpenAI 키, 결과 저널 |
| `gp1l4-g3`, Ubuntu 24.04-gpu, L4 24GB | B의 검사 API·상주 모델. A의 GPU 학습·평가와 시간 분리 |
| `s2-g3`, Ubuntu 24.04-base, 2 vCPU/8GB | C의 사건 수집·SQLite·SSE·대시보드. B가 임의로 변경하지 않음 |

제공된 자료에는 SSH 로그인에 필요한 사설 주소·계정·비밀번호·포트가 있다. 해당 사설망에 도달할 수 있으면 이 정보로 접속을 시도할 수 있지만, 현재 환경에서는 접속되지 않았다. 루트 담당자가 sandbox 밖에서 두 서버의 TCP 22를 `nc -vz -G 3`으로 재확인했고 모두 `Operation timed out`이었다. VPN만 원인으로 단정하지 않으며 네트워크 경로·ACL·SSH 서버 상태 중 원인은 미확정이다. 이 문서 작성자는 연결을 다시 시도하지 않았다.

접속이 가능해지면 먼저 승인된 읽기 전용 확인으로 호스트 신원, OS, CPU/RAM, 디스크 여유, 실행 중 서비스·작업, GPU/드라이버/CUDA, Python/Conda 경로를 기록한다. 기존 사용자 작업과 A/C의 서비스를 보존한다. `rm`, 디스크 초기화·포맷, 기존 디렉터리 덮어쓰기, 무분별한 컨테이너·이미지 정리를 배포 준비에 포함하지 않는다. 쓰기·설치·서비스 변경은 읽기 전용 결과와 자원 충돌을 검토한 뒤 별도 실행 단계로 진행한다.

## 환경과 모델 패키지

현재 검증한 개발 Python은 Anaconda `py312`의 Python 3.12다. 서버에서도 실제 interpreter 경로와 버전을 먼저 확인한다. 로컬 macOS arm64의 Conda build 핀과 절대 Python 경로를 Linux 서버로 복사하지 않는다. `dev/environment.yml`은 전체 모델 환경 명세나 fresh solve·재생성 검증을 마친 lockfile이 아니다. 모델 관련 실제 설치 버전·provenance와 검증 범위는 [DEPENDENCIES.md](DEPENDENCIES.md)의 현재 환경 기록을 참고하며, 이 기록도 Linux 재현 lockfile은 아니다.

필요 버전은 Conda 채널에서 먼저 찾는다. 설치 전에 dry-run의 LINK/UNLINK, 기존 PyTorch·CUDA·Protobuf·Abseil 등 ABI 계열과 패키지 소유권 변화를 검토한다. 문제가 있으면 적용하지 않고 대안을 제시한다. 필요한 패키지/버전이 Conda에 없을 때에만 검토한 pip fallback을 사용한다. 의존성을 확인하지 않은 `--no-deps`로 충돌을 숨기지 않는다. 설치 뒤에는 별도 fresh process, 잘못된 경로·버전·native import 순서를 겨냥한 확인, 실제 사용자 호출 경로까지 독립 검증한다.

모델 패키지는 별도의 새 버전 디렉터리에 둔다. 원본·backbone/tokenizer·라이브러리 revision, 해시, 라이선스와 변경 이력을 보존하고 manifest 파일 전체를 검증한다. 현재 engineering 후보는 18개 라벨/0.5 임계값이 A/B 미합의이고 `quality_gate=blocked`, `status=not_evaluated`다. CPU 로딩 성공을 품질 인수나 배포 승인으로 바꾸지 않는다. 캐시가 빈 오프라인 cold start와 실제 tokenizer 범위 검증은 사용하려는 서버·패키지 조합에서 확인해야 한다.

## 현재 진입점과 설정

`server.py`의 기본 loader는 `load_gliner`이며 **기본 device는 `cpu`**다. GPU 배포에서는 `PII_DETECTOR_DEVICE=cuda`를 명시한다. CUDA 자동 선택이나 CPU fallback은 하지 않는다. 요청한 CUDA 장치를 사용할 수 없거나 로딩 후 모델 parameter/buffer가 해당 GPU에 있지 않으면 초기화에 실패하고 readiness는 503으로 남는다. 설정 전달·실패 경계는 합성 회귀와 실제 CPU 환경의 CUDA 부재로 확인했지만, 실제 L4 로딩·GPU tensor 배치·추론·성능 검증은 남아 있다.

환경변수는 서비스 관리자가 명시적으로 주입한다. [`.env.example`](.env.example)은 참고용이며 dotenv 파일을 자동으로 읽지 않는다.

| 변수 | 현재 동작 |
|---|---|
| `PII_DETECTOR_TOKEN` | 필수. 공백 없는 ASCII 전용 비밀 토큰. gateway의 검사 토큰과 일치시킴 |
| `PII_MODEL_PACKAGE` | 필수. 검증할 로컬 패키지의 절대 경로 |
| `PII_DETECTOR_PORT` | 기본 `8766`, 정수 `1..65535` |
| `PII_DETECTOR_DEVICE` | 정확히 `cpu` 또는 `cuda`, 기본 `cpu`. CUDA 사용 실패 시 CPU로 대체하지 않음 |

설정한 Python 환경에서 `dev`를 작업 디렉터리로 사용한다.

```sh
rtk proxy python -m detector
```

실행 인자를 받지 않으며 bind는 `127.0.0.1`로 고정되고 access log는 꺼진다. 허용 목록 밖의 `PII_DETECTOR_`/`PII_MODEL_` 환경변수는 거절한다. 프로세스마다 모델과 대기열이 별도이므로 현재는 **단일 프로세스**로 계획한다. 여러 worker를 추가하여 단일 검사 실행·대기열 한도를 공유한다고 가정하지 않는다.

원격 접근은 외부 TLS 종료 계층이 HTTPS `/v1/inspect`를 loopback API에 전달하는 구조로 준비한다. 이 계층의 인증서 발급·갱신·검증, 네트워크 제한과 장애 처리는 아직 실제 서버에서 검증하지 않았다. JSON 검사 요청에는 정확히 하나의 `Authorization: Bearer <token>`을 전달한다. 토큰·본문·모델 원문을 reverse proxy access/error log, shell history, 모니터링에 기록하지 않는다. gateway는 HTTPS 검사 목적지와 인증서 검증을 유지하며 TLS 검증 해제로 연결 문제를 우회하지 않는다.

## 상태 확인과 인수 범위

`GET /healthz`는 프로세스의 `alive`만 나타낸다. `GET /readyz`는 토큰과 ModelManager의 준비 상태를 반영하여 200/503을 반환한다. 두 상태 경로는 앱에서 Bearer 인증을 요구하지 않으므로 외부 노출 정책도 TLS 계층에서 정해야 한다.

실제 manager 준비는 패키지 검증 → loader → 예열 → 패키지 재검증을 통과한 상태다. 초기화 실패 시 프로세스가 살아 있어도 unready로 남으며 안전한 오류 코드만 보관한다. readiness는 GPU 사용, A의 품질 승인, 한국어 정확도, 16,000자 전체 추론, TLS 왕복이나 p50/p95 목표를 증명하지 않는다. 기본 검사 동시 실행 1개·대기 8개·대기 2초·총시간 10초는 프로세스 내부 한도이며 native 작업 취소 완료와 동일하지 않다.

실제 서버 인수에는 GPU/VRAM·예열·인증/TLS·장애 후 복구, 전체 구간/동시 요청, 실제 CLI, 지연·품질과 C 계약 사건 전달을 별도 기록한다. 합성 테스트 또는 로컬 CPU 한 번의 수치를 운영 완료 판정으로 사용하지 않는다.

## 비파괴 교체·롤백

현재 패키지와 이전 검증 패키지, 환경 명세, 서비스 설정을 보존한다. 새 패키지는 별도 디렉터리에서 검증하고 기존 패키지를 덮어쓰지 않는다. ModelManager의 교체는 readiness를 내린 뒤 진행 추론을 drain하고 새 loader·예열·재검증을 수행하며, 실패하면 이전 backend/준비 상태로 복귀한다. 이 교체는 내부 API이며 운영자용 HTTP 교체 endpoint가 구현된 것으로 설명하지 않는다.

운영 재시작은 신규 요청 유입을 먼저 막고 진행 요청과 native 작업 종료를 확인한 뒤 수행한다. 현재 server shutdown은 진행 중 backend를 임의로 폐기하지 않으므로 service manager가 강제 종료 시점을 자동으로 안전하다고 가정하면 안 된다. 실패 시 보존한 이전 패키지의 `PII_MODEL_PACKAGE`와 이전 환경·설정으로 복귀하고 상태·합성 요청을 재확인한다. rollback을 위해 모델·캐시·저널·기존 사용자 파일을 삭제하지 않는다. 실제 서버 rollback 실행도 아직 미검증이다.
