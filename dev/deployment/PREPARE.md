# 로컬 배포 packet 준비

`deployment.prepare`는 서버에 접속하지 않는 plan/build/verify 도구다. SSH, HTTP,
패키지 설치, 모델 로딩, GPU 사용, 서비스 기동 기능이 없다. 실제 적용은 별도
사용자 지시가 있어야 하며 이 packet 생성은 배포 완료나 모델 품질 승인도 아니다.

`dev`에서 Anaconda py312 환경으로 실행한다. 모드를 생략하면 기본 동작은 `plan`이며
출력 디렉터리나 파일을 만들지 않는다. 설정 파일은 기존 `Spec` 계약을 따른다.
실제 토큰이나 환경 파일 내용 대신 원격 `env_file` 경로만 포함한다.

```sh
python -m deployment.prepare plan --source . --spec .cache/operator-spec.json
python -m deployment.prepare build --source . --spec .cache/operator-spec.json --model .cache/verified-model-package --output /private/tmp/codyssey-prepared/new-packet
python -m deployment.prepare verify --output /private/tmp/codyssey-prepared/new-packet --expected-manifest-sha256 TRUSTED_SHA256
```

출력 예시는 소스 디렉터리 밖의 새 경로다. `--source .`를 사용할 때 소스 내부의
`.cache/releases`는 입력 보존을 위해 출력 경로로 사용할 수 없다.

`--model`은 GPU 역할의 선택 인자다. 실제 C 구현과 Spec 계약이 없는 general
packet은 entrypoint 검증에서 출력 생성 전에 실패한다. 도구가 C API를 만들거나
계약을 승인하지 않는다. 사용자 PC용 gateway를 일반 서버로 옮기지도 않는다.

packet 파일:

- `application.tar`: 기존 `core.bundle`의 역할별 allowlist로 묶은 앱 소스/허용 정적
  자산. 검사된 module의 `.py` 또는 `__main__.py` entrypoint가 반드시 있어야 한다.
- `deploy.json`: 검증·정규화한 Spec. 최대 1MiB다.
- `model-assets.tar`: 선택 파일. 로컬 ModelPackage manifest와 정확히 일치하는 파일만
  담는 USTAR archive. manifest.json 포함 최대 1,000파일·archive 전체 2GiB다.
- `APPLY.md`: 고정된 사전 조건·권한·TLS·롤백·A/C 인수 안내. 자동 실행 명령은 없다.
- `release-manifest.json`: schema_version/role/application/spec/model/files. `files`는
  manifest 자신을 제외한 packet 전체 파일의 SHA-256이다. `model.files`는
  manifest.json 해시와 모델 manifest의 선언 파일 해시를 합친 것이다.

모델 파일은 ASCII의 정규 상대 경로만 허용하며 하위 디렉터리 이름은 유지한다.
USTAR로 표현할 수 없는 긴 이름, 숨김 경로, `..`, backslash, symlink/hardlink,
개인 키·인증서·DB·저널용 확장자/민감 디렉터리는 거부한다. USTAR 표현 가능성과
archive 예상 크기는 출력 생성 전에 확인한다. 모델 가중치는 스트리밍으로 해시·
복사할 뿐 텐서로 로드하지 않는다. 원본 앱/모델 디렉터리 내부에는 출력하지 않는다.

출력은 새 전용 디렉터리 0700과 파일 0600으로 생성하며 기존 파일을 덮어쓰지
않는다. 입력 검증이 실패하면 출력 생성 전 중단한다. 기록 중 I/O 오류 또는
원본 변경이 발견되면 새 부분 packet을 삭제하지 않고 보존한다. 검증에 통과하지
못한 부분 packet은 적용할 수 없다. 자동 정리·삭제·재시도는 없다.

verify는 쓰지 않고 정확한 파일 집합·권한·소유자·링크·크기·각 해시와 역할/Spec/
entrypoint/모델 manifest 교차 일치를 검증한다. 모델 archive의 물리 USTAR header,
regular member, 중복·경로·확장 header·끝 marker/패딩도 확인한다. PAX/GNU/sparse/
링크 및 뒤에 붙인 payload를 허용하지 않는다. JSON 중복 키와 NaN/무한대는 거부한다.

해시는 서명이 아니다. 공격자가 소스와 모든 manifest/hash를 함께 바꾸면 내부
일관성 검증만으로 출처를 알아낼 수 없다. build가 출력하는 manifest_sha256을
별도 신뢰된 기록에 보관하고, verify의 `--expected-manifest-sha256`으로 비교한다.
그 digest의 신뢰성을 확보하지 않으면 verify는 구조·무결성 검증만 의미한다.
준비·검증 중 원본과 출력에 다른 writer가 접근하지 않는 환경을 사용한다.

미래 모델 staging에는 같은 revision의 `deployment.model_stage` 모듈이 별도로
필요하다. 이 packet은 staging 실행 코드를 내장하지 않는다. 원격 known_hosts,
토큰/키/인증서, 실제 Python 환경, TLS 주소, 서비스 소유권, 이전 정상 release,
학습/추론 시간 조율, A 품질 인수와 C 계약은 별도 운영 입력·승인 사항이다.

테스트는 작은 합성 모델과 로컬 파일/CLI로 검증하며 실제 모델 품질, 서버 적용,
GPU 지연·메모리 또는 운영 TLS 검증을 뜻하지 않는다.

```sh
python -m pytest deployment/tests/test_prepare.py -q
```
