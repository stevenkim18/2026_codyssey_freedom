# 일반 서버의 CPU 런타임 준비

이 도구는 B가 일반 서버에 사용하는 별도 rootless Python 런타임을 준비한다.
C의 사건 API 소스와 공용 계약이 현재 저장소에 없으므로 **애플리케이션 배포는
차단 상태**다. C API를 새로 만들거나 사용자 PC용 gateway를 서버로 이동하지
않으며 포트·방화벽·서비스·시스템 Python을 변경하지 않는다.

`dev`의 Anaconda py312 환경에서 실행한다. 모든 SSH는 비공개 접속 파일의
`general` 역할, 실제 hostname 일치, 검증된 known_hosts와 일회용 password
session을 사용한다. GPU 역할 옵션이나 임의 원격 명령 인자는 없다.

```sh
python -m deployment.general_runtime inspect
python -m deployment.general_runtime bootstrap
python -m deployment.general_runtime manager-verify
python -m deployment.general_runtime plan
# 위 신규 환경 변경 계획을 검토한 뒤에만 실행
python -m deployment.general_runtime install
python -m deployment.general_runtime verify
```

`bootstrap`은 `$HOME/miniforge3`가 없을 때만 공식 Miniforge 26.9.4-1 Linux x86_64
설치 파일을 받아 지정 SHA-256과 대조한다. 기존 prefix는 덮어쓰지 않는다.
설치 파일은 `$HOME/.cache`에 보존하며 해시가 다른 파일을 지우거나 바꾸지 않는다.
`manager-verify`는 별도 SSH 프로세스의 native conda info에서 실제 root와 플랫폼을
확인한다. 이는 독립 검토자의 검증이나 응용 서비스 완료를 대체하지 않는다.

`plan`은 conda-forge 채널에서 Python 3.12.16, fastapi-core 0.116.1, uvicorn 0.35.0,
httpx 0.28.1을 검색하고 새 `$HOME/miniforge3/envs/py312`에 대한 dry-run을 실행한다.
기존 환경이 있거나 UNLINK가 있는 계획은 거부한다. `install`도 같은 검색·계획
검증을 다시 수행한다. Torch·CUDA·모델·학습 패키지는 요청하지 않는다. SQLite는
Python 표준 라이브러리로 검증한다. conda 검색은 원격 index/cache를 만들 수
있으므로 순수 읽기 전용 서버 조사와 구분한다.

`verify`는 새 환경 Python의 격리 프로세스에서 sys.prefix/Python3.12, FastAPI,
uvicorn/httpx 정확한 버전, SQLite 메모리 DB 쿼리를 확인한다. 이어 native `conda run`의 같은 prefix/버전 결과, `pip check`,
Conda 패키지 목록의 Torch/NVIDIA/CUDA 부재를 확인한다. 시스템 Python을
응용 환경으로 대신 사용하지 않는다. 실패 시 원문 stdout/stderr·환경변수·경로를
출력하지 않고 `general_runtime_failed`만 남긴다. 설치 중단·실패는 자동 삭제나
강제 재설치를 하지 않으며 보존된 상태의 별도 검토가 필요하다.

관리자 설치와 환경 준비는 실제 C 애플리케이션 실행·인증·TLS·사건 전달·DB 내구성
검증과 별도다. 결과의 `application_deployed`는 항상 false다. 최종 완료 보고에는
별도 검증자의 cold-process 경로/버전·충돌·실행 검증 증거가 필요하다.

## 2026-10-31 실행 범위

일반 서버에서 Ubuntu 24.04/x86_64, CPU 2개, 새 prefix 부재를 먼저 확인했다.
고정 해시 관리자 설치 후 별도 SSH의 `conda info`에서 Conda 26.9.4와 실제
root identity를 확인했다. conda-forge 검색·dry-run의 UNLINK 0을 검토한 뒤
새 py312를 만들었다. 별도 SSH 검증은 Python 3.12.16/FastAPI 0.116.1/uvicorn
0.35.0/httpx 0.28.1/SQLite 3.54.1, 직접 실행과 native conda run의 일치,
pip check와 GPU 패키지 부재를 통과했다. 독립 검토자의 별도 검증 결과는
운영 기록에서 함께 확인해야 한다. C 소스·서비스 계약 미제공으로 애플리케이션
실행·TLS·사건 DB·SSE 검증은 계속 미완료다.

## 로컬 사전 배포 경계 검증

후속 작업은 서버 접속 없이 로컬 검증만 수행한다. 원격 응답의 중복 JSON 키,
정확하지 않은 Python/패키지 버전 및 UNLINK 0이 아닌 계획을 성공으로 인정하지
않는다. 성공 출력은 실행 모드별 정확한 필드·상태·타입과 일치해야 한다.
합성 파일시스템에서 잘못된 호스트/OS/아키텍처, 상대 홈 경로 및 홈·manager·bin·
envs·환경의 symlink를 거부하고 명령을 실행하지 않는지 검사한다.

coverage의 모듈 수치는 문자열로 전송하는 원격 소스의 실행문을 포함하지 않는다.
원격 소스의 guard는 별도의 합성 실행 테스트로 검증하며 이를 실제 서버 적용
또는 원격 전체 경로 커버리지로 해석하지 않는다. 향후 bootstrap/install 실행은
별도 사용자 지시가 있을 때만 수행한다.
